#!/usr/bin/env python3
"""
Atomic Reddit fetch tool for Scout-style explicit source runs.

This script accepts one or more explicit Reddit source definitions and reuses the
existing fetch-reddit.py implementation. It does not depend on digest source overlays.
"""

import argparse
import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List


MODULE_PATH = Path(__file__).resolve().parent / "fetch-reddit.py"


def load_fetch_reddit_module():
    spec = importlib.util.spec_from_file_location("fetch_reddit_module", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize_source(entry: Dict[str, Any]) -> Dict[str, Any]:
    source_id = entry.get("id") or entry.get("source_id")
    subreddit = entry.get("subreddit")
    if not source_id or not subreddit:
        raise ValueError("Each Reddit source must include id and subreddit")

    return {
        "id": source_id,
        "type": "reddit",
        "name": entry.get("name", f"r/{subreddit}"),
        "subreddit": subreddit,
        "sort": entry.get("sort", "hot"),
        "limit": int(entry.get("limit", 25)),
        "min_score": int(entry.get("min_score", 0)),
        "enabled": True,
        "priority": bool(entry.get("priority", False)),
        "topics": entry.get("topics", []),
    }


def load_sources(args: argparse.Namespace) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []

    if args.source_file:
        payload = json.loads(args.source_file.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            raw_sources = payload
        else:
            raw_sources = payload.get("sources") or payload.get("reddit_sources") or []
        for item in raw_sources:
            if item.get("type") not in (None, "reddit"):
                continue
            sources.append(normalize_source(item))

    if args.subreddit:
        explicit = {
            "id": args.source_id or f"reddit-{args.subreddit.lower()}",
            "name": args.name or f"r/{args.subreddit}",
            "subreddit": args.subreddit,
            "sort": args.sort,
            "limit": args.limit,
            "min_score": args.min_score,
            "priority": args.priority,
            "topics": args.topic or [],
        }
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
        metrics = article.get("metrics", {}) or {}
        items.append({
            "title": article.get("title", ""),
            "link": article.get("link", ""),
            "snippet": article.get("flair", "") or "",
            "date": article.get("date", ""),
            "source_type": "reddit",
            "source_id": result.get("source_id", ""),
            "source_name": result.get("name", result.get("source_id", "")),
            "topics": article.get("topics", result.get("topics", [])),
            "author": "",
            "metrics": {
                "score": metrics.get("score", article.get("score", 0)),
                "num_comments": metrics.get("num_comments", article.get("num_comments", 0)),
                "upvote_ratio": metrics.get("upvote_ratio", 0),
            },
            "score": article.get("score", metrics.get("score", 0)),
            "num_comments": article.get("num_comments", metrics.get("num_comments", 0)),
            "raw": {
                "subreddit": result.get("subreddit"),
                "sort": result.get("sort"),
                "priority": result.get("priority", False),
                "reddit_url": article.get("reddit_url"),
                "external_url": article.get("external_url"),
                "flair": article.get("flair"),
                "is_self": article.get("is_self", True),
            },
        })
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomic Reddit fetch tool for explicit source definitions")
    parser.add_argument("--source-file", type=Path, help="JSON file containing {\"sources\": [...]} or a raw source list")
    parser.add_argument("--source-id", help="Source id for a single explicit source")
    parser.add_argument("--name", help="Optional display name for a single explicit source")
    parser.add_argument("--subreddit", help="Subreddit name for a single explicit source")
    parser.add_argument("--topic", action="append", help="Topic label to attach to the explicit source. Can be repeated.")
    parser.add_argument("--sort", default="hot", help="Reddit listing sort: hot, new, top, rising")
    parser.add_argument("--limit", type=int, default=25, help="Max posts to request")
    parser.add_argument("--min-score", type=int, default=0, help="Minimum Reddit score filter")
    parser.add_argument("--priority", action="store_true", help="Mark the explicit source as priority")
    parser.add_argument("--hours", type=int, default=48, help="Time window in hours")
    parser.add_argument("--output", "-o", type=Path, help="Output JSON path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    fetch_reddit = load_fetch_reddit_module()
    logger = fetch_reddit.setup_logging(args.verbose)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)

    sources = load_sources(args)
    if not sources:
        logger.error("No explicit Reddit sources provided. Use --source-file or --subreddit.")
        return 1

    output_path = args.output
    if not output_path:
        fd, temp_path = tempfile.mkstemp(prefix="scout-reddit-fetch-", suffix=".json")
        Path(temp_path).touch()
        output_path = Path(temp_path)

    source_results: List[Dict[str, Any]] = []
    items: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for source in sources:
        result = fetch_reddit.fetch_subreddit(source, cutoff)
        source_results.append(result)
        items.extend(normalize_items(result))
        if result.get("status") != "ok":
            errors.append({
                "source_id": result.get("source_id"),
                "error": result.get("error", "unknown error"),
            })

    envelope = {
        "tool": "reddit_fetch",
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
                    "subreddit": item.get("subreddit"),
                    "sort": item.get("sort"),
                }
                for item in source_results
            ],
        },
    }

    output_path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote %s Reddit item(s) to %s", envelope["count"], output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
