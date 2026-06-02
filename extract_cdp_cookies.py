"""
Extract JobsDB cookies from a RUNNING Chrome instance via CDP.
Uses Chrome DevTools Protocol to read cookies directly from browser memory.
No file locking issues - works while Chrome is running.

Prerequisite: Chrome must be launched with remote debugging enabled.
  chrome.exe --remote-debugging-port=9222

Or: this script can launch Chrome for you with the flag.
"""

import json
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

CDP_PORT = 9222
CDP_HOST = f"http://localhost:{CDP_PORT}"
TARGET_DOMAIN = ".jobsdb.com"
REQUIRED_COOKIES = ["JobseekerSessionId", "__cf_bm"]
OPTIONAL_COOKIES = ["_cfuvid", "JobseekerVisitorId"]


def check_cdp_available():
    """Check if Chrome CDP is accessible."""
    try:
        r = urllib.request.urlopen(f"{CDP_HOST}/json/version", timeout=3)
        data = json.loads(r.read())
        print(f"Chrome CDP available: {data.get('Browser', 'unknown')}")
        return True
    except:
        return False


def launch_chrome_with_debug():
    """Launch Chrome with remote debugging enabled."""
    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Users\Administrator\AppData\Local\Google\Chrome\Application\chrome.exe",
    ]

    chrome_exe = None
    for p in chrome_paths:
        if Path(p).exists():
            chrome_exe = p
            break

    if not chrome_exe:
        # Try to find via registry
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe")
            chrome_exe = winreg.QueryValue(key, "")
            winreg.CloseKey(key)
        except:
            pass

    if not chrome_exe:
        print("Cannot find Chrome executable.")
        print("Please start Chrome manually with:")
        print(f'  chrome.exe --remote-debugging-port={CDP_PORT}')
        return False

    print(f"Launching Chrome: {chrome_exe}")
    subprocess.Popen([
        chrome_exe,
        f"--remote-debugging-port={CDP_PORT}",
        "--user-data-dir=default",
        "https://hk.jobsdb.com"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Wait for Chrome to start
    for i in range(10):
        time.sleep(1)
        if check_cdp_available():
            return True
        print(f"  Waiting for Chrome... ({i+1}/10)")

    print("Chrome didn't start with CDP in time.")
    return False


def get_all_cookies_via_cdp():
    """Get all cookies from Chrome via CDP Network.getAllCookies."""
    # First get a valid target (page)
    try:
        r = urllib.request.urlopen(f"{CDP_HOST}/json", timeout=5)
        targets = json.loads(r.read())
    except Exception as e:
        print(f"Cannot get Chrome targets: {e}")
        return []

    # Find a page target
    page_target = None
    for t in targets:
        if t.get("type") == "page":
            page_target = t
            break

    if not page_target:
        print("No page targets found in Chrome.")
        return []

    # Get WebSocket URL
    ws_url = page_target.get("webSocketDebuggerUrl")
    if not ws_url:
        print("No WebSocket URL available.")
        return []

    # Use HTTP-based CDP instead of WebSocket (simpler)
    # We need to send CDP commands via the debug target's session
    target_id = page_target.get("id")

    # Send CDP command via HTTP endpoint
    try:
        # Create a new session
        session_url = f"{CDP_HOST}/json/new?{target_id}"
        urllib.request.urlopen(session_url, timeout=5)
    except:
        pass

    # Use the simpler approach: send CDP via fetch to the browser endpoint
    # Actually, CDP over HTTP requires WebSocket. Let's use a different approach.
    # We'll use the /json/protocol endpoint to check, then use a simple approach.

    # Alternative: navigate Chrome to a js snippet that extracts cookies
    # But that won't give us HttpOnly cookies.

    # Best approach for HTTP-only: use CDP's Network.getAllCookies
    # But that requires WebSocket connection which is complex without a library.
    # Let's try using the requests-style approach with the /json endpoint.

    print("CDP WebSocket connection needed for cookie extraction.")
    print("Using alternative method: selenium/playwright...")

    return []


def extract_via_playwright():
    """Extract cookies using Playwright (recommended for running Chrome)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Installing playwright...")
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "playwright"], check=True)
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        from playwright.sync_api import sync_playwright

    print("\nAttempting to connect to running Chrome via CDP...")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        except Exception as e:
            print(f"Cannot connect to Chrome CDP: {e}")
            print("\nPlease restart Chrome with remote debugging:")
            print(f'  1. Close ALL Chrome windows')
            print(f'  2. Run: chrome.exe --remote-debugging-port={CDP_PORT}')
            print(f'  3. Login to https://hk.jobsdb.com')
            print(f'  4. Re-run this script')
            return {}

        # Get cookies from all contexts
        all_cookies = {}
        target_keys = set(REQUIRED_COOKIES + OPTIONAL_COOKIES)

        for context in browser.contexts:
            cookies = context.cookies()
            for cookie in cookies:
                domain = cookie.get("domain", "")
                name = cookie.get("name", "")
                value = cookie.get("value", "")

                if "jobsdb.com" in domain and name in target_keys:
                    httponly = cookie.get("httpOnly", False)
                    secure = cookie.get("secure", False)
                    httponly_flag = "[H]" if httponly else "   "
                    secure_flag = "[S]" if secure else "   "
                    is_target = "*" if name in target_keys else " "
                    display_val = value[:50] + "..." if len(value) > 50 else value
                    print(f"  {is_target} {httponly_flag}{secure_flag} {name} = {display_val}")
                    all_cookies[name] = value

        browser.close()
        return all_cookies


def write_to_config(cookies):
    """Write cookies to config.ini."""
    import configparser

    config_path = Path(__file__).parent / "config.ini"
    cfg = configparser.ConfigParser()
    if config_path.exists():
        cfg.read(config_path, encoding="utf-8")

    if "cookies" not in cfg:
        cfg.add_section("cookies")

    for key, value in cookies.items():
        cfg["cookies"][key] = value

    with open(config_path, "w", encoding="utf-8") as f:
        cfg.write(f)

    print(f"\nWrote {len(cookies)} cookies to {config_path}")


def main():
    print("=" * 60)
    print("  JobsDB Cookie Extractor (CDP / Playwright)")
    print("  Reads from running Chrome - includes HttpOnly")
    print("=" * 60)
    print()

    if not check_cdp_available():
        print("Chrome CDP not available.")
        print()
        ans = input("Launch Chrome with debugging? (y/n): ").strip().lower()
        if ans == "y":
            if not launch_chrome_with_debug():
                sys.exit(1)
            print("\nChrome launched. Please login to JobsDB, then press Enter...")
            input()
        else:
            print("\nPlease start Chrome manually:")
            print(f'  chrome.exe --remote-debugging-port={CDP_PORT}')
            print("\nThen login to https://hk.jobsdb.com and re-run this script.")
            sys.exit(0)

    cookies = extract_via_playwright()

    if cookies:
        missing = [k for k in REQUIRED_COOKIES if k not in cookies]
        if missing:
            print(f"\nMissing required cookies: {', '.join(missing)}")
            print("Please login to https://hk.jobsdb.com first.")
        else:
            print("\nAll required cookies ready!")

        write_to_config(cookies)
    else:
        print("\nNo JobsDB cookies found.")
        print("Please login to https://hk.jobsdb.com in Chrome, then re-run.")


if __name__ == "__main__":
    main()
