"""
Extract JobsDB cookies from Chrome local database.
No manual copy needed - directly decrypts Chrome's Cookies file.
"""

import base64
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

_local_app = os.environ.get("LOCALAPPDATA", "")
_CHROME_USER_DATA = os.path.join(_local_app, "Google", "Chrome", "User Data")

# Try multiple possible cookie file locations (Chrome moved Cookies to Network/ subfolder)
def _find_cookie_paths():
    """Find all Chrome cookie database files."""
    base = Path(_CHROME_USER_DATA)
    paths = []
    if not base.exists():
        return paths
    for p in sorted(base.glob("*/Network/Cookies")):
        paths.append(p)
    for p in sorted(base.glob("*/Cookies")):
        if p not in paths:
            paths.append(p)
    return paths

CHROME_LOCAL_STATE = Path(os.path.join(_local_app, "Google", "Chrome", "User Data", "Local State"))

TARGET_DOMAIN = ".jobsdb.com"
REQUIRED_COOKIES = ["JobseekerSessionId", "__cf_bm"]
OPTIONAL_COOKIES = ["_cfuvid", "JobseekerVisitorId"]


def get_chrome_encryption_key():
    """Get Chrome encryption key from Local State."""
    if not CHROME_LOCAL_STATE.exists():
        print(f"Chrome Local State not found: {CHROME_LOCAL_STATE}")
        sys.exit(1)

    with open(CHROME_LOCAL_STATE, "r", encoding="utf-8") as f:
        local_state = json.load(f)

    encrypted_key = base64.b64decode(local_state["os_crypt"]["encrypted_key"])
    encrypted_key = encrypted_key[5:]  # strip "DPAPI" prefix

    import ctypes
    import ctypes.wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    p = ctypes.create_string_buffer(encrypted_key)
    blob_in = DATA_BLOB(len(encrypted_key), p)
    blob_out = DATA_BLOB()

    if ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        key = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return key
    else:
        print("DPAPI decrypt failed")
        sys.exit(1)


def decrypt_cookie_value(encrypted_value, key):
    """Decrypt Chrome cookie value."""
    if not encrypted_value:
        return ""

    if encrypted_value[:3] in (b"v10", b"v20"):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = encrypted_value[3:15]
        ciphertext = encrypted_value[15:]
        aesgcm = AESGCM(key)
        decrypted = aesgcm.decrypt(nonce, ciphertext, None)
        return decrypted.decode("utf-8", errors="replace")

    elif encrypted_value[:5] == b"\x01\x00\x00\x00":
        import ctypes
        import ctypes.wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        p = ctypes.create_string_buffer(encrypted_value)
        blob_in = DATA_BLOB(len(encrypted_value), p)
        blob_out = DATA_BLOB()

        if ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            value = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
            return value.decode("utf-8", errors="replace")
        return "[DPAPI failed]"

    else:
        return encrypted_value.decode("utf-8", errors="replace")


def read_chrome_cookies():
    """Read JobsDB cookies from Chrome cookie database."""
    cookie_paths = _find_cookie_paths()
    if not cookie_paths:
        print(f"No Chrome cookie files found under {_CHROME_USER_DATA}")
        print("You may not have Chrome installed or using a custom path.")
        sys.exit(1)

    print(f"Found {len(cookie_paths)} Chrome profile(s):")
    for p in cookie_paths:
        profile = p.parent.parent.name  # Profile 1, Default, etc.
        print(f"  - {profile}: {p}")

    # Get encryption key
    print("\nGetting Chrome encryption key...")
    key = get_chrome_encryption_key()
    print(f"  Key length: {len(key)} bytes")

    all_cookies = {}
    target_keys = set(REQUIRED_COOKIES + OPTIONAL_COOKIES)

    for cookie_path in cookie_paths:
        profile = cookie_path.parent.parent.name
        print(f"\n--- Scanning profile: {profile} ---")

        tmp_path = Path(__file__).parent / f"_cookies_tmp_{profile}.db"
        try:
            shutil.copy2(cookie_path, tmp_path)
        except Exception as e:
            print(f"  Cannot copy: {e}")
            continue

        try:
            conn = sqlite3.connect(str(tmp_path))
            cursor = conn.cursor()
            cursor.execute("""
                SELECT name, encrypted_value, host_key, is_httponly, is_secure
                FROM cookies
                WHERE host_key LIKE '%jobsdb.com%'
                ORDER BY name
            """)
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                print("  No JobsDB cookies in this profile.")
                continue

            print(f"  Found {len(rows)} JobsDB cookies:")

            for name, encrypted_value, host, is_httponly, is_secure in rows:
                value = decrypt_cookie_value(encrypted_value, key)
                httponly_flag = "[H]" if is_httponly else "   "
                secure_flag = "[S]" if is_secure else "   "
                is_target = "*" if name in target_keys else " "

                display_val = value[:50] + "..." if len(value) > 50 else value
                print(f"    {is_target} {httponly_flag}{secure_flag} {name} = {display_val}")

                if name in target_keys and name not in all_cookies:
                    all_cookies[name] = value

        finally:
            if tmp_path.exists():
                tmp_path.unlink()

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
    print("  JobsDB Cookie Auto-Extractor")
    print("  Reads from Chrome local DB (includes HttpOnly)")
    print("=" * 60)
    print()

    cookies = read_chrome_cookies()

    if cookies:
        missing = [k for k in REQUIRED_COOKIES if k not in cookies]
        if missing:
            print(f"\nMissing required cookies: {', '.join(missing)}")
            print("Please login to https://hk.jobsdb.com in Chrome first.")
        else:
            print("\nAll required cookies ready!")

        write_to_config(cookies)
    else:
        print("\nNo target cookies extracted.")
        print("Please login to https://hk.jobsdb.com in Chrome, then re-run.")


if __name__ == "__main__":
    main()
