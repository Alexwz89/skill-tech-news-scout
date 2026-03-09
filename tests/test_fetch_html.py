#!/usr/bin/env python3
"""Tests for fetch-html.py."""

import json
import tempfile
import unittest
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("fetch_html", SCRIPTS_DIR / "fetch-html.py")
fetch_html = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch_html)


class TestFetchHtml(unittest.TestCase):
    def test_extracts_anchor_articles_from_listing(self):
        html = """
        <html><body>
          <a href="/news/mozilla-firefox-security">Mar 6, 2026 Policy Partnering with Mozilla to improve Firefox's security</a>
          <a href="/news/department-of-war">Mar 5, 2026 Announcements Where things stand with the Department of War</a>
          <a href="/careers">Careers</a>
        </body></html>
        """
        source = {
            "id": "anthropic-news-html",
            "url": "https://www.anthropic.com/news",
            "allowed_domains": ["www.anthropic.com", "anthropic.com"],
            "include_paths": ["/news/"],
            "max_articles": 10,
        }
        cutoff = datetime(2026, 3, 1, tzinfo=timezone.utc)
        articles = fetch_html.extract_anthropic_news(html, source, cutoff)
        self.assertEqual(len(articles), 2)
        self.assertEqual(articles[0]["title"], "Partnering with Mozilla to improve Firefox's security")
        self.assertEqual(articles[0]["category"], "Policy")
        self.assertTrue(articles[0]["link"].startswith("https://www.anthropic.com/news/"))

    def test_prefers_json_ld_when_available(self):
        html = """
        <html><head>
          <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "@type": "NewsArticle",
            "headline": "Introducing Claude Sonnet 4.6",
            "url": "https://www.anthropic.com/news/claude-sonnet-4-6",
            "datePublished": "2026-02-17"
          }
          </script>
        </head><body></body></html>
        """
        source = {
            "id": "anthropic-news-html",
            "url": "https://www.anthropic.com/news",
        }
        cutoff = datetime(2026, 2, 1, tzinfo=timezone.utc)
        extractor_name, articles = fetch_html.extract_articles(html, source, cutoff)
        self.assertEqual(extractor_name, "anthropic-news-v1")
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "Introducing Claude Sonnet 4.6")

    def test_load_html_sources_filters_enabled_only(self):
        defaults_dir = Path(__file__).parent.parent / "config" / "defaults"
        with tempfile.TemporaryDirectory() as tmpdir:
            overlay = {
                "sources": [
                    {
                        "id": "anthropic-news-html",
                        "type": "html",
                        "name": "Anthropic Newsroom",
                        "url": "https://www.anthropic.com/news",
                        "enabled": True,
                        "topics": ["llm"]
                    }
                ]
            }
            path = Path(tmpdir) / "tech-news-digest-sources.json"
            path.write_text(json.dumps(overlay), encoding="utf-8")
            sources = fetch_html.load_html_sources(defaults_dir, Path(tmpdir))
            ids = [source["id"] for source in sources]
            self.assertIn("anthropic-news-html", ids)

    def test_generic_extractor_selected_for_unknown_source(self):
        html = """
        <html><body>
          <a href="https://example.com/blog/post-1">Mar 6, 2026 Product Example launch post</a>
        </body></html>
        """
        source = {
            "id": "example-html",
            "url": "https://example.com/blog",
            "allowed_domains": ["example.com"],
        }
        cutoff = datetime(2026, 3, 1, tzinfo=timezone.utc)
        extractor_name, articles = fetch_html.extract_articles(html, source, cutoff)
        self.assertEqual(extractor_name, "generic-listing-v1")
        self.assertEqual(len(articles), 1)


if __name__ == "__main__":
    unittest.main()
