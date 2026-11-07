"""Keep the shared NotebookLM login alive.

The Google session lives in the shared browser's Chrome profile (see
browser_pool.py). notebooklm-mcp reports `authenticated=false` once
browser_state/state.json is older than 24h, so this module opens NotebookLM in
the shared browser, confirms there is no Google login redirect, and rewrites
state.json so both servers agree the session is good.

Run directly:
    .venv/bin/python3 -m src.auth_keepalive          # check + refresh state.json
    .venv/bin/python3 -m src.auth_keepalive --login  # sign in (opens a Chrome window)
    .venv/bin/python3 -m src.auth_keepalive --reap   # close the shared browser if idle
Exit codes: 0 = logged in + state refreshed, 2 = Google login required,
3 = browser error.
"""

import asyncio
import json
import logging
import subprocess
import sys
from datetime import datetime

from . import config
from .browser_pool import ensure_browser, has_google_session, reap_if_idle, run_in_browser, scrub_exported_session

logger = logging.getLogger("auto-research.keepalive")

NOTEBOOKLM_HOME = "https://notebook.google.com/"
LOGIN_URL = "https://accounts.google.com/ServiceLogin?continue=https%3A%2F%2Fnotebook.google.com%2F"
LOGIN_TIMEOUT_S = 15 * 60
CHECK_TIMEOUT_S = 120
STATUS_PATH = config.NOTEBOOKLM_DATA_DIR / "keepalive_status.json"


def is_signed_in_url(url: str) -> bool:
    """NotebookLM moved to notebook.google.com ("Gemini Notebook") in Oct 2026.

    Signed-out visitors land on /trynow; the old host just redirects.
    """
    return (
        url.startswith(("https://notebook.google.com/", "https://notebooklm.google.com/"))
        and "/trynow" not in url
    )


async def _check(context, page) -> dict:
    await page.goto(NOTEBOOKLM_HOME, wait_until="domcontentloaded", timeout=config.BROWSER_TIMEOUT)
    await asyncio.sleep(4)
    if not is_signed_in_url(page.url) or not await has_google_session(context):
        return {"ok": False, "login_required": True, "error": f"Not signed in to Google (page: {page.url})"}
    scrub_exported_session()
    return {"ok": True, "login_required": False, "error": None}


async def _login(context, page) -> dict:
    await page.bring_to_front()
    await page.goto(NOTEBOOKLM_HOME, wait_until="domcontentloaded", timeout=config.BROWSER_TIMEOUT)
    await asyncio.sleep(4)
    if not is_signed_in_url(page.url) or not await has_google_session(context):
        logger.info("Sign in to Google in the Chrome window (waiting up to 15 min)...")
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=config.BROWSER_TIMEOUT)
        # Done as soon as Google issues sign-in cookies, whatever page Google shows
        # next (security checkup, "verify it's you", account chooser...).
        for _ in range(LOGIN_TIMEOUT_S):
            if await has_google_session(context):
                break
            await asyncio.sleep(1)
        else:
            return {"ok": False, "login_required": True, "error": "Timed out waiting for Google sign-in"}
        await asyncio.sleep(3)
        await page.goto(NOTEBOOKLM_HOME, wait_until="domcontentloaded", timeout=config.BROWSER_TIMEOUT)
        await asyncio.sleep(4)
        if not is_signed_in_url(page.url):
            return {"ok": False, "login_required": True, "error": f"Signed in to Google but NotebookLM sent us to {page.url}"}
    scrub_exported_session()
    return {"ok": True, "login_required": False, "error": None}


def _write_status(result: dict) -> None:
    try:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATUS_PATH.write_text(json.dumps({**result, "checked_at": datetime.now().isoformat(timespec="seconds")}, indent=2))
    except OSError:
        pass


async def refresh_auth_state(interactive: bool = False) -> dict:
    """Open NotebookLM in the shared browser and re-save state.json.

    interactive=True shows a Chrome window and waits for the user to sign in
    (replaces notebooklm-mcp's setup_auth, which only recognises the old
    notebooklm.google.com host and so never completes). Afterwards the shared
    browser goes back to headless.

    Never fails because another call is running: every call gets its own tab.
    Returns {"ok": bool, "login_required": bool, "error": str | None}.
    """
    try:
        if interactive:
            result = await run_in_browser(_login, LOGIN_TIMEOUT_S + 120, headed=True)
        else:
            result = await run_in_browser(_check, CHECK_TIMEOUT_S)
    except asyncio.TimeoutError:
        result = {"ok": False, "login_required": False,
                  "error": "NotebookLM did not respond in time; that tab was closed. Retry."}
    except Exception as e:
        result = {"ok": False, "login_required": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        if interactive:
            # Back to an invisible browser for normal research calls.
            try:
                await asyncio.to_thread(ensure_browser, False, True)
            except Exception as e:
                logger.warning(f"Could not restart shared browser headless: {e}")
    _write_status(result)
    return result


def _notify(message: str) -> None:
    """macOS notification so a lost login gets noticed before the next research call."""
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{message}" with title "RECON / NotebookLM"'],
            timeout=10,
            check=False,
        )
    except Exception:
        pass


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if "--reap" in sys.argv:
        reap_if_idle()
        return 0
    result = asyncio.run(refresh_auth_state(interactive="--login" in sys.argv))

    if result["ok"]:
        logger.info(f"NotebookLM session valid (shared browser)")
        return 0
    if result["login_required"]:
        logger.error(f"Google login required: {result['error']}")
        _notify("NotebookLM login expired. In auto-research-mcp run: .venv/bin/python3 -m src.auth_keepalive --login")
        return 2
    logger.error(f"Keepalive failed: {result['error']}")
    return 3


if __name__ == "__main__":
    sys.exit(main())
