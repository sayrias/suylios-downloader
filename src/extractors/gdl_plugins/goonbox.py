# -*- coding: utf-8 -*-
"""Custom gallery-dl extractor for goonbox.cr / goonbox.cc albums and images."""

from gallery_dl import text
from gallery_dl.extractor.common import BaseExtractor, Message


class GoonboxExtractor(BaseExtractor):
    """Extractor for goonbox.cr albums and single images."""
    category = "goonbox"
    subcategory = "album"
    directory_fmt = ("{category}", "{album_title}")
    filename_fmt = "{filename}.{extension}"
    archive_fmt = "{album_id}_{filename}"
    description = "GoonBox image hosting"
    example = "https://goonbox.cr/a/example"
    pattern = r"https?://(?:www\.)?goonbox\.(?:cr|cc)/(?:[ai]|img|images?|albums?)/([a-zA-Z0-9_-]+)"

    def __init__(self, match):
        BaseExtractor.__init__(self, match)
        self.album_id = match.group(1)

    def items(self):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": self.url,
        }

        # Check if URL is for a single image (/i/ID, /img/ID, /image/ID, /images/ID)
        if any(x in self.url for x in ("/i/", "/img/", "/image/", "/images/")):
            yield Message.Directory, "", {"album_id": self.album_id, "album_title": self.album_id}
            url = f"https://goonbox.cr/api/images/{self.album_id}"
            try:
                data = self.request(url, headers=headers).json()
            except Exception:
                # Fallback directly if API fails
                yield Message.Url, f"https://goonbox.cr/img/{self.album_id}", {
                    "filename": self.album_id,
                    "extension": "jpg",
                }
                return

            img = data.get("image") or data
            if isinstance(img, dict):
                img_url = img.get("original_url") or img.get("url") or f"https://goonbox.cr/img/{self.album_id}"
                filename = img.get("original_filename") or img.get("filename") or self.album_id
                if "." in filename:
                    filename, _, ext = filename.rpartition(".")
                else:
                    ext = img.get("extension") or "jpg"
                yield Message.Url, img_url, {
                    "filename": filename,
                    "extension": ext,
                }
            return

        # Otherwise handle album (/a/ID, /album/ID, /albums/ID)
        page = 1
        total_pages = 1
        is_first = True
        album_title = self.album_id

        while page <= total_pages:
            if is_first:
                url = f"https://goonbox.cr/api/albums/{self.album_id}"
                try:
                    data = self.request(url, headers=headers).json()
                except Exception as e:
                    self.log.error("Failed to fetch goonbox album metadata: %s", e)
                    break

                images = data.get("images") or []
                pag = data.get("pagination") or {}
                total_pages = int(pag.get("last_page") or 1)
                album_info = data.get("album") or {}
                album_title = album_info.get("title") or self.album_id
                is_first = False
                yield Message.Directory, "", {"album_id": self.album_id, "album_title": album_title}
            else:
                url = f"https://goonbox.cr/api/albums/{self.album_id}/images?page={page}&per_page=100"
                try:
                    data = self.request(url, headers=headers).json()
                except Exception as e:
                    self.log.error("Failed to fetch goonbox page %d: %s", page, e)
                    break
                images = data.get("images") or []

            if not images:
                break

            for img in images:
                if not isinstance(img, dict):
                    continue
                img_url = img.get("original_url") or img.get("url")
                if not img_url:
                    img_id = img.get("encoded_id") or img.get("id")
                    if img_id:
                        img_url = f"https://goonbox.cr/img/{img_id}"
                if not img_url:
                    continue

                filename = img.get("original_filename") or img.get("filename") or img.get("file_name")
                if not filename:
                    text.nameext_from_url(img_url, img)
                    filename = img.get("filename") or str(img.get("id") or "image")
                    ext = img.get("extension") or "jpg"
                else:
                    if "." in filename:
                        filename, _, ext = filename.rpartition(".")
                    else:
                        ext = img.get("extension") or "jpg"

                yield Message.Url, img_url, {
                    "filename": filename,
                    "extension": ext,
                    "album_title": album_title,
                }

            page += 1
