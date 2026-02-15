"""Configuration for Auto-Research MCP v2."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
PROJECT_DIR = Path(__file__).parent.parent
load_dotenv(PROJECT_DIR / ".env")

# ──── API Keys ────
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
PODCAST_INDEX_KEY = os.getenv("PODCAST_INDEX_KEY", "")
PODCAST_INDEX_SECRET = os.getenv("PODCAST_INDEX_SECRET", "")

# ──── NotebookLM MCP shared paths ────
NOTEBOOKLM_DATA_DIR = Path.home() / "Library" / "Application Support" / "notebooklm-mcp"
NOTEBOOKLM_STATE_PATH = NOTEBOOKLM_DATA_DIR / "browser_state" / "state.json"
NOTEBOOKLM_LIBRARY_PATH = NOTEBOOKLM_DATA_DIR / "library.json"

# ──── Browser settings ────
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
BROWSER_TIMEOUT = 60_000  # 60s for general navigation
SOURCE_ADD_TIMEOUT = 120_000  # 120s per source (transcription is slow)

# ──── YouTube search defaults ────
DEFAULT_NUM_VIDEOS = 5
MAX_NUM_VIDEOS = 10
MIN_VIEWS = 1_000
MIN_DURATION_MINUTES = 5
MAX_DURATION_MINUTES = 240

# ──── Scoring weights (v1 — used as reference, v2 weights are in youtube_search.py) ────
WEIGHT_VIEWS = 0.25
WEIGHT_CHANNEL = 0.20
WEIGHT_RELEVANCE = 0.25
WEIGHT_RECENCY = 0.15
WEIGHT_ENGAGEMENT = 0.15

# ──── Debug ────
DEBUG_SCREENSHOT_DIR = Path("/tmp/auto-research-debug")
