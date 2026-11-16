"""브리핑 검증. 통과하지 못하면 발행하지 않는다.

검사 항목
- 건수·별점·키워드 개수 등 형식
- 실린 사안이 수집·선정된 것인지
- 마크다운 기호·주소가 AI 글에 없는지(AI·데이터 브리핑은 이모지도 금지)
- 조립본의 주소가 모두 수집한 주소인지
- 글에 나온 숫자(두 자리 이상)가 그 사안 원자료(제목·요약·본문)에 실제로 있는지
- AI·데이터 브리핑: 언론 기사만 있는 사안의 기관명이 원자료에 나오는지

단독 실행: python scripts/verify.py --briefing edu_policy --date 2026-11-17
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Any

MARKDOWN = re.compile(r"\*\*|__|##|`|\[[^\]]+\]\(|^\s*[-*]\s", re.M)
URL = re.compile(r"https?://\S+")
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐⬆↔-⇿]")


def numbers_in(text: str) -> set[str]:
    found = set()
    for raw in NUMBER.findall(text or ""):
        value = raw.replace(",", "").rstrip(".")
        if len(value.replace(".", "")) >= 2:
            found.add(value)
    return found


def source_blob(cluster: dict[str, Any], extra: dict[str, str] | None = None) -> str:
    parts = []
    for article in cluster["articles"]:
        parts += [article["title"], article.get("summary", ""), article.get("published", ""), article.get("org", ""), article.get("publisher", "")]
    if extra:
        parts += list(extra.values())
    return " ".join(parts)


def source_text(cluster: dict[str, Any], extra: dict[str, str] | None = None) -> set[str]:
    """원자료에 나온 숫자 모음. 글자 일부 일치가 아니라 숫자 단위로 대조한다
    ('99'가 '1999' 안에 있다고 통과시키지 않으려고)."""
    return numbers_in(source_blob(cluster, extra))


def check_items(items: list[dict[str, Any]], config: dict[str, Any], clusters: dict[str, dict[str, Any]],
                shortlist: list[str], bodies: dict[str, dict[str, str]], text_fields: list[str]) -> tuple[list[str], set[str]]:
    errors: list[str] = []
    if not 1 <= len(items) <= config["max_items"]:
        errors.append(f"기사 수 {len(items)}건 — 1~{config['max_items']}건이어야 함")
    seen = set()
    all_numbers: set[str] = set()
    for index, item in enumerate(items, start=1):
        cid = item.get("cluster_id")
        if cid not in clusters or cid not in shortlist:
            errors.append(f"{index}번 기사: 선정 목록에 없는 사안 {cid}")
            continue
        if cid in seen:
            errors.append(f"{index}번 기사: 같은 사안 {cid} 중복")
        seen.add(cid)
        numbers = source_text(clusters[cid], bodies.get(cid))
        all_numbers |= numbers
        written = []
        for field in text_fields:
            value = item.get(field, "")
            written += value if isinstance(value, list) else [value]
        for number in sorted(numbers_in(" ".join(written))):
            if number not in numbers:
                errors.append(f"{index}번 기사: 숫자 '{number}'가 원자료에 없음")
    return errors, all_numbers


def common_text_checks(fields: list[str], forbid_emoji: bool) -> list[str]:
    errors = []
    joined = "\n".join(fields)
    if MARKDOWN.search(joined):
        errors.append("마크다운 기호(**, ##, `, [](), - 머리표)가 들어 있음")
    if URL.search(joined):
        errors.append("AI가 쓴 글에 주소가 들어 있음 — 주소는 코드가 넣음")
    if forbid_emoji and EMOJI.search(joined):
        errors.append("이모지가 들어 있음 — 일반 텍스트만")
    return errors


def verify_edu_policy(draft, config, clusters, shortlist, bodies) -> list[str]:
    items = draft.get("items") or []
    errors, numbers = check_items(items, config, clusters, shortlist, bodies, ["headline", "core", "why"])
    keywords = draft.get("keywords") or []
    if not 3 <= len(keywords) <= 5:
        errors.append(f"키워드 {len(keywords)}개 — 3~5개여야 함")
    if not (draft.get("today_line") or "").strip():
        errors.append("오늘 한 줄이 비어 있음")
    if not (draft.get("policy_flow") or "").strip():
        errors.append("정책 흐름이 비어 있음")
    if not draft.get("insights"):
        errors.append("인사이트가 비어 있음")
    for index, item in enumerate(items, start=1):
        if int(item.get("stars", 0)) not in (2, 3):
            errors.append(f"{index}번 기사: 별점은 2 또는 3")
        if not item.get("core") or not item.get("why"):
            errors.append(f"{index}번 기사: 기사 핵심·왜 중요한가가 비어 있음")
    overall = " ".join([draft.get("today_line", ""), draft.get("policy_flow", "")] + list(draft.get("insights", [])))
    for number in sorted(numbers_in(overall)):
        if number not in numbers:
            errors.append(f"오늘 한 줄·정책 흐름·인사이트: 숫자 '{number}'가 원자료에 없음")
    fields = [draft.get("today_line", ""), draft.get("policy_flow", "")] + list(keywords) + list(draft.get("insights", []))
    for item in items:
        fields += [item.get("headline", "")] + item.get("core", []) + item.get("why", [])
    return errors + common_text_checks(fields, forbid_emoji=False)


def verify_ai_data(draft, config, clusters, shortlist, bodies) -> list[str]:
    items = draft.get("items") or []
    errors, _ = check_items(items, config, clusters, shortlist, bodies, ["summary", "implication"])
    fields = []
    for index, item in enumerate(items, start=1):
        if not (item.get("summary") or "").strip() or not (item.get("implication") or "").strip():
            errors.append(f"{index}번 기사: 핵심내용·시사점이 비어 있음")
        cid = item.get("cluster_id")
        if cid in clusters and not clusters[cid].get("official"):
            org = (item.get("org") or "").strip()
            if not org or org not in source_blob(clusters[cid], bodies.get(cid)):
                errors.append(f"{index}번 기사: 기관명 '{org}'이 원자료에 없음")
        fields += [item.get("summary", ""), item.get("implication", ""), item.get("org", "")]
    return errors + common_text_checks(fields, forbid_emoji=True)


VERIFIERS = {"edu_policy": verify_edu_policy, "ai_data": verify_ai_data}


def verify_draft(draft, config, clusters, shortlist, bodies) -> list[str]:
    return VERIFIERS[config["format"]](draft, config, clusters, shortlist, bodies)


def verify_text(text: str, allowed_urls: set[str], max_chars: int = 4000) -> list[str]:
    errors = []
    for url in URL.findall(text):
        if url not in allowed_urls:
            errors.append(f"수집하지 않은 주소: {url[:80]}")
    if len(text) > max_chars:
        errors.append(f"글이 너무 김: {len(text)}자 (상한 {max_chars}자)")
    return errors


def main() -> int:
    from datetime import date

    from common import PUBLIC_DIR, data_path, load_config, read_json

    parser = argparse.ArgumentParser()
    parser.add_argument("--briefing", default="edu_policy")
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    day = date.fromisoformat(args.date)
    config = load_config(args.briefing)
    candidates = read_json(data_path(args.briefing, day, "candidates.json"))
    clusters = {c["id"]: c for c in candidates["clusters"]}
    selection = read_json(data_path(args.briefing, day, "selection.json"))
    sources = read_json(data_path(args.briefing, day, "sources.json"))
    draft = read_json(data_path(args.briefing, day, "draft.json"))
    published = read_json(PUBLIC_DIR / args.briefing / f"{args.date}.json")

    errors = verify_draft(draft, config, clusters, [p["id"] for p in selection["picks"]], sources)
    allowed = {a["url"] for c in candidates["clusters"] for a in c["articles"]}
    errors += verify_text(published["text"], allowed)
    if config["format"] == "ai_data":
        if URL.search(published["text"]) or EMOJI.search(published["text"]):
            errors.append("AI·데이터 브리핑 본문에 주소나 이모지가 있음")
    if errors:
        print(f"[검증] {args.briefing} 실패")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"[검증] {args.briefing} 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
