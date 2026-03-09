#!/usr/bin/env python3
"""
Atomic HTML fetch tool for Scout-style explicit source runs.

This script accepts one or more explicit HTML source definitions and reuses the
existing extractor framework from fetch-html.py. It does not depend on digest
source overlays.

Usage examples:
    python3 scripts/html-fetch.py --source-file workspace/html-sources.json --hours 168
    python3 scripts/html-fetch.py --source-id anthropic-news-html --url https://www.anthropic.com/news --topic llm --topic ai-agent
"""

import argparse
import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List


MODULE_PATH = Path(__file__).resolve().parent / "fetch-html.py"


def load_fetch_html_module():
    spec = importlib.util.spec_from_file_location("fetch_html_module", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize_source(entry: Dict[str, Any]) -> Dict[str, Any]:
    source_id = entry.get("id") or entry.get("source_id")
    url = entry.get("url")
    if not source_id or not url:
        raise ValueError("Each HTML source must include id and url")

    source = {
        "id": source_id,
        "type": "html",
        "name": entry.get("name", source_id),
        "url": url,
        "enabled": True,
        "priority": bool(entry.get("priority", False)),
        "topics": entry.get("topics", []),
    }

    for optional_key in (
        "allowed_domains",
        "include_paths",
        "title_min_length",
        "max_articles",
        "note",
    ):
        if optional_key in entry:
            source[optional_key] = entry[optional_key]

    return source


def load_sources(args: argparse.Namespace) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []

    if args.source_file:
        payload = json.loads(args.source_file.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            raw_sources = payload
        else:
            raw_sources = payload.get("sources") or payload.get("html_sources") or []
        for item in raw_sources:
            if item.get("type") not in (None, "html"):
                continue
            sources.append(normalize_source(item))

    if args.url:
        explicit = {
            "id": args.source_id or "html-source",
            "name": args.name or args.source_id or "html-source",
            "url": args.url,
            "priority": args.priority,
            "topics": args.topic or [],
        }
        if args.allowed_domain:
            explicit["allowed_domains"] = args.allowed_domain
        if args.include_path:
            explicit["include_paths"] = args.include_path
        if args.max_articles is not None:
            explicit["max_articles"] = args.max_articles
        if args.title_min_length is not None:
            explicit["title_min_length"] = args.title_min_length
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
            "snippet": article.get("category", "") or "",
            "date": article.get("date", ""),
            "source_type": "html",
            "source_id": result.get("source_id", ""),
            "source_name": result.get("name", result.get("source_id", "")),
            "topics": result.get("topics", []),
            "author": "",
            "metrics": {},
            "raw": {
                "extractor": result.get("extractor"),
                "priority": result.get("priority", False),
                "category": article.get("category"),
                "source_url": result.get("url"),
            },
        })
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomic HTML fetch tool for explicit source definitions")
    parser.add_argument("--source-file", type=Path, help="JSON file containing {\"sources\": [...]} or a raw source list")
    parser.add_argument("--source-id", help="Source id for a single explicit source")
    parser.add_argument("--name", help="Optional display name for a single explicit source")
    parser.add_argument("--url", help="URL for a single explicit source")
    parser.add_argument("--topic", action="append", help="Topic label to attach to the explicit source. Can be repeated.")
    parser.add_argument("--allowed-domain", action="append", help="Allowed domain for explicit source link filtering")
    parser.add_argument("--include-path", action="append", help="Allowed path prefix for explicit source link filtering")
    parser.add_argument("--priority", action="store_true", help="Mark the explicit source as priority")
    parser.add_argument("--max-articles", type=int, help="Max articles to emit for explicit source")
    parser.add_argument("--title-min-length", type=int, help="Title minimum length for explicit source")
    parser.add_argument("--hours", type=int, default=48, help="Time window in hours")
    parser.add_argument("--output", "-o", type=Path, help="Output JSON path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    fetch_html = load_fetch_html_module()
    logger = fetch_html.setup_logging(args.verbose)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)

    sources = load_sources(args)
    if not sources:
        logger.error("No explicit HTML sources provided. Use --source-file or --url.")
        return 1

    output_path = args.output
    if not output_path:
        fd, temp_path = tempfile.mkstemp(prefix="scout-html-fetch-", suffix=".json")
        Path(temp_path).touch()
        output_path = Path(temp_path)

    source_results: List[Dict[str, Any]] = []
    items: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for source in sources:
        result = fetch_html.fetch_source(source, cutoff)
        source_results.append(result)
        items.extend(normalize_items(result))
        if result.get("status") != "ok":
            errors.append({
                "source_id": result.get("source_id"),
                "error": result.get("error", "unknown error"),
            })

    envelope = {
        "tool": "html_fetch",
        "status": "ok" if source_results and all(item.get("status") == "ok" for item in source_results) else ("partial" if items else "error"),
        "count": len(items),
        "items": items,
        "errors": errors,
        "meta": {
            "generated": datetime.now(timezone.utc).isoformat(),
            "hours": args.hours,
            "sources_total": len(source_results),
            "sources_ok": sum(1 for item in source_results if item.get("status") == "ok"),
            "sources": [
                {
                    "source_id": item.get("source_id"),
                    "status": item.get("status"),
                    "count": item.get("count"),
                    "extractor": item.get("extractor"),
                }
                for item in source_results
            ],
        },
    }

    output_path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote %s HTML item(s) to %s", envelope["count"], output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
