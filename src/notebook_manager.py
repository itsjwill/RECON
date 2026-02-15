"""Playwright browser automation for NotebookLM notebook creation + source addition."""

import asyncio
import logging
import os
from datetime import datetime

from . import config

logger = logging.getLogger("auto-research.notebook")

# Selector cascades — try each in order until one works
SELECTORS = {
    # "New notebook" button on the home page
    "new_notebook": [
        'button:has-text("New notebook")',
        'button:has-text("Create")',
        'button:has-text("New")',
        '[aria-label="Create new notebook"]',
        '[aria-label="New notebook"]',
        'a:has-text("New notebook")',
    ],
    # "Add source" button inside a notebook
    "add_source": [
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
    path = config.DEBUG_SCREENSHOT_DIR / f"{ts}_{name}.png"
    try:
        await page.screenshot(path=str(path))
        logger.info(f"  Screenshot saved: {path}")
    except Exception as e:
        logger.warning(f"  Screenshot failed: {e}")
    return str(path)


async def create_notebook_with_sources(topic: str, video_urls: list[str]) -> dict:
    """
    Create a new NotebookLM notebook and add YouTube URLs as sources.

    Returns:
        {
            "notebook_url": str,  # The notebook URL (or share link)
            "videos_added": list[str],
            "videos_failed": list[str],
            "error": str | None,
        }
    """
    # Check auth state exists
    state_path = str(config.NOTEBOOKLM_STATE_PATH)
    if not os.path.exists(state_path):
        return {
            "notebook_url": "",
            "videos_added": [],
            "videos_failed": video_urls,
            "error": "NotebookLM auth state not found. Run 'setup_auth' via the NotebookLM MCP first.",
        }

    from patchright.async_api import async_playwright

    pw = None
    browser = None
    videos_added = []
    videos_failed = []
    notebook_url = ""

    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=config.HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-first-run",
            ],
        )

        # Use non-persistent context with saved auth cookies
        # This avoids Chrome profile lock conflicts with the NotebookLM MCP
        context = await browser.new_context(
            storage_state=state_path,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = await context.new_page()

        # Step 1: Navigate to NotebookLM home
        logger.info("Navigating to NotebookLM...")
        await page.goto("https://notebooklm.google.com/", wait_until="domcontentloaded", timeout=config.BROWSER_TIMEOUT)
        await asyncio.sleep(3)

        # Check if we landed on a login page
        current_url = page.url
        if "accounts.google.com" in current_url:
            await _take_screenshot(page, "auth_redirect")
            return {
                "notebook_url": "",
                "videos_added": [],
                "videos_failed": video_urls,
                "error": "Redirected to Google login. Auth cookies may be expired. Run 'setup_auth' via NotebookLM MCP.",
            }

        # Also try to restore sessionStorage
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
            return {
                "notebook_url": "",
                "videos_added": [],
                "videos_failed": video_urls,
                "error": "Could not find 'New notebook' button. Check screenshot at /tmp/auto-research-debug/",
            }

        # Wait for notebook page to load (URL changes to /notebook/<uuid>)
        logger.info("  Waiting for notebook to load...")
        try:
            await page.wait_for_url("**/notebook/**", timeout=20000)
        except Exception:
            await asyncio.sleep(5)  # Extra wait
            if "/notebook/" not in page.url:
                await _take_screenshot(page, "notebook_load_fail")
                return {
                    "notebook_url": "",
                    "videos_added": [],
                    "videos_failed": video_urls,
                    "error": "Notebook page did not load. Check screenshot.",
                }

        notebook_url = page.url
        logger.info(f"  Notebook created: {notebook_url}")
        await asyncio.sleep(3)
        await _take_screenshot(page, "notebook_created")

        # Step 3: Add each YouTube URL as a source
        for i, url in enumerate(video_urls):
            logger.info(f"Adding source {i+1}/{len(video_urls)}: {url}")
            success = await _add_youtube_source(page, url, attempt=1)
            if success:
                videos_added.append(url)
                logger.info(f"  Source {i+1} added successfully")
            else:
                # Retry once
                logger.warning(f"  Retrying source {i+1}...")
                await asyncio.sleep(3)
                success = await _add_youtube_source(page, url, attempt=2)
                if success:
                    videos_added.append(url)
                else:
                    videos_failed.append(url)
                    logger.error(f"  Source {i+1} failed after retry")

            # Delay between sources
            if i < len(video_urls) - 1:
                await asyncio.sleep(3)

        await _take_screenshot(page, "sources_added")

        # Step 4: Try to get share link
        share_url = await _get_share_link(page)
        if share_url:
            notebook_url = share_url

        logger.info(f"Done. Added {len(videos_added)}/{len(video_urls)} sources.")

    except Exception as e:
        logger.error(f"Browser automation error: {e}")
        if page:
            await _take_screenshot(page, "error")
        return {
            "notebook_url": notebook_url,
            "videos_added": videos_added,
            "videos_failed": videos_failed + [u for u in video_urls if u not in videos_added and u not in videos_failed],
            "error": str(e),
        }

    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()

    return {
        "notebook_url": notebook_url,
        "videos_added": videos_added,
        "videos_failed": videos_failed,
        "error": None,
    }


async def _add_youtube_source(page, url: str, attempt: int = 1) -> bool:
    """Add a single YouTube URL as a source. Returns True on success."""
    try:
        # Click "Add source"
        if not await _try_selectors(page, SELECTORS["add_source"], timeout=10000):
            await _take_screenshot(page, f"add_source_btn_fail_{attempt}")
            return False

        await asyncio.sleep(2)

        # Try YouTube option first, then Link/Website option
        found_type = False
        if await _try_selectors(page, SELECTORS["youtube_option"], timeout=5000):
            found_type = True
        elif await _try_selectors(page, SELECTORS["link_option"], timeout=5000):
            found_type = True

        if not found_type:
            # Maybe URL input is shown directly without type selection
            logger.info("  No source type selector found, trying URL input directly...")

        await asyncio.sleep(1)

        # Find and fill the URL input
        if not await _try_selectors(page, SELECTORS["url_input"], action="fill", text=url, timeout=8000):
            # Try typing instead of fill
            if not await _try_selectors(page, SELECTORS["url_input"], action="type", text=url, timeout=5000):
                await _take_screenshot(page, f"url_input_fail_{attempt}")
                # Try pressing Escape to close dialog and return
                await page.keyboard.press("Escape")
                await asyncio.sleep(1)
                return False

        await asyncio.sleep(1)

        # Click Insert/Add
        if not await _try_selectors(page, SELECTORS["insert_button"], timeout=5000):
            # Try pressing Enter as fallback
            await page.keyboard.press("Enter")

        # Wait for processing (transcription can take a while)
        logger.info(f"  Waiting for source to process (up to {config.SOURCE_ADD_TIMEOUT // 1000}s)...")
        await asyncio.sleep(10)  # Initial wait for dialog to close

        # Wait for the source to appear in the sidebar or for the dialog to close
        # We check periodically if we're back to the main notebook view
        for _ in range(config.SOURCE_ADD_TIMEOUT // 5000):
            # Check if dialog is gone (we're back to main view)
            try:
                dialog = page.locator('[role="dialog"]')
                if not await dialog.is_visible(timeout=1000):
                    logger.info("  Dialog closed — source likely processing")
                    break
            except Exception:
                break
            await asyncio.sleep(5)

        await asyncio.sleep(3)
        return True

    except Exception as e:
        logger.error(f"  Error adding source: {e}")
        await _take_screenshot(page, f"add_source_error_{attempt}")
        # Try to dismiss any open dialogs
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
                if link and "notebooklm.google.com" in link:
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
