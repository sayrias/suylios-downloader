"""
Suylios Downloader - Gofile Extractor (v5 – Tam Proxy + Hibrit İndirici).

Gofile.io IP'si Python'dan engellendiğinde (ISP bloğu, Cloudflare):
  → Kullanıcının proxy/VPN ayarını kullan
  → Lokal proxy otomatik tara (Urban VPN, Tor, vs.)
  → curl_cffi Chrome TLS taklidi (Cloudflare bypass)
  → Standart requests fallback
  → HTML scrape son çare

Proxy Sırası:
1. Uygulama ayarları → proxy (kullanıcı tanımlı)
2. Ortam değişkeni (HTTPS_PROXY / HTTP_PROXY)
3. Windows sistem proxy
4. Otomatik yerel proxy taraması (127.0.0.1:8888, :1080, :8080, :3128...)
5. Proxy yok — doğrudan bağlan
"""

from collections import defaultdict, deque
import concurrent.futures
import json
import logging
import os
import random
import re
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests

from src.config import config
from src.extractors.base_extractor import BaseExtractor, ExtractionCancelled, ExtractionError
from src.extractors.fast_downloader import (
    SlidingSpeedTracker,
    _sleep_cancelable,
    _wait_if_paused,
    compute_smart_workers,
    min_split_bytes,
    ranged_work_stealing_download,
    should_start_direct_segmented,
)

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 262144  # 256 KB stream read/write buffer
_MAX_RETRIES = 3
_RETRY_BACKOFF = 2
_PARALLEL_MIN = 1536 * 1024      # kalan veri bundan büyükse parçalı moda geçilebilir
_SLOW_BPS = 400 * 1024            # ortalama hız bunun altındaysa 'yavaş' sayılır
_PARALLEL_CONNS = 8
_DOH_TTL_SECS = 600               # 10 dakika DoH önbellek
_TREE_TTL_SECS = 300              # 5 dakika içerik ağacı önbelleği

_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?gofile\.io/(?:d/|/?c=)(?P<id>[A-Za-z0-9_-]+)",
)

_API_BASE = "https://api.gofile.io"
_WEBSITE = "https://gofile.io"

# Gofile config.js'den alınan statik websiteToken (SHA-256 hash DEĞİL)
_STATIC_WEBSITE_TOKEN = "4fd6sg89d7s6"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "X-BL": "en-US",
    "Origin": _WEBSITE,
    "Referer": f"{_WEBSITE}/",
}

# Otomatik taranacak yerel proxy portları (Urban VPN, Tor, vs.)
_LOCAL_PROXY_PORTS = [8888, 1080, 8080, 3128, 1081, 10808, 9150, 9050, 7890, 7891]


class _RateLimitError(Exception):
    """Gofile API rate-limit (429) veya error-rateLimit durumunda fırlatılır."""


