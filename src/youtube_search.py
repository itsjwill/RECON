"""YouTube Data API search + curation scoring + transcript + credibility + feedback."""

import math
import random
import re
import logging
from datetime import datetime, timezone
from typing import Any

from googleapiclient.discovery import build

from . import config
from .transcript import get_transcript, analyze_transcript, score_transcript_quality
from .credibility import score_youtube_credibility
from .feedback import get_channel_boost

logger = logging.getLogger("auto-research.youtube")


def _parse_duration(iso_duration: str) -> float:
    """Parse ISO 8601 duration (PT1H2M3S) to minutes."""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration or "")
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 60 + minutes + seconds / 60


def _score_views(views: int) -> float:
    """Score 0-1 based on view count (log scale)."""
    if views <= 0:
        return 0
    return min(1.0, max(0, (math.log10(views) - 2) / 5))


def _score_views_trading(views: int) -> float:
    """Score 0-1 INVERTED for trading — low views = hidden edge.

    Sweet spot: 5K-50K views (real practitioners, not mainstream).
    High views (500K+) = consensus = everyone knows it = alpha decayed.
    """
    if views <= 0:
        return 0
    if views < 1000:
        return 0.3  # Too obscure, might be garbage
    elif views < 5000:
        return 0.6  # Emerging content
    elif views <= 50000:
        return 1.0  # Sweet spot — real practitioners, not mainstream
    elif views <= 200000:
        return 0.7  # Getting popular
    elif views <= 500000:
        return 0.4  # Mainstream
    else:
        return 0.2  # Everyone's seen it — consensus, not edge


def _score_channel_subs(subs: int) -> float:
    """Score 0-1 based on channel subscriber count."""
    if subs <= 0:
        return 0
    return min(1.0, max(0, (math.log10(subs) - 2) / 5))


def _score_relevance(query: str, title: str, description: str) -> float:
    """Score 0-1 based on keyword overlap between query and video."""
    query_words = set(query.lower().split())
    text = f"{title} {description}".lower()
    text_words = set(text.split())

    if not query_words:
        return 0

    matches = sum(1 for w in query_words if w in text_words)
    base = matches / len(query_words)

    if query.lower() in title.lower():
        base = min(1.0, base + 0.3)

    return min(1.0, base)


def _score_recency(published_at: str) -> float:
    """Score 0-1 based on how recent the video is."""
    try:
        pub_date = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - pub_date).days
    except (ValueError, TypeError):
        return 0.3

    if age_days <= 180:
        return 1.0
    elif age_days <= 365:
        return 0.7
    elif age_days <= 730:
        return 0.5
    else:
        return 0.3


def _score_velocity(views: int, published_at: str) -> float:
    """Score 0-1 based on view velocity (views/day since upload).

    A 2-day-old video with 8K views = 4000 views/day (exploding content).
    A 3-year-old video with 8K views = 7 views/day (dead content).

    This catches content that is gaining traction RIGHT NOW — the market
    hasn't fully priced in the information yet.
    """
    if views <= 0:
        return 0

    try:
        pub_date = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        age_days = max(1, (datetime.now(timezone.utc) - pub_date).days)
    except (ValueError, TypeError):
        return 0.5  # Unknown age, neutral

    vpd = views / age_days  # Views per day

    if vpd >= 5000:
        return 1.0   # Viral — exploding right now
    elif vpd >= 1000:
        return 0.9   # Very strong traction
    elif vpd >= 500:
        return 0.8   # Strong traction
    elif vpd >= 100:
        return 0.65  # Healthy growth
    elif vpd >= 30:
        return 0.5   # Normal
    elif vpd >= 10:
        return 0.35  # Slow
    else:
        return 0.2   # Stagnant


def _score_engagement(likes: int, views: int) -> float:
    """Score 0-1 based on like-to-view ratio."""
    if views <= 0:
        return 0
    ratio = likes / views
    if ratio >= 0.05:
        return 1.0
    elif ratio >= 0.03:
        return 0.8
    elif ratio >= 0.01:
        return 0.5
    else:
        return 0.2


