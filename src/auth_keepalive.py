"""Keep the shared NotebookLM login alive.

notebooklm-mcp stores the real Google session in a persistent Chrome profile
(cookies valid ~13 months), but it reports `authenticated=false` once
browser_state/state.json is older than 24h, and auto-research used to refuse
to run when that file was missing. This module opens the profile headless,
confirms NotebookLM loads without a Google login redirect, and rewrites
state.json so both servers agree the session is good.

Run directly (launchd does this every 6h):
    .venv/bin/python3 -m src.auth_keepalive
Sign in again (opens a Chrome window):
    .venv/bin/python3 -m src.auth_keepalive --login
Exit codes: 0 = logged in + state refreshed, 2 = Google login required,
3 = profile busy / browser error.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import datetime

from . import config

logger = logging.getLogger("auto-research.keepalive")

NOTEBOOKLM_HOME = "https://notebook.google.com/"
LOGIN_URL = "https://accounts.google.com/ServiceLogin?continue=https%3A%2F%2Fnotebook.google.com%2F"
LOGIN_TIMEOUT_S = 15 * 60
STATUS_PATH = config.NOTEBOOKLM_DATA_DIR / "keepalive_status.json"


def is_signed_in_url(url: str) -> bool:
    """NotebookLM moved to notebook.google.com ("Gemini Notebook") in Oct 2026.

    Signed-out visitors land on /trynow; the old host just redirects.
    """
    return (
        url.startswith(("https://notebook.google.com/", "https://notebooklm.google.com/"))
        and "/trynow" not in url
    )


def clear_stale_profile_lock() -> bool:
    """Remove Chrome Singleton* files left behind by a crashed browser.

    Returns True if the profile is free to open (no live process holds it).
    """
    profile = config.NOTEBOOKLM_CHROME_PROFILE
    lock = profile / "SingletonLock"
    if not os.path.islink(lock):
        return True
    # Lock target looks like "<hostname>-<pid>"
    target = os.readlink(lock)
    try:
        pid = int(target.rsplit("-", 1)[1])
        os.kill(pid, 0)
        logger.warning(f"Profile in use by live pid {pid}")
        return False
    except (ValueError, IndexError, ProcessLookupError):
        pass
    except PermissionError:
        return False
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            os.unlink(profile / name)
        except FileNotFoundError:
            pass
    logger.info(f"Cleared stale profile lock ({target})")
    return True


async def launch_shared_profile(pw, headless: bool):
    """Open the notebooklm-mcp Chrome profile.

    Uses installed Google Chrome first: the profile is written by Chrome, and
    the bundled Playwright Chromium lives in a cache dir that gets wiped.
    """
    options = dict(
        user_data_dir=str(config.NOTEBOOKLM_CHROME_PROFILE),
        headless=headless,
        viewport={"width": 1280, "height": 900},
        locale="en-US",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    )
    try:
        return await pw.chromium.launch_persistent_context(channel=config.BROWSER_CHANNEL, **options)
    except Exception as e:
        if config.BROWSER_CHANNEL == "chromium":
            raise
        logger.warning(f"Chrome channel '{config.BROWSER_CHANNEL}' failed ({e}); falling back to chromium")
        return await pw.chromium.launch_persistent_context(channel="chromium", **options)


async def _save_state(context, page) -> None:
    """Write state.json + session.json where notebooklm-mcp expects them."""
    state_path = config.NOTEBOOKLM_STATE_PATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(state_path))
    session_data = await page.evaluate(
        "() => { const o = {}; for (let i = 0; i < sessionStorage.length; i++) {"
        " const k = sessionStorage.key(i); o[k] = sessionStorage.getItem(k); } return o; }"
    )
    (state_path.parent / "session.json").write_text(json.dumps(session_data))


async def refresh_auth_state(interactive: bool = False) -> dict:
    """Open NotebookLM with the shared profile and re-save state.json.

    interactive=True shows a Chrome window and waits for the user to sign in
    (replaces notebooklm-mcp's setup_auth, which only recognises the old
    notebooklm.google.com host and so never completes).

    Returns {"ok": bool, "login_required": bool, "error": str | None}.
    """
    if not clear_stale_profile_lock():
        return {"ok": False, "login_required": False, "error": "Chrome profile is in use by another process"}

    from patchright.async_api import async_playwright

    pw = await async_playwright().start()
    context = None
    try:
        context = await launch_shared_profile(pw, headless=not interactive)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(NOTEBOOKLM_HOME, wait_until="domcontentloaded", timeout=config.BROWSER_TIMEOUT)
        await asyncio.sleep(4)

        if not is_signed_in_url(page.url):
            if not interactive:
                return {"ok": False, "login_required": True, "error": f"Redirected to {page.url}"}
            logger.info("Sign in to Google in the Chrome window (waiting up to 15 min)...")
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=config.BROWSER_TIMEOUT)
            for _ in range(LOGIN_TIMEOUT_S):
                if is_signed_in_url(page.url):
                    break
                await asyncio.sleep(1)
            else:
                return {"ok": False, "login_required": True, "error": "Timed out waiting for Google sign-in"}
            await asyncio.sleep(3)

        await _save_state(context, page)
        return {"ok": True, "login_required": False, "error": None}
    except Exception as e:
        return {"ok": False, "login_required": False, "error": str(e)}
    finally:
        if context:
            await context.close()
        await pw.stop()


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
    result = asyncio.run(refresh_auth_state(interactive="--login" in sys.argv))
    result["checked_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        STATUS_PATH.write_text(json.dumps(result, indent=2))
    except OSError:
        pass

    if result["ok"]:
        logger.info(f"NotebookLM session valid, state refreshed: {config.NOTEBOOKLM_STATE_PATH}")
        return 0
    if result["login_required"]:
        logger.error(f"Google login required: {result['error']}")
        _notify("NotebookLM login expired. In auto-research-mcp run: .venv/bin/python3 -m src.auth_keepalive --login")
        return 2
    logger.error(f"Keepalive failed: {result['error']}")
    return 3


if __name__ == "__main__":
    sys.exit(main())
