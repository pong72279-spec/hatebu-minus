from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_entries  # noqa: E402


RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns="http://purl.org/rss/1.0/"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:hatena="http://www.hatena.ne.jp/info/xmlns#">
  <item rdf:about="https://Example.com/news?id=2&amp;utm_source=test&amp;a=1#section">
    <title>安全な &amp; 記事</title>
    <link>https://Example.com/news?id=2&amp;utm_source=test&amp;a=1#section</link>
    <description>&lt;p&gt;概要 &lt;strong&gt;本文&lt;/strong&gt;&lt;/p&gt;&lt;script&gt;ignored()&lt;/script&gt;</description>
    <dc:date>2026-08-04T01:02:03Z</dc:date>
    <hatena:bookmarkcount>18</hatena:bookmarkcount>
    <hatena:bookmarkCommentListPageUrl>https://b.hatena.ne.jp/entry/s/example.com/news?id=2</hatena:bookmarkCommentListPageUrl>
  </item>
  <item rdf:about="https://sub.togetter.com/blocked">
    <title>除外対象</title>
    <link>https://sub.togetter.com/blocked</link>
    <hatena:bookmarkcount>99</hatena:bookmarkcount>
  </item>
  <item rdf:about="https://example.org/missing-fields">
    <link>https://example.org/missing-fields</link>
    <hatena:bookmarkcount>4 users</hatena:bookmarkcount>
  </item>
  <item rdf:about="javascript:alert(1)">
    <title>危険なURL</title>
    <link>javascript:alert(1)</link>
  </item>
</rdf:RDF>
""".encode("utf-8")


class UrlTests(unittest.TestCase):
    def test_normalize_url_removes_tracking_fragment_and_default_port(self) -> None:
        normalized = fetch_entries.normalize_url(
            "HTTPS://Example.COM:443//news/?utm_medium=social&b=2&a=1#top"
        )
        self.assertEqual(normalized, "https://example.com/news/?a=1&b=2")

    def test_normalize_url_rejects_non_http_scheme_and_credentials(self) -> None:
        self.assertIsNone(fetch_entries.normalize_url("javascript:alert(1)"))
        self.assertIsNone(fetch_entries.normalize_url("https://user:pass@example.com/"))

    def test_blocked_domain_matches_subdomains_only(self) -> None:
        blocked = ["togetter.com"]
        self.assertTrue(fetch_entries.is_blocked_domain("togetter.com", blocked))
        self.assertTrue(fetch_entries.is_blocked_domain("sub.togetter.com", blocked))
        self.assertFalse(fetch_entries.is_blocked_domain("not-togetter.com", blocked))


class RssTests(unittest.TestCase):
    def test_parse_rss_tolerates_missing_fields_and_rejects_bad_url(self) -> None:
        fetched_at = datetime(2026, 8, 4, 2, 0, tzinfo=timezone.utc)
        articles = fetch_entries.parse_rss(
            RSS_FIXTURE, "all", "popular", fetched_at
        )
        self.assertEqual(len(articles), 3)
        first = articles[0]
        self.assertEqual(first["url"], "https://example.com/news?a=1&id=2")
        self.assertEqual(first["bookmarkCount"], 18)
        self.assertIn("概要 本文", first["description"])
        self.assertNotIn("ignored()", first["description"])

        missing = articles[2]
        self.assertEqual(missing["title"], "タイトル不明")
        self.assertEqual(missing["description"], "")
        self.assertEqual(missing["publishedAt"], "2026-08-04T02:00:00Z")
        self.assertEqual(
            missing["commentUrl"],
            "https://b.hatena.ne.jp/entry/s/example.org/missing-fields",
        )

    def test_merge_filters_blocked_domains_and_deduplicates(self) -> None:
        now = datetime(2026, 8, 4, 2, 0, tzinfo=timezone.utc)
        config = {
            "blockedDomains": ["togetter.com"],
            "blockedKeywords": [],
            "minimumBookmarkCount": 3,
            "retentionDays": 7,
        }
        parsed = fetch_entries.parse_rss(RSS_FIXTURE, "all", "popular", now)
        duplicate = dict(parsed[0])
        duplicate["url"] = "https://example.com/news?id=2&a=1&utm_campaign=again"
        duplicate["normalizedUrl"] = duplicate["url"]
        duplicate["appearances"] = [
            {
                "category": "it",
                "mode": "recent",
                "rank": 1,
                "firstSeenAt": "2026-08-04T02:00:00Z",
                "lastSeenAt": "2026-08-04T02:00:00Z",
            }
        ]
        merged = fetch_entries.merge_articles([], [*parsed, duplicate], config, now)
        self.assertEqual(len(merged), 2)
        example = next(article for article in merged if article["domain"] == "example.com")
        self.assertEqual(len(example["appearances"]), 2)
        self.assertEqual(example["categories"], ["all", "it"])


class FailureSafetyTests(unittest.TestCase):
    def test_total_fetch_failure_keeps_existing_json_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            config_path = directory / "config.json"
            data_path = directory / "entries.json"
            config_path.write_text(
                json.dumps(
                    {
                        "blockedDomains": ["togetter.com"],
                        "blockedKeywords": [],
                        "minimumBookmarkCount": 3,
                        "retentionDays": 7,
                    }
                ),
                encoding="utf-8",
            )
            original = b'{"marker":"keep-me","articles":[]}\n'
            data_path.write_bytes(original)

            def failing_fetcher(url: str, timeout: int, user_agent: str) -> bytes:
                raise urllib.error.URLError("offline")

            updated = fetch_entries.run_update(
                config_path=config_path,
                data_path=data_path,
                fetcher=failing_fetcher,
                sleep_fn=lambda _: None,
                now=datetime(2026, 8, 4, 2, 0, tzinfo=timezone.utc),
            )

            self.assertFalse(updated)
            self.assertEqual(data_path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
