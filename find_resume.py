"""Find resume list elements after selecting resume-method=change."""
import sys, time
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
    if not pg: pg = ctx.pages[0]
    pg.bring_to_front()

    # Ensure resume-method=change is selected
    try:
        pg.locator('input[name="resume-method"][value="change"]').first.click()
        time.sleep(2)
    except:
        pass

    print("Page title:", pg.title())

    # Find ALL elements with "resume" in text or attributes
    html = pg.content()

    # Search for resume-related data-automation attributes
    import re
    for m in re.finditer(r'data-automation="([^"]*resume[^"]*)"', html):
        print(f"data-automation: {m.group(1)}")

    print()

    # Search for interesting text patterns
    for line in html.split("\n"):
        if "resume" in line.lower() and len(line) < 500:
            # look for labels, buttons, spans
            if any(tag in line for tag in ["label", "button", "span", "input", "data-"]):
                print(line.strip()[:200])

    browser.close()
