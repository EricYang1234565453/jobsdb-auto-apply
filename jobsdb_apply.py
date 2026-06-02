"""
JobsDB Search + Apply CLI.

Uses CDP (Chrome DevTools Protocol) to attach to a running Chrome session
for both job searching and applying.

Apply flow:
  1. Search API called via page.evaluate(fetch) – carries auth cookies.
  2. Navigate to the **job detail page** (not /apply).
  3. Click the Easy Apply button on that page.
  4. Wait for redirect to the real apply form URL (contains ?sol=…).
  5. Return that final URL so easy_apply_form_v2.py can fill Pages 1-4.
"""

import argparse
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

from chrome_cdp import check_cdp_available, connect_browser_over_cdp, get_cdp_url, get_first_context, get_first_page


BASE_URL = "https://hk.jobsdb.com"
SEARCH_API = f"{BASE_URL}/api/jobsearch/v5/search"
JOB_URL_TEMPLATE = f"{BASE_URL}/job/{{job_id}}"

# Selectors for the Easy Apply button on the job detail page
EASY_APPLY_SELECTORS = [
    '[data-automation="easy-apply-btn"]',
    'button:has-text("Easy Apply")',
    'button:has-text("Quick Apply")',
    '[data-testid*="easy-apply" i]',
    '[data-testid*="quick-apply" i]',
]


# ---------------------------------------------------------------------------
# CDP helpers
# ---------------------------------------------------------------------------

def _ensure_cdp(playwright):
    """Connect to the running Chrome via CDP and return (browser, context, page)."""
    cdp_url = get_cdp_url()
    ok, info = check_cdp_available(cdp_url)
    if not ok:
        raise RuntimeError(
            f"Chrome CDP is not available at {cdp_url}. "
            "Start Chrome with --remote-debugging-port=9222."
        )
    browser_name = info.get("Browser", "unknown")
    print(f"[CDP] Connected to {browser_name}")
    browser = connect_browser_over_cdp(playwright, cdp_url)
    ctx = get_first_context(browser)
    page = get_first_page(ctx)
    return browser, ctx, page


# ---------------------------------------------------------------------------
# Search via CDP (fetch inside the browser – carries auth cookies)
# ---------------------------------------------------------------------------

def search_jobs_cdp(page, keywords: str, where: str = "Hong Kong",
                     page_num: int = 1, page_size: int = 20,
                     days: int = None, sortby: str = "date",
                     classification: str = None) -> dict:
    """Call the JobsDB search API through the browser so cookies are sent.

    Args:
        days: Filter jobs posted within the last N days (maps to postedwithin).
        sortby: Sort mode - 'date' (ListedDate) or 'relevance' (KeywordRelevance).
        classification: Job classification ID (e.g. '6281' for IT).
    """
    import urllib.request as _urllib_req  # only for URL quoting
    params = [
        f"keywords={_urllib_req.quote(keywords)}",
        f"where={_urllib_req.quote(where)}",
        f"page={page_num}",
        f"pageSize={page_size}",
        f"sortby={sortby}",
    ]
    if days is not None:
        params.append(f"postedwithin={days}")
    if classification:
        params.append(f"classification={classification}")
    url = f"{SEARCH_API}?{'&'.join(params)}"

    js = """
    async ([fetchUrl]) => {
        try {
            const resp = await fetch(fetchUrl, {
                headers: { 'Accept': 'application/json' },
                credentials: 'include'
            });
            if (!resp.ok) return { error: 'HTTP ' + resp.status };
            return await resp.json();
        } catch (e) {
            return { error: e.message };
        }
    }
    """
    result = page.evaluate(js, [url])
    if isinstance(result, dict) and "error" in result:
        print(f"Search API error: {result['error']}")
        return {"data": [], "totalCount": 0}
    return result or {"data": [], "totalCount": 0}


