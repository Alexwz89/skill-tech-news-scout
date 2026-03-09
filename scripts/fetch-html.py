#!/usr/bin/env python3
"""
Fetch HTML listing pages from unified sources configuration.

Reads sources.json, filters HTML sources, fetches pages in parallel with retry,
and extracts dated article links using a registered extractor strategy.

Usage:
    python3 fetch-html.py [--defaults DIR] [--config DIR] [--hours 48] [--output FILE] [--verbose]
"""

import argparse
import html
import json
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

TIMEOUT = 20
MAX_WORKERS = 5
RETRY_COUNT = 2
RETRY_DELAY = 2.0
MAX_BYTES = 2_000_000
MAX_ARTICLES_PER_SOURCE = 20
USER_AGENT = "tech-news-digest-html-fetcher/1.0"
DATE_PATTERNS = [
    "%b %d, %Y",
    "%B %d, %Y",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ",
]
DATE_REGEXES = [
    re.compile(r"\b([A-Z][a-z]{2,8} \d{1,2}, \d{4})\b"),
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
    re.compile(r"\b(\d{4}/\d{2}/\d{2})\b"),
]
DEFAULT_KNOWN_CATEGORIES = {
    "Announcement", "Announcements", "Company", "Event", "Events",
    "Partnership", "Partnerships", "Policy", "Product", "Publication",
    "Research", "Safety"
}
TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
ANCHOR_RE = re.compile(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
JSON_LD_RE = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL)
Extractor = Callable[[str, Dict[str, Any], datetime], List[Dict[str, Any]]]


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def get_text(self) -> str:
        return " ".join(self.parts)


def setup_logging(verbose: bool) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    return logging.getLogger(__name__)


def normalize_whitespace(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text).strip()


