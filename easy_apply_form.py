"""
JobsDB Easy Apply form filler -- CDP mode (attaches to real Chrome).

Multi-step apply form automation:
  Page 1: Choose documents (resume + cover letter)
  Page 2: Answer employer screening questions (radio + select dropdowns)
  Page 3: Update JobsDB profile (skip)
  Page 4: Submit application

Workflow:
  1. Navigate to the **job detail page** (not /apply).
  2. Click the Easy Apply button to open the real apply form URL (with ?sol=…).
  3. Fill Pages 1-4.

  Alternatively, pass --url with a direct apply form URL (e.g. obtained from
  jobsdb_apply.py) to skip the click step.

Usage:
  python easy_apply_form_v2.py --job-id 92108788 --role-id data-analyst
  python easy_apply_form_v2.py --keywords "data analyst" --max 5
  python easy_apply_form_v2.py --url "https://hk.jobsdb.com/job/92108788/apply?sol=abc123"
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

from chrome_cdp import check_cdp_available, connect_browser_over_cdp, get_cdp_url

# -- Easy Apply button selectors on the job detail page -----------------------
EASY_APPLY_SELECTORS = [
    '[data-automation="job-detail-apply"]',   # JobsDB "Quick apply" <a> link
    '[data-automation="easy-apply-btn"]',
    'a:has-text("Quick apply")',
    'a:has-text("Quick Apply")',
    'a:has-text("Easy Apply")',
    'button:has-text("Easy Apply")',
    'button:has-text("Quick Apply")',
    'button:has-text("Quick apply")',
    '[data-testid*="easy-apply" i]',
    '[data-testid*="quick-apply" i]',
]

# -- Predefined screening-question answers -----------------------------------
SCREENING_ANSWERS: dict[str, str | list[str]] = {
    # Experience as receptionist / guest liaison
    "How many years' experience": "More than 5 years",
    "How many years' experience do you have as": "More than 5 years",
    "How many years": "More than 5 years",
    # Right to work in Hong Kong
    "right to work in hong kong": "I have a temporary visa (eg. Qmas, ttps, iang)",
    "right to work": "I have a temporary visa (eg. Qmas, ttps, iang)",
    "work in hong kong": "I have a temporary visa (eg. Qmas, ttps, iang)",
    # Expected monthly salary
    "expected monthly basic salary": "$25K",
    "expected monthly salary": "$25K",
    "monthly basic salary": "$25K",
    # Notice period
    "notice are you required": "1 week",
    "notice period": "1 week",
    "how much notice": "1 week",
    # Microsoft Office
    "microsoft office": ["Excel", "SQL", "Python"],
    "microsoft office products": ["Excel", "SQL", "Python"],
    "office products": ["Excel", "SQL", "Python"],
    "tools": ["Excel", "SQL", "Python"],
    "data analytics tools": ["Excel", "SQL", "Python"],
    # Languages
    "languages are you fluent": ["Mandarin", "English"],
    "languages fluent": ["Mandarin", "English"],
    "fluent in": ["Mandarin", "English"],
    # Education level (default: Bachelor)
    "highest level of education": "Bachelor degree or equivalent (HKQF level 5)",
    "level of education": "Bachelor degree or equivalent (HKQF level 5)",
    "education": "Bachelor degree or equivalent (HKQF level 5)",
     # yes to all
     "Are you willing": "yes",
     "Are you available to work": "yes",

     "How would you rate your": ["Writes proficiently in a professional setting","Speaks proficiently in a professional setting"],
     "Which of the following programming languages": ["Python"],
     "programming languages": ["Python"],
     "languages are you experienced": ["Python"],
    
}

# -- Constants ----------------------------------------------------------------
BASE_URL = "https://hk.jobsdb.com"
JOB_URL_TEMPLATE = f"{BASE_URL}/hk/en/job/{{job_id}}-{{role_id}}"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)


def _ensure_playwright():
    """Lazy import with auto-install fallback."""
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


# ---------------------------------------------------------------------------
#  Sanitised text helper (avoids GBK encoding crashes from invisibles)
# ---------------------------------------------------------------------------

def _safe_text(el, timeout_ms: int = 1000) -> str:
    """Return element inner_text, stripping non-ASCII word-joiners that crash GBK."""
    try:
        raw = el.inner_text(timeout=timeout_ms)
    except Exception:
        return ""
    return raw.replace("\u2060", "").replace("\u200b", "").replace("\ufeff", "")


def _safe_body_text(page) -> str:
    """Return page body text with encoding-safe characters only."""
    try:
        raw = page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""
    return raw.replace("\u2060", "").replace("\u200b", "").replace("\ufeff", "")


# ---------------------------------------------------------------------------
#  Continue button
# ---------------------------------------------------------------------------

CONTINUE_SELECTORS = [
    '[data-testid="continue-button"]',
    'button:has-text("Continue")',
    'button:has-text("Submit application")',
    'button:has-text("Submit")',
    'button[type="submit"]',
]


def _click_continue(page, stage_name: str) -> bool:
    """Click the Continue / Submit button at the bottom of the form."""
    for sel in CONTINUE_SELECTORS:
        btn = page.locator(sel).first
        try:
            if btn.is_visible(timeout=2000):
                btn.click()
                print(f"    [{stage_name}] Clicked: {sel}")
                time.sleep(2)
                return True
        except Exception:
            continue

    print(f"    [{stage_name}] No continue button found")
    return False


# ---------------------------------------------------------------------------
#  Page-type detection
# ---------------------------------------------------------------------------

def _detect_page_type(page) -> str:
    """Detect which step of the multi-page apply form we are on."""
    try:
        url = page.url.lower()
    except Exception:
        url = ""
    try:
        title = page.title().lower()
    except Exception:
        title = ""

    # URL-based (most reliable)
    if "/role-requirements" in url or "/questions" in url:
        return "screening"
    if "/review" in url:
        return "submit"

    # Title-based
    if "choose documents" in title:
        return "documents"
    if "answer employer questions" in title:
        return "screening"
    if "update jobsdb profile" in title:
        return "profile"
    if "review and submit" in title:
        return "submit"

    # Check for step indicator elements (JobsDB multi-step form)
    step_info = page.evaluate("""() => {
        const steps = document.querySelectorAll('[class*="step"], [data-testid*="step"]');
        const activeStep = document.querySelector('[class*="active"], [aria-current="step"]');
        const activeText = activeStep ? activeStep.textContent.trim().toLowerCase() : '';
        return {stepCount: steps.length, activeText: activeText.substring(0, 50)};
    }""")
    if step_info['activeText']:
        at = step_info['activeText']
        if "document" in at or "resume" in at:
            return "documents"
        if "question" in at or "employer" in at:
            return "screening"
        if "profile" in at:
            return "profile"
        if "review" in at or "submit" in at:
            return "submit"

    # Body-text fallback
    body = _safe_body_text(page).lower()

    # Documents page: look for resume upload or "choose documents" in body
    if "resume" in body and ("cover letter" in body or "choose" in body or "upload" in body):
        return "documents"
    if "choose documents" in body and ("upload" in body or "resume" in body):
        return "documents"

    if any(kw in body for kw in ["screening question", "employer question",
           "years' experience", "right to work", "expected monthly",
           "notice are you required", "receptionist"]):
        return "screening"

    if any(kw in body for kw in ["update your profile", "add your work experience",
           "add your education", "skills and expertise"]):
        return "profile"

    if any(kw in body for kw in ["review your application", "submit your application",
           "by submitting"]):
        return "submit"

    # Last resort: check if there's a Continue button + form elements
    has_continue = False
    for sel in CONTINUE_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=500):
                has_continue = True
                break
        except Exception:
            continue
    
    if has_continue:
        # Has Continue button but couldn't detect page type
        # Check form elements to guess
        form_counts = page.evaluate("""() => {
            return {
                selects: document.querySelectorAll('select').length,
                radios: document.querySelectorAll('input[type=radio]').length,
                fieldsets: document.querySelectorAll('fieldset').length,
            };
        }""")
        if form_counts['radios'] > 3 or form_counts['fieldsets'] > 1:
            return "screening"  # likely a screening questions page
        if form_counts['selects'] > 0 and form_counts['radios'] > 0:
            return "documents"  # likely the documents page with resume picker

    return "unknown"


# ---------------------------------------------------------------------------
#  Click Easy Apply on job detail page
# ---------------------------------------------------------------------------

def _find_easy_apply_href(page, timeout_ms: int = 5000) -> str | None:
    """Find the Easy/Quick Apply element and return its href (if <a>) or None."""
    for sel in EASY_APPLY_SELECTORS:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=timeout_ms):
                href = el.get_attribute("href")
                tag = el.evaluate("el => el.tagName")
                print(f"  [Easy Apply] Found: {sel} (tag={tag}, href={href})")
                if href:
                    # It's a link -- return the href so we can navigate directly
                    return href
                return ""  # button (no href), will need click
        except Exception:
            continue
    return None  # not found


def _click_easy_apply(page, timeout_ms: int = 5000) -> bool:
    """Find and click the Easy Apply button on the job detail page (button-only)."""
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


def navigate_and_click_easy_apply(page, job_url: str) -> str:
    """Navigate to the job detail page, click Easy Apply, wait for redirect.

    Returns the final apply-form URL (with ?sol=…), or empty string on failure.
    """
    print(f"  Navigating to job page: {job_url}")
    try:
        page.goto(job_url, wait_until="domcontentloaded", timeout=30_000)
    except Exception as exc:
        print(f"  Navigation error: {exc}")
        return ""

    time.sleep(3)
    current_url = page.url

    if "login" in current_url.lower() or "oauth" in current_url.lower():
        print("  Login required.")
        return ""

    if "applied-jobs" in current_url.lower():
        print("  Already applied.")
        return ""

    # Try to find the Easy Apply element and check if it's a link or button
    href = _find_easy_apply_href(page)
    if href is None:
        print("  No Easy Apply button found on job detail page.")
        return ""

    if href:
        # It's an <a> link -- navigate directly to the apply URL (most reliable)
        if href.startswith("/"):
            apply_url = f"{BASE_URL}{href}"
        elif href.startswith("http"):
            apply_url = href
        else:
            apply_url = f"{BASE_URL}/{href}"
        print(f"  Navigating directly to apply URL: {apply_url}")
        try:
            page.goto(apply_url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:
            print(f"  Apply page navigation error: {exc}")
            return ""
        time.sleep(2)
        apply_url_final = page.url
        if "/apply" in apply_url_final:
            print(f"  Apply form loaded: {apply_url_final}")
            return apply_url_final
        print(f"  Unexpected URL after apply navigation: {apply_url_final}")
        return apply_url_final if "/apply" in apply_url_final else ""

    # It's a <button> -- click and wait for redirect
    clicked = _click_easy_apply(page)
    if not clicked:
        print("  No Easy Apply button found on job detail page.")
        return ""

    # Wait for redirect to the apply form URL
    time.sleep(4)
    apply_url = page.url

    if "/apply" in apply_url:
        print(f"  Apply form URL: {apply_url}")
        return apply_url

    # Check if a new tab was opened
    context = page.context
    for pg in context.pages:
        try:
            if "/apply" in pg.url:
                print(f"  Apply form opened in new tab: {pg.url}")
                pg.bring_to_front()
                return pg.url
        except Exception:
            continue

    print(f"  Easy Apply clicked but no /apply URL detected. Current: {apply_url}")
    return ""


# ---------------------------------------------------------------------------
#  Page 1: Choose documents
# ---------------------------------------------------------------------------

def _find_resume_picker_elements(page):
    """Find resume items in the JobsDB resume picker (after clicking change).

    The JobsDB resume picker uses a <select> element (data-testid="select-input")
    inside the resume section.  Options include a placeholder ("Please select...")
    followed by saved resumes (most-recent first).

    Returns a list of (kind, element_or_select) tuples.
    """
    items = []

    # Strategy A (primary): <select data-testid="select-input"> with resume options
    try:
        sel = page.locator('[data-testid="select-input"]').first
        if sel.is_visible(timeout=1000):
            items.append(("select", sel))
    except Exception:
        pass

    if items:
        return items

    # Strategy B: fallback -- any visible <select> near the resume section
    for el in page.locator("select").all():
        try:
            if el.is_visible(timeout=300):
                items.append(("select-fallback", el))
        except Exception:
            continue

    if items:
        return items

    # Strategy C: clickable resume card/div elements
    for css in (
        '[data-testid*="resume" i] li',
        '[data-automation*="resume" i] li',
        '[class*="resume-picker"] li',
        '[class*="document-picker"] li',
    ):
        try:
            for el in page.locator(css).all():
                if el.is_visible(timeout=300):
                    items.append(("picker-item", el))
        except Exception:
            continue

    return items


def _handle_documents_page(page) -> bool:
    """Fill resume and cover letter choices.

    Resume picker structure (discovered via diagnostics):
      - resume-method radio: upload | change | none
      - After clicking change, a <select data-testid="select-input"> appears
        with options: placeholder + saved resumes (most-recent first)
      - Selecting an option from the <select> attaches the resume

    Cover letter: always choose "none".
    """
    print("  [Documents page]")

    # --- Resume --------------------------------------------------------------
    resume_selected = False

    for attempt in range(2):
        wait_secs = 3 if attempt == 0 else 5
        print(f"    Resume: clicking 'Select a resume' (attempt {attempt + 1})...")

        try:
            page.locator('input[name="resume-method"][value="change"]').first.click()
        except Exception:
            page.evaluate("""
                const el = document.querySelector('input[name="resume-method"][value="change"]');
                if (el) el.click();
            """)

        print(f"    Resume: waiting {wait_secs}s for picker to load...")
        time.sleep(wait_secs)

        picker_items = _find_resume_picker_elements(page)

        if picker_items:
            kind, element = picker_items[0]

            if kind in ("select", "select-fallback"):
                # It's a <select> element -- use select_option
                print(f"    Resume: found <select> ({kind})")
                options = element.locator("option").all()
                print(f"    Resume: {len(options)} options in select")

                # Find the first non-placeholder option (the most recent resume)
                selected = False
                for i, opt in enumerate(options):
                    try:
                        opt_text = _safe_text(opt, timeout_ms=300)
                        opt_value = opt.get_attribute("value") or ""
                        # Skip placeholder options
                        if "please select" in opt_text.lower():
                            continue
                        if opt_text and opt_value:
                            element.select_option(value=opt_value)
                            print(f"    Resume: selected option {i}: '{opt_text[:60]}'")
                            selected = True
                            break
                    except Exception:
                        continue

                if not selected and len(options) > 1:
                    # Fallback: select by index (skip placeholder at index 0)
                    try:
                        element.select_option(index=1)
                        print(f"    Resume: selected by index=1")
                        selected = True
                    except Exception:
                        pass

                if selected:
                    time.sleep(1)
                    resume_selected = True
                    break
                else:
                    print(f"    Resume: could not select any option")

            else:
                # Non-select picker (radio buttons or clickable cards)
                print(f"    Resume: found {len(picker_items)} item(s) via {kind}")
                try:
                    element.click()
                    print(f"    Resume: clicked first item")
                    time.sleep(1)
                except Exception:
                    try:
                        element.evaluate("el => el.click()")
                        time.sleep(1)
                    except Exception:
                        pass

                # Look for confirm button
                for btn_sel in (
                    'button:has-text("Choose")',
                    'button:has-text("Select")',
                    'button:has-text("Confirm")',
                    'button:has-text("Done")',
                ):
                    try:
                        btn = page.locator(btn_sel).first
                        if btn.is_visible(timeout=800):
                            btn.click()
                            print(f"    Resume: confirmed via '{btn_sel}'")
                            time.sleep(1)
                            break
                    except Exception:
                        continue

                resume_selected = True
                break
        else:
            print(f"    Resume: no picker items found after {wait_secs}s wait")

    if not resume_selected:
        print("    Resume: no saved resumes found -> 'Don't include a resume'")
        try:
            page.locator('input[name="resume-method"][value="none"]').first.click()
        except Exception:
            page.evaluate("""
                const el = document.querySelector('input[name="resume-method"][value="none"]');
                if (el) el.click();
            """)

    # --- Cover letter --------------------------------------------------------
    try:
        page.locator('input[name="coverLetter-method"][value="none"]').first.click()
    except Exception:
        page.evaluate("""
            const el = document.querySelector('input[name="coverLetter-method"][value="none"]');
            if (el) el.click();
        """)
    print("    Cover letter: Don't include")

    time.sleep(0.5)
    return _click_continue(page, "documents")


# ---------------------------------------------------------------------------
#  Page 2: Employer screening questions (radio + select dropdowns)
# ---------------------------------------------------------------------------

def _match_screening_answer(question_text: str) -> str | list[str] | None:
    """Match a screening question text to a predefined answer."""
    q_lower = question_text.lower().strip()
    for pattern, answer in SCREENING_ANSWERS.items():
        if pattern in q_lower:
            return answer
    return None


def _find_select_for_question(page, question_text: str):
    """Given a question label text, find the associated <select> element.

    Uses the actual JobsDB DOM structure discovered via diagnostics:
      <label for="question-HK_Q_xxx_V_1">
        <span><strong>Question text?</strong></span>
      </label>
      ...
      <select id="question-HK_Q_xxx_V_1">...</select>

    Strategy (in order of reliability):
      1. Find <label> with matching text -> use its `for` attr -> getElementById
      2. Find matching label -> walk up to question container -> find select inside
      3. No dangerous "any visible select" fallback (prevents wrong matches)
    """
    q_lower = question_text.lower().strip()

    # Strategy 1: label[for] -> select[id]  (most reliable)
    for lbl in page.locator("label[for]").all():
        try:
            txt = _safe_text(lbl, timeout_ms=300)
        except Exception:
            continue
        if not txt or len(txt) < 5:
            continue
        txt_lower = txt.lower().strip()
        if q_lower[:40] in txt_lower or txt_lower[:40] in q_lower:
            for_attr = lbl.get_attribute("for")
            if for_attr:
                sel = page.locator(f"select#{for_attr}").first
                try:
                    if sel.is_visible(timeout=500):
                        return sel
                except Exception:
                    pass

    # Strategy 2: matching label -> walk up to container with _9zha26t class ->
    # find select inside (JobsDB question container class)
    for lbl in page.locator("label").all():
        try:
            txt = _safe_text(lbl, timeout_ms=300)
        except Exception:
            continue
        if not txt or len(txt) < 5:
            continue
        txt_lower = txt.lower().strip()
        if q_lower[:40] in txt_lower or txt_lower[:40] in q_lower:
            # Walk up to find a container that holds both the label and a select
            for levels_up in range(1, 5):
                ancestor = lbl.locator("../" * levels_up)
                sel = ancestor.locator("select").first
                try:
                    if sel.is_visible(timeout=300):
                        # Verify this select is NOT inside the label itself
                        sel_id = sel.get_attribute("id") or ""
                        if sel_id and sel_id != (lbl.get_attribute("for") or "___"):
                            # Different select - likely the right one if it's a sibling container
                            return sel
                        elif not sel_id:
                            return sel
                except Exception:
                    continue

    # No fallback to "any visible select" - that caused wrong matches
    return None


def _is_select_already_answered(sel) -> bool:
    """Check if a <select> element already has a non-placeholder value selected."""
    try:
        val = sel.input_value(timeout=300)
        if val:  # non-empty value means it's been answered
            return True
    except Exception:
        pass
    return False


def _is_question_in_fieldset(page, question_text: str) -> bool:
    """Check if a question text appears inside a <fieldset> (radio group)."""
    for fs in page.locator("fieldset").all():
        try:
            legend = fs.locator("legend").first
            txt = _safe_text(legend, timeout_ms=300)
            if txt and question_text.lower().strip()[:30] in txt.lower():
                return True
        except Exception:
            continue
    return False


def _answer_select_question(page, question_text: str, answer: str):
    """Answer a screening question that uses a <select> dropdown.

    If no option matches the preset answer, falls back to picking the
    last option (the highest/most value).
    """
    prefix = question_text[:55] + "..." if len(question_text) > 55 else question_text
    print(f"    Q: {prefix}")
    print(f"    A: {answer}")

    sel = _find_select_for_question(page, question_text)
    if sel is None:
        print(f"      [WARN] No select found for question")
        return

    answer_lower = answer.lower()
    options = sel.locator("option").all()
    for opt in options:
        try:
            opt_text = _safe_text(opt, timeout_ms=300)
            if answer_lower in opt_text.lower():
                opt_value = opt.get_attribute("value") or ""
                sel.select_option(value=opt_value)
                print(f"      Selected: {opt_text[:50]}")
                time.sleep(0.3)
                return
        except Exception:
            continue

    # Fallback: pick the last option with a non-empty value
    print(f"      [WARN] No option matching '{answer}' - picking last option")
    for opt in reversed(options):
        try:
            opt_val = opt.get_attribute("value") or ""
            opt_text = _safe_text(opt, timeout_ms=200)
            if opt_val and opt_text:
                sel.select_option(value=opt_val)
                print(f"      Last-option fallback: {opt_text[:50]}")
                time.sleep(0.3)
                return
        except Exception:
            continue


def _answer_question(page, question_text: str, answer: str | list[str]):
    """Answer a screening question using radio/checkbox inputs."""
    if isinstance(answer, list):
        answers = [a.strip() for a in answer]
    else:
        answers = [answer.strip()]

    prefix = question_text[:55] + "..." if len(question_text) > 55 else question_text
    print(f"    Q: {prefix}")
    print(f"    A: {answer}")

    for ans_text in answers:
        clicked = False

        # Strategy 1: click a label/span containing the answer text
        for tag in ["label", "span"]:
            try:
                locator = page.locator(f'{tag}:has-text("{ans_text}")').first
                if locator.is_visible(timeout=1500):
                    locator.click()
                    clicked = True
                    break
            except Exception:
                continue

        # Strategy 2: click input by value attribute
        if not clicked:
            for itype in ("radio", "checkbox"):
                try:
                    inp = page.locator(
                        f'input[type="{itype}"][value*="{ans_text}" i]'
                    ).first
                    if inp.is_visible(timeout=800):
                        inp.click()
                        clicked = True
                        break
                except Exception:
                    continue

        # Strategy 3: click any matching visible text
        if not clicked:
            try:
                el = page.locator(f'text="{ans_text}"').first
                if el.is_visible(timeout=800):
                    el.click()
                    clicked = True
            except Exception:
                pass

        if not clicked:
            print(f"      [WARN] Could not click: {ans_text}")

        time.sleep(0.25)


def _find_checkbox_groups(page) -> list[dict]:
    """Find groups of standalone checkboxes (not inside fieldsets) with their question text.

    Groups checkboxes by their `name` attribute (since JobsDB uses empty `value` attrs
    and identifies checkboxes by their label text instead).

    Returns a list of dicts: {question_text, name, checkbox_count}
    The checkboxes can be re-located via page.locator(f'input[type="checkbox"][name="{name}"]')
    """
    # Use JavaScript to find all checkbox groups grouped by name
    group_info = page.evaluate("""() => {
        const groups = {};
        const checkboxes = document.querySelectorAll('input[type="checkbox"]');
        
        checkboxes.forEach(cb => {
            // Skip if inside a fieldset
            let inFieldset = false;
            let p = cb.parentElement;
            while (p) {
                if (p.tagName === 'FIELDSET') { inFieldset = true; break; }
                p = p.parentElement;
            }
            if (inFieldset) return;
            
            // Skip if not visible
            if (cb.offsetParent === null) return;
            
            const name = cb.name;
            if (!name) return;
            
            if (!groups[name]) {
                // Find question text for this group
                let questionText = '';
                let ancestor = cb.parentElement;
                for (let i = 0; i < 8 && ancestor; i++) {
                    // Look for a label/legend/span that describes this group
                    const candidates = ancestor.querySelectorAll('label, legend, span, h3, h4');
                    for (const c of candidates) {
                        const txt = c.textContent.trim();
                        // Question text is usually longer and doesn't contain a checkbox
                        if (txt.length > 15 && !c.querySelector('input[type="checkbox"]')) {
                            questionText = txt.substring(0, 200);
                            break;
                        }
                    }
                    if (questionText) break;
                    ancestor = ancestor.parentElement;
                }
                
                groups[name] = {
                    name: name,
                    question_text: questionText,
                    count: 0,
                    labels: []
                };
            }
            
            groups[name].count++;
            // Collect label text for this checkbox
            const label = cb.closest('label');
            if (label) {
                groups[name].labels.push(label.textContent.trim().substring(0, 60));
            }
        });
        
        return Object.values(groups);
    }""")

    results = []
    for g in group_info:
        if g['count'] > 0 and g['question_text']:
            results.append({
                "question_text": g['question_text'],
                "name": g['name'],
                "checkbox_count": g['count'],
                "labels": g['labels'],
            })

    return results


def _is_checkbox_group_answered(page, name: str) -> bool:
    """Check if any checkbox with the given name is already checked."""
    try:
        checked = page.locator(f'input[type="checkbox"][name="{name}"]:checked')
        return checked.count() > 0
    except Exception:
        return False


def _answer_checkbox_group(page, name: str, question_text: str, answers: list[str]):
    """Click checkboxes matching the answer list for a checkbox group identified by name.

    Since JobsDB checkboxes have empty value="" attributes, we match by label text.
    """
    prefix = question_text[:55] + "..." if len(question_text) > 55 else question_text
    print(f"    Q: {prefix}")
    print(f"    A: {answers} (checkbox)")

    for ans_text in answers:
        clicked = False

        # Strategy 1: find a <label> within the checkbox group whose text matches,
        # then click it (the label click toggles the associated checkbox)
        cbs = page.locator(f'input[type="checkbox"][name="{name}"]').all()
        for cb in cbs:
            try:
                label = cb.locator("xpath=ancestor::label")
                if label.count() > 0:
                    lbl_text = _safe_text(label.first, timeout_ms=200)
                    if ans_text.lower() in lbl_text.lower():
                        label.first.click()
                        print(f"      Checked: {ans_text[:50]}")
                        clicked = True
                        break
            except Exception:
                continue

        # Strategy 2: click a label containing the answer text (page-wide)
        if not clicked:
            try:
                locator = page.locator(f'label:has-text("{ans_text}")').first
                if locator.is_visible(timeout=1500):
                    locator.click()
                    print(f"      Checked (label): {ans_text[:50]}")
                    clicked = True
            except Exception:
                pass

        # Strategy 3: click any matching visible text element
        if not clicked:
            try:
                el = page.locator(f'text="{ans_text}"').first
                if el.is_visible(timeout=800):
                    el.click()
                    print(f"      Checked (text): {ans_text[:50]}")
                    clicked = True
            except Exception:
                pass

        if not clicked:
            print(f"      [WARN] Could not check: {ans_text}")

        time.sleep(0.25)


def _is_radio_question_answered(page) -> bool:
    """Check if the page already has a checked radio (pre-filled answer)."""
    try:
        checked = page.locator('input[type="radio"]:checked').first
        if checked.is_visible(timeout=500):
            return True
    except Exception:
        pass
    return False


def _is_fieldset_radio_checked(page, question_text: str) -> bool:
    """Check if a radio inside a matching fieldset is already checked."""
    q_lower = question_text.lower().strip()
    for fs in page.locator("fieldset").all():
        try:
            legend = fs.locator("legend").first
            txt = _safe_text(legend, timeout_ms=300)
            if txt and q_lower[:30] in txt.lower():
                checked = fs.locator('input[type="radio"]:checked').first
                if checked.count() > 0:
                    return True
        except Exception:
            continue
    return False


def _handle_screening_questions_page(page) -> bool:
    """Detect and answer all screening questions (radio + select dropdowns + checkboxes).

    JobsDB uses three types of form controls:
      - <fieldset> with <legend> + radio inputs
      - <label> text + <select> dropdown
      - Standalone checkbox groups (not in fieldset) with nearby label text

    Strategy (order matters):
      1. For each known question pattern, check FIELDSET (radio) FIRST.
      2. Then check SELECT dropdown.
      2.5. Then check standalone CHECKBOX groups (list-type answers).
      3. Skip questions that are already answered.
      4. Fallback: iterate all unanswered <select> elements, match by option text.
      5. Fallback: iterate all unanswered checkbox groups.
      6. Fallback: text-node scan of page body.
    """
    print("  [Screening questions page]")

    answered = 0
    answered_selects = set()  # track select elements we've already handled

    # Collect unique question texts from legends and labels
    question_texts = set()
    for el in page.locator("fieldset legend, label").all():
        try:
            txt = _safe_text(el, timeout_ms=300)
            if len(txt) > 5:
                question_texts.add(txt)
        except Exception:
            continue

    # Sort by length descending for more-specific matches first
    for q_text in sorted(question_texts, key=len, reverse=True):
        answer = _match_screening_answer(q_text)
        if not answer:
            continue

        # --- 1. Check if this is a fieldset (radio) question FIRST ---
        if _is_question_in_fieldset(page, q_text):
            if _is_fieldset_radio_checked(page, q_text):
                print(f"    Q: {q_text[:55]}... - radio already answered")
                answered += 1
                continue
            _answer_question(page, q_text, answer)
            answered += 1
            continue

        # --- 2. Check for select-based question ---
        sel = _find_select_for_question(page, q_text)
        if sel is not None:
            if _is_select_already_answered(sel):
                print(f"    Q: {q_text[:55]}... - select already answered")
                answered += 1
                continue
            ans_str = answer if isinstance(answer, str) else answer[0]
            _answer_select_question(page, q_text, ans_str)
            try:
                answered_selects.add(sel.evaluate("el => el.outerHTML.substring(0, 80)"))
            except Exception:
                pass
            answered += 1
            continue

        # --- 2.5. Check for standalone checkbox group ---
        if isinstance(answer, list):
            cb_groups = _find_checkbox_groups(page)
            for group in cb_groups:
                gq = group["question_text"].lower().strip()
                if q_text.lower().strip()[:30] in gq or gq[:30] in q_text.lower().strip():
                    if _is_checkbox_group_answered(page, group["name"]):
                        print(f"    Q: {q_text[:55]}... - checkbox already answered")
                    else:
                        _answer_checkbox_group(page, group["name"], q_text, answer)
                    answered += 1
                    break
            else:
                # No matching checkbox group found, fall through to step 3
                pass
            if any(
                q_text.lower().strip()[:30] in g["question_text"].lower().strip()
                or g["question_text"].lower().strip()[:30] in q_text.lower().strip()
                for g in cb_groups
            ):
                continue

        # --- 3. Not in fieldset and no select found -- try radio anyway ---
        if not _is_radio_question_answered(page):
            _answer_question(page, q_text, answer)
            answered += 1
        else:
            print(f"    Q: {q_text[:55]}... - radio already answered (global)")
            answered += 1

    # --- Fallback: iterate ALL visible <select> elements ---
    # Handles cases where question label text is not detected correctly
    # (e.g. select shows option text like "No experience" instead of the question)
    print("    Fallback: checking all unanswered selects...")
    for sel in page.locator("select:visible").all():
        try:
            if _is_select_already_answered(sel):
                continue
            sel_id = sel.evaluate("el => el.outerHTML.substring(0, 80)")
            if sel_id in answered_selects:
                continue
            # Check if any option text matches a known answer
            options = sel.locator("option").all()
            matched = False
            for pattern, answer_val in SCREENING_ANSWERS.items():
                if isinstance(answer_val, list):
                    continue  # skip multi-select patterns for this fallback
                ans_lower = answer_val.lower()
                for opt in options:
                    try:
                        opt_text = _safe_text(opt, timeout_ms=200)
                        if opt_text and ans_lower in opt_text.lower():
                            sel.select_option(value=opt.get_attribute("value") or "")
                            print(f"      Fallback: selected '{opt_text[:50]}' (matched '{pattern}')")
                            answered_selects.add(sel_id)
                            answered += 1
                            matched = True
                            break
                    except Exception:
                        continue
                if matched:
                    break
        except Exception:
            continue

    # Fallback: check all standalone checkbox groups
    print("    Fallback: checking standalone checkbox groups...")
    cb_groups = _find_checkbox_groups(page)
    for group in cb_groups:
        gq = group["question_text"]
        if _is_checkbox_group_answered(page, group["name"]):
            continue
        answer = _match_screening_answer(gq)
        if answer and isinstance(answer, list):
            _answer_checkbox_group(page, group["name"], gq, answer)
            answered += len(answer)
        elif answer:
            # Single answer matched a checkbox group -- click first matching checkbox
            _answer_checkbox_group(page, group["name"], gq, [answer] if isinstance(answer, str) else answer)
            answered += 1

    # Fallback: text-node scan (last resort)
    if answered == 0:
        print("    Trying text-node scan...")
        body = _safe_body_text(page)
        for line in body.split("\n"):
            line = line.strip()
            if len(line) < 10:
                continue
            q_kw = ["?", "years' experience", "which of the following",
                    "what's your", "how many", "how much",
                    "right to work", "expected monthly", "notice are you",
                    "microsoft office", "languages are you fluent",
                    "level of education", "receptionist",
                    "how would you rate", "programming languages"]
            if any(kw in line.lower() for kw in q_kw):
                answer = _match_screening_answer(line)
                if answer:
                    if _is_question_in_fieldset(page, line):
                        if not _is_fieldset_radio_checked(page, line):
                            _answer_question(page, line, answer)
                            answered += 1
                    elif _find_select_for_question(page, line):
                        ans_str = answer if isinstance(answer, str) else answer[0]
                        _answer_select_question(page, line, ans_str)
                        answered += 1
                    elif isinstance(answer, list):
                        # Check if this is a checkbox group
                        cb_groups = _find_checkbox_groups(page)
                        matched_cb = False
                        for group in cb_groups:
                            gq = group["question_text"].lower().strip()
                            if line.lower().strip()[:30] in gq or gq[:30] in line.lower().strip():
                                if not _is_checkbox_group_answered(page, group["name"]):
                                    _answer_checkbox_group(page, group["name"], line, answer)
                                answered += 1
                                matched_cb = True
                                break
                        if not matched_cb:
                            _answer_question(page, line, answer)
                            answered += 1
                    else:
                        _answer_question(page, line, answer)
                        answered += 1

    # --- Final fallback: pick last option for any remaining unanswered questions ---
    print("    Fallback: picking last option for remaining unanswered questions...")

    # Unanswered selects
    for sel in page.locator("select:visible").all():
        try:
            if _is_select_already_answered(sel):
                continue
            sel_html = sel.evaluate("el => el.outerHTML.substring(0, 80)")
            if sel_html in answered_selects:
                continue
            options = sel.locator("option").all()
            # Find the last option with a non-empty value (skip placeholder at index 0)
            last_valid = None
            for opt in reversed(options):
                try:
                    opt_val = opt.get_attribute("value") or ""
                    opt_text = _safe_text(opt, timeout_ms=200)
                    if opt_val and opt_text:
                        last_valid = (opt_val, opt_text)
                        break
                except Exception:
                    continue
            if last_valid:
                sel.select_option(value=last_valid[0])
                print(f"      Last-option fallback: selected '{last_valid[1][:50]}' (last option)")
                answered += 1
        except Exception:
            continue

    # Unanswered fieldset radio groups
    for fs in page.locator("fieldset").all():
        try:
            legend = fs.locator("legend").first
            q_txt = _safe_text(legend, timeout_ms=300)
            # Check if already answered
            checked = fs.locator('input[type="radio"]:checked')
            if checked.count() > 0:
                continue
            # Pick the last radio option
            radios = fs.locator('input[type="radio"]').all()
            if radios:
                last_radio = radios[-1]
                last_label_text = ""
                try:
                    parent_label = last_radio.locator("..")
                    last_label_text = _safe_text(parent_label, timeout_ms=200)
                except Exception:
                    pass
                last_radio.click()
                print(f"      Last-option fallback (radio): '{last_label_text[:50]}' for Q: {q_txt[:40]}")
                answered += 1
                time.sleep(0.3)
        except Exception:
            continue

    # Unanswered checkbox groups: check the last checkbox
    for group in _find_checkbox_groups(page):
        try:
            if _is_checkbox_group_answered(page, group["name"]):
                continue
            # Find all checkboxes in this group and click the last one
            cbs = page.locator(f'input[type="checkbox"][name="{group["name"]}"]').all()
            if cbs:
                last_cb = cbs[-1]
                last_label_text = ""
                try:
                    lbl = last_cb.locator("xpath=ancestor::label")
                    if lbl.count() > 0:
                        last_label_text = _safe_text(lbl.first, timeout_ms=200)
                except Exception:
                    pass
                last_cb.click()
                print(f"      Last-option fallback (checkbox): '{last_label_text[:50]}' for Q: {group['question_text'][:40]}")
                answered += 1
                time.sleep(0.3)
        except Exception:
            continue

    print(f"    Answered {answered} screening question(s)")
    time.sleep(1)
    return _click_continue(page, "screening questions")


# ---------------------------------------------------------------------------
#  Page 3: Update JobsDB profile (skip)
# ---------------------------------------------------------------------------

def _handle_profile_page(page) -> bool:
    """Update JobsDB profile page -- just click Continue (default settings)."""
    print("  [Update profile page] - using defaults")
    time.sleep(1)
    return _click_continue(page, "profile")


# ---------------------------------------------------------------------------
#  Page 4: Submit application
# ---------------------------------------------------------------------------

def _handle_submit_page(page) -> bool:
    """Final review & submit page. Click the submit button."""
    print("  [Submit application page]")
    time.sleep(1.5)

    for sel in CONTINUE_SELECTORS:
        btn = page.locator(sel).first
        try:
            if btn.is_visible(timeout=2000):
                btn.click()
                print("    Application submitted!")
                time.sleep(2)
                return True
        except Exception:
            continue

    return _click_continue(page, "submit")


# ---------------------------------------------------------------------------
#  Main form-filling orchestration
# ---------------------------------------------------------------------------

def fill_apply_form(page) -> dict[str, Any]:
    """Complete the multi-page JobsDB apply form.

    Args:
        page: Playwright Page object already on an apply URL.

    Returns:
        dict with status, steps_completed, steps_failed, submitted flag.
    """
    result: dict[str, Any] = {
        "status": "unknown",
        "steps_completed": [],
        "steps_failed": [],
        "submitted": False,
    }

    handlers: dict[str, callable] = {
        "documents": _handle_documents_page,
        "screening": _handle_screening_questions_page,
        "profile": _handle_profile_page,
        "submit": _handle_submit_page,
    }

    max_steps = 8
    consecutive_same_page = 0
    last_page_type = None

    for step in range(max_steps):
        time.sleep(2)

        page_type = _detect_page_type(page)
        print(f"  Step {step + 1}: detected = {page_type}")

        current_url = page.url

        # Detect screening loop: if same page type appears 3+ times, break
        if page_type == last_page_type and page_type in ("screening", "unknown"):
            consecutive_same_page += 1
            if consecutive_same_page >= 3:
                error_text = _check_validation_errors(page)
                reason = error_text[:60] if error_text else f"stuck on {page_type}"
                print(f"    Loop detected: {page_type} repeated {consecutive_same_page} times. Reason: {reason}")
                result["steps_failed"].append(f"loop:{reason}")
                break
        else:
            consecutive_same_page = 1
            last_page_type = page_type

        if page_type == "submit":
            success = _handle_submit_page(page)
            result["submitted"] = success
            if success:
                result["steps_completed"].append("submit")
            else:
                result["steps_failed"].append("submit")
            break

        if page_type == "unknown":
            error_text = _check_validation_errors(page)
            if error_text:
                print(f"    Validation error: {error_text[:80]}")
                result["steps_failed"].append(f"validation:{error_text[:60]}")
                break
            # Check if page has any form elements at all
            has_form = page.evaluate("""() => {
                return document.querySelectorAll('select, input[type=radio], fieldset, form').length > 0;
            }""")
            if not has_form:
                print("    Unknown page with no form elements - likely expired/removed job")
                result["steps_failed"].append("empty_apply_page")
                break
            print("    Unknown page, trying Continue anyway...")
            success = _click_continue(page, "unknown")
        else:
            handler = handlers.get(page_type)
            if handler:
                success = handler(page)
            else:
                success = _click_continue(page, page_type)

        if success:
            result["steps_completed"].append(page_type)
        else:
            result["steps_failed"].append(page_type)
            break

        # Check for redirects/landings
        new_url = page.url
        if new_url != current_url:
            if "applied-jobs" in new_url:
                print("  Redirected to applied-jobs. Application complete.")
                result["submitted"] = True
                break
            if any(kw in new_url for kw in ["confirmation", "thank-you", "success"]):
                print("  Confirmation page detected.")
                result["submitted"] = True
                break

    if result["submitted"]:
        result["status"] = "submitted"
    elif result["steps_failed"]:
        result["status"] = "partial"
    elif result["steps_completed"]:
        result["status"] = "in_progress"
    else:
        result["status"] = "failed"

    return result


def _check_validation_errors(page) -> str:
    """Check for JobsDB validation error messages."""
    for sel in ['[class*="error"]', '[class*="validation"]',
                '[data-automation*="error"]', '[role="alert"]']:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=500):
                return _safe_text(el)
        except Exception:
            continue
    return ""


# ---------------------------------------------------------------------------
#  CLI entry point
# ---------------------------------------------------------------------------

def _search_api_cdp(page, keywords: str, where: str = "Hong Kong", page_size: int = 20) -> list[dict]:
    """Search JobsDB via the browser (carries auth cookies)."""
    import urllib.request as _urllib_req
    params = [
        f"keywords={_urllib_req.quote(keywords)}",
        f"where={_urllib_req.quote(where)}",
        f"pageSize={page_size}",
    ]
    url = f"{BASE_URL}/api/jobsearch/v5/search?{'&'.join(params)}"

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
        return []
    return (result or {}).get("data", [])


def extract_urls_from_markdown(filepath: str) -> list[dict]:
    """Extract job URLs from a Markdown table file.

    Parses tables like:
        | 序号 | 发布日期 | 职位名称 | 公司名称 | 薪资 | 地点 | 链接 |
        | 1 | 2026-05-19 | Data Analyst | ABC Corp | $30K | HK | [查看详情](https://...) |

    Returns a list of dicts with job_url, title, company.
    """
    import re
    from pathlib import Path

    path = Path(filepath)
    if not path.exists():
        print(f"File not found: {filepath}")
        return []

    text = path.read_text(encoding="utf-8")
    jobs = []

    # Match markdown link: [text](url)
    link_pattern = re.compile(r'\[([^\]]*)\]\((https://hk\.jobsdb\.com/[^\s)]+)\)')

    for line in text.splitlines():
        m = link_pattern.search(line)
        if not m:
            continue
        job_url = m.group(2).strip()
        # Extract title from table columns (column 3, index 2 after splitting by |)
        cols = [c.strip() for c in line.split("|")]
        title = cols[3] if len(cols) > 3 else ""
        company = cols[4] if len(cols) > 4 else ""
        # Clean markdown formatting from title
        title = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', title)
        company = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', company)

        if job_url and "jobsdb.com" in job_url:
            jobs.append({
                "job_url": job_url,
                "title": title.strip() or "unknown",
                "company": company.strip() or "N/A",
            })

    print(f"Extracted {len(jobs)} job URLs from {filepath}")
    return jobs


def main():
    parser = argparse.ArgumentParser(description="JobsDB Easy Apply Form Filler (CDP)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--job-id", help="Single job ID (requires --role-id)")
    parser.add_argument("--role-id", help="Role ID / slug for single job mode")
    mode.add_argument("--keywords", help="Search keywords for batch mode")
    parser.add_argument("--where", default="Hong Kong", help="Location for search")
    parser.add_argument("--max", type=int, default=5, help="Max jobs to process in batch mode")
    mode.add_argument("--url", help="Direct apply form URL (bypasses job page + click)")
    mode.add_argument("--file", help="Markdown file containing job URLs (extracts all links)")
    args = parser.parse_args()

    cdp_url = get_cdp_url()
    available, version_info = check_cdp_available(cdp_url)
    if not available:
        print(f"Chrome CDP not available at {cdp_url}")
        print("Start Chrome with: chrome.exe --remote-debugging-port=9222")
        sys.exit(1)

    print(f"Connected to Chrome: {version_info.get('Browser', 'unknown')}")
    sync_playwright = _ensure_playwright()

    # Build the list of jobs to process
    jobs: list[dict] = []

    with sync_playwright() as p:
        browser = connect_browser_over_cdp(p, cdp_url)
        if not browser.contexts:
            print("No browser contexts available. Open a tab in Chrome first.")
            browser.close()
            sys.exit(1)

        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()

        # -- Resolve job list ---------------------------------------------------
        if args.url:
            # Direct apply form URL – no search, no click needed
            jobs = [{"job_url": None, "apply_url": args.url,
                     "title": "direct-url", "company": ""}]
        elif args.job_id:
            if not args.role_id:
                print("Error: --role-id is required with --job-id")
                sys.exit(1)
            job_url = JOB_URL_TEMPLATE.format(job_id=args.job_id, role_id=args.role_id)
            jobs = [{"job_url": job_url, "apply_url": None,
                     "title": f"job {args.job_id}", "company": ""}]
        elif args.keywords:
            # Use CDP search (browser fetch)
            if "jobsdb.com" not in page.url:
                page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=20_000)
                time.sleep(1)
            results = _search_api_cdp(page, args.keywords, args.where, args.max)
            for job in results[: args.max]:
                j_url = JOB_URL_TEMPLATE.format(job_id=job["id"], role_id=job["roleId"])
                jobs.append({
                    "job_url": j_url,
                    "apply_url": None,
                    "title": job.get("title", ""),
                    "company": job.get("companyName", ""),
                })
            if not jobs:
                print("No search results found.")
                sys.exit(1)
        elif args.file:
            # Read job URLs from a Markdown file
            raw_jobs = extract_urls_from_markdown(args.file)
            for j in raw_jobs:
                jobs.append({
                    "job_url": j["job_url"],
                    "apply_url": None,
                    "title": j["title"],
                    "company": j["company"],
                })
            if not jobs:
                print("No URLs found in the markdown file.")
                sys.exit(1)

        print(f"\n{'=' * 60}")
        print(f"  JobsDB Easy Apply Form Filler (CDP)")
        print(f"{'=' * 60}")
        print(f"Jobs to process: {len(jobs)}")
        for i, j in enumerate(jobs, 1):
            print(f"  {i}. {j['title']} @ {j.get('company', 'N/A')}")
        print()

        # -- Process each job ---------------------------------------------------
        results = []
        for idx, job in enumerate(jobs, 1):
            print(f"\n{'=' * 60}")
            print(f"  [{idx}/{len(jobs)}] {job['title']} @ {job.get('company', '')}")
            print(f"{'=' * 60}")

            apply_url = job.get("apply_url")

            if apply_url:
                # Direct URL mode – navigate to it
                print(f"  Direct apply URL: {apply_url}")
                try:
                    page.goto(apply_url, wait_until="domcontentloaded", timeout=30_000)
                except Exception as exc:
                    print(f"  Navigation error: {exc}")
                    results.append({"job": job, "status": "navigation_error", "error": str(exc)})
                    continue
                time.sleep(3)
            else:
                # Job detail page → click Easy Apply → get apply form URL
                apply_url = navigate_and_click_easy_apply(page, job["job_url"])
                if not apply_url:
                    results.append({"job": job, "status": "no_easy_apply"})
                    continue

            current_url = page.url

            if "login" in current_url.lower() or "oauth" in current_url.lower():
                print("  Login required.")
                results.append({"job": job, "status": "login_required"})
                continue

            if "applied-jobs" in current_url.lower():
                print("  Already applied.")
                results.append({"job": job, "status": "already_applied"})
                continue

            if "/apply" not in current_url.lower():
                print(f"  Not on apply page. URL: {current_url[:80]}")
                results.append({"job": job, "status": "not_apply_page", "url": current_url})
                continue

            fill_result = fill_apply_form(page)
            print(f"  Result: {fill_result['status']} | Steps: {fill_result['steps_completed']}")
            if fill_result["steps_failed"]:
                print(f"  Failed steps: {fill_result['steps_failed']}")

            results.append({"job": job, "apply_url": apply_url, **fill_result})
            time.sleep(2)

        browser.close()

    print(f"\n{'=' * 60}")
    print("  Summary")
    print(f"{'=' * 60}")
    submitted = sum(1 for r in results if r.get("submitted"))
    partial = sum(1 for r in results if r.get("status") == "partial")
    failed = sum(1 for r in results if r.get("status") in ("failed", "navigation_error"))
    already = sum(1 for r in results if r.get("status") == "already_applied")
    login = sum(1 for r in results if r.get("status") == "login_required")
    no_btn = sum(1 for r in results if r.get("status") == "no_easy_apply")
    print(f"  Submitted: {submitted} | Partial: {partial} | Failed: {failed}")
    print(f"  Already: {already} | Login needed: {login} | No Easy Apply: {no_btn}")


if __name__ == "__main__":
    main()
