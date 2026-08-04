#!/usr/bin/env python3
"""Fetch public Hatena Bookmark RSS feeds and atomically update site data."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config.json"
DEFAULT_DATA_PATH = ROOT / "data" / "entries.json"

USER_AGENT = "Hatebu-Minus/1.0 (+https://github.com/)"
REQUEST_TIMEOUT_SECONDS = 15
REQUEST_DELAY_SECONDS = 0.4
MAX_RESPONSE_BYTES = 3_000_000
MAX_ENTRIES = 5_000

RSS_NS = "http://purl.org/rss/1.0/"
DC_NS = "http://purl.org/dc/elements/1.1/"
HATENA_NS = "http://www.hatena.ne.jp/info/xmlns#"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"

CATEGORIES: tuple[tuple[str, str], ...] = (
    ("all", "総合"),
    ("general", "一般"),
    ("social", "世の中"),
    ("economics", "政治と経済"),
    ("life", "暮らし"),
    ("knowledge", "学び"),
    ("it", "テクノロジー"),
    ("fun", "おもしろ"),
    ("entertainment", "エンタメ"),
    ("game", "アニメとゲーム"),
)
CATEGORY_ORDER = {slug: index for index, (slug, _) in enumerate(CATEGORIES)}
MODE_ORDER = {"popular": 0, "recent": 1}

TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "dclid",
    "yclid",
    "mc_cid",
    "mc_eid",
    "ref_src",
}


class TextExtractor(HTMLParser):
    """Extract visible text without trusting feed HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "template"}:
            self.ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "template"}:
            self.ignored_depth = max(0, self.ignored_depth - 1)

    def handle_data(self, data: str) -> None:
        if self.ignored_depth == 0:
            self.parts.append(data)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(candidate)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_datetime(value: Any, fallback: datetime) -> str:
    parsed = parse_datetime(value)
    return isoformat_utc(parsed if parsed is not None else fallback)


def clean_text(value: Any, limit: int) -> str:
    if not isinstance(value, str) or not value:
        return ""
    parser = TextExtractor()
    try:
        parser.feed(value)
        parser.close()
        text = " ".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]*>", " ", value)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def normalize_url(value: Any) -> str | None:
    """Return a canonical HTTP(S) URL suitable for display and deduplication."""

    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw or any(ord(character) < 32 for character in raw):
        return None
    try:
        parsed = urllib.parse.urlsplit(raw)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"} or not parsed.hostname:
            return None
        hostname = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        port = parsed.port
    except (UnicodeError, ValueError):
        return None

    if not hostname or parsed.username is not None or parsed.password is not None:
        return None

    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")

    try:
        query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    except ValueError:
        query_items = []
    filtered_query = [
        (key, item_value)
        for key, item_value in query_items
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_KEYS
    ]
    filtered_query.sort(key=lambda item: (item[0].casefold(), item[1]))
    query = urllib.parse.urlencode(filtered_query, doseq=True)

    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


