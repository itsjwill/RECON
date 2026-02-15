"""Edge Tracker — close the loop between research and real results.

The missing piece: track which research sessions actually produced
profitable trading edge, then use that signal to boost similar
sources/channels/topics in future research.

This is what nobody else has. Research → Implement → Results → Better Research.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from . import config

logger = logging.getLogger("auto-research.edge")

DATA_DIR = config.PROJECT_DIR / "data"
EDGE_PATH = DATA_DIR / "edge_tracker.json"


def _load_edge_data() -> dict:
    """Load edge tracking data from disk."""
    if EDGE_PATH.exists():
        try:
            return json.loads(EDGE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    return {
        "edges": [],  # Individual edge records
        "channel_edge": {},  # Channel → edge stats
        "topic_edge": {},  # Topic → edge stats
        "source_type_edge": {},  # youtube/paper/podcast → edge stats
    }


def _save_edge_data(data: dict) -> None:
    """Save edge tracking data to disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EDGE_PATH.write_text(json.dumps(data, indent=2))


def track_edge(notebook_id: str, result: str, notes: str = "") -> dict:
    """
    Record whether a research session produced real edge.

    Args:
        notebook_id: The notebook that was researched
        result: "edge" (profitable), "no_edge" (neutral), "negative_edge" (lost money)
        notes: Optional notes about what worked/didn't

    Returns:
        Status dict with updated edge stats.
    """
    valid_results = {"edge", "no_edge", "negative_edge"}
    if result not in valid_results:
        return {"error": f"result must be one of: {valid_results}"}

    data = _load_edge_data()
    now = datetime.now(timezone.utc).isoformat()

    # Load feedback to get notebook details
    from .feedback import _load_feedback
    feedback = _load_feedback()
    notebook_info = feedback.get("notebooks", {}).get(notebook_id, {})

    if not notebook_info:
        return {"error": f"Notebook '{notebook_id}' not found in feedback data. Use list_research_notebooks to find the ID."}

    topic = notebook_info.get("topic", "unknown")
    sources = notebook_info.get("sources", [])

    # Record the edge event
    edge_record = {
        "notebook_id": notebook_id,
        "topic": topic,
        "result": result,
        "notes": notes,
        "timestamp": now,
        "sources": [
            {"channel": s.get("channel", ""), "type": s.get("type", "youtube")}
            for s in sources
        ],
    }
    data["edges"].append(edge_record)

    # Update channel edge stats
    for source in sources:
        channel = source.get("channel", "")
        if not channel:
            continue

        if channel not in data["channel_edge"]:
            data["channel_edge"][channel] = {
                "edge_count": 0,
                "no_edge_count": 0,
                "negative_count": 0,
                "edge_rate": 0.0,
            }

        stats = data["channel_edge"][channel]
        if result == "edge":
            stats["edge_count"] += 1
        elif result == "no_edge":
            stats["no_edge_count"] += 1
        else:
            stats["negative_count"] += 1

        total = stats["edge_count"] + stats["no_edge_count"] + stats["negative_count"]
        stats["edge_rate"] = round(stats["edge_count"] / total, 3) if total > 0 else 0

    # Update topic edge stats
    topic_lower = topic.lower()
    for word in [topic_lower] + [w for w in topic_lower.split() if len(w) > 3]:
        if word not in data["topic_edge"]:
            data["topic_edge"][word] = {
                "edge_count": 0,
                "no_edge_count": 0,
                "negative_count": 0,
                "edge_rate": 0.0,
            }

        stats = data["topic_edge"][word]
        if result == "edge":
            stats["edge_count"] += 1
        elif result == "no_edge":
            stats["no_edge_count"] += 1
        else:
            stats["negative_count"] += 1

        total = stats["edge_count"] + stats["no_edge_count"] + stats["negative_count"]
        stats["edge_rate"] = round(stats["edge_count"] / total, 3) if total > 0 else 0

    # Update source type edge stats
    for source in sources:
        stype = source.get("type", "youtube")
        if stype not in data["source_type_edge"]:
            data["source_type_edge"][stype] = {
                "edge_count": 0,
                "no_edge_count": 0,
                "negative_count": 0,
                "edge_rate": 0.0,
            }

        stats = data["source_type_edge"][stype]
        if result == "edge":
            stats["edge_count"] += 1
        elif result == "no_edge":
            stats["no_edge_count"] += 1
        else:
            stats["negative_count"] += 1

        total = stats["edge_count"] + stats["no_edge_count"] + stats["negative_count"]
        stats["edge_rate"] = round(stats["edge_count"] / total, 3) if total > 0 else 0

    _save_edge_data(data)
    logger.info(f"Edge tracked: {notebook_id} → {result}")

    return {
        "status": "recorded",
        "notebook_id": notebook_id,
        "topic": topic,
        "result": result,
        "total_edges_tracked": len(data["edges"]),
    }


