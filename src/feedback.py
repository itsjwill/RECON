"""Feedback loop — track what works, learn from usage, compound the edge."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from . import config

logger = logging.getLogger("auto-research.feedback")

DATA_DIR = config.PROJECT_DIR / "data"
FEEDBACK_PATH = DATA_DIR / "feedback.json"


def _load_feedback() -> dict:
    """Load feedback data from disk."""
    if FEEDBACK_PATH.exists():
        try:
            return json.loads(FEEDBACK_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    return {
        "notebooks": {},
        "channels": {},
        "topics": {},
        "authors": {},
    }


def _save_feedback(data: dict) -> None:
    """Save feedback data to disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FEEDBACK_PATH.write_text(json.dumps(data, indent=2))


def record_research(notebook_id: str, topic: str, sources: list[dict]) -> None:
    """
    Record a completed research session.

    Called after research() successfully creates a notebook.
    """
    data = _load_feedback()
    now = datetime.now(timezone.utc).isoformat()

    # Record notebook
    data["notebooks"][notebook_id] = {
        "created": now,
        "topic": topic,
        "query_count": 0,
        "last_queried": None,
        "user_rating": None,
        "sources": [
            {
                "url": s.get("url", ""),
                "type": s.get("source_type", "youtube"),
                "channel": s.get("channel", ""),
                "title": s.get("title", ""),
            }
            for s in sources
        ],
    }

    # Record channels
    for s in sources:
        channel = s.get("channel", "")
        if channel and s.get("source_type", "youtube") == "youtube":
            if channel not in data["channels"]:
                data["channels"][channel] = {
                    "notebooks_appeared_in": 0,
                    "total_queries_on_notebooks": 0,
                    "avg_notebook_rating": None,
                    "ratings": [],
                }
            data["channels"][channel]["notebooks_appeared_in"] += 1

    # Record authors (for papers)
    for s in sources:
        if s.get("source_type") == "paper":
            for author in s.get("authors", []):
                if author not in data["authors"]:
                    data["authors"][author] = {
                        "papers_used": 0,
                        "total_citations": 0,
                    }
                data["authors"][author]["papers_used"] += 1
                data["authors"][author]["total_citations"] += s.get("citations", 0)

    # Record topics
    # Split topic into individual words and 2-word phrases
    topic_lower = topic.lower()
    words = topic_lower.split()

    # Full topic
    if topic_lower not in data["topics"]:
        data["topics"][topic_lower] = {"count": 0, "last_researched": None}
    data["topics"][topic_lower]["count"] += 1
    data["topics"][topic_lower]["last_researched"] = now

    # Individual words (for cross-topic linking)
    for word in words:
        if len(word) > 3:  # Skip short words
            if word not in data["topics"]:
                data["topics"][word] = {"count": 0, "last_researched": None}
            data["topics"][word]["count"] += 1
            data["topics"][word]["last_researched"] = now

    _save_feedback(data)
    logger.info(f"Recorded research: {notebook_id} ({topic})")


def record_query(notebook_id: str) -> None:
    """Record that a notebook was queried."""
    data = _load_feedback()
    now = datetime.now(timezone.utc).isoformat()

    if notebook_id in data["notebooks"]:
        data["notebooks"][notebook_id]["query_count"] += 1
        data["notebooks"][notebook_id]["last_queried"] = now

        # Update channel query counts
        for source in data["notebooks"][notebook_id].get("sources", []):
            channel = source.get("channel", "")
            if channel and channel in data["channels"]:
                data["channels"][channel]["total_queries_on_notebooks"] += 1

        _save_feedback(data)


def rate_notebook(notebook_id: str, rating: int) -> dict:
    """
    Rate a research notebook 1-5.

    Returns status dict.
    """
    if not 1 <= rating <= 5:
        return {"error": "Rating must be 1-5"}

    data = _load_feedback()

    if notebook_id not in data["notebooks"]:
        return {"error": f"Notebook '{notebook_id}' not found in feedback data"}

    data["notebooks"][notebook_id]["user_rating"] = rating

    # Update channel ratings
    for source in data["notebooks"][notebook_id].get("sources", []):
        channel = source.get("channel", "")
        if channel and channel in data["channels"]:
            ch = data["channels"][channel]
            if "ratings" not in ch:
                ch["ratings"] = []
            ch["ratings"].append(rating)
            ch["avg_notebook_rating"] = round(
                sum(ch["ratings"]) / len(ch["ratings"]), 1
            )

    _save_feedback(data)
    logger.info(f"Rated {notebook_id}: {rating}/5")

    return {
        "status": "success",
        "notebook_id": notebook_id,
        "rating": rating,
    }


