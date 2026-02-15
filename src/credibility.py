"""Source credibility scoring — filter garbage, let substance speak for itself.

v2: Dropped practitioner signals (self-reported keywords = meaningless).
    Reduced channel age weight (penalizes early adopters of new topics).
    Added description substance scoring (length + specificity, not keywords).
    Clickbait detection is now the heaviest factor — it's the most honest signal.
"""

import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger("auto-research.credibility")

# Red flag patterns in video titles
CLICKBAIT_PATTERNS = [
    r"GUARANTEED",
    r"MAKE \$\d+",
    r"EARN \$\d+",
    r"FREE MONEY",
    r"GET RICH",
    r"SECRET",
    r"NOBODY TELLS YOU",
    r"YOU WON'T BELIEVE",
    r"SHOCKING",
    r"🚀{3,}",  # Excessive rocket emojis
    r"💰{3,}",
    r"🔥{3,}",
    r"PASSIVE INCOME",
    r"EASY MONEY",
    r"NO EXPERIENCE",
]


def score_youtube_credibility(channel_data: dict) -> dict:
    """
    Score YouTube channel credibility.

    v2 weights (sum to 1.0):
    - clickbait_clean: 0.45 — most honest signal, catches real garbage
    - upload_consistency: 0.25 — commitment over time is earned
    - description_substance: 0.20 — actual substance in about section
    - channel_age: 0.10 — weak signal, punishes new-topic creators

    Dropped from v1:
    - practitioner_signals — self-reported keywords are meaningless
      (anyone can type "CFA" in their description)

    Args:
        channel_data: {
            "channel_id": str,
            "channel_title": str,
            "description": str,
            "subscriber_count": int,
            "video_count": int,
            "created_at": str,
            "recent_titles": list[str],
        }

    Returns:
        {
            "score": float,
            "channel_age_years": float,
            "upload_consistency": float,
            "clickbait_clean": float,
            "description_substance": float,
            "red_flags": list[str],
        }
    """
    red_flags = []

    # ── Channel age (10% weight — weak signal) ──
    # A 1-year channel about a 1-year-old topic (e.g., Claude Code) is fine.
    # Only flag channels < 3 months as truly suspicious.
    channel_age_years = 0.0
    created_at = channel_data.get("created_at", "")
    if created_at:
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - created).days
            channel_age_years = age_days / 365.25
        except (ValueError, TypeError):
            pass

    age_score = min(1.0, channel_age_years / 3)  # 3+ years = full (was 5)
    if channel_age_years < 0.25:
        red_flags.append("very new channel (<3 months)")

    # ── Upload consistency (25% weight) ──
    video_count = channel_data.get("video_count", 0)
    if channel_age_years > 0:
        videos_per_year = video_count / channel_age_years
        upload_consistency = min(1.0, videos_per_year / 50)
    else:
        upload_consistency = 0.0

    if video_count < 10:
        red_flags.append("very few videos (<10)")

    # ── Clickbait detection (45% weight — most honest signal) ──
    recent_titles = channel_data.get("recent_titles", [])
    clickbait_count = 0
    caps_count = 0

    for title in recent_titles:
        for pattern in CLICKBAIT_PATTERNS:
            if re.search(pattern, title, re.IGNORECASE):
                clickbait_count += 1
                break

        words = title.split()
        caps_words = sum(1 for w in words if len(w) > 3 and w.isupper())
        if len(words) > 0 and caps_words / len(words) > 0.5:
            caps_count += 1

    if recent_titles:
        clickbait_ratio = clickbait_count / len(recent_titles)
        caps_ratio = caps_count / len(recent_titles)
    else:
        clickbait_ratio = 0
        caps_ratio = 0

    clickbait_clean = 1.0 - min(1.0, clickbait_ratio + caps_ratio * 0.5)

    if clickbait_ratio > 0.3:
        red_flags.append("frequent clickbait titles")
    if caps_ratio > 0.5:
        red_flags.append("excessive ALL CAPS titles")

    # ── Description substance (20% weight) ──
    # Not keyword matching — just checking if there's real substance.
    # Long, specific descriptions correlate with serious creators.
    description = (channel_data.get("description", "") or "").strip()
    desc_len = len(description)

    if desc_len > 500:
        desc_length_score = 1.0
    elif desc_len > 200:
        desc_length_score = 0.7
    elif desc_len > 50:
        desc_length_score = 0.4
    else:
        desc_length_score = 0.1

    # Check for specificity: numbers, URLs, dates suggest real info
    desc_lower = description.lower()
    has_numbers = bool(re.search(r'\d{4}', description))  # Years, counts
    has_urls = bool(re.search(r'https?://', description))
    has_contact = any(w in desc_lower for w in ["email", "contact", "business inquiries"])
    specificity_bonus = min(0.3, sum([has_numbers, has_urls, has_contact]) * 0.1)

    description_substance = min(1.0, desc_length_score + specificity_bonus)

    if desc_len < 30:
        red_flags.append("empty/minimal channel description")

    # ── Composite score ──
    score = (
        age_score * 0.10
        + upload_consistency * 0.25
        + clickbait_clean * 0.45
        + description_substance * 0.20
    )

    # Penalty for red flags
    flag_penalty = min(0.3, len(red_flags) * 0.1)
    score = max(0.0, score - flag_penalty)

    return {
        "score": round(score, 3),
        "channel_age_years": round(channel_age_years, 1),
        "upload_consistency": round(upload_consistency, 3),
        "clickbait_clean": round(clickbait_clean, 3),
        "description_substance": round(description_substance, 3),
        "red_flags": red_flags,
    }


