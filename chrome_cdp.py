"""
Helpers for attaching Playwright to a real Chrome instance via CDP.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


DEFAULT_CDP_URL = os.getenv("CHROME_CDP_URL", "http://127.0.0.1:9222")


def get_cdp_url() -> str:
    return DEFAULT_CDP_URL.rstrip("/")


def check_cdp_available(cdp_url: str | None = None) -> tuple[bool, dict]:
    cdp_url = (cdp_url or get_cdp_url()).rstrip("/")
    try:
        response = urllib.request.urlopen(f"{cdp_url}/json/version", timeout=3)
        data = json.loads(response.read())
        return True, data
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False, {}


def connect_browser_over_cdp(playwright, cdp_url: str | None = None):
    cdp_url = (cdp_url or get_cdp_url()).rstrip("/")
    return playwright.chromium.connect_over_cdp(cdp_url)


def get_first_context(browser):
    if browser.contexts:
        return browser.contexts[0]
    return browser.new_context()


def get_first_page(context):
    if context.pages:
        return context.pages[0]
    return context.new_page()
