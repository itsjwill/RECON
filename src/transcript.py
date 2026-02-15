"""YouTube transcript extraction + quality analysis.

v2: Added substance detection — measures whether the speaker demonstrates
    real depth vs surface-level content. Catches:
    - Hedging language ("on the other hand", "it depends") = nuanced thinking
    - Specific references (years, data, names) = grounded in reality
    - Failure case discussion ("doesn't work when", "the risk is") = practitioner
    - Technical vocabulary depth = knows the domain vs parroting keywords
"""

import logging
import re
from collections import Counter

logger = logging.getLogger("auto-research.transcript")

# Common filler/ad phrases that indicate low-quality content
GARBAGE_PHRASES = [
    "subscribe to my channel",
    "hit the like button",
    "don't forget to subscribe",
    "link in the description",
    "use my referral",
    "sponsored by",
    "use code",
    "discount code",
    "check out my course",
    "sign up for free",
    "limited time offer",
    "click the link below",
]

# Substance indicators — patterns that reveal real depth
# These can't be faked easily in a 20-minute video
HEDGING_PATTERNS = [
    "on the other hand",
    "it depends",
    "counterargument",
    "the tradeoff",
    "trade-off",
    "however",
    "that said",
    "the downside",
    "the caveat",
    "in some cases",
    "not always",
    "it's nuanced",
    "context matters",
    "there are exceptions",
    "the flip side",
    "conversely",
    "to be fair",
    "the limitation",
]

FAILURE_PATTERNS = [
    "doesn't work when",
    "the risk is",
    "the problem with",
    "failed because",
    "went wrong",
    "mistake was",
    "lesson learned",
    "i was wrong",
    "what not to do",
    "common mistake",
    "pitfall",
    "backfired",
    "lost money",
    "blew up",
    "drawdown",
    "the hard way",
    "overlooked",
]

SPECIFICITY_PATTERNS = [
    r"\b(19|20)\d{2}\b",              # Years (1990-2099)
    r"\b\d+(\.\d+)?%\b",              # Percentages
    r"\$\d+",                          # Dollar amounts
    r"\b\d{3,}\b",                     # Large numbers (data points)
    r"according to",
    r"research shows",
    r"data suggests",
    r"study found",
    r"published in",
    r"paper by",
    r"chapter \d",
    r"page \d",
    r"figure \d",
    r"table \d",
]

# Stop words to exclude from concept extraction
STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "each",
    "every", "both", "few", "more", "most", "other", "some", "such", "no",
    "not", "only", "own", "same", "so", "than", "too", "very", "just",
    "about", "up", "it", "its", "this", "that", "these", "those", "i",
    "me", "my", "we", "our", "you", "your", "he", "him", "his", "she",
    "her", "they", "them", "their", "what", "which", "who", "whom",
    "and", "but", "or", "if", "while", "because", "also", "like",
    "get", "got", "going", "go", "know", "think", "want", "thing",
    "things", "really", "actually", "basically", "right", "okay", "yeah",
    "well", "um", "uh", "gonna", "gotta", "wanna", "let", "see",
    "look", "say", "said", "one", "two", "make", "much", "even",
}