def get_channel_edge_boost(channel: str) -> float:
    """
    Get an edge-based scoring boost for a channel.

    Returns 0.0 to 0.25 boost.

    Channels that have produced real trading edge get boosted hard.
    Channels that produced negative edge get penalized.
    """
    data = _load_edge_data()
    stats = data.get("channel_edge", {}).get(channel)

    if not stats:
        return 0.0

    total = stats["edge_count"] + stats["no_edge_count"] + stats["negative_count"]
    if total < 1:
        return 0.0

    edge_rate = stats["edge_rate"]

    # Need at least 1 tracked result
    # Boost scales with edge rate AND sample size
    if edge_rate >= 0.7 and total >= 2:
        return 0.25  # Proven edge producer
    elif edge_rate >= 0.5 and total >= 2:
        return 0.15  # Solid
    elif edge_rate >= 0.3:
        return 0.05  # Decent

    # Negative edge penalty
    if stats["negative_count"] > stats["edge_count"]:
        return -0.10  # Penalize channels that consistently don't deliver

    return 0.0


def get_topic_edge_boost(topic: str) -> float:
    """
    Get an edge-based boost for a topic.

    Topics that have previously produced real edge get boosted in suggestions.
    """
    data = _load_edge_data()
    topic_lower = topic.lower().strip()

    # Check exact match first
    stats = data.get("topic_edge", {}).get(topic_lower)
    if stats and stats.get("edge_count", 0) > 0:
        return stats["edge_rate"]

    # Check word matches
    max_rate = 0.0
    for word in topic_lower.split():
        if len(word) > 3:
            word_stats = data.get("topic_edge", {}).get(word)
            if word_stats:
                max_rate = max(max_rate, word_stats.get("edge_rate", 0))

    return max_rate


def get_edge_report() -> dict:
    """
    Full edge report — what's producing real results and what's not.

    This is your ROI on research time.
    """
    data = _load_edge_data()
    edges = data.get("edges", [])

    if not edges:
        return {
            "message": "No edge data yet. Use track_edge after implementing strategies from your research.",
            "total_tracked": 0,
        }

    total = len(edges)
    edge_count = sum(1 for e in edges if e["result"] == "edge")
    no_edge_count = sum(1 for e in edges if e["result"] == "no_edge")
    negative_count = sum(1 for e in edges if e["result"] == "negative_edge")

    # Top edge-producing channels
    channel_stats = data.get("channel_edge", {})
    top_channels = sorted(
        [(ch, s) for ch, s in channel_stats.items() if s["edge_count"] > 0],
        key=lambda x: (x[1]["edge_rate"], x[1]["edge_count"]),
        reverse=True,
    )[:10]

    # Top edge-producing topics
    topic_stats = data.get("topic_edge", {})
    top_topics = sorted(
        [(t, s) for t, s in topic_stats.items() if s["edge_count"] > 0],
        key=lambda x: (x[1]["edge_rate"], x[1]["edge_count"]),
        reverse=True,
    )[:10]

    # Source type effectiveness
    source_stats = data.get("source_type_edge", {})

    # Channels to AVOID (negative edge)
    avoid_channels = sorted(
        [(ch, s) for ch, s in channel_stats.items() if s["negative_count"] > s["edge_count"]],
        key=lambda x: x[1]["negative_count"],
        reverse=True,
    )[:5]

    # Recent edge history
    recent = edges[-10:]
    recent.reverse()

    return {
        "summary": {
            "total_tracked": total,
            "edge": edge_count,
            "no_edge": no_edge_count,
            "negative_edge": negative_count,
            "overall_edge_rate": round(edge_count / total, 3) if total > 0 else 0,
        },
        "top_edge_channels": [
            {
                "channel": ch,
                "edge_count": s["edge_count"],
                "edge_rate": s["edge_rate"],
                "total_tracked": s["edge_count"] + s["no_edge_count"] + s["negative_count"],
            }
            for ch, s in top_channels
        ],
        "top_edge_topics": [
            {
                "topic": t,
                "edge_count": s["edge_count"],
                "edge_rate": s["edge_rate"],
            }
            for t, s in top_topics
        ],
        "source_type_effectiveness": {
            stype: {
                "edge_rate": s["edge_rate"],
                "edge_count": s["edge_count"],
                "total": s["edge_count"] + s["no_edge_count"] + s["negative_count"],
            }
            for stype, s in source_stats.items()
        },
        "avoid_channels": [
            {"channel": ch, "negative_count": s["negative_count"]}
            for ch, s in avoid_channels
        ],
        "recent_history": [
            {
                "topic": e["topic"],
                "result": e["result"],
                "timestamp": e["timestamp"],
                "notes": e.get("notes", ""),
            }
            for e in recent
        ],
    }