def format_job_row(job: dict) -> dict:
    salary = job.get("salaryLabel", "")
    location = job.get("locations", [{}])[0].get("label", "") if job.get("locations") else ""
    listing_date = job.get("listingDate", "") or ""  # Relative date like "13d ago"
    listing_date_iso = job.get("listingDateISO", "") or ""  # ISO timestamp

    return {
        "id": job["id"],
        "roleId": job.get("roleId", ""),
        "title": job["title"],
        "company": job.get("companyName", ""),
        "salary": salary,
        "location": location,
        "listingDate": listing_date,
        "listingDateISO": listing_date_iso,
        "workTypes": job.get("workTypes", []),
        "bulletPoints": job.get("bulletPoints", []),
        "classifications": job.get("classifications", []),
        "jobUrl": JOB_URL_TEMPLATE.format(job_id=job["id"]),
    }


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def is_within_days(listing_date_iso: str, max_days: int) -> bool:
    """Check if a job was posted within the specified number of days."""
    if not listing_date_iso:
        return True  # If no date info, keep the job

    try:
        # Parse ISO format date
        post_date = datetime.fromisoformat(listing_date_iso.replace('Z', '+00:00'))
        post_date = post_date.replace(tzinfo=None)

        # Calculate time difference
        now = datetime.now()
        delta = now - post_date

        return delta.days <= max_days
    except Exception:
        return True


def is_internship(title: str, work_types: list = None) -> bool:
    """Check if a job is an internship position."""
    internship_keywords = ['实习生', '实习', 'Intern', 'Internship', 'Trainee', '实习岗位']

    # Check title
    for keyword in internship_keywords:
        if keyword.lower() in title.lower():
            return True

    # Check work types (if available)
    if work_types:
        for wt in work_types:
            wt_str = wt.get("label", "") if isinstance(wt, dict) else str(wt)
            if 'intern' in wt_str.lower() or 'trainee' in wt_str.lower():
                return True

    return False


def search_and_format(keywords: str, where: str = "Hong Kong",
                       pages: int = 1, page_size: int = 20,
                       days: int = None, sortby: str = "date",
                       classification: str = None,
                       max_days: int = None, exclude_internship: bool = False) -> list:
    """Connect via CDP, search JobsDB, and return formatted job rows.

    Args:
        max_days: Filter jobs posted within the last N days (applied in code, not API).
        exclude_internship: If True, exclude internship positions.
    """
    all_jobs: list[dict] = []
    stats = {"total": 0, "date_filtered": 0, "internship_filtered": 0}

    with sync_playwright() as pw:
        browser, ctx, pg = _ensure_cdp(pw)
        # Make sure the page is on a jobsdb origin so fetch credentials work
        if "jobsdb.com" not in pg.url:
            pg.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=20_000)
            time.sleep(1)

        for page_num in range(1, pages + 1):
            filter_info = ""
            if days:
                filter_info += f", days={days}"
            if classification:
                filter_info += f", classification={classification}"
            if max_days:
                filter_info += f", max_days={max_days}"
            if exclude_internship:
                filter_info += f", exclude_internship=True"

            print(f"Searching page {page_num}: keywords='{keywords}', where='{where}'{filter_info}")
            data = search_jobs_cdp(pg, keywords, where, page_num, page_size,
                                   days=days, sortby=sortby, classification=classification)
            jobs = data.get("data", [])

            for job in jobs:
                stats["total"] += 1
                formatted = format_job_row(job)

                # Apply date filter
                if max_days is not None:
                    if not is_within_days(formatted["listingDateISO"], max_days):
                        stats["date_filtered"] += 1
                        continue

                # Apply internship filter
                if exclude_internship:
                    if is_internship(formatted["title"], formatted.get("workTypes")):
                        stats["internship_filtered"] += 1
                        continue

                all_jobs.append(formatted)

            print(f"  page results: {len(jobs)} | total so far: {len(all_jobs)}")
            if page_num < pages:
                time.sleep(0.5)

        browser.close()

    # Print statistics
    print(f"\n{'=' * 60}")
    print(f"Search Statistics:")
    print(f"  Total jobs found: {stats['total']}")
    print(f"  After filtering: {len(all_jobs)}")
    if max_days:
        print(f"  Filtered by date (> {max_days} days): {stats['date_filtered']}")
    if exclude_internship:
        print(f"  Filtered as internship: {stats['internship_filtered']}")
    print(f"{'=' * 60}\n")

    return all_jobs


# ---------------------------------------------------------------------------
# Apply: job detail page → click Easy Apply → get real apply form URL
# ---------------------------------------------------------------------------

def _click_easy_apply(page, timeout_ms: int = 5000) -> bool:
    """Find and click the Easy Apply button on the job detail page."""
    for sel in EASY_APPLY_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=timeout_ms):
                btn.click()
                print(f"  [Easy Apply] Clicked: {sel}")
                return True
        except Exception:
            continue
    return False


