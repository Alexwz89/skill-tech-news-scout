# Digest Prompt Template

Replace `<...>` placeholders before use. Daily defaults shown; weekly overrides in parentheses.

## Placeholders

| Placeholder | Default | Weekly Override |
|-------------|---------|----------------|
| `<MODE>` | `daily` | `weekly` |
| `<TIME_WINDOW>` | `past 1-2 days` | `past 7 days` |
| `<FRESHNESS>` | `pd` | `pw` |
| `<RSS_HOURS>` | `48` | `168` |
| `<ITEMS_PER_SECTION>` | `3-5` | `10-15` |
| `<BLOG_PICKS_COUNT>` | `3` | `3-5` |
| `<SUBJECT>` | `Daily Tech Digest - YYYY-MM-DD` | `Weekly Tech Digest - YYYY-MM-DD` |
| `<WORKSPACE>` | Your workspace path | |
| `<SKILL_DIR>` | Installed skill directory | |
| `<DISCORD_CHANNEL_ID>` | Target channel ID | |
| `<EMAIL>` | *(optional)* Recipient email | |
| `<EMAIL_FROM>` | *(optional)* e.g. `MyBot <bot@example.com>` | |
| `<LANGUAGE>` | `Chinese` | |
| `<TEMPLATE>` | `openclaw-emoji` / `openclaw-weekly` / `discord` / `email` / `markdown` | |
| `<DATE>` | Today's date YYYY-MM-DD (caller provides) | |
| `<VERSION>` | Read from SKILL.md frontmatter | |
| `<SEARCH_TARGETS>` | *(optional)* explicit web-search target JSON file | |

---

Generate the `<MODE>` tech digest for **`<DATE>`**. Use `<DATE>` as the report date. Do not infer it.

## Configuration

Read config files in this order:
1. **Sources**: prefer `<WORKSPACE>/<MODE>-report-config/fixed-sources.json`; final fallback `<SKILL_DIR>/config/defaults/sources.json`
2. **Topics**: prefer `<WORKSPACE>/<MODE>-report-config/display-topics.json`; final fallback `<SKILL_DIR>/config/defaults/topics.json`
3. **Search targets**: only if `<SEARCH_TARGETS>` is provided and non-empty

Role of each config file:
- `fixed-sources.json`: defines where content comes from
- `display-topics.json`: defines how the final report is grouped and displayed
- `search-targets.json`: defines what to search only when search is explicitly requested

This workspace intentionally leaves daily and weekly `search.queries` empty. That means web search must be explicitly requested via `<SEARCH_TARGETS>`.

## Context: Previous Report

Read the most recent file from `<WORKSPACE>/archive/tech-news-digest/` to avoid repeats and follow up on developing stories. Skip if none exists.

## Data Collection

### Primary runtime
Use the Scout runtime for the main source set.

```bash
python3 <SKILL_DIR>/scripts/scout-runtime.py \
  --mode sources-only \
  --hours <RSS_HOURS> --freshness <FRESHNESS> \
  --rss-sources <WORKSPACE>/<MODE>-report-config/fixed-sources.json \
  --html-sources <WORKSPACE>/<MODE>-report-config/fixed-sources.json \
  --github-sources <WORKSPACE>/<MODE>-report-config/fixed-sources.json \
  $([ "<MODE>" = "daily" ] && echo "--reddit-sources <WORKSPACE>/daily-report-config/fixed-sources.json") \
  --archive-dir <WORKSPACE>/archive/tech-news-digest/ \
  --output /tmp/td-merged.json --verbose
```

Notes:
- Daily and weekly both skip Twitter and GitHub Trending by design.
- Weekly normally omits Reddit by not passing `--reddit-sources`.
- Anthropic is expected to arrive through the `anthropic-news-html` source.
- Daily Reddit behavior follows the current active daily config. At the moment that includes `r/n8n`, `r/ClaudeCode`, and `r/openclaw`.

### Optional explicit web search
Only do this step if `<SEARCH_TARGETS>` is provided.

Standalone search:
```bash
python3 <SKILL_DIR>/scripts/fetch-web.py \
  --defaults <SKILL_DIR>/config/defaults \
  --config <WORKSPACE>/<MODE>-report-config \
  --search-targets <SEARCH_TARGETS> \
  --freshness <FRESHNESS> \
  --output /tmp/td-web.json --verbose --force
```

