"""List ALL visible text elements on documents page."""
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

    # Ensure resume=change is selected
    try:
        pg.locator('input[name="resume-method"][value="change"]').first.click()
        time.sleep(2)
    except:
        pass

    print("=== ALL VISIBLE BUTTONS ===")
    for b in pg.locator("button").all():
        try:
            if b.is_visible(timeout=300):
                txt = b.inner_text(timeout=300).replace("\u2060","").strip()
                testid = b.get_attribute("data-testid") or ""
                aria = b.get_attribute("aria-label") or ""
                print(f"  '{txt[:60]}' testid='{testid}' aria='{aria[:40]}'")
        except:
            pass

    print("\n=== ALL VISIBLE LABELS/SPANS ===")
    for el in pg.locator("label, span").all():
        try:
            if el.is_visible(timeout=200):
                txt = el.inner_text(timeout=200).replace("\u2060","").strip()
                if len(txt) > 2:
                    print(f"  '{txt[:80]}'")
        except:
            pass

    print("\n=== ALL VISIBLE RADIO INPUTS ===")
    for inp in pg.locator('input[type="radio"]').all():
        try:
            if inp.is_visible(timeout=200):
                nm = inp.get_attribute("name") or ""
                v = inp.get_attribute("value") or ""
                chk = inp.is_checked(timeout=200)
                print(f"  name='{nm}' val='{v}' checked={chk}")
        except:
            pass

    browser.close()