def domain_from_url(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def is_blocked_domain(domain: str, blocked_domains: Iterable[str]) -> bool:
    normalized_domain = domain.lower().rstrip(".")
    for blocked in blocked_domains:
        normalized_blocked = str(blocked).strip().lower().rstrip(".")
        if normalized_blocked and (
            normalized_domain == normalized_blocked
            or normalized_domain.endswith("." + normalized_blocked)
        ):
            return True
    return False


def contains_blocked_keyword(
    title: str, description: str, blocked_keywords: Iterable[str]
) -> bool:
    haystack = f"{title}\n{description}".casefold()
    return any(
        keyword.strip().casefold() in haystack
        for keyword in map(str, blocked_keywords)
        if keyword.strip()
    )


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child_text(element: ET.Element, name: str, namespace: str | None = None) -> str:
    if namespace is not None:
        match = element.find(f"{{{namespace}}}{name}")
        if match is not None and match.text:
            return match.text.strip()
    for child in element:
        if local_name(child.tag) == name and child.text:
            return child.text.strip()
    return ""


def fallback_comment_url(article_url: str) -> str:
    parsed = urllib.parse.urlsplit(article_url)
    secure_marker = "s/" if parsed.scheme == "https" else ""
    suffix = parsed.netloc + parsed.path
    if parsed.query:
        suffix += "?" + parsed.query
    return "https://b.hatena.ne.jp/entry/" + secure_marker + suffix.lstrip("/")


def parse_bookmark_count(value: Any) -> int:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else 0


def parse_rss(
    xml_bytes: bytes,
    category: str,
    mode: str,
    fetched_at: datetime,
) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_bytes)
    items = [element for element in root.iter() if local_name(element.tag) == "item"]
    parsed_items: list[dict[str, Any]] = []
    fetched_at_text = isoformat_utc(fetched_at)

    for rank, item in enumerate(items, start=1):
        raw_url = child_text(item, "link", RSS_NS)
        if not raw_url:
            raw_url = item.attrib.get(f"{{{RDF_NS}}}about", "")
        normalized_url = normalize_url(raw_url)
        if normalized_url is None:
            continue

        title = clean_text(child_text(item, "title", RSS_NS), 300)
        if not title:
            title = "タイトル不明"
        description = clean_text(child_text(item, "description", RSS_NS), 520)
        published_at = canonical_datetime(
            child_text(item, "date", DC_NS), fetched_at
        )
        bookmark_count = parse_bookmark_count(
            child_text(item, "bookmarkcount", HATENA_NS)
        )
        comment_url = normalize_url(
            child_text(item, "bookmarkCommentListPageUrl", HATENA_NS)
        ) or fallback_comment_url(normalized_url)
        domain = domain_from_url(normalized_url)
        if not domain:
            continue

        parsed_items.append(
            {
                "id": hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()[:16],
                "title": title,
                "url": normalized_url,
                "normalizedUrl": normalized_url,
                "domain": domain,
                "description": description,
                "bookmarkCount": bookmark_count,
                "publishedAt": published_at,
                "firstSeenAt": fetched_at_text,
                "lastSeenAt": fetched_at_text,
                "commentUrl": comment_url,
                "appearances": [
                    {
                        "category": category,
                        "mode": mode,
                        "rank": rank,
                        "firstSeenAt": fetched_at_text,
                        "lastSeenAt": fetched_at_text,
                    }
                ],
            }
        )
    return parsed_items


def build_feed_specs() -> list[dict[str, str]]:
    specs: list[dict[str, str]] = []
    for category, label in CATEGORIES:
        popular_url = (
            "https://b.hatena.ne.jp/hotentry.rss"
            if category == "all"
            else f"https://b.hatena.ne.jp/hotentry/{category}.rss"
        )
        recent_url = (
            "https://b.hatena.ne.jp/entrylist.rss?sort=recent"
            if category == "all"
            else f"https://b.hatena.ne.jp/entrylist/{category}.rss?sort=recent"
        )
        specs.extend(
            (
                {
                    "category": category,
                    "label": label,
                    "mode": "popular",
                    "url": popular_url,
                },
                {
                    "category": category,
                    "label": label,
                    "mode": "recent",
                    "url": recent_url,
                },
            )
        )
    return specs


def fetch_feed(
    url: str,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    user_agent: str = USER_AGENT,
) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8",
        },
    )
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise ValueError("RSS response exceeded the safety size limit")
            return payload
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
            ValueError,
        ) as error:
            last_error = error
            if attempt == 0:
                time.sleep(1)
    assert last_error is not None
    raise last_error


def validated_config(raw_config: Any) -> dict[str, Any]:
    if not isinstance(raw_config, dict):
        raise ValueError("config.json must contain a JSON object")
    blocked_domains = raw_config.get("blockedDomains", [])
    blocked_keywords = raw_config.get("blockedKeywords", [])
    if not isinstance(blocked_domains, list) or not isinstance(blocked_keywords, list):
        raise ValueError("blockedDomains and blockedKeywords must be arrays")
    minimum = int(raw_config.get("minimumBookmarkCount", 3))
    retention = int(raw_config.get("retentionDays", 7))
    if minimum < 0:
        raise ValueError("minimumBookmarkCount must be zero or greater")
    if not 1 <= retention <= 90:
        raise ValueError("retentionDays must be between 1 and 90")
    return {
        "blockedDomains": [str(value) for value in blocked_domains],
        "blockedKeywords": [str(value) for value in blocked_keywords],
        "minimumBookmarkCount": minimum,
        "retentionDays": retention,
    }


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as config_file:
        return validated_config(json.load(config_file))


def load_existing(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"articles": []}
    try:
        with path.open(encoding="utf-8") as data_file:
            data = json.load(data_file)
    except (OSError, json.JSONDecodeError):
        return {"articles": []}
    if not isinstance(data, dict) or not isinstance(data.get("articles"), list):
        return {"articles": []}
    return data


