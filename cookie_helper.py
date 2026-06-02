"""
Cookie 解析工具
===============
從瀏覽器 document.cookie 字符串或手動輸入解析 cookies 並寫入 config.ini

Usage:
  python cookie_helper.py "JobseekerSessionId=abc123; __cf_bm=xyz789; _cfuvid=aaa"
  python cookie_helper.py --interactive
"""

import configparser
import sys
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.ini"

REQUIRED_COOKIES = ["JobseekerSessionId", "__cf_bm"]
OPTIONAL_COOKIES = ["_cfuvid", "JobseekerVisitorId"]


def parse_cookie_string(cookie_str: str) -> dict:
    """解析 document.cookie 格式的字符串"""
    cookies = {}
    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if "=" in pair:
            key, _, value = pair.partition("=")
            key = key.strip()
            value = value.strip()
            if key in REQUIRED_COOKIES + OPTIONAL_COOKIES:
                cookies[key] = value
    return cookies


def write_cookies(cookies: dict, config_path: Path = CONFIG_PATH):
    """將 cookies 寫入 config.ini"""
    cfg = configparser.ConfigParser()
    if config_path.exists():
        cfg.read(config_path, encoding="utf-8")

    if "cookies" not in cfg:
        cfg.add_section("cookies")

    for key in REQUIRED_COOKIES + OPTIONAL_COOKIES:
        if key in cookies:
            cfg["cookies"][key] = cookies[key]

    with open(config_path, "w", encoding="utf-8") as f:
        cfg.write(f)

    print(f"✅ 已寫入 {len(cookies)} 個 cookies 到 {config_path}")

    # 驗證
    missing = [k for k in REQUIRED_COOKIES if k not in cookies]
    if missing:
        print(f"⚠️ 缺少必填 cookies: {', '.join(missing)}")
        print("   apply 頁面可能無法正常訪問")
    else:
        print("✅ 所有必填 cookies 已就緒")


def interactive_input():
    """交互式逐個輸入 cookies"""
    cookies = {}
    print("請輸入 JobsDB Cookies（直接回車跳過可選項）：")
    print()

    for key in REQUIRED_COOKIES:
        value = input(f"  {key} (必填): ").strip()
        if value:
            cookies[key] = value
        else:
            print(f"    ⚠️ {key} 為必填項，未提供")

    for key in OPTIONAL_COOKIES:
        value = input(f"  {key} (可選): ").strip()
        if value:
            cookies[key] = value

    return cookies


def main():
    if len(sys.argv) > 1:
        cookie_str = " ".join(sys.argv[1:])
        if cookie_str == "--interactive":
            cookies = interactive_input()
        else:
            cookies = parse_cookie_string(cookie_str)
            print(f"解析到 {len(cookies)} 個已知 cookies:")
            for k, v in cookies.items():
                print(f"  {k} = {v[:10]}..." if len(v) > 10 else f"  {k} = {v}")
    else:
        cookies = interactive_input()

    if cookies:
        write_cookies(cookies)
    else:
        print("❌ 沒有提供任何 cookies")


if __name__ == "__main__":
    main()
