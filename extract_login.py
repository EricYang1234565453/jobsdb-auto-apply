"""
Capture a JobsDB / Seek authenticated session.

Default behavior:
  1. Try to attach to a real Chrome instance over CDP.
  2. Reuse the user's existing OAuth session if Chrome is already logged in.
  3. Fall back to a dedicated Playwright persistent profile if CDP is unavailable.

Usage:
  python extract_login.py
  python extract_login.py 180
"""

import json
import sys
import time
from pathlib import Path

from chrome_cdp import check_cdp_available, connect_browser_over_cdp, get_cdp_url
from seek_auth import automate_seek_login, has_credentials, page_has_captcha


SESSION_PATH = Path(__file__).parent / "session.json"
USER_DATA_DIR = Path(__file__).parent / "browser_data"
REQUIRED_COOKIES = ["JobseekerSessionId", "__cf_bm"]
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)


def _ensure_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        print("Installing playwright...")
        import subprocess

        subprocess.run([sys.executable, "-m", "pip", "install", "playwright"], check=True)
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        from playwright.sync_api import sync_playwright

        return sync_playwright


def _wait_for_homepage_login(page, wait_seconds: int, auto_login_enabled: bool) -> bool:
    for remaining in range(wait_seconds, 0, -1):
        try:
            if auto_login_enabled and "login.seek.com" in page.url:
                auth_result = automate_seek_login(page, timeout_seconds=min(remaining, 90))
                if auth_result["completed"]:
                    print("\nAuto-login completed on Seek.")
                elif auth_result["captcha_detected"]:
                    print("\nCAPTCHA detected on Seek. Please solve it in the browser.")

            logged_in = page.locator(
                'a[href*="my-activity"], [data-automation="user-avatar"]'
            ).first.is_visible(timeout=500)
            if logged_in:
                print("\nHomepage shows logged-in state.")
                return True
        except Exception:
            pass

        if remaining % 15 == 0 or remaining <= 5:
            print(f"  ... {remaining}s remaining")
        time.sleep(1)
    return False


def _ensure_oauth(page, auto_login_enabled: bool) -> bool:
    print("\nVerifying OAuth authentication (visiting my-activity page)...")
    try:
        page.goto(
            "https://hk.jobsdb.com/my-activity/applied-jobs",
            wait_until="domcontentloaded",
            timeout=20_000,
        )
    except Exception:
        pass

    time.sleep(3)
    final_url = page.url
    if "oauth/login" not in final_url and "login.seek.com" not in final_url:
        print("my-activity page accessible. OAuth is valid.")
        return True

    print(f"OAuth login required. URL: {final_url[:80]}...")
    if auto_login_enabled:
        auth_result = automate_seek_login(page, timeout_seconds=90)
        if auth_result["completed"]:
            print("Seek OAuth login submitted automatically.")
        elif auth_result["captcha_detected"]:
            print("CAPTCHA detected on Seek. Please complete it in the browser.")

    print("Please finish any remaining Seek login, CAPTCHA, or MFA in the browser.")
    for remaining in range(90, 0, -1):
        try:
            current_url = page.url
            if "my-activity" in current_url and "oauth" not in current_url:
                print("\nOAuth completed.")
                return True
            if page_has_captcha(page):
                print("\nCAPTCHA still present on Seek. Waiting for manual completion...")
        except Exception:
            pass

        if remaining % 15 == 0 or remaining <= 5:
            print(f"  ... {remaining}s remaining")
        time.sleep(1)

    print("\nOAuth login not completed in time. Session may not work for apply pages.")
    return False