def apply_via_playwright(job_id: str, role_id: str = "", headless: bool = True) -> dict:
    """Navigate to the job detail page, click Easy Apply, and capture the
    redirected apply-form URL (with ?sol=… token).

    Returns a result dict containing:
      - job_url: the job detail page URL
      - apply_url: the real apply form URL after clicking Easy Apply (may be empty)
      - status: one of login_required / already_applied / easy_apply_clicked /
                no_easy_apply_button / navigation_error
    """
    job_url = JOB_URL_TEMPLATE.format(job_id=job_id)

    result = {
        "job_id": job_id,
        "role_id": role_id,
        "job_url": job_url,
        "apply_url": "",
        "status": "unknown",
        "final_url": "",
        "page_title": "",
        "error": None,
        "mode": "cdp",
    }

    try:
        with sync_playwright() as p:
            browser, context, _ = _ensure_cdp(p)
            page = get_first_page(context)

            # --- Navigate to the job detail page --------------------------------
            try:
                response = page.goto(job_url, wait_until="domcontentloaded", timeout=30_000)
                result["status"] = f"HTTP {response.status}" if response else "no response"
            except Exception as exc:
                result["status"] = "navigation_error"
                result["error"] = str(exc)
                browser.close()
                return result

            time.sleep(3)
            final_url = page.url
            result["final_url"] = final_url
            result["page_title"] = page.title()

            # --- Login check ----------------------------------------------------
            if "oauth/login" in final_url or "login.seek.com" in final_url:
                result["status"] = "login_required"
                browser.close()
                return result

            # --- Already applied? ------------------------------------------------
            if "my-activity/applied-jobs" in final_url:
                result["status"] = "already_applied"
                browser.close()
                return result

            # --- Click Easy Apply ------------------------------------------------
            clicked = _click_easy_apply(page)

            if not clicked:
                result["status"] = "no_easy_apply_button"
                browser.close()
                return result

            # --- Wait for redirect to the apply form -----------------------------
            time.sleep(4)
            apply_url = page.url
            result["apply_url"] = apply_url
            result["final_url"] = apply_url
            result["page_title"] = page.title()

            if "/apply" in apply_url:
                result["status"] = "easy_apply_clicked"
            else:
                # Might have opened a new tab – check for it
                all_pages = context.pages
                for pg in all_pages:
                    try:
                        if "/apply" in pg.url:
                            result["apply_url"] = pg.url
                            result["final_url"] = pg.url
                            result["status"] = "easy_apply_clicked"
                            break
                    except Exception:
                        continue

                if result["status"] != "easy_apply_clicked":
                    result["status"] = "easy_apply_clicked_but_no_apply_url"

            browser.close()

    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"

    return result


def print_apply_result(result: dict):
    print(f"\n{'=' * 60}")
    print(f"Job: {result['job_id']} ({result['role_id']})")
    print(f"Mode: {result['mode']}")
    print(f"Job URL: {result['job_url']}")
    print(f"Status: {result['status']}")

    if result.get("error"):
        print(f"Error: {result['error']}")
    elif result["status"] == "login_required":
        print("Session is not logged in inside the real Chrome CDP profile.")
    elif result["status"] == "already_applied":
        print("Already applied to this job.")
    elif result["status"] == "easy_apply_clicked":
        print("Easy Apply clicked successfully!")
        print(f"  Apply form URL: {result['apply_url']}")
        print(f"  Page title: {result['page_title']}")
        print(f"  -> Run easy_apply_form_v2.py --url \"{result['apply_url']}\" to fill Pages 1-4")
    elif result["status"] == "no_easy_apply_button":
        print("No Easy Apply button found on the job detail page.")
        print(f"  Final URL: {result['final_url']}")
    elif result["status"] == "navigation_error":
        print(f"Navigation error: {result.get('error', 'unknown')}")
    else:
        print(f"  Final URL: {result['final_url']}")
        if result.get("apply_url"):
            print(f"  Apply form URL: {result['apply_url']}")


