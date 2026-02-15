"""Cross-notebook knowledge graph — connect concepts across research sessions."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from . import config

logger = logging.getLogger("auto-research.graph")

DATA_DIR = config.PROJECT_DIR / "data"
GRAPH_PATH = DATA_DIR / "knowledge_graph.json"


def _load_graph() -> dict:
    """Load knowledge graph from disk."""
    if GRAPH_PATH.exists():
        try:
            return json.loads(GRAPH_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    return {
        "entities": {},
        "edges": [],
    }


def _save_graph(data: dict) -> None:
    """Save knowledge graph to disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    GRAPH_PATH.write_text(json.dumps(data, indent=2))


def extract_entities(
    topic: str,
    key_concepts: list[str],
    sources: list[dict],
) -> list[dict]:
    """
    Extract entities from a research session.

    Returns list of entity dicts: [{name, type, source}]
    """
    entities = []
    seen = set()

    # Topic itself is an entity
    topic_lower = topic.lower().strip()
    if topic_lower not in seen:
        entities.append({"name": topic_lower, "type": "topic"})
        seen.add(topic_lower)

    # Topic words (for connecting)
    for word in topic_lower.split():
        if len(word) > 3 and word not in seen:
            entities.append({"name": word, "type": "keyword"})
            seen.add(word)

    # Key concepts from transcript analysis
    for concept in key_concepts:
        concept_lower = concept.lower().strip()
        if concept_lower not in seen and len(concept_lower) > 2:
            entities.append({"name": concept_lower, "type": "concept"})
            seen.add(concept_lower)

    # Channels/authors from sources
    for source in sources:
        channel = source.get("channel", "").strip()
        if channel and channel.lower() not in seen:
            entities.append({"name": channel, "type": "channel"})
            seen.add(channel.lower())

        # Paper authors
        for author in source.get("authors", []):
            if author and author.lower() not in seen:
                entities.append({"name": author, "type": "author"})
                seen.add(author.lower())

    return entities


def update_graph(notebook_id: str, entities: list[dict]) -> dict:
    """
    Add entities and edges to the knowledge graph.

    Returns summary of changes.
    """
    graph = _load_graph()
    now = datetime.now(timezone.utc).isoformat()

    new_entities = 0
    new_edges = 0
    updated_entities = 0

    # Add/update entities
    for entity in entities:
        name = entity["name"]
        if name in graph["entities"]:
            # Update existing
            existing = graph["entities"][name]
            if notebook_id not in existing["notebooks"]:
                existing["notebooks"].append(notebook_id)
                updated_entities += 1
            existing["mention_count"] = existing.get("mention_count", 0) + 1
        else:
            # Add new
            graph["entities"][name] = {
                "type": entity["type"],
                "notebooks": [notebook_id],
                "first_seen": now,
                "mention_count": 1,
            }
            new_entities += 1

    # Create edges between entities that co-occur in this notebook
    entity_names = [e["name"] for e in entities]
    existing_edges = {
        (e["from"], e["to"]) for e in graph["edges"]
    }

    for i, name_a in enumerate(entity_names):
        for name_b in entity_names[i + 1:]:
            if name_a == name_b:
                continue

            # Normalize edge direction (alphabetical)
            edge_key = tuple(sorted([name_a, name_b]))

            if edge_key in existing_edges:
                # Update existing edge
                for edge in graph["edges"]:
                    if (edge["from"], edge["to"]) == edge_key or (edge["to"], edge["from"]) == edge_key:
                        if notebook_id not in edge.get("via_notebooks", []):
                            edge["via_notebooks"].append(notebook_id)
                        edge["weight"] = min(1.0, edge.get("weight", 0.5) + 0.1)
                        break
            else:
                # Only create edges between meaningful pairs
                # (topic-concept, topic-channel, concept-concept)
                type_a = graph["entities"].get(name_a, {}).get("type", "")
                type_b = graph["entities"].get(name_b, {}).get("type", "")

                # Skip keyword-keyword edges (too noisy)
                if type_a == "keyword" and type_b == "keyword":
                    continue

                graph["edges"].append({
                    "from": edge_key[0],
                    "to": edge_key[1],
                    "weight": 0.5,
                    "via_notebooks": [notebook_id],
                })
                existing_edges.add(edge_key)
                new_edges += 1

    _save_graph(graph)

    summary = {
        "new_entities": new_entities,
        "updated_entities": updated_entities,
        "new_edges": new_edges,
        "total_entities": len(graph["entities"]),
        "total_edges": len(graph["edges"]),
    }
    logger.info(f"Graph updated: {summary}")
    return summary


def find_connections(topic: str) -> list[dict]:
    """
    Find which existing notebooks and concepts connect to a topic.

    Returns list of connections with strength.
    """
    graph = _load_graph()
    topic_lower = topic.lower().strip()
    topic_words = set(topic_lower.split())

    connections = []
    seen_notebooks = set()

    # Direct match
    if topic_lower in graph["entities"]:
        entity = graph["entities"][topic_lower]
        for nb_id in entity["notebooks"]:
            if nb_id not in seen_notebooks:
                connections.append({
                    "notebook_id": nb_id,
                    "connection": "direct",
                    "via_entity": topic_lower,
                    "strength": 1.0,
                })
                seen_notebooks.add(nb_id)

    # Find related entities via edges
    related = set()
    for edge in graph["edges"]:
        if edge["from"] == topic_lower or any(w == edge["from"] for w in topic_words):
            related.add(edge["to"])
        if edge["to"] == topic_lower or any(w == edge["to"] for w in topic_words):
            related.add(edge["from"])

    # Find notebooks connected to related entities
    for entity_name in related:
        entity = graph["entities"].get(entity_name, {})
        for nb_id in entity.get("notebooks", []):
            if nb_id not in seen_notebooks:
                # Find edge weight
                weight = 0.3
                for edge in graph["edges"]:
                    if (edge["from"] == entity_name or edge["to"] == entity_name):
                        if topic_lower in [edge["from"], edge["to"]] or \
                           any(w in [edge["from"], edge["to"]] for w in topic_words):
                            weight = max(weight, edge.get("weight", 0.3))

                connections.append({
                    "notebook_id": nb_id,
                    "connection": "related",
                    "via_entity": entity_name,
                    "strength": round(weight, 2),
                })
                seen_notebooks.add(nb_id)

    connections.sort(key=lambda x: x["strength"], reverse=True)
    return connections


