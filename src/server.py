"""Auto-Research MCP Server v3 — FastMCP rewrite for Claude Code compatibility."""

import asyncio
import json
import logging
import sys
import threading
import time
import traceback

from mcp.server.fastmcp import FastMCP

from . import config
from .youtube_search import search_and_curate
from .paper_search import search_papers as _search_papers_fn
from .podcast_search import search_podcasts
from .notebook_manager import create_notebook_with_sources, ask_notebook as _ask_notebook
from .auth_keepalive import refresh_auth_state
from .browser_pool import reap_if_idle
from .library_sync import add_notebook_to_library, list_research_notebooks
from .feedback import record_research, rate_notebook, get_feedback_summary, get_topic_history, get_stale_notebooks
from .knowledge_graph import (
    extract_entities, update_graph, find_connections,
    get_knowledge_gaps, get_graph_summary, get_cross_domain_insights,
)
from .edge_tracker import track_edge as _track_edge_fn, get_edge_report as _get_edge_report, get_topic_edge_boost

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("auto-research")

server = FastMCP("auto-research")


# ──── Tools ────

@server.tool()
async def research(topic: str, num_sources: int = 5, sources: list[str] = None, mode: str = "general") -> str:
    """Research a topic by finding the top sources (YouTube videos, academic papers, podcasts) and creating a NotebookLM notebook. Each source is scored for quality, credibility, and transcript depth. The notebook becomes immediately queryable via NotebookLM's ask_question tool."""
    if sources is None:
        sources = ["youtube"]

    try:
        num_sources = min(num_sources, config.MAX_NUM_VIDEOS)
        source_types = sources

        if not topic:
            return "Error: 'topic' is required"

        if "all" in source_types:
            source_types = ["youtube", "papers", "podcasts"]

        all_sources = []
        all_key_concepts = []

        if "youtube" in source_types:
            if not config.YOUTUBE_API_KEY:
                return "Error: YOUTUBE_API_KEY not set"
            logger.info(f"Searching YouTube for: {topic} (mode={mode})")
            yt_result = await search_and_curate(topic, num_sources, mode=mode)
            if "error" not in yt_result:
                for v in yt_result.get("videos", []):
                    v["source_type"] = "youtube"
                    all_sources.append(v)
                    all_key_concepts.extend(v.get("key_concepts", []))

        if "papers" in source_types:
            logger.info(f"Searching papers for: {topic}")
            paper_result = _search_papers_fn(topic, num_sources)
            if "error" not in paper_result:
                for p in paper_result.get("papers", []):
                    p["source_type"] = "paper"
                    all_sources.append(p)

        if "podcasts" in source_types:
            logger.info(f"Searching podcasts for: {topic}")
            pod_result = search_podcasts(topic, num_sources)
            if "error" not in pod_result:
                for ep in pod_result.get("episodes", []):
                    ep["source_type"] = "podcast"
                    all_sources.append(ep)

        if not all_sources:
            return f"No sources found for '{topic}'. Try a different query."

        # Contrarian search
        contrarian_suffixes = ["problems", "criticism", "limitations", "risks", "doesn't work", "mistakes"]
        contrarian_query = f"{topic} {contrarian_suffixes[hash(topic) % len(contrarian_suffixes)]}"
        contrarian_sources = []

        if "youtube" in source_types and config.YOUTUBE_API_KEY:
            logger.info(f"Contrarian search: {contrarian_query}")
            contra_result = await search_and_curate(contrarian_query, 3, mode=mode)
            if "error" not in contra_result:
                for v in contra_result.get("videos", []):
                    v["source_type"] = "youtube"
                    v["is_contrarian"] = True
                    contrarian_sources.append(v)

        if "papers" in source_types:
            contra_paper = _search_papers_fn(contrarian_query, 2)
            if "error" not in contra_paper:
                for p in contra_paper.get("papers", []):
                    p["source_type"] = "paper"
                    p["is_contrarian"] = True
                    contrarian_sources.append(p)

        # Unified ranking (deduped by URL: the contrarian search often returns a main pick)
        all_sources.sort(key=lambda x: x.get("score", 0), reverse=True)
        seen, ranked = set(), []
        for s in all_sources:
            if s.get("url") and s["url"] not in seen:
                seen.add(s["url"])
                ranked.append(s)
        contrarian_sources.sort(key=lambda x: x.get("score", 0), reverse=True)
        contrarian = next((c for c in contrarian_sources if c.get("url") not in set(s["url"] for s in ranked[:num_sources - 1])), None)
        if contrarian:
            selected_sources = ranked[:num_sources - 1] + [contrarian]
        else:
            selected_sources = ranked[:num_sources]

        source_urls = [s["url"] for s in selected_sources if s.get("url")]
        if not source_urls:
            return "No valid URLs found in selected sources."

        # Create NotebookLM notebook
        logger.info(f"Creating NotebookLM notebook with {len(source_urls)} sources...")
        nb_result = await create_notebook_with_sources(topic, source_urls)
        notebook_url = nb_result.get("notebook_url", "")
        videos_added = nb_result.get("videos_added", [])
        videos_failed = nb_result.get("videos_failed", [])
        nb_error = nb_result.get("error")

        # Register in library
        notebook_id = None
        if notebook_url:
            source_type_counts = {}
            for s in selected_sources:
                st = s.get("source_type", "unknown")
                source_type_counts[st] = source_type_counts.get(st, 0) + 1
            source_summary = ", ".join(f"{count} {stype}" for stype, count in source_type_counts.items())
            notebook_id = add_notebook_to_library(
                url=notebook_url,
                name=f"Research: {topic}",
                description=f"Auto-researched: {source_summary} on {topic}",
                topics=topic.lower().split()[:5],
                videos=selected_sources,
            )

        if notebook_id:
            record_research(notebook_id, topic, selected_sources)
            entities = extract_entities(topic, all_key_concepts, selected_sources)
            graph_update = update_graph(notebook_id, entities)
            connections = find_connections(topic)
        else:
            graph_update = None
            connections = []

        result = {
            "status": "success" if videos_added and not nb_error else "partial" if videos_added else "failed",
            "topic": topic,
            "mode": mode,
            "notebook_url": notebook_url,
            "notebook_id": notebook_id,
            "sources_added": len(videos_added),
            "sources_failed": len(videos_failed),
            "source_types_searched": source_types,
            "sources": [
                {
                    "title": s.get("title", ""),
                    "url": s.get("url", ""),
                    "type": s.get("source_type", "unknown"),
                    "score": s.get("score", 0),
                    "channel": s.get("channel", s.get("podcast_name", "")),
                    **({"key_concepts": s["key_concepts"]} if s.get("key_concepts") else {}),
                    **({"citations": s["citations"]} if s.get("citations") else {}),
                    **({"contrarian": True} if s.get("is_contrarian") else {}),
                }
                for s in selected_sources
            ],
        }
        if nb_error:
            result["error"] = nb_error
        if notebook_id:
            result["next_step"] = f"Query this notebook: ask_question(question='...', notebook_id='{notebook_id}')"
        if graph_update:
            result["knowledge_graph"] = graph_update
        if connections:
            result["connected_notebooks"] = [
                {"notebook_id": c["notebook_id"], "via": c["via_entity"], "strength": c["strength"]}
                for c in connections[:5]
            ]

        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error in research: {e}\n{traceback.format_exc()}")
        return f"Error: {str(e)}"


