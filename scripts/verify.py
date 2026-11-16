"""브리핑 검증. 통과하지 못하면 발행하지 않는다.

검사 항목
- 건수·별점·키워드 개수 등 형식
- 실린 기사가 수집·선정된 사안인지
- 마크다운 기호가 없는지
- 본문의 주소가 모두 수집한 기사 주소인지
- 글에 나온 숫자(두 자리 이상)가 그 기사 원자료(제목·요약·본문)에 실제로 있는지

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


def numbers_in(text: str) -> set[str]:
    found = set()
    for raw in NUMBER.findall(text or ""):
        value = raw.replace(",", "").rstrip(".")
        if len(value.replace(".", "")) >= 2:
            found.add(value)
    return found


def source_text(cluster: dict[str, Any], extra: dict[str, str] | None = None) -> set[str]:
    """원자료에 나온 숫자 모음. 글자 일부 일치가 아니라 숫자 단위로 대조한다
    ('99'가 '1999' 안에 있다고 통과시키지 않으려고)."""
    parts = []
    for article in cluster["articles"]:
        parts += [article["title"], article.get("summary", ""), article.get("published", "")]
    if extra:
        parts += list(extra.values())
    return numbers_in(" ".join(parts))


def verify_draft(
    draft: dict[str, Any],
    config: dict[str, Any],
    clusters: dict[str, dict[str, Any]],
    shortlist: list[str],
    bodies: dict[str, dict[str, str]],
) -> list[str]:
    errors: list[str] = []
    items = draft.get("items") or []
    if not 1 <= len(items) <= config["max_items"]:
        errors.append(f"기사 수 {len(items)}건 — 1~{config['max_items']}건이어야 함")
    keywords = draft.get("keywords") or []
    if not 3 <= len(keywords) <= 5:
        errors.append(f"키워드 {len(keywords)}개 — 3~5개여야 함")
    if not (draft.get("today_line") or "").strip():
        errors.append("오늘 한 줄이 비어 있음")
    if not (draft.get("policy_flow") or "").strip():
        errors.append("정책 흐름이 비어 있음")
    if not draft.get("insights"):
        errors.append("인사이트가 비어 있음")

    seen = set()
    all_sources: set[str] = set()
    for index, item in enumerate(items, start=1):
        cid = item.get("cluster_id")
        if cid not in clusters or cid not in shortlist:
            errors.append(f"{index}번 기사: 선정 목록에 없는 사안 {cid}")
            continue
        if cid in seen:
            errors.append(f"{index}번 기사: 같은 사안 {cid} 중복")
        seen.add(cid)
        if int(item.get("stars", 0)) not in (2, 3):
            errors.append(f"{index}번 기사: 별점은 2 또는 3")
        if not item.get("core") or not item.get("why"):
            errors.append(f"{index}번 기사: 기사 핵심·왜 중요한가가 비어 있음")
        source = source_text(clusters[cid], bodies.get(cid))
        all_sources |= source
        written = " ".join([item.get("headline", "")] + item.get("core", []) + item.get("why", []))
        for number in sorted(numbers_in(written)):
            if number not in source:
                errors.append(f"{index}번 기사: 숫자 '{number}'가 원자료에 없음")

    combined = all_sources
    overall = " ".join([draft.get("today_line", ""), draft.get("policy_flow", "")] + list(draft.get("insights", [])))
    for number in sorted(numbers_in(overall)):
        if number not in combined:
            errors.append(f"오늘 한 줄·정책 흐름·인사이트: 숫자 '{number}'가 원자료에 없음")

    every_field = [draft.get("today_line", ""), draft.get("policy_flow", "")] + list(keywords) + list(draft.get("insights", []))
    for item in items:
        every_field += [item.get("headline", "")] + item.get("core", []) + item.get("why", [])
    joined = "\n".join(every_field)
    if MARKDOWN.search(joined):
        errors.append("마크다운 기호(**, ##, `, [](), - 머리표)가 들어 있음")
    if URL.search(joined):
        errors.append("AI가 쓴 글에 주소가 들어 있음 — 주소는 코드가 넣음")
    return errors


def verify_text(text: str, allowed_urls: set[str], max_chars: int = 3500) -> list[str]:
    errors = []
    for url in URL.findall(text):
        if url not in allowed_urls:
            errors.append(f"수집하지 않은 주소: {url[:80]}")
    if len(text) > max_chars:
        errors.append(f"글이 너무 김: {len(text)}자 (상한 {max_chars}자)")
    return errors


def main() -> int:
    from common import PUBLIC_DIR, read_json, data_path, load_config
    from datetime import date

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
    if errors:
        print("[검증] 실패")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("[검증] 통과")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    raise SystemExit(main())
