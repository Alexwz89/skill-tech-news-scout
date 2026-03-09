# Scout Atomic Tool Details

这份文档只保留原子工具层的契约细节。
如果你只是想运行 skill，不需要先看这份。

## Common Contract

所有原子工具都应该：
- 接收显式输入
- 不掺入报告级业务逻辑
- 返回统一 envelope
- 尽量返回统一的 article schema

### Result Envelope

```json
{
  "tool": "tool_name",
  "status": "ok",
  "count": 0,
  "items": [],
  "errors": [],
  "meta": {}
}
```

### Normalized Article Shape

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

## Available Tools

### `scripts/tavily-search.py`
用途：执行显式 Tavily 查询，支持多 key 轮换，返回 normalized web items。

### `scripts/html-fetch.py`
用途：执行显式 HTML 页面抓取，支持通用 extractor 和站点专用 extractor。

### `scripts/rss-fetch.py`
用途：执行显式 RSS 抓取，返回 normalized RSS items。

### `scripts/github-fetch.py`
用途：执行显式 GitHub release 抓取，支持 `.env` 中的 `GITHUB_TOKEN`。

### `scripts/reddit-fetch.py`
用途：执行显式 Reddit 抓取，保留 `score`、`num_comments`、`upvote_ratio`。

### `scripts/merge-rank.py`
用途：合并多个 envelope，做 dedup、ranking、topic grouping。

## Runtime Relationship

这些原子工具的推荐调用方式不是手工逐个拼，而是通过：
- `scripts/scout-runtime.py`

除非你在做单独调试或平台层集成。