def get_transcript(video_id: str) -> str | None:
    """
    Extract transcript text from a YouTube video.

    Uses youtube-transcript-api (free, no API key).
    Returns full transcript text or None if unavailable.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        # Prefer manually created transcripts, fall back to auto-generated
        transcript = None
        try:
            transcript = transcript_list.find_manually_created_transcript(["en"])
        except Exception:
            try:
                transcript = transcript_list.find_generated_transcript(["en"])
            except Exception:
                # Try any available transcript
                for t in transcript_list:
                    transcript = t
                    break

        if transcript is None:
            logger.warning(f"No transcript found for {video_id}")
            return None

        entries = transcript.fetch()
        text = " ".join(entry.get("text", "") for entry in entries)
        return text.strip() if text.strip() else None

    except Exception as e:
        logger.warning(f"Transcript extraction failed for {video_id}: {e}")
        return None


def _score_substance(text: str) -> dict:
    """
    Score how much substance is in a transcript.

    Measures three things that are hard to fake in a 20+ minute video:
    1. Hedging — nuanced thinkers acknowledge tradeoffs
    2. Failure discussion — practitioners talk about what went wrong
    3. Specificity — grounded speakers cite data, years, amounts

    Returns:
        {
            "substance_score": float,   # 0-1 composite
            "hedging": float,           # 0-1
            "failure_awareness": float, # 0-1
            "specificity": float,       # 0-1
        }
    """
    text_lower = text.lower()
    word_count = max(1, len(text_lower.split()))

    # Hedging: nuanced thinkers acknowledge complexity
    hedging_hits = sum(1 for p in HEDGING_PATTERNS if p in text_lower)
    # Normalize: 5+ hedging phrases in a transcript = full score
    hedging_score = min(1.0, hedging_hits / 5)

    # Failure awareness: practitioners discuss what goes wrong
    failure_hits = sum(1 for p in FAILURE_PATTERNS if p in text_lower)
    # Normalize: 3+ failure discussions = full score
    failure_score = min(1.0, failure_hits / 3)

    # Specificity: grounded speakers use numbers, citations, data
    specificity_hits = 0
    for pattern in SPECIFICITY_PATTERNS:
        matches = re.findall(pattern, text_lower if isinstance(pattern, str) and not pattern.startswith(r"\b") else text, re.IGNORECASE)
        specificity_hits += len(matches) if isinstance(matches, list) else 0
    # Normalize by transcript length: expect ~1 specific reference per 200 words
    expected_refs = word_count / 200
    specificity_score = min(1.0, specificity_hits / max(expected_refs, 1))

    # Composite: all three matter equally
    substance_score = (
        hedging_score * 0.30
        + failure_score * 0.35  # Heaviest — practitioners discuss failures
        + specificity_score * 0.35
    )

    return {
        "substance_score": round(substance_score, 3),
        "hedging": round(hedging_score, 3),
        "failure_awareness": round(failure_score, 3),
        "specificity": round(specificity_score, 3),
    }


def analyze_transcript(text: str, query: str) -> dict:
    """
    Analyze transcript text for quality, relevance, and substance.

    v2: Added substance scoring — hedging, failure discussion, specificity.

    Returns:
        {
            "word_count": int,
            "info_density": float,       # 0-1
            "query_relevance": float,    # 0-1
            "substance": dict,           # v2: hedging, failure, specificity scores
            "key_concepts": list[str],   # Top 10 concepts
            "is_garbage": bool,
            "summary_hint": str,
        }
    """
    words = text.lower().split()
    word_count = len(words)

    if word_count < 50:
        return {
            "word_count": word_count,
            "info_density": 0.0,
            "query_relevance": 0.0,
            "substance": {"substance_score": 0.0, "hedging": 0.0, "failure_awareness": 0.0, "specificity": 0.0},
            "key_concepts": [],
            "is_garbage": True,
            "summary_hint": "Transcript too short to analyze.",
        }

    # Info density: unique meaningful words / total words
    meaningful_words = [w for w in words if w not in STOP_WORDS and len(w) > 2]
    unique_meaningful = set(meaningful_words)
    info_density = len(unique_meaningful) / max(len(meaningful_words), 1)

    # Repetition penalty: check for repeated 3-word phrases
    trigrams = [" ".join(words[i:i + 3]) for i in range(len(words) - 2)]
    trigram_counts = Counter(trigrams)
    repeated_trigrams = sum(1 for _, count in trigram_counts.items() if count > 5)
    repetition_penalty = min(0.3, repeated_trigrams * 0.05)
    info_density = max(0.0, info_density - repetition_penalty)

    # Query relevance: how much does the transcript relate to the query
    query_words = set(query.lower().split()) - STOP_WORDS
    if query_words:
        query_word_freq = sum(
            meaningful_words.count(qw) for qw in query_words
        )
        # Normalize: expect ~1% of words to match query terms in a relevant video
        query_relevance = min(1.0, (query_word_freq / max(word_count, 1)) * 100)
    else:
        query_relevance = 0.5

    # Substance scoring (v2) — the real credibility signal
    substance = _score_substance(text)

    # Key concepts: most frequent meaningful 2-word phrases related to topic
    bigrams = [" ".join(words[i:i + 2]) for i in range(len(words) - 1)]
    meaningful_bigrams = [
        bg for bg in bigrams
        if all(w not in STOP_WORDS and len(w) > 2 for w in bg.split())
    ]
    bigram_counts = Counter(meaningful_bigrams)

    # Also count single meaningful words
    single_counts = Counter(meaningful_words)

    # Combine and pick top concepts
    concept_candidates = []
    for phrase, count in bigram_counts.most_common(30):
        if count >= 3:
            concept_candidates.append((phrase, count * 2))  # Bigrams weighted 2x
    for word, count in single_counts.most_common(30):
        if count >= 5 and word not in STOP_WORDS:
            concept_candidates.append((word, count))

    concept_candidates.sort(key=lambda x: x[1], reverse=True)
    key_concepts = [c[0] for c in concept_candidates[:10]]

    # Garbage detection
    garbage_count = sum(
        1 for phrase in GARBAGE_PHRASES
        if phrase in text.lower()
    )
    garbage_ratio = garbage_count / max(len(GARBAGE_PHRASES), 1)
    short_for_duration = word_count < 500  # Very short transcript

    is_garbage = (
        garbage_ratio > 0.3  # >30% garbage phrases found
        or (info_density < 0.15 and word_count > 200)  # Very repetitive
        or short_for_duration
    )

    # Summary hint: first substantial sentence containing a query word
    sentences = re.split(r'[.!?]+', text)
    summary_hint = ""
    for sent in sentences:
        sent = sent.strip()
        if len(sent) > 40 and any(qw in sent.lower() for qw in query_words if query_words):
            summary_hint = sent[:200]
            break
    if not summary_hint and sentences:
        # Fallback to first substantial sentence
        for sent in sentences:
            if len(sent.strip()) > 40:
                summary_hint = sent.strip()[:200]
                break

    return {
        "word_count": word_count,
        "info_density": round(info_density, 3),
        "query_relevance": round(query_relevance, 3),
        "substance": substance,
        "key_concepts": key_concepts,
        "is_garbage": is_garbage,
        "summary_hint": summary_hint,
    }


def score_transcript_quality(analysis: dict) -> float:
    """
    Convert transcript analysis to a 0-1 quality score.

    v2: Substance score is now the heaviest factor.
    A video can have perfect info density and relevance but still be
    surface-level if the speaker never hedges, never discusses failures,
    and never cites specific data.

    Weights:
    - substance: 0.35 — the new credibility signal (replaces practitioner keywords)
    - relevance: 0.25 — does this match the query?
    - density: 0.20 — information richness
    - concepts: 0.10 — extracted meaningful terms
    - length: 0.10 — longer = more ground covered
    """
    if analysis.get("is_garbage"):
        return 0.0

    density_score = min(1.0, analysis.get("info_density", 0) / 0.4)
    relevance_score = analysis.get("query_relevance", 0)
    concept_score = min(1.0, len(analysis.get("key_concepts", [])) / 7)
    length_score = min(1.0, analysis.get("word_count", 0) / 3000)

    # v2: substance score from hedging + failure + specificity
    substance = analysis.get("substance", {})
    substance_score = substance.get("substance_score", 0.0)

    quality = (
        substance_score * 0.35
        + relevance_score * 0.25
        + density_score * 0.20
        + concept_score * 0.10
        + length_score * 0.10
    )

    return round(min(1.0, max(0.0, quality)), 3)
