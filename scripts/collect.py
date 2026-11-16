"""후보 수집: 언론 기사(구글·빙 뉴스 RSS) + 공식 보도자료 → 기간 필터 → 제외 규칙 → 중복 제거 → 사안 묶기.

출력: data/<briefing>/<날짜>/candidates.json
AI는 쓰지 않는다. 여기서 모은 자료의 주소·제목·기관·날짜만 브리핑에 실린다.

공식 보도자료 출처(설정 sources.official)
- korea_kr: 정책브리핑(korea.kr) 보도자료 목록. 기관명·날짜·요약(lead)이 함께 온다. RSS는 중단됨
- edu_news_alert: 교육부·시도교육청 보도자료(edu-news-alert가 매일 모으는 공개 news.json)
보도자료는 날짜만 있으므로 '직전 영업일 ~ 어제' 날짜분을 쓴다(오늘 날짜분은 다음 영업일 몫).
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests
from bs4 import BeautifulSoup

from common import KST, clean_text, collection_window, data_path, load_config, now_kst, write_json

GOOGLE_RSS = "https://news.google.com/rss/search"
BING_RSS = "https://www.bing.com/news/search"
KOREA_KR_LIST = "https://www.korea.kr/briefing/pressReleaseList.do"
USER_AGENT = "Mozilla/5.0 (compatible; jbe-daily-briefings/1.0; +https://github.com/kain9012-bit/jbe-daily-briefings)"
TIMEOUT = (5, 20)
INTERVAL = 1.5  # 요청 사이 최소 간격(초)

# 제목 비교 때 버리는 상투어. 이것만 겹쳐 다른 사안이 묶이는 것을 막는다.
STOPWORDS = {
    "교육", "교육부", "교육청", "학교", "학생", "정책", "추진", "지원", "운영", "확대",
    "위한", "통해", "관련", "올해", "내년", "전국", "지역", "기자", "단독", "종합",
    "AI", "인공지능", "데이터", "디지털", "공공", "기반", "활용", "도입",
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


def fetch(session: requests.Session, url: str, params: dict[str, str] | None = None) -> str | None:
    try:
        response = session.get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException as exc:
        print(f"  요청 실패: {exc}", file=sys.stderr)
        return None
    if response.status_code != 200:
        print(f"  응답 코드 {response.status_code}: {url}", file=sys.stderr)
        return None
    if response.encoding in (None, "ISO-8859-1"):
        response.encoding = "utf-8"
    return response.text


def article(title: str, publisher: str, published: datetime | None, url: str, summary: str,
            kind: str, query: str, org: str = "", date_only: bool = False) -> dict[str, Any]:
    return {
        "title": clean_text(title), "publisher": publisher, "published": published, "url": url,
        "summary": summary, "kind": kind, "query": query, "org": org, "dateOnly": date_only,
    }


# ---------- 언론 기사 ----------

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
        # 구글 뉴스 RSS 설명은 링크 목록뿐이라 버린다
        out.append(article(title, source or "미상", parse_pubdate(node.findtext("pubDate")), link, "", "news", query))
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
        out.append(article(
            title, source or urllib.parse.urlsplit(link).netloc.replace("www.", ""),
            parse_pubdate(node.findtext("pubDate")), link,
            clean_text(node.findtext("description") or "")[:400], "news", query,
        ))
    return out


def collect_news(session: requests.Session, queries: list[str], days: int) -> tuple[list[dict[str, Any]], int]:
    found_all: list[dict[str, Any]] = []
    failures = 0
    for query in queries:
        google = fetch(session, GOOGLE_RSS, {"q": f"{query} when:{days}d", "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
        time.sleep(INTERVAL)
        bing = fetch(session, BING_RSS, {"q": query, "format": "rss", "setmkt": "ko-KR", "qft": 'interval="8"'})
        time.sleep(INTERVAL)
        if google is None and bing is None:
            failures += 1
        found = (parse_google(google, query) if google else []) + (parse_bing(bing, query) if bing else [])
        found_all.extend(found)
        print(f"  [기사] {query}: {len(found)}건")
    return found_all, failures


# ---------- 공식 보도자료 ----------

def as_day_moment(day: date) -> datetime:
    """날짜만 있는 보도자료의 정렬용 시각(정오)."""
    return datetime(day.year, day.month, day.day, 12, 0, tzinfo=KST)


def collect_korea_kr(session: requests.Session, conf: dict[str, Any], first: date, last: date) -> tuple[list[dict[str, Any]], bool]:
    """정책브리핑 보도자료 목록을 최신순으로 넘기며 first~last 날짜분을 모은다."""
    orgs = set(conf["orgs"])
    org_pattern = re.compile(conf["org_title_or_lead_pattern"])
    any_pattern = re.compile(conf["any_org_title_pattern"])
    out: list[dict[str, Any]] = []
    seen_pages = 0
    ok = False
    for page in range(1, 61):
        html = fetch(session, KOREA_KR_LIST, {"pageIndex": str(page)})
        time.sleep(0.8)
        if html is None:
            break
        seen_pages += 1
        items = BeautifulSoup(html, "html.parser").select(".list_type ul li")
        if not items:
            break
        ok = True
        oldest = None
        for li in items:
            link = li.find("a")
            title_node = li.select_one("strong")
            source = [s.get_text(strip=True) for s in li.select(".source span")]
            if not link or not title_node or len(source) < 2:
                continue
            try:
                day = date.fromisoformat(source[0].replace(".", "-"))
            except ValueError:
                continue
            oldest = day if oldest is None else min(oldest, day)
            if not first <= day <= last:
                continue
            org = source[1]
            title = title_node.get_text(" ", strip=True)
            lead_node = li.select_one(".lead")
            lead = clean_text(lead_node.get_text(" ", strip=True)) if lead_node else ""
            wanted = (org in orgs and org_pattern.search(f"{title} {lead[:300]}")) or any_pattern.search(title)
            if not wanted:
                continue
            href = urllib.parse.urljoin(KOREA_KR_LIST, link["href"])
            news_id = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query).get("newsId", [""])[0]
            url = f"https://www.korea.kr/briefing/pressReleaseView.do?newsId={news_id}" if news_id else href
            out.append(article(title, org, as_day_moment(day), url, lead[:600], "official", "정책브리핑", org, True))
        if oldest is not None and oldest < first:
            break
    print(f"  [공식] 정책브리핑 {seen_pages}쪽 확인, {len(out)}건 채택")
    return out, ok


def collect_edu_news_alert(session: requests.Session, conf: dict[str, Any], first: date, last: date) -> tuple[list[dict[str, Any]], bool]:
    try:
        response = session.get(conf["url"], timeout=(5, 60))
        response.raise_for_status()
        items = response.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"  [공식] 시도교육청 보도자료 받기 실패: {exc}", file=sys.stderr)
        return [], False
    items = items if isinstance(items, list) else items.get("items", [])
    pattern = re.compile(conf["title_pattern"])
    excluded = set(conf.get("exclude_source_ids", []))
    out = []
    for item in items:
        try:
            day = date.fromisoformat(str(item.get("date", ""))[:10])
        except ValueError:
            continue
        if not first <= day <= last or item.get("sourceId") in excluded:
            continue
        if not pattern.search(item.get("title", "")):
            continue
        org = item.get("source", "")
        out.append(article(item["title"], org, as_day_moment(day), item["url"],
                           clean_text(item.get("summary", ""))[:600], "official", "시도교육청 보도자료", org, True))
    print(f"  [공식] 교육부·시도교육청 {len(out)}건 채택")
    return out, True


# ---------- 정리 ----------

def norm_title(title: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", title or "")


def norm_publisher(name: str) -> str:
    text = re.sub(r"\s*on\s+MSN\s*$", "", name or "", flags=re.I)
    return re.sub(r"\s+", "", text).lower()


def is_direct(url: str) -> bool:
    host = urllib.parse.urlsplit(url).netloc
    return not any(h in host for h in ("news.google.com", "msn.com", "bing.com"))


def tokens(title: str) -> set[str]:
    words = re.findall(r"[0-9A-Za-z가-힣]{2,}", re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", title))
    result = set()
    for word in words:
        word = re.sub(r"(은|는|이|가|을|를|의|에|로|으로|과|와|도|만|서|에서|에게)$", "", word)
        if len(word) >= 2 and word not in STOPWORDS:
            result.add(word)
    return result


def dedupe(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 언론사의 같은 제목은 하나로. 원문 주소(리디렉션 아님)를 남긴다."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for item in articles:
        key = (norm_publisher(item["publisher"]), norm_title(item["title"]))
        current = best.get(key)
        if current is None:
            best[key] = item
            continue
        if is_direct(item["url"]) and not is_direct(current["url"]):
            item["summary"] = item["summary"] or current["summary"]
            best[key] = item
        elif not current["summary"] and item["summary"]:
            current["summary"] = item["summary"]
    return list(best.values())


def cluster(articles: list[dict[str, Any]], threshold: float = 0.45) -> list[list[dict[str, Any]]]:
    """제목 낱말이 많이 겹치면 같은 사안으로 묶는다. 보도량이 곧 중요도 신호가 된다.
    공식 보도자료를 먼저 놓아 사안의 대표가 되게 한다."""
    groups: list[tuple[set[str], list[dict[str, Any]]]] = []
    ordered = sorted(articles, key=lambda a: (a["kind"] != "official", -(a["published"] or now_kst()).timestamp()))
    for item in ordered:
        words = tokens(item["title"])
        placed = False
        for group_words, members in groups:
            if not words or not group_words:
                continue
            overlap = len(words & group_words)
            score = overlap / min(len(words), len(group_words))
            # 대표 기사 낱말과만 비교한다. 묶을 때마다 낱말을 합치면 눈덩이처럼 커져
            # 수능 관련 기사 50건이 한 사안으로 뭉쳤다(2026-11-16 시험).
            if overlap >= 2 and score >= threshold:
                members.append(item)
                placed = True
                break
        if not placed:
            groups.append((set(words), [item]))
    return [members for _, members in groups]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--briefing", default="edu_policy")
    args = parser.parse_args()

    config = load_config(args.briefing)
    now = now_kst()
    start, end = collection_window(now, int(config.get("cutoff_hour", 7)))
    hours = (end - start).total_seconds() / 3600
    days = max(1, math.ceil(hours / 24))
    # 날짜만 있는 보도자료: 직전 영업일 ~ 어제(오늘 날짜분은 다음 영업일 몫)
    first_day, last_day = start.date(), end.date() - timedelta(days=1)
    print(f"[수집] {config['title']} 기사 {start:%m-%d %H:%M} ~ {end:%m-%d %H:%M} ({hours:.0f}시간), 보도자료 {first_day:%m-%d} ~ {last_day:%m-%d}")

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    sources = config["sources"]
    source_status: dict[str, bool] = {}

    raw: list[dict[str, Any]] = []
    news_failures = 0
    if "news" in sources:
        found, news_failures = collect_news(session, sources["news"]["queries"], days)
        raw += found
        source_status["news"] = news_failures < len(sources["news"]["queries"])
    official: list[dict[str, Any]] = []
    if "korea_kr" in sources.get("official", {}):
        found, ok = collect_korea_kr(session, sources["official"]["korea_kr"], first_day, last_day)
        official += found
        source_status["korea_kr"] = ok
    if "edu_news_alert" in sources.get("official", {}):
        found, ok = collect_edu_news_alert(session, sources["official"]["edu_news_alert"], first_day, last_day)
        official += found
        source_status["edu_news_alert"] = ok

    if not any(source_status.values()):
        print("모든 출처 수집이 실패했습니다. 네트워크를 확인하세요.", file=sys.stderr)
        return 2

    in_window = [a for a in raw if a["published"] and start <= a["published"] <= end] + official
    patterns = [re.compile(p) for p in config.get("exclude_title_patterns", [])]
    excluded = [a for a in in_window if any(p.search(a["title"]) for p in patterns)]
    kept = [a for a in in_window if not any(p.search(a["title"]) for p in patterns)]
    unique = dedupe(kept)
    groups = cluster(unique)
    groups.sort(key=lambda g: (len({norm_publisher(a["publisher"]) for a in g}), len(g)), reverse=True)

    clusters = []
    for index, members in enumerate(groups, start=1):
        members.sort(key=lambda a: (a["kind"] != "official", not is_direct(a["url"]), a["published"]))
        clusters.append({
            "id": f"C{index:03d}",
            "size": len(members),
            "official": any(a["kind"] == "official" for a in members),
            "publishers": sorted({a["publisher"] for a in members}),
            "articles": [
                {
                    "title": a["title"],
                    "publisher": a["publisher"],
                    "org": a["org"],
                    "kind": a["kind"],
                    "published": a["published"].isoformat(timespec="minutes"),
                    "dateOnly": a["dateOnly"],
                    "url": a["url"],
                    "direct": is_direct(a["url"]),
                    "summary": a["summary"],
                }
                for a in members
            ],
        })

    payload = {
        "briefing": args.briefing,
        "collectedAt": now.isoformat(timespec="minutes"),
        "window": {"start": start.isoformat(timespec="minutes"), "end": end.isoformat(timespec="minutes"), "hours": round(hours)},
        "stats": {
            "raw": len(raw) + len(official),
            "official": len(official),
            "inWindow": len(in_window),
            "excludedByRule": len(excluded),
            "unique": len(unique),
            "clusters": len(clusters),
            "failedQueries": news_failures,
            "sources": source_status,
        },
        "clusters": clusters,
    }
    out = data_path(args.briefing, now.date(), "candidates.json")
    write_json(out, payload)
    print(f"[수집] 원자료 {payload['stats']['raw']}(공식 {len(official)}) → 기간 내 {len(in_window)} → 규칙 제외 {len(excluded)} → 고유 {len(unique)} → 사안 {len(clusters)}")
    failed = [k for k, v in source_status.items() if not v]
    if failed:
        print(f"[수집] 실패한 출처: {', '.join(failed)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
