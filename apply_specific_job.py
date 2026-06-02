"""Apply to all jobs listed in a Markdown file via Quick Apply + form filler.

Reads job URLs from a Markdown file, processes them sequentially
(one at a time), with random 0-3s delays between applications.
Already-applied jobs are skipped.  Failed jobs are collected and
written to failed_jobs.md.
"""
import random
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from chrome_cdp import connect_browser_over_cdp, get_cdp_url
from easy_apply_form import extract_urls_from_markdown, fill_apply_form, navigate_and_click_easy_apply
from playwright.sync_api import sync_playwright

# -- Config -------------------------------------------------------------------
MARKDOWN_FILE = Path(__file__).parent / "数据分析岗位.md"
FAILED_OUTPUT = Path(__file__).parent / "failed_jobs.md"

QUICK_APPLY_SELECTORS = [
    '[data-automation="easy-apply-btn"]',
    'a:has-text("Quick Apply")',
    'button:has-text("Quick Apply")',
    'button:has-text("Easy Apply")',
    'a:has-text("Easy Apply")',
]


def click_quick_apply_on_page(page) -> bool:
    """Click the Quick Apply button on the current job detail page."""
    for sel in QUICK_APPLY_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=3000):
                btn.click(force=True)
                print(f"    Clicked: {sel}")
                return True
        except Exception:
            continue

    # Scroll and retry
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        time.sleep(1)
        for sel in QUICK_APPLY_SELECTORS:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2000):
                    btn.click(force=True)
                    print(f"    Clicked (after scroll): {sel}")
                    return True
            except Exception:
                continue
    except Exception:
        pass

    # Check if form is already loaded
    try:
        radio = page.locator('input[name="resume-method"]').first
        if radio.is_visible(timeout=2000):
            print("    Form already loaded")
            return True
    except Exception:
        pass

    return False


# ---------------------------------------------------------------------------
#  Main batch loop
# ---------------------------------------------------------------------------

print("=" * 60)
print("  JobsDB 批量投递 - Markdown URL 模式")
print("=" * 60)

# 1. Extract URLs from the markdown file
jobs = extract_urls_from_markdown(str(MARKDOWN_FILE))
if not jobs:
    print("No job URLs found in the markdown file.")
    sys.exit(1)

print(f"共读取 {len(jobs)} 个职位URL\n")

# 2. Connect to Chrome via CDP
results = []
failed_jobs = []