def print_jobs_table(jobs: list):
    if not jobs:
        print("No jobs found.")
        return

    print(f"\n{'#':<3} {'Date':<10} {'Title':<35} {'Company':<30} {'Salary':<20} {'Location':<20}")
    print("-" * 120)
    for index, job in enumerate(jobs, 1):
        date = job.get("listingDate", "")[:10]  # truncate to ISO date part
        title = job["title"][:33] + ".." if len(job["title"]) > 35 else job["title"]
        company = job["company"][:28] + ".." if len(job["company"]) > 30 else job["company"]
        salary = (job["salary"] or "N/A")[:18]
        location = (job["location"] or "N/A")[:18] + ".." if len(job.get("location", "") or "") > 20 else (job.get("location", "") or "N/A")
        print(f"{index:<3} {date:<10} {title:<35} {company:<30} {salary:<20} {location:<20}")
    print(f"\nTotal {len(jobs)} jobs")

    # Print URLs after the table
    print(f"\n{'=' * 80}")
    print("Job URLs:")
    for index, job in enumerate(jobs, 1):
        print(f"  [{index}] {job['title'][:50]}")
        print(f"      {job['jobUrl']}")


def export_markdown_table(jobs: list, output_path: str = None) -> str:
    """Generate a Markdown table from job listings.

    Args:
        jobs: List of job dictionaries.
        output_path: If provided, save the Markdown table to this file.

    Returns:
        The Markdown table as a string.
    """
    if not jobs:
        markdown = "No jobs found.\n"
        if output_path:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(markdown)
        return markdown

    # Table header
    markdown = "| 序号 | 发布日期 | 职位名称 | 公司名称 | 薪资 | 地点 | 链接 |\n"
    markdown += "|------|----------|----------|----------|------|------|------|\n"

    for index, job in enumerate(jobs, 1):
        # Extract fields
        date = job.get('listingDate', '')[:10]  # Truncate to ISO date part
        title = job['title']
        company = job.get('company', 'N/A')
        salary = job.get('salary', 'N/A') or 'N/A'
        location = job.get('location', 'N/A') or 'N/A'
        url = job.get('jobUrl', '')

        # Escape Markdown special characters
        title_escaped = title.replace('|', '\\|')
        company_escaped = company.replace('|', '\\|')

        # Create clickable link
        link = f"[查看详情]({url})"

        # Add row to table
        markdown += f"| {index} | {date} | {title_escaped} | {company_escaped} | {salary} | {location} | {link} |\n"

    # Add summary
    markdown += f"\nTotal {len(jobs)} jobs\n"

    # Output to file if specified
    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(markdown)
        print(f"Markdown table saved to: {output_path}")

    return markdown


def cmd_search(args):
    jobs = search_and_format(args.keywords, args.where, args.pages, args.page_size,
                             days=args.days, sortby=args.sortby,
                             classification=args.classification,
                             max_days=getattr(args, "max_days", None),
                             exclude_internship=getattr(args, "exclude_internship", False))
    print_jobs_table(jobs)
    output_path = Path(__file__).parent / f"search_{args.keywords.replace(' ', '_')}.json"
    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(jobs, file, ensure_ascii=False, indent=2)
    print(f"\nSaved results to {output_path}")


def cmd_search_markdown(args):
    """Search jobs and export results as Markdown table."""
    jobs = search_and_format(
        args.keywords,
        args.where,
        args.pages,
        args.page_size,
        days=args.days,
        sortby=args.sortby,
        classification=args.classification,
        max_days=args.max_days,
        exclude_internship=args.exclude_internship
    )

    # Generate Markdown table
    markdown = export_markdown_table(jobs, args.output)

    # Print to console if no output file specified
    if not args.output:
        print("\n" + "="*80)
        print(markdown)


def cmd_apply(args):
    result = apply_via_playwright(args.job_id, getattr(args, "role_id", ""), headless=not args.visible)
    print_apply_result(result)


