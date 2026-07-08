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

import json
import logging
import os
import re
import socket
import time
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from src.extractors.base_extractor import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 8192
_MAX_RETRIES = 3
_RETRY_BACKOFF = 2

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

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._tokens_pool: list[str] = []
        self._token_index: int = 0
        self._proxy: Optional[str] = None  # Çalışan proxy URL'si
        self._session = requests.Session()
        self._session.headers.update(_DEFAULT_HEADERS)

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

        last_path = ""
        for idx, finfo in enumerate(files, 1):
            fid = finfo.get("id", "")
            fname = self._safe_filename(finfo.get("name", fid))
            file_dest = dest / fname

            if fid in downloaded_set and file_dest.exists() and file_dest.stat().st_size > 0:
                logger.debug("Zaten indirilmiş ve dosyası mevcut, atlıyor: %s", fname)
                last_path = str(file_dest)
                continue

            file_url = finfo.get("link", "")
            if not file_url:
                logger.warning("Dosya için indirme bağlantısı yok: %s", fname)
                continue
            file_size = finfo.get("size", 0)

            try:
                def _wrapped_hook(data: dict[str, Any]) -> None:
                    data["item_index"] = idx
                    data["item_count"] = len(files)
                    data["item_title"] = fname
                    if "speed" not in data or not data.get("speed"):
                        data["speed"] = getattr(self, "_last_speed", 0.0)
                    if progress_hook:
                        progress_hook(data)

                if progress_hook:
                    progress_hook({
                        "status": "downloading",
                        "filename": str(file_dest),
                        "item_index": idx,
                        "item_count": len(files),
                        "item_title": fname,
                        "downloaded_bytes": 0,
                        "total_bytes": file_size,
                        "speed": getattr(self, "_last_speed", 0.0),
                    })

                self._download_file(file_url, file_dest, file_size, _wrapped_hook)
                downloaded_set.add(fid)
                self._save_tracker(tracker_path, downloaded_set)
                last_path = str(file_dest)
            except Exception as exc:
                logger.error(
                    "Gofile dosyası %d/%d (%s) indirilemedi: %s",
                    idx, len(files), fname, exc,
                )

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
        """Gofile wt.obf.js içindeki generateWT (SHA-256) algoritmasını dinamik hesaplar."""
        import hashlib
        import os
        tok = token if token is not None else (self._token or "")
        ua = custom_ua or _DEFAULT_HEADERS.get("User-Agent", "")
        lang = _DEFAULT_HEADERS.get("Accept-Language", "").split(",")[0] or "en-US"
        time_slot = str(int(time.time() // 14400))
        salt = os.getenv("GOFILE_WT_SALT", "9844d94d963d30")
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

    def _fetch_content_tree(self, content_id: str) -> dict[str, Any]:
        """İçerik ağacını çek: curl_cffi → requests → (rate-limit retry) → HTML scrape."""
        last_exc: Optional[Exception] = None
        import random

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
        """curl_cffi Chrome taklit + proxy ile API çağrısı."""
        try:
            from curl_cffi.requests import Session as CffiSession
        except ImportError:
            return None

        proxies = {}
        if self._proxy:
            proxies = {"http": self._proxy, "https": self._proxy}

        with CffiSession(impersonate="chrome", proxies=proxies) as s:
            native_ua = s.headers.get("User-Agent") or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
            wt_token = self._compute_wt(self._token, custom_ua=native_ua)
            url = f"{_API_BASE}/contents/{content_id}?wt={wt_token}&cache=true"
            headers = self._api_headers(custom_ua=native_ua)
            cookies = {"wt": wt_token}
            if self._token:
                cookies["accountToken"] = self._token

            resp = s.get(url, headers=headers, cookies=cookies, timeout=15)
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
            logger.debug("cffi API cevabı: %s", status)
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
    ) -> None:
        import random
        proxies = {}
        if self._proxy:
            proxies = {"http": self._proxy, "https": self._proxy}

        for attempt in range(1, _MAX_RETRIES + 1):
            session_ctx: Any = None
            use_cffi = False
            try:
                # curl_cffi öncelikli (Cloudflare bypass + proxy)
                try:
                    from curl_cffi.requests import Session as CffiSession
                    session_ctx = CffiSession(impersonate="chrome", proxies=proxies)
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

                # CDN (storeX.gofile.io) paylaşımlı tokenlarda 429 verdiği için ilk denemede token göndermiyoruz.
                # Ancak 302/401/403 alırsa veya 2. denemeden itibaren oturum gerekirse token ekliyoruz.
                if attempt >= 2 and self._token:
                    wt_token = self._compute_wt(self._token, custom_ua=native_ua)
                    cookies = {"wt": wt_token, "accountToken": self._token}
                    dl_headers["Authorization"] = f"Bearer {self._token}"
                    dl_headers["X-Website-Token"] = wt_token

                resp = session_ctx.get(
                    url, stream=True, headers=dl_headers,
                    cookies=cookies, allow_redirects=False, timeout=(10, 60)
                )

                if resp.status_code in (301, 302, 303, 307, 308):
                    # CDN sunucusu misafir isteği reddedip klasör sayfasına yönlendiriyor (oturum gerekiyor)
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

                total = int(resp.headers.get("content-length", 0)) or total_size
                downloaded = 0
                start_ts = time.monotonic()
                first_chunk_checked = False

                with open(dest, "wb") as fp:
                    for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                        if chunk:
                            if not first_chunk_checked:
                                first_chunk_checked = True
                                # Dosya başına bakarak HTML sayfası iniyorsa hemen durdur ve dosyayı sil
                                if chunk.lstrip().startswith((b"<!doctype html", b"<!DOCTYPE html", b"<html")):
                                    fp.close()
                                    dest.unlink(missing_ok=True)
                                    if attempt == 1 and self._token:
                                        raise ValueError("CDN_NEED_AUTH")
                                    raise ExtractionError("Gofile indirme adresi HTML sayfası döndürdü.")
                            fp.write(chunk)
                            downloaded += len(chunk)
                            if progress_hook:
                                elapsed = time.monotonic() - start_ts
                                speed = downloaded / elapsed if elapsed > 0 else 0
                                self._last_speed = speed
                                eta = int((total - downloaded) / speed) if speed > 0 else 0
                                progress_hook({
                                    "status": "downloading",
                                    "downloaded_bytes": downloaded,
                                    "total_bytes": total,
                                    "speed": speed,
                                    "eta": eta,
                                    "filename": str(dest),
                                })

                if use_cffi and hasattr(session_ctx, "close"):
                    session_ctx.close()
                return

            except Exception as exc:
                if use_cffi and session_ctx and hasattr(session_ctx, "close"):
                    try:
                        session_ctx.close()
                    except Exception:
                        pass
                if dest.exists() and dest.stat().st_size < 50000 and "html" in str(dest).lower():
                    dest.unlink(missing_ok=True)
                if any(x in str(exc).lower() for x in ("premium", "bulunamadı", "şifre")):
                    raise
                if attempt == _MAX_RETRIES:
                    raise ExtractionError(
                        f"Gofile dosya indirme {_MAX_RETRIES} denemeden sonra başarısız: {exc}"
                    ) from exc
                wait = _RETRY_BACKOFF * (2 ** (attempt - 1)) + random.uniform(1.0, 3.0)
                if "CDN_NEED_AUTH" in str(exc):
                    wait = 0.5
                logger.warning("Gofile retry %d/%d (%ds): %s", attempt, _MAX_RETRIES, int(wait), exc)
                time.sleep(wait)

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