@server.tool()
async def search_videos(query: str, limit: int = 10) -> str:
    """Search YouTube for videos on a topic and return scored/curated results with transcript quality analysis and credibility scores."""
    if not query:
        return "Error: 'query' is required"
    if not config.YOUTUBE_API_KEY:
        return "Error: YOUTUBE_API_KEY not set"
    try:
        result = await search_and_curate(query, limit)
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"


@server.tool()
async def search_papers(query: str, limit: int = 10) -> str:
    """Search OpenAlex for academic papers on a topic. Returns scored results with citation counts, open access status, and credibility scores. Free, no API key needed, 474M+ papers."""
    if not query:
        return "Error: 'query' is required"
    try:
        result = _search_papers_fn(query, limit)
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"


@server.tool()
async def list_research_notebooks_tool() -> str:
    """List all research notebooks created by auto-research. Shows notebook IDs, names, topics, and URLs so you can query them via NotebookLM's ask_question."""
    notebooks = list_research_notebooks()
    if not notebooks:
        return "No auto-research notebooks yet. Use the 'research' tool to create one."
    summary = get_feedback_summary()
    return json.dumps({"notebooks": notebooks, "feedback_summary": summary}, indent=2)


@server.tool()
async def rate_research(notebook_id: str, rating: int) -> str:
    """Rate a research notebook 1-5 to improve future research quality."""
    if not notebook_id:
        return "Error: 'notebook_id' is required"
    result = rate_notebook(notebook_id, rating)
    return json.dumps(result, indent=2)


