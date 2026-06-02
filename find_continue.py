"""Find the Continue button and stepper state."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from chrome_cdp import connect_browser_over_cdp, get_cdp_url
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = connect_browser_over_cdp(p, get_cdp_url())
    ctx = browser.contexts[0]
    pg = None
    for p_ in ctx.pages:
        if "/apply" in p_.url:
            pg = p_
            break
    if not pg:
        pg = ctx.pages[0]
    pg.bring_to_front()

    html = pg.content()

    # Search for "Continue" in HTML (case-insensitive)
    import re
    for m in re.finditer(r'(?i)continue', html):
        start = max(0, m.start()-200)
        end = min(len(html), m.end()+200)
        snippet = html[start:end]
        print(f"--- Found 'Continue' at pos {m.start()} ---")
        print(snippet)
        print()

    # Also search for "submit"  
    for m in re.finditer(r'(?i)submit', html):
        start = max(0, m.start()-200)
        end = min(len(html), m.end()+200)
        snippet = html[start:end]
        print(f"--- Found 'Submit' at pos {m.start()} ---")
        print(snippet[:400])
        print()

    # Find all form elements / submit buttons at page bottom
    for m in re.finditer(r'<button[^>]*type="submit"[^>]*>.*?</button>', html, re.DOTALL):
        print(f"--- Submit button ---")
        print(m.group()[:300])
        print()

    browser.close()
