"""One shared, logged-in Chrome for every auto-research call.

Chrome lets only one process open a profile at a time. Before this module,
every research call launched its own Chrome on notebooklm-mcp's profile, so a
second call, a notebooklm-mcp query, or one hung browser made every other call
fail with "profile is open in another process".

Now one detached Chrome runs on auto-research's OWN profile with a local CDP
port. Every call, from any Claude session, any number at once, connects over
CDP and works in its own tab:

- launching is serialized with a file lock, so two calls never start two browsers
- each job gets a hard timeout; a hung tab is closed without touching other tabs
- a browser that stops answering is killed and relaunched automatically
- the profile is seeded once from notebooklm-mcp's logged-in profile, and every
  successful run writes fresh cookies back to notebooklm-mcp's state.json, which
  notebooklm-mcp loads when it falls back to an isolated profile
"""

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import plistlib
import shutil
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

from . import config

log = logging.getLogger("auto-research.browser")

HOME = Path.home() / "Library" / "Application Support" / "auto-research"
PROFILE = HOME / "chrome_profile"
PORT = int(os.getenv("RECON_CDP_PORT", "9322"))
CDP_URL = f"http://127.0.0.1:{PORT}"
PID_FILE = HOME / "browser.pid"
MODE_FILE = HOME / "browser.mode"  # "headless" or "headed"
LAUNCH_LOCK = HOME / "launch.lock"
LAST_USED = HOME / "last_used"
BROWSER_LOG = HOME / "browser.log"
CHROME_APP = Path("/Applications/Google Chrome.app")
CHROME_BIN = CHROME_APP / "Contents" / "MacOS" / "Google Chrome"


# ──── process helpers ────

def _cdp_alive(timeout: float = 3) -> bool:
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _cdp_pages() -> list[dict]:
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/list", timeout=3) as r:
            return [t for t in json.load(r) if t.get("type") == "page"]
    except Exception:
        return []


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _read_pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if _alive(pid) else None


def _kill(pid: int, grace: float = 5.0) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    end = time.monotonic() + grace
    while time.monotonic() < end:
        if not _alive(pid):
            return
        time.sleep(0.2)
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)


def _profile_holder(profile: Path) -> int | None:
    """PID of the Chrome holding a profile's SingletonLock, if it is still alive."""
    try:
        pid = int(os.readlink(profile / "SingletonLock").rsplit("-", 1)[1])
    except (OSError, ValueError, IndexError):
        return None
    return pid if _alive(pid) else None


def _clear_singletons(profile: Path) -> None:
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(profile / name)


def _chrome_major() -> str:
    try:
        with open(CHROME_APP / "Contents" / "Info.plist", "rb") as f:
            return plistlib.load(f)["CFBundleShortVersionString"].split(".")[0]
    except Exception:
        return "140"


