"""Auto-Research MCP Server v2 — multi-source research + knowledge graph + feedback."""

import asyncio
import json
import logging
import os
import signal
import sys
import traceback

from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
import mcp.server.stdio
import mcp.types as types

from . import config
from .youtube_search import search_and_curate
from .paper_search import search_papers
from .podcast_search import search_podcasts
from .notebook_manager import create_notebook_with_sources
from .library_sync import add_notebook_to_library, list_research_notebooks
from .feedback import record_research, rate_notebook, get_feedback_summary, get_topic_history, get_stale_notebooks
from .knowledge_graph import (
    extract_entities, update_graph, find_connections,
    get_knowledge_gaps, get_graph_summary, get_cross_domain_insights,
)
from .edge_tracker import track_edge, get_edge_report, get_topic_edge_boost

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("auto-research")

SERVER_NAME = "auto-research"
SERVER_VERSION = "0.4.0"

server = Server(SERVER_NAME)


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """Return available tools."""
    return [
        # ──── Core tools ────
        types.Tool(
            name="research",
            description=(
                "Research a topic by finding the top sources (YouTube videos, academic papers, "
                "podcasts) and creating a NotebookLM notebook with them. Each source is scored "
                "for quality, credibility, and transcript depth. The notebook becomes immediately "
                "queryable via NotebookLM's ask_question tool. After research, the knowledge "
                "graph is updated to connect concepts across sessions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "The research topic (e.g., 'order flow trading strategies')",
                    },
                    "num_sources": {
                        "type": "integer",
                        "description": "Number of top sources to include (default: 5, max: 10)",
                        "default": 5,
                    },
                    "sources": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["youtube", "papers", "podcasts"]},
                        "description": "Source types to search. Default: ['youtube']. Use ['youtube', 'papers'] for multi-source. Use ['all'] for everything.",
                        "default": ["youtube"],
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["general", "trading"],
                        "description": "Scoring mode. 'general' = popular content is good (learning). 'trading' = inverted: low-view practitioner content scored higher, alpha decay awareness, substance over hype.",
                        "default": "general",
                    },
                },
                "required": ["topic"],
            },
        ),
        types.Tool(
            name="search_videos",
            description=(
                "Search YouTube for videos on a topic and return scored/curated results "
                "with transcript quality analysis and credibility scores. "
                "Useful for previewing what videos would be selected."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "YouTube search query",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default: 10)",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="search_papers",
            description=(
                "Search OpenAlex for academic papers on a topic. Returns scored results "
                "with citation counts, open access status, and credibility scores. "
                "Free, no API key needed, 474M+ papers."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Academic search query",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default: 10)",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="list_research_notebooks",
            description=(
                "List all research notebooks created by auto-research. Shows notebook IDs, "
                "names, topics, and URLs so you can query them via NotebookLM's ask_question."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        # ──── v2 tools ────
        types.Tool(
            name="rate_research",
            description=(
                "Rate a research notebook 1-5 to improve future research quality. "
                "The feedback loop learns which channels and sources produce the best results "
                "and boosts them in future searches."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "notebook_id": {
                        "type": "string",
                        "description": "The notebook ID to rate (from list_research_notebooks)",
                    },
                    "rating": {
                        "type": "integer",
                        "description": "Rating from 1 (poor) to 5 (excellent)",
                        "minimum": 1,
                        "maximum": 5,
                    },
                },
                "required": ["notebook_id", "rating"],
            },
        ),
        types.Tool(
            name="suggest_research",
            description=(
                "Get AI-suggested research topics based on your knowledge graph. "
                "Finds gaps in your knowledge — concepts that keep appearing in your research "
                "but haven't been explored as their own topic yet. Also suggests related "
                "topics to deepen your expertise."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="knowledge_map",
            description=(
                "View your knowledge graph — see how concepts connect across research sessions. "
                "Shows most connected entities, strongest links between topics, and knowledge gaps."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        # ──── v3 tools — edge tracking ────
        types.Tool(
            name="track_edge",
            description=(
                "Record whether a research session produced real trading/business edge. "
                "This closes the loop: Research → Implement → Results → Better Research. "
                "After you implement something from a research notebook and see results, "
                "use this to tell the system what worked. It learns which channels, topics, "
                "and source types actually produce profitable outcomes."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "notebook_id": {
                        "type": "string",
                        "description": "The notebook ID (from list_research_notebooks)",
                    },
                    "result": {
                        "type": "string",
                        "enum": ["edge", "no_edge", "negative_edge"],
                        "description": "'edge' = produced profitable results, 'no_edge' = neutral/didn't help, 'negative_edge' = lost money or wasted time",
                    },
                    "notes": {
                        "type": "string",
                        "description": "What specifically worked or didn't (e.g., 'OFI strategy from SMB Capital video increased win rate 15%')",
                        "default": "",
                    },
                },
                "required": ["notebook_id", "result"],
            },
        ),
        types.Tool(
            name="edge_report",
            description=(
                "See your research ROI — which channels, topics, and source types have "
                "produced real edge vs not. Shows top edge producers, channels to avoid, "
                "and overall research effectiveness rate."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    """Route tool calls."""
    if arguments is None:
        arguments = {}

    try:
        if name == "research":
            return await _handle_research(arguments)
        elif name == "search_videos":
            return await _handle_search_videos(arguments)
        elif name == "search_papers":
            return await _handle_search_papers(arguments)
        elif name == "list_research_notebooks":
            return await _handle_list_notebooks(arguments)
        elif name == "rate_research":
            return await _handle_rate_research(arguments)
        elif name == "suggest_research":
            return await _handle_suggest_research(arguments)
        elif name == "knowledge_map":
            return await _handle_knowledge_map(arguments)
        elif name == "track_edge":
            return await _handle_track_edge(arguments)
        elif name == "edge_report":
            return await _handle_edge_report(arguments)
        else:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        logger.error(f"Error in {name}: {e}\n{traceback.format_exc()}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]


async def _handle_research(args: dict) -> list[types.TextContent]:
    """Full v2 pipeline: multi-source search -> create notebook -> knowledge graph -> feedback."""
    topic = args.get("topic", "")
    num_sources = min(args.get("num_sources", args.get("num_videos", 5)), config.MAX_NUM_VIDEOS)
    source_types = args.get("sources", ["youtube"])
    mode = args.get("mode", "general")

    if not topic:
        return [types.TextContent(type="text", text="Error: 'topic' is required")]

    # Expand "all"
    if "all" in source_types:
        source_types = ["youtube", "papers", "podcasts"]

    # ──── Step 1: Search all requested source types ────
    all_sources = []
    all_key_concepts = []

    if "youtube" in source_types:
        if not config.YOUTUBE_API_KEY:
            return [types.TextContent(type="text", text="Error: YOUTUBE_API_KEY not set in .env")]

        logger.info(f"Searching YouTube for: {topic} (mode={mode})")
        yt_result = await search_and_curate(topic, num_sources, mode=mode)

        if "error" in yt_result:
            logger.warning(f"YouTube search error: {yt_result['error']}")
        else:
            for v in yt_result.get("videos", []):
                v["source_type"] = "youtube"
                all_sources.append(v)
                all_key_concepts.extend(v.get("key_concepts", []))

    if "papers" in source_types:
        logger.info(f"Searching papers for: {topic}")
        paper_result = search_papers(topic, num_sources)

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
        return [types.TextContent(type="text", text=f"No sources found for '{topic}'. Try a different query.")]

    # ──── Step 1b: Contrarian search — auto-find opposing viewpoints ────
    # For every research session, we search for criticism/problems to ensure
    # balanced knowledge. Nobody wants an echo chamber.
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
        contra_paper = search_papers(contrarian_query, 2)
        if "error" not in contra_paper:
            for p in contra_paper.get("papers", []):
                p["source_type"] = "paper"
                p["is_contrarian"] = True
                contrarian_sources.append(p)

    # ──── Step 2: Unified ranking — select top N across all source types ────
    # Reserve 1 slot for the best contrarian source (if any found)
    all_sources.sort(key=lambda x: x.get("score", 0), reverse=True)

    if contrarian_sources:
        contrarian_sources.sort(key=lambda x: x.get("score", 0), reverse=True)
        best_contrarian = contrarian_sources[0]
        # Main picks get N-1 slots, contrarian gets 1
        selected_sources = all_sources[:num_sources - 1]
        selected_sources.append(best_contrarian)
    else:
        selected_sources = all_sources[:num_sources]

    source_urls = [s["url"] for s in selected_sources if s.get("url")]

    if not source_urls:
        return [types.TextContent(type="text", text="No valid URLs found in selected sources.")]

    # ──── Step 3: Create NotebookLM notebook ────
    logger.info(f"Creating NotebookLM notebook with {len(source_urls)} sources...")
    nb_result = await create_notebook_with_sources(topic, source_urls)

    notebook_url = nb_result.get("notebook_url", "")
    videos_added = nb_result.get("videos_added", [])
    videos_failed = nb_result.get("videos_failed", [])
    nb_error = nb_result.get("error")

    # ──── Step 4: Register in shared library ────
    notebook_id = None
    if notebook_url:
        source_type_counts = {}
        for s in selected_sources:
            st = s.get("source_type", "unknown")
            source_type_counts[st] = source_type_counts.get(st, 0) + 1

        source_summary = ", ".join(f"{count} {stype}" for stype, count in source_type_counts.items())
        name = f"Research: {topic}"
        description = f"Auto-researched: {source_summary} on {topic}"
        topics_list = topic.lower().split()[:5]

        notebook_id = add_notebook_to_library(
            url=notebook_url,
            name=name,
            description=description,
            topics=topics_list,
            videos=selected_sources,
        )

    # ──── Step 5: Record feedback ────
    if notebook_id:
        record_research(notebook_id, topic, selected_sources)

    # ──── Step 6: Update knowledge graph ────
    if notebook_id:
        entities = extract_entities(topic, all_key_concepts, selected_sources)
        graph_update = update_graph(notebook_id, entities)
        connections = find_connections(topic)
    else:
        graph_update = None
        connections = []

    # ──── Build response ────
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

    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_search_videos(args: dict) -> list[types.TextContent]:
    """Search YouTube with v2 scoring (transcript + credibility + feedback)."""
    query = args.get("query", "")
    limit = args.get("limit", 10)

    if not query:
        return [types.TextContent(type="text", text="Error: 'query' is required")]

    if not config.YOUTUBE_API_KEY:
        return [types.TextContent(type="text", text="Error: YOUTUBE_API_KEY not set in .env")]

    result = await search_and_curate(query, limit)
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_search_papers(args: dict) -> list[types.TextContent]:
    """Search OpenAlex for academic papers."""
    query = args.get("query", "")
    limit = args.get("limit", 10)

    if not query:
        return [types.TextContent(type="text", text="Error: 'query' is required")]

    result = search_papers(query, limit)
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_list_notebooks(args: dict) -> list[types.TextContent]:
    """List auto-research notebooks."""
    notebooks = list_research_notebooks()

    if not notebooks:
        return [types.TextContent(type="text", text="No auto-research notebooks yet. Use the 'research' tool to create one.")]

    summary = get_feedback_summary()

    return [types.TextContent(type="text", text=json.dumps({
        "notebooks": notebooks,
        "feedback_summary": summary,
    }, indent=2))]


async def _handle_rate_research(args: dict) -> list[types.TextContent]:
    """Rate a notebook to improve future research quality."""
    notebook_id = args.get("notebook_id", "")
    rating = args.get("rating", 0)

    if not notebook_id:
        return [types.TextContent(type="text", text="Error: 'notebook_id' is required")]

    result = rate_notebook(notebook_id, rating)
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_suggest_research(args: dict) -> list[types.TextContent]:
    """Suggest research topics based on knowledge graph gaps, stale notebooks, and edge data."""
    gaps = get_knowledge_gaps()
    topic_history = get_topic_history()
    graph_summary = get_graph_summary()
    stale = get_stale_notebooks()

    suggestions = []

    # Suggestion type 0: STALE RESEARCH ALERTS (highest priority)
    for nb in stale[:3]:
        staleness = nb["staleness"]
        urgency = "urgent" if staleness >= 2.0 else "recommended"
        label = "TRADING" if nb["is_trading"] else "GENERAL"
        suggestions.append({
            "topic": nb["topic"],
            "type": "refresh",
            "reason": (
                f"[{label}] Research on '{nb['topic']}' is {nb['age_days']} days old "
                f"(threshold: {nb['threshold_days']}d). "
                + (f"You've queried it {nb['query_count']} times — it's valuable but stale. " if nb["query_count"] > 0 else "")
                + f"New content likely exists. {urgency.upper()}: refresh this notebook."
            ),
            "confidence": min(0.95, 0.5 + staleness * 0.2),
            "notebook_id": nb["notebook_id"],
        })

    # Suggestion type 1: Knowledge gaps (boosted by edge data)
    for gap in gaps[:5]:
        edge_boost = get_topic_edge_boost(gap)
        confidence = 0.8 + (edge_boost * 0.15)  # Edge-producing topics get higher confidence
        suggestions.append({
            "topic": gap,
            "type": "knowledge_gap",
            "reason": (
                f"'{gap}' appears across multiple research sessions but hasn't been researched as its own topic"
                + (f" — related topics have produced real edge ({edge_boost:.0%} edge rate)" if edge_boost > 0 else "")
            ),
            "confidence": round(min(0.99, confidence), 2),
        })

    # Suggestion type 2: Deepen most-researched topics
    if topic_history:
        top_topics = [t for t in topic_history[:3] if t["count"] >= 2]
        for t in top_topics:
            edge_boost = get_topic_edge_boost(t["topic"])
            suggestions.append({
                "topic": f"{t['topic']} advanced techniques",
                "type": "depth",
                "reason": (
                    f"You've researched '{t['topic']}' {t['count']} times — go deeper"
                    + (f" (edge rate: {edge_boost:.0%})" if edge_boost > 0 else "")
                ),
                "confidence": round(0.6 + (edge_boost * 0.2), 2),
            })

    # Suggestion type 3: Cross-domain insights
    cross_domain = get_cross_domain_insights()
    for cd in cross_domain[:3]:
        suggestions.append({
            "topic": f"{cd['entity']} across {' and '.join(cd['domains_bridged'][:2])}",
            "type": "cross_domain",
            "reason": cd["insight"],
            "confidence": round(0.75 + min(0.2, cd["domain_count"] * 0.05), 2),
            "domains": cd["domains_bridged"],
        })

    # Suggestion type 4: Bridge concepts
    for entity in graph_summary.get("most_connected", [])[:3]:
        name = entity["entity"]
        connections = entity["connections"]
        if connections >= 3 and name not in gaps:
            suggestions.append({
                "topic": name,
                "type": "bridge",
                "reason": f"'{name}' connects {connections} concepts in your knowledge graph — central to your expertise",
                "confidence": 0.7,
            })

    if not suggestions:
        return [types.TextContent(type="text", text=json.dumps({
            "suggestions": [],
            "message": "Not enough research history yet. Use the 'research' tool a few times to build your knowledge graph, then come back for suggestions.",
        }, indent=2))]

    # Sort by confidence (stale alerts naturally rank high)
    suggestions.sort(key=lambda x: x["confidence"], reverse=True)

    return [types.TextContent(type="text", text=json.dumps({
        "suggestions": suggestions[:10],
        "stale_notebooks": len(stale),
        "total_entities": graph_summary.get("total_entities", 0),
        "total_edges": graph_summary.get("total_edges", 0),
    }, indent=2))]


async def _handle_knowledge_map(args: dict) -> list[types.TextContent]:
    """Show the knowledge graph."""
    summary = get_graph_summary()

    if summary["total_entities"] == 0:
        return [types.TextContent(type="text", text=json.dumps({
            "message": "Knowledge graph is empty. Use the 'research' tool to start building it.",
            "total_entities": 0,
            "total_edges": 0,
        }, indent=2))]

    return [types.TextContent(type="text", text=json.dumps(summary, indent=2))]


async def _handle_track_edge(args: dict) -> list[types.TextContent]:
    """Record whether research produced real edge."""
    notebook_id = args.get("notebook_id", "")
    result = args.get("result", "")
    notes = args.get("notes", "")

    if not notebook_id:
        return [types.TextContent(type="text", text="Error: 'notebook_id' is required")]
    if not result:
        return [types.TextContent(type="text", text="Error: 'result' is required (edge, no_edge, or negative_edge)")]

    edge_result = track_edge(notebook_id, result, notes)
    return [types.TextContent(type="text", text=json.dumps(edge_result, indent=2))]


async def _handle_edge_report(args: dict) -> list[types.TextContent]:
    """Show the full edge report."""
    report = get_edge_report()
    return [types.TextContent(type="text", text=json.dumps(report, indent=2))]


async def async_main():
    """Start the MCP server with stdio transport."""
    logger.info(f"Starting {SERVER_NAME} v{SERVER_VERSION}")

    if not config.YOUTUBE_API_KEY:
        logger.warning("YOUTUBE_API_KEY not set — YouTube search will fail until configured")

    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        logger.info("Server ready")
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name=SERVER_NAME,
                server_version=SERVER_VERSION,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main():
    """Entry point."""
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        sys.exit(0)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("Shutting down (KeyboardInterrupt)")
    except Exception as e:
        logger.error(f"Fatal error: {e}\n{traceback.format_exc()}")
        sys.exit(1)


if __name__ == "__main__":
    main()
