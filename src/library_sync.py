"""Read/write the shared NotebookLM MCP library.json."""

import fcntl
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from . import config

logger = logging.getLogger("auto-research.library")


def _generate_id(name: str, existing_ids: set[str]) -> str:
    """Generate a slug ID matching NotebookLM MCP's pattern."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30]
    if slug not in existing_ids:
        return slug
    # Append counter for uniqueness
    for i in range(2, 100):
        candidate = f"{slug[:27]}-{i}"
        if candidate not in existing_ids:
            return candidate
    return f"{slug[:20]}-{hash(name) % 10000}"


def read_library() -> dict:
    """Read the shared library.json. Returns empty structure if not found."""
    path = config.NOTEBOOKLM_LIBRARY_PATH
    if not path.exists():
        return {"notebooks": [], "active_notebook_id": None, "last_modified": None, "version": "1.0.0"}

    with open(path, "r") as f:
        return json.load(f)


def _write_library(data: dict) -> None:
    """Write library.json with file locking for safe concurrent access."""
    path = config.NOTEBOOKLM_LIBRARY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    data["last_modified"] = datetime.now(timezone.utc).isoformat()

    with open(path, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            json.dump(data, f, indent=2)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def add_notebook_to_library(
    url: str,
    name: str,
    description: str,
    topics: list[str],
    videos: list[dict] | None = None,
) -> str:
    """
    Add a research notebook to the shared library.

    Returns the generated notebook ID.
    """
    library = read_library()
    existing_ids = {nb["id"] for nb in library["notebooks"]}
    notebook_id = _generate_id(name, existing_ids)

    now = datetime.now(timezone.utc).isoformat()

    # Build use cases from video info
    use_cases = ["Auto-research from YouTube"]
    if videos:
        channels = [v.get("channel", "") for v in videos[:3] if v.get("channel")]
        if channels:
            use_cases.append(f"Sources from: {', '.join(channels)}")

    entry = {
        "id": notebook_id,
        "url": url,
        "name": name,
        "description": description,
        "topics": topics,
        "content_types": ["youtube-videos", "auto-research"],
        "use_cases": use_cases,
        "added_at": now,
        "last_used": now,
        "use_count": 0,
        "tags": ["auto-research"],
    }

    library["notebooks"].append(entry)
    _write_library(library)

    logger.info(f"Added notebook '{name}' to library as '{notebook_id}'")
    return notebook_id


def list_research_notebooks() -> list[dict]:
    """List only notebooks created by auto-research (tagged 'auto-research')."""
    library = read_library()
    return [
        {
            "id": nb["id"],
            "name": nb["name"],
            "url": nb["url"],
            "topics": nb.get("topics", []),
            "added_at": nb.get("added_at", ""),
            "use_count": nb.get("use_count", 0),
        }
        for nb in library["notebooks"]
        if "auto-research" in nb.get("tags", [])
    ]
