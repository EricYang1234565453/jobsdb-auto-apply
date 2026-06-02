"""Quick launcher: kill Chrome, relaunch with CDP debug port enabled."""
import subprocess
import sys
import time
from pathlib import Path

# Kill existing Chrome
subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)

# Find Chrome
chrome_exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
if not Path(chrome_exe).exists():
    chrome_exe = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not Path(chrome_exe).exists():
    import os
    local_app = os.environ.get("LOCALAPPDATA", "")
    fallback = Path(local_app) / "Google" / "Chrome" / "Application" / "chrome.exe"
    if fallback.exists():
        chrome_exe = str(fallback)
if not Path(chrome_exe).exists():
    print("Cannot find Chrome.exe")
    sys.exit(1)

user_data = str(Path(__file__).parent / "chrome_debug_profile")
print(f"Chrome: {chrome_exe}")
print(f"Profile: {user_data}")

subprocess.Popen(
    [chrome_exe, "--remote-debugging-port=9222", f"--user-data-dir={user_data}",
     "https://hk.jobsdb.com"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

# Wait for CDP
sys.path.insert(0, str(Path(__file__).parent))
for i in range(20):
    time.sleep(1)
    from chrome_cdp import check_cdp_available
    ok, info = check_cdp_available()
    if ok:
        print(f"CDP ready after {i + 1}s")
        print(f"Browser: {info.get('Browser', '')}")
        sys.exit(0)
    if i % 5 == 0:
        print(f"  waiting... ({i + 1}s)")

print("CDP not available after 20s")
sys.exit(1)
