"""
Suylios Downloader - High-Speed Work-Stealing Multi-Connection Range Downloader.

Provides:
- ``SlidingSpeedTracker`` for responsive real-time speed/ETA calculation.
- ``ranged_work_stealing_download`` for resilient, stall-proof parallel Range
  downloads (shared between Gofile and general HTTP extractors).
- ``download_file_fast`` respecting the user's segmented-download settings.
"""

from collections import deque
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

try:
    from curl_cffi import requests as cffi_requests
    from curl_cffi import CurlHttpVersion, CurlOpt
    from curl_cffi.requests import Session as CffiSession
    HAS_CFFI = True
except ImportError:
    cffi_requests = None  # type: ignore[assignment]
    CurlHttpVersion = None  # type: ignore[assignment,misc]
    CurlOpt = None  # type: ignore[assignment,misc]
    CffiSession = None  # type: ignore[assignment,misc]
    HAS_CFFI = False

import requests

from src.config import config
from src.extractors.base_extractor import ExtractionCancelled, ExtractionError

logger = logging.getLogger("suylios.fast_downloader")

_PIECE_MIN_BYTES = 256 * 1024       # 256 KB minimum piece size (IDM granularity)
_PIECE_MAX_BYTES = 2 * 1024 * 1024  # 2 MB maximum piece size
_TAIL_STEAL_MIN = 384 * 1024        # 384 KB minimum remaining to split a straggler tail
_IO_CHUNK_SIZE = 16384              # 16 KB chunk so requests stall/cancel/progress never freeze for 256 KB
_ADAPTIVE_CHECK_SECS = 3.5
_ADAPTIVE_SLOW_BPS = 450 * 1024     # < 450 KB/s triggers segmented switch
_ADAPTIVE_MIN_REMAIN = 1536 * 1024  # 1.5 MB minimum remaining to split
_ADAPTIVE_DIRECT_MIN = 10 * 1024 * 1024  # >= 10 MB starts segmented directly even in adaptive mode
_LARGE_DIRECT_MIN = 5 * 1024 * 1024      # >= 5 MB in large-file accelerator mode
_ALWAYS_DIRECT_MIN = 1024 * 1024         # >= 1 MB in continuous multi-part mode
_TURBO_MIN_BYTES = 512 * 1024            # >= 512 KB in full-power max-parallel mode
_STALL_IDLE_SECS = 10.0             # 10 s without data chunk = dead socket
_STALL_CHECK_SECS = 7.5             # avoid premature kills on shared multi-socket streams
_STALL_TAIL_CHECK_SECS = 3.5        # faster detection when idle peers wait on final tail
_STALL_DEAD_BPS = 6 * 1024          # < 6 KB/s = stalled anywhere
_STALL_STRAGGLER_BPS = 14 * 1024    # < 14 KB/s while peers run >5x faster = straggler


def _norm_mode(mode: str) -> str:
    m = str(mode or "adaptive").strip().lower()
    if m in ("aggressive", "idm"):
        return "turbo"
    if m not in ("adaptive", "large", "always", "turbo"):
        return "adaptive"
    return m


def min_split_bytes(mode: str = "adaptive") -> int:
    """Return the minimum remaining byte threshold to use segmented mode."""
    norm = _norm_mode(mode)
    if norm == "turbo":
        return _TURBO_MIN_BYTES
    if norm == "always":
        return _ALWAYS_DIRECT_MIN
    return _ADAPTIVE_MIN_REMAIN


def should_start_direct_segmented(total_size: int, mode: str = "adaptive") -> bool:
    """Return True when segmented download should start immediately without waiting for slowdown."""
    if total_size <= 0:
        return False
    norm = _norm_mode(mode)
    if norm == "turbo":
        return total_size >= _TURBO_MIN_BYTES
    if norm == "always":
        return total_size >= _ALWAYS_DIRECT_MIN
    if norm == "large":
        return total_size >= _LARGE_DIRECT_MIN
    return total_size >= _ADAPTIVE_DIRECT_MIN


