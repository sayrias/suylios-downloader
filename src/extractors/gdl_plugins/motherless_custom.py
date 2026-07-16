# -*- coding: utf-8 -*-
"""Custom gallery-dl plugin extractors for motherless.xxx / motherless.com profile, member, shouts, and global galleries."""

import re
import urllib.parse
from gallery_dl import text
from gallery_dl.extractor.common import BaseExtractor, Message


def _is_valid_motherless_id(item_id: str) -> bool:
    if not item_id:
        return False
    item_id = item_id.upper()
    if item_id.lower() in (
        "images", "videos", "shouts", "groups", "galleries", "community", "girls",
        "chat", "store", "upload", "search", "login", "signup", "premium", "register",
        "categories", "members", "terms", "privacy", "contact", "about", "faq",
        "live", "feedback", "dmca", "help", "settings", "status", "mobile",
    ):
        return False
    if any(c.isdigit() for c in item_id):
        return True
    if item_id.startswith("G") and len(item_id) >= 7:
        return True
    return False


class _DummyResponse:
    def __init__(self, text, status_code):
        self.text = text
        self.status_code = status_code


class MotherlessBaseExtractor(BaseExtractor):
    def request(self, url: str, **kwargs):
        try:
            from curl_cffi import requests as c_requests
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Referer": "https://motherless.xxx/",
            }
            r = c_requests.get(url, headers=headers, timeout=15, verify=False, impersonate="chrome124")
            return _DummyResponse(r.text, r.status_code)
        except Exception as e1:
            self.log.debug("curl_cffi get failed for %s (%s), trying gallery-dl urllib3...", url, e1)

        try:
            return BaseExtractor.request(self, url, **kwargs)
        except Exception as e2:
            self.log.debug("urllib3 get failed for %s (%s), trying standard requests...", url, e2)

        import time, requests
        for attempt in range(3):
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                    "Referer": "https://motherless.xxx/",
                }
                r = requests.get(url, headers=headers, timeout=20, verify=False)
                return _DummyResponse(r.text, r.status_code)
            except Exception as e3:
                if attempt == 2:
                    self.log.error("All fetch methods failed for %s: %s", url, e3)
                    raise e3
                time.sleep(1 + attempt)


