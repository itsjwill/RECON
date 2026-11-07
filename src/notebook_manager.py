"""Playwright browser automation for NotebookLM notebook creation + source addition."""

import asyncio
import logging
import os
from datetime import datetime

from . import config
from .auth_keepalive import NOTEBOOKLM_HOME, is_signed_in_url
from .browser_pool import has_google_session, run_in_browser, scrub_exported_session

logger = logging.getLogger("auto-research.notebook")

# Selector cascades — try each in order until one works
SELECTORS = {
    # "New notebook" button on the home page
    "new_notebook": [
        '[aria-label="New notebook"]',
        '[aria-label="Create notebook"]',
        'button:has-text("New notebook")',
        'button:has-text("Create")',
        'button:has-text("New")',
        '[aria-label="Create new notebook"]',
        '[aria-label="New notebook"]',
        'a:has-text("New notebook")',
    ],
    # "Add source" button inside a notebook
    "add_source": [
        '[aria-label="Add source"]',
        'button:has-text("Add source")',
        'button:has-text("Add")',
        '[aria-label="Add source"]',
        '[aria-label*="source"]',
        'button:has-text("add source")',
    ],
    # YouTube source type option in the add-source dialog
    "youtube_option": [
        'button:has-text("YouTube")',
        'div:has-text("YouTube")',
        '[aria-label="YouTube"]',
        'text=YouTube',
        'li:has-text("YouTube")',
    ],
    # URL/link source type option (fallback if YouTube option not found)
    "link_option": [
        'button:has-text("Website")',
        'button:has-text("Link")',
        'button:has-text("URL")',
        '[aria-label="Website"]',
        '[aria-label="Link"]',
    ],
    # URL input field in the add-source dialog
    "url_input": [
        'input[type="url"]',
        'input[placeholder*="URL"]',
        'input[placeholder*="url"]',
        'input[placeholder*="paste"]',
        'input[placeholder*="Paste"]',
        'input[placeholder*="link"]',
        'input[placeholder*="youtube"]',
        'textarea[placeholder*="URL"]',
        'input[type="text"]',
    ],
    # Submit/insert button in the add-source dialog
    "insert_button": [
        'button:has-text("Insert")',
        'button:has-text("Add")',
        'button:has-text("Submit")',
        'button:has-text("Save")',
        '[aria-label="Insert"]',
    ],
    # Notebook title (for renaming)
    "notebook_title": [
        '[contenteditable="true"]',
        'input[aria-label*="title"]',
        'input[aria-label*="name"]',
        '.notebook-title',
        'h1[contenteditable]',
    ],
    # Share button
    "share_button": [
        'button:has-text("Share")',
        '[aria-label="Share"]',
        '[aria-label*="share"]',
        'button[aria-label="Share notebook"]',
    ],
    # "Anyone with the link" option in share dialog
    "share_anyone": [
        'text=Anyone with the link',
        'div:has-text("Anyone with the link")',
        '[role="option"]:has-text("Anyone")',
        'button:has-text("Anyone")',
    ],
    # Copy link button
    "copy_link": [
        'button:has-text("Copy link")',
        'button:has-text("Copy")',
        '[aria-label="Copy link"]',
    ],
}


async def _try_selectors(page, selector_list: list[str], action: str = "click", timeout: int = 10000, text: str | None = None) -> bool:
    """Try a cascade of selectors. Returns True if one worked."""
    for selector in selector_list:
        try:
            element = page.locator(selector).first
            await element.wait_for(state="visible", timeout=timeout)

            if action == "click":
                await element.click()
            elif action == "fill" and text is not None:
                await element.fill(text)
            elif action == "type" and text is not None:
                await element.type(text, delay=50)

            logger.info(f"  Selector worked: {selector}")
            return True
        except Exception:
            continue

    logger.warning(f"  No selector worked for this step")
    return False


async def _take_screenshot(page, name: str) -> str:
    """Take a debug screenshot."""
    config.DEBUG_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%H%M%S")
    path = config.DEBUG_SCREENSHOT_DIR / f"{ts}_{id(page) % 10000:04d}_{name}.png"  # unique per tab
    try:
        await page.screenshot(path=str(path))
        logger.info(f"  Screenshot saved: {path}")
    except Exception as e:
        logger.warning(f"  Screenshot failed: {e}")
    return str(path)