def appearance_key(appearance: dict[str, Any]) -> tuple[str, str]:
    return (str(appearance.get("category", "")), str(appearance.get("mode", "")))


def merge_appearances(
    existing: Any, incoming: Any, fallback_time: str
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for appearance in [*(existing or []), *(incoming or [])]:
        if not isinstance(appearance, dict):
            continue
        category, mode = appearance_key(appearance)
        if category not in CATEGORY_ORDER or mode not in MODE_ORDER:
            continue
        candidate = {
            "category": category,
            "mode": mode,
            "rank": max(1, int(appearance.get("rank", 9999))),
            "firstSeenAt": canonical_datetime(
                appearance.get("firstSeenAt"), parse_datetime(fallback_time) or utc_now()
            ),
            "lastSeenAt": canonical_datetime(
                appearance.get("lastSeenAt"), parse_datetime(fallback_time) or utc_now()
            ),
        }
        key = (category, mode)
        if key not in merged:
            merged[key] = candidate
            continue
        current = merged[key]
        current["rank"] = min(current["rank"], candidate["rank"])
        current["firstSeenAt"] = min(
            current["firstSeenAt"], candidate["firstSeenAt"]
        )
        if candidate["lastSeenAt"] >= current["lastSeenAt"]:
            current["lastSeenAt"] = candidate["lastSeenAt"]
            current["rank"] = candidate["rank"]
    return sorted(
        merged.values(),
        key=lambda value: (
            CATEGORY_ORDER[value["category"]],
            MODE_ORDER[value["mode"]],
        ),
    )


def sanitize_article(
    raw_article: Any, config: dict[str, Any], fallback_time: str
) -> dict[str, Any] | None:
    if not isinstance(raw_article, dict):
        return None
    normalized_url = normalize_url(
        raw_article.get("normalizedUrl") or raw_article.get("url")
    )
    if normalized_url is None:
        return None
    domain = domain_from_url(normalized_url)
    title = clean_text(raw_article.get("title", ""), 300) or "タイトル不明"
    description = clean_text(raw_article.get("description", ""), 520)
    bookmark_count = parse_bookmark_count(raw_article.get("bookmarkCount"))
    if (
        not domain
        or is_blocked_domain(domain, config["blockedDomains"])
        or contains_blocked_keyword(title, description, config["blockedKeywords"])
        or bookmark_count < config["minimumBookmarkCount"]
    ):
        return None

    fallback_dt = parse_datetime(fallback_time) or utc_now()
    first_seen = canonical_datetime(raw_article.get("firstSeenAt"), fallback_dt)
    last_seen = canonical_datetime(raw_article.get("lastSeenAt"), fallback_dt)
    comment_url = normalize_url(raw_article.get("commentUrl")) or fallback_comment_url(
        normalized_url
    )
    appearances = merge_appearances(
        raw_article.get("appearances", []), [], last_seen
    )
    return {
        "id": hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()[:16],
        "title": title,
        "url": normalized_url,
        "normalizedUrl": normalized_url,
        "domain": domain,
        "description": description,
        "bookmarkCount": bookmark_count,
        "publishedAt": canonical_datetime(raw_article.get("publishedAt"), fallback_dt),
        "firstSeenAt": min(first_seen, last_seen),
        "lastSeenAt": max(first_seen, last_seen),
        "commentUrl": comment_url,
        "appearances": appearances,
    }


def combine_article(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    latest = incoming if incoming["lastSeenAt"] >= current["lastSeenAt"] else current
    earliest_published = min(current["publishedAt"], incoming["publishedAt"])
    return {
        "id": current["id"],
        "title": latest["title"] or current["title"],
        "url": current["normalizedUrl"],
        "normalizedUrl": current["normalizedUrl"],
        "domain": current["domain"],
        "description": latest["description"] or current["description"],
        "bookmarkCount": max(current["bookmarkCount"], incoming["bookmarkCount"]),
        "publishedAt": earliest_published,
        "firstSeenAt": min(current["firstSeenAt"], incoming["firstSeenAt"]),
        "lastSeenAt": max(current["lastSeenAt"], incoming["lastSeenAt"]),
        "commentUrl": latest["commentUrl"] or current["commentUrl"],
        "appearances": merge_appearances(
            current.get("appearances", []),
            incoming.get("appearances", []),
            latest["lastSeenAt"],
        ),
    }


def merge_articles(
    existing_articles: Iterable[Any],
    incoming_articles: Iterable[Any],
    config: dict[str, Any],
    generated_at: datetime,
) -> list[dict[str, Any]]:
    generated_at_text = isoformat_utc(generated_at)
    merged: dict[str, dict[str, Any]] = {}
    for raw_article in [*existing_articles, *incoming_articles]:
        article = sanitize_article(raw_article, config, generated_at_text)
        if article is None:
            continue
        key = article["normalizedUrl"]
        merged[key] = combine_article(merged[key], article) if key in merged else article

    cutoff = generated_at - timedelta(days=config["retentionDays"])
    retained: list[dict[str, Any]] = []
    for article in merged.values():
        appearances = [
            appearance
            for appearance in article["appearances"]
            if (parse_datetime(appearance["lastSeenAt"]) or generated_at) >= cutoff
        ]
        last_seen = parse_datetime(article["lastSeenAt"]) or generated_at
        if not appearances or last_seen < cutoff:
            continue
        article["appearances"] = appearances
        article["categories"] = sorted(
            {appearance["category"] for appearance in appearances},
            key=lambda value: CATEGORY_ORDER[value],
        )
        article["modes"] = sorted(
            {appearance["mode"] for appearance in appearances},
            key=lambda value: MODE_ORDER[value],
        )
        retained.append(article)

    retained.sort(
        key=lambda article: (article["lastSeenAt"], article["bookmarkCount"]),
        reverse=True,
    )
    return retained[:MAX_ENTRIES]


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as data_file:
            json.dump(payload, data_file, ensure_ascii=False, indent=2)
            data_file.write("\n")
            data_file.flush()
            os.fsync(data_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def run_update(
    config_path: Path = DEFAULT_CONFIG_PATH,
    data_path: Path = DEFAULT_DATA_PATH,
    fetcher: Callable[[str, int, str], bytes] = fetch_feed,
    sleep_fn: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
) -> bool:
    config = load_config(config_path)
    existing = load_existing(data_path)
    generated_at = now or utc_now()
    feed_specs = build_feed_specs()
    incoming: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    success_count = 0

    for index, spec in enumerate(feed_specs):
        if index:
            sleep_fn(REQUEST_DELAY_SECONDS)
        try:
            xml_bytes = fetcher(spec["url"], REQUEST_TIMEOUT_SECONDS, USER_AGENT)
            incoming.extend(
                parse_rss(xml_bytes, spec["category"], spec["mode"], generated_at)
            )
            success_count += 1
        except (ET.ParseError, OSError, ValueError, urllib.error.URLError) as error:
            failures.append(
                {
                    "category": spec["category"],
                    "mode": spec["mode"],
                    "message": clean_text(str(error), 180) or error.__class__.__name__,
                }
            )
            print(
                f"warning: {spec['category']}/{spec['mode']} failed: {error}",
                file=sys.stderr,
            )

    if success_count == 0:
        print("error: all RSS requests failed; existing JSON was left unchanged", file=sys.stderr)
        return False

    articles = merge_articles(
        existing.get("articles", []), incoming, config, generated_at
    )
    payload = {
        "schemaVersion": 1,
        "generatedAt": isoformat_utc(generated_at),
        "lastSuccessfulUpdateAt": isoformat_utc(generated_at),
        "source": {
            "name": "はてなブックマーク 公開RSS",
            "url": "https://b.hatena.ne.jp/",
        },
        "categories": [
            {"id": category, "label": label} for category, label in CATEGORIES
        ],
        "filters": {
            "minimumBookmarkCount": config["minimumBookmarkCount"],
            "retentionDays": config["retentionDays"],
            "blockedDomainCount": len(config["blockedDomains"]),
        },
        "fetchSummary": {
            "successfulFeeds": success_count,
            "failedFeeds": len(failures),
            "totalFeeds": len(feed_specs),
            "failures": failures,
        },
        "articleCount": len(articles),
        "articles": articles,
    }
    atomic_write_json(data_path, payload)
    print(
        f"updated {data_path}: {len(articles)} articles "
        f"({success_count}/{len(feed_specs)} feeds succeeded)"
    )
    return True


def main() -> int:
    try:
        return 0 if run_update() else 1
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}; existing JSON was left unchanged", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