@server.tool()
async def suggest_research() -> str:
    """Get AI-suggested research topics based on your knowledge graph. Finds gaps in your knowledge and suggests related topics to deepen your expertise."""
    gaps = get_knowledge_gaps()
    topic_history = get_topic_history()
    graph_summary = get_graph_summary()
    stale = get_stale_notebooks()

    suggestions = []

    for nb in stale[:3]:
        staleness = nb["staleness"]
        urgency = "urgent" if staleness >= 2.0 else "recommended"
        label = "EXPERT" if nb["is_trading"] else "GENERAL"
        suggestions.append({
            "topic": nb["topic"],
            "type": "refresh",
            "reason": (
                f"[{label}] Research on '{nb['topic']}' is {nb['age_days']} days old "
                f"(threshold: {nb['threshold_days']}d). "
                + (f"You've queried it {nb['query_count']} times - valuable but stale. " if nb["query_count"] > 0 else "")
                + f"New content likely exists. {urgency.upper()}: refresh this notebook."
            ),
            "confidence": min(0.95, 0.5 + staleness * 0.2),
            "notebook_id": nb["notebook_id"],
        })

    for gap in gaps[:5]:
        edge_boost = get_topic_edge_boost(gap)
        confidence = 0.8 + (edge_boost * 0.15)
        suggestions.append({
            "topic": gap,
            "type": "knowledge_gap",
            "reason": (
                f"'{gap}' appears across multiple research sessions but hasn't been researched as its own topic"
                + (f" - related topics have produced real edge ({edge_boost:.0%} edge rate)" if edge_boost > 0 else "")
            ),
            "confidence": round(min(0.99, confidence), 2),
        })

    if topic_history:
        for t in [t for t in topic_history[:3] if t["count"] >= 2]:
            edge_boost = get_topic_edge_boost(t["topic"])
            suggestions.append({
                "topic": f"{t['topic']} advanced techniques",
                "type": "depth",
                "reason": f"You've researched '{t['topic']}' {t['count']} times - go deeper" + (f" (edge rate: {edge_boost:.0%})" if edge_boost > 0 else ""),
                "confidence": round(0.6 + (edge_boost * 0.2), 2),
            })

    for cd in get_cross_domain_insights()[:3]:
        suggestions.append({
            "topic": f"{cd['entity']} across {' and '.join(cd['domains_bridged'][:2])}",
            "type": "cross_domain",
            "reason": cd["insight"],
            "confidence": round(0.75 + min(0.2, cd["domain_count"] * 0.05), 2),
            "domains": cd["domains_bridged"],
        })

    for entity in graph_summary.get("most_connected", [])[:3]:
        name = entity["entity"]
        conns = entity["connections"]
        if conns >= 3 and name not in gaps:
            suggestions.append({
                "topic": name,
                "type": "bridge",
                "reason": f"'{name}' connects {conns} concepts in your knowledge graph - central to your expertise",
                "confidence": 0.7,
            })

    if not suggestions:
        return json.dumps({"suggestions": [], "message": "Not enough research history yet. Use the 'research' tool a few times to build your knowledge graph."}, indent=2)

    suggestions.sort(key=lambda x: x["confidence"], reverse=True)
    return json.dumps({
        "suggestions": suggestions[:10],
        "stale_notebooks": len(stale),
        "total_entities": graph_summary.get("total_entities", 0),
        "total_edges": graph_summary.get("total_edges", 0),
    }, indent=2)


