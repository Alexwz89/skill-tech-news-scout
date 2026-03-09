#!/usr/bin/env python3
"""
Atomic merge/rank tool for Scout-style normalized envelopes.

This tool merges items from multiple input envelopes, applies base scoring,
merges multi-source stories, deduplicates near-duplicates, optionally applies
previous-report penalties, and emits both a flat ranked pool and topic groups.

It accepts both:
- Scout-style atomic envelopes: {tool, status, count, items, errors, meta}
- Legacy digest fetch outputs for compatibility during migration
"""

import argparse
import json
import logging
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

SCORE_MULTI_SOURCE = 5
SCORE_PRIORITY_SOURCE = 3
SCORE_RECENT = 2
SCORE_ENGAGEMENT_VIRAL = 5
SCORE_ENGAGEMENT_HIGH = 3
SCORE_ENGAGEMENT_MED = 2
SCORE_ENGAGEMENT_LOW = 1
PENALTY_OLD_REPORT = -5
TITLE_SIMILARITY_THRESHOLD = 0.85
DOMAIN_LIMIT_EXEMPT = {"x.com", "twitter.com", "github.com", "reddit.com"}


def setup_logging(verbose: bool) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    return logging.getLogger(__name__)


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def normalize_title(title: str) -> str:
    title = re.sub(r'^(RT\s+@\w+:\s*)', '', title or '', flags=re.IGNORECASE)
    title = re.sub(r'\s*[|\-–]\s*[^|]*$', '', title)
    title = re.sub(r'\s+', ' ', title).strip()
    title = re.sub(r'[^\w\s]', '', title.lower())
    return title


def calculate_title_similarity(title1: str, title2: str) -> float:
    norm1 = normalize_title(title1)
    norm2 = normalize_title(title2)
    if not norm1 or not norm2:
        return 0.0
    return SequenceMatcher(None, norm1, norm2).ratio()


def normalize_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace('www.', '')
        path = parsed.path.rstrip('/')
        return f"{domain}{path}"
    except Exception:
        return url or ''


def get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace('www.', '')
    except Exception:
        return ''


def calculate_base_score(article: Dict[str, Any]) -> float:
    score = float(article.get('quality_score', 0) or 0)

    if article.get('priority', False) or article.get('raw', {}).get('priority', False):
        score += SCORE_PRIORITY_SOURCE

    try:
        date_value = article.get('date', '')
        if date_value:
            article_date = datetime.fromisoformat(date_value.replace('Z', '+00:00'))
            hours_old = (datetime.now(timezone.utc) - article_date).total_seconds() / 3600
            if hours_old < 24:
                score += SCORE_RECENT
    except Exception:
        pass

    metrics = article.get('metrics', {}) or {}
    likes = metrics.get('like_count', 0) or 0
    retweets = metrics.get('retweet_count', 0) or 0
    if likes >= 1000 or retweets >= 500:
        score += SCORE_ENGAGEMENT_VIRAL
    elif likes >= 500 or retweets >= 200:
        score += SCORE_ENGAGEMENT_HIGH
    elif likes >= 100 or retweets >= 50:
        score += SCORE_ENGAGEMENT_MED
    elif likes >= 50 or retweets >= 20:
        score += SCORE_ENGAGEMENT_LOW

    if article.get('source_type') == 'reddit':
        reddit_score = article.get('score', 0) or metrics.get('score', 0) or 0
        if reddit_score > 500:
            score += 5
        elif reddit_score > 200:
            score += 3
        elif reddit_score > 100:
            score += 1

    if article.get('source_type') == 'github_trending':
        score += min(10, int(article.get('daily_stars_est', 0) or 0) // 10)

    return score


def extract_tokens(title: str) -> Set[str]:
    norm = normalize_title(title)
    stopwords = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'in', 'on', 'at', 'to', 'for', 'of',
        'and', 'or', 'with', 'by', 'from', 'as', 'it', 'its', 'that', 'this', 'be', 'has',
        'had', 'have', 'not', 'but', 'what', 'how', 'new', 'will', 'can', 'do', 'does', 'did'
    }
    return {word for word in norm.split() if len(word) >= 3 and word not in stopwords}


def build_token_buckets(articles: List[Dict[str, Any]]) -> Dict[int, Set[int]]:
    from collections import defaultdict
    token_to_indices: Dict[str, List[int]] = defaultdict(list)
    article_tokens: List[Set[str]] = []
    for i, article in enumerate(articles):
        tokens = extract_tokens(article.get('title', ''))
        article_tokens.append(tokens)
        for token in tokens:
            token_to_indices[token].append(i)

    candidates: Dict[int, Set[int]] = defaultdict(set)
    for i, tokens in enumerate(article_tokens):
        overlap_count: Dict[int, int] = defaultdict(int)
        for token in tokens:
            for j in token_to_indices[token]:
                if j != i:
                    overlap_count[j] += 1
        for j, count in overlap_count.items():
            if count >= 2:
                candidates[i].add(j)
    return candidates