def strip_tags(raw_html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(raw_html)
        parser.close()
        return normalize_whitespace(html.unescape(parser.get_text()))
    except Exception:
        return normalize_whitespace(html.unescape(TAG_RE.sub(" ", raw_html)))


def is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
        return parsed.scheme in ("http", "https")
    except Exception:
        return False


def resolve_link(link: str, base_url: str) -> str:
    if not link:
        return ""
    if link.startswith(("http://", "https://")):
        return link
    resolved = urljoin(base_url, link)
    return resolved if is_safe_url(resolved) else ""


def parse_date(text: str) -> Optional[datetime]:
    if not text:
        return None
    value = text.strip()
    for fmt in DATE_PATTERNS:
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def find_date_in_text(text: str) -> Optional[datetime]:
    for pattern in DATE_REGEXES:
        match = pattern.search(text)
        if match:
            parsed = parse_date(match.group(1))
            if parsed:
                return parsed
    return None


def allowed_domains_for_source(source: Dict[str, Any], page_url: str) -> List[str]:
    domains = list(source.get("allowed_domains") or source.get("expected_domains") or [])
    if not domains:
        host = urlparse(page_url).hostname or ""
        if host:
            domains = [host, host.removeprefix("www.")]
    return [d.lower() for d in domains if d]


def link_allowed(link: str, source: Dict[str, Any], page_url: str) -> bool:
    if not is_safe_url(link):
        return False
    parsed = urlparse(link)
    host = (parsed.hostname or "").lower()
    domains = allowed_domains_for_source(source, page_url)
    if domains and not any(host == d or host.endswith("." + d) for d in domains):
        return False
    include_paths = source.get("include_paths") or []
    if include_paths and not any(piece in parsed.path for piece in include_paths):
        return False
    exclude_paths = source.get("exclude_paths") or []
    if exclude_paths and any(piece in parsed.path for piece in exclude_paths):
        return False
    return True


def fetch_url(url: str, timeout: int = TIMEOUT) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError(f"HTML page too large: {len(raw)} bytes")
        charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def try_json_loads(payload: str) -> Optional[Any]:
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def iter_json_ld_nodes(node: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from iter_json_ld_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_json_ld_nodes(item)


def extract_json_ld_articles(content: str, page_url: str, cutoff: datetime) -> List[Dict[str, Any]]:
    articles: List[Dict[str, Any]] = []
    seen = set()
    for match in JSON_LD_RE.finditer(content):
        data = try_json_loads(html.unescape(match.group(1).strip()))
        if data is None:
            continue
        for node in iter_json_ld_nodes(data):
            raw_type = node.get("@type")
            types = set(str(t) for t in raw_type) if isinstance(raw_type, list) else {str(raw_type)}
            if not types.intersection({"NewsArticle", "Article", "BlogPosting"}):
                continue
            title = normalize_whitespace(str(node.get("headline") or node.get("name") or ""))
            link = resolve_link(str(node.get("url") or ""), page_url)
            published = parse_date(str(node.get("datePublished") or node.get("dateCreated") or ""))
            if not title or not link or not published or published < cutoff:
                continue
            key = link.lower()
            if key in seen:
                continue
            seen.add(key)
            article = {"title": title[:200], "link": link, "date": published.isoformat()}
            section = normalize_whitespace(str(node.get("articleSection") or ""))
            if section:
                article["category"] = section[:60]
            articles.append(article)
    return articles[:MAX_ARTICLES_PER_SOURCE]


def parse_listing_text(text: str, known_categories: Optional[set[str]] = None) -> Tuple[Optional[datetime], str, Optional[str]]:
    known_categories = known_categories or DEFAULT_KNOWN_CATEGORIES
    date_value = find_date_in_text(text)
    if not date_value:
        return None, text, None
    date_match = None
    for pattern in DATE_REGEXES:
        date_match = pattern.search(text)
        if date_match:
            break
    if not date_match:
        return date_value, text, None
    remainder = normalize_whitespace(text[date_match.end():])
    if not remainder:
        return date_value, text, None
    parts = remainder.split()
    category = None
    for size in (2, 1):
        if len(parts) >= size:
            candidate = normalize_whitespace(" ".join(parts[:size]))
            if candidate in known_categories:
                category = candidate
                remainder = normalize_whitespace(" ".join(parts[size:]))
                break
    title = remainder or text
    return date_value, title, category


def extract_anchor_articles(content: str, source: Dict[str, Any], cutoff: datetime, known_categories: Optional[set[str]] = None) -> List[Dict[str, Any]]:
    page_url = source["url"]
    articles: List[Dict[str, Any]] = []
    seen = set()
    title_min_length = int(source.get("title_min_length", 15))
    for match in ANCHOR_RE.finditer(content):
        href, inner_html = match.groups()
        link = resolve_link(href, page_url)
        if not link or not link_allowed(link, source, page_url):
            continue
        text = strip_tags(inner_html)
        if len(text) < title_min_length:
            continue
        date_value, title, category = parse_listing_text(text, known_categories=known_categories)
        if not date_value:
            snippet = content[max(0, match.start() - 600): min(len(content), match.end() + 600)]
            date_value = find_date_in_text(strip_tags(snippet))
        if not date_value or date_value < cutoff:
            continue
        title = normalize_whitespace(title)
        if len(title) < title_min_length:
            continue
        key = link.lower()
        if key in seen:
            continue
        seen.add(key)
        article = {"title": title[:200], "link": link, "date": date_value.isoformat()}
        if category:
            article["category"] = category[:60]
        articles.append(article)
    articles.sort(key=lambda item: item["date"], reverse=True)
    return articles[: int(source.get("max_articles", MAX_ARTICLES_PER_SOURCE))]


def extract_generic_listing(content: str, source: Dict[str, Any], cutoff: datetime) -> List[Dict[str, Any]]:
    json_ld_articles = extract_json_ld_articles(content, source["url"], cutoff)
    if json_ld_articles:
        return json_ld_articles
    return extract_anchor_articles(content, source, cutoff)


def extract_anthropic_news(content: str, source: Dict[str, Any], cutoff: datetime) -> List[Dict[str, Any]]:
    source = dict(source)
    source.setdefault("allowed_domains", ["www.anthropic.com", "anthropic.com"])
    source.setdefault("include_paths", ["/news/"])
    source.setdefault("title_min_length", 20)
    known_categories = {"Announcements", "Policy", "Product", "Research", "Safety"}
    articles = extract_json_ld_articles(content, source["url"], cutoff)
    if articles:
        return articles
    return extract_anchor_articles(content, source, cutoff, known_categories=known_categories)


SITE_EXTRACTORS: Dict[str, Tuple[str, Extractor]] = {
    "anthropic-news-html": ("anthropic-news-v1", extract_anthropic_news),
}
DEFAULT_EXTRACTOR_NAME = "generic-listing-v1"
DEFAULT_EXTRACTOR = extract_generic_listing


def extract_articles(content: str, source: Dict[str, Any], cutoff: datetime) -> Tuple[str, List[Dict[str, Any]]]:
    source_id = source.get("id", "")
    extractor_name, extractor = SITE_EXTRACTORS.get(source_id, (DEFAULT_EXTRACTOR_NAME, DEFAULT_EXTRACTOR))
    articles = extractor(content, source, cutoff)
    return extractor_name, articles


def load_html_sources(defaults_dir: Path, config_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    try:
        from config_loader import load_merged_sources
    except ImportError:
        sys.path.append(str(Path(__file__).parent))
        from config_loader import load_merged_sources
    sources = load_merged_sources(defaults_dir, config_dir)
    html_sources = []
    for source in sources:
        if source.get("type") != "html":
            continue
        if not source.get("enabled", True):
            continue
        if not source.get("url"):
            logging.warning("HTML source %s missing url, skipping", source.get("id"))
            continue
        html_sources.append(source)
    return html_sources


def fetch_source(source: Dict[str, Any], cutoff: datetime) -> Dict[str, Any]:
    source_id = source["id"]
    url = source["url"]
    error_msg = ""
    extractor_name = DEFAULT_EXTRACTOR_NAME
    for attempt in range(RETRY_COUNT + 1):
        try:
            content = fetch_url(url)
            extractor_name, articles = extract_articles(content, source, cutoff)
            return {
                "source_id": source_id,
                "source_type": "html",
                "extractor": extractor_name,
                "name": source.get("name", source_id),
                "url": url,
                "priority": source.get("priority", False),
                "topics": source.get("topics", []),
                "status": "ok",
                "attempts": attempt + 1,
                "count": len(articles),
                "articles": articles,
            }
        except HTTPError as exc:
            error_msg = f"HTTP {exc.code}"
            logging.warning("Error fetching %s: %s", source_id, error_msg)
        except (URLError, OSError, ValueError) as exc:
            error_msg = str(exc)
            logging.warning("Network/parsing error for %s: %s", source_id, error_msg)
        except Exception as exc:
            error_msg = str(exc)
            logging.error("Unexpected error for %s: %s", source_id, error_msg)
        if attempt < RETRY_COUNT:
            time.sleep(RETRY_DELAY * (attempt + 1))
    return {
        "source_id": source_id,
        "source_type": "html",
        "extractor": extractor_name,
        "name": source.get("name", source_id),
        "url": url,
        "priority": source.get("priority", False),
        "topics": source.get("topics", []),
        "status": "error",
        "attempts": RETRY_COUNT + 1,
        "count": 0,
        "articles": [],
        "error": error_msg or "unknown error",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch HTML listing pages from configured sources.")
    parser.add_argument("--defaults", type=Path, default=Path("config/defaults"), help="Default configuration directory")
    parser.add_argument("--config", type=Path, help="User configuration directory for overlays")
    parser.add_argument("--hours", type=int, default=48, help="Time window in hours")
    parser.add_argument("--output", type=Path, default=Path("/tmp/td-html.json"), help="Output JSON file")
    parser.add_argument("--force", action="store_true", help="Accepted for pipeline compatibility; HTML fetch always re-downloads")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    logger = setup_logging(args.verbose)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    sources = load_html_sources(args.defaults, args.config)
    if not sources:
        logger.warning("No HTML sources found or all disabled")
        output = {
            "generated": datetime.now(timezone.utc).isoformat(),
            "source_type": "html",
            "defaults_dir": str(args.defaults),
            "config_dir": str(args.config) if args.config else None,
            "hours": args.hours,
            "sources_total": 0,
            "sources_ok": 0,
            "total_articles": 0,
            "sources": [],
        }
        args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
        return 0
    logger.info("🌐 Fetching %d HTML sources (cutoff: %s UTC)", len(sources), cutoff.strftime("%Y-%m-%d %H:%M"))
    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(sources)))) as pool:
        futures = {pool.submit(fetch_source, source, cutoff): source for source in sources}
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: item.get("source_id", ""))
    ok_count = sum(1 for result in results if result["status"] == "ok")
    total_articles = sum(result.get("count", 0) for result in results)
    output = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "source_type": "html",
        "defaults_dir": str(args.defaults),
        "config_dir": str(args.config) if args.config else None,
        "hours": args.hours,
        "sources_total": len(results),
        "sources_ok": ok_count,
        "total_articles": total_articles,
        "sources": results,
    }
    args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("✅ Fetched %d/%d HTML sources, %d articles", ok_count, len(results), total_articles)
    print(f"Output: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

