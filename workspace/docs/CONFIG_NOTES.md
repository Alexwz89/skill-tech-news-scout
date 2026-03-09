# Workspace Notes

这份文档只描述当前 workspace 的实际运行约定。

## 生效配置
- Daily: `workspace/daily-report-config`
- Weekly: `workspace/weekly-report-config`

## 默认运行策略

### Daily
- 来源：RSS + HTML + GitHub + Reddit
- Reddit 只允许 `r/n8n`、`r/ClaudeCode`、`r/openclaw`
- 默认不跑 web search
- 默认不跑 Twitter
- 默认不跑 GitHub Trending

### Weekly
- 来源：RSS + HTML + GitHub
- 默认不带 Reddit
- 默认不跑 web search
- 默认不跑 Twitter
- 默认不跑 GitHub Trending

## 标准命令

### Daily
```bash
python3 scripts/scout-runtime.py \
  --mode sources-only \
  --hours 48 --freshness pd \
  --rss-sources workspace/daily-report-config/fixed-sources.json \
  --html-sources workspace/daily-report-config/fixed-sources.json \
  --github-sources workspace/daily-report-config/fixed-sources.json \
  --reddit-sources workspace/daily-report-config/fixed-sources.json \
  --archive-dir workspace/archive/tech-news-digest/ \
  --output /tmp/td-merged.json --verbose
```

### Weekly
```bash
python3 scripts/scout-runtime.py \
  --mode sources-only \
  --hours 168 --freshness pw \
  --rss-sources workspace/weekly-report-config/fixed-sources.json \
  --html-sources workspace/weekly-report-config/fixed-sources.json \
  --github-sources workspace/weekly-report-config/fixed-sources.json \
  --archive-dir workspace/archive/tech-news-digest/ \
  --output /tmp/td-merged.json --verbose
```

## 搜索
默认不搜索。只有显式提供 search targets 时才运行。

- Daily targets: `workspace/daily-report-config/search-targets.json`
- Weekly targets: `workspace/weekly-report-config/search-targets.json`

### 显式搜索
```bash
python3 scripts/fetch-web.py \
  --defaults config/defaults \
  --config workspace/daily-report-config \
  --search-targets workspace/daily-report-config/search-targets.json \
  --freshness pd \
  --output workspace/web-daily.json \
  --verbose --force
```

### Hybrid
```bash
python3 scripts/scout-runtime.py \
  --mode hybrid \
  --hours 48 --freshness pd \
  --rss-sources workspace/daily-report-config/fixed-sources.json \
  --html-sources workspace/daily-report-config/fixed-sources.json \
  --github-sources workspace/daily-report-config/fixed-sources.json \
  --reddit-sources workspace/daily-report-config/fixed-sources.json \
  --search-queries-file workspace/daily-report-config/search-targets.json \
  --archive-dir workspace/archive/tech-news-digest/ \
  --output /tmp/td-merged.json --verbose
```

## 表达层规则
- Anthropic 不再作为 RSS source，而是 HTML source
- Daily 中 Reddit 必须单独输出为 `Community Picks`
- 每个 subreddit 最多 Top 5
- 排序按 `score`，其次 `num_comments`