def deduplicate_articles(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not articles:
        return []
    articles.sort(key=lambda item: item.get('quality_score', 0), reverse=True)

    url_seen: Dict[str, int] = {}
    url_duplicates: Set[int] = set()
    for i, article in enumerate(articles):
        url = article.get('link', '')
        if not url:
            continue
        norm_url = normalize_url(url)
        if norm_url in url_seen:
            url_duplicates.add(i)
        else:
            url_seen[norm_url] = i
    if url_duplicates:
        articles = [item for i, item in enumerate(articles) if i not in url_duplicates]

    deduplicated: List[Dict[str, Any]] = []
    candidates = build_token_buckets(articles)
    duplicate_indices: Set[int] = set()

    for i, article in enumerate(articles):
        if i in duplicate_indices:
            continue
        title = article.get('title', '')
        for j in candidates.get(i, set()):
            if j <= i or j in duplicate_indices:
                continue
            other_title = articles[j].get('title', '')
            norm_i = normalize_title(title)
            norm_j = normalize_title(other_title)
            if abs(len(norm_i) - len(norm_j)) > 0.3 * max(len(norm_i), len(norm_j), 1):
                continue
            similarity = calculate_title_similarity(title, other_title)
            if similarity >= TITLE_SIMILARITY_THRESHOLD:
                duplicate_indices.add(j)
        deduplicated.append(article)
    return deduplicated


def apply_domain_limits(articles: List[Dict[str, Any]], max_per_domain: int = 3) -> List[Dict[str, Any]]:
    result = []
    domain_counts: Dict[str, int] = {}
    for article in articles:
        domain = get_domain(article.get('link', ''))
        if domain and domain not in DOMAIN_LIMIT_EXEMPT:
            count = domain_counts.get(domain, 0)
            if count >= max_per_domain:
                continue
            domain_counts[domain] = count + 1
        result.append(article)
    return result


def merge_multi_source_articles(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not articles:
        return []
    title_groups: Dict[str, List[Dict[str, Any]]] = {}
    for article in articles:
        key = normalize_title(article.get('title', ''))
        title_groups.setdefault(key, []).append(article)

    merged: List[Dict[str, Any]] = []
    for group in title_groups.values():
        if len(group) == 1:
            merged.append(group[0])
            continue
        primary = max(group, key=lambda item: item.get('quality_score', 0))
        source_types = sorted({item.get('source_type', '') for item in group if item.get('source_type')})
        source_names = [item.get('source_name', '') for item in group if item.get('source_name')]
        topic_set = []
        seen_topics = set()
        for item in group:
            for topic in item.get('topics', []) or []:
                if topic not in seen_topics:
                    seen_topics.add(topic)
                    topic_set.append(topic)
        primary = dict(primary)
        primary['quality_score'] = primary.get('quality_score', 0) + len(source_types) * SCORE_MULTI_SOURCE
        primary['multi_source'] = True
        primary['source_count'] = len(group)
        primary['all_sources'] = source_names[:5]
        primary['all_source_types'] = source_types
        if topic_set:
            primary['topics'] = topic_set
        merged.append(primary)
    return merged


def load_previous_titles(archive_dir: Optional[Path], days: int = 7) -> Set[str]:
    if not archive_dir or not archive_dir.exists():
        return set()
    seen_titles: Set[str] = set()
    cutoff = datetime.now() - timedelta(days=days)
    for file_path in archive_dir.glob('*.md'):
        match = re.search(r'(\d{4}-\d{2}-\d{2})', file_path.name)
        if match:
            try:
                file_date = datetime.strptime(match.group(1), '%Y-%m-%d')
                if file_date < cutoff:
                    continue
            except ValueError:
                continue
        try:
            content = file_path.read_text(encoding='utf-8')
        except Exception:
            continue
        for found in re.finditer(r'-\s*\[([^\]]+)\]', content):
            title = normalize_title(found.group(1))
            if title:
                seen_titles.add(title)
    return seen_titles


def apply_previous_digest_penalty(articles: List[Dict[str, Any]], previous_titles: Set[str]) -> List[Dict[str, Any]]:
    if not previous_titles:
        return articles
    updated = []
    for article in articles:
        title = normalize_title(article.get('title', ''))
        item = dict(article)
        if title in previous_titles:
            item['quality_score'] = item.get('quality_score', 0) + PENALTY_OLD_REPORT
            item['in_previous_digest'] = True
        updated.append(item)
    return updated


def group_by_topics(articles: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for article in articles:
        topics = article.get('topics', []) or ['uncategorized']
        for topic in topics:
            article_copy = dict(article)
            article_copy['primary_topic'] = topic
            groups.setdefault(topic, []).append(article_copy)
    for topic in groups:
        groups[topic].sort(key=lambda item: item.get('quality_score', 0), reverse=True)
    return groups


def items_from_legacy_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    for source in payload.get('sources', []):
        source_type = source.get('source_type') or payload.get('source_type') or 'unknown'
        for article in source.get('articles', []):
            item = dict(article)
            item.setdefault('source_type', source_type)
            item.setdefault('source_id', source.get('source_id', ''))
            item.setdefault('source_name', source.get('name', source.get('source_id', '')))
            item.setdefault('topics', source.get('topics', []))
            item['priority'] = source.get('priority', False)
            item['quality_score'] = calculate_base_score(item)
            items.append(item)

    for topic_result in payload.get('topics', []):
        if 'articles' in topic_result:
            for article in topic_result.get('articles', []):
                item = dict(article)
                topic_id = topic_result.get('topic') or topic_result.get('topic_id')
                if not item.get('topics') and topic_id:
                    item['topics'] = [topic_id]
                item.setdefault('source_type', 'web')
                item.setdefault('source_id', f"web-{topic_id or 'unknown'}")
                item.setdefault('source_name', 'Web Search')
                item['quality_score'] = calculate_base_score(item)
                items.append(item)

    for source in payload.get('subreddits', []):
        for article in source.get('articles', []):
            item = dict(article)
            item.setdefault('source_type', 'reddit')
            item.setdefault('source_id', source.get('source_id', ''))
            item.setdefault('source_name', f"r/{source.get('subreddit', '')}")
            item.setdefault('topics', source.get('topics', []))
            item['priority'] = source.get('priority', False)
            item['quality_score'] = calculate_base_score(item)
            items.append(item)

    for repo in payload.get('repos', []):
        item = {
            'title': f"{repo['repo']}: {repo['description']}" if repo.get('description') else repo['repo'],
            'link': repo.get('url', f"https://github.com/{repo['repo']}"),
            'snippet': repo.get('description', ''),
            'date': repo.get('pushed_at', ''),
            'source_type': 'github_trending',
            'source_id': 'github-trending',
            'source_name': 'GitHub Trending',
            'topics': repo.get('topics', []),
            'stars': repo.get('stars', 0),
            'daily_stars_est': repo.get('daily_stars_est', 0),
            'forks': repo.get('forks', 0),
            'language': repo.get('language', ''),
            'quality_score': calculate_base_score({'source_type': 'github_trending', 'daily_stars_est': repo.get('daily_stars_est', 0)}),
        }
        items.append(item)

    return items


def items_from_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if 'items' in payload and isinstance(payload.get('items'), list):
        items = []
        for article in payload.get('items', []):
            item = dict(article)
            item['quality_score'] = calculate_base_score(item)
            items.append(item)
        return items
    return items_from_legacy_payload(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description='Atomic merge/rank tool for Scout-style normalized envelopes')
    parser.add_argument('--input', action='append', type=Path, help='Input JSON envelope. Can be repeated.')
    parser.add_argument('--inputs-file', type=Path, help='Optional JSON file containing a list of input file paths')
    parser.add_argument('--archive-dir', type=Path, help='Optional archive directory for previous-digest penalties')
    parser.add_argument('--output', '-o', type=Path, help='Output JSON path')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    logger = setup_logging(args.verbose)
    input_paths: List[Path] = []
    if args.input:
        input_paths.extend(args.input)
    if args.inputs_file:
        payload = json.loads(args.inputs_file.read_text(encoding='utf-8'))
        if isinstance(payload, list):
            input_paths.extend(Path(item) for item in payload)
        else:
            input_paths.extend(Path(item) for item in payload.get('inputs', []))

    if not input_paths:
        logger.error('No inputs provided. Use --input or --inputs-file.')
        return 1

    all_items: List[Dict[str, Any]] = []
    input_meta: List[Dict[str, Any]] = []
    for path in input_paths:
        if not path.exists():
            logger.warning('Skipping missing input: %s', path)
            continue
        payload = load_json(path)
        items = items_from_payload(payload)
        all_items.extend(items)
        input_meta.append({
            'path': str(path),
            'tool': payload.get('tool') or payload.get('source_type') or 'legacy',
            'count': len(items),
        })

    total_input = len(all_items)
    previous_titles = load_previous_titles(args.archive_dir)
    all_items = apply_previous_digest_penalty(all_items, previous_titles)
    all_items = merge_multi_source_articles(all_items)
    all_items = deduplicate_articles(all_items)
    all_items.sort(key=lambda item: item.get('quality_score', 0), reverse=True)

    flat_ranked_items = list(all_items)
    topic_groups = group_by_topics(flat_ranked_items)
    for topic, articles in list(topic_groups.items()):
        topic_groups[topic] = apply_domain_limits(articles)

    output_path = args.output
    if not output_path:
        fd, temp_path = tempfile.mkstemp(prefix='scout-merge-rank-', suffix='.json')
        Path(temp_path).touch()
        output_path = Path(temp_path)

    output = {
        'tool': 'merge_rank',
        'status': 'ok',
        'count': len(flat_ranked_items),
        'items': flat_ranked_items,
        'errors': [],
        'meta': {
            'generated': datetime.now(timezone.utc).isoformat(),
            'inputs': input_meta,
            'total_input_items': total_input,
            'archive_penalty_applied': bool(previous_titles),
            'topic_distribution': {topic: len(items) for topic, items in topic_groups.items()},
        },
        'topics': {
            topic: {
                'count': len(items),
                'articles': items,
            }
            for topic, items in topic_groups.items()
        },
    }

    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    logger.info('Merged %s input item(s) into %s unique ranked item(s) -> %s', total_input, len(flat_ranked_items), output_path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