def compute_smart_workers(
    remain_bytes: int,
    max_workers: int,
    mode: str = "adaptive",
) -> int:
    """Compute optimal parallel connections strictly capped at *max_workers*.

    - ``turbo`` opens up to *max_workers* (limited only by 256 KB minimum piece size).
    - ``large`` scales fast up to *max_workers* for large files (>= 5 MB).
    - ``adaptive`` / ``always`` scale intelligently with size and server capacity.
    """
    ceiling = max(1, min(32, int(max_workers or 1)))
    if ceiling < 2 or remain_bytes <= 0:
        return 1

    norm_mode = _norm_mode(mode)
    mb = 1024 * 1024

    if norm_mode == "turbo":
        if remain_bytes < _TURBO_MIN_BYTES:
            return 1
        max_useful_pieces = max(1, remain_bytes // _PIECE_MIN_BYTES)
        return max(1, min(ceiling, max_useful_pieces))

    if norm_mode == "large":
        if remain_bytes < _ADAPTIVE_MIN_REMAIN:
            return 1
        if remain_bytes < 5 * mb:
            cap = 4
        elif remain_bytes < 12 * mb:
            cap = 8
        elif remain_bytes < 25 * mb:
            cap = 12
        else:
            cap = ceiling
    else:
        min_req = _ALWAYS_DIRECT_MIN if norm_mode == "always" else _ADAPTIVE_MIN_REMAIN
        if remain_bytes < min_req:
            return 1
        if remain_bytes < 3 * mb:
            cap = 3
        elif remain_bytes < 10 * mb:
            cap = 4
        elif remain_bytes < 30 * mb:
            cap = 6
        elif remain_bytes < 80 * mb:
            cap = 10
        elif remain_bytes < 200 * mb:
            cap = 16
        else:
            cap = ceiling

    max_useful_pieces = max(1, remain_bytes // _PIECE_MIN_BYTES)
    return max(1, min(ceiling, cap, max_useful_pieces))


class SlidingSpeedTracker:
    """3-second sliding-window throughput estimator."""

    def __init__(self, window: float = 3.0) -> None:
        self._window = window
        self._samples: deque[tuple[float, int]] = deque()
        self._total_window_bytes = 0
        self._start = time.monotonic()

    def add(self, nbytes: int, now: Optional[float] = None) -> None:
        if nbytes <= 0:
            return
        ts = now if now is not None else time.monotonic()
        self._samples.append((ts, nbytes))
        self._total_window_bytes += nbytes
        self._prune(ts)

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        while self._samples and self._samples[0][0] < cutoff:
            _, old_b = self._samples.popleft()
            self._total_window_bytes -= old_b

    def rate(self, now: Optional[float] = None) -> float:
        ts = now if now is not None else time.monotonic()
        self._prune(ts)
        if not self._samples:
            return 0.0
        span = min(self._window, max(ts - self._start, 0.25))
        return self._total_window_bytes / span


def _wait_if_paused(
    pause_event: Optional[threading.Event],
    cancel_event: Optional[threading.Event],
) -> None:
    """Block cleanly while paused, waking immediately on cancel or resume."""
    if pause_event is None:
        if cancel_event is not None and cancel_event.is_set():
            raise ExtractionCancelled("İndirme iptal edildi.")
        return
    while not pause_event.is_set():
        if cancel_event is not None and cancel_event.is_set():
            raise ExtractionCancelled("İndirme iptal edildi.")
        pause_event.wait(timeout=0.25)
    if cancel_event is not None and cancel_event.is_set():
        raise ExtractionCancelled("İndirme iptal edildi.")


def _sleep_cancelable(
    seconds: float,
    cancel_event: Optional[threading.Event],
) -> bool:
    """Sleep up to *seconds*; return True immediately if cancelled."""
    if cancel_event is not None:
        return cancel_event.wait(timeout=max(0.0, seconds))
    time.sleep(max(0.0, seconds))
    return False


def ranged_work_stealing_download(
    url: str,
    dest: Path,
    total_size: int,
    num_workers: int,
    make_session: Callable[[int], Any],
    *,
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
    start: int = 0,
    allow_redirects: bool = True,
    progress_hook: Optional[Callable[[Dict[str, Any]], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    pause_event: Optional[threading.Event] = None,
    extra_payload: Optional[Dict[str, Any]] = None,
    mode: str = "adaptive",
    session_pool: Optional[Dict[int, Any]] = None,
    keep_sessions: bool = False,
) -> None:
    """Download ``[start .. total_size - 1]`` into *dest* using smart work-stealing.

    - Treats *num_workers* as a strict upper ceiling and picks an optimal smart
      connection count via ``compute_smart_workers``.
    - Progressively ramps up connections (unless *mode* is ``"aggressive"``) so
      rate-limited CDNs are not slammed by unnecessary parallel handshakes.
    - Dynamically splits straggler tails (hedged tail-stealing) when the queue
      empties so fast workers help finish the final pieces.
    - Detects dead sockets and extreme single-worker stragglers after first byte
      while avoiding reconnect storms when the entire link is uniformly slow.
    """
    remain = total_size - start
    if remain <= 0:
        return

    norm_mode = _norm_mode(mode)
    ceiling = max(2, min(32, int(num_workers)))
    smart_workers = compute_smart_workers(remain, ceiling, norm_mode)
    if remain < (_PIECE_MIN_BYTES * 2):
        # Small direct calls still work cleanly without over-splitting.
        piece_size = max(64 * 1024, remain // max(1, min(ceiling, 2)))
    else:
        # 2-4 bağlantılı veya büyük dosyalarda küçük 2 MB parçalar aşırı Range GET
        # round-trip'ine ve CDN throttling'ine yol açar; dinamik tail-stealing zaten
        # son parçayı 384 KB'a kadar bölebildiği için 4-6 MB parçalar güvenle kullanılır.
        if norm_mode == "turbo" and smart_workers >= 8:
            max_p = 1024 * 1024
        elif smart_workers <= 2 and remain >= 32 * 1024 * 1024:
            max_p = 6 * 1024 * 1024
        elif smart_workers <= 4 and remain >= 24 * 1024 * 1024:
            max_p = 4 * 1024 * 1024
        elif remain >= 32 * 1024 * 1024:
            max_p = 3 * 1024 * 1024
        else:
            max_p = _PIECE_MAX_BYTES
        div = smart_workers * 2 if norm_mode == "turbo" else smart_workers * 3
        piece_size = max(
            _PIECE_MIN_BYTES,
            min(max_p, remain // max(1, div)),
        )

    work_q: queue.Queue[Optional[tuple[int, int, int]]] = queue.Queue()
    piece_count = 0
    cur = start
    while cur < total_size:
        end = min(total_size - 1, cur + piece_size - 1)
        work_q.put((cur, end, 0))
        piece_count += 1
        cur = end + 1

    actual_workers = max(1, min(smart_workers if remain >= _PIECE_MIN_BYTES * 2 else ceiling, piece_count))
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "r+b" if start > 0 and dest.exists() else "wb") as fp:
        fp.truncate(total_size)

    lock = threading.Lock()
    stop = threading.Event()
    speed_tracker = SlidingSpeedTracker(window=3.0)
    active_pieces: Dict[int, Dict[str, Any]] = {}
    state: Dict[str, Any] = {
        "done": start,
        "remaining_pieces": piece_count,
        "last_emit": 0.0,
        "err": None,
        "next_piece_id": 1,
        "rate_limited": False,
        "active_workers": 0,
        "retry_count": 0,
        "no_progress_failures": 0,
        "completed_intervals": [],
        "tail_stolen_until": 0.0,
        "retry_status_until": 0.0,
        "phase_detail": "",
        "first_chunk_emitted": False,
    }
    base_payload = dict(extra_payload or {})

    def _emit(force: bool = False, phase_detail: str = "") -> None:
        if not progress_hook:
            return
        now = time.monotonic()
        with lock:
            if not force and (now - state["last_emit"] < 0.15):
                return
            state["last_emit"] = now
            done_snap = state["done"]
            sp = speed_tracker.rate(now)
            live_pieces = len(active_pieces)
            active_cnt = max(1, live_pieces if (state["first_chunk_emitted"] and live_pieces > 0) else state["active_workers"])
            explicit_phase = phase_detail or state["phase_detail"]
            if explicit_phase:
                detail = explicit_phase
            elif now < state["tail_stolen_until"]:
                detail = f"Kuyruk sonu hızlandırılıyor ({active_cnt} parça)"
            elif now < state["retry_status_until"] and (active_cnt == 1 or state["no_progress_failures"] >= 2):
                detail = f"Bağlantı yenileniyor ({active_cnt} parça)"
            else:
                detail = f"{active_cnt} parça paralel iniyor"
        eta = int((total_size - done_snap) / sp) if sp > 0 else 0
        dest_str = str(dest)
        for _tmp_ext in (".downloading", ".part"):
            if dest_str.endswith(_tmp_ext):
                dest_str = dest_str[: -len(_tmp_ext)]
                break
        payload = {
            "filename": dest_str,
            **base_payload,
            "status": "downloading",
            "status_detail": detail,
            "downloaded_bytes": done_snap,
            "total_bytes": total_size,
            "speed": sp,
            "eta": eta,
        }
        progress_hook(payload)

    def _close_quiet(obj: Any) -> None:
        if obj is not None and hasattr(obj, "close"):
            try:
                obj.close()
            except Exception:
                pass

    def _record_interval_locked(span_start: int, span_end_inclusive: int) -> None:
        if span_end_inclusive >= span_start:
            state["completed_intervals"].append((span_start, span_end_inclusive))

    @staticmethod
    def _compute_contiguous_end(base_start: int, intervals: list[tuple[int, int]]) -> int:
        if not intervals:
            return base_start
        cur = base_start
        for a, b in sorted(intervals):
            if a <= cur <= b:
                cur = b + 1
            elif a > cur:
                break
        return cur

    def _finish_piece() -> None:
        with lock:
            state["remaining_pieces"] -= 1
            left = state["remaining_pieces"]
        if left <= 0:
            stop.set()
            for _ in range(actual_workers + 4):
                work_q.put(None)

    def _try_steal_tail() -> Optional[tuple[int, int, int]]:
        """Split the largest running tail in half if a worker is idle."""
        now = time.monotonic()
        with lock:
            if stop.is_set() or state["remaining_pieces"] <= 0:
                return None
            best_entry = None
            best_avail = 0
            for entry in active_pieces.values():
                if (now - entry.get("last_split", 0.0)) < 0.9:
                    continue
                rem_tail = entry["end"] - entry["pos"] + 1
                entry_bps = float(entry.get("bps") or 0.0)
                min_steal = 256 * 1024 if (entry_bps > 0 and entry_bps < 120 * 1024) else _TAIL_STEAL_MIN
                if rem_tail < min_steal:
                    continue
                # Halihazırda ~1.2 sn içinde bitecek hızlı bir parçayı tekrar bölüp
                # yeni Range GET RTT gecikmesi eklememek için tahmin kontrolü yap.
                est_secs = rem_tail / max(entry_bps, 220 * 1024)
                if est_secs <= 1.2 and rem_tail < 1024 * 1024:
                    continue
                if rem_tail > best_avail:
                    best_avail = rem_tail
                    best_entry = entry
            if best_entry is None:
                return None
            safe_cursor = best_entry["pos"] + 128 * 1024
            splittable = best_entry["end"] - safe_cursor + 1
            if splittable < 192 * 1024:
                return None
            mid = safe_cursor + splittable // 2
            orig_end = best_entry["end"]
            if mid > orig_end or mid <= safe_cursor:
                return None
            best_entry["end"] = mid - 1
            best_entry["last_split"] = now
            state["remaining_pieces"] += 1
            state["tail_stolen_until"] = now + 2.0
            return (mid, orig_end, 0)

    def _worker(worker_idx: int) -> None:
        with lock:
            sess = session_pool.pop(worker_idx, None) if session_pool is not None else None
        sess_gen = worker_idx
        first_connect = True
        try:
            while not stop.is_set():
                if cancel_event is not None and cancel_event.is_set():
                    raise ExtractionCancelled("İndirme iptal edildi.")
                if pause_event is not None and not pause_event.is_set():
                    _close_quiet(sess)
                    sess = None
                    _wait_if_paused(pause_event, cancel_event)

                if first_connect:
                    first_connect = False
                    if sess is None and worker_idx > 0:
                        if _sleep_cancelable((worker_idx % 4) * 0.07, cancel_event):
                            raise ExtractionCancelled("İndirme iptal edildi.")

                item = None
                try:
                    item = work_q.get_nowait()
                except queue.Empty:
                    item = _try_steal_tail()
                    if item is None:
                        try:
                            item = work_q.get(timeout=0.35)
                        except queue.Empty:
                            continue
                if item is None:
                    break

                a, b, retries = item
                pos = a
                cur_end = b
                piece_id = 0
                piece_entry: Dict[str, Any] = {"pos": pos, "end": b, "last_split": 0.0, "bps": 0.0}
                with lock:
                    piece_id = state["next_piece_id"]
                    state["next_piece_id"] = piece_id + 1
                    active_pieces[piece_id] = piece_entry

                resp = None
                stream_ok_bytes = 0
                try:
                    if sess is None:
                        sess = make_session(sess_gen)

                    with lock:
                        cur_end = piece_entry["end"]
                    if pos > cur_end:
                        with lock:
                            active_pieces.pop(piece_id, None)
                        _finish_piece()
                        continue

                    req_h = dict(headers or {})
                    req_h["Range"] = f"bytes={pos}-{cur_end}"
                    req_kw: Dict[str, Any] = {
                        "stream": True,
                        "headers": req_h,
                        "allow_redirects": allow_redirects,
                        "timeout": (6, 12),
                    }
                    if cookies:
                        req_kw["cookies"] = cookies

                    resp = sess.get(url, **req_kw)
                    if resp.status_code in (401, 403):
                        raise ExtractionError(f"HTTP {resp.status_code}: Sunucu erişimi reddetti.")
                    if resp.status_code in (429, 503):
                        with lock:
                            state["rate_limited"] = True
                        raise TimeoutError(f"HTTP {resp.status_code}: Geçici sunucu yoğunluğu")
                    if resp.status_code != 206:
                        raise ExtractionError(f"Range desteklenmiyor/hata (HTTP {resp.status_code})")

                    got_first_byte = False
                    last_chunk_ts = time.monotonic()
                    stall_ts = last_chunk_ts
                    stall_bytes = 0
                    stream_ok_bytes = 0
                    paused_mid = False

                    is_cffi_resp = "curl_cffi" in type(resp).__module__
                    chunk_iter = resp.iter_content() if is_cffi_resp else resp.iter_content(chunk_size=_IO_CHUNK_SIZE)
                    with open(dest, "r+b") as fp:
                        fp.seek(pos)
                        for chunk in chunk_iter:
                            if stop.is_set() or (cancel_event is not None and cancel_event.is_set()):
                                raise ExtractionCancelled("İndirme iptal edildi.")
                            if pause_event is not None and not pause_event.is_set():
                                paused_mid = True
                                break
                            now = time.monotonic()
                            if not chunk:
                                if (now - last_chunk_ts) >= _STALL_IDLE_SECS:
                                    raise TimeoutError("Parça veri akışı durdu (idle timeout)")
                                continue

                            with lock:
                                allowed_end = piece_entry["end"]
                                if pos > allowed_end:
                                    break
                                need = allowed_end - pos + 1
                                write_data = chunk[:need] if len(chunk) > need else chunk
                                clen = len(write_data)
                                piece_entry["pos"] = pos + clen

                            fp.write(write_data)
                            pos += clen
                            last_chunk_ts = now
                            if not got_first_byte:
                                got_first_byte = True
                                stall_ts = now
                                stall_bytes = 0
                            stall_bytes += clen
                            stream_ok_bytes += clen

                            with lock:
                                state["done"] += clen
                                if stream_ok_bytes >= 65536:
                                    state["no_progress_failures"] = 0
                                    state["retry_count"] = 0
                                speed_tracker.add(clen, now)
                                total_bps = speed_tracker.rate(now)
                                allowed_end = piece_entry["end"]
                                active_cnt = max(1, len(active_pieces))
                                first_chunk = not state["first_chunk_emitted"]
                                if first_chunk:
                                    state["first_chunk_emitted"] = True

                            _emit(force=first_chunk)

                            if pos > allowed_end:
                                break

                            sdt = now - stall_ts
                            rem_bytes = allowed_end - pos + 1
                            with lock:
                                tail_phase = (
                                    state["remaining_pieces"] <= max(2, actual_workers // 2)
                                    and active_cnt <= 2
                                    and actual_workers >= 3
                                )
                            eval_window = _STALL_TAIL_CHECK_SECS if tail_phase else _STALL_CHECK_SECS
                            if got_first_byte and sdt >= eval_window:
                                worker_bps = stall_bytes / sdt
                                with lock:
                                    piece_entry["bps"] = worker_bps
                                if tail_phase and rem_bytes > 96 * 1024:
                                    # Kuyruk sonunda boşta bekleyen işçi varken son parça aşırı yavaşsa (<32 KB/s) devret
                                    if worker_bps < 32 * 1024:
                                        raise TimeoutError(
                                            f"Kuyruk sonu yavaşladı ({worker_bps / 1024:.1f} KB/s)"
                                        )
                                elif not tail_phase and rem_bytes > 512 * 1024:
                                    is_dead = worker_bps < _STALL_DEAD_BPS
                                    peer_avg = (total_bps - worker_bps) / max(1, active_cnt - 1) if active_cnt >= 2 else 0.0
                                    is_straggler = (
                                        worker_bps < _STALL_STRAGGLER_BPS
                                        and total_bps > max(128 * 1024, worker_bps * 5.0)
                                    ) or (
                                        2 <= active_cnt <= 6
                                        and rem_bytes > 1024 * 1024
                                        and total_bps >= 512 * 1024
                                        and worker_bps < 48 * 1024
                                        and peer_avg > worker_bps * 8.0
                                    )
                                    if is_dead or is_straggler:
                                        raise TimeoutError(
                                            f"Parça yavaşladı ({worker_bps / 1024:.1f} KB/s)"
                                        )
                                stall_ts = now
                                stall_bytes = 0

                    _close_quiet(resp)
                    resp = None

                    with lock:
                        entry = active_pieces.pop(piece_id, None)
                        cur_end = entry["end"] if entry is not None else cur_end
                        if pos > a:
                            _record_interval_locked(a, pos - 1)

                    if paused_mid:
                        if pos <= cur_end:
                            work_q.put((pos, cur_end, retries))
                        else:
                            _finish_piece()
                        _close_quiet(sess)
                        sess = None
                        _wait_if_paused(pause_event, cancel_event)
                        continue

                    if pos <= cur_end:
                        raise IOError(f"Eksik parça: {pos - a}/{cur_end - a + 1}")

                    _finish_piece()

                except BaseException as exc:
                    _close_quiet(resp)
                    resp = None
                    with lock:
                        entry = active_pieces.pop(piece_id, None)
                        if entry is not None:
                            cur_end = entry["end"]
                        if pos > a:
                            _record_interval_locked(a, pos - 1)

                    if (
                        isinstance(exc, ExtractionCancelled)
                        or type(exc).__name__ == "_CancelledError"
                        or (cancel_event is not None and cancel_event.is_set())
                    ):
                        with lock:
                            if state["err"] is None:
                                state["err"] = ExtractionCancelled("İndirme iptal edildi.")
                        stop.set()
                        break

                    # Rotate/reset worker session (lets caller switch HTTP version / IP route)
                    _close_quiet(sess)
                    sess = None
                    sess_gen += 1

                    # Fatal auth/403 should not be retried indefinitely
                    is_fatal = isinstance(exc, ExtractionError) and any(
                        code in str(exc) for code in ("401", "403")
                    )
                    max_piece_retries = 10 if isinstance(exc, TimeoutError) else 4
                    with lock:
                        live_peers = len(active_pieces)
                        state["retry_count"] += 1
                        if stream_ok_bytes < 65536:
                            state["no_progress_failures"] += 1
                        no_prog = state["no_progress_failures"]
                        if live_peers == 0 or no_prog >= 2:
                            state["retry_status_until"] = time.monotonic() + 2.0

                    can_retry = (
                        not is_fatal
                        and pos <= cur_end
                        and (retries < max_piece_retries or (isinstance(exc, TimeoutError) and no_prog < 14 and retries < 14))
                    )
                    if can_retry:
                        # Re-queue unfinished remainder so another healthy worker can pick it up
                        work_q.put((pos, cur_end, retries + 1))
                        pause_s = 0.15 if (isinstance(exc, TimeoutError) and live_peers >= 1) else min(1.5, 0.35 * (retries + 1))
                        _sleep_cancelable(pause_s, cancel_event)
                    elif pos > cur_end:
                        _finish_piece()
                    else:
                        with lock:
                            if state["err"] is None:
                                state["err"] = exc
                        stop.set()
                        break
        except ExtractionCancelled as cexc:
            with lock:
                if state["err"] is None:
                    state["err"] = cexc
            stop.set()
        finally:
            if keep_sessions and session_pool is not None and sess is not None:
                with lock:
                    can_save = state["err"] is None and not (cancel_event is not None and cancel_event.is_set())
                    if can_save:
                        old_s = session_pool.get(worker_idx)
                        session_pool[worker_idx] = sess
                        sess = old_s if old_s is not sess else None
            _close_quiet(sess)

    initial_workers = (
        actual_workers
        if norm_mode in ("turbo", "large") or actual_workers <= 4
        else 4
    )
    with lock:
        state["active_workers"] = initial_workers
    _emit(force=True, phase_detail=f"{initial_workers} parça bağlanıyor...")
    threads: list[threading.Thread] = []
    for wi in range(initial_workers):
        t = threading.Thread(target=_worker, args=(wi,), daemon=True)
        threads.append(t)
        t.start()

    last_ramp_ts = time.monotonic()
    while any(t.is_alive() for t in threads):
        if cancel_event is not None and cancel_event.is_set():
            stop.set()
            for _ in range(actual_workers + 4):
                work_q.put(None)
            for t in threads:
                t.join(timeout=0.3)
            raise ExtractionCancelled("İndirme iptal edildi.")

        # Progressive ramp-up: expand up to actual_workers unless server is returning 429/503
        if not stop.is_set() and len(threads) < actual_workers:
            now = time.monotonic()
            if (
                (now - last_ramp_ts) >= 0.5
                and work_q.qsize() >= 1
                and (pause_event is None or pause_event.is_set())
            ):
                with lock:
                    progressed = state["done"] > start
                    rate_limited = state["rate_limited"]
                if not rate_limited and (progressed or (now - last_ramp_ts) >= 2.0):
                    step = min(2, actual_workers - len(threads))
                    for _ in range(step):
                        wi = len(threads)
                        t = threading.Thread(target=_worker, args=(wi,), daemon=True)
                        threads.append(t)
                        t.start()
                    with lock:
                        state["active_workers"] = len(threads)
                    last_ramp_ts = now

        for t in threads:
            t.join(timeout=0.2)

    if state["err"] is not None:
        err = state["err"]
        contig = _compute_contiguous_end(start, state["completed_intervals"])
        if isinstance(err, ExtractionCancelled):
            raise err
        if type(err).__name__ == "_CancelledError":
            raise ExtractionCancelled("İndirme iptal edildi.")
        out_err: BaseException = err if isinstance(err, ExtractionError) else ExtractionError(f"Parçalı indirme hatası: {err}")
        try:
            setattr(out_err, "contiguous_bytes", contig)
        except Exception:
            pass
        raise out_err

    if state["done"] < total_size:
        contig = _compute_contiguous_end(start, state["completed_intervals"])
        out_err = ExtractionError("Parçalı indirme eksik kaldı.")
        try:
            setattr(out_err, "contiguous_bytes", contig)
        except Exception:
            pass
        raise out_err

    _emit(force=True, phase_detail="Parçalar birleştiriliyor...")


def download_file_fast(
    url: str,
    target_path: Path,
    headers: Optional[Dict[str, str]] = None,
    progress_hook: Optional[Callable[[Dict[str, Any]], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    pause_event: Optional[threading.Event] = None,
    display_name: str = "",
    item_idx: int = 0,
    item_cnt: int = 0,
    num_workers: Optional[int] = None,
    max_connections: Optional[int] = None,
    known_size: int = 0,
    resumable: Optional[bool] = None,
    session_pool: Optional[Dict[int, Any]] = None,
    keep_sessions: bool = False,
) -> Path:
    """Download a file respecting segmented download settings, with work-stealing Range support."""
    if pause_event is None and progress_hook is not None:
        task_obj = getattr(progress_hook, "_task", None)
        if task_obj is not None:
            pause_event = getattr(task_obj, "_pause_event", None)

    _wait_if_paused(pause_event, cancel_event)

    seg_enabled, seg_conns, seg_mode = config.get_segmented_settings()
    workers = seg_conns if num_workers is None else max(1, int(num_workers))
    if max_connections is not None:
        workers = max(1, min(workers, int(max_connections)))

    req_headers = dict(headers or {})
    if "User-Agent" not in req_headers:
        req_headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )

    def _make_session(_gen: int = 0) -> Any:
        if HAS_CFFI and CffiSession is not None:
            kw: Dict[str, Any] = {"impersonate": "chrome", "verify": False}
            if CurlHttpVersion is not None:
                kw["http_version"] = CurlHttpVersion.V2TLS
            if CurlOpt is not None:
                kw["curl_options"] = {CurlOpt.IPRESOLVE: 1}
            return CffiSession(**kw)
        s = requests.Session()
        s.verify = False
        return s

    extra_payload: Dict[str, Any] = {}
    if item_cnt > 1:
        extra_payload["item_index"] = item_idx
        extra_payload["item_count"] = item_cnt
        extra_payload["item_title"] = display_name

    # Rust Hibrit TransferEngine (`suylios-engine download` — pwrite zero-merge + .suylios-part)
    if session_pool is None:
        try:
            from src.engine.rust_bridge import is_rust_engine_available, rust_download_file
            if is_rust_engine_available():
                rust_res = rust_download_file(
                    url=url,
                    target_path=target_path,
                    headers=req_headers,
                    proxy=str(config.get("proxy", "") or ""),
                    segmented_download=seg_enabled,
                    segmented_connections=workers,
                    segmented_mode=seg_mode,
                    speed_limit_bps=int(config.get("speed_limit", 0) or 0),
                    known_size=int(known_size or 0),
                    resumable=resumable,
                    max_host_conns=int(max_connections or 0),
                    display_name=display_name or target_path.name,
                    item_idx=item_idx,
                    item_cnt=item_cnt,
                    progress_hook=progress_hook,
                    cancel_event=cancel_event,
                    pause_event=pause_event,
                )
                if rust_res is not None and rust_res.is_file():
                    return rust_res
        except ExtractionCancelled:
            raise
        except Exception as rust_exc:
            logger.debug("Rust TransferEngine fallback to Python engine (%s): %s", url, rust_exc)

    owns_pool = session_pool is None
    warm_pool: Dict[int, Any] = session_pool if session_pool is not None else {}

    def _cleanup_warm_pool() -> None:
        if owns_pool:
            for ws in warm_pool.values():
                try:
                    ws.close()
                except Exception:
                    pass
            warm_pool.clear()

    # Probe size & Range support only when unknown and segmented downloads are enabled
    total_size = max(0, int(known_size or 0))
    accept_ranges = bool(resumable) if resumable is not None else False
    if seg_enabled and workers > 1 and (total_size <= 0 or resumable is None):
        if progress_hook:
            progress_hook({
                **extra_payload,
                "status": "downloading",
                "status_detail": "Sunucu yoklanıyor...",
                "downloaded_bytes": 0,
                "total_bytes": total_size,
                "filename": display_name or target_path.name,
            })
        probe_sess = warm_pool.pop(0, None) or _make_session(0)
        probe_ok = False
        try:
            r_test = probe_sess.get(
                url,
                headers={**req_headers, "Range": "bytes=0-0"},
                stream=True,
                allow_redirects=True,
                timeout=(8, 12),
            )
            try:
                if r_test.status_code == 206:
                    accept_ranges = True
                    probe_ok = True
                    cr = r_test.headers.get("Content-Range", "")
                    if "/" in cr and cr.split("/")[-1].isdigit():
                        total_size = int(cr.split("/")[-1])
                elif r_test.status_code == 200:
                    probe_ok = True
                    total_size = int(r_test.headers.get("Content-Length", 0)) or total_size
            finally:
                if hasattr(r_test, "close"):
                    try:
                        r_test.close()
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug("fast_downloader Range probe failed: %s", exc)
        if probe_ok:
            # Sıcak TLS oturumunu Worker 0'a devret (sıfırdan el sıkışmayı önler)
            warm_pool[0] = probe_sess
        elif hasattr(probe_sess, "close"):
            try:
                probe_sess.close()
            except Exception:
                pass

    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(target_path.suffix + ".downloading")

    # Decide whether to go straight to segmented download
    split_threshold = min_split_bytes(seg_mode)
    if (
        seg_enabled
        and accept_ranges
        and workers > 1
        and should_start_direct_segmented(total_size, seg_mode)
    ):
        smart_workers = compute_smart_workers(total_size, workers, seg_mode)
        logger.info(
            "Parçalı indirme başlatılıyor (%d/%d akıllı bağlantı, %s): %s",
            smart_workers, workers, seg_mode, display_name or target_path.name,
        )
        try:
            ranged_work_stealing_download(
                url=url,
                dest=tmp_path,
                total_size=total_size,
                num_workers=workers,
                make_session=_make_session,
                headers=req_headers,
                start=0,
                allow_redirects=True,
                progress_hook=progress_hook,
                cancel_event=cancel_event,
                pause_event=pause_event,
                extra_payload=extra_payload,
                mode=seg_mode,
                session_pool=warm_pool,
                keep_sessions=keep_sessions and not owns_pool,
            )
            tmp_path.replace(target_path)
            return target_path
        except Exception:
            if cancel_event is not None and cancel_event.is_set():
                tmp_path.unlink(missing_ok=True)
                raise
            raise
        finally:
            _cleanup_warm_pool()

    # Single-stream path (reuses warmed probe session for 0ms start)
    sess = warm_pool.pop(0, None) or _make_session(0)
    speed_tracker = SlidingSpeedTracker(window=3.0)
    start_ts = time.monotonic()
    last_emit = 0.0
    downloaded = 0
    switch_at = -1
    resume_supported = accept_ranges

    try:
        while True:
            _wait_if_paused(pause_event, cancel_event)
            req_h = dict(req_headers)
            mode = "wb"
            if downloaded > 0 and resume_supported:
                req_h["Range"] = f"bytes={downloaded}-"
                mode = "r+b"
            else:
                downloaded = 0

            resp = sess.get(url, headers=req_h, stream=True, allow_redirects=True, timeout=25)
            try:
                if downloaded > 0 and resp.status_code == 200:
                    # Server ignored Range on resume — restart cleanly from 0
                    downloaded = 0
                    mode = "wb"
                else:
                    resp.raise_for_status()

                if total_size == 0:
                    total_size = int(resp.headers.get("Content-Length", 0))
                if not resume_supported and resp.headers.get("Accept-Ranges", "").lower() == "bytes":
                    resume_supported = True

                paused_mid = False
                with open(tmp_path, mode) as fp:
                    if mode == "r+b":
                        fp.seek(downloaded)
                    for chunk in resp.iter_content(chunk_size=_IO_CHUNK_SIZE):
                        if cancel_event is not None and cancel_event.is_set():
                            raise ExtractionCancelled("İndirme iptal edildi.")
                        if pause_event is not None and not pause_event.is_set():
                            paused_mid = True
                            break
                        if not chunk:
                            continue

                        fp.write(chunk)
                        downloaded += len(chunk)
                        now = time.monotonic()
                        speed_tracker.add(len(chunk), now)

                        el = now - start_ts
                        if (
                            seg_enabled
                            and resume_supported
                            and workers > 1
                            and el >= _ADAPTIVE_CHECK_SECS
                            and total_size > 0
                            and (total_size - downloaded) >= split_threshold
                            and (downloaded / el) < _ADAPTIVE_SLOW_BPS
                        ):
                            switch_at = downloaded
                            break

                        if progress_hook and (now - last_emit >= 0.15):
                            last_emit = now
                            sp = speed_tracker.rate(now)
                            payload = {
                                **extra_payload,
                                "status": "downloading",
                                "status_detail": "Tek bağlantı iniyor",
                                "downloaded_bytes": downloaded,
                                "total_bytes": total_size,
                                "speed": sp,
                                "eta": int((total_size - downloaded) / sp) if sp > 0 and total_size > 0 else 0,
                                "filename": display_name or target_path.name,
                            }
                            progress_hook(payload)
            finally:
                if hasattr(resp, "close"):
                    try:
                        resp.close()
                    except Exception:
                        pass

            if switch_at >= 0:
                smart_workers = compute_smart_workers(total_size - switch_at, workers, seg_mode)
                logger.info(
                    "Bağlantı yavaş (%s), kalan kısım %d/%d akıllı parçalı moda geçiriliyor.",
                    display_name or target_path.name, smart_workers, workers,
                )
                if sess is not None:
                    warm_pool[0] = sess
                    sess = None
                ranged_work_stealing_download(
                    url=url,
                    dest=tmp_path,
                    total_size=total_size,
                    num_workers=workers,
                    make_session=_make_session,
                    headers=req_headers,
                    start=switch_at,
                    allow_redirects=True,
                    progress_hook=progress_hook,
                    cancel_event=cancel_event,
                    pause_event=pause_event,
                    extra_payload=extra_payload,
                    mode=seg_mode,
                    session_pool=warm_pool,
                    keep_sessions=keep_sessions and not owns_pool,
                )
                break

            if paused_mid:
                _wait_if_paused(pause_event, cancel_event)
                continue

            break
    except Exception:
        if cancel_event is not None and cancel_event.is_set():
            tmp_path.unlink(missing_ok=True)
            raise ExtractionCancelled("İndirme iptal edildi.")
        raise
    finally:
        if sess is not None:
            if keep_sessions and not owns_pool:
                warm_pool[0] = sess
            elif hasattr(sess, "close"):
                try:
                    sess.close()
                except Exception:
                    pass
        _cleanup_warm_pool()

    if cancel_event is not None and cancel_event.is_set():
        tmp_path.unlink(missing_ok=True)
        raise ExtractionCancelled("İndirme iptal edildi.")

    if tmp_path.exists():
        tmp_path.replace(target_path)

    if progress_hook:
        final_total = total_size or downloaded
        progress_hook({
            **extra_payload,
            "status": "downloading",
            "downloaded_bytes": final_total,
            "total_bytes": final_total,
            "speed": speed_tracker.rate(),
            "eta": 0,
            "filename": display_name or target_path.name,
        })

    return target_path
