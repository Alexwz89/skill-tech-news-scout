#!/usr/bin/env python3
"""
Atomic Tavily search tool for Scout-style explicit search runs.

This script does not depend on digest topic configuration. It accepts one or more
explicit queries, rotates across Tavily API keys, and returns a normalized result
envelope that can be consumed by merge/rank layers or higher-level skills.

Usage examples:
    python3 scripts/tavily-search.py --query "OpenAI latest news" --freshness pd
    python3 scripts/tavily-search.py --queries-file workspace/search-queries.json --topic llm
"""

import argparse
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

TAVILY_API_BASE = "https://api.tavily.com/search"
TIMEOUT = 30
MAX_RESULTS_PER_QUERY = 10
USER_AGENT = "scout-tavily-search/1.0"


def setup_logging(verbose: bool) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger(__name__)


def load_env_file(env_path: Path) -> None:
    """Load simple KEY=VALUE pairs from a repo-local .env without overriding shell env vars."""
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


def get_tavily_api_keys() -> List[str]:
    for env_name in ("TAVILY_API_KEYS", "SCOUT_TAVILY_API_KEYS"):
        raw = os.getenv(env_name, "").strip()
        if raw:
            return [key.strip() for key in raw.split(",") if key.strip()]

    for env_name in ("TAVILY_API_KEY", "SCOUT_TAVILY_API_KEY"):
        raw = os.getenv(env_name, "").strip()
        if raw:
            return [raw]

    return []


def freshness_to_days(freshness: str) -> Optional[int]:
    if freshness in ("pd",):
        return 1
    if freshness in ("pw",):
        return 7
    if freshness in ("pm",):
        return 30
    if freshness in ("py",):
        return 365
    if freshness.endswith("h"):
        try:
            return max(1, int(freshness[:-1]) // 24)
        except ValueError:
            return None
    if freshness.endswith("d"):
        try:
            return max(1, int(freshness[:-1]))
        except ValueError:
            return None
    return None


def load_queries(args: argparse.Namespace) -> List[str]:
    queries: List[str] = []
    if args.query:
        queries.extend(args.query)
    if args.queries_file:
        payload = json.loads(args.queries_file.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            queries.extend(str(item).strip() for item in payload if str(item).strip())
        elif isinstance(payload, dict):
            raw_queries = payload.get("queries") or payload.get("search_queries") or []
            queries.extend(str(item).strip() for item in raw_queries if str(item).strip())
        else:
            raise ValueError("Unsupported queries-file format")

    deduped: List[str] = []
    seen = set()
    for query in queries:
        if query and query not in seen:
            seen.add(query)
            deduped.append(query)
    return deduped


def normalize_article(result: Dict[str, Any], query: str, topic: Optional[str], key_label: str) -> Dict[str, Any]:
    link = result.get("url", "")
    domain = ""
    try:
        from urllib.parse import urlparse
        domain = urlparse(link).netloc.lower()
    except Exception:
        domain = ""

    return {
        "title": result.get("title", "").strip(),
        "link": link,
        "snippet": (result.get("content", "") or "").strip()[:500],
        "date": result.get("published_date", "") or "",
        "source_type": "web",
        "source_id": "tavily-search",
        "source_name": "Tavily Search",
        "topics": [topic] if topic else [],
        "author": "",
        "metrics": {},
        "raw": {
            "query": query,
            "provider": "tavily",
            "key_label": key_label,
            "domain": domain,
            "score": result.get("score"),
        },
    }


def search_tavily(query: str, api_key: str, key_label: str, freshness: str, max_results: int) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "topic": "news",
        "max_results": max_results,
        "include_answer": False,
    }
    days = freshness_to_days(freshness)
    if days is not None:
        payload["days"] = days

    try:
        data = json.dumps(payload).encode()
        req = Request(
            TAVILY_API_BASE,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        with urlopen(req, timeout=TIMEOUT) as resp:
            parsed = json.loads(resp.read().decode())
        return {
            "query": query,
            "status": "ok",
            "results": parsed.get("results", []),
            "count": len(parsed.get("results", [])),
            "key": key_label,
            "error": None,
        }
    except HTTPError as exc:
        return {
            "query": query,
            "status": "error",
            "results": [],
            "count": 0,
            "key": key_label,
            "error": f"HTTP {exc.code}",
        }
    except Exception as exc:
        return {
            "query": query,
            "status": "error",
            "results": [],
            "count": 0,
            "key": key_label,
            "error": str(exc),
        }


def run_queries(queries: List[str], keys: List[str], freshness: str, topic: Optional[str], max_results: int, logger: logging.Logger) -> Dict[str, Any]:
    if not keys:
        raise RuntimeError("No Tavily API keys configured")

    items: List[Dict[str, Any]] = []
    query_results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for index, query in enumerate(queries):
        final_result: Optional[Dict[str, Any]] = None
        for offset in range(len(keys)):
            key_index = (index + offset) % len(keys)
            key_label = f"key_{key_index + 1}"
            result = search_tavily(query, keys[key_index], key_label, freshness, max_results)
            final_result = result
            if result["status"] == "ok":
                logger.debug("Tavily query ok: %s via %s", query, key_label)
                break
            logger.warning("Tavily query failed: %s via %s (%s)", query, key_label, result["error"])

        if final_result is None:
            continue

        query_results.append({
            "query": query,
            "status": final_result["status"],
            "count": final_result["count"],
            "key": final_result["key"],
            "error": final_result["error"],
        })

        if final_result["status"] == "ok":
            for raw_item in final_result["results"]:
                items.append(normalize_article(raw_item, query, topic, final_result["key"]))
        else:
            errors.append({
                "query": query,
                "key": final_result["key"],
                "error": final_result["error"],
            })

    return {
        "tool": "tavily_search",
        "status": "ok" if query_results and all(result["status"] == "ok" for result in query_results) else ("partial" if items else "error"),
        "count": len(items),
        "items": items,
        "errors": errors,
        "meta": {
            "generated": datetime.now(timezone.utc).isoformat(),
            "provider": "tavily",
            "topic": topic,
            "freshness": freshness,
            "queries": query_results,
            "api_keys_configured": len(keys),
            "max_results_per_query": max_results,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomic Tavily search tool for Scout-style explicit queries")
    parser.add_argument("--query", action="append", help="Query to execute. Can be repeated.")
    parser.add_argument("--queries-file", type=Path, help="Optional JSON file with {\"queries\": [...]} or a raw query list")
    parser.add_argument("--topic", help="Optional logical topic label to attach to returned items")
    parser.add_argument("--freshness", default="pd", help="Freshness window: pd, pw, pm, py, 24h, 48h, 7d")
    parser.add_argument("--max-results", type=int, default=MAX_RESULTS_PER_QUERY, help="Max Tavily results per query")
    parser.add_argument("--output", "-o", type=Path, help="Output JSON path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    load_env_file(Path(__file__).resolve().parent.parent / ".env")
    logger = setup_logging(args.verbose)

    queries = load_queries(args)
    if not queries:
        logger.error("No queries provided. Use --query or --queries-file.")
        return 1

    keys = get_tavily_api_keys()
    logger.info("Loaded %s Tavily key(s)", len(keys))

    output_path = args.output
    if not output_path:
        fd, temp_path = tempfile.mkstemp(prefix="scout-tavily-search-", suffix=".json")
        os.close(fd)
        output_path = Path(temp_path)

    try:
        result = run_queries(queries, keys, args.freshness, args.topic, args.max_results, logger)
    except Exception as exc:
        logger.error("Tavily atomic search failed: %s", exc)
        return 1

    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote %s item(s) to %s", result["count"], output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
