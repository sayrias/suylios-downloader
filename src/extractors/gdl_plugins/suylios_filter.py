# -*- coding: utf-8 -*-
"""Suylios Downloader - Gallery-DL Domain Filter Plugin.

This plugin monkey-patches gallery_dl.job.Job.handle_url and Job.handle_queue
to prevent gallery-dl from scraping or downloading unwanted social media links
(TikTok, Instagram, Facebook, YouTube, Twitter/X) when they are embedded inside
forum posts or albums (such as SimpCity, Xenforo, Reddit, etc.).

However, if the user explicitly enters a direct TikTok or Instagram link as the
root URL to download, this filter allows it to run normally.
"""

import os
import logging
from gallery_dl import job

logger = logging.getLogger("suylios.plugin.filter")

# Default domains that should never be scraped from inside forum threads/albums
DEFAULT_BLOCKLIST = [
    "tiktok.com",
    "instagram.com",
    "facebook.com",
    "fb.watch",
    "youtube.com",
    "youtu.be",
    "twitter.com",
    "x.com",
]


def _get_blocklist():
    env_val = os.environ.get("SUYLIOS_BLOCKLIST", "")
    if env_val:
        return [x.strip().lower() for x in env_val.split(",") if x.strip()]
    return DEFAULT_BLOCKLIST


def _is_url_blocked(url: str, blocklist: list) -> bool:
    if not url:
        return False
    url_lower = url.lower()
    for domain in blocklist:
        if domain in url_lower:
            return True
    return False


def _is_parent_blocked_domain(job_instance, blocklist: list) -> bool:
    """Check if the root URL itself (user input) belongs to a blocked domain."""
    ext = getattr(job_instance, "extractor", None)
    if ext:
        root_url = getattr(ext, "root", "") or getattr(ext, "url", "")
        if root_url and _is_url_blocked(root_url, blocklist):
            return True
    job_url = getattr(job_instance, "url", "")
    if job_url and _is_url_blocked(job_url, blocklist):
        return True
    return False


# Save original methods before patching
_orig_handle_url = job.Job.handle_url
_orig_handle_queue = job.Job.handle_queue


def _suylios_handle_url(self, url, kwdict):
    blocklist = _get_blocklist()
    # If the parent/root URL is NOT from a blocked domain (e.g. simpcity.cr),
    # but the current sub-link IS a blocked domain (e.g. tiktok.com or instagram.com),
    # skip it immediately!
    if not _is_parent_blocked_domain(self, blocklist) and _is_url_blocked(url, blocklist):
        self.log.info("Suylios Blocklist: Skipping embedded URL %s", url)
        return 0
    return _orig_handle_url(self, url, kwdict)


def _suylios_handle_queue(self, url, kwdict):
    blocklist = _get_blocklist()
    if not _is_parent_blocked_domain(self, blocklist) and _is_url_blocked(url, blocklist):
        self.log.info("Suylios Blocklist: Skipping embedded queue URL %s", url)
        return 0
    return _orig_handle_queue(self, url, kwdict)


# Apply monkey-patch
job.Job.handle_url = _suylios_handle_url
job.Job.handle_queue = _suylios_handle_queue
