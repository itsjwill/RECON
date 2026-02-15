<div align="center">

# RECON — Multi-Source Research Engine That Learns What Gives You Edge

### Research YouTube, academic papers, and podcasts in one command. Scores every source for substance, credibility, and transcript depth. Builds a compounding knowledge graph that gets smarter the more you use it.

![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.11+-blue?style=for-the-badge)
![MCP](https://img.shields.io/badge/MCP-Claude_Code-orange?style=for-the-badge)
![Updated](https://img.shields.io/badge/Updated-2026-blue?style=for-the-badge)

**Compress weeks of research into minutes. Stop reading garbage. Let the engine find edge.**

</div>

---

## What This Does

One command:

```
research("order flow trading", sources=["youtube", "papers"], mode="trading")
```

What happens:
1. Searches YouTube (25 candidates), OpenAlex (474M papers), Podcast Index
2. Grabs transcripts and scores them for **substance** — hedging language, failure discussion, specific data
3. Scores channel **credibility** — clickbait detection, upload consistency, description depth
4. Auto-searches for **contrarian viewpoints** (criticism, limitations, risks)
5. Selects top sources across all types, creates a **NotebookLM notebook**
6. Updates your **knowledge graph** — connects concepts across sessions
7. Registers in the **feedback loop** — learns which channels actually produce results

Next research session? The system already knows what worked last time.

---

## The Edge Problem

Most research tools find you **popular** content. Popular = consensus. Consensus = no edge.

RECON has a `trading` mode that **inverts popularity scoring**:

| Signal | General Mode | Trading Mode |
|--------|-------------|--------------|
| Views | High views = good | **5K-50K sweet spot** (practitioners, not mainstream) |
| Velocity | Trending = surface first | **Removed entirely** (trending = consensus) |
| Transcript | 14% weight | **24% weight** (substance over hype) |
| Credibility | 9% weight | **20% weight** (practitioners over influencers) |
| Engagement | Like ratio | Like ratio (same — honest signal either way) |

The thesis: a 12K-view video from a trader who discusses **what went wrong** and **cites specific data** is worth more than a 500K-view video that says "this strategy is GUARANTEED to work."

---

## Architecture

```
research("order flow trading")
         |
    +----+--------------------+
    v                         v
[YouTube API]           [OpenAlex API]          [Podcast Index]
 25 videos               20 papers               20 episodes
    |                         |                       |
    v                         v                       v
[Transcript              [Paper                  [Podcast
 Analyzer]                Scorer]                  Scorer]
 - substance              - citations             - episode count
 - hedging                - open access           - regularity
 - failures               - journal tier          - description
 - specificity            - recency               - relevance
    |                         |                       |
    +----+--------------------+-----------------------+
         v
[Credibility Scorer]
 - clickbait detection (45%)
 - upload consistency (25%)
 - description substance (20%)
 - channel age (10%)
         |
         v
[Contrarian Search]
 - auto-searches "topic + criticism/problems/risks"
 - reserves 1 slot for the best opposing viewpoint
         |
         v
[Top N Sources Selected]
         |
    +----+----+
    v         v
[NotebookLM]  [Knowledge Graph]
 notebook       entities
 created        edges
    |            |
    +----+-------+
         v
[Feedback Loop]          [Edge Tracker]
 learns which             tracks which research
 channels work            produced real P&L
```

---

## 9 MCP Tools

| Tool | What It Does |
|------|-------------|
| `research` | Full pipeline: multi-source search, notebook creation, graph update |
| `search_videos` | YouTube search with transcript + credibility scoring |
| `search_papers` | OpenAlex academic paper search (free, 474M+ papers) |
| `list_research_notebooks` | List all auto-created NotebookLM notebooks |
| `rate_research` | Rate a notebook 1-5 — feeds back into future scoring |
| `suggest_research` | AI-suggested topics from knowledge graph gaps |
| `knowledge_map` | Visualize concept connections across all research |
| `track_edge` | Record whether research produced real results |
| `edge_report` | ROI report — which sources/channels actually deliver |

---

## Substance Detection (Transcript v2)

Most YouTube scoring looks at views and likes. RECON looks at **what the person actually said**.

Three signals that are hard to fake in a 20-minute video:

### 1. Hedging Language (30% of substance score)
Nuanced thinkers acknowledge complexity:
> "on the other hand", "it depends", "the tradeoff", "there are exceptions", "context matters"

### 2. Failure Discussion (35% — heaviest factor)
Practitioners talk about what went wrong:
> "doesn't work when", "the risk is", "I was wrong", "lesson learned", "lost money", "the hard way"

### 3. Specificity (35%)
Grounded speakers cite data:
> Years, percentages, dollar amounts, "according to", "research shows", "study found"

A video can have 1M views and perfect engagement but still score low on substance if the speaker never hedges, never discusses failures, and never cites specific data.

---

## Credibility Scoring (v2)

**What got dropped:** Practitioner keywords. Anyone can type "CFA" or "10 years experience" in their channel description. Self-reported credentials are meaningless.

**What replaced it:**

| Factor | Weight | Why |
|--------|--------|-----|
| Clickbait Detection | 45% | Most honest signal. "GUARANTEED PROFITS" is unfakeable garbage. |
| Upload Consistency | 25% | Regular uploads over years = committed to the craft |
| Description Substance | 20% | Length + specificity (URLs, dates, contact info) |
| Channel Age | 10% | Weak signal — punishes early adopters of new topics |

Red flags that trigger penalties:
- Channel under 3 months old
- Fewer than 10 videos
- 30%+ clickbait titles
- 50%+ ALL CAPS titles
- Empty channel description

---

## Knowledge Graph

Every research session extracts entities (topics, concepts, channels, authors) and links them.

After 5+ sessions, the graph reveals:

- **Knowledge Gaps** — concepts that keep appearing but haven't been researched directly
- **Cross-Domain Insights** — entities that bridge different research areas (e.g., "momentum" appears in both trading AND marketing research)
- **Bridge Concepts** — central nodes that connect many topics in your expertise
- **Stale Notebooks** — trading research decays in 14 days (alpha gets arbed), general in 30

```
suggest_research()
  -> "You've researched 'order flow' 3 times — go deeper"
  -> "'market microstructure' appears across 4 notebooks but was never researched directly"
  -> "'momentum' bridges trading and marketing research — cross-domain opportunity"
  -> "[TRADING] Research on 'scalping setups' is 18 days old — URGENT: refresh"
```

---

## Edge Tracker

The feedback loop most research tools are missing:

```
Research -> Implement -> Results -> Better Research
```

After you implement something from a research notebook:

```
track_edge(notebook_id="research-ofi-vpin", result="edge", notes="OFI strategy increased win rate 15%")
```

The system learns:
- Which **channels** produce real results (boosted in future searches)
- Which **topics** have high edge rates (surfaced first in suggestions)
- Which **source types** deliver (YouTube vs papers vs podcasts)
- Overall **research ROI** — what % of sessions produced actionable edge

```
edge_report()
  -> "SMB Capital: 3/4 sessions produced edge (75%)"
  -> "Trading topic edge rate: 42% (above average)"
  -> "YouTube delivers 2x more edge than papers for trading topics"
```

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/itsjwill/auto-research-mcp.git
cd auto-research-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

### 2. Environment variables

```bash
cp .env.example .env
# Edit .env:
YOUTUBE_API_KEY=your_key_here  # https://console.cloud.google.com/apis/credentials
```

YouTube API key is the only requirement. OpenAlex (papers) needs no key. Podcast Index is optional.

### 3. Add to Claude Code MCP config

```json
{
  "mcpServers": {
    "auto-research": {
      "command": "/path/to/auto-research-mcp/.venv/bin/python",
      "args": ["-m", "src.server"],
      "cwd": "/path/to/auto-research-mcp"
    }
  }
}
```

### 4. Use it

```
research("order flow trading", mode="trading", sources=["youtube", "papers"])
```

---

## Cost

| Component | Cost |
|-----------|------|
| YouTube Data API | Free (10,000 units/day) |
| OpenAlex Papers | Free (no key, 100K req/day) |
| Transcripts | Free (youtube-transcript-api) |
| NotebookLM | Free (Google account) |
| Podcast Index | Free (optional, needs registration) |
| **Total** | **$0/month** |

---

## File Structure

```
auto-research-mcp/
├── src/
│   ├── server.py            # MCP server — 9 tools, v0.4.0
│   ├── config.py            # Environment + settings
│   ├── youtube_search.py    # YouTube API + 8-factor scoring + trading mode
│   ├── transcript.py        # Transcript extraction + substance detection
│   ├── credibility.py       # Channel credibility (clickbait, consistency)
│   ├── paper_search.py      # OpenAlex academic paper search
│   ├── podcast_search.py    # Podcast Index search
│   ├── notebook_manager.py  # NotebookLM browser automation
│   ├── library_sync.py      # Shared library.json management
│   ├── feedback.py          # Usage tracking + stale detection
│   ├── knowledge_graph.py   # Cross-notebook entity graph
│   └── edge_tracker.py      # Research ROI tracking
├── data/
│   ├── knowledge_graph.json # Entity graph (auto-created)
│   └── feedback.json        # Usage data (auto-created)
├── pyproject.toml
├── requirements.txt
└── .env                     # API keys (not committed)
```

---

## More From itsjwill

| Repo | What It Does |
|------|-------------|
| [vanta](https://github.com/itsjwill/vanta) | Open source AI video engine — voice cloning, AI avatars, auto-captions |
| [seoctopus](https://github.com/itsjwill/seoctopus) | 8-armed SEO intelligence — MCP server with 23 tools |
| [ghosthacker](https://github.com/itsjwill/ghosthacker) | Adversarial AI pentester — CHAOS vs ORDER dual-agent system |
| [claude-memory](https://github.com/itsjwill/claude-memory) | Persistent memory for Claude Code — auto-capture decisions and patterns |

---

<div align="center">

**Built for finding edge, not collecting bookmarks.**

</div>
