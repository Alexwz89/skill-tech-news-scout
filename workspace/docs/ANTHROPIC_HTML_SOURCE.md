# Anthropic HTML Source Details

这份文档只讲 Anthropic 为什么按 HTML source 处理，以及对应的提取规则。

## Source Definition

建议 source 形态：

```json
{
  "id": "anthropic-news-html",
  "type": "html",
  "name": "Anthropic Newsroom",
  "url": "https://www.anthropic.com/news",
  "enabled": true,
  "priority": true,
  "topics": ["llm", "ai-agent", "frontier-tech"]
}
```

## Why HTML Instead of RSS

原因：
- 没有确认到稳定官方 RSS/Atom
- 官方 newsroom HTML 页面可以直接访问
- 页面结构足够稳定，可以提取列表页新闻条目
- 这样能避免依赖第三方镜像 feed

## Extraction Rule

从 `https://www.anthropic.com/news` 提取：
- `title`
- `link`
- `date`
- 可选 `category`

并归一化成和 RSS 类似的 article 结构。

## Selection Rule

- 只保留当前时间窗口内的条目
- 评分上把它视为官方源
- 允许它进入 `llm`、`ai-agent`、`frontier-tech`

## Implementation Note

当前对应实现：
- `scripts/fetch-html.py`
- `scripts/html-fetch.py`

如果未来增加新的 HTML 页面源，推荐继续沿用：
- 通用 extractor
- source-specific extractor registry