def get_channel_boost(channel: str) -> float:
    """
    Get a scoring boost for a channel based on historical performance.

    Returns 0.0 to 0.2 boost.

    Channels score higher when:
    - They appear in multiple notebooks (breadth)
    - Those notebooks get queried often (usefulness)
    - Those notebooks get high ratings (quality)
    """
    data = _load_feedback()

    if channel not in data.get("channels", {}):
        return 0.0

    ch = data["channels"][channel]
    appearances = ch.get("notebooks_appeared_in", 0)
    queries = ch.get("total_queries_on_notebooks", 0)
    avg_rating = ch.get("avg_notebook_rating")

    # Appearance score (max 0.06)
    appearance_boost = min(0.06, appearances * 0.02)

    # Query score (max 0.08)
    query_boost = min(0.08, queries * 0.005)

    # Rating score (max 0.06)
    if avg_rating and avg_rating >= 4:
        rating_boost = 0.06
    elif avg_rating and avg_rating >= 3:
        rating_boost = 0.03
    else:
        rating_boost = 0.0

    total = appearance_boost + query_boost + rating_boost
    return round(min(0.2, total), 3)


def get_topic_history() -> list[dict]:
    """
    Get research topic history, sorted by frequency.

    Returns list of {topic, count, last_researched}.
    """
    data = _load_feedback()
    topics = data.get("topics", {})

    result = [
        {
            "topic": topic,
            "count": info["count"],
            "last_researched": info.get("last_researched"),
        }
        for topic, info in topics.items()
    ]

    result.sort(key=lambda x: x["count"], reverse=True)
    return result


def get_stale_notebooks(trading_days: int = 14, general_days: int = 30) -> list[dict]:
    """
    Find research notebooks that are getting stale and need refreshing.

    Trading research decays faster (alpha gets arbed out).
    General research stays relevant longer.

    Returns list of stale notebooks with staleness info.
    """
    data = _load_feedback()
    now = datetime.now(timezone.utc)
    stale = []

    # Trading keywords — if the topic contains these, it's trading research
    trading_keywords = {
        "trading", "trade", "scalping", "order flow", "strategy", "backtest",
        "indicator", "setup", "entry", "exit", "momentum", "mean reversion",
        "arbitrage", "funding", "liquidation", "leverage", "futures", "options",
        "crypto", "bitcoin", "ethereum", "defi", "alpha", "edge", "signal",
        "bot", "algorithm", "quant", "market making", "spread", "volatility",
        "rsi", "macd", "vwap", "ofi", "vpin", "tape reading", "price action",
    }

    for nb_id, nb_info in data.get("notebooks", {}).items():
        created = nb_info.get("created", "")
        topic = nb_info.get("topic", "").lower()

        if not created:
            continue

        try:
            created_date = datetime.fromisoformat(created)
            age_days = (now - created_date).days
        except (ValueError, TypeError):
            continue

        # Determine if this is trading research
        is_trading = any(kw in topic for kw in trading_keywords)
        threshold = trading_days if is_trading else general_days

        if age_days >= threshold:
            stale.append({
                "notebook_id": nb_id,
                "topic": nb_info.get("topic", ""),
                "created": created,
                "age_days": age_days,
                "threshold_days": threshold,
                "is_trading": is_trading,
                "staleness": round(age_days / threshold, 1),  # 1.0 = just stale, 2.0 = very stale
                "query_count": nb_info.get("query_count", 0),
                "user_rating": nb_info.get("user_rating"),
            })

    # Sort by staleness (most stale first), but prioritize high-usage stale notebooks
    stale.sort(key=lambda x: (x["query_count"] > 0, x["staleness"]), reverse=True)
    return stale


def get_feedback_summary() -> dict:
    """Get a summary of all feedback data."""
    data = _load_feedback()

    total_notebooks = len(data.get("notebooks", {}))
    rated = [
        nb for nb in data.get("notebooks", {}).values()
        if nb.get("user_rating") is not None
    ]
    avg_rating = (
        round(sum(nb["user_rating"] for nb in rated) / len(rated), 1)
        if rated else None
    )

    top_channels = sorted(
        data.get("channels", {}).items(),
        key=lambda x: x[1].get("total_queries_on_notebooks", 0),
        reverse=True,
    )[:5]

    return {
        "total_notebooks": total_notebooks,
        "total_rated": len(rated),
        "avg_rating": avg_rating,
        "top_channels": [
            {"channel": name, "queries": info.get("total_queries_on_notebooks", 0)}
            for name, info in top_channels
        ],
        "total_topics": len(data.get("topics", {})),
    }
