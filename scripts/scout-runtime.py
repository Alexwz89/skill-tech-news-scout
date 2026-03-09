#!/usr/bin/env python3
"""
Lightweight Scout runtime orchestrator for atomic tool seeds.

Supports:
- sources-only
- search-only
- hybrid

This orchestrator is intentionally thin: it shells out to the atomic tools,
collects their envelopes, and runs merge-rank.py to produce one ranked pool.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
WORKSPACE = ROOT / "workspace"


def run_command(command: List[str], verbose: bool) -> None:
    if verbose:
        print("[run]", " ".join(command))
    result = subprocess.run(command, cwd=str(ROOT))
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(command)}")


def temp_output(prefix: str) -> Path:
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".json")
    Path(path).touch()
    return Path(path)


def add_source_tool_commands(commands: List[List[str]], args: argparse.Namespace, outputs: List[Path]) -> None:
    if args.rss_sources:
        rss_output = args.rss_output or temp_output("scout-rss-")
        outputs.append(rss_output)
        commands.append([
            sys.executable,
            str(SCRIPTS / "rss-fetch.py"),
            "--source-file", str(args.rss_sources),
            "--hours", str(args.hours),
            "--output", str(rss_output),
            *(["--verbose"] if args.verbose else []),
        ])

    if args.html_sources:
        html_output = args.html_output or temp_output("scout-html-")
        outputs.append(html_output)
        commands.append([
            sys.executable,
            str(SCRIPTS / "html-fetch.py"),
            "--source-file", str(args.html_sources),
            "--hours", str(args.hours),
            "--output", str(html_output),
            *(["--verbose"] if args.verbose else []),
        ])

    if args.github_sources:
        github_output = args.github_output or temp_output("scout-github-")
        outputs.append(github_output)
        commands.append([
            sys.executable,
            str(SCRIPTS / "github-fetch.py"),
            "--source-file", str(args.github_sources),
            "--hours", str(args.hours),
            "--output", str(github_output),
            *(["--verbose"] if args.verbose else []),
        ])

    if args.reddit_sources:
        reddit_output = args.reddit_output or temp_output("scout-reddit-")
        outputs.append(reddit_output)
        commands.append([
            sys.executable,
            str(SCRIPTS / "reddit-fetch.py"),
            "--source-file", str(args.reddit_sources),
            "--hours", str(args.hours),
            "--output", str(reddit_output),
            *(["--verbose"] if args.verbose else []),
        ])


def add_search_tool_commands(commands: List[List[str]], args: argparse.Namespace, outputs: List[Path]) -> None:
    if args.search_queries or args.search_queries_file:
        search_output = args.search_output or temp_output("scout-search-")
        outputs.append(search_output)
        command = [
            sys.executable,
            str(SCRIPTS / "tavily-search.py"),
            "--freshness", args.freshness,
            "--output", str(search_output),
            *(["--topic", args.search_topic] if args.search_topic else []),
            *(["--verbose"] if args.verbose else []),
        ]
        for query in args.search_queries or []:
            command.extend(["--query", query])
        if args.search_queries_file:
            command.extend(["--queries-file", str(args.search_queries_file)])
        commands.append(command)


def build_merge_command(input_files: List[Path], args: argparse.Namespace) -> List[str]:
    command = [
        sys.executable,
        str(SCRIPTS / "merge-rank.py"),
        "--output", str(args.output),
        *(["--verbose"] if args.verbose else []),
    ]
    if args.archive_dir:
        command.extend(["--archive-dir", str(args.archive_dir)])
    for path in input_files:
        command.extend(["--input", str(path)])
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description="Lightweight Scout runtime orchestrator for atomic tool seeds")
    parser.add_argument("--mode", choices=["sources-only", "search-only", "hybrid"], required=True)
    parser.add_argument("--hours", type=int, default=48, help="Time window for source tools")
    parser.add_argument("--freshness", default="pd", help="Freshness for explicit search tools")
    parser.add_argument("--rss-sources", type=Path)
    parser.add_argument("--html-sources", type=Path)
    parser.add_argument("--github-sources", type=Path)
    parser.add_argument("--reddit-sources", type=Path)
    parser.add_argument("--search-query", dest="search_queries", action="append")
    parser.add_argument("--search-queries-file", type=Path)
    parser.add_argument("--search-topic")
    parser.add_argument("--rss-output", type=Path)
    parser.add_argument("--html-output", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--reddit-output", type=Path)
    parser.add_argument("--search-output", type=Path)
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--output", "-o", type=Path, default=WORKSPACE / "scout-runtime-output.json")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    commands: List[List[str]] = []
    input_files: List[Path] = []

    if args.mode in ("sources-only", "hybrid"):
        add_source_tool_commands(commands, args, input_files)
    if args.mode in ("search-only", "hybrid"):
        add_search_tool_commands(commands, args, input_files)

    if not commands:
        raise SystemExit("No tool inputs provided for the selected mode.")

    for command in commands:
        run_command(command, args.verbose)

    merge_command = build_merge_command(input_files, args)
    run_command(merge_command, args.verbose)

    runtime_meta = {
        "tool": "scout_runtime",
        "status": "ok",
        "mode": args.mode,
        "generated": datetime.now(timezone.utc).isoformat(),
        "inputs": [str(path) for path in input_files],
        "output": str(args.output),
    }
    meta_path = args.output.with_suffix(args.output.suffix + ".meta.json")
    meta_path.write_text(json.dumps(runtime_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Output: {args.output}")
    print(f"Meta: {meta_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