def _touch(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


@contextlib.contextmanager
def _launch_lock(timeout: float):
    """Cross-process lock; the kernel drops it if the holder dies, so it can't go stale."""
    HOME.mkdir(parents=True, exist_ok=True)
    fd = os.open(LAUNCH_LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    end = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > end:
                    raise TimeoutError("Another call is still starting the shared browser")
                time.sleep(0.3)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# ──── launch ────

def _seed_profile() -> None:
    """First run only: copy notebooklm-mcp's logged-in profile so no new sign-in is needed."""
    if (PROFILE / "Default" / "Cookies").exists():
        return
    base = config.NOTEBOOKLM_CHROME_PROFILE
    HOME.mkdir(parents=True, exist_ok=True)
    if base.exists():
        log.info(f"Seeding shared browser profile from {base}")
        skip = lambda d, names: [n for n in names if n.startswith("Singleton") or n.endswith((".lock", ".tmp"))]
        shutil.copytree(base, PROFILE, symlinks=True, ignore=skip, dirs_exist_ok=True)
    else:
        PROFILE.mkdir(parents=True, exist_ok=True)


def _launch(headed: bool) -> None:
    """Start the detached shared Chrome. Caller holds the launch lock."""
    old = _read_pid()
    if old:
        _kill(old)
    # Nobody else uses this profile, so whatever holds it is a leftover of ours.
    holder = _profile_holder(PROFILE)
    if holder:
        _kill(holder)
    _clear_singletons(PROFILE)

    args = [
        str(CHROME_BIN),
        f"--user-data-dir={PROFILE}",
        f"--remote-debugging-port={PORT}",
        "--remote-debugging-address=127.0.0.1",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",  # the seeded profile carries personal extensions that open tabs
        "--disable-blink-features=AutomationControlled",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
        "--window-size=1280,900",
    ]
    if not headed:
        args += [
            "--headless=new",
            # Headless Chrome advertises "HeadlessChrome"; look like the normal browser.
            "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{_chrome_major()}.0.0.0 Safari/537.36",
        ]
    args.append("about:blank")

    with open(BROWSER_LOG, "ab") as logf:
        proc = subprocess.Popen(args, stdout=logf, stderr=logf, stdin=subprocess.DEVNULL, start_new_session=True)
    PID_FILE.write_text(str(proc.pid))
    MODE_FILE.write_text("headed" if headed else "headless")

    end = time.monotonic() + 30
    while time.monotonic() < end:
        if _cdp_alive(1):
            log.info(f"Shared browser up (pid {proc.pid}, {'headed' if headed else 'headless'}, port {PORT})")
            return
        if proc.poll() is not None:
            raise RuntimeError(f"Chrome exited with code {proc.returncode}; see {BROWSER_LOG}")
        time.sleep(0.3)
    _kill(proc.pid)
    raise RuntimeError("Chrome did not open its debugging port within 30s")


def ensure_browser(headed: bool = False, restart: bool = False) -> None:
    """Make sure the shared browser is running (blocking; call via asyncio.to_thread)."""
    if not restart and not headed and _cdp_alive():
        return
    with _launch_lock(timeout=120):
        mode = MODE_FILE.read_text().strip() if MODE_FILE.exists() else ""
        if not restart and _cdp_alive() and (not headed or mode == "headed"):
            return
        _seed_profile()
        _launch(headed)


def shutdown() -> None:
    with _launch_lock(timeout=60):
        pid = _read_pid()
        if pid:
            _kill(pid)
        _clear_singletons(PROFILE)


def reap_if_idle(idle_s: float = 30 * 60) -> bool:
    """Close the shared browser after a long idle stretch. True if it was closed."""
    pid = _read_pid()
    if not pid:
        return False
    try:
        idle = time.time() - LAST_USED.stat().st_mtime
    except OSError:
        idle = idle_s + 1
    busy = any(p.get("url", "about:blank") not in ("about:blank", "") for p in _cdp_pages())
    if idle < idle_s or busy:
        return False
    try:
        with _launch_lock(timeout=5):
            _kill(pid)
            _clear_singletons(PROFILE)
    except TimeoutError:
        return False
    log.info("Closed idle shared browser")
    return True


# ──── Google session: lives in this ONE browser only ────
#
# Never copy the login cookies anywhere else. Nov 7 2026: exporting them to
# notebooklm-mcp's state.json let notebooklm-mcp open a second browser with the
# same session, and Google signed the account out everywhere within minutes
# (it treats one session in two browsers as stolen cookies). Ask questions with
# auto-research's ask_notebook tool instead of notebooklm-mcp.

AUTH_COOKIES = {"SID", "__Secure-1PSID", "__Secure-3PSID"}


async def has_google_session(context) -> bool:
    """True if the browser holds real Google sign-in cookies (the URL alone can lie
    for a few seconds before NotebookLM redirects a signed-out visitor)."""
    try:
        cookies = await context.cookies(["https://accounts.google.com", "https://notebook.google.com"])
    except Exception:
        return False
    return any(c["name"] in AUTH_COOKIES for c in cookies)


def scrub_exported_session() -> None:
    """Remove Google auth cookies from notebooklm-mcp's state.json so no other
    browser can ever reuse (and get Google to revoke) this session."""
    state = config.NOTEBOOKLM_STATE_PATH
    try:
        data = json.loads(state.read_text())
    except (OSError, ValueError):
        return
    cookies = data.get("cookies") or []
    kept = [c for c in cookies if "google" not in c.get("domain", "") and "youtube" not in c.get("domain", "")]
    if len(kept) != len(cookies):
        data["cookies"] = kept
        state.write_text(json.dumps(data))
        log.info(f"Scrubbed {len(cookies) - len(kept)} Google cookies from {state}")


# ──── public API ────

@contextlib.asynccontextmanager
async def browser_page(headed: bool | None = None):
    """Yield (context, page): a fresh tab in the shared logged-in browser.

    Safe to use from many calls and processes at once. The tab is closed
    afterwards; the browser keeps running for the next call.
    """
    from patchright.async_api import async_playwright

    if headed is None:
        headed = not config.HEADLESS
    pw = await async_playwright().start()
    page = None
    try:
        for attempt in (1, 2):
            await asyncio.to_thread(ensure_browser, headed, attempt == 2)
            try:
                browser = await asyncio.wait_for(pw.chromium.connect_over_cdp(CDP_URL), 20)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = await asyncio.wait_for(context.new_page(), 20)
                break
            except Exception as e:
                if attempt == 2:
                    raise RuntimeError(f"Shared browser unreachable after restart: {e}") from e
                log.warning(f"Shared browser not responding ({e}); restarting it")
        _touch(LAST_USED)
        yield context, page
    finally:
        if page:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(page.close(), 10)
        _touch(LAST_USED)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(pw.stop(), 10)


async def run_in_browser(job, timeout_s: float, headed: bool | None = None):
    """Run job(context, page) in its own tab with a hard timeout.

    On timeout only this tab is closed; other running jobs are untouched.
    Raises asyncio.TimeoutError on timeout.
    """
    async with browser_page(headed=headed) as (context, page):
        return await asyncio.wait_for(job(context, page), timeout_s)