Or combined runtime path:
```bash
python3 <SKILL_DIR>/scripts/scout-runtime.py \
  --mode hybrid \
  --hours <RSS_HOURS> --freshness <FRESHNESS> \
  --rss-sources <WORKSPACE>/<MODE>-report-config/fixed-sources.json \
  --html-sources <WORKSPACE>/<MODE>-report-config/fixed-sources.json \
  --github-sources <WORKSPACE>/<MODE>-report-config/fixed-sources.json \
  $([ "<MODE>" = "daily" ] && echo "--reddit-sources <WORKSPACE>/daily-report-config/fixed-sources.json") \
  --search-queries-file <SEARCH_TARGETS> \
  --archive-dir <WORKSPACE>/archive/tech-news-digest/ \
  --output /tmp/td-merged.json --verbose
```

Rules for optional web search:
- Do not run it when `<SEARCH_TARGETS>` is empty.
- Use web results as supplemental candidates.
- Prefer net-new facts, confirmations, or missing vendor updates.

## Report Generation

Get a structured overview of the merged runtime output:
```bash
python3 <SKILL_DIR>/scripts/summarize-merged.py --input /tmp/td-merged.json --top <ITEMS_PER_SECTION>
```

Apply the template from `<SKILL_DIR>/references/templates/<TEMPLATE>.md`. For this workspace, prefer `openclaw-emoji` for daily and `openclaw-weekly` for weekly.

For daily output, prefer an event-first style: classify what happened, keep summaries factual, and expose visible statistics such as merged source counts and Reddit interaction counts.

Each article line must include its score using the `🔥{score}` prefix.

## Main Section Layout
- `🌍 全球 AI 行业热点`
- `🏢 模型厂商动态`
  - `🟢 OpenAI`
  - `🟠 Anthropic`
  - `🔵 Gemini`
  - `🧪 Open Source`
- `🤖 AI 智能体 / 工作流`
- `🧩 开源社区动态`

## Selection Rules
- Only news from `<TIME_WINDOW>`
- Main topic sections should include only items with `quality_score >= 5`
- Preserve descending score order within a section
- Deduplicate same event; keep the most authoritative source

## Source-Specific Rules

### HTML official newsroom sources
- HTML sources are first-class inputs.
- `anthropic-news-html` should be treated like an official vendor source.
- Prefer Anthropic HTML items over third-party commentary when both cover the same announcement.

### Model vendor buckets
Always check these vendor buckets in order:
1. `OpenAI`
2. `Anthropic`
3. `Gemini`
4. `Open Source`

### Daily Reddit handling
For `<MODE> == daily`:
- Render Reddit as a separate `Community Picks` section.
- Use only the communities enabled in the active daily config.
- At most 5 unique posts per subreddit.
- Rank by `score` descending, then `num_comments` descending.
- Deduplicate by `reddit_url` or `link` before selecting Top 5.
- Prefer RSS, HTML official sources, and GitHub for the main sections.

### Weekly Reddit handling
For `<MODE> == weekly`, Reddit should not appear unless the active config explicitly enables it.

## Fixed Sections After Main Buckets

### `📦 GitHub Releases`
Include notable watched-repo releases.

### `💬 Community Picks`
Daily only, using the Reddit rules above.

### `📝 Blog Picks`
Include `<BLOG_PICKS_COUNT>` strong blog posts when qualifying blog items exist.

## Footer Stats
Use a footer like:
```text
---
📊 Data Sources: RSS {{rss}} | HTML {{html}} | Twitter {{twitter}} | Reddit {{reddit}} | Web {{web}} | GitHub {{github}} | Dedup: {{merged}} articles
🤖 Generated by tech-news-digest v<VERSION> | <https://github.com/draco-agent/tech-news-digest> | Powered by OpenClaw
```

If web search was not explicitly run, `{{web}}` should be `0`.

## Archive
Save to `<WORKSPACE>/archive/tech-news-digest/<MODE>-YYYY-MM-DD.md`.

## Delivery
1. **Discord / Forum**: Send to `<DISCORD_CHANNEL_ID>`.
2. **Email** if `<EMAIL>` is set:
   - Generate `/tmp/td-email.html`
   - Generate PDF with `generate-pdf.py`
   - Send with `send-email.py`

Write the report in `<LANGUAGE>`.



