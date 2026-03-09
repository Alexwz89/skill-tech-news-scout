#!/usr/bin/env python3
"""
Atomic GitHub releases fetch tool for Scout-style explicit source runs.

This script accepts one or more explicit GitHub release source definitions and
reuses the existing fetch-github.py implementation for authentication, caching,
and release retrieval. It does not depend on digest source overlays.
"""

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List


MODULE_PATH = Path(__file__).resolve().parent / "fetch-github.py"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env_file(env_path: Path) -> None:
    """Load simple KEY=VALUE pairs from a local .env file without overriding existing env vars."""
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value

def load_fetch_github_module():
    spec = importlib.util.spec_from_file_location("fetch_github_module", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize_source(entry: Dict[str, Any]) -> Dict[str, Any]:
    source_id = entry.get("id") or entry.get("source_id")
    repo = entry.get("repo")
    if not source_id or not repo:
        raise ValueError("Each GitHub source must include id and repo")

    return {
        "id": source_id,
        "type": "github",
        "name": entry.get("name", repo),
        "repo": repo,
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
            raw_sources = payload.get("sources") or payload.get("github_sources") or []
        for item in raw_sources:
            if item.get("type") not in (None, "github"):
                continue
            sources.append(normalize_source(item))

    if args.repo:
        explicit = {
            "id": args.source_id or args.repo.replace("/", "-") + "-github",
            "name": args.name or args.repo,
            "repo": args.repo,
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
        items.append({
            "title": article.get("title", ""),
            "link": article.get("link", ""),
            "snippet": article.get("summary", "") or "",
            "date": article.get("date", ""),
            "source_type": "github",
            "source_id": result.get("source_id", ""),
            "source_name": result.get("name", result.get("source_id", "")),
            "topics": article.get("topics", result.get("topics", [])),
            "author": "",
            "metrics": {},
            "raw": {
                "priority": result.get("priority", False),
                "repo": result.get("repo"),
            },
        })
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomic GitHub releases fetch tool for explicit source definitions")
    parser.add_argument("--source-file", type=Path, help="JSON file containing {\"sources\": [...]} or a raw source list")
    parser.add_argument("--source-id", help="Source id for a single explicit source")
    parser.add_argument("--name", help="Optional display name for a single explicit source")
    parser.add_argument("--repo", help="GitHub repo in owner/repo form for a single explicit source")
    parser.add_argument("--topic", action="append", help="Topic label to attach to the explicit source. Can be repeated.")
    parser.add_argument("--priority", action="store_true", help="Mark the explicit source as priority")
    parser.add_argument("--hours", type=int, default=168, help="Time window in hours")
    parser.add_argument("--no-cache", action="store_true", help="Bypass GitHub ETag/Last-Modified cache")
    parser.add_argument("--output", "-o", type=Path, help="Output JSON path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    load_env_file(ENV_PATH)
    fetch_github = load_fetch_github_module()
    logger = fetch_github.setup_logging(args.verbose)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)

    sources = load_sources(args)
    if not sources:
        logger.error("No explicit GitHub sources provided. Use --source-file or --repo.")
        return 1

    github_token = fetch_github.resolve_github_token()
    fetch_github._get_github_cache(no_cache=args.no_cache)

    output_path = args.output
    if not output_path:
        fd, temp_path = tempfile.mkstemp(prefix="scout-github-fetch-", suffix=".json")
        Path(temp_path).touch()
        output_path = Path(temp_path)

    source_results: List[Dict[str, Any]] = []
    items: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for source in sources:
        result = fetch_github.fetch_releases_with_retry(source, cutoff, github_token, args.no_cache)
        source_results.append(result)
        items.extend(normalize_items(result))
        if result.get("status") != "ok":
            errors.append({
                "source_id": result.get("source_id"),
                "error": result.get("error", "unknown error"),
            })

    fetch_github._flush_github_cache()

    envelope = {
        "tool": "github_fetch",
        "status": "ok" if source_results and all(item.get("status") == "ok" for item in source_results) else ("partial" if items else "error"),
        "count": len(items),
        "items": items,
        "errors": errors,
        "meta": {
            "generated": datetime.now(timezone.utc).isoformat(),
            "hours": args.hours,
            "github_token_used": github_token is not None,
            "sources_total": len(source_results),
            "sources_ok": sum(1 for item in source_results if item.get("status") == "ok"),
            "sources": [
                {
                    "source_id": item.get("source_id"),
                    "status": item.get("status"),
                    "count": item.get("count"),
                    "repo": item.get("repo"),
                    "priority": item.get("priority", False),
                }
                for item in source_results
            ],
        },
    }

    output_path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote %s GitHub release item(s) to %s", envelope["count"], output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())




