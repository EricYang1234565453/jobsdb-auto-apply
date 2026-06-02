"""
Helpers for automating the Seek / JobsDB login flow in Playwright.
"""

from __future__ import annotations

import configparser
import os
import time
from pathlib import Path


CONFIG_PATH = Path(__file__).parent / "config.ini"
SEEK_LOGIN_HOST = "login.seek.com"
DEFAULT_TIMEOUT_MS = 15_000

EMAIL_SELECTORS = [
    'input[name="email"]',
    'input[name="username"]',
    'input[name="identifier"]',
    'input[type="email"]',
    'input[autocomplete="username"]',
    'input[autocomplete="email"]',
    '#email',
]

PASSWORD_SELECTORS = [
    'input[name="password"]',
    'input[type="password"]',
    'input[autocomplete="current-password"]',
    '#password',
]

SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'button:has-text("Sign in")',
    'button:has-text("Log in")',
    'button:has-text("Continue")',
    'input[type="submit"]',
]

CAPTCHA_SELECTORS = [
    'iframe[src*="recaptcha"]',
    'iframe[title*="reCAPTCHA"]',
    '.g-recaptcha',
    '[data-sitekey]',
    '#captcha',
]

POST_LOGIN_SELECTORS = [
    '[data-automation="user-avatar"]',
    'a[href*="my-activity"]',
    'button[aria-label*="account"]',
]


def load_seek_credentials() -> tuple[str | None, str | None]:
    username = os.getenv("SEEK_USERNAME") or os.getenv("JOBSDB_USERNAME")
    password = os.getenv("SEEK_PASSWORD") or os.getenv("JOBSDB_PASSWORD")

    if username and password:
        return username.strip(), password

    if not CONFIG_PATH.exists():
        return None, None

    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    cfg.read(CONFIG_PATH, encoding="utf-8")

    if not cfg.has_section("auth"):
        return username, password

    username = username or cfg.get("auth", "SEEK_USERNAME", fallback=None)
    password = password or cfg.get("auth", "SEEK_PASSWORD", fallback=None)

    return (username.strip() if username else None), password


def has_credentials() -> bool:
    username, password = load_seek_credentials()
    return bool(username and password)


def _first_visible(page, selectors: list[str], timeout_ms: int = 1000):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=timeout_ms):
                return locator, selector
        except Exception:
            continue
    return None, None


def is_seek_login_page(page) -> bool:
    try:
        return SEEK_LOGIN_HOST in page.url
    except Exception:
        return False


def page_has_captcha(page) -> bool:
    locator, _ = _first_visible(page, CAPTCHA_SELECTORS, timeout_ms=500)
    if locator:
        return True

    try:
        html = page.content().lower()
    except Exception:
        return False

    return "recaptcha" in html or "captcha" in html or "hcaptcha" in html


def wait_for_login_completion(page, timeout_seconds: int = 120) -> dict:
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        current_url = page.url

        if "my-activity" in current_url and "oauth" not in current_url:
            return {"status": "completed", "url": current_url}

        if not is_seek_login_page(page):
            locator, _ = _first_visible(page, POST_LOGIN_SELECTORS, timeout_ms=500)
            if locator:
                return {"status": "completed", "url": current_url}

        if page_has_captcha(page):
            return {"status": "captcha", "url": current_url}

        time.sleep(1)

    return {"status": "timeout", "url": page.url}


def automate_seek_login(page, timeout_seconds: int = 120) -> dict:
    result = {
        "attempted": False,
        "completed": False,
        "captcha_detected": False,
        "credentials_found": False,
        "status": "skipped",
        "detail": "",
    }

    username, password = load_seek_credentials()
    if not username or not password:
        result["detail"] = "No SEEK_USERNAME / SEEK_PASSWORD configured."
        return result

    result["credentials_found"] = True

    if not is_seek_login_page(page):
        result["status"] = "not_on_seek_login"
        result["detail"] = f"Current URL is {page.url}"
        return result

    email_input, email_selector = _first_visible(page, EMAIL_SELECTORS, timeout_ms=3000)
    password_input, password_selector = _first_visible(page, PASSWORD_SELECTORS, timeout_ms=3000)
    if not email_input or not password_input:
        result["status"] = "form_not_found"
        result["detail"] = "Could not find email/password inputs on Seek login page."
        return result

    result["attempted"] = True

    email_input.click()
    email_input.fill(username)
    password_input.click()
    password_input.fill(password)

    if page_has_captcha(page):
        result["captcha_detected"] = True
        result["status"] = "captcha"
        result["detail"] = "CAPTCHA detected before submit."
        return result

    submit_button, submit_selector = _first_visible(page, SUBMIT_SELECTORS, timeout_ms=2000)
    if submit_button:
        submit_button.click()
    else:
        password_input.press("Enter")
        submit_selector = "Enter"

    completion = wait_for_login_completion(page, timeout_seconds=timeout_seconds)
    result["status"] = completion["status"]
    result["completed"] = completion["status"] == "completed"
    result["captcha_detected"] = completion["status"] == "captcha"
    result["detail"] = (
        f"email={email_selector}, password={password_selector}, submit={submit_selector}"
    )

    return result
