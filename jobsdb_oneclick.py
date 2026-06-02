"""
JobsDB one-click flow: search jobs, complete Seek OAuth, then open apply pages.

Usage:
  python jobsdb_oneclick.py --keywords "data analyst" --max 5
  python jobsdb_oneclick.py --keywords "business analyst" --where "Hong Kong" --max 10
"""

import argparse
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from seek_auth import automate_seek_login, has_credentials, page_has_captcha


BASE_URL = "https://hk.jobsdb.com"
SEARCH_API = f"{BASE_URL}/api/jobsearch/v5/search"
USER_DATA_DIR = Path(__file__).parent / "browser_data"
SESSION_PATH = Path(__file__).parent / "session.json"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)


def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _ensure_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        print("Installing Playwright...")
        import subprocess

        subprocess.run([sys.executable, "-m", "pip", "install", "playwright"], check=True)
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        from playwright.sync_api import sync_playwright

        return sync_playwright


def search_api(keywords: str, where: str = "Hong Kong", page: int = 1, page_size: int = 30) -> list:
    params = [
        f"keywords={urllib.request.quote(keywords)}",
        f"where={urllib.request.quote(where)}",
        f"page={page}",
        f"pageSize={page_size}",
    ]
    url = f"{SEARCH_API}?{'&'.join(params)}"

    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        response = urllib.request.urlopen(req, timeout=15, context=_ssl_ctx())
        data = json.loads(response.read())
        return data.get("data", [])
    except Exception as exc:
        print(f"Search API error: {exc}")
        return []


def _wait_for_homepage_login(page, wait_seconds: int, auto_login_enabled: bool) -> bool:
    for remaining in range(wait_seconds, 0, -1):
        try:
            if auto_login_enabled and "login.seek.com" in page.url:
                auth_result = automate_seek_login(page, timeout_seconds=min(remaining, 90))
                if auth_result["completed"]:
                    print("\nAuto-login completed on Seek.")
                elif auth_result["captcha_detected"]:
                    print("\nCAPTCHA detected. Please solve it in the browser.")
                elif auth_result["attempted"]:
                    print(f"\nAuto-login status: {auth_result['status']}")

            sign_in_btn = page.locator('[data-automation="sign in"]').first
            if not sign_in_btn.is_visible(timeout=500):
                return True
        except Exception:
            return True

        if remaining % 15 == 0 or remaining <= 5:
            print(f"  ... {remaining}s remaining")
        time.sleep(1)
    return False


def _ensure_oauth(page, auto_login_enabled: bool) -> bool:
    print("\nVerifying OAuth by opening my-activity...")
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
        print("OAuth already valid.")
        return True

    print("Seek OAuth login is required.")
    if auto_login_enabled:
        auth_result = automate_seek_login(page, timeout_seconds=90)
        if auth_result["completed"]:
            print("Seek login submitted automatically.")
        elif auth_result["captcha_detected"]:
            print("CAPTCHA detected. Please complete it manually.")
        else:
            print(f"Auto-login status: {auth_result['status']}")

    print("Waiting for remaining Seek login, CAPTCHA, or MFA...")
    for remaining in range(90, 0, -1):
        try:
            current_url = page.url
            if "my-activity" in current_url and "oauth" not in current_url:
                print("\nOAuth completed.")
                return True
            if page_has_captcha(page):
                print("\nCAPTCHA still visible. Waiting...")
        except Exception:
            pass

        if remaining % 15 == 0 or remaining <= 5:
            print(f"  ... {remaining}s remaining")
        time.sleep(1)

    print("\nOAuth did not complete in time.")
    return False


