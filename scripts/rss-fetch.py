#!/usr/bin/env python3
"""
Atomic RSS fetch tool for Scout-style explicit source runs.

This script accepts one or more explicit RSS source definitions and reuses the
existing fetch-rss.py parser/fetcher implementation. It does not depend on
digest source overlays.

Usage examples:
    python3 scripts/rss-fetch.py --source-file workspace/rss-sources.json --hours 48
    python3 scripts/rss-fetch.py --source-id openai-rss --url https://openai.com/blog/rss.xml --topic llm --priority
"""

import argparse
import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List


MODULE_PATH = Path(__file__).resolve().parent / "fetch-rss.py"


def load_fetch_rss_module():
    spec = importlib.util.spec_from_file_location("fetch_rss_module", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize_source(entry: Dict[str, Any]) -> Dict[str, Any]:
    source_id = entry.get("id") or entry.get("source_id")
    url = entry.get("url")
    if not source_id or not url:
        raise ValueError("Each RSS source must include id and url")

    source = {
        "id": source_id,
        "type": "rss",
        "name": entry.get("name", source_id),
        "url": url,
        "enabled": True,
        "priority": bool(entry.get("priority", False)),
        "topics": entry.get("topics", []),
    }
    if "expected_domains" in entry:
        source["expected_domains"] = entry["expected_domains"]
    if "note" in entry:
        source["note"] = entry["note"]
    return source


def load_sources(args: argparse.Namespace) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []

    if args.source_file:
        payload = json.loads(args.source_file.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            raw_sources = payload
        else:
            raw_sources = payload.get("sources") or payload.get("rss_sources") or []
        for item in raw_sources:
            if item.get("type") not in (None, "rss"):
                continue
            sources.append(normalize_source(item))

    if args.url:
        explicit = {
            "id": args.source_id or "rss-source",
            "name": args.name or args.source_id or "rss-source",
            "url": args.url,
            "priority": args.priority,
            "topics": args.topic or [],
        }
        if args.expected_domain:
            explicit["expected_domains"] = args.expected_domain
        sources.append(normalize_source(explicit))

    deduped: List[Dict[str, Any]] = []
    seen = set()
    for source in sources:
        if source["id"] in seen:
            continue
        seen.add(source["id"])
        deduped.append(source)
    return deduped


def normalize_items(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for article in result.get("articles", []):
        items.append({
            "title": article.get("title", ""),
            "link": article.get("link", ""),
            "snippet": article.get("snippet", "") if article.get("snippet") else "",
            "date": article.get("date", ""),
            "source_type": "rss",
            "source_id": result.get("source_id", ""),
            "source_name": result.get("name", result.get("source_id", "")),
            "topics": article.get("topics", result.get("topics", [])),
            "author": "",
            "metrics": {},
            "raw": {
                "priority": result.get("priority", False),
                "source_url": result.get("url"),
                "expected_domains": result.get("expected_domains", []),
            },
        })
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomic RSS fetch tool for explicit source definitions")
    parser.add_argument("--source-file", type=Path, help="JSON file containing {\"sources\": [...]} or a raw source list")
    parser.add_argument("--source-id", help="Source id for a single explicit source")
    parser.add_argument("--name", help="Optional display name for a single explicit source")
    parser.add_argument("--url", help="URL for a single explicit source")
    parser.add_argument("--topic", action="append", help="Topic label to attach to the explicit source. Can be repeated.")
    parser.add_argument("--expected-domain", action="append", help="Expected article domain filter for mirrors")
    parser.add_argument("--priority", action="store_true", help="Mark the explicit source as priority")
    parser.add_argument("--hours", type=int, default=48, help="Time window in hours")
    parser.add_argument("--no-cache", action="store_true", help="Bypass RSS ETag/Last-Modified cache")
    parser.add_argument("--output", "-o", type=Path, help="Output JSON path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    fetch_rss = load_fetch_rss_module()
    logger = fetch_rss.setup_logging(args.verbose)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)

    sources = load_sources(args)
    if not sources:
        logger.error("No explicit RSS sources provided. Use --source-file or --url.")
        return 1

    fetch_rss._get_rss_cache(no_cache=args.no_cache)

    output_path = args.output
    if not output_path:
        fd, temp_path = tempfile.mkstemp(prefix="scout-rss-fetch-", suffix=".json")
        Path(temp_path).touch()
        output_path = Path(temp_path)

    source_results: List[Dict[str, Any]] = []
    items: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for source in sources:
        result = fetch_rss.fetch_feed_with_retry(source, cutoff, args.no_cache)
        source_results.append(result)
        items.extend(normalize_items(result))
        if result.get("status") != "ok":
            errors.append({
                "source_id": result.get("source_id"),
                "error": result.get("error", "unknown error"),
            })

    fetch_rss._flush_rss_cache()

    envelope = {
        "tool": "rss_fetch",
        "status": "ok" if source_results and all(item.get("status") == "ok" for item in source_results) else ("partial" if items else "error"),
        "count": len(items),
        "items": items,
        "errors": errors,
        "meta": {
            "generated": datetime.now(timezone.utc).isoformat(),
            "hours": args.hours,
            "feedparser_available": bool(getattr(fetch_rss, "HAS_FEEDPARSER", False)),
            "sources_total": len(source_results),
            "sources_ok": sum(1 for item in source_results if item.get("status") == "ok"),
            "sources": [
                {
                    "source_id": item.get("source_id"),
                    "status": item.get("status"),
                    "count": item.get("count"),
                    "priority": item.get("priority", False),
                }
                for item in source_results
            ],
        },
    }

    output_path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote %s RSS item(s) to %s", envelope["count"], output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