def _compute_score(video: dict, query: str, mode: str = "general") -> dict:
    """
    Compute composite score for a video with all factors.

    Modes:
    - "general": Standard scoring — popular = good (learning mode)
    - "trading": Inverted popularity — low views = hidden edge, substance over hype

    General weights (sum to 1.0):
    - views: 0.15, channel: 0.12, relevance: 0.18, recency: 0.10
    - engagement: 0.10, velocity: 0.12, transcript: 0.14, credibility: 0.09

    Trading weights (sum to 1.0) — NO velocity (trending = consensus = no edge):
    - views: 0.05 (inverted), channel: 0.07, relevance: 0.18, recency: 0.14
    - engagement: 0.12, transcript: 0.24, credibility: 0.20
    """
    views = int(video.get("views", 0))
    likes = int(video.get("likes", 0))
    subs = int(video.get("channel_subs", 0))
    published_at = video.get("published_at", "")

    if mode == "trading":
        # Trading mode: substance > popularity. NO velocity — trending = consensus = no edge.
        # The entire point is to find what nobody's watching yet.
        breakdown = {
            "views": _score_views_trading(views) * 0.05,
            "channel": _score_channel_subs(subs) * 0.07,
            "relevance": _score_relevance(query, video.get("title", ""), video.get("description", "")) * 0.18,
            "recency": _score_recency(published_at) * 0.14,
            "engagement": _score_engagement(likes, views) * 0.12,
        }
        breakdown["transcript"] = video.get("transcript_quality", 0.5) * 0.24
        breakdown["credibility"] = video.get("credibility_score", 0.5) * 0.20
    else:
        # General mode: velocity helps surface trending educational content
        breakdown = {
            "views": _score_views(views) * 0.15,
            "channel": _score_channel_subs(subs) * 0.12,
            "relevance": _score_relevance(query, video.get("title", ""), video.get("description", "")) * 0.18,
            "recency": _score_recency(published_at) * 0.10,
            "engagement": _score_engagement(likes, views) * 0.10,
            "velocity": _score_velocity(views, published_at) * 0.12,
        }
        breakdown["transcript"] = video.get("transcript_quality", 0.5) * 0.14
        breakdown["credibility"] = video.get("credibility_score", 0.5) * 0.09

    total = sum(breakdown.values()) * 100  # Scale to 0-100

    # Feedback boost (v2) — added on top, up to +20 points
    feedback_boost = get_channel_boost(video.get("channel", ""))
    total += feedback_boost * 100

    # Edge boost (v3) — channels/topics that produced real trading edge
    from .edge_tracker import get_channel_edge_boost
    edge_boost = get_channel_edge_boost(video.get("channel", ""))
    total += edge_boost * 100

    return {
        "score": round(min(100, total), 1),
        "breakdown": {k: round(v * 100, 1) for k, v in breakdown.items()},
        "feedback_boost": round(feedback_boost * 100, 1),
        "edge_boost": round(edge_boost * 100, 1),
    }