@server.tool()
async def knowledge_map() -> str:
    """View your knowledge graph - see how concepts connect across research sessions."""
    summary = get_graph_summary()
    if summary["total_entities"] == 0:
        return json.dumps({"message": "Knowledge graph is empty. Use the 'research' tool to start building it.", "total_entities": 0, "total_edges": 0}, indent=2)
    return json.dumps(summary, indent=2)


@server.tool()
async def track_edge(notebook_id: str, result: str, notes: str = "") -> str:
    """Record whether a research session produced real actionable results. result must be 'edge', 'no_edge', or 'negative_edge'."""
    if not notebook_id:
        return "Error: 'notebook_id' is required"
    if not result:
        return "Error: 'result' is required (edge, no_edge, or negative_edge)"
    edge_result = _track_edge_fn(notebook_id, result, notes)
    return json.dumps(edge_result, indent=2)


@server.tool()
async def edge_report() -> str:
    """See your research ROI - which channels, topics, and source types have produced real results vs not."""
    report = _get_edge_report()
    return json.dumps(report, indent=2)


@server.tool()
async def ask_notebook(question: str, notebook_id: str = "", notebook_url: str = "") -> str:
    """Ask a question of a NotebookLM notebook (answer grounded in its sources). Use this instead of notebooklm-mcp ask_question: it runs in auto-research's shared logged-in browser, so many questions and research calls can run at once, and it never copies the Google login into a second browser (which makes Google sign the account out). Pass notebook_id (from research / list_research_notebooks) or notebook_url. Follow-ups in the same notebook keep NotebookLM's chat context."""
    url = notebook_url
    if not url and notebook_id:
        from .library_sync import read_library
        for nb in read_library().get("notebooks", []):
            if nb.get("id") == notebook_id:
                url = nb.get("url", "")
                break
        if not url:
            return json.dumps({"error": f"Unknown notebook_id '{notebook_id}'. Call list_research_notebooks_tool."})
    if not url or url.rstrip("/").endswith("/notebook/creating"):
        return json.dumps({"error": "Need a valid notebook_url or notebook_id."})
    return json.dumps(await _ask_notebook(url, question), indent=2)


@server.tool()
async def notebooklm_login(interactive: bool = False) -> str:
    """Check/refresh the shared NotebookLM (notebook.google.com) login used by research and notebooklm-mcp. interactive=True opens a Chrome window for the user to sign in (use this instead of notebooklm-mcp setup_auth, which no longer detects the rebranded site)."""
    result = await refresh_auth_state(interactive=interactive)
    if result["login_required"] and not interactive:
        result["next_step"] = "Call notebooklm_login(interactive=true) and have the user sign in to Google in the window."
    return json.dumps(result, indent=2)


# ──── Entry point ────

KEEPALIVE_INTERVAL_S = 6 * 60 * 60
KEEPALIVE_TICK_S = 5 * 60


def _keepalive_loop():
    """Every 5 min: close the shared browser if it has sat idle 30 min.
    Every 6h: use the login so Google rotates the session cookies instead of
    forcing a "Verify it's you" re-login, and keep state.json fresh so
    notebooklm-mcp's 24h check reports authenticated=true.
    Several Claude sessions each run a server; the state.json age check means
    only one of them refreshes per window, and the shared browser makes it
    safe even when two do."""
    time.sleep(60)
    while True:
        try:
            reap_if_idle()
        except Exception as e:
            logger.warning(f"Shared browser reap failed: {e}")
        try:
            state = config.NOTEBOOKLM_STATE_PATH
            if not state.exists() or time.time() - state.stat().st_mtime > KEEPALIVE_INTERVAL_S - 600:
                result = asyncio.run(refresh_auth_state())
                logger.info(f"NotebookLM keepalive: {result}")
        except Exception as e:
            logger.warning(f"NotebookLM keepalive failed: {e}")
        time.sleep(KEEPALIVE_TICK_S)


def main():
    """Start the MCP server."""
    logger.info("Starting auto-research v0.5.0")
    if not config.YOUTUBE_API_KEY:
        logger.warning("YOUTUBE_API_KEY not set")
    threading.Thread(target=_keepalive_loop, daemon=True, name="notebooklm-keepalive").start()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