def main():
    parser = argparse.ArgumentParser(description="JobsDB One-Click Apply")
    parser.add_argument("--keywords", required=True, help="Search keywords")
    parser.add_argument("--where", default="Hong Kong", help="Location")
    parser.add_argument("--max", type=int, default=5, help="Maximum jobs to open")
    parser.add_argument("--wait", type=int, default=120, help="Seconds to wait for login")
    args = parser.parse_args()

    jobs_data = search_api(args.keywords, args.where, 1, args.max)
    if not jobs_data:
        print("No search results found.")
        return

    jobs = [
        {
            "id": job["id"],
            "roleId": job["roleId"],
            "title": job["title"],
            "company": job.get("companyName", ""),
            "salary": job.get("salaryLabel", "N/A"),
        }
        for job in jobs_data
    ]

    print("=" * 60)
    print("  JobsDB One-Click Apply")
    print("=" * 60)
    print(f"Search: keywords='{args.keywords}', where='{args.where}'")
    print(f"Found {len(jobs)} jobs:\n")
    for index, job in enumerate(jobs, 1):
        print(f"  {index}. {job['title']} @ {job['company']} ({job['salary']})")

    sync_playwright = _ensure_playwright()
    auto_login_enabled = has_credentials()

    print(f"\n{'=' * 60}")
    print("  Browser Login")
    print(f"{'=' * 60}")
    if auto_login_enabled:
        print("Credentials found. Will try to auto-fill Seek login.")
    else:
        print("No credentials configured. Manual login will be required.")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            headless=False,
            user_agent=UA,
            viewport={"width": 1280, "height": 800},
        )
        page = context.pages[0] if context.pages else context.new_page()

        page.goto("https://hk.jobsdb.com/", wait_until="domcontentloaded", timeout=30_000)

        logged_in = _wait_for_homepage_login(page, args.wait, auto_login_enabled)
        if logged_in:
            print("Homepage login looks good.")
        else:
            print("Homepage login was not confirmed in time. Continuing to OAuth check...")

        oauth_valid = _ensure_oauth(page, auto_login_enabled)

        print(f"\n{'=' * 60}")
        print(f"  Opening Apply Pages ({min(args.max, len(jobs))} jobs)")
        print(f"{'=' * 60}")

        results = {"success": 0, "already_applied": 0, "login_required": 0, "error": 0}
        for index, job in enumerate(jobs[: args.max], 1):
            apply_url = f"{BASE_URL}/hk/en/job/{job['id']}-{job['roleId']}/apply"
            print(f"\n[{index}/{min(args.max, len(jobs))}] {job['title']} @ {job['company']}")
            print(f"  Apply URL: {apply_url}")

            try:
                page.goto(apply_url, wait_until="domcontentloaded", timeout=20_000)
            except Exception:
                pass

            time.sleep(4)
            final_url = page.url

            if "/apply" in final_url:
                print("  Apply page loaded.")
                if page_has_captcha(page):
                    print("  CAPTCHA detected on apply page.")
                try:
                    easy_apply = page.locator(
                        'button:has-text("Easy Apply"), [data-automation="easy-apply-btn"]'
                    ).first
                    if easy_apply.is_visible(timeout=2000):
                        print("  Clicking Easy Apply...")
                        easy_apply.click(force=True)
                        time.sleep(3)
                        print(f"  Easy Apply clicked. Current URL: {page.url[:80]}")
                except Exception:
                    pass
                results["success"] += 1
            elif "applied-jobs" in final_url:
                print("  Already applied.")
                results["already_applied"] += 1
            elif "oauth" in final_url or "login.seek.com" in final_url:
                print("  OAuth expired or was not completed.")
                results["login_required"] += 1
            else:
                print(f"  Unexpected final URL: {final_url[:80]}")
                results["error"] += 1

            time.sleep(2)

        print(f"\n{'=' * 60}")
        print("  Results")
        print(f"{'=' * 60}")
        print(f"  Success: {results['success']}")
        print(f"  Already applied: {results['already_applied']}")
        print(f"  Login required: {results['login_required']}")
        print(f"  Errors: {results['error']}")

        try:
            storage_state = context.storage_state()
            with open(SESSION_PATH, "w", encoding="utf-8") as file:
                json.dump(storage_state, file, ensure_ascii=False, indent=2)
            print(f"\nSession saved to: {SESSION_PATH}")
            if oauth_valid:
                print("OAuth cookies are present in the saved session.")
        except Exception:
            pass

        context.close()


if __name__ == "__main__":
    main()