class GofileExtractor(BaseExtractor):
    """Gofile downloader – proxy destekli, curl_cffi öncelikli, çok katmanlı."""

    _slow_host_ts: dict[str, float] = {}

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._tokens_pool: list[str] = []
        self._token_index: int = 0
        self._proxy: Optional[str] = None  # Çalışan proxy URL'si
        self._session = requests.Session()
        self._session.headers.update(_DEFAULT_HEADERS)
        self._dl_local = threading.local()
        self._dl_lock = threading.Lock()
        self._no_parallel_hosts: set[str] = set()
        self._range_verified_hosts: set[str] = set()
        self._host_best_proto: dict[str, Any] = {}
        self._host_v2_only: set[str] = set()
        self._all_pools: list[dict[int, Any]] = []

    def _get_thread_host_pool(self, host: str) -> dict[int, Any]:
        pools = getattr(self._dl_local, "host_pools", None)
        if pools is None:
            pools = {}
            self._dl_local.host_pools = pools
        if host not in pools:
            pool: dict[int, Any] = {}
            pools[host] = pool
            with self._dl_lock:
                self._all_pools.append(pool)
        return pools[host]

    def _close_all_pools(self) -> None:
        with self._dl_lock:
            for pool in self._all_pools:
                for s in pool.values():
                    try:
                        s.close()
                    except Exception:
                        pass
                pool.clear()
            self._all_pools.clear()

    @classmethod
    def _is_host_slow(cls, host: str) -> bool:
        ts = cls._slow_host_ts.get(host, 0.0)
        return ts > 0.0 and (time.monotonic() - ts) < 180.0

    @classmethod
    def _mark_host_slow(cls, host: str) -> None:
        if host:
            cls._slow_host_ts[host] = time.monotonic()

    @staticmethod
    def _new_cffi_dl_session(proxies: dict[str, str], use_v3: bool = False, http_ver: Optional[Any] = None) -> Any:
        from curl_cffi.requests import Session as CffiSession
        from curl_cffi import CurlHttpVersion, CurlOpt
        if http_ver is not None:
            ver = http_ver
        else:
            ver = CurlHttpVersion.V3 if (use_v3 and not proxies) else CurlHttpVersion.V2TLS
        kw: dict[str, Any] = {
            "impersonate": "chrome",
            "proxies": proxies,
            "http_version": ver,
        }
        if not proxies:
            # IPv4'e sabitle: IPv6 kaynaklı anlık bağlantı reddi ve gecikmeleri önler
            kw["curl_options"] = {CurlOpt.IPRESOLVE: 1}
        return CffiSession(**kw)

    # ------------------------------------------------------------------
    # Capability
    # ------------------------------------------------------------------

    @staticmethod
    def can_handle(url: str) -> bool:
        return bool(_URL_PATTERN.match(url))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_info(self, url: str) -> dict[str, Any]:
        self._init_proxy()
        content_id = self._parse_url(url)
        self._ensure_token()
        tree = self._fetch_content_tree(content_id)
        return self._normalise(tree, content_id)

    def download(
        self,
        url: str,
        output_path: str,
        format_id: str = "best",
        progress_hook: Optional[Callable[[dict[str, Any]], None]] = None,
        **kwargs: Any,
    ) -> str:
        self._init_proxy()
        content_id = self._parse_url(url)
        self._ensure_token()
        tree = self._fetch_content_tree(content_id)

        folder_name = self._safe_filename(tree.get("name", content_id))
        dest = Path(output_path) / folder_name
        dest.mkdir(parents=True, exist_ok=True)

        tracker_dir = Path.home() / ".suylios_trackers"
        tracker_dir.mkdir(parents=True, exist_ok=True)
        tracker_path = tracker_dir / f"gofile_{content_id}.json"
        for old_t in dest.glob(".gofile_tracker_*.json"):
            try:
                old_t.unlink(missing_ok=True)
            except Exception:
                pass
        downloaded_set = self._load_tracker(tracker_path)

        files = self._collect_files(tree)
        if not files:
            raise ExtractionError("Gofile içeriğinde indirilebilir dosya bulunamadı.")

        _cancel = kwargs.get("cancel_event")
        _pause = kwargs.get("pause_event")
        last_path = ""

        # Tek dosya: doğrudan maksimum hızla indir
        if len(files) == 1:
            finfo = files[0]
            fid = finfo.get("id", "")
            fname = self._safe_filename(finfo.get("name", fid))
            file_dest = dest / fname
            if fid in downloaded_set and file_dest.exists() and file_dest.stat().st_size > 0:
                logger.debug("Zaten indirilmiş ve dosyası mevcut, atlıyor: %s", fname)
                last_path = str(file_dest)
            else:
                file_url = finfo.get("link", "")
                if not file_url:
                    raise ExtractionError("Dosya için indirme bağlantısı bulunamadı.")
                file_size = finfo.get("size", 0)
                if progress_hook:
                    progress_hook({
                        "status": "downloading",
                        "filename": str(file_dest),
                        "item_index": 1,
                        "item_count": 1,
                        "item_title": fname,
                        "downloaded_bytes": 0,
                        "total_bytes": file_size,
                        "speed": 0.0,
                    })
                try:
                    self._download_file(
                        file_url, file_dest, file_size, progress_hook,
                        cancel_event=_cancel, pause_event=_pause,
                    )
                    downloaded_set.add(fid)
                    self._save_tracker(tracker_path, downloaded_set)
                    last_path = str(file_dest)
                except ExtractionCancelled:
                    raise
                except Exception as exc:
                    if type(exc).__name__ == "_CancelledError" or (_cancel is not None and _cancel.is_set()):
                        raise ExtractionCancelled("İndirme iptal edildi.") from exc
                    logger.error("Gofile dosyası 1/1 (%s) indirilemedi: %s", fname, exc)
                finally:
                    self._close_all_pools()
        else:
            # Çoklu galeri: yavaş/aşırı yüklü sunucuların hızlı sunucuları tıkamasını önlemek
            # için sunucu bazlı dengeli zamanlama ve eşzamanlı indirme havuzu (en fazla 3 dosya)
            total_count = len(files)
            pending_items: list[tuple[int, dict[str, Any], Path, str, int, str]] = []
            per_file_done: dict[int, int] = {}
            per_file_speed: dict[int, float] = {}
            completed_count = 0
            total_gallery_bytes = 0

            for idx, finfo in enumerate(files, 1):
                fid = finfo.get("id", "")
                fname = self._safe_filename(finfo.get("name", fid))
                file_dest = dest / fname
                fsz = max(0, int(finfo.get("size", 0) or 0))
                if fsz == 0 and file_dest.exists():
                    fsz = file_dest.stat().st_size
                total_gallery_bytes += fsz
                if fid in downloaded_set and file_dest.exists() and file_dest.stat().st_size > 0:
                    logger.debug("Zaten indirilmiş ve dosyası mevcut, atlıyor: %s", fname)
                    last_path = str(file_dest)
                    per_file_done[idx] = fsz or file_dest.stat().st_size
                    completed_count += 1
                    continue
                file_url = finfo.get("link", "")
                if not file_url:
                    logger.warning("Dosya için indirme bağlantısı yok: %s", fname)
                    continue
                host = urlparse(file_url).hostname or ""
                pending_items.append((idx, finfo, file_dest, file_url, fsz, host))

            if pending_items:
                host_queues: dict[str, deque] = defaultdict(deque)
                for item in pending_items:
                    host_queues[item[5]].append(item)
                per_host_active: dict[str, int] = defaultdict(int)
                agg_lock = threading.Lock()
                tracker_lock = threading.Lock()
                state_box: dict[str, Any] = {
                    "last_path": last_path,
                    "completed": completed_count,
                    "last_emit": 0.0,
                    "latest_title": pending_items[0][2].name,
                    "latest_detail": "Sunucu bağlanıyor...",
                }
                pool_workers = min(3, len(pending_items))
                seg_en_g, seg_conns_g, _ = config.get_segmented_settings()
                queue_cond = threading.Condition(agg_lock)

                def _pick_next_item():
                    with queue_cond:
                        while True:
                            if _cancel is not None and _cancel.is_set():
                                return None, 1
                            candidates = [h for h, dq in host_queues.items() if dq]
                            if not candidates:
                                return None, 1
                            # Hızlı sunuculara öncelik ver; yavaş/tıkanan sunucuda aynı anda en fazla 1 dosya çalıştır
                            healthy = [h for h in candidates if not self._is_host_slow(h)]
                            if healthy:
                                chosen_host = min(healthy, key=lambda h: (per_host_active[h], -len(host_queues[h])))
                            else:
                                free_slow = [h for h in candidates if per_host_active[h] == 0]
                                if not free_slow:
                                    queue_cond.wait(timeout=0.4)
                                    continue
                                chosen_host = max(free_slow, key=lambda h: len(host_queues[h]))
                            item_tup = host_queues[chosen_host].popleft()
                            per_host_active[chosen_host] += 1
                            remaining_queues = sum(1 for dq in host_queues.values() if dq)
                            active_total = sum(per_host_active.values()) + remaining_queues
                            if not seg_en_g:
                                item_conns = 1
                            elif self._is_host_slow(chosen_host):
                                item_conns = min(seg_conns_g, 5)
                            elif active_total > 1:
                                item_conns = max(4, seg_conns_g // min(3, active_total))
                            else:
                                item_conns = seg_conns_g
                            return item_tup, item_conns

                def _emit_gallery(item_title: str, item_file: Path, item_done: int, item_size: int, force: bool = False) -> None:
                    if not progress_hook:
                        return
                    now = time.monotonic()
                    with agg_lock:
                        if not force and (now - state_box["last_emit"] < 0.15):
                            return
                        state_box["last_emit"] = now
                        agg_done = sum(per_file_done.values())
                        agg_speed = sum(per_file_speed.values())
                        done_cnt = state_box["completed"]
                        cur_idx = min(total_count, done_cnt + 1 if done_cnt < total_count else total_count)
                        title_snap = state_box["latest_title"] or item_title
                        active_files = sum(per_host_active.values())
                        sub_detail = state_box.get("latest_detail", "")
                    if active_files > 1 and sub_detail:
                        detail_text = f"{active_files} eşzamanlı • {sub_detail}"
                    elif active_files > 1:
                        detail_text = f"{active_files} dosya paralel iniyor"
                    else:
                        detail_text = sub_detail or "İndiriliyor..."
                    use_total = total_gallery_bytes if total_gallery_bytes > 0 else item_size
                    use_done = agg_done if total_gallery_bytes > 0 else item_done
                    eta = int((use_total - use_done) / agg_speed) if agg_speed > 0 and use_total > use_done else 0
                    progress_hook({
                        "status": "downloading",
                        "status_detail": detail_text,
                        "filename": str(item_file),
                        "item_index": cur_idx,
                        "item_count": total_count,
                        "item_title": title_snap,
                        "downloaded_bytes": use_done,
                        "total_bytes": use_total,
                        "speed": agg_speed,
                        "eta": eta,
                    })

                def _gallery_worker() -> None:
                    while True:
                        _wait_if_paused(_pause, _cancel)
                        nxt, item_conns = _pick_next_item()
                        if nxt is None:
                            return
                        idx, finfo, file_dest, file_url, fsz, host = nxt
                        fid = finfo.get("id", "")
                        fname = file_dest.name
                        try:
                            with agg_lock:
                                state_box["latest_title"] = fname

                            def _item_hook(data: dict[str, Any], _idx=idx, _fname=fname, _fdest=file_dest, _fsz=fsz, _host=host) -> None:
                                db = max(0, int(data.get("downloaded_bytes") or 0))
                                sp = float(data.get("speed") or 0.0)
                                with agg_lock:
                                    if _fsz > 0:
                                        db = min(db, _fsz)
                                    prev = per_file_done.get(_idx, 0)
                                    if db > prev:
                                        per_file_done[_idx] = db
                                    per_file_speed[_idx] = sp
                                    state_box["latest_title"] = _fname
                                    if data.get("status_detail"):
                                        state_box["latest_detail"] = str(data["status_detail"])
                                _emit_gallery(_fname, _fdest, db, _fsz, force=False)

                            self._download_file(
                                file_url, file_dest, fsz, _item_hook,
                                cancel_event=_cancel, pause_event=_pause,
                                max_conns_override=item_conns,
                            )
                            actual_sz = file_dest.stat().st_size if file_dest.exists() else fsz
                            with agg_lock:
                                per_file_done[idx] = max(fsz, actual_sz)
                                per_file_speed[idx] = 0.0
                                state_box["completed"] += 1
                                state_box["last_path"] = str(file_dest)
                            with tracker_lock:
                                downloaded_set.add(fid)
                                self._save_tracker(tracker_path, downloaded_set)
                            _emit_gallery(fname, file_dest, actual_sz, fsz or actual_sz, force=True)
                        except ExtractionCancelled:
                            raise
                        except Exception as exc:
                            with agg_lock:
                                per_file_speed[idx] = 0.0
                            if type(exc).__name__ == "_CancelledError" or (_cancel is not None and _cancel.is_set()):
                                raise ExtractionCancelled("İndirme iptal edildi.") from exc
                            logger.error("Gofile dosyası %d/%d (%s) indirilemedi: %s", idx, total_count, fname, exc)
                        finally:
                            with queue_cond:
                                per_host_active[host] = max(0, per_host_active[host] - 1)
                                queue_cond.notify_all()

                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=pool_workers) as pool:
                        futs = [pool.submit(_gallery_worker) for _ in range(pool_workers)]
                        for fut in futs:
                            fut.result()
                    last_path = state_box["last_path"]
                finally:
                    self._close_all_pools()

        if not last_path:
            raise ExtractionError(
                "Gofile: Hiçbir dosya indirilemedi. "
                "IP engellenmiş olabilir. VPN olarak Cloudflare WARP kullanın veya "
                "Ayarlar'dan bir proxy (HTTP/SOCKS5) girin."
            )

        for old_t in dest.glob(".gofile_tracker_*.json"):
            try:
                old_t.unlink(missing_ok=True)
            except Exception:
                pass

        final_ret = str(dest) if len(files) > 1 else (last_path or str(dest))
        if progress_hook:
            progress_hook({"status": "finished", "filename": final_ret})

        return final_ret

    # ------------------------------------------------------------------
    # Proxy yönetimi
    # ------------------------------------------------------------------

    def _init_proxy(self) -> None:
        """Çalışan bir proxy bul ve kaydet."""
        if self._proxy is not None:
            return  # Zaten belirlendi (None string değil, atlanmış demek)

        # 1) Uygulama ayarlarından proxy
        cfg_proxy = getattr(self, "_app_proxy", "").strip()
        if cfg_proxy:
            if self._test_proxy(cfg_proxy):
                self._proxy = cfg_proxy
                logger.info("Gofile: Ayarlardaki proxy kullanılıyor: %s", cfg_proxy)
                self._apply_proxy_to_session(cfg_proxy)
                return
            else:
                logger.warning("Gofile: Ayarlardaki proxy çalışmıyor: %s", cfg_proxy)

        # 2) Ortam değişkeni
        env_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
        if env_proxy:
            if self._test_proxy(env_proxy):
                self._proxy = env_proxy
                logger.info("Gofile: Ortam değişkeni proxy kullanılıyor: %s", env_proxy)
                self._apply_proxy_to_session(env_proxy)
                return

        # 3) Windows sistem proxy
        sys_proxy = self._get_windows_proxy()
        if sys_proxy:
            if self._test_proxy(sys_proxy):
                self._proxy = sys_proxy
                logger.info("Gofile: Sistem proxy kullanılıyor: %s", sys_proxy)
                self._apply_proxy_to_session(sys_proxy)
                return

        # 4) Yerel proxy otomatik tarama (Urban VPN, Tor, Clash, vs.)
        found = self._scan_local_proxies()
        if found:
            self._proxy = found
            logger.info("Gofile: Yerel proxy bulundu ve kullanılıyor: %s", found)
            self._apply_proxy_to_session(found)
            return

        # 5) Proxy yok, boş string ile işaretle
        self._proxy = ""
        logger.debug("Gofile: Proxy bulunamadı, doğrudan bağlantı deneniyor.")

    _doh_cache: dict[str, tuple[float, list[str]]] = {}
    _doh_lock = threading.Lock()
    _doh_session: Optional[requests.Session] = None

    def _resolve_via_doh(self, hostname: str) -> list[str]:
        """
        DNS over HTTPS (DoH) ile hostname'i çöz (10 dk önbellekli + keep-alive).
        Önce A (IPv4) sorgular; yalnızca boş dönerse AAAA dener.
        """
        now = time.monotonic()
        with GofileExtractor._doh_lock:
            hit = GofileExtractor._doh_cache.get(hostname)
            if hit and (now - hit[0]) < _DOH_TTL_SECS:
                return list(hit[1])
            if GofileExtractor._doh_session is None:
                GofileExtractor._doh_session = requests.Session()
            doh_sess = GofileExtractor._doh_session

        all_ips: list[str] = []
        for record_type in ("A", "AAAA"):
            if record_type == "AAAA" and all_ips:
                break
            doh_endpoints = (
                f"https://dns.google/resolve?name={hostname}&type={record_type}",
                f"https://cloudflare-dns.com/dns-query?name={hostname}&type={record_type}",
                f"https://dns.quad9.net/dns-query?name={hostname}&type={record_type}",
            )
            for endpoint in doh_endpoints:
                try:
                    resp = doh_sess.get(
                        endpoint,
                        headers={"Accept": "application/dns-json"},
                        timeout=3,
                        verify=True,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        ips = [
                            a["data"]
                            for a in data.get("Answer", [])
                            if a.get("type") in (1, 28)
                        ]
                        if ips:
                            all_ips.extend(ips)
                            break
                except Exception as e:
                    logger.debug("DoH endpoint başarısız (%s): %s", endpoint, e)

        if all_ips:
            # IPv4 adreslerini öne al (IPv6 erişimi olmayan ağlarda gereksiz timeout'u önler)
            all_ips.sort(key=lambda ip: ":" in ip)
            with GofileExtractor._doh_lock:
                GofileExtractor._doh_cache[hostname] = (time.monotonic(), list(all_ips))
            logger.debug("DoH ile %s çözümlendi: %s", hostname, all_ips)
            return all_ips
        return []

    def _fetch_via_cffi_with_doh(self, content_id: str) -> Optional[dict[str, Any]]:
        """
        curl_cffi (Chrome TLS taklit) + DoH çözümlenmiş IP üzerinden API çağrısı.
        
        ISP'nin DNS engelini DoH ile aşar, Gofile'ın wt token doğrulaması için
        Chrome TLS parmak izini kullanır.
        """
        try:
            from curl_cffi.requests import Session as CffiSession
            from curl_cffi import CurlHttpVersion, CurlOpt
        except ImportError:
            return None

        ips = self._resolve_via_doh("api.gofile.io")
        if not ips:
            return None

        proxies = {}
        if self._proxy:
            proxies = {"http": self._proxy, "https": self._proxy}

        # Her IP'yi sırayla dene
        last_err = None
        for target_ip in ips:
            # HTTP sürümlerini dene: HTTP/3 önce (UDP), sonra HTTP/2, HTTP/1.1
            for ver_name, ver_code in [
                ("HTTP/3", CurlHttpVersion.V3),
                ("HTTP/2", CurlHttpVersion.V2TLS),
                ("HTTP/1.1", CurlHttpVersion.V1_1),
            ]:
                try:
                    with CffiSession(
                        impersonate="chrome",
                        proxies=proxies,
                        # DNS yerine doğrudan IP kullan
                        curl_options={CurlOpt.RESOLVE: [f"api.gofile.io:443:{target_ip}"]},
                    ) as s:
                        native_ua = s.headers.get("User-Agent") or _DEFAULT_HEADERS["User-Agent"]
                        wt_token = self._compute_wt(self._token, custom_ua=native_ua)
                        url = f"{_API_BASE}/contents/{content_id}?wt={wt_token}&cache=true"
                        headers = self._api_headers(custom_ua=native_ua)
                        cookies = {"wt": wt_token}
                        if self._token:
                            cookies["accountToken"] = self._token

                        resp = s.get(
                            url, headers=headers, cookies=cookies,
                            timeout=15, http_version=ver_code,
                        )
                        data = resp.json()
                        if data.get("status") == "ok":
                            logger.debug("DoH+cffi başarılı: IP=%s %s", target_ip, ver_name)
                            return data.get("data", {})
                        status = str(data.get("status", ""))
                        if "notFound" in status:
                            raise ExtractionError(f"Gofile içerik bulunamadı veya silinmiş: {content_id}")
                        if "notPremium" in status or "password" in status:
                            raise ExtractionError("Bu içerik gizli veya premium üyelik gerektiriyor. Lütfen Gofile hesabınızdan aldığınız API Token'ı (accountToken) Ayarlar > Desteklenen Platformlar > Gofile bölümündeki 'Ayarla' butonundan girin.")
                        if "rateLimit" in status or resp.status_code == 429:
                            raise _RateLimitError(f"Gofile rate-limit (DoH+cffi): {status}")
                        if resp.status_code == 401:
                            # Token geçersiz — yenile ve döngüden çık
                            logger.debug("DoH+cffi 401 — token yenileniyor")
                            self._refresh_token()
                            break
                        logger.debug("DoH+cffi cevabı (%s %s): %s", target_ip, ver_name, status)
                except (ExtractionError, _RateLimitError):
                    raise
                except Exception as exc:
                    logger.debug("DoH+cffi başarısız (IP=%s %s): %s", target_ip, ver_name, exc)
                    last_err = exc
                    continue
        if last_err:
            raise last_err  # type: ignore[misc]
        return None


    def _apply_proxy_to_session(self, proxy_url: str) -> None:
        """requests.Session'a proxy uygula."""
        self._session.proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }

    @staticmethod
    def _get_windows_proxy() -> str:
        """Windows kayıt defterinden sistem proxy'sini oku."""
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
            )
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
            if enabled:
                server = winreg.QueryValueEx(key, "ProxyServer")[0]
                winreg.CloseKey(key)
                if server and "=" not in server:
                    return f"http://{server}"
        except Exception:
            pass
        return ""

    @staticmethod
    def _test_proxy(proxy_url: str, timeout: float = 3.0) -> bool:
        """Proxy'nin Gofile API'sine erişip erişemediğini test et."""
        try:
            resp = requests.get(
                f"{_API_BASE}/",
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=timeout,
                headers=_DEFAULT_HEADERS,
            )
            # 404 bile olsa sunucu yanıt veriyor demektir
            return resp.status_code < 600
        except Exception:
            return False

    @staticmethod
    def _scan_local_proxies() -> str:
        """Yaygın yerel proxy portlarını tara ve çalışanı döndür."""
        for port in _LOCAL_PROXY_PORTS:
            # Önce port açık mı diye bak (hızlı)
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                    pass
            except OSError:
                continue

            # Port açık — proxy türünü tahmin et
            if port in (1080, 1081, 9050, 9150, 10808):
                proxy_url = f"socks5://127.0.0.1:{port}"
            else:
                proxy_url = f"http://127.0.0.1:{port}"

            # Gofile API'sine erişebildiğini doğrula
            if GofileExtractor._test_proxy(proxy_url, timeout=3.0):
                logger.info("Gofile: Yerel proxy bulundu: %s", proxy_url)
                return proxy_url
            else:
                logger.debug("Gofile: Port %d açık ama Gofile'a erişemiyor.", port)

        return ""

    # ------------------------------------------------------------------
    # Token yönetimi
    # ------------------------------------------------------------------

    def _ensure_token(self) -> None:
        """Token hazırla — önce ayarlar, sonra önbellek, sonra API. Birden fazla token varsa havuz oluştur."""
        if self._token and self._tokens_pool:
            return

        tokens_list = []
        site_cfg = getattr(self, "_site_settings", {}).get("gofile", {})
        raw_token = site_cfg.get("token", "").strip()
        if raw_token:
            for t in re.split(r'[,;\n]+', raw_token):
                clean_t = t.strip()
                if len(clean_t) > 10 and clean_t not in tokens_list:
                    tokens_list.append(clean_t)

        cookies_val = site_cfg.get("cookies", "").strip()
        if cookies_val:
            if Path(cookies_val).is_file():
                try:
                    txt = Path(cookies_val).read_text(encoding="utf-8", errors="ignore")
                    for m in re.finditer(r'(?:accountToken|token)\s+([A-Za-z0-9_-]+)', txt):
                        if m.group(1) not in tokens_list:
                            tokens_list.append(m.group(1))
                except Exception:
                    pass
            elif len(cookies_val) > 10 and cookies_val not in tokens_list:
                for t in re.split(r'[,;\n]+', cookies_val):
                    clean_t = t.strip()
                    if len(clean_t) > 10 and clean_t not in tokens_list:
                        tokens_list.append(clean_t)

        if not tokens_list:
            tokens_list.append("xSpfjPMJNfMWKw4cKOaJVBmbzjxeGr3Y")

        self._tokens_pool = tokens_list
        if not self._token or self._token not in self._tokens_pool:
            self._token_index = 0
            self._token = self._tokens_pool[0]

        logger.debug("Gofile havuzdaki token hazır (%d adet): %s…", len(self._tokens_pool), self._token[:8])

    def _rotate_token(self) -> bool:
        """Kullanılan token 429/kota hatası verirse havuzdaki bir sonraki token'a geç."""
        if not getattr(self, "_tokens_pool", None) or len(self._tokens_pool) <= 1:
            return False
        old_token = self._token
        self._token_index = (getattr(self, "_token_index", 0) + 1) % len(self._tokens_pool)
        self._token = self._tokens_pool[self._token_index]
        if self._token != old_token:
            logger.warning("Gofile kota/hız sınırı! Havuzdaki diğer token'a geçildi: %s... (%d/%d)", self._token[:8], self._token_index + 1, len(self._tokens_pool))
            return True
        return False

    def _create_guest_token_cffi(self) -> Optional[str]:
        """curl_cffi Chrome taklit + proxy ile guest token oluştur."""
        try:
            from curl_cffi.requests import Session as CffiSession
            proxies = None
            if self._proxy:
                proxies = {"https": self._proxy, "http": self._proxy}
            with CffiSession(impersonate="chrome", proxies=proxies or {}) as s:
                resp = s.post(f"{_API_BASE}/accounts", headers=_DEFAULT_HEADERS, timeout=10)
                data = resp.json()
                if data.get("status") == "ok":
                    token = data["data"]["token"]
                    try:
                        (Path.home() / ".gofile_token").write_text(token, encoding="utf-8")
                    except Exception:
                        pass
                    logger.debug("Gofile guest token (curl_cffi): %s…", token[:8])
                    return token
        except Exception as exc:
            logger.debug("curl_cffi guest token başarısız: %s", exc)
        return None

    def _create_guest_token_requests(self) -> Optional[str]:
        """Standart requests + proxy ile guest token oluştur."""
        try:
            resp = self._session.post(f"{_API_BASE}/accounts", timeout=6)
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "ok":
                token = data["data"]["token"]
                try:
                    (Path.home() / ".gofile_token").write_text(token, encoding="utf-8")
                except Exception:
                    pass
                logger.debug("Gofile guest token (requests): %s…", token[:8])
                return token
        except Exception as exc:
            logger.debug("requests guest token başarısız: %s", exc)
        return None

    def _compute_wt(self, token: Optional[str] = None, custom_ua: Optional[str] = None) -> str:
        """Gofile wt.obf.js içindeki generateWT (SHA-256) algoritmasını dinamik hesaplar (canlı hotfix manifesti destekli)."""
        import hashlib
        import os
        tok = token if token is not None else (self._token or "")
        ua = custom_ua or _DEFAULT_HEADERS.get("User-Agent", "")
        lang = _DEFAULT_HEADERS.get("Accept-Language", "").split(",")[0] or "en-US"
        interval = 14400
        salt = os.getenv("GOFILE_WT_SALT", "")
        if not salt:
            try:
                from src.engine.site_registry import site_registry
                gf_rule = site_registry.get_site_rule("gofile")
                wt_cfg = gf_rule.get("wt_config") or {}
                salt = str(wt_cfg.get("salt") or "12af056dacea0b")
                interval = int(wt_cfg.get("interval_secs") or 14400)
            except Exception:
                salt = "12af056dacea0b"
        time_slot = str(int(time.time() // max(1, interval)))
        raw = f"{ua}::{lang}::{tok}::{time_slot}::{salt}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _api_headers(self, custom_ua: Optional[str] = None) -> dict[str, str]:
        wt_token = self._compute_wt(self._token, custom_ua=custom_ua)
        h = {
            "X-Website-Token": wt_token,
            **_DEFAULT_HEADERS,
        }
        if custom_ua:
            h["User-Agent"] = custom_ua
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _refresh_token(self) -> None:
        """Eski token'ı sil ve yeni guest token al (özel veya varsayılan hesap tokenı korunur)."""
        site_cfg = getattr(self, "_site_settings", {}).get("gofile", {})
        custom_token = site_cfg.get("token", "").strip() or site_cfg.get("cookies", "").strip()
        if custom_token and len(custom_token) > 10 and not Path(custom_token).is_file():
            self._token = custom_token
            return
        if self._token == "xSpfjPMJNfMWKw4cKOaJVBmbzjxeGr3Y":
            return  # Varsayılan hesap tokenı korunur
        self._token = None
        try:
            (Path.home() / ".gofile_token").unlink(missing_ok=True)
        except Exception:
            pass
        token = self._create_guest_token_cffi() or self._create_guest_token_requests()
        if token:
            self._token = token
            logger.debug("Gofile token yenilendi: %s…", token[:8])

    # ------------------------------------------------------------------
    # İçerik ağacı çekme — 3 katman + rate-limit retry
    # ------------------------------------------------------------------

    _tree_cache: dict[str, tuple[float, dict[str, Any]]] = {}
    _tree_lock = threading.Lock()

    def _fetch_content_tree(self, content_id: str) -> dict[str, Any]:
        """Kısa süreli (5 dk) önbellek: metadata + indirme aşaması aynı ağacı tekrar çekmesin."""
        with GofileExtractor._tree_lock:
            hit = GofileExtractor._tree_cache.get(content_id)
            if hit and time.monotonic() - hit[0] < _TREE_TTL_SECS:
                return hit[1]
        tree = self._fetch_content_tree_uncached(content_id)
        with GofileExtractor._tree_lock:
            GofileExtractor._tree_cache[content_id] = (time.monotonic(), tree)
        return tree

    def _fetch_content_tree_uncached(self, content_id: str) -> dict[str, Any]:
        """İçerik ağacını çek: DoH bypass → curl_cffi → requests → (rate-limit retry) → HTML scrape."""
        last_exc: Optional[Exception] = None

        # Katman 0: DoH + curl_cffi Chrome TLS ile ISP engelini atlatarak bağlan
        # (DNS bloğunu DoH ile, SNI/wt token doğrulamasını Chrome TLS taklitiyle geçer)
        try:
            tree = self._fetch_via_cffi_with_doh(content_id)
            if tree:
                logger.info("Gofile içeriği DoH+cffi ile çekildi.")
                return tree
        except (_RateLimitError, ExtractionError) as exc:
            if any(x in str(exc).lower() for x in ("premium", "bulunamadı", "şifre")):
                raise
            last_exc = exc
            logger.debug("DoH+cffi başarısız: %s", exc)
        except Exception as exc:
            last_exc = exc
            logger.debug("DoH+cffi başarısız: %s", exc)

        for attempt in range(1, 4):
            # Katman 1: curl_cffi (Cloudflare bypass + proxy)
            try:
                tree = self._fetch_via_cffi(content_id)
                if tree:
                    logger.info("Gofile içeriği curl_cffi ile çekildi (deneme %d).", attempt)
                    return tree
            except _RateLimitError as e:
                last_exc = e
                logger.warning("Gofile rate-limit (cffi), yeni token deneniyor... (%d/3)", attempt)
                self._refresh_token()
                backoff = (2 ** attempt) + random.uniform(0.5, 2.5)
                time.sleep(backoff)
                continue
            except ExtractionError as exc:
                if any(x in str(exc).lower() for x in ("premium", "bulunamadı", "şifre")):
                    raise
                logger.debug("curl_cffi Gofile API başarısız: %s", exc)
                last_exc = exc
            except Exception as exc:
                logger.debug("curl_cffi Gofile API başarısız: %s", exc)
                last_exc = exc

            # Katman 2: standart requests (proxy ile)
            try:
                tree = self._fetch_via_requests(content_id)
                if tree:
                    logger.info("Gofile içeriği requests ile çekildi (deneme %d).", attempt)
                    return tree
            except _RateLimitError as e:
                last_exc = e
                logger.warning("Gofile rate-limit (requests), yeni token deneniyor... (%d/3)", attempt)
                self._refresh_token()
                backoff = (2 ** attempt) + random.uniform(0.5, 2.5)
                time.sleep(backoff)
                continue
            except ExtractionError as exc:
                if any(x in str(exc).lower() for x in ("premium", "bulunamadı", "şifre")):
                    raise
                logger.debug("requests Gofile API başarısız: %s", exc)
                last_exc = exc
            except Exception as exc:
                logger.debug("requests Gofile API başarısız: %s", exc)
                last_exc = exc

            break  # Rate-limit değilse döngüyü kır, Node.js hibrit ve HTML'e geç

        # Katman 3: Node.js üzerinden hibrit (tam tarayıcı simülasyonlu) çekim
        try:
            tree = self._fetch_via_node(content_id)
            if tree:
                logger.info("Gofile içeriği Node.js hibrit motoru ile çekildi.")
                return tree
        except _RateLimitError as exc:
            last_exc = exc
        except ExtractionError as exc:
            if any(x in str(exc).lower() for x in ("premium", "bulunamadı", "şifre")):
                raise
            last_exc = exc
        except Exception as exc:
            logger.debug("Node.js hibrit motor başarısız: %s", exc)
            last_exc = exc

        # Katman 4: HTML sayfasından scrape
        try:
            tree = self._fetch_via_html(content_id)
            if tree:
                logger.info("Gofile içeriği HTML scrape ile çekildi.")
                return tree
        except Exception as exc:
            logger.debug("HTML scrape başarısız: %s", exc)
            last_exc = exc

        if isinstance(last_exc, _RateLimitError) or "429" in str(last_exc) or "rateLimit" in str(last_exc):
            raise ExtractionError(
                "Gofile API sunucusu şu anda IP adresinize (veya kullandığınız VPN/Cloudflare WARP hattına) geçici hız sınırı (Rate-Limit 429) uyguluyor.\n"
                "💡 Çözüm Bilgisi:\n"
                "• Gofile, veri merkezi ve ortak VPN/Cloudflare WARP çıkış IP'lerine sıkı hız sınırları uygular.\n"
                "• Lütfen 2-3 dakika bekledikten sonra tekrar deneyin (kısıtlama otomatik sıfırlanır).\n"
                "• Alternatif olarak Ayarlar > Proxy alanına farklı bir konut (residential) HTTP/SOCKS5 proxy girebilir veya VPN'i kapatıp açabilirsiniz."
            ) from last_exc
        raise ExtractionError(
            "Gofile sunucusuna bağlanılamadı (Bağlantı Zaman Aşımı / Timeout).\n"
            "💡 Çözüm Bilgisi:\n"
            "• Gofile güvenlik duvarı (WAF) mevcut IP adresinizin üst üste denemelerini veya bağlantı türünü geçici olarak engellemiş olabilir.\n"
            "• Çözüm: Cloudflare WARP (ücretsiz sistem VPN) kurup bağlayın (https://one.one.one.one) — veya halihazırda bağlıysa kapatıp normal internet bağlantınızla deneyin.\n"
            "• Ayarlar > Proxy alanından aktif bir HTTP/SOCKS5 proxy ile de bağlanabilirsiniz."
        ) from last_exc

    def _fetch_via_cffi(self, content_id: str) -> Optional[dict[str, Any]]:
        """curl_cffi Chrome taklit + HTTP/3 öncelikli API çağrısı.
        
        HTTP/3 (QUIC) UDP üzerinde çalışır — ISP'nin TCP tabanlı SNI filtrelerini atlatır.
        """
        try:
            from curl_cffi.requests import Session as CffiSession
            from curl_cffi import CurlHttpVersion
        except ImportError:
            return None

        proxies = {}
        if self._proxy:
            proxies = {"http": self._proxy, "https": self._proxy}

        # HTTP sürümlerini öncelik sırasıyla dene: HTTP/3 → HTTP/2 → HTTP/1.1
        # HTTP/3 (QUIC/UDP) ISP TCP engelini tamamen atlar
        http_versions = []
        if not self._proxy:
            # Proxy yokken HTTP/3 önce (QUIC/UDP - ISP DPI'yi atlar)
            http_versions = [
                ("HTTP/3", CurlHttpVersion.V3),
                ("HTTP/2", CurlHttpVersion.V2TLS),
                ("HTTP/1.1", CurlHttpVersion.V1_1),
            ]
        else:
            # Proxy varken HTTP/3 çalışmayabilir, önce HTTP/2
            http_versions = [
                ("HTTP/2", CurlHttpVersion.V2TLS),
                ("HTTP/1.1", CurlHttpVersion.V1_1),
            ]

        last_err = None
        for ver_name, ver_code in http_versions:
            try:
                with CffiSession(impersonate="chrome", proxies=proxies) as s:
                    native_ua = s.headers.get("User-Agent") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                    wt_token = self._compute_wt(self._token, custom_ua=native_ua)
                    url = f"{_API_BASE}/contents/{content_id}?wt={wt_token}&cache=true"
                    headers = self._api_headers(custom_ua=native_ua)
                    cookies = {"wt": wt_token}
                    if self._token:
                        cookies["accountToken"] = self._token

                    resp = s.get(
                        url, headers=headers, cookies=cookies,
                        timeout=15, http_version=ver_code,
                    )
                    data = resp.json()
                    if data.get("status") == "ok":
                        logger.debug("Gofile API başarılı: %s", ver_name)
                        return data.get("data", {})
                    status = str(data.get("status", ""))
                    if "notFound" in status:
                        raise ExtractionError(f"Gofile içerik bulunamadı veya silinmiş: {content_id}")
                    if "notPremium" in status or "password" in status:
                        raise ExtractionError("Bu içerik gizli veya premium üyelik gerektiriyor. Lütfen Gofile hesabınızdan aldığınız API Token'ı (accountToken) Ayarlar > Desteklenen Platformlar > Gofile bölümündeki 'Ayarla' butonundan girin.")
                    if "rateLimit" in status or resp.status_code == 429:
                        if "limit exceeded" in resp.text.lower() or "limit exceeded" in status.lower():
                            if self._rotate_token():
                                raise _RateLimitError("Gofile API kota aşıldı, sonraki token denenecek...")
                            raise ExtractionError("Gofile Kota Hatası (HTTP 429): Kullanılan API tokenın (veya klasörün) 1000 GB'lık aylık indirme kotası dolmuştur! ('Monthly download limit exceeded'). Çözüm: Ayarlar > Desteklenen Platformlar > Gofile kısmına kotası dolmamış yeni/başka Gofile API Token(lar)ı ekleyin (birden fazla tokenı alt alta veya virgülle girebilirsiniz).")
                        raise _RateLimitError(f"Gofile rate-limit: {status}")
                    logger.debug("cffi API cevabı (%s): %s", ver_name, status)
            except (ExtractionError, _RateLimitError):
                raise
            except Exception as exc:
                logger.debug("cffi %s başarısız: %s", ver_name, exc)
                last_err = exc
                continue  # Sonraki HTTP versiyonunu dene

        if last_err:
            raise last_err  # type: ignore[misc]
        return None


    def _fetch_via_requests(self, content_id: str) -> Optional[dict[str, Any]]:
        """Standart requests + proxy ile API çağrısı."""
        wt_token = self._compute_wt(self._token)
        url = f"{_API_BASE}/contents/{content_id}?wt={wt_token}&cache=true"
        headers = self._api_headers()
        cookies = {"wt": wt_token}
        if self._token:
            cookies["accountToken"] = self._token
        resp = self._session.get(url, headers=headers, cookies=cookies, timeout=10)
        data = resp.json()
        if data.get("status") == "ok":
            return data.get("data", {})
        status = str(data.get("status", ""))
        if "notFound" in status:
            raise ExtractionError(f"Gofile içerik bulunamadı veya silinmiş: {content_id}")
        if "notPremium" in status or "password" in status:
            raise ExtractionError("Bu içerik gizli veya premium üyelik gerektiriyor. Lütfen Gofile hesabınızdan aldığınız API Token'ı (accountToken) Ayarlar > Desteklenen Platformlar > Gofile bölümündeki 'Ayarla' butonundan girin.")
        if "rateLimit" in status or resp.status_code == 429:
            if "limit exceeded" in resp.text.lower() or "limit exceeded" in status.lower():
                if self._rotate_token():
                    raise _RateLimitError("Gofile API kota aşıldı, sonraki token denenecek...")
                raise ExtractionError("Gofile Kota Hatası (HTTP 429): Kullanılan API tokenın (veya klasörün) 1000 GB'lık aylık indirme kotası dolmuştur! ('Monthly download limit exceeded'). Çözüm: Ayarlar > Desteklenen Platformlar > Gofile kısmına kotası dolmamış yeni/başka Gofile API Token(lar)ı ekleyin (birden fazla tokenı alt alta veya virgülle girebilirsiniz).")
            raise _RateLimitError(f"Gofile rate-limit: {status}")
        return None

    def _fetch_via_node(self, content_id: str) -> Optional[dict[str, Any]]:
        """Node.js ve canlı wt.obf.js üzerinden hibrit (tam tarayıcı simülasyonlu) API çağrısı."""
        import shutil
        import subprocess
        import tempfile

        if not shutil.which("node"):
            return None

        token = self._token or ""
        script_content = f"""
const https = require('https');
const http = require('http');

function fetchUrl(url, headers = {{}}, timeout = 15000) {{
    return new Promise((resolve, reject) => {{
        const client = url.startsWith('https') ? https : http;
        const req = client.get(url, {{ headers, timeout }}, (res) => {{
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => resolve({{ status: res.statusCode, text: data }}));
        }});
        req.on('error', reject);
        req.on('timeout', () => req.destroy(new Error('timeout')));
    }});
}}

(async () => {{
    try {{
        const tok = {json.dumps(token)};
        let wt = '';
        try {{
            const wtRes = await fetchUrl('https://gofile.io/dist/js/wt.obf.js', {{}}, 8000);
            if (wtRes.status === 200) {{
                eval(wtRes.text);
                if (typeof generateWT === 'function') wt = generateWT(tok);
            }}
        }} catch (e) {{}}

        if (!wt) {{
            const crypto = require('crypto');
            const ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36';
            const timeSlot = Math.floor(Date.now() / 1000 / 14400).toString();
            wt = crypto.createHash('sha256').update(`${{ua}}::en-US::${{tok}}::${{timeSlot}}::9844d94d963d30`).digest('hex');
        }}

        const headers = {{
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'X-BL': 'en-US',
            'Origin': 'https://gofile.io',
            'Referer': `https://gofile.io/d/{content_id}`,
            'X-Website-Token': wt,
            'Cookie': `wt=${{wt}}` + (tok ? `; accountToken=${{tok}}` : '')
        }};
        if (tok) headers['Authorization'] = `Bearer ${{tok}}`;
        const apiRes = await fetchUrl(`https://api.gofile.io/contents/{content_id}?wt=${{wt}}&cache=true`, headers);
        console.log(apiRes.text);
    }} catch (err) {{
        process.exit(1);
    }}
}})();
"""
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as tf:
                tf.write(script_content)
                temp_path = tf.name
            try:
                out = subprocess.check_output(["node", temp_path], timeout=25, stderr=subprocess.DEVNULL)
                data = json.loads(out.decode("utf-8", errors="ignore"))
                if data.get("status") == "ok":
                    return data.get("data", {})
                status = str(data.get("status", ""))
                if "notFound" in status:
                    raise ExtractionError(f"Gofile içerik bulunamadı veya silinmiş: {content_id}")
                if "notPremium" in status or "password" in status:
                    raise ExtractionError("Bu içerik gizli veya premium üyelik gerektiriyor. Lütfen Gofile hesabınızdan aldığınız API Token'ı (accountToken) Ayarlar > Desteklenen Platformlar > Gofile bölümündeki 'Ayarla' butonundan girin.")
                if "rateLimit" in status or "429" in status:
                    raise _RateLimitError(f"Gofile rate-limit: {status}")
            finally:
                Path(temp_path).unlink(missing_ok=True)
        except (_RateLimitError, ExtractionError):
            raise
        except Exception as exc:
            logger.debug("Node.js hibrit çekim başarısız: %s", exc)
        return None

    def _fetch_via_html(self, content_id: str) -> Optional[dict[str, Any]]:
        """
        Gofile sayfasını (gofile.io/d/ID) indir ve gömülü JSON verisinden
        dosya listesini çıkar. api.gofile.io engellendiğinde son çare.
        """
        page_url = f"{_WEBSITE}/d/{content_id}"
        headers = {**_DEFAULT_HEADERS, "Accept": "text/html,application/xhtml+xml,*/*"}

        html = None
        # curl_cffi ile dene (proxy + Chrome TLS taklit)
        try:
            from curl_cffi.requests import Session as CffiSession
            proxies = {}
            if self._proxy:
                proxies = {"http": self._proxy, "https": self._proxy}
            with CffiSession(impersonate="chrome", proxies=proxies) as s:
                resp = s.get(page_url, headers=headers, timeout=15)
                if resp.status_code == 200:
                    html = resp.text
        except Exception:
            pass

        if not html:
            try:
                resp = self._session.get(page_url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    html = resp.text
            except Exception:
                pass

        if not html:
            return None

        # Nuxt/Vue uygulamaları window.__nuxt__ veya __NUXT_DATA__ içine veri gömer
        patterns = [
            r'<script[^>]*id=["\']__NUXT_DATA__["\'][^>]*>(.*?)</script>',
            r'window\.__NUXT__\s*=\s*({.*?})\s*;?\s*</script>',
            r'<script[^>]*>\s*window\.__nuxt[^=]*=\s*({.*?})\s*</script>',
        ]

        for pat in patterns:
            matches = re.findall(pat, html, re.DOTALL)
            for match in matches:
                try:
                    data = json.loads(match)
                    files = self._extract_files_from_nuxt(data)
                    if files:
                        return {
                            "type": "folder",
                            "name": content_id,
                            "children": {f["id"]: f for f in files},
                        }
                except Exception:
                    continue

        # Son çare: doğrudan "link" içeren dosya objelerini bul
        file_links = re.findall(
            r'"link"\s*:\s*"(https://[^"]+)"[^}]*"name"\s*:\s*"([^"]+)"',
            html
        )
        if file_links:
            files = []
            for link, name in file_links:
                files.append({
                    "id": re.sub(r'[^a-zA-Z0-9]', '', name)[:12],
                    "type": "file",
                    "name": name,
                    "link": link,
                    "size": 0,
                })
            return {
                "type": "folder",
                "name": content_id,
                "children": {f["id"]: f for f in files},
            }

        return None

    def _extract_files_from_nuxt(self, data: Any) -> list[dict[str, Any]]:
        """Nuxt JSON yapısından dosyaları çıkar."""
        files = []
        if isinstance(data, dict):
            if data.get("type") == "file" and data.get("link"):
                files.append(data)
            for v in data.values():
                files.extend(self._extract_files_from_nuxt(v))
        elif isinstance(data, list):
            for item in data:
                files.extend(self._extract_files_from_nuxt(item))
        return files

    # ------------------------------------------------------------------
    # Dosya ağacı dolaşımı
    # ------------------------------------------------------------------

    def _collect_files(self, node: dict[str, Any]) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        node_type = node.get("type", "")
        if node_type == "file":
            files.append(node)
        elif node_type in ("folder", ""):
            for key in ("children", "contents"):
                children = node.get(key, {})
                if isinstance(children, dict):
                    for child in children.values():
                        files.extend(self._collect_files(child))
                elif isinstance(children, list):
                    for child in children:
                        files.extend(self._collect_files(child))
        return files

    # ------------------------------------------------------------------
    # Dosya indirme (proxy destekli)
    # ------------------------------------------------------------------

    def _download_file(
        self,
        url: str,
        dest: Path,
        total_size: int,
        progress_hook: Optional[Callable[[dict[str, Any]], None]],
        cancel_event: Optional[threading.Event] = None,
        pause_event: Optional[threading.Event] = None,
        max_conns_override: Optional[int] = None,
    ) -> None:
        proxies = {}
        if self._proxy:
            proxies = {"http": self._proxy, "https": self._proxy}

        seg_enabled, seg_conns, seg_mode = config.get_segmented_settings()
        if max_conns_override is not None:
            seg_conns = max(2, min(seg_conns, int(max_conns_override)))
        host = urlparse(url).hostname or ""
        downloaded = 0
        resumable = False
        rotated_once = False
        use_v3_fallback = False
        attempt = 0

        while attempt < _MAX_RETRIES:
            attempt += 1
            _wait_if_paused(pause_event, cancel_event)
            session_ctx: Any = None
            use_cffi = False
            resp = None
            try:
                # curl_cffi öncelikli (IPv4 + HTTP/2 TLS varsayılan; yalnızca bağlantı hatasında V3 denenir)
                try:
                    _key = ("v3" if use_v3_fallback else "v2", self._proxy or "")
                    _cur = getattr(self._dl_local, "dl_sess", None)
                    if _cur and _cur[0] == _key:
                        session_ctx = _cur[1]
                    else:
                        if _cur:
                            try:
                                _cur[1].close()
                            except Exception:
                                pass
                        session_ctx = self._new_cffi_dl_session(proxies, use_v3=use_v3_fallback)
                        self._dl_local.dl_sess = (_key, session_ctx)
                    use_cffi = True
                except ImportError:
                    session_ctx = self._session
                    use_cffi = False

                native_ua = _DEFAULT_HEADERS["User-Agent"]
                if use_cffi and hasattr(session_ctx, "headers"):
                    native_ua = session_ctx.headers.get("User-Agent") or native_ua

                dl_headers = {
                    "User-Agent": native_ua,
                    "Accept": "*/*",
                    "Referer": f"{_WEBSITE}/",
                    "Origin": _WEBSITE,
                }
                cookies = {}

                # CDN tokenı her denemede gönder (CDN_NEED_AUTH retry döngüsünü önler)
                if self._token:
                    wt_token = self._compute_wt(self._token, custom_ua=native_ua)
                    cookies = {"wt": wt_token, "accountToken": self._token}
                    dl_headers["Authorization"] = f"Bearer {self._token}"
                    dl_headers["X-Website-Token"] = wt_token

                # Mod/Boyut kuralına göre doğrudan IDM / akıllı parçalı indirme başlat
                if (
                    downloaded == 0
                    and seg_enabled
                    and use_cffi
                    and should_start_direct_segmented(total_size, seg_mode)
                    and host not in self._no_parallel_hosts
                ):
                    try:
                        if self._download_parallel(
                            url, dest, total_size, dl_headers, cookies, proxies, progress_hook,
                            start=0, num_workers=seg_conns, cancel_event=cancel_event,
                            pause_event=pause_event, mode=seg_mode,
                        ):
                            return
                    except ExtractionCancelled:
                        raise
                    except Exception as pexc:
                        if type(pexc).__name__ == "_CancelledError" or (cancel_event is not None and cancel_event.is_set()):
                            raise ExtractionCancelled("İndirme iptal edildi.") from pexc
                        self._mark_host_slow(host)
                        contig = int(getattr(pexc, "contiguous_bytes", 0) or 0)
                        if contig > downloaded and dest.exists():
                            downloaded = contig
                            resumable = True
                            try:
                                with open(dest, "r+b") as _tfp:
                                    _tfp.truncate(downloaded)
                            except Exception:
                                pass
                        logger.debug("Gofile parçalı mod geçici hata (korunan=%d KB), tek bağlantıya dönülüyor (%s): %s", downloaded // 1024, host, pexc)

                req_headers = dict(dl_headers)
                if downloaded > 0 and resumable and dest.exists():
                    req_headers["Range"] = f"bytes={downloaded}-"

                resp = session_ctx.get(
                    url, stream=True, headers=req_headers,
                    cookies=cookies, allow_redirects=False, timeout=(8, 15)
                )

                if resp.status_code in (301, 302, 303, 307, 308):
                    if attempt == 1 and self._token:
                        logger.info("Gofile CDN oturum istiyor, token ile deneniyor (%s)...", dest.name)
                        raise ValueError("CDN_NEED_AUTH")
                    raise ExtractionError(f"Gofile CDN yönlendirmesi alındı (HTTP {resp.status_code}). İçerik gizli/premium olabilir.")

                if resp.status_code == 429:
                    err_body = resp.text.strip().lower()
                    if self._rotate_token():
                        raise _RateLimitError("Gofile CDN kota/hız sınırı, havuzdan sonraki token ile deneniyor...")
                    if "limit exceeded" in err_body or "monthly download limit" in err_body:
                        raise ExtractionError("Gofile Aylık Kota Doldu (HTTP 429): Kullanılan API tokenın (veya klasörün) 1000 GB'lık aylık indirme kotası dolmuştur! ('Monthly download limit exceeded'). Çözüm: Ayarlar > Desteklenen Platformlar > Gofile ekranından kotası dolmamış yeni/başka Gofile API Token(lar)ı ekleyin (birden fazla tokenı alt alta veya virgülle girebilirsiniz).")
                    raise _RateLimitError("Gofile hız sınırı: çok fazla istek.")
                if resp.status_code in (401, 403):
                    raise ExtractionError(f"HTTP {resp.status_code}: Gofile erişim reddetti.")
                resp.raise_for_status()

                ctype = resp.headers.get("content-type", "").lower()
                if "text/html" in ctype or "text/plain" in ctype:
                    raise ExtractionError("Gofile indirme adresi geçersiz (HTML/metin sayfası döndü).")

                is_resumed = (downloaded > 0 and resp.status_code == 206)
                if not is_resumed:
                    downloaded = 0
                    total = int(resp.headers.get("content-length", 0)) or total_size
                else:
                    cr = resp.headers.get("content-range", "")
                    total = int(cr.rsplit("/", 1)[1]) if "/" in cr and cr.rsplit("/", 1)[1].isdigit() else (total_size or downloaded)

                if resp.headers.get("accept-ranges", "").lower() == "bytes" or resp.status_code == 206:
                    resumable = True

                switch_at = -1
                paused_mid = False
                start_ts = time.monotonic()
                last_emit = 0.0
                session_bytes = 0
                speed_tracker = SlidingSpeedTracker(window=3.0)
                first_chunk_checked = is_resumed
                open_mode = "r+b" if is_resumed else "wb"

                with open(dest, open_mode) as fp:
                    if is_resumed:
                        fp.seek(downloaded)
                    _is_cffi_r = "curl_cffi" in type(resp).__module__
                    _chunk_it = resp.iter_content() if _is_cffi_r else resp.iter_content(chunk_size=16384)
                    for chunk in _chunk_it:
                        if cancel_event is not None and cancel_event.is_set():
                            raise ExtractionCancelled("İndirme iptal edildi.")
                        if pause_event is not None and not pause_event.is_set():
                            paused_mid = True
                            break
                        if not chunk:
                            continue

                        if not first_chunk_checked:
                            first_chunk_checked = True
                            if chunk.lstrip().startswith((b"<!doctype html", b"<!DOCTYPE html", b"<html")):
                                fp.close()
                                dest.unlink(missing_ok=True)
                                if attempt == 1 and self._token:
                                    raise ValueError("CDN_NEED_AUTH")
                                raise ExtractionError("Gofile indirme adresi HTML sayfası döndürdü.")

                        fp.write(chunk)
                        clen = len(chunk)
                        downloaded += clen
                        session_bytes += clen
                        now = time.monotonic()
                        speed_tracker.add(clen, now)

                        _el = now - start_ts
                        if _el >= 3.0 and (session_bytes / _el) < 350 * 1024 and (total - downloaded) >= 768 * 1024:
                            self._mark_host_slow(host)
                            if (
                                seg_enabled
                                and seg_mode in ("adaptive", "large", "always", "turbo")
                                and (total - downloaded) >= min_split_bytes(seg_mode)
                                and use_cffi
                                and host not in self._no_parallel_hosts
                            ):
                                self._dl_local.dl_sess = None
                                switch_at = downloaded
                                break
                            if not rotated_once and resumable and use_cffi:
                                rotated_once = True
                                self._dl_local.dl_sess = None
                                attempt -= 1
                                raise ValueError("FAST_ROTATE")

                        if progress_hook and (now - last_emit >= 0.15):
                            last_emit = now
                            speed = speed_tracker.rate(now)
                            self._last_speed = speed
                            eta = int((total - downloaded) / speed) if speed > 0 and total > 0 else 0
                            progress_hook({
                                "status": "downloading",
                                "status_detail": "Tek bağlantı iniyor",
                                "downloaded_bytes": downloaded,
                                "total_bytes": total,
                                "speed": speed,
                                "eta": eta,
                                "filename": str(dest),
                            })

                if hasattr(resp, "close"):
                    try:
                        resp.close()
                    except Exception:
                        pass
                resp = None

                if paused_mid:
                    _wait_if_paused(pause_event, cancel_event)
                    if not resumable:
                        downloaded = 0
                    self._resume_tail(
                        url, dest, total, downloaded, resumable, dl_headers, cookies, proxies,
                        progress_hook, cancel_event, pause_event, seg_enabled, seg_conns, seg_mode, use_cffi,
                    )
                    return

                if switch_at >= 0:
                    if use_cffi and hasattr(session_ctx, "close"):
                        try:
                            session_ctx.close()
                        except Exception:
                            pass
                    smart_n = compute_smart_workers(total - switch_at, seg_conns, seg_mode)
                    logger.info(
                        "Gofile yavaş (%s): kalan kısım %d/%d parçalı indirmeye geçiliyor (%s).",
                        dest.name, smart_n, seg_conns, seg_mode,
                    )
                    try:
                        if self._download_parallel(
                            url, dest, total, dl_headers, cookies, proxies, progress_hook,
                            start=switch_at, num_workers=seg_conns,
                            cancel_event=cancel_event, pause_event=pause_event,
                            mode=seg_mode, skip_probe=resumable,
                        ):
                            return
                    except ExtractionCancelled:
                        raise
                    except Exception as pexc:
                        if type(pexc).__name__ == "_CancelledError" or (cancel_event is not None and cancel_event.is_set()):
                            raise ExtractionCancelled("İndirme iptal edildi.") from pexc
                        self._mark_host_slow(host)
                        contig = int(getattr(pexc, "contiguous_bytes", 0) or 0)
                        if contig > downloaded and dest.exists():
                            downloaded = contig
                            resumable = True
                            try:
                                with open(dest, "r+b") as _tfp:
                                    _tfp.truncate(downloaded)
                            except Exception:
                                pass
                        raise ValueError(f"PARALLEL_FAILED: {pexc}")
                    self._no_parallel_hosts.add(host)
                    raise ValueError("PARALLEL_UNSUPPORTED")

                if progress_hook:
                    progress_hook({
                        "status": "downloading",
                        "downloaded_bytes": downloaded,
                        "total_bytes": total or downloaded,
                        "speed": speed_tracker.rate(),
                        "eta": 0,
                        "filename": str(dest),
                    })
                return

            except BaseException as exc:
                if resp is not None and hasattr(resp, "close"):
                    try:
                        resp.close()
                    except Exception:
                        pass
                if (
                    isinstance(exc, ExtractionCancelled)
                    or type(exc).__name__ == "_CancelledError"
                    or (cancel_event is not None and cancel_event.is_set())
                ):
                    dest.unlink(missing_ok=True)
                    if isinstance(exc, ExtractionCancelled):
                        raise
                    raise ExtractionCancelled("İndirme iptal edildi.") from exc

                if use_cffi and session_ctx and hasattr(session_ctx, "close"):
                    try:
                        session_ctx.close()
                    except Exception:
                        pass
                    self._dl_local.dl_sess = None
                if "timed out" in str(exc).lower() or "connection" in str(exc).lower():
                    self._mark_host_slow(host)
                    if attempt == 1:
                        use_v3_fallback = not use_v3_fallback
                if dest.exists() and dest.stat().st_size < 50000 and "html" in str(dest).lower():
                    dest.unlink(missing_ok=True)
                if any(x in str(exc).lower() for x in ("premium", "bulunamadı", "şifre")):
                    raise
                if attempt >= _MAX_RETRIES:
                    raise ExtractionError(
                        f"Gofile dosya indirme {_MAX_RETRIES} denemeden sonra başarısız: {exc}"
                    ) from exc
                wait = _RETRY_BACKOFF * (2 ** max(0, attempt - 1)) + random.uniform(0.5, 1.5)
                exc_low = str(exc).lower()
                if "FAST_ROTATE" in str(exc):
                    wait = 0.05
                    logger.info("Gofile yavaş soket (%s: %d KB alındı), taze bağlantı & protokol deneniyor.", dest.name, int(downloaded / 1024))
                elif any(k in exc_low for k in ("timed out", "curl: (28)", "ngtcp2", "connection reset", "recv failure")):
                    GofileExtractor._prefer_v2 = not getattr(GofileExtractor, "_prefer_v2", True)
                    wait = 0.25
                    logger.warning("Gofile bağlantı hatası/zaman aşımı (%s), protokol değiştirilip yeniden deneniyor: %s", dest.name, exc)
                elif "SLOW_CONNECTION" in str(exc) or "PARALLEL_" in str(exc):
                    wait = 0.3
                    logger.info("Gofile bağlantı yenileniyor (%s).", dest.name)
                elif "CDN_NEED_AUTH" in str(exc):
                    wait = 0.3
                    logger.debug("Gofile retry %d/%d (%.1fs): %s", attempt, _MAX_RETRIES, wait, exc)
                else:
                    logger.warning("Gofile retry %d/%d (%.1fs): %s", attempt, _MAX_RETRIES, wait, exc)
                if _sleep_cancelable(wait, cancel_event):
                    dest.unlink(missing_ok=True)
                    raise ExtractionCancelled("İndirme iptal edildi.")

    def _resume_tail(
        self,
        url: str,
        dest: Path,
        total: int,
        start_offset: int,
        resumable: bool,
        dl_headers: dict[str, str],
        cookies: dict[str, str],
        proxies: dict[str, str],
        progress_hook: Optional[Callable[[dict[str, Any]], None]],
        cancel_event: Optional[threading.Event],
        pause_event: Optional[threading.Event],
        seg_enabled: bool,
        seg_conns: int,
        seg_mode: str,
        use_cffi: bool,
    ) -> None:
        """Duraklatma sonrası kaldığı bayttan devam et (Range destekliyorsa parçalıya da geçebilir)."""
        host = urlparse(url).hostname or ""
        if (
            resumable
            and seg_enabled
            and use_cffi
            and total > 0
            and (total - start_offset) >= min_split_bytes(seg_mode)
            and host not in self._no_parallel_hosts
        ):
            if self._download_parallel(
                url, dest, total, dl_headers, cookies, proxies, progress_hook,
                start=start_offset, num_workers=seg_conns,
                cancel_event=cancel_event, pause_event=pause_event,
                mode=seg_mode, skip_probe=True,
            ):
                return

        downloaded = start_offset if resumable else 0
        while True:
            _wait_if_paused(pause_event, cancel_event)
            req_h = dict(dl_headers)
            if downloaded > 0:
                req_h["Range"] = f"bytes={downloaded}-"
            sess = self._new_cffi_dl_session(proxies, use_v3=False) if use_cffi else self._session
            paused_again = False
            try:
                resp = sess.get(url, stream=True, headers=req_h, cookies=cookies, allow_redirects=False, timeout=(15, 25))
                resp.raise_for_status()
                is_part = (downloaded > 0 and resp.status_code == 206)
                if not is_part:
                    downloaded = 0
                mode = "r+b" if is_part else "wb"
                tracker = SlidingSpeedTracker(window=3.0)
                last_emit = 0.0
                with open(dest, mode) as fp:
                    if is_part:
                        fp.seek(downloaded)
                    _is_cffi_t = "curl_cffi" in type(resp).__module__
                    _chunk_it_t = resp.iter_content() if _is_cffi_t else resp.iter_content(chunk_size=16384)
                    for chunk in _chunk_it_t:
                        if cancel_event is not None and cancel_event.is_set():
                            raise ExtractionCancelled("İndirme iptal edildi.")
                        if pause_event is not None and not pause_event.is_set():
                            paused_again = True
                            break
                        if not chunk:
                            continue
                        fp.write(chunk)
                        clen = len(chunk)
                        downloaded += clen
                        now = time.monotonic()
                        tracker.add(clen, now)
                        if progress_hook and (now - last_emit >= 0.15):
                            last_emit = now
                            sp = tracker.rate(now)
                            self._last_speed = sp
                            progress_hook({
                                "status": "downloading",
                                "downloaded_bytes": downloaded,
                                "total_bytes": total,
                                "speed": sp,
                                "eta": int((total - downloaded) / sp) if sp > 0 and total > 0 else 0,
                                "filename": str(dest),
                            })
            finally:
                if use_cffi and hasattr(sess, "close"):
                    try:
                        sess.close()
                    except Exception:
                        pass
            if not paused_again:
                return

    def _download_parallel(
        self,
        url: str,
        dest: Path,
        total_size: int,
        dl_headers: dict[str, str],
        cookies: dict[str, str],
        proxies: dict[str, str],
        progress_hook: Optional[Callable[[dict[str, Any]], None]],
        start: int = 0,
        num_workers: int = _PARALLEL_CONNS,
        cancel_event: Optional[threading.Event] = None,
        pause_event: Optional[threading.Event] = None,
        mode: str = "adaptive",
        skip_probe: bool = False,
    ) -> bool:
        """Range + Smart Work-Stealing ile çok parçalı indirme. Sunucu Range desteklemiyorsa False döner."""
        host = urlparse(url).hostname or ""
        total = total_size
        warm_pool = self._get_thread_host_pool(host)
        already_verified = host in self._range_verified_hosts and total_size > 0

        if not ((skip_probe or already_verified) and total_size > 0):
            from curl_cffi import CurlHttpVersion
            probe_ok = False
            last_probe_err: Optional[Exception] = None
            proto_order = [CurlHttpVersion.V2TLS]
            if host not in self._host_v2_only and not self._proxy:
                best_p = self._host_best_proto.get(host)
                proto_order = [best_p] if best_p is not None else [CurlHttpVersion.V2TLS, CurlHttpVersion.V1_1]
                if CurlHttpVersion.V1_1 not in proto_order:
                    proto_order.append(CurlHttpVersion.V1_1)
                if CurlHttpVersion.V3 not in proto_order:
                    proto_order.append(CurlHttpVersion.V3)
            for p_try, p_ver in enumerate(proto_order[:3]):
                probe_sess = (
                    warm_pool.pop(0, None) if p_try == 0 and host not in self._host_best_proto else None
                ) or self._new_cffi_dl_session(proxies, http_ver=p_ver)
                probe_kept = False
                r = None
                try:
                    h = dict(dl_headers)
                    h["Range"] = "bytes=0-0"
                    r = probe_sess.get(
                        url, stream=True, headers=h, cookies=cookies, allow_redirects=False, timeout=(6, 10),
                    )
                    cr = r.headers.get("content-range", "")
                    if r.status_code == 206 and "/" in cr:
                        total = int(cr.rsplit("/", 1)[1])
                        probe_ok = True
                        self._range_verified_hosts.add(host)
                        self._host_best_proto[host] = p_ver
                        try:
                            for _ in r.iter_content():
                                break
                        except Exception:
                            pass
                        warm_pool[0] = probe_sess
                        probe_kept = True
                        break
                    if r.status_code in (301, 302, 303, 307, 308) and p_ver != CurlHttpVersion.V2TLS:
                        self._host_v2_only.add(host)
                        continue
                    # Sunucu açıkça 200/4xx döndürdüyse Range desteklemiyordur; ağ zaman aşımı ise tekrar dene
                    if r.status_code in (200, 400, 403, 416):
                        self._no_parallel_hosts.add(host)
                        return False
                except Exception as pexc:
                    last_probe_err = pexc
                    self._mark_host_slow(host)
                finally:
                    if r is not None and hasattr(r, "close"):
                        try:
                            r.close()
                        except Exception:
                            pass
                    if not probe_kept:
                        try:
                            probe_sess.close()
                        except Exception:
                            pass
            if not probe_ok:
                if total_size > 0 and last_probe_err is not None:
                    total = total_size
                else:
                    return False

        smart_n = compute_smart_workers(total - start, num_workers, mode)
        logger.debug(
            "Gofile akıllı parçalı (%s): %d/%d bağlantı (%s, kalan=%.2f MB)",
            mode, smart_n, num_workers, dest.name, (total - start) / (1024 * 1024),
        )

        # Sunucu bazlı öğrenilen protokolle başla; timeout döngüsünde V2TLS -> V1_1 -> V3 döndür
        def _make_worker_session(gen: int) -> Any:
            from curl_cffi import CurlHttpVersion
            if host in self._host_v2_only or self._proxy:
                return self._new_cffi_dl_session(proxies, http_ver=CurlHttpVersion.V2TLS)
            base_ver = self._host_best_proto.get(host, CurlHttpVersion.V2TLS)
            candidates = [base_ver] + [v for v in (CurlHttpVersion.V2TLS, CurlHttpVersion.V1_1, CurlHttpVersion.V3) if v != base_ver]
            chosen = candidates[gen % len(candidates)] if gen >= 1 else base_ver
            return self._new_cffi_dl_session(proxies, http_ver=chosen)

        ranged_work_stealing_download(
            url=url,
            dest=dest,
            total_size=total,
            num_workers=num_workers,
            make_session=_make_worker_session,
            headers=dl_headers,
            cookies=cookies,
            start=start,
            allow_redirects=False,
            progress_hook=progress_hook,
            cancel_event=cancel_event,
            pause_event=pause_event,
            mode=mode,
            session_pool=warm_pool,
            keep_sessions=True,
        )
        return True

    # ------------------------------------------------------------------
    # Tracker
    # ------------------------------------------------------------------

    @staticmethod
    def _load_tracker(path: Path) -> set[str]:
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    return set(json.load(f))
        except Exception:
            pass
        return set()

    @staticmethod
    def _save_tracker(path: Path, ids: set[str]) -> None:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(sorted(ids), f)
        except Exception as exc:
            logger.warning("Tracker kaydedilemedi: %s", exc)

    # ------------------------------------------------------------------
    # Yardımcılar
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_url(url: str) -> str:
        m = _URL_PATTERN.match(url)
        if not m:
            raise ExtractionError(f"Geçersiz Gofile URL'si: {url}")
        return m.group("id")

    @staticmethod
    def _safe_filename(name: str) -> str:
        name = re.sub(r'^[\s⭐🔥🆕✨💎]+', '', name)
        name = re.sub(r'[<>:"/\\|?*]', "_", name)
        return name.strip(". ") or "gofile_download"

    @staticmethod
    def _normalise(tree: dict[str, Any], content_id: str) -> dict[str, Any]:
        files = GofileExtractor._collect_files_static(tree)
        thumb = None
        for f in files:
            t = f.get("thumbnailLink") or f.get("thumb")
            if t:
                thumb = t
                break
        if not thumb:
            thumb = "https://www.google.com/s2/favicons?domain=gofile.io&sz=128"

        items = [
            {
                "title": f.get("name", ""),
                "url": f.get("link", ""),
                "duration": None,
                "thumbnail": f.get("thumbnailLink") or f.get("thumb") or thumb,
            }
            for f in files
        ]
        return {
            "title": tree.get("name", content_id),
            "thumbnail": thumb,
            "duration": None,
            "formats": [
                {
                    "format_id": "original",
                    "ext": "mixed",
                    "quality": "original",
                    "filesize": sum(f.get("size", 0) for f in files),
                }
            ],
            "is_playlist": len(items) > 1,
            "playlist_items": items,
            "entries": items,
        }

    @staticmethod
    def _collect_files_static(node: dict[str, Any]) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        node_type = node.get("type", "")
        if node_type == "file":
            files.append(node)
        elif node_type in ("folder", ""):
            for key in ("children", "contents"):
                children = node.get(key, {})
                if isinstance(children, dict):
                    for child in children.values():
                        files.extend(GofileExtractor._collect_files_static(child))
                elif isinstance(children, list):
                    for child in children:
                        files.extend(GofileExtractor._collect_files_static(child))
        return files