def score_paper_credibility(paper: dict) -> dict:
    """
    Score academic paper credibility.

    Args:
        paper: dict with citations, year, journal, is_open_access, authors

    Returns:
        {
            "score": float,
            "citation_count": int,
            "journal_tier": str,
            "open_access": bool,
        }
    """
    citations = paper.get("citations", 0)
    year = paper.get("year") or 2020
    journal = paper.get("journal", "")
    is_oa = paper.get("is_open_access", False)

    # Citation impact
    if citations >= 100:
        citation_score = 1.0
    elif citations >= 50:
        citation_score = 0.8
    elif citations >= 20:
        citation_score = 0.6
    elif citations >= 5:
        citation_score = 0.4
    else:
        citation_score = 0.2

    # Age-adjusted citations (newer papers have less time to accumulate)
    current_year = datetime.now(timezone.utc).year
    age = max(1, current_year - year)
    citations_per_year = citations / age
    if citations_per_year >= 20:
        velocity_bonus = 0.2
    elif citations_per_year >= 10:
        velocity_bonus = 0.1
    else:
        velocity_bonus = 0.0

    # Journal tier (simple heuristic)
    journal_lower = journal.lower()
    if any(t in journal_lower for t in ["nature", "science", "cell", "lancet", "nejm", "jama"]):
        journal_tier = "top"
        journal_score = 1.0
    elif any(t in journal_lower for t in ["review", "proceedings", "transactions", "journal of"]):
        journal_tier = "mid"
        journal_score = 0.6
    elif journal:
        journal_tier = "indexed"
        journal_score = 0.4
    else:
        journal_tier = "preprint"
        journal_score = 0.2

    # Open access bonus
    oa_score = 0.8 if is_oa else 0.4

    score = min(1.0, (
        (citation_score + velocity_bonus) * 0.40
        + journal_score * 0.30
        + oa_score * 0.30
    ))

    return {
        "score": round(score, 3),
        "citation_count": citations,
        "journal_tier": journal_tier,
        "open_access": is_oa,
    }


def score_podcast_credibility(podcast: dict) -> dict:
    """
    Score podcast credibility.

    Args:
        podcast: dict with episode_count, podcast_name, description

    Returns:
        {
            "score": float,
            "episode_count": int,
            "regularity": float,
        }
    """
    episode_count = podcast.get("episode_count", 0)

    # Established show
    if episode_count >= 100:
        establishment_score = 1.0
    elif episode_count >= 50:
        establishment_score = 0.8
    elif episode_count >= 20:
        establishment_score = 0.6
    elif episode_count >= 10:
        establishment_score = 0.4
    else:
        establishment_score = 0.2

    # Simple description quality check
    description = (podcast.get("description", "") or "").lower()
    has_substance = len(description) > 100
    desc_score = 0.7 if has_substance else 0.3

    score = establishment_score * 0.6 + desc_score * 0.4

    return {
        "score": round(score, 3),
        "episode_count": episode_count,
        "regularity": round(establishment_score, 3),
    }