def get_knowledge_gaps() -> list[str]:
    """
    Find concepts mentioned often but never researched as a primary topic.

    These are gaps in your knowledge — things that keep coming up
    in your research but you've never done a deep dive on.
    """
    graph = _load_graph()

    # Find concepts with high mention counts that appear in multiple notebooks
    # but are of type "concept" or "keyword" (not "topic")
    gaps = []

    for name, entity in graph["entities"].items():
        etype = entity.get("type", "")
        notebooks = entity.get("notebooks", [])
        mentions = entity.get("mention_count", 0)

        # Skip topics (already researched), channels, and authors
        if etype in ("topic", "channel", "author"):
            continue

        # Gap = concept in 2+ notebooks with 3+ mentions, never researched as topic
        if len(notebooks) >= 2 and mentions >= 3:
            gaps.append({
                "concept": name,
                "notebooks_count": len(notebooks),
                "mention_count": mentions,
                "notebooks": notebooks[:5],
            })

    gaps.sort(key=lambda x: x["mention_count"], reverse=True)
    return [g["concept"] for g in gaps[:10]]


def get_cross_domain_insights() -> list[dict]:
    """
    Find concepts that bridge different research domains.

    A "domain" = cluster of topics that share notebooks. When an entity
    appears in notebooks from different domains, it's a cross-pollination
    opportunity — the kind of insight competitors miss because they
    stay in their lane.

    Example: "momentum" appears in both trading research AND marketing research
    → insight: momentum principles from trading could apply to campaign timing.
    """
    graph = _load_graph()

    if len(graph["entities"]) < 5:
        return []

    # Step 1: Build domain clusters from topics
    # Each topic entity defines a domain. Notebooks containing that topic
    # belong to that domain.
    domains: dict[str, set[str]] = {}  # domain_name → set of notebook_ids
    for name, entity in graph["entities"].items():
        if entity.get("type") == "topic":
            domains[name] = set(entity.get("notebooks", []))

    if len(domains) < 2:
        return []  # Need at least 2 research domains

    # Step 2: For each non-topic entity, check if it appears across domains
    cross_domain = []
    for name, entity in graph["entities"].items():
        if entity.get("type") in ("topic", "keyword"):
            continue  # Skip topics themselves and generic keywords

        entity_notebooks = set(entity.get("notebooks", []))
        if len(entity_notebooks) < 2:
            continue

        # Find which domains this entity bridges
        bridged_domains = []
        for domain_name, domain_notebooks in domains.items():
            if entity_notebooks & domain_notebooks:  # Set intersection
                bridged_domains.append(domain_name)

        if len(bridged_domains) >= 2:
            cross_domain.append({
                "entity": name,
                "type": entity.get("type", "concept"),
                "domains_bridged": bridged_domains,
                "domain_count": len(bridged_domains),
                "mentions": entity.get("mention_count", 0),
                "insight": (
                    f"'{name}' appears in both {' and '.join(bridged_domains[:3])} research. "
                    f"Cross-domain patterns here could reveal insights competitors miss."
                ),
            })

    # Sort by domain count (more domains = rarer/more valuable bridge)
    cross_domain.sort(key=lambda x: (x["domain_count"], x["mentions"]), reverse=True)
    return cross_domain[:10]


def get_graph_summary() -> dict:
    """Get a summary of the knowledge graph for the knowledge_map() tool."""
    graph = _load_graph()

    # Entity counts by type
    type_counts = {}
    for entity in graph["entities"].values():
        etype = entity.get("type", "unknown")
        type_counts[etype] = type_counts.get(etype, 0) + 1

    # Most connected entities
    connection_counts = {}
    for edge in graph["edges"]:
        connection_counts[edge["from"]] = connection_counts.get(edge["from"], 0) + 1
        connection_counts[edge["to"]] = connection_counts.get(edge["to"], 0) + 1

    most_connected = sorted(
        connection_counts.items(),
        key=lambda x: x[1],
        reverse=True,
    )[:10]

    # Strongest edges
    strong_edges = sorted(
        graph["edges"],
        key=lambda x: x.get("weight", 0),
        reverse=True,
    )[:10]

    # Knowledge gaps
    gaps = get_knowledge_gaps()

    cross_domain = get_cross_domain_insights()

    return {
        "total_entities": len(graph["entities"]),
        "total_edges": len(graph["edges"]),
        "entity_types": type_counts,
        "most_connected": [
            {"entity": name, "connections": count}
            for name, count in most_connected
        ],
        "strongest_links": [
            {"from": e["from"], "to": e["to"], "weight": e.get("weight", 0)}
            for e in strong_edges
        ],
        "knowledge_gaps": gaps,
        "cross_domain_insights": [
            {"entity": c["entity"], "bridges": c["domains_bridged"], "insight": c["insight"]}
            for c in cross_domain[:5]
        ],
    }
