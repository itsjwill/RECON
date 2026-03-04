<div align="center">

# RECON — AI Research Engine That Finds What Others Miss

### Free alternative to Perplexity Pro, Elicit, Consensus, SciSpace

![Stars](https://img.shields.io/github/stars/itsjwill/RECON?style=for-the-badge)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.11+-blue?style=for-the-badge)
![MCP](https://img.shields.io/badge/MCP-Claude_Code-orange?style=for-the-badge)
![Updated](https://img.shields.io/badge/Updated-2026-blue?style=for-the-badge)

**One command. YouTube, papers, podcasts — scored, filtered, and loaded into a queryable NotebookLM notebook.**

</div>

---

## What This Does

One command:

```
research("prompt engineering techniques", sources=["youtube", "papers"])
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

## Why Not Just Use Perplexity?

| Feature | Perplexity Pro | Elicit | RECON |
|---------|---------------|--------|-------|
| **Source scoring** | No — shows top results | Citation count only | 8-factor scoring (substance, credibility, engagement, recency, velocity, relevance, transcript, feedback) |
| **Transcript analysis** | No | No | Analyzes what the speaker actually said — hedging, failures, specificity |
| **Clickbait detection** | No | N/A | Penalizes ALL CAPS titles, sensational language, empty descriptions |
| **Knowledge graph** | No | No | Connects concepts across sessions, finds gaps, suggests next research |
| **Feedback loop** | No | No | Tracks which sources produced real results — boosts them in future searches |
| **Expert mode** | No | No | Inverts popularity scoring to find practitioners over influencers |
| **NotebookLM integration** | No | No | Creates queryable notebooks you can have conversations with |
| **Contrarian search** | No | No | Auto-searches for criticism and opposing viewpoints |
| **Cost** | $20/month | $10/month | **$0/month** |

---

## Expert Mode

Most research tools find you **popular** content. Popular = mainstream consensus. RECON has an `expert` mode that **inverts popularity scoring** to find practitioners instead of influencers:

| Signal | General Mode | Expert Mode |
|--------|-------------|-------------|
| Views | High views = good | **5K-50K sweet spot** (practitioners, not mainstream) |
| Velocity | Trending = surface first | **Removed entirely** (trending = consensus) |
| Transcript | 14% weight | **24% weight** (substance over hype) |
| Credibility | 9% weight | **20% weight** (practitioners over influencers) |
| Engagement | Like ratio | Like ratio (same — honest signal either way) |

The thesis: a 12K-view video from someone who discusses **what went wrong** and **cites specific data** is worth more than a 500K-view video that says "this technique is GUARANTEED to work."

```
research("RAG architecture", mode="expert", sources=["youtube", "papers"])
```

---

## Architecture

```
research("prompt engineering")
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
[Feedback Loop]          [Outcome Tracker]
 learns which             tracks which research
 channels work            produced real results
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

## Substance Detection

Most YouTube scoring looks at views and likes. RECON looks at **what the person actually said**.

Three signals that are hard to fake in a 20-minute video:

### 1. Hedging Language (30% of substance score)
Nuanced thinkers acknowledge complexity:
> "on the other hand", "it depends", "the tradeoff", "there are exceptions", "context matters"

### 2. Failure Discussion (35% — heaviest factor)
Practitioners talk about what went wrong:
> "doesn't work when", "the risk is", "I was wrong", "lesson learned", "the hard way"

### 3. Specificity (35%)
Grounded speakers cite data:
> Years, percentages, dollar amounts, "according to", "research shows", "study found"

A video can have 1M views and perfect engagement but still score low on substance if the speaker never hedges, never discusses failures, and never cites specific data.

---

## Credibility Scoring

**What got dropped:** Self-reported credentials. Anyone can type "10 years experience" in their channel description. Meaningless.

**What replaced it:**

| Factor | Weight | Why |
|--------|--------|-----|
| Clickbait Detection | 45% | Most honest signal. "GUARANTEED RESULTS" is unfakeable garbage. |
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
- **Cross-Domain Insights** — entities that bridge different research areas (e.g., "embeddings" appears in both NLP AND image generation research)
- **Bridge Concepts** — central nodes that connect many topics in your expertise
- **Stale Notebooks** — time-sensitive research decays in 14 days, general in 30

```
suggest_research()
  -> "You've researched 'RAG' 3 times — go deeper"
  -> "'vector databases' appears across 4 notebooks but was never researched directly"
  -> "'embeddings' bridges NLP and computer vision research — cross-domain opportunity"
  -> "Research on 'LLM fine-tuning' is 18 days old — URGENT: refresh"
```

---

## Outcome Tracker

The feedback loop most research tools are missing:

```
Research -> Implement -> Results -> Better Research
```

After you implement something from a research notebook:

```
track_edge(notebook_id="research-rag-architecture", result="edge", notes="New chunking strategy improved retrieval accuracy 23%")
```

The system learns:
- Which **channels** produce real results (boosted in future searches)
- Which **topics** have high success rates (surfaced first in suggestions)
- Which **source types** deliver (YouTube vs papers vs podcasts)
- Overall **research ROI** — what % of sessions produced actionable insights

```
edge_report()
  -> "3Blue1Brown: 3/4 sessions produced results (75%)"
  -> "AI topic success rate: 42% (above average)"
  -> "YouTube delivers 2x more actionable content than papers for applied topics"
```

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/itsjwill/RECON.git
cd RECON
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
      "command": "/path/to/RECON/.venv/bin/python",
      "args": ["-m", "src.server"],
      "cwd": "/path/to/RECON"
    }
  }
}
```

### 4. Use it

```
research("prompt engineering techniques", sources=["youtube", "papers"])
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
RECON/
├── src/
│   ├── server.py            # MCP server — 9 tools
│   ├── config.py            # Environment + settings
│   ├── youtube_search.py    # YouTube API + 8-factor scoring + expert mode
│   ├── transcript.py        # Transcript extraction + substance detection
│   ├── credibility.py       # Channel credibility (clickbait, consistency)
│   ├── paper_search.py      # OpenAlex academic paper search
│   ├── podcast_search.py    # Podcast Index search
│   ├── notebook_manager.py  # NotebookLM browser automation
│   ├── library_sync.py      # Shared library.json management
│   ├── feedback.py          # Usage tracking + stale detection
│   ├── knowledge_graph.py   # Cross-notebook entity graph
│   └── edge_tracker.py      # Research outcome tracking
├── data/
│   ├── knowledge_graph.json # Entity graph (auto-created)
│   └── feedback.json        # Usage data (auto-created)
├── pyproject.toml
├── requirements.txt
├── .env.example             # API key template
└── .env                     # Your API keys (not committed)
```

---

## Join The Community

Built by the team at **[The Agentic Advantage](https://www.skool.com/ai-elite-9507/about?ref=67521860944147018da6145e3db6e51c)**

Get help setting up RECON, share research workflows, and connect with other builders using AI tools that actually work.

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

**Built for finding what matters, not collecting bookmarks.**

</div>
