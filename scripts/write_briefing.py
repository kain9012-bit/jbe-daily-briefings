"""선정 → 본문 보강 → 작성 → 검증 → 발행.

1) 선정: 사안 목록(제목·보도량)만 보고 후보 shortlist개를 고른다.
2) 보강: 고른 사안의 기사 본문을 robots.txt가 허용하는 언론사에서만 가져온다(요약 근거용, 저장만 하고 공개하지 않음).
3) 작성: 최대 max_items건을 골라 브리핑 내용을 JSON으로 쓴다. 주소는 쓰지 않는다.
4) 검증: verify.py 규칙. 실패하면 오류를 알려 주고 다시 쓰게 한다(최대 2회).
5) 발행: 코드가 글을 조립해 public/briefings/<id>/<날짜>.json 에 저장한다.

실행: OPENROUTER_API_KEY=... python scripts/write_briefing.py --briefing edu_policy
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.robotparser
from datetime import date, datetime
from typing import Any

import requests
from bs4 import BeautifulSoup

from collect import BING_RSS, is_direct, norm_title, parse_bing
from common import PUBLIC_DIR, clean_text, data_path, korean_date_label, load_config, now_kst, read_json, write_json
from openrouter_client import DEFAULT_MODEL, OpenRouterClient, OpenRouterError
from render import pick_article, render_edu_policy
from verify import numbers_in, source_text, verify_draft, verify_text

USER_AGENT = "Mozilla/5.0 (compatible; jbe-daily-briefings/1.0; +https://github.com/kain9012-bit/jbe-daily-briefings)"
BODY_LIMIT = 1800
BODY_SELECTORS = [
    "[itemprop=articleBody]", "#article-view-content-div", "#articleBody", "#articleBodyContents",
    "#article_body", ".article_body", ".article-body", ".news_body", "#news_body_area",
    ".view_con", ".article_view", "#textBody", "article",
]

STYLE = (
    "문체 규칙: 공문서 개조식. 문장은 짧고 간결하게. "
    "'~함', '~임', '~됨', '~했다', '~이다'로 끝내지 말고 명사로 끝맺음(예: '교부금 산정 방식 개편 추진'). "
    "마크다운(**, ##, -, 링크 문법)과 이모지를 쓰지 말 것. 주소(URL)를 쓰지 말 것. "
    "원자료에 없는 숫자·기관·사실을 만들어 내지 말 것."
)


# ---------- 1) 선정 ----------

SELECT_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "topic": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "topic", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["picks"],
    "additionalProperties": False,
}


def cluster_line(cluster: dict[str, Any]) -> str:
    titles = [a["title"] for a in cluster["articles"][:3]]
    return f"{cluster['id']} | 기사 {cluster['size']}건·언론사 {len(cluster['publishers'])}곳 | " + " / ".join(titles)


def select(client: OpenRouterClient, config: dict[str, Any], candidates: dict[str, Any]) -> dict[str, Any]:
    system = (
        f"당신은 {config['audience']}에게 보낼 '{config['title']}'의 기사 선정 담당자입니다. "
        "전국 언론 기사 중 교육정책 담당자가 오늘 꼭 알아야 할 사안을 고릅니다."
    )
    prompt = "\n".join([
        f"아래는 최근 {candidates['window']['hours']}시간 동안 수집한 기사를 같은 사안끼리 묶은 목록입니다.",
        "형식: 사안ID | 보도량 | 대표 제목들",
        "",
        f"반드시 놓치지 말 주제: {', '.join(config['focus_topics'])}",
        f"조건부: {config['conditional_sources']}",
        f"제외: {config['exclude_rules']}. 특정 시·도의회 의원 한 명의 질의·발언, 개별 교육지원청·학교 소식, 입시 설명회도 전국 정책 변화가 아니면 제외.",
        "우선: 중앙정부·국회의 제도·예산·법령 변화, 여러 시도에 걸친 흐름, 시도교육청 운영에 직접 영향을 주는 결정.",
        "보도량이 많을수록 중요 신호지만, 보도량이 적어도 제도 변화면 고릅니다.",
        "",
        f"중요한 순서로 최대 {config['shortlist']}개 사안을 고르세요. 같은 사안이 여러 ID로 나뉘어 있으면 가장 보도량이 큰 ID 하나만.",
        "topic은 주제 분류(짧게), reason은 고른 이유 한 문장.",
        "",
        "[사안 목록]",
        *[cluster_line(c) for c in candidates["clusters"]],
    ])
    result = client.generate_json(system, prompt, SELECT_SCHEMA)
    ids = {c["id"] for c in candidates["clusters"]}
    picks, seen = [], set()
    for pick in result.get("picks", []):
        if pick["id"] in ids and pick["id"] not in seen:
            picks.append(pick)
            seen.add(pick["id"])
    return {"picks": picks[: config["shortlist"]]}


# ---------- 2) 본문 보강 ----------

class RobotsCache:
    def __init__(self, session: requests.Session) -> None:
        self.session = session
        self.cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        parts = urllib.parse.urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self.cache:
            parser = urllib.robotparser.RobotFileParser()
            try:
                response = self.session.get(f"{base}/robots.txt", timeout=(5, 10))
                if response.status_code >= 400:
                    parser.parse([])  # robots.txt가 없으면 허용으로 본다
                else:
                    parser.parse(response.text.splitlines())
                self.cache[base] = parser
            except requests.RequestException:
                self.cache[base] = None  # 확인 못 하면 가져오지 않는다
        parser = self.cache[base]
        return bool(parser and parser.can_fetch(USER_AGENT, url))


def extract_body(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "figure", "figcaption", "aside", "nav", "header", "footer", "form"]):
        tag.decompose()
    for selector in BODY_SELECTORS:
        node = soup.select_one(selector)
        if node:
            text = clean_text(node.get_text(" "))
            if len(text) >= 200:
                return text[:BODY_LIMIT]
    paragraphs = [clean_text(p.get_text(" ")) for p in soup.find_all("p")]
    text = " ".join(p for p in paragraphs if len(p) >= 40)
    if len(text) >= 200:
        return text[:BODY_LIMIT]
    meta = soup.find("meta", attrs={"property": "og:description"})
    return clean_text(meta.get("content", "")) if meta else ""


def resolve_direct(session: requests.Session, cluster: dict[str, Any]) -> bool:
    """원문 주소가 없는 사안(구글 뉴스 중계 주소뿐)은 같은 제목을 빙 뉴스에서 찾아 원문 주소를 얻는다.

    구글 중계 주소는 길고 카톡에서 보기 나빠서다. 제목이 글자 그대로 같은 기사만 인정한다.
    """
    if any(a["direct"] for a in cluster["articles"]):
        return False
    for article in cluster["articles"][:3]:
        try:
            response = session.get(
                BING_RSS, params={"q": article["title"], "format": "rss", "setmkt": "ko-KR"}, timeout=(5, 15)
            )
            time.sleep(1.0)
        except requests.RequestException:
            continue
        if response.status_code != 200:
            continue
        for found in parse_bing(response.text, "title-resolve"):
            if norm_title(found["title"]) == norm_title(article["title"]) and is_direct(found["url"]):
                cluster["articles"].insert(0, {
                    "title": found["title"],
                    "publisher": found["publisher"],
                    "published": (found["published"] or now_kst()).isoformat(timespec="minutes"),
                    "url": found["url"],
                    "direct": True,
                    "summary": found["summary"],
                    "resolvedFrom": article["url"],
                })
                return True
    return False


def enrich(picks: list[dict[str, Any]], clusters: dict[str, dict[str, Any]]) -> dict[str, dict[str, str]]:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    robots = RobotsCache(session)
    bodies: dict[str, dict[str, str]] = {}
    for pick in picks:
        cluster = clusters[pick["id"]]
        if resolve_direct(session, cluster):
            print(f"  {pick['id']} 원문 주소 찾음: {cluster['articles'][0]['url'][:60]}")
        got: dict[str, str] = {}
        used_publishers = set()
        for article in cluster["articles"]:
            if len(got) >= 2:
                break
            if not article["direct"] or article["publisher"] in used_publishers:
                continue
            used_publishers.add(article["publisher"])
            if not robots.allowed(article["url"]):
                continue
            try:
                response = session.get(article["url"], timeout=(5, 15))
                time.sleep(1.0)
            except requests.RequestException:
                continue
            if response.status_code != 200 or "html" not in response.headers.get("content-type", ""):
                continue
            response.encoding = response.apparent_encoding if response.encoding in (None, "ISO-8859-1") else response.encoding
            body = extract_body(response.text)
            if body:
                got[article["url"]] = body
        bodies[pick["id"]] = got
        print(f"  {pick['id']} 본문 {len(got)}건 확보 / {cluster['articles'][0]['title'][:40]}")
    return bodies


# ---------- 3) 작성 ----------

WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "today_line": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "policy_flow": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cluster_id": {"type": "string"},
                    "stars": {"type": "integer"},
                    "headline": {"type": "string"},
                    "core": {"type": "array", "items": {"type": "string"}},
                    "why": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["cluster_id", "stars", "headline", "core", "why"],
                "additionalProperties": False,
            },
        },
        "insights": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["today_line", "keywords", "policy_flow", "items", "insights"],
    "additionalProperties": False,
}


def material(pick: dict[str, Any], cluster: dict[str, Any], body: dict[str, str]) -> str:
    lines = [f"■ 사안 {cluster['id']} (주제: {pick['topic']}, 보도 {cluster['size']}건)"]
    for article in cluster["articles"][:6]:
        line = f"- [{article['publisher']} {article['published'][:10]}] {article['title']}"
        if article.get("summary"):
            line += f" — {article['summary'][:300]}"
        lines.append(line)
    for text in body.values():
        lines.append(f"  본문 발췌: {text}")
    return "\n".join(lines)


def write(client: OpenRouterClient, config: dict[str, Any], day: date, picks: list[dict[str, Any]],
          clusters: dict[str, dict[str, Any]], bodies: dict[str, dict[str, str]], feedback: list[str]) -> dict[str, Any]:
    system = (
        f"당신은 {config['audience']}를 위한 '{config['title']}' 작성자입니다. "
        "주어진 원자료만 근거로 씁니다. " + STYLE
    )
    prompt_lines = [
        f"오늘은 {korean_date_label(day)}입니다. 아래 후보 사안 중 최대 {config['max_items']}건을 골라 브리핑을 쓰세요.",
        "읽는 데 3~5분 분량. 중요한 것부터 순서대로.",
        "",
        "작성 항목",
        "- today_line: 오늘 동향 전체를 한 줄로(40자 안팎).",
        "- keywords: 오늘 흐름을 보여 주는 핵심어 3~5개(각 2~8자, # 없이).",
        "- policy_flow: 여러 기사를 관통하는 정책 흐름 2~3문장.",
        "- items: 기사별. cluster_id는 후보의 사안 ID 그대로. stars는 3(꼭 볼 것) 또는 2(참고).",
        "  headline: 사안을 한 줄로 다시 쓴 제목(30자 안팎, 기사 제목 복사 금지).",
        "  core: 기사 핵심 2~3개. 무엇이 언제 어떻게 바뀌는지 사실 위주.",
        "  why: 왜 중요한가 1~2개. 시도교육청·학교 현장에 미치는 영향 중심.",
        "- insights: 전북교육청 정책 담당자가 챙길 점 2~3개. 부서를 지정하거나 지시하는 말투 금지.",
        "",
        f"제외: {config['exclude_rules']}.",
    ]
    if feedback:
        prompt_lines += ["", "[직전 작성본의 검증 오류 — 반드시 고칠 것]", *[f"- {e}" for e in feedback]]
    prompt_lines += ["", "[후보 사안 원자료]"]
    for pick in picks:
        prompt_lines.append(material(pick, clusters[pick["id"]], bodies.get(pick["id"], {})))
        prompt_lines.append("")
    return client.generate_json(system, "\n".join(prompt_lines), WRITE_SCHEMA)


def strip_unverified(draft: dict[str, Any], clusters: dict[str, dict[str, Any]], bodies: dict[str, dict[str, str]]) -> dict[str, Any]:
    """마지막 수단: 원자료에 없는 숫자가 든 문장만 뺀다. 문장을 고쳐 쓰지는 않는다."""
    combined: set[str] = set()
    for item in draft["items"]:
        cid = item["cluster_id"]
        if cid not in clusters:
            continue
        source = source_text(clusters[cid], bodies.get(cid))
        combined |= source
        item["core"] = [c for c in item["core"] if all(n in source for n in numbers_in(c))]
        item["why"] = [w for w in item["why"] if all(n in source for n in numbers_in(w))]
    draft["items"] = [i for i in draft["items"] if i["cluster_id"] in clusters and i["core"] and i["why"]]
    draft["insights"] = [s for s in draft["insights"] if all(n in combined for n in numbers_in(s))]
    return draft


# ---------- 실행 ----------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--briefing", default="edu_policy")
    parser.add_argument("--date", help="candidates.json 날짜(기본: 오늘)")
    args = parser.parse_args()

    config = load_config(args.briefing)
    day = date.fromisoformat(args.date) if args.date else now_kst().date()
    candidates = read_json(data_path(args.briefing, day, "candidates.json"))
    clusters = {c["id"]: c for c in candidates["clusters"]}
    if not clusters:
        print("후보 기사가 없습니다.", file=sys.stderr)
        return 2

    model = os.environ.get("LLM_MODEL") or DEFAULT_MODEL
    client = OpenRouterClient(os.environ.get("OPENROUTER_API_KEY", ""), model=model)

    print(f"[선정] 사안 {len(clusters)}개 중 고르는 중 (모델 {model})")
    selection = select(client, config, candidates)
    write_json(data_path(args.briefing, day, "selection.json"), selection)
    picks = selection["picks"]
    if not picks:
        print("선정된 사안이 없습니다.", file=sys.stderr)
        return 2
    print(f"[선정] {len(picks)}개")

    print("[보강] 기사 본문 확보(robots.txt 허용분만)")
    bodies = enrich(picks, clusters)
    write_json(data_path(args.briefing, day, "sources.json"), bodies)
    # 원문 주소를 새로 찾은 사안이 있으면 후보 파일에도 반영한다(검증이 허용 주소로 쓴다).
    write_json(data_path(args.briefing, day, "candidates.json"), candidates)

    shortlist = [p["id"] for p in picks]
    feedback: list[str] = []
    draft: dict[str, Any] = {}
    errors: list[str] = []
    for attempt in range(1, 4):
        print(f"[작성] {attempt}회차")
        draft = write(client, config, day, picks, clusters, bodies, feedback)
        errors = verify_draft(draft, config, clusters, shortlist, bodies)
        if not errors:
            break
        print("  검증 오류: " + " / ".join(errors))
        feedback = errors
    notes: list[str] = []
    if errors:
        draft = strip_unverified(draft, clusters, bodies)
        errors = verify_draft(draft, config, clusters, shortlist, bodies)
        notes.append("원자료에서 확인되지 않은 숫자가 든 문장을 뺐음")
    if errors:
        print("[검증] 3회 작성 후에도 실패 — 발행하지 않습니다.", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        write_json(data_path(args.briefing, day, "draft.failed.json"), {"draft": draft, "errors": errors})
        return 1

    write_json(data_path(args.briefing, day, "draft.json"), draft)
    text, used = render_edu_policy(config["title"], day, draft, clusters)
    allowed = {a["url"] for c in candidates["clusters"] for a in c["articles"]}
    text_errors = verify_text(text, allowed)
    if text_errors:
        print("[검증] 조립본 실패: " + " / ".join(text_errors), file=sys.stderr)
        return 1

    published = {
        "briefing": args.briefing,
        "title": config["title"],
        "date": day.isoformat(),
        "dateLabel": korean_date_label(day),
        "window": candidates["window"],
        "generatedAt": now_kst().isoformat(timespec="minutes"),
        "model": client.served_model or model,
        "usage": client.usage,
        "stats": {**candidates["stats"], "shortlisted": len(picks), "published": len(used)},
        "verify": {"passed": True, "notes": notes},
        "items": used,
        "text": text,
    }
    out = PUBLIC_DIR / args.briefing / f"{day.isoformat()}.json"
    write_json(out, published)
    print(f"[발행] {out.relative_to(PUBLIC_DIR.parent.parent)} ({len(text)}자, 기사 {len(used)}건)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OpenRouterError as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        raise SystemExit(3)
