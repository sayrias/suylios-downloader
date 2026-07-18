"""
Suylios Downloader - Video Host Extractor (Streamtape, DoodStream, VoE, StreamWish, etc.).

Specialized high-speed direct MP4 extractor and CDN downloader for video/file hosting
platforms where generic extractors or yt-dlp fail due to JS token obfuscation or removed support.
"""

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

try:
    from curl_cffi import requests
except ImportError:
    import requests

from src.extractors.base_extractor import BaseExtractor, ExtractionError, ExtractionCancelled
from src.extractors.fast_downloader import download_file_fast

logger = logging.getLogger("suylios.extractor.videohost")

# Supported domains for this specialized extractor
VIDEO_HOST_DOMAINS = {
    "streamtape.com", "streamtape.to", "streamtape.xyz", "streamta.pe", "shavem.pw",
    "doodstream.com", "dood.to", "dood.so", "dood.la", "dood.ws", "d0000d.com", "d000d.com", "ds2play.com", "doods.pro",
    "streamwish.com", "streamwish.to", "filemoon.sx", "filemoon.to", "streamsb.com", "sbembed.com", "sbspeed.com",
    "voe.sx", "voe-unblock.com", "launchtvy.com",
}


class VideoHostExtractor(BaseExtractor):
    """Universal high-speed extractor for Streamtape and popular video hosting mirrors."""

    def __init__(self) -> None:
        self._session = requests.Session(impersonate="chrome124") if hasattr(requests, "Session") else requests.Session()
        self._cached_info: Optional[Dict[str, Any]] = None

    @staticmethod
    def can_handle(url: str) -> bool:
        """Check if URL domain matches Streamtape or other video hosts."""
        if not url:
            return False
        url_lower = url.lower()
        return any(domain in url_lower for domain in VIDEO_HOST_DOMAINS)

    def _get_headers(self, referer: str = "") -> Dict[str, str]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,tr;q=0.8",
        }
        if referer:
            headers["Referer"] = referer
        return headers

    def extract_info(self, url: str) -> Dict[str, Any]:
        """Fetch metadata and resolve direct MP4 video link."""
        if self._cached_info and self._cached_info.get("webpage_url") == url:
            return self._cached_info

        url_lower = url.lower()
        logger.debug("VideoHostExtractor analyzing: %s", url)

        # 1. Streamtape extraction
        if any(dom in url_lower for dom in ("streamtape", "streamta.pe", "shavem.pw")):
            info = self._extract_streamtape(url)
        # 2. Doodstream extraction
        elif any(dom in url_lower for dom in ("dood", "d000")):
            info = self._extract_doodstream(url)
        # 3. Generic JS / regex fallback for VoE and others
        else:
            info = self._extract_generic_video(url)

        self._cached_info = info
        return info

    def _extract_streamtape(self, url: str) -> Dict[str, Any]:
        headers = self._get_headers()
        try:
            r = self._session.get(url, headers=headers, timeout=15)
        except Exception as exc:
            raise ExtractionError(f"Streamtape sayfasına bağlanılamadı: {exc}") from exc

        if r.status_code != 200:
            raise ExtractionError(f"Streamtape HTTP {r.status_code} hatası verdi.")

        html = r.text
        # Extract Title
        title_match = re.search(r'<meta\s+name="og:title"\s+content="([^"]+)"', html, re.I)
        if not title_match:
            title_match = re.search(r'<title>([^<]+)</title>', html, re.I)
        title = title_match.group(1).strip() if title_match else "Streamtape_Video.mp4"
        title = re.sub(r'\s+at\s+Streamtape\.com.*$', '', title, flags=re.I).strip()
        if not title.lower().endswith((".mp4", ".mkv", ".avi")):
            title += ".mp4"

        # Extract thumbnail
        thumb_match = re.search(r'<meta\s+name="og:image"\s+content="([^"]+)"', html, re.I)
        thumbnail = thumb_match.group(1).strip() if thumb_match else ""

        # Extract robotlink / botlink / get_video
        matches = re.findall(
            r"document\.getElementById\([\"\'](botlink|robotlink|ideoolink|norobotlink)[\"\']\)\.innerHTML\s*=\s*[\"\']([^\"\']+)[\"\']\s*(?:\+\s*[\"\'][^\"\']*[\"\']\s*)*\+\s*\([\"\']([^\"\']+)[\"\']\)((\.substring\(\d+\))+)",
            html
        )

        video_url = ""
        # Prioritize robotlink and botlink which hold the true get_video redirect
        for elem_id, prefix, token_str, sub_chain, _ in matches:
            if elem_id in ("robotlink", "botlink"):
                cut = sum(int(n) for n in re.findall(r"\.substring\((\d+)\)", sub_chain))
                link = prefix + token_str[cut:]
                if link.startswith("//"):
                    link = "https:" + link
                elif link.startswith("/"):
                    link = "https:/" + link
                if "&stream=1" not in link:
                    link += "&stream=1"
                if "get_video?" in link:
                    video_url = link
                    break

        if not video_url:
            # Fallback regex check inside HTML or scripts
            fallback_m = re.search(r'[\'"](//[^\'"]+/get_video\?[^\'"]+)[\'"]', html)
            if fallback_m:
                video_url = "https:" + fallback_m.group(1)
                if "&stream=1" not in video_url:
                    video_url += "&stream=1"

        if not video_url:
            raise ExtractionError("Streamtape video URL adresi (robotlink) çözülemedi. Video silinmiş veya korumalı olabilir.")

        # Resolve exact redirect target using the session cookies
        direct_url = video_url
        try:
            r_redirect = self._session.get(video_url, headers=self._get_headers(referer=url), allow_redirects=False, timeout=10)
            if "location" in r_redirect.headers:
                direct_url = r_redirect.headers["location"]
        except Exception as exc:
            logger.warning("Streamtape redirect çözme uyarısı (%s), get_video linkiyle devam ediliyor...", exc)

        logger.info("Streamtape resolved direct MP4: %s -> %s", title, direct_url)

        req_headers = self._get_headers(referer=url)
        if hasattr(self._session, "cookies") and self._session.cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in self._session.cookies.items())
            if cookie_str:
                req_headers["Cookie"] = cookie_str

        return {
            "id": re.sub(r'[^a-zA-Z0-9_-]', '', url.rstrip('/').split('/')[-1]) or "streamtape_file",
            "title": title,
            "extractor": "VideoHostExtractor",
            "webpage_url": url,
            "direct_url": direct_url,
            "thumbnail": thumbnail,
            "duration": None,
            "headers": req_headers,
            "formats": [
                {
                    "format_id": "best",
                    "ext": "mp4",
                    "quality": "HD",
                    "url": direct_url,
                }
            ],
            "is_playlist": False,
            "playlist_items": [],
        }

    def _extract_doodstream(self, url: str) -> Dict[str, Any]:
        headers = self._get_headers()
        r = self._session.get(url, headers=headers, timeout=15)
        html = r.text

        title_m = re.search(r'<title>([^<]+)</title>', html, re.I)
        title = title_m.group(1).strip() if title_m else "DoodStream_Video.mp4"
        title = re.sub(r'\s+-\s+DoodStream.*$', '', title, flags=re.I).strip()
        if not title.lower().endswith((".mp4", ".mkv")):
            title += ".mp4"

        md5_m = re.search(r'(/pass_md5/[^?\'"\s]+)', html)
        if not md5_m:
            raise ExtractionError("DoodStream token adresi (pass_md5) bulunamadı.")

        pass_url = "https://" + url.split('/')[2] + md5_m.group(1)
        r_pass = self._session.get(pass_url, headers=self._get_headers(referer=url), timeout=10)
        direct_url = r_pass.text.strip() + "z?token=" + md5_m.group(1).split('/')[-1] + "&expiry=" + str(int(time.time() * 1000))

        req_headers = self._get_headers(referer=url)
        return {
            "id": url.rstrip('/').split('/')[-1] or "dood_file",
            "title": title,
            "extractor": "VideoHostExtractor",
            "webpage_url": url,
            "direct_url": direct_url,
            "thumbnail": "",
            "duration": None,
            "headers": req_headers,
            "formats": [{"format_id": "best", "ext": "mp4", "quality": "HD", "url": direct_url}],
            "is_playlist": False,
            "playlist_items": [],
        }

    def _extract_generic_video(self, url: str) -> Dict[str, Any]:
        headers = self._get_headers()
        r = self._session.get(url, headers=headers, timeout=15)
        html = r.text

        title_m = re.search(r'<title>([^<]+)</title>', html, re.I)
        title = title_m.group(1).strip() if title_m else "VideoHost_File.mp4"
        if not title.lower().endswith((".mp4", ".mkv")):
            title += ".mp4"

        # Look for source / mp4 link in JS or video tags
        video_m = re.search(r'[\'"](https?://[^\'"]+\.mp4(?:\?[^\'"]+)?)[\'"]', html)
        if not video_m:
            video_m = re.search(r'src\s*:\s*[\'"](https?://[^\'"]+)[\'"]', html)
        if not video_m:
            raise ExtractionError(f"Bu video sunucusundan doğrudan MP4 bağlantısı çözülemedi: {url}")

        direct_url = video_m.group(1)
        req_headers = self._get_headers(referer=url)
        return {
            "id": url.rstrip('/').split('/')[-1] or "video_file",
            "title": title,
            "extractor": "VideoHostExtractor",
            "webpage_url": url,
            "direct_url": direct_url,
            "thumbnail": "",
            "duration": None,
            "headers": req_headers,
            "formats": [{"format_id": "best", "ext": "mp4", "quality": "HD", "url": direct_url}],
            "is_playlist": False,
            "playlist_items": [],
        }

    def download(
        self,
        url: str,
        output_path: str,
        format_id: str = "best",
        progress_hook: Optional[Callable[[Dict[str, Any]], None]] = None,
        cancel_event: Optional[Any] = None,
        **kwargs: Any,
    ) -> str:
        """Download media using zero-lock fast downloader with Range support."""
        info = self.extract_info(url)
        direct_url = info.get("direct_url") or info["formats"][0]["url"]
        filename = info.get("title") or "streamtape_video.mp4"
        filename = re.sub(r'[\/:*?"<>|]', '_', filename).strip()

        dest_dir = Path(output_path)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target_path = dest_dir / filename

        req_headers = info.get("headers") or self._get_headers(referer=url)
        logger.info("VideoHostExtractor downloading: %s -> %s", direct_url, target_path)

        try:
            download_file_fast(
                url=direct_url,
                target_path=target_path,
                headers=req_headers,
                progress_hook=progress_hook,
                cancel_event=cancel_event,
                display_name=filename,
                num_workers=32,
            )
            return str(target_path)
        except Exception as exc:
            logger.warning("Fast downloader hatası (%s), normal oturum akışıyla indiriliyor...", exc)
            return self._stream_download_fallback(direct_url, target_path, req_headers, progress_hook, cancel_event)

    def _stream_download_fallback(
        self,
        url: str,
        dest_path: Path,
        headers: Dict[str, str],
        progress_hook: Optional[Callable[[Dict[str, Any]], None]],
        cancel_event: Optional[Any],
    ) -> str:
        with self._session.get(url, headers=headers, stream=True, timeout=30) as r:
            r.raise_for_status()
            total_size = int(r.headers.get("content-length") or 0)
            downloaded = 0
            start_time = time.time()

            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=128 * 1024):
                    if cancel_event and cancel_event.is_set():
                        raise ExtractionCancelled("İndirme kullanıcı tarafından iptal edildi.")
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_hook:
                            elapsed = max(time.time() - start_time, 0.001)
                            speed = downloaded / elapsed
                            progress_hook({
                                "status": "downloading",
                                "downloaded": downloaded,
                                "total": total_size,
                                "speed": speed,
                                "eta": int((total_size - downloaded) / speed) if speed > 0 and total_size > 0 else 0,
                            })

        if progress_hook:
            progress_hook({"status": "finished", "downloaded": downloaded, "total": total_size})
        return str(dest_path)