def _summarize_storage_state(storage_state: dict, source_label: str, oauth_valid: bool):
    cookies = storage_state.get("cookies", [])
    domains = {}
    for cookie in cookies:
        domain = cookie.get("domain", "unknown")
        domains.setdefault(domain, []).append(cookie.get("name", ""))

    print(f"\nTotal cookies: {len(cookies)} across {len(domains)} domains:")
    for domain, names in sorted(domains.items()):
        print(f"  {domain:35s} {', '.join(names)}")

    jobsdb_cookies = [c for c in cookies if "jobsdb.com" in c.get("domain", "")]
    seek_cookies = [c for c in cookies if "seek.com" in c.get("domain", "")]
    required_found = [c["name"] for c in jobsdb_cookies if c["name"] in REQUIRED_COOKIES]

    print("\nKey JobsDB cookies:")
    for cookie in jobsdb_cookies:
        if cookie["name"] in REQUIRED_COOKIES:
            value = cookie["value"]
            display_value = value[:50] + "..." if len(value) > 50 else value
            flags = f"{'[H]' if cookie.get('httpOnly') else '   '}{'[S]' if cookie.get('secure') else '   '}"
            print(f"  * {flags} {cookie['name']} = {display_value}")

    print(f"\nSeek.com cookies ({len(seek_cookies)}): {[c['name'] for c in seek_cookies]}")

    with open(SESSION_PATH, "w", encoding="utf-8") as file:
        json.dump(storage_state, file, ensure_ascii=False, indent=2)

    print(f"\n{'=' * 60}")
    print(f"Full session saved to: {SESSION_PATH}")
    print(f"  Session source: {source_label}")
    print(f"  Cookies: {len(cookies)} across {len(domains)} domains")

    missing = [name for name in REQUIRED_COOKIES if name not in required_found]
    if missing:
        print(f"\nMissing required cookies: {', '.join(missing)}")
    else:
        print("\nAll required cookies present.")

    if not seek_cookies:
        print("\nNo seek.com cookies found.")
        print("Apply pages may not work. Re-run and complete the OAuth step.")
    elif oauth_valid:
        print("\nSeek OAuth cookies were captured successfully.")


def _capture_via_cdp(sync_playwright, wait_seconds: int):
    cdp_url = get_cdp_url()
    available, version_info = check_cdp_available(cdp_url)
    if not available:
        return False

    print(f"CDP available at {cdp_url}")
    if version_info.get("Browser"):
        print(f"Connected browser: {version_info['Browser']}")

    with sync_playwright() as p:
        browser = connect_browser_over_cdp(p, cdp_url)
        if not browser.contexts:
            print("Connected to Chrome, but no browser contexts were exposed over CDP.")
            browser.close()
            return False

        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()

        auto_login_enabled = has_credentials()
        print("Using your real Chrome session via CDP.")
        print("If Chrome is already logged into Seek OAuth, no credentials are needed.")

        try:
            page.goto("https://hk.jobsdb.com/", wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            pass

        logged_in = _wait_for_homepage_login(page, wait_seconds, auto_login_enabled)
        if not logged_in:
            print("\nHomepage login was not confirmed in time. Continuing to OAuth check...")

        oauth_valid = _ensure_oauth(page, auto_login_enabled)
        storage_state = context.storage_state()
        browser.close()

    _summarize_storage_state(storage_state, f"real Chrome via CDP ({cdp_url})", oauth_valid)
    return True


def _capture_via_persistent_context(sync_playwright, wait_seconds: int):
    auto_login_enabled = has_credentials()

    with sync_playwright() as p:
        print("CDP not available. Falling back to Playwright persistent context...")
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            headless=False,
            user_agent=UA,
            viewport={"width": 1280, "height": 800},
        )
        page = context.pages[0] if context.pages else context.new_page()

        page.goto("https://hk.jobsdb.com/", wait_until="domcontentloaded", timeout=30_000)
        try:
            sign_in = page.locator(
                'a[href*="sign-in"], a[href*="login"], button:has-text("Sign in"), '
                'a:has-text("Sign in")'
            ).first
            if sign_in.is_visible(timeout=3000):
                sign_in.click()
                page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except Exception:
            pass

        logged_in = _wait_for_homepage_login(page, wait_seconds, auto_login_enabled)
        if not logged_in:
            print("\nHomepage login was not confirmed in time. Continuing to OAuth check...")

        oauth_valid = _ensure_oauth(page, auto_login_enabled)
        storage_state = context.storage_state()
        context.close()

    _summarize_storage_state(storage_state, f"Playwright persistent profile at {USER_DATA_DIR}", oauth_valid)


def main():
    sync_playwright = _ensure_playwright()
    wait_seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 120

    print("=" * 60)
    print("  JobsDB Session Extractor")
    print("=" * 60)
    print("1. First tries to attach to your real Chrome via CDP")
    print("2. Reuses your existing Seek OAuth session when available")
    print("3. Falls back to a dedicated Playwright profile only if CDP is unavailable")
    print()

    if _capture_via_cdp(sync_playwright, wait_seconds):
        print("\nYou can now run:")
        print("  python jobsdb_apply.py apply --job-id 92360739 --role-id data-analyst --use-cdp")
        print("  python jobsdb_apply.py apply-all --keywords 'data analyst' --max 5 --use-cdp")
        return

    _capture_via_persistent_context(sync_playwright, wait_seconds)
    print("\nYou can now run:")
    print("  python jobsdb_apply.py apply --job-id 92360739 --role-id data-analyst")
    print("  python jobsdb_apply.py apply-all --keywords 'data analyst' --max 5")


if __name__ == "__main__":
    main()
