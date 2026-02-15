"""Podcast Index API search — free, no rate limits."""

import hashlib
import logging
import math
import time
from datetime import datetime, timezone

import requests

from . import config

logger = logging.getLogger("auto-research.podcasts")

PODCAST_INDEX_BASE = "https://api.podcastindex.org/api/1.0"


def _get_auth_headers() -> dict | None:
    """Build Podcast Index API auth headers (HMAC-SHA1)."""
    api_key = config.PODCAST_INDEX_KEY
    api_secret = config.PODCAST_INDEX_SECRET

    if not api_key or not api_secret:
        return None

    epoch_time = int(time.time())
    data_to_hash = api_key + api_secret + str(epoch_time)
    sha1_hash = hashlib.sha1(data_to_hash.encode("utf-8")).hexdigest()

    return {
        "User-Agent": "auto-research-mcp/0.2.0",
        "X-Auth-Key": api_key,
        "X-Auth-Date": str(epoch_time),
        "Authorization": sha1_hash,
    }


def search_podcasts(query: str, limit: int = 10) -> dict:
    """
    Search Podcast Index for episodes on a topic.

    Returns:
        {
            "query": str,
            "episodes": [{title, url, podcast_name, duration_min, published, description, score, source_type}],
            "total_results": int,
        }
    """
    headers = _get_auth_headers()
    if headers is None:
        return {
            "query": query,
            "episodes": [],
            "total_results": 0,
            "error": "Podcast Index API keys not configured. Set PODCAST_INDEX_KEY and PODCAST_INDEX_SECRET in .env",
        }

    try:
        # Search for episodes by keyword
        resp = requests.get(
            f"{PODCAST_INDEX_BASE}/search/byterm",
            params={"q": query, "max": min(limit * 3, 60)},
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        feeds = data.get("feeds", [])

        # For each matching podcast, get recent episodes
        episodes = []
        for feed in feeds[:10]:  # Top 10 podcasts
            feed_episodes = _get_feed_episodes(feed, query, headers)
            episodes.extend(feed_episodes)

        # Score and sort
        for ep in episodes:
            ep["score"] = _score_episode(ep, query)

        episodes.sort(key=lambda x: x["score"], reverse=True)
        episodes = episodes[:limit]

        return {
            "query": query,
            "episodes": episodes,
            "total_results": len(episodes),
        }

    except Exception as e:
        logger.error(f"Podcast search failed: {e}")
        return {"query": query, "episodes": [], "total_results": 0, "error": str(e)}


def _get_feed_episodes(feed: dict, query: str, headers: dict) -> list[dict]:
    """Get recent episodes from a podcast feed matching the query."""
    feed_id = feed.get("id")
    podcast_name = feed.get("title", "")
    episode_count = feed.get("episodeCount", 0)

    if not feed_id or episode_count < 10:
        return []  # Skip shows with very few episodes

    try:
        resp = requests.get(
            f"{PODCAST_INDEX_BASE}/episodes/byfeedid",
            params={"id": feed_id, "max": 20},
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        episodes = []
        query_lower = query.lower()

        for item in data.get("items", []):
            title = item.get("title", "")
            description = item.get("description", "")
            duration = item.get("duration", 0)
            duration_min = duration / 60 if duration else 0

            # Filter: 15-120 minutes, title or description matches query
            if duration_min < 15 or duration_min > 120:
                continue

            title_lower = title.lower()
            desc_lower = description.lower()
            query_words = query_lower.split()
            if not any(w in title_lower or w in desc_lower for w in query_words):
                continue

            # Get the best URL (prefer enclosure/audio, then link)
            url = item.get("enclosureUrl", "") or item.get("link", "")

            # For NotebookLM, we need a web page URL, not audio
            # Use the episode link if available
            web_url = item.get("link", "") or url

            published = item.get("datePublished", 0)
            pub_date = ""
            if published:
                try:
                    pub_date = datetime.fromtimestamp(
                        published, tz=timezone.utc
                    ).isoformat()
                except (ValueError, OSError):
                    pass

            episodes.append({
                "title": title,
                "url": web_url,
                "podcast_name": podcast_name,
                "duration_min": round(duration_min, 1),
                "published": pub_date,
                "description": description[:300],
                "episode_count": episode_count,
                "source_type": "podcast",
            })

        return episodes

    except Exception as e:
        logger.warning(f"Failed to get episodes for {podcast_name}: {e}")
        return []


def _score_episode(episode: dict, query: str) -> float:
    """
    Score a podcast episode 0-100.

    Factors:
    - Relevance (35%): title/description keyword match
    - Show quality (25%): episode count, established show
    - Recency (25%): newer episodes score higher
    - Duration (15%): sweet spot 30-90 minutes
    """
    # Relevance
    query_words = set(query.lower().split())
    title_words = set(episode.get("title", "").lower().split())
    desc_words = set(episode.get("description", "").lower().split())
    all_words = title_words | desc_words

    if query_words:
        matches = sum(1 for w in query_words if w in all_words)
        relevance = matches / len(query_words)
        if query.lower() in episode.get("title", "").lower():
            relevance = min(1.0, relevance + 0.3)
    else:
        relevance = 0.5

    # Show quality
    ep_count = episode.get("episode_count", 0)
    if ep_count >= 200:
        quality = 1.0
    elif ep_count >= 100:
        quality = 0.8
    elif ep_count >= 50:
        quality = 0.6
    elif ep_count >= 20:
        quality = 0.4
    else:
        quality = 0.2

    # Recency
    published = episode.get("published", "")
    if published:
        try:
            pub_date = datetime.fromisoformat(published)
            age_days = (datetime.now(timezone.utc) - pub_date).days
            if age_days <= 90:
                recency = 1.0
            elif age_days <= 365:
                recency = 0.7
            elif age_days <= 730:
                recency = 0.4
            else:
                recency = 0.2
        except (ValueError, TypeError):
            recency = 0.5
    else:
        recency = 0.5

    # Duration sweet spot (30-90 min is ideal)
    duration = episode.get("duration_min", 0)
    if 30 <= duration <= 90:
        duration_score = 1.0
    elif 15 <= duration <= 120:
        duration_score = 0.6
    else:
        duration_score = 0.3

    total = (
        relevance * 0.35
        + quality * 0.25
        + recency * 0.25
        + duration_score * 0.15
    ) * 100

    return round(total, 1)