class MotherlessCustomMediaExtractor(MotherlessBaseExtractor):
    """Robust custom extractor for single motherless.xxx media items (videos/images)."""
    category = "motherless"
    subcategory = "media"
    pattern = r"https?://(?:www\.)?motherless\.(?:xxx|com)/(?:G[IVG])?([A-Z0-9]{6,12})(?:[?/#].*)?$"
    example = "https://motherless.xxx/1A88CFA"

    def __init__(self, match):
        MotherlessBaseExtractor.__init__(self, match)
        self.media_id = match.group(1).upper()

    def items(self):
        url = f"https://motherless.xxx/{self.media_id}"
        self.log.debug("Fetching motherless media page: %s", url)
        try:
            response = self.request(url)
            if response.status_code == 404:
                return
            html = response.text
        except Exception as exc:
            self.log.error("Failed to fetch %s: %s", url, exc)
            return

        file_url = ""
        # 1. Check __fileurl
        m_furl = re.search(r'__fileurl\s*=\s*["\']([^"\']+)["\']', html)
        if m_furl:
            file_url = m_furl.group(1)
        # 2. Check <source src="...">
        if not file_url:
            m_src = re.search(r'<source[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
            if m_src:
                file_url = m_src.group(1)
        # 3. Check direct CDN URL in html
        if not file_url:
            m_cdn = re.search(r'(https?://cdn\d+-(?:videos|images)\.motherlessmedia\.com/[^"\'\s<>]+)', html, re.IGNORECASE)
            if m_cdn:
                file_url = m_cdn.group(1)

        if not file_url:
            self.log.error("Could not extract file URL for %s", self.media_id)
            return

        if file_url.startswith("//"):
            file_url = "https:" + file_url
        elif file_url.startswith("/"):
            file_url = "https://motherless.xxx" + file_url

        title = self.media_id
        m_title = re.search(r'<title>([^<]+)</title>', html, re.IGNORECASE)
        if m_title:
            raw_t = m_title.group(1).rpartition(" | ")[0].strip()
            if raw_t and raw_t != "Motherless":
                title = text.unescape(raw_t)

        uploader = ""
        m_upl = re.search(r'class="username">([^<]+)<', html)
        if m_upl:
            uploader = text.unescape(m_upl.group(1).strip())

        ext = text.ext_from_url(file_url) or "mp4"
        data = {
            "id": self.media_id,
            "title": title,
            "uploader": uploader,
            "url": file_url,
            "filename": f"{self.media_id} {title}",
            "extension": ext,
            "_filename": f"{self.media_id} {title}.{ext}",
            "_http_headers": {
                "Referer": "https://motherless.xxx/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            },
        }
        yield Message.Directory, "", data
        yield Message.Url, file_url, data


class MotherlessProfileExtractor(MotherlessBaseExtractor):
    """Extractor for motherless.xxx user profiles (/u/username)."""
    category = "motherless"
    subcategory = "profile"
    pattern = r"https?://(?:www\.)?motherless\.(?:xxx|com)/u/([a-zA-Z0-9_-]+)(?:[?/#](.*))?"
    example = "https://motherless.xxx/u/dhaero99?t=v"

    def __init__(self, match):
        MotherlessBaseExtractor.__init__(self, match)
        self.username = match.group(1)
        self.query_str = match.group(2) or ""

    def items(self):
        url_base = f"https://motherless.xxx/u/{self.username}"
        # Parse existing query params (e.g. t=v or t=i)
        params = urllib.parse.parse_qs(self.query_str)
        t_val = params.get("t", ["all"])[0]

        seen_ids = set()
        max_pages = 20  # Safe upper limit of profile pages to check per queue

        for page in range(1, max_pages + 1):
            if t_val and t_val != "all":
                page_url = f"{url_base}?t={t_val}&page={page}"
            else:
                page_url = f"{url_base}?page={page}" if page > 1 else url_base

            self.log.debug("Fetching profile page %s: %s", page, page_url)
            try:
                response = self.request(page_url)
                if response.status_code == 404:
                    break
                html = response.text
            except Exception as exc:
                self.log.error("Failed to fetch %s: %s", page_url, exc)
                break

            # Find all thumbnail links pointing to media or galleries: e.g. <a href="/1A88CFA" or <a href="/GB57E7A5"
            # Exclude standard navigation paths like /u/, /m/, /g/, /images, /videos, /shouts, /login, /signup
            found_on_page = 0
            for match in re.finditer(r'<a[^>]+href=["\']/(G?[A-Z0-9]{6,12})["\']', html, re.IGNORECASE):
                item_id = match.group(1).upper()
                if item_id in seen_ids or not _is_valid_motherless_id(item_id):
                    continue
                seen_ids.add(item_id)
                found_on_page += 1
                yield Message.Queue, f"https://motherless.xxx/{item_id}", {}

            # Also check thumb data attributes or div links if distinct
            for match in re.finditer(r'data-codename=["\'](G?[A-Z0-9]{6,12})["\']', html, re.IGNORECASE):
                item_id = match.group(1).upper()
                if item_id not in seen_ids and _is_valid_motherless_id(item_id):
                    seen_ids.add(item_id)
                    found_on_page += 1
                    yield Message.Queue, f"https://motherless.xxx/{item_id}", {}

            if found_on_page == 0 or "class=\"pagination\"" not in html and "next" not in html.lower():
                break


class MotherlessMemberExtractor(MotherlessBaseExtractor):
    """Extractor for motherless.xxx member profiles (/m/username)."""
    category = "motherless"
    subcategory = "member"
    pattern = r"https?://(?:www\.)?motherless\.(?:xxx|com)/m/([a-zA-Z0-9_-]+)"
    example = "https://motherless.xxx/m/Sakura5"

    def __init__(self, match):
        MotherlessBaseExtractor.__init__(self, match)
        self.username = match.group(1)

    def items(self):
        # A member page (/m/username) contains uploads (/u/username) and member shouts (/shouts/member/username)
        self.log.info("Queuing member %s uploads and shouts...", self.username)
        yield Message.Queue, f"https://motherless.xxx/u/{self.username}?t=u", {}
        yield Message.Queue, f"https://motherless.xxx/u/{self.username}", {}
        yield Message.Queue, f"https://motherless.xxx/shouts/member/{self.username}", {}

        # Also extract any direct media links embedded right on the member page
        try:
            html = self.request(self.url).text
            for match in re.finditer(r'<a[^>]+href=["\']/(G?[A-Z0-9]{6,12})["\']', html, re.IGNORECASE):
                item_id = match.group(1).upper()
                if _is_valid_motherless_id(item_id):
                    yield Message.Queue, f"https://motherless.xxx/{item_id}", {}
        except Exception:
            pass


class MotherlessGlobalExtractor(MotherlessBaseExtractor):
    """Extractor for motherless.xxx global sections (/images, /videos, /galleries)."""
    category = "motherless"
    subcategory = "global"
    pattern = r"https?://(?:www\.)?motherless\.(?:xxx|com)/(images|videos|galleries)(?:[?/#](.*))?$"
    example = "https://motherless.xxx/images"

    def __init__(self, match):
        MotherlessBaseExtractor.__init__(self, match)
        self.section = match.group(1).lower()
        self.query_str = match.group(2) or ""

    def items(self):
        params = urllib.parse.parse_qs(self.query_str)
        start_page = int(params.get("page", ["1"])[0]) if params.get("page", ["1"])[0].isdigit() else 1
        max_pages = start_page + 10  # Check up to 10 pages from requested start page

        seen_ids = set()
        for page in range(start_page, max_pages + 1):
            page_url = f"https://motherless.xxx/{self.section}?page={page}"
            self.log.debug("Fetching %s page %s", self.section, page)
            try:
                response = self.request(page_url)
                if response.status_code == 404:
                    break
                html = response.text
            except Exception:
                break

            found = 0
            for match in re.finditer(r'<a[^>]+href=["\']/(G?[A-Z0-9]{6,12})["\']', html, re.IGNORECASE):
                item_id = match.group(1).upper()
                if item_id in seen_ids or not _is_valid_motherless_id(item_id):
                    continue
                seen_ids.add(item_id)
                found += 1
                yield Message.Queue, f"https://motherless.xxx/{item_id}", {}

            if found == 0:
                break


class MotherlessShoutsExtractor(MotherlessBaseExtractor):
    """Extractor for motherless.xxx global or member shouts (/shouts, /shouts?shoutmodal=ID, /shouts/member/user)."""
    category = "motherless"
    subcategory = "shouts"
    pattern = r"https?://(?:www\.)?motherless\.(?:xxx|com)/shouts(?:[?/#](.*))?$"
    example = "https://motherless.xxx/shouts?page=1&shoutmodal=69639481"

    def __init__(self, match):
        MotherlessBaseExtractor.__init__(self, match)
        self.query_str = match.group(1) or ""

    def items(self):
        params = urllib.parse.parse_qs(self.query_str)
        shout_modal = params.get("shoutmodal", [""])[0]
        start_page = int(params.get("page", ["1"])[0]) if params.get("page", ["1"])[0].isdigit() else 1

        seen_ids = set()
        seen_urls = set()

        pages_to_check = [start_page] if shout_modal else list(range(start_page, start_page + 10))

        for page in pages_to_check:
            if shout_modal:
                page_url = f"https://motherless.xxx/shouts?page={page}&shoutmodal={shout_modal}"
            else:
                page_url = f"https://motherless.xxx/shouts?page={page}"

            self.log.debug("Fetching shouts page: %s", page_url)
            try:
                response = self.request(page_url)
                if response.status_code == 404:
                    break
                html = response.text
            except Exception:
                break

            found = 0
            # 1. Check for standard motherless media IDs inside shout content or links
            for match in re.finditer(r'<a[^>]+href=["\']/(G?[A-Z0-9]{6,12})["\']', html, re.IGNORECASE):
                item_id = match.group(1).upper()
                if item_id in seen_ids or not _is_valid_motherless_id(item_id):
                    continue
                seen_ids.add(item_id)
                found += 1
                yield Message.Queue, f"https://motherless.xxx/{item_id}", {}

            # 2. Check for direct CDN image or video files embedded inside shout modals or cards
            for match in re.finditer(r'(https?://cdn\d*-(?:images|videos)\.motherlessmedia\.com/[^"\'>\s]+)', html, re.IGNORECASE):
                media_url = match.group(1)
                if media_url in seen_urls:
                    continue
                seen_urls.add(media_url)
                found += 1
                filename = media_url.rpartition("/")[-1].rpartition("?")[0] or "shout_media.jpg"
                ext = filename.rpartition(".")[-1] if "." in filename else "jpg"
                yield Message.Url, media_url, {
                    "filename": filename,
                    "extension": ext,
                    "_http_headers": {
                        "Referer": "https://motherless.xxx/",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    },
                }

            if found == 0 and not shout_modal:
                break


class MotherlessMemberShoutsExtractor(MotherlessBaseExtractor):
    """Extractor specifically for member shouts (/shouts/member/username)."""
    category = "motherless"
    subcategory = "member_shouts"
    pattern = r"https?://(?:www\.)?motherless\.(?:xxx|com)/shouts/member/([a-zA-Z0-9_-]+)(?:[?/#](.*))?"
    example = "https://motherless.xxx/shouts/member/Painman66"

    def __init__(self, match):
        MotherlessBaseExtractor.__init__(self, match)
        self.username = match.group(1)
        self.query_str = match.group(2) or ""

    def items(self):
        params = urllib.parse.parse_qs(self.query_str)
        start_page = int(params.get("page", ["1"])[0]) if params.get("page", ["1"])[0].isdigit() else 1

        seen_ids = set()
        seen_urls = set()

        for page in range(start_page, start_page + 10):
            page_url = f"https://motherless.xxx/shouts/member/{self.username}?page={page}"
            self.log.debug("Fetching member shouts page: %s", page_url)
            try:
                response = self.request(page_url)
                if response.status_code == 404:
                    break
                html = response.text
            except Exception:
                break

            found = 0
            for match in re.finditer(r'<a[^>]+href=["\']/(G?[A-Z0-9]{6,12})["\']', html, re.IGNORECASE):
                item_id = match.group(1).upper()
                if item_id in seen_ids or not _is_valid_motherless_id(item_id):
                    continue
                seen_ids.add(item_id)
                found += 1
                yield Message.Queue, f"https://motherless.xxx/{item_id}", {}

            for match in re.finditer(r'(https?://cdn\d*-(?:images|videos)\.motherlessmedia\.com/[^"\'>\s]+)', html, re.IGNORECASE):
                media_url = match.group(1)
                if media_url in seen_urls:
                    continue
                seen_urls.add(media_url)
                found += 1
                filename = media_url.rpartition("/")[-1].rpartition("?")[0] or f"shout_{self.username}.jpg"
                ext = filename.rpartition(".")[-1] if "." in filename else "jpg"
                yield Message.Url, media_url, {
                    "filename": filename,
                    "extension": ext,
                    "_http_headers": {
                        "Referer": "https://motherless.xxx/",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    },
                }

            if found == 0:
                break