async def search_and_curate(
    query: str,
    num_videos: int = 5,
    language: str = "en",
    analyze_transcripts: bool = True,
    mode: str = "general",
) -> dict:
    """
    Search YouTube, score, filter, and return curated video list.

    v2: includes transcript analysis, credibility scoring, and feedback boost.
    v3: mode="trading" inverts popularity scoring — hidden gems > mainstream.

    Returns:
        {
            "query": str,
            "videos": [{video_id, url, title, channel, views, likes, duration_min,
                        score, breakdown, key_concepts, transcript_quality}, ...],
            "total_candidates": int,
            "filtered_out": int,
        }
    """
    if not config.YOUTUBE_API_KEY:
        return {"error": "YOUTUBE_API_KEY not set. Get one at https://console.cloud.google.com/apis/credentials"}

    num_videos = min(num_videos, config.MAX_NUM_VIDEOS)

    youtube = build("youtube", "v3", developerKey=config.YOUTUBE_API_KEY)

    # Step 1: Search for videos (100 API units)
    logger.info(f"Searching YouTube for: {query}")
    search_response = youtube.search().list(
        q=query,
        part="snippet",
        type="video",
        order="relevance",
        maxResults=25,
        relevanceLanguage=language,
    ).execute()

    video_ids = [item["id"]["videoId"] for item in search_response.get("items", [])]
    if not video_ids:
        return {"query": query, "videos": [], "total_candidates": 0, "filtered_out": 0}

    # Step 2: Get full video details
    details_response = youtube.videos().list(
        id=",".join(video_ids),
        part="snippet,statistics,contentDetails",
    ).execute()

    # Step 3: Get channel details for subscriber counts + credibility data
    channel_ids = list({item["snippet"]["channelId"] for item in details_response.get("items", [])})
    channel_data = {}
    if channel_ids:
        channels_response = youtube.channels().list(
            id=",".join(channel_ids),
            part="statistics,snippet",
        ).execute()
        for ch in channels_response.get("items", []):
            ch_id = ch["id"]
            ch_snippet = ch.get("snippet", {})
            ch_stats = ch.get("statistics", {})
            channel_data[ch_id] = {
                "channel_id": ch_id,
                "channel_title": ch_snippet.get("title", ""),
                "description": ch_snippet.get("description", ""),
                "subscriber_count": int(ch_stats.get("subscriberCount", 0)),
                "video_count": int(ch_stats.get("videoCount", 0)),
                "created_at": ch_snippet.get("publishedAt", ""),
            }

    # Step 4: Build video objects with all metadata
    candidates = []
    for item in details_response.get("items", []):
        snippet = item["snippet"]
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})
        ch_id = snippet.get("channelId", "")

        video = {
            "video_id": item["id"],
            "url": f"https://www.youtube.com/watch?v={item['id']}",
            "title": snippet.get("title", ""),
            "description": snippet.get("description", "")[:300],
            "channel": snippet.get("channelTitle", ""),
            "channel_id": ch_id,
            "channel_subs": channel_data.get(ch_id, {}).get("subscriber_count", 0),
            "views": int(stats.get("viewCount", 0)),
            "likes": int(stats.get("likeCount", 0)),
            "duration_min": round(_parse_duration(content.get("duration", "")), 1),
            "published_at": snippet.get("publishedAt", ""),
            "source_type": "youtube",
        }
        candidates.append(video)

    total_candidates = len(candidates)

    # Step 5: Filter
    filtered = []
    for v in candidates:
        if v["views"] < config.MIN_VIEWS:
            continue
        if v["duration_min"] < config.MIN_DURATION_MINUTES:
            continue
        if v["duration_min"] > config.MAX_DURATION_MINUTES:
            continue
        filtered.append(v)

    # Step 6: Credibility scoring (v2)
    for v in filtered:
        ch_id = v.get("channel_id", "")
        if ch_id in channel_data:
            cred = score_youtube_credibility(channel_data[ch_id])
            v["credibility_score"] = cred["score"]
            v["credibility_details"] = cred
        else:
            v["credibility_score"] = 0.5

    # Step 7: Transcript analysis (v2) — only top 15 candidates to save API calls
    if analyze_transcripts:
        # Sort by preliminary score first (without transcript factor)
        for v in filtered:
            v["_prelim_score"] = (
                _score_views(v["views"]) * 0.25
                + _score_relevance(query, v["title"], v.get("description", "")) * 0.25
                + _score_engagement(v["likes"], v["views"]) * 0.15
            )
        filtered.sort(key=lambda x: x.get("_prelim_score", 0), reverse=True)

        for v in filtered[:15]:
            logger.info(f"  Analyzing transcript: {v['title'][:50]}...")
            transcript_text = get_transcript(v["video_id"])
            if transcript_text:
                analysis = analyze_transcript(transcript_text, query)
                v["transcript_quality"] = score_transcript_quality(analysis)
                v["key_concepts"] = analysis.get("key_concepts", [])
                v["is_garbage"] = analysis.get("is_garbage", False)
                v["summary_hint"] = analysis.get("summary_hint", "")
            else:
                v["transcript_quality"] = 0.3  # Penalty for no transcript
                v["key_concepts"] = []
                v["is_garbage"] = False
                v["summary_hint"] = ""

        # Remove garbage videos
        filtered = [v for v in filtered if not v.get("is_garbage", False)]

        # Clean up temp field
        for v in filtered:
            v.pop("_prelim_score", None)

    # Step 8: Full scoring with all v2 factors (mode-aware)
    for v in filtered:
        score_data = _compute_score(v, query, mode=mode)
        v["score"] = score_data["score"]
        v["score_breakdown"] = score_data["breakdown"]
        if score_data.get("feedback_boost", 0) > 0:
            v["feedback_boost"] = score_data["feedback_boost"]

    # Step 9: Deduplicate (max 1 per channel, keep highest scored)
    filtered.sort(key=lambda x: x["score"], reverse=True)
    seen_channels: set[str] = set()
    deduped = []
    for v in filtered:
        if v["channel_id"] not in seen_channels:
            seen_channels.add(v["channel_id"])
            deduped.append(v)

    # Step 10: Select with randomization
    if len(deduped) <= num_videos:
        selected = deduped
    else:
        top_tier = deduped[:7]
        mid_tier = deduped[7:15]

        top_picks = min(3, len(top_tier))
        mid_picks = min(num_videos - top_picks, len(mid_tier))

        selected = random.sample(top_tier, top_picks)
        if mid_tier and mid_picks > 0:
            selected += random.sample(mid_tier, mid_picks)

        remaining = num_videos - len(selected)
        if remaining > 0:
            extras = [v for v in top_tier if v not in selected]
            selected += extras[:remaining]

    selected.sort(key=lambda x: x["score"], reverse=True)

    # Clean up output (keep key_concepts for knowledge graph)
    for v in selected:
        v.pop("description", None)
        v.pop("channel_id", None)
        v.pop("channel_subs", None)
        v.pop("published_at", None)
        v.pop("credibility_details", None)
        v.pop("is_garbage", None)
        v.pop("_prelim_score", None)

    return {
        "query": query,
        "videos": selected,
        "total_candidates": total_candidates,
        "filtered_out": total_candidates - len(deduped),
    }
