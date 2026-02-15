"""OpenAlex API search for academic papers — completely free, no API key."""

import logging
import math
from datetime import datetime, timezone

import requests

logger = logging.getLogger("auto-research.papers")

OPENALEX_BASE = "https://api.openalex.org"
# Polite pool: include email for faster rate limits (optional)
OPENALEX_HEADERS = {"User-Agent": "auto-research-mcp/0.2.0 (research tool)"}


def search_papers(query: str, limit: int = 10) -> dict:
    """
    Search OpenAlex for academic papers on a topic.

    Returns:
        {
            "query": str,
            "papers": [{title, doi, url, authors, year, citations, abstract, open_access_url, score}],
            "total_results": int,
        }
    """
    try:
        params = {
            "search": query,
            "per_page": min(limit * 2, 50),  # Fetch extra for filtering
            "sort": "relevance_score:desc",
            "filter": "type:article|review|book-chapter,language:en",
            "select": "id,doi,title,authorships,publication_year,cited_by_count,"
                      "primary_location,open_access,abstract_inverted_index,relevance_score",
        }

        resp = requests.get(
            f"{OPENALEX_BASE}/works",
            params=params,
            headers=OPENALEX_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        total = data.get("meta", {}).get("count", 0)

        papers = []
        for work in results:
            paper = _parse_work(work, query)
            if paper:
                papers.append(paper)

        # Score and sort
        for p in papers:
            p["score"] = _score_paper(p, query)

        papers.sort(key=lambda x: x["score"], reverse=True)
        papers = papers[:limit]

        return {
            "query": query,
            "papers": papers,
            "total_results": total,
        }

    except Exception as e:
        logger.error(f"OpenAlex search failed: {e}")
        return {"query": query, "papers": [], "total_results": 0, "error": str(e)}


def _parse_work(work: dict, query: str) -> dict | None:
    """Parse an OpenAlex work into our paper format."""
    title = work.get("title", "")
    if not title:
        return None

    # Extract authors (first 5)
    authors = []
    for auth in (work.get("authorships") or [])[:5]:
        name = auth.get("author", {}).get("display_name", "")
        if name:
            authors.append(name)

    # Get DOI and URLs
    doi = work.get("doi", "")
    openalex_id = work.get("id", "")

    # Open access URL
    oa_info = work.get("open_access", {})
    oa_url = oa_info.get("oa_url", "")
    is_oa = oa_info.get("is_oa", False)

    # Primary location for journal/source info
    location = work.get("primary_location", {}) or {}
    source = location.get("source", {}) or {}
    journal = source.get("display_name", "")

    # Landing page URL
    landing_url = location.get("landing_page_url", "")

    # Reconstruct abstract from inverted index
    abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))

    # Best URL for NotebookLM (prefer open access PDF, then DOI, then landing page)
    best_url = oa_url or doi or landing_url or f"https://openalex.org/works/{openalex_id.split('/')[-1]}"

    return {
        "title": title,
        "authors": authors,
        "year": work.get("publication_year"),
        "citations": work.get("cited_by_count", 0),
        "doi": doi,
        "url": best_url,
        "open_access_url": oa_url if is_oa else None,
        "is_open_access": is_oa,
        "journal": journal,
        "abstract": (abstract or "")[:500],
        "source_type": "paper",
    }


def _reconstruct_abstract(inverted_index: dict | None) -> str:
    """Reconstruct abstract text from OpenAlex inverted index format."""
    if not inverted_index:
        return ""

    # Inverted index: {"word": [position1, position2, ...]}
    word_positions = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))

    word_positions.sort(key=lambda x: x[0])
    return " ".join(w for _, w in word_positions)


def _score_paper(paper: dict, query: str) -> float:
    """
    Score a paper 0-100.

    Factors:
    - Citations (30%): log-scaled citation count
    - Recency (25%): newer papers score higher
    - Relevance (25%): title/abstract keyword match
    - Open access (20%): OA papers score higher (NotebookLM can read them)
    """
    # Citation score (log scale)
    citations = paper.get("citations", 0)
    if citations <= 0:
        citation_score = 0
    else:
        citation_score = min(1.0, math.log10(citations + 1) / 3)  # 1000 citations = 1.0

    # Recency score
    year = paper.get("year") or 2020
    current_year = datetime.now(timezone.utc).year
    age = current_year - year
    if age <= 2:
        recency_score = 1.0
    elif age <= 5:
        recency_score = 0.7
    elif age <= 10:
        recency_score = 0.4
    else:
        recency_score = 0.2

    # Relevance score
    query_words = set(query.lower().split())
    title_words = set(paper.get("title", "").lower().split())
    abstract_words = set(paper.get("abstract", "").lower().split())
    all_text_words = title_words | abstract_words

    if query_words:
        matches = sum(1 for w in query_words if w in all_text_words)
        relevance_score = matches / len(query_words)
        # Bonus for exact phrase in title
        if query.lower() in paper.get("title", "").lower():
            relevance_score = min(1.0, relevance_score + 0.3)
    else:
        relevance_score = 0.5

    # Open access bonus
    oa_score = 1.0 if paper.get("is_open_access") else 0.3

    # Weighted combination
    total = (
        citation_score * 0.30
        + recency_score * 0.25
        + relevance_score * 0.25
        + oa_score * 0.20
    ) * 100

    return round(total, 1)