async def create_notebook_with_sources(topic: str, video_urls: list[str]) -> dict:
    """
    Create a new NotebookLM notebook and add YouTube URLs as sources.

    Runs in its own tab of the shared browser (browser_pool), so any number of
    research calls can run at the same time, from any Claude session.

    Returns:
        {
            "notebook_url": str,  # The notebook URL (or share link)
            "videos_added": list[str],
            "videos_failed": list[str],
            "error": str | None,
        }
    """
    # Progress survives a timeout, so a partly built notebook is still returned.
    progress = {"notebook_url": "", "videos_added": [], "videos_failed": []}
    timeout_s = 180 + len(video_urls) * 2 * (config.SOURCE_ADD_TIMEOUT // 1000 + 45)

    def _result(error):
        done = progress["videos_added"] + progress["videos_failed"]
        return {
            "notebook_url": progress["notebook_url"],
            "videos_added": progress["videos_added"],
            "videos_failed": progress["videos_failed"] + [u for u in video_urls if u not in done],
            "error": error,
        }

    try:
        error = await run_in_browser(
            lambda context, page: _build_notebook(context, page, video_urls, progress), timeout_s
        )
    except asyncio.TimeoutError:
        error = f"NotebookLM did not finish within {timeout_s}s; that tab was closed (other calls unaffected)."
    except Exception as e:
        logger.error(f"Browser automation error: {e}")
        error = f"{type(e).__name__}: {e}"
    return _result(error)


async def _build_notebook(context, page, video_urls: list[str], progress: dict) -> str | None:
    """Drive one tab. Fills `progress` as it goes; returns an error string or None."""
    try:
        # Step 1: Navigate to NotebookLM home
        logger.info("Navigating to NotebookLM...")
        await page.goto(NOTEBOOKLM_HOME, wait_until="domcontentloaded", timeout=config.BROWSER_TIMEOUT)
        await asyncio.sleep(3)

        # Signed out (Google sign-in or the /trynow page)?
        if not is_signed_in_url(page.url) or not await has_google_session(context):
            await _take_screenshot(page, "auth_redirect")
            return (f"NotebookLM is signed out (landed on {page.url}). "
                    "Run notebooklm_login(interactive=true) to sign in again.")

        # Restore sessionStorage saved by the last login/keepalive
        session_file = config.NOTEBOOKLM_DATA_DIR / "browser_state" / "session.json"
        if session_file.exists():
            try:
                import json
                session_data = json.loads(session_file.read_text())
                if isinstance(session_data, str):
                    session_data = json.loads(session_data)
                await page.evaluate("""(data) => {
                    for (const [key, value] of Object.entries(data)) {
                        sessionStorage.setItem(key, value);
                    }
                }""", session_data)
                logger.info("  Restored sessionStorage")
            except Exception as e:
                logger.warning(f"  Could not restore sessionStorage: {e}")

        await _take_screenshot(page, "home_loaded")

        # Step 2: Click "New notebook"
        logger.info("Creating new notebook...")
        if not await _try_selectors(page, SELECTORS["new_notebook"], timeout=15000):
            await _take_screenshot(page, "new_notebook_fail")
            return "Could not find 'New notebook' button. Check screenshot at /tmp/auto-research-debug/"

        # Wait for notebook page to load (URL changes to /notebook/<uuid>)
        logger.info("  Waiting for notebook to load...")
        try:
            await page.wait_for_url(_is_real_notebook_url, timeout=30000)
        except Exception:
            await asyncio.sleep(5)
            if not _is_real_notebook_url(page.url):
                await _take_screenshot(page, "notebook_load_fail")
                return "Notebook page did not load. Check screenshot."

        progress["notebook_url"] = page.url
        logger.info(f"  Notebook created: {page.url}")
        await asyncio.sleep(3)
        await _take_screenshot(page, "notebook_created")

        # Step 3: Add each URL as a source, confirming each one with the source counter
        for i, url in enumerate(video_urls):
            logger.info(f"Adding source {i+1}/{len(video_urls)}: {url}")
            success = await _add_source(page, url, attempt=1)
            if not success:
                logger.warning(f"  Retrying source {i+1}...")
                await asyncio.sleep(3)
                success = await _add_source(page, url, attempt=2)
            if success:
                progress["videos_added"].append(url)
                logger.info(f"  Source {i+1} added")
            else:
                progress["videos_failed"].append(url)
                logger.error(f"  Source {i+1} failed after retry")

        await _take_screenshot(page, "sources_added")

        # Step 4: Try to get share link
        share_url = await _get_share_link(page)
        if share_url:
            progress["notebook_url"] = share_url

        # A working session: refresh the cookies notebooklm-mcp reads.
        try:
            scrub_exported_session()
        except Exception as e:
            logger.warning(f"  Could not refresh state.json: {e}")

        logger.info(f"Done. Added {len(progress['videos_added'])}/{len(video_urls)} sources.")
        return None

    except Exception as e:
        logger.error(f"Browser automation error: {e}")
        await _take_screenshot(page, "error")
        return str(e)


def _is_real_notebook_url(url: str) -> bool:
    """/notebook/<uuid>, not the transitional /notebook/creating."""
    if "/notebook/" not in url:
        return False
    nb_id = url.split("/notebook/")[1].split("?")[0].strip("/")
    return len(nb_id) >= 20 and nb_id != "creating"


async def _source_count(page) -> int:
    """Read the "N sources" counter next to the chat box (-1 if not found)."""
    try:
        return await page.evaluate(r"""() => {
            for (const el of document.querySelectorAll('span,div')) {
                if (el.children.length) continue;
                const m = (el.innerText || '').trim().match(/^(\d+) sources?$/);
                if (m) return parseInt(m[1]);
            }
            return -1;
        }""")
    except Exception:
        return -1


# Gemini Notebook add-source dialog (Nov 2026): "Add source" -> "Websites"
# (website + YouTube) -> textarea "Enter URLs" -> "Insert".
_OVERLAY = ".cdk-overlay-container"


async def _add_source(page, url: str, attempt: int = 1) -> bool:
    """Add one website/YouTube URL. True once the notebook's source count goes up."""
    try:
        before = await _source_count(page)
        url_box = page.locator(f'{_OVERLAY} textarea[aria-label="Enter URLs"]')
        websites = page.locator(f'{_OVERLAY} button:has-text("Websites")')

        # An empty notebook opens the dialog by itself; otherwise open it.
        if not await url_box.is_visible() and not await websites.is_visible():
            if not await _try_selectors(page, SELECTORS["add_source"], timeout=10000):
                await _take_screenshot(page, f"add_source_btn_fail_{attempt}")
                return False
            await asyncio.sleep(1.5)
        if not await url_box.is_visible():
            await websites.first.click(timeout=10000)
            await asyncio.sleep(1)

        await url_box.first.fill(url, timeout=10000)
        insert = page.locator(f'{_OVERLAY} button:has-text("Insert")').first
        for _ in range(20):  # Insert enables once the URL validates
            if await insert.is_enabled():
                break
            await asyncio.sleep(0.25)
        await insert.click(timeout=10000)

        # Confirmed when the counter goes up (YouTube transcripts finish in the background).
        deadline = asyncio.get_running_loop().time() + config.SOURCE_ADD_TIMEOUT / 1000
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(2)
            now = await _source_count(page)
            if before >= 0 and now > before:
                return True
            if before < 0 and not await url_box.is_visible():
                return True  # no counter on the page; trust the closed dialog
        await _take_screenshot(page, f"source_not_counted_{attempt}")
        return False

    except Exception as e:
        logger.error(f"  Error adding source: {e}")
        await _take_screenshot(page, f"add_source_error_{attempt}")
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(1)
        except Exception:
            pass
        return False


async def _get_share_link(page) -> str | None:
    """Try to get a share link for the notebook."""
    try:
        # Click Share
        if not await _try_selectors(page, SELECTORS["share_button"], timeout=5000):
            logger.warning("  Could not find Share button")
            return None

        await asyncio.sleep(2)

        # Try to set "Anyone with the link"
        await _try_selectors(page, SELECTORS["share_anyone"], timeout=5000)
        await asyncio.sleep(1)

        # Try to copy link
        if await _try_selectors(page, SELECTORS["copy_link"], timeout=5000):
            # Read from clipboard
            try:
                link = await page.evaluate("navigator.clipboard.readText()")
                nb_id = page.url.split("/notebook/")[-1].split("?")[0] if "/notebook/" in page.url else ""
                if link and ("notebook.google.com" in link or "notebooklm.google.com" in link) and (not nb_id or nb_id in link):
                    logger.info(f"  Share link: {link}")
                    return link
            except Exception:
                pass

        # Fallback: use the current page URL
        logger.info("  Could not get share link, using notebook URL")
        # Close share dialog
        await page.keyboard.press("Escape")
        return None

    except Exception as e:
        logger.warning(f"  Error getting share link: {e}")
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        return None


# ──── Ask a notebook (replaces notebooklm-mcp ask_question) ────
#
# Runs in its own tab of the shared browser, so any number of questions (and
# research calls) can run at once without a second browser touching the login.
# Gemini Notebook (Nov 2026): textarea[aria-label="Query box"] + Submit; each
# answer is a .to-user-message-card-content card whose copy/thumbs actions
# appear when it finishes; the "Thoughts" part is a <thinking-chain-view>.

_READ_ANSWER_TO = r"""(question) => {
    // Chat history renders after load, so anchor on OUR question: the answer is
    // the first assistant card after the last user message with this text.
    const norm = t => (t || '').replace(/\s+/g, ' ').trim();
    const q = norm(question);
    // User turns: .from-user-message-inner-content; answers: mat-card.to-user-message-card-content.
    // They never nest, so document order is the conversation order.
    const msgs = [...document.querySelectorAll('.from-user-message-inner-content, mat-card.to-user-message-card-content')];
    let asked = -1;
    msgs.forEach((e, i) => {
        if (e.matches('.from-user-message-inner-content') && norm(e.innerText).includes(q)) asked = i;
    });
    if (asked < 0) return {asked: false, text: '', done: false};
    const card = msgs.slice(asked + 1).find(e => e.matches('.to-user-message-card-content'));
    if (!card) return {asked: true, text: '', done: false};
    const body = card.querySelector('.message-text-content') || card;
    const clone = body.cloneNode(true);
    clone.querySelectorAll('thinking-chain-view, button, mat-icon').forEach(e => e.remove());
    const done = !!card.querySelector('.message-actions button, mat-card-actions button');
    return {asked: true, text: clone.innerText.trim(), done};
}"""


async def ask_notebook(notebook_url: str, question: str, timeout_s: int = 240) -> dict:
    """Ask one question in a NotebookLM notebook. Returns {"answer", "notebook_url", "error"}."""
    url = notebook_url.replace("notebooklm.google.com", "notebook.google.com")

    async def job(context, page):
        await page.goto(url, wait_until="domcontentloaded", timeout=config.BROWSER_TIMEOUT)
        box = page.locator('textarea[aria-label="Query box"]').first
        try:
            await box.wait_for(timeout=30000)
        except Exception:
            if not is_signed_in_url(page.url) or not await has_google_session(context):
                return {"answer": "", "error": "NotebookLM is signed out. Run notebooklm_login(interactive=true)."}
            await _take_screenshot(page, "ask_no_query_box")
            return {"answer": "", "error": f"No question box on {page.url} (bad notebook URL?)"}

        # The chat history renders after load and resets the box, so let it settle,
        # then send and confirm our question shows up in the conversation.
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await asyncio.sleep(2)
        submit = page.locator('query-box button[aria-label="Submit"]').first
        for attempt in range(3):
            await box.click()
            await box.fill(question)
            await asyncio.sleep(0.5)
            if (await box.input_value()).strip() != question.strip():
                continue
            for _ in range(40):  # disabled while sources are still processing
                if await submit.is_enabled():
                    break
                await asyncio.sleep(0.5)
            await submit.click(timeout=10000)
            sent = False
            for _ in range(20):
                await asyncio.sleep(0.5)
                if (await page.evaluate(_READ_ANSWER_TO, question, isolated_context=False))["asked"]:
                    sent = True
                    break
            if sent:
                break
        else:
            await _take_screenshot(page, "ask_not_sent")
            return {"answer": "", "error": "Could not send the question (box kept resetting)."}

        last_text, stable = "", 0
        while True:  # the outer timeout bounds this loop
            await asyncio.sleep(1.5)
            state = await page.evaluate(_READ_ANSWER_TO, question, isolated_context=False)
            if not state["text"]:
                continue
            stable = stable + 1 if state["text"] == last_text else 0
            last_text = state["text"]
            if state["done"] and stable >= 1:
                return {"answer": last_text, "error": None}

    try:
        result = await run_in_browser(job, timeout_s)
    except asyncio.TimeoutError:
        result = {"answer": "", "error": f"No complete answer within {timeout_s}s; that tab was closed."}
    except Exception as e:
        result = {"answer": "", "error": f"{type(e).__name__}: {e}"}
    result["notebook_url"] = url
    return result