def cmd_apply_all(args):
    jobs = search_and_format(args.keywords, args.where, 1, args.max,
                             days=args.days, sortby=args.sortby,
                             classification=args.classification)
    print_jobs_table(jobs)

    n = min(args.max, len(jobs))
    print(f"\n{'=' * 60}")
    print(f"Opening apply pages for the first {n} jobs...")
    print(f"{'=' * 60}")

    results = {"success": 0, "already_applied": 0, "login_required": 0, "no_button": 0, "error": 0}
    for index, job in enumerate(jobs[: args.max], 1):
        print(f"\n[{index}/{n}] {job['title']} @ {job['company']}")
        result = apply_via_playwright(
            job["id"],
            job["roleId"],
            headless=not args.visible,
        )
        print_apply_result(result)

        if result["status"] == "easy_apply_clicked":
            results["success"] += 1
        elif result["status"] == "already_applied":
            results["already_applied"] += 1
        elif result["status"] == "login_required":
            results["login_required"] += 1
        elif result["status"] == "no_easy_apply_button":
            results["no_button"] += 1
        else:
            results["error"] += 1
        time.sleep(2)

    print(f"\n{'=' * 60}")
    print(
        f"Results: clicked {results['success']} | no button {results['no_button']} | "
        f"already applied {results['already_applied']} | "
        f"login required {results['login_required']} | error {results['error']}"
    )


def main():
    parser = argparse.ArgumentParser(description="JobsDB Search + Apply CLI")
    subparsers = parser.add_subparsers(dest="command", help="subcommands")

    p_search = subparsers.add_parser("search", help="search jobs")
    p_search.add_argument("--keywords", required=True, help="search keywords")
    p_search.add_argument("--where", default="Hong Kong", help="location")
    p_search.add_argument("--pages", type=int, default=1, help="number of pages")
    p_search.add_argument("--page-size", type=int, default=20, help="results per page")
    p_search.add_argument("--days", type=int, default=None,
                          help="filter jobs posted within last N days (uses postedwithin)")
    p_search.add_argument("--sortby", default="date", choices=["date", "relevance"],
                          help="sort mode (default: date)")
    p_search.add_argument("--classification", default=None,
                          help="job classification ID (e.g. 6281 for IT)")

    p_apply = subparsers.add_parser("apply", help="open one apply URL")
    p_apply.add_argument("--job-id", required=True, help="job id")
    p_apply.add_argument("--role-id", default="", help="role id / slug (optional)")
    p_apply.add_argument("--visible", action="store_true", help="show browser window")

    p_all = subparsers.add_parser("apply-all", help="search and open multiple apply URLs")
    p_all.add_argument("--keywords", required=True, help="search keywords")
    p_all.add_argument("--where", default="Hong Kong", help="location")
    p_all.add_argument("--max", type=int, default=5, help="max jobs to open")
    p_all.add_argument("--days", type=int, default=None,
                       help="filter jobs posted within last N days (uses postedwithin)")
    p_all.add_argument("--sortby", default="date", choices=["date", "relevance"],
                       help="sort mode (default: date)")
    p_all.add_argument("--classification", default=None,
                       help="job classification ID (e.g. 6281 for IT)")
    p_all.add_argument("--visible", action="store_true", help="show browser window")

    p_search_markdown = subparsers.add_parser("search-markdown", help="search jobs and export as Markdown table")
    p_search_markdown.add_argument("--keywords", required=True, help="search keywords")
    p_search_markdown.add_argument("--where", default="Hong Kong", help="location")
    p_search_markdown.add_argument("--pages", type=int, default=1, help="number of pages")
    p_search_markdown.add_argument("--page-size", type=int, default=50, help="results per page")
    p_search_markdown.add_argument("--days", type=int, default=None,
                          help="filter jobs posted within last N days (uses postedwithin)")
    p_search_markdown.add_argument("--max-days", type=int, default=20,
                          help="filter jobs posted within last N days (applied in code, default: 20)")
    p_search_markdown.add_argument("--exclude-internship", action="store_true", default=True,
                          help="exclude internship positions (default: True)")
    p_search_markdown.add_argument("--no-exclude-internship", action="store_false", dest="exclude_internship",
                          help="do not exclude internship positions")
    p_search_markdown.add_argument("--output", type=str, default=None,
                          help="output file path for Markdown table (print to console if not specified)")
    p_search_markdown.add_argument("--sortby", default="date", choices=["date", "relevance"],
                          help="sort mode (default: date)")
    p_search_markdown.add_argument("--classification", default=None,
                          help="job classification ID (e.g. 6281 for IT)")

    args = parser.parse_args()
    if args.command == "search":
        cmd_search(args)
    elif args.command == "apply":
        cmd_apply(args)
    elif args.command == "apply-all":
        cmd_apply_all(args)
    elif args.command == "search-markdown":
        cmd_search_markdown(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
