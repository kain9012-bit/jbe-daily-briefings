"""뉴스 후보 수집: 구글·빙 뉴스 RSS → 기간 필터 → 제외 규칙 → 중복 제거 → 사안 묶기.

출력: data/<briefing>/<날짜>/candidates.json
AI는 쓰지 않는다. 여기서 모은 기사 주소만 브리핑에 실린다.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from common import KST, clean_text, collection_window, data_path, load_config, now_kst, write_json

GOOGLE_RSS = "https://news.google.com/rss/search"
BING_RSS = "https://www.bing.com/news/search"
USER_AGENT = "Mozilla/5.0 (compatible; jbe-daily-briefings/1.0; +https://github.com/kain9012-bit/jbe-daily-briefings)"
TIMEOUT = (5, 15)
INTERVAL = 1.5  # 요청 사이 최소 간격(초)

# 제목 비교 때 버리는 상투어. 이것만 겹쳐 다른 사안이 묶이는 것을 막는다.
STOPWORDS = {
    "교육", "교육부", "교육청", "학교", "학생", "정책", "추진", "지원", "운영", "확대",
    "위한", "통해", "관련", "올해", "내년", "전국", "지역", "기자", "단독", "종합",
}


def parse_pubdate(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(KST)


def unwrap_bing(link: str) -> str:
    parsed = urllib.parse.urlsplit(link)
    if "bing.com" in parsed.netloc:
        for key, value in urllib.parse.parse_qsl(parsed.query):
            if key == "url":
                return value
    return link


def fetch(session: requests.Session, url: str, params: dict[str, str]) -> str | None:
    try:
        response = session.get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException as exc:
        print(f"  요청 실패: {exc}", file=sys.stderr)
        return None
    if response.status_code != 200:
        print(f"  응답 코드 {response.status_code}: {url}", file=sys.stderr)
        return None
    return response.text


def parse_google(xml_text: str, query: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for node in root.iterfind(".//item"):
        raw = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        if not raw or not link:
            continue
        source = (node.findtext("source") or "").strip()
        title = raw
        if source and raw.endswith(f" - {source}"):
            title = raw[: -len(f" - {source}")].strip()
        elif " - " in raw:
            title, _, tail = raw.rpartition(" - ")
            source = source or tail.strip()
        out.append({
            "title": clean_text(title),
            "publisher": source or "미상",
            "published": parse_pubdate(node.findtext("pubDate")),
            "url": link,
            "summary": "",  # 구글 뉴스 RSS 설명은 링크 목록뿐이라 버린다
            "engine": "google",
            "query": query,
        })
    return out


def parse_bing(xml_text: str, query: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for node in root.iterfind(".//item"):
        title = clean_text(node.findtext("title") or "")
        link = unwrap_bing((node.findtext("link") or "").strip())
        if not title or not link:
            continue
        source = ""
        for child in node:
            if child.tag.lower().endswith("source") and (child.text or "").strip():
                source = child.text.strip()
                break
        out.append({
            "title": title,
            "publisher": source or urllib.parse.urlsplit(link).netloc.replace("www.", ""),
            "published": parse_pubdate(node.findtext("pubDate")),
            "url": link,
            "summary": clean_text(node.findtext("description") or "")[:400],
            "engine": "bing",
            "query": query,
        })
    return out


def norm_title(title: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", title or "")


def norm_publisher(name: str) -> str:
    text = re.sub(r"\s*on\s+MSN\s*$", "", name or "", flags=re.I)
    return re.sub(r"\s+", "", text).lower()


def is_direct(url: str) -> bool:
    host = urllib.parse.urlsplit(url).netloc
    return not any(h in host for h in ("news.google.com", "msn.com", "bing.com"))


def tokens(title: str) -> set[str]:
    words = re.findall(r"[0-9A-Za-z가-힣]{2,}", re.sub(r"\[[^\]]*\]", " ", title))
    result = set()
    for word in words:
        word = re.sub(r"(은|는|이|가|을|를|의|에|로|으로|과|와|도|만|서|에서|에게)$", "", word)
        if len(word) >= 2 and word not in STOPWORDS:
            result.add(word)
    return result


def dedupe(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 언론사의 같은 제목은 하나로. 원문 주소(리디렉션 아님)를 남긴다."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for article in articles:
        key = (norm_publisher(article["publisher"]), norm_title(article["title"]))
        current = best.get(key)
        if current is None:
            best[key] = article
            continue
        if is_direct(article["url"]) and not is_direct(current["url"]):
            article["summary"] = article["summary"] or current["summary"]
            best[key] = article
        elif not current["summary"] and article["summary"]:
            current["summary"] = article["summary"]
    return list(best.values())


def cluster(articles: list[dict[str, Any]], threshold: float = 0.45) -> list[list[dict[str, Any]]]:
    """제목 낱말이 많이 겹치면 같은 사안으로 묶는다. 보도량이 곧 중요도 신호가 된다."""
    groups: list[tuple[set[str], list[dict[str, Any]]]] = []
    for article in sorted(articles, key=lambda a: a["published"] or now_kst(), reverse=True):
        words = tokens(article["title"])
        placed = False
        for group_words, members in groups:
            if not words or not group_words:
                continue
            overlap = len(words & group_words)
            score = overlap / min(len(words), len(group_words))
            if overlap >= 2 and score >= threshold:
                members.append(article)
                group_words |= words
                placed = True
                break
        if not placed:
            groups.append((set(words), [article]))
    return [members for _, members in groups]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--briefing", default="edu_policy")
    args = parser.parse_args()

    config = load_config(args.briefing)
    end = now_kst()
    start, end = collection_window(end)
    hours = (end - start).total_seconds() / 3600
    days = max(1, math.ceil(hours / 24))
    print(f"[수집] {config['title']} 기간 {start:%m-%d %H:%M} ~ {end:%m-%d %H:%M} ({hours:.0f}시간)")

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    raw: list[dict[str, Any]] = []
    failures = 0
    for query in config["queries"]:
        google = fetch(session, GOOGLE_RSS, {"q": f"{query} when:{days}d", "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
        time.sleep(INTERVAL)
        bing = fetch(session, BING_RSS, {"q": query, "format": "rss", "setmkt": "ko-KR", "qft": "interval=\"8\""})
        time.sleep(INTERVAL)
        if google is None and bing is None:
            failures += 1
        found = (parse_google(google, query) if google else []) + (parse_bing(bing, query) if bing else [])
        raw.extend(found)
        print(f"  {query}: {len(found)}건")

    if failures == len(config["queries"]):
        print("모든 검색이 실패했습니다. 네트워크를 확인하세요.", file=sys.stderr)
        return 2

    in_window = [a for a in raw if a["published"] and start <= a["published"] <= end]
    patterns = [re.compile(p) for p in config.get("exclude_title_patterns", [])]
    excluded = [a for a in in_window if any(p.search(a["title"]) for p in patterns)]
    kept = [a for a in in_window if not any(p.search(a["title"]) for p in patterns)]
    unique = dedupe(kept)
    groups = cluster(unique)
    groups.sort(key=lambda g: (len({norm_publisher(a["publisher"]) for a in g}), len(g)), reverse=True)

    clusters = []
    for index, members in enumerate(groups, start=1):
        members.sort(key=lambda a: (not is_direct(a["url"]), a["published"]))
        clusters.append({
            "id": f"C{index:03d}",
            "size": len(members),
            "publishers": sorted({a["publisher"] for a in members}),
            "queries": sorted({a["query"] for a in members}),
            "articles": [
                {
                    "title": a["title"],
                    "publisher": a["publisher"],
                    "published": a["published"].isoformat(timespec="minutes"),
                    "url": a["url"],
                    "direct": is_direct(a["url"]),
                    "summary": a["summary"],
                }
                for a in members
            ],
        })

    payload = {
        "briefing": args.briefing,
        "collectedAt": end.isoformat(timespec="minutes"),
        "window": {"start": start.isoformat(timespec="minutes"), "end": end.isoformat(timespec="minutes"), "hours": round(hours)},
        "stats": {
            "raw": len(raw),
            "inWindow": len(in_window),
            "excludedByRule": len(excluded),
            "unique": len(unique),
            "clusters": len(clusters),
            "failedQueries": failures,
        },
        "clusters": clusters,
    }
    out = data_path(args.briefing, end.date(), "candidates.json")
    write_json(out, payload)
    print(f"[수집] 원자료 {len(raw)} → 기간 내 {len(in_window)} → 규칙 제외 {len(excluded)} → 고유 {len(unique)} → 사안 {len(clusters)}")
    print(f"[수집] 저장: {out.relative_to(out.parents[3])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