with sync_playwright() as p:
    browser = connect_browser_over_cdp(p, get_cdp_url())
    ctx = browser.contexts[0]
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.bring_to_front()

    for idx, job in enumerate(jobs, 1):
        job_url = job["job_url"]
        title = job["title"]
        company = job["company"]

        print(f"[{idx}/{len(jobs)}] {title} @ {company}")
        print(f"    URL: {job_url}")

        # --- Step A: Navigate to job page ---
        try:
            page.goto(job_url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as e:
            print(f"    ❌ Navigation error: {e}")
            failed_jobs.append({**job, "reason": f"navigation_error: {e}"})
            results.append({"job": job, "status": "navigation_error"})
            continue

        time.sleep(3)

        # --- Step B: Check if already applied ---
        current_url = page.url.lower()
        if "applied-jobs" in current_url:
            print(f"    ⏭️  Already applied, skipping")
            results.append({"job": job, "status": "already_applied"})
            delay = random.uniform(0, 3)
            print(f"    等待 {delay:.1f}s...")
            time.sleep(delay)
            continue

        if "login" in current_url or "oauth" in current_url:
            print(f"    ⚠️  Login required")
            failed_jobs.append({**job, "reason": "login_required"})
            results.append({"job": job, "status": "login_required"})
            delay = random.uniform(0, 3)
            print(f"    等待 {delay:.1f}s...")
            time.sleep(delay)
            continue

        # --- Step C: Click Quick Apply + detect new tab ---
        clicked = click_quick_apply_on_page(page)

        if clicked:
            # Wait for redirect or new tab
            time.sleep(4)
            # Check if apply form opened in current page
            if "/apply" in page.url.lower():
                print(f"    Apply form opened in same tab: {page.url[:80]}")
            else:
                # Check for new tab opened
                new_page = None
                for pg in ctx.pages:
                    try:
                        if "/apply" in pg.url.lower():
                            new_page = pg
                            break
                    except Exception:
                        continue
                if new_page:
                    new_page.bring_to_front()
                    print(f"    Apply form opened in new tab: {new_page.url[:80]}")
                    page = new_page  # Switch to the apply form tab!
                else:
                    print(f"    Quick Apply clicked but no /apply URL found. Trying fallback...")
                    apply_url = navigate_and_click_easy_apply(page, job_url)
                    if not apply_url:
                        print(f"    ❌ No apply form detected")
                        failed_jobs.append({**job, "reason": "no_apply_form"})
                        results.append({"job": job, "status": "no_apply_form"})
                        delay = random.uniform(0, 3)
                        print(f"    等待 {delay:.1f}s...")
                        time.sleep(delay)
                        continue
        else:
            # Try navigate_and_click_easy_apply as fallback
            apply_url = navigate_and_click_easy_apply(page, job_url)
            if not apply_url:
                print(f"    ❌ No Quick Apply button found")
                failed_jobs.append({**job, "reason": "no_quick_apply"})
                results.append({"job": job, "status": "no_quick_apply"})
                delay = random.uniform(0, 3)
                print(f"    等待 {delay:.1f}s...")
                time.sleep(delay)
                continue

        current_url = page.url.lower()

        # Re-check for login / already-applied after click
        if "login" in current_url or "oauth" in current_url:
            print(f"    ⚠️  Login required (after click)")
            failed_jobs.append({**job, "reason": "login_required"})
            results.append({"job": job, "status": "login_required"})
            delay = random.uniform(0, 3)
            print(f"    等待 {delay:.1f}s...")
            time.sleep(delay)
            continue

        if "applied-jobs" in current_url:
            print(f"    ⏭️  Already applied (after click)")
            results.append({"job": job, "status": "already_applied"})
            delay = random.uniform(0, 3)
            print(f"    等待 {delay:.1f}s...")
            time.sleep(delay)
            continue

        # --- Step D: Run form filler ---
        print(f"    Running form filler...")
        try:
            fill_result = fill_apply_form(page)
        except Exception as e:
            print(f"    ❌ Form filler error: {e}")
            failed_jobs.append({**job, "reason": f"form_error: {e}"})
            results.append({"job": job, "status": "form_error", "error": str(e)})
            delay = random.uniform(0, 3)
            print(f"    等待 {delay:.1f}s...")
            time.sleep(delay)
            continue

        status = fill_result.get("status", "unknown")
        submitted = fill_result.get("submitted", False)
        steps = fill_result.get("steps_completed", [])
        failed_steps = fill_result.get("steps_failed", [])

        if submitted or status == "submitted":
            print(f"    ✅ Submitted! Steps: {steps}")
            results.append({"job": job, "status": "submitted", "steps": steps})
        elif status == "partial":
            print(f"    ⚠️  Partial: steps={steps}, failed={failed_steps}")
            failed_jobs.append({**job, "reason": f"partial: failed_steps={failed_steps}"})
            results.append({"job": job, "status": "partial", "steps": steps, "failed_steps": failed_steps})
        else:
            print(f"    ❌ Failed: status={status}, steps={steps}")
            failed_jobs.append({**job, "reason": f"status={status}, steps={steps}"})
            results.append({"job": job, "status": "failed", "steps": steps})

        # --- Random delay between applications ---
        delay = random.uniform(0, 3)
        print(f"    等待 {delay:.1f}s...")
        time.sleep(delay)

    browser.close()

# 3. Summary
print(f"\n{'=' * 60}")
print(f"  汇总报告")
print(f"{'=' * 60}")

status_counts: dict[str, int] = {}
for r in results:
    s = r.get("status", "unknown")
    status_counts[s] = status_counts.get(s, 0) + 1

submitted = status_counts.get("submitted", 0)
already = status_counts.get("already_applied", 0)
partial = status_counts.get("partial", 0)
failed = status_counts.get("failed", 0) + status_counts.get("no_quick_apply", 0) + \
         status_counts.get("form_error", 0) + status_counts.get("navigation_error", 0)
login = status_counts.get("login_required", 0)

print(f"  ✅ 提交成功: {submitted}")
print(f"  ⏭️ 已投递(跳过): {already}")
print(f"  ⚠️ 部分完成: {partial}")
print(f"  ❌ 失败: {failed}")
print(f"  🔒 需登录: {login}")

# 4. Write failed_jobs.md if any failures
if failed_jobs:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# 投递失败职位清单",
        f"",
        f"生成时间: {timestamp}",
        f"",
        f"| # | 职位名称 | 公司 | URL | 失败原因 |",
        f"|---|----------|------|-----|----------|",
    ]
    for i, fj in enumerate(failed_jobs, 1):
        title = fj.get("title", "unknown")
        company = fj.get("company", "N/A")
        url = fj.get("job_url", "")
        reason = fj.get("reason", "unknown")
        lines.append(f"| {i} | {title} | {company} | {url} | {reason} |")

    FAILED_OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n  📄 失败清单已保存: {FAILED_OUTPUT}")
else:
    print(f"\n  🎉 全部成功，无失败记录！")

print(f"\n{'=' * 60}")
print(f"  完成")
print(f"{'=' * 60}")
