# Scout Rearchitecture

This document defines the target architecture for rebuilding Scout around atomic tools and a single top-level business skill.

## Goal

Rebuild Scout so that:
- existing scattered Scout capabilities are removed or folded down into atomic tools
- search providers such as Tavily, SerpAPI, and Jina are callable independently
- fixed-source collectors such as RSS, HTML, GitHub, and Reddit are callable independently
- merge, ranking, deduplication, and archive behavior become shared runtime capabilities
- `tech-news-digest` becomes the only top-level business skill used for digest/report generation
- the system supports three execution modes:
  - `sources-only`
  - `search-only`
  - `hybrid`

This keeps Scout small at the top and flexible at the bottom.

## Target Stack

### Layer 1: Soul

Scout should have one global soul, not many competing personalities.

Soul responsibilities:
- prioritize signal over volume
- prefer official and first-party sources over commentary
- default to low API spend
- require explicit search intent instead of running web search automatically
- support Chinese digest/report output by default
- preserve source attribution and structured evidence

The soul should not contain provider-specific details, command syntax, or file layout. It should only define operating principles and output philosophy.

## Layer 2: Atomic Tools

Scout should expose only atomic, reusable tools. Each tool should do one thing and return a normalized payload.

### Search Tools
- `tavily_search`
- `serpapi_search`
- `jina_fetch` or `jina_reader`

### Fixed-Source Tools
- `rss_fetch`
- `html_fetch`
- `github_fetch`
- `reddit_fetch`

### Runtime Tools
- `merge_rank`
- `archive_store`
- `template_render`
- `delivery_discord_forum`
- `delivery_email`

## Atomic Tool Contract

Every atomic tool should:
- accept explicit input only
- avoid hidden business logic
- return normalized article or document records
- include status and error metadata
- be usable independently from any digest/report workflow

### Normalized Article Schema

All source/search tools should output a shared article schema:

```json
{
  "title": "",
  "link": "",
  "snippet": "",
  "date": "",
  "source_type": "",
  "source_id": "",
  "source_name": "",
  "topics": [],
  "author": "",
  "metrics": {},
  "raw": {}
}
```

Required principles:
- `source_type` identifies the class: `rss`, `html`, `github`, `reddit`, `web`, etc.
- `source_id` is the stable logical source id
- `raw` preserves provider-specific fields for later debugging
- tools may add extra fields, but the common fields above must always exist when known

### Tool Result Envelope

Each tool should return a structured envelope:

```json
{
  "tool": "tavily_search",
  "status": "ok",
  "count": 12,
  "items": [],
  "errors": [],
  "meta": {}
}
```

This makes composition predictable.

## Layer 3: Scout Runtime

Scout needs a shared runtime that sits above atomic tools and below the business skill.

Runtime responsibilities:
- provider key loading and rotation
- `.env` loading and secret lookup
- source definition loading
- explicit search target loading
- merge and deduplication
- ranking and priority boosts
- cache and archive behavior
- mode selection
- common logging and run metadata

The runtime should not know about final digest wording or section layout.

### Runtime Modes

Scout runtime should support exactly these modes:

#### `sources-only`
Use only fixed configured sources.

Inputs:
- source configs
- mode profile
- time window

Typical tools:
- `rss_fetch`
- `html_fetch`
- `github_fetch`
- `reddit_fetch`

Use case:
- low-cost daily or weekly report from trusted sources only

#### `search-only`
Use only explicit search targets.

Inputs:
- search target file or inline search request
- selected provider set
- freshness window

Typical tools:
- `tavily_search`
- `serpapi_search`
- `jina_fetch`

Use case:
- investigative follow-up
- vendor update sweep
- rapid check for a focused topic

#### `hybrid`
Use both fixed sources and explicit search.

Inputs:
- source configs
- search target file
- mode profile
- freshness/time window

Typical flow:
1. fetch fixed sources
2. fetch explicit search targets
3. normalize into one article pool
4. deduplicate and rank
5. render final output

Use case:
- weekly report with gap-filling web search
- special event monitoring

## Top-Level Skill

`tech-news-digest` should become the only top-level business skill in Scout.

That skill should be responsible for:
- choosing `daily` or `weekly`
- choosing `sources-only`, `search-only`, or `hybrid`
- choosing config overlays
- invoking runtime tools in the right order
- applying digest-specific ranking preferences
- rendering the final forum/email/report output

It should not contain provider-specific search implementations.

## What Moves Out of tech-news-digest

The following should be extracted downward into Scout runtime or atomic tools:
- Tavily provider logic
- SerpAPI provider logic
- Jina provider logic
- generic HTML fetch/extractor framework
- generic RSS fetch logic
- generic GitHub fetch logic
- generic Reddit fetch logic
- shared article normalization
- shared merge/rank/dedup logic
- shared archive metadata handling

## What Stays in tech-news-digest

The following should remain in the top-level skill:
- daily vs weekly policy
- section layout
- model vendor bucket logic
- Reddit presentation rules for daily reports
- preferred template selection
- source curation choices for this product
- explicit search target presets for digest use cases
- delivery instructions for Discord Forum and email

## Recommended Directory Model For Scout

A clean target layout could look like this:

```text
scout/
├── soul/
│   └── SCOUT_SOUL.md
├── tools/
│   ├── tavily_search/
│   ├── serpapi_search/
│   ├── jina_fetch/
│   ├── rss_fetch/
│   ├── html_fetch/
│   ├── github_fetch/
│   ├── reddit_fetch/
│   ├── merge_rank/
│   ├── template_render/
│   ├── delivery_discord_forum/
│   └── delivery_email/
├── runtime/
│   ├── schemas/
│   ├── profiles/
│   ├── env/
│   ├── archive/
│   └── orchestration/
└── skills/
    └── tech-news-digest/
```

## Recommended tech-news-digest Internal Layout After Migration

```text
tech-news-digest/
├── SKILL.md
├── references/
│   ├── digest-prompt.md
│   └── templates/
├── workspace/
│   ├── config-daily/
│   ├── config-weekly/
│   ├── search-targets-daily.json
│   └── search-targets-weekly.json
└── scripts/
    ├── run-pipeline.py
    ├── summarize-merged.py
    ├── generate-pdf.py
    └── send-email.py
```

After migration, the fetch scripts can either:
- stay as thin wrappers around Scout runtime tools, or
- be removed from this skill entirely if Scout runtime owns them fully

## Tool Definitions

### `tavily_search`
Purpose:
- run explicit search queries against Tavily
- support single-key and multi-key rotation
- return normalized web results

Input:
```json
{
  "queries": ["OpenAI latest news"],
  "freshness": "pd",
  "topic": "llm"
}
```

Output:
- normalized article envelope

### `serpapi_search`
Purpose:
- run explicit search queries against SerpAPI
- normalize organic/news results into the shared schema

### `jina_fetch`
Purpose:
- fetch clean page text or page extraction for already-known URLs
- support hydration or article enrichment

This is not a search replacement. It is a URL-to-readable-content tool.

### `html_fetch`
Purpose:
- fetch listing/newsroom/blog HTML pages
- apply generic or site-specific extractors
- return normalized articles

Support model:
- default generic extractor
- per-source extractor registry

### `merge_rank`
Purpose:
- combine normalized items from all tools
- deduplicate by canonical URL and title similarity
- apply ranking boosts and penalties
- output a merged scored pool

This tool is where `intel-pipeline` style ranking logic should live.

## Ranking Strategy

Ranking should move out of individual product skills and into Scout runtime.

Borrow and standardize these ideas:
- official-source boost
- high-signal keyword boost
- provider/source priority
- query priority
- freshness decay
- low-signal penalty
- URL canonicalization
- title-based dedup

That lets `tech-news-digest` inherit better search quality without owning all the ranking code.

## Search Target Strategy

Search should be explicit, not implicit.

Recommended rule:
- topic overlays define buckets only
- search target files define actual search intent for the current run

Examples:
- `search-targets-daily.json`
- `search-targets-weekly.json`
- ad-hoc generated search target files for special monitoring jobs

This reduces API waste and keeps user intent visible.

## Secrets Model

Scout should standardize provider secrets around environment variables and local `.env` files.

Recommended rules:
- no secrets in markdown docs
- support repo-local `.env`
- existing shell env vars override `.env`
- multi-key providers use comma-separated `*_API_KEYS`
- single-key fallbacks remain supported for compatibility

Recommended naming:
- `SCOUT_TAVILY_API_KEYS`
- `SCOUT_SERPAPI_API_KEYS`
- `SCOUT_LINKUP_API_KEY`
- `SCOUT_JINA_API_KEY`

## Migration Plan

### Phase 1: Freeze current digest behavior
Done or nearly done in this repo:
- daily/weekly overlays
- Anthropic HTML source
- explicit search-target mode
- Tavily multi-key support
- `openclaw-emoji` output structure

### Phase 2: Extract atomic search and fetch tools
Build or refactor Scout tools for:
- Tavily
- SerpAPI
- Jina
- RSS
- HTML
- GitHub
- Reddit

### Phase 3: Extract runtime merge/rank layer
Move dedup, ranking, and archive logic into reusable runtime modules.

### Phase 4: Thin the top-level skill
Refactor `tech-news-digest` so it mostly orchestrates:
- profiles
- modes
- templates
- delivery

### Phase 5: Remove obsolete Scout agents/tools
Delete or archive older overlapping Scout agents once the atomic tool layer is stable.

## Immediate Recommended Next Steps

1. Define the Scout tool contracts first.
2. Extract `tavily_search` as the first standalone Scout tool.
3. Extract `html_fetch` second, because Anthropic already proves the pattern.
4. Move ranking/dedup into a reusable runtime module.
5. Repoint `tech-news-digest` to those runtime tools instead of embedding all fetch logic.

## Decision Summary

The target design is:
- one global Scout soul
- many atomic tools
- one shared runtime
- one top-level digest business skill

That gives you:
- fixed sources only
- search only
- hybrid mode
- lower complexity at the top
- reusable capability at the bottom
- cleaner future expansion for Scout
