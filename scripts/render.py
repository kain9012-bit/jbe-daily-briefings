"""AI가 쓴 내용(JSON)을 카톡용 일반 글자로 조립한다.

기사 주소·제목·언론사·기관·날짜는 AI가 아니라 수집 자료에서 코드가 넣는다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from common import korean_date_label

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"


def pick_article(cluster: dict[str, Any], preferred: list[str] | None = None) -> dict[str, Any]:
    """대표 자료: 공식 보도자료 → 선호 언론사(원문 주소) → 원문 주소 → 첫 기사."""
    articles = cluster["articles"]
    for test in (
        lambda a: a.get("kind") == "official",
        lambda a: a.get("direct") and any(p in a["publisher"] for p in (preferred or [])),
        lambda a: a.get("direct"),
    ):
        found = [a for a in articles if test(a)]
        if found:
            return found[0]
    return articles[0]


def short_date(iso: str) -> str:
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    return f"{moment.month}.{moment.day}."


def full_date(iso: str) -> str:
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    return f"{moment.year}.{moment.month:02d}.{moment.day:02d}."


def used_entry(cluster: dict[str, Any], article: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "clusterId": cluster["id"],
        "title": article["title"],
        "publisher": article["publisher"],
        "org": article.get("org", ""),
        "kind": article.get("kind", "news"),
        "published": article["published"],
        "url": article["url"],
        "relatedCount": cluster["size"],
        **extra,
    }


def render_edu_policy(config: dict[str, Any], day: date, draft: dict[str, Any], clusters: dict[str, dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    lines: list[str] = [f"📚 {config['title']}", korean_date_label(day), ""]
    lines += ["📌 오늘 한 줄", draft["today_line"].strip(), ""]
    lines += ["🏷 키워드", " ".join(f"#{k.strip().lstrip('#').replace(' ', '')}" for k in draft["keywords"]), ""]
    lines += ["📈 정책 흐름", draft["policy_flow"].strip(), ""]

    preferred = config["sources"].get("news", {}).get("preferred_publishers", [])
    used: list[dict[str, Any]] = []
    for index, item in enumerate(draft["items"]):
        cluster = clusters[item["cluster_id"]]
        article = pick_article(cluster, preferred)
        stars = "⭐⭐⭐" if int(item["stars"]) >= 3 else "⭐⭐"
        lines.append(f"{stars} {CIRCLED[index]} {item['headline'].strip()}")
        lines.append(f"({article['publisher']}, {short_date(article['published'])})")
        lines.append("기사 핵심")
        lines += [f"• {c.strip()}" for c in item["core"]]
        lines.append("📌 왜 중요한가")
        lines += [f"• {w.strip()}" for w in item["why"]]
        lines.append(f"🔗 {article['url']}")
        lines.append("")
        used.append(used_entry(cluster, article, headline=item["headline"].strip(), stars=int(item["stars"])))

    lines.append("💡 인사이트")
    lines += [f"• {i.strip()}" for i in draft["insights"]]
    return "\n".join(lines).strip() + "\n", used


def render_ai_data(config: dict[str, Any], day: date, draft: dict[str, Any], clusters: dict[str, dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """오직 일반 텍스트: 이모지·링크·마크다운 없음, 머리말·맺음말 없음."""
    preferred = config["sources"].get("news", {}).get("preferred_publishers", [])
    lines: list[str] = [f"{config['title']} {korean_date_label(day)}", ""]
    used: list[dict[str, Any]] = []
    for index, item in enumerate(draft["items"], start=1):
        cluster = clusters[item["cluster_id"]]
        article = pick_article(cluster, preferred)
        org = article.get("org") or item["org"].strip()
        lines.append(f"{index}. {article['title']}")
        lines.append(f"기관: {org}")
        lines.append(f"게시일: {full_date(article['published'])}")
        lines.append(f"핵심내용: {item['summary'].strip()}")
        lines.append(f"전북교육청 시사점: {item['implication'].strip()}")
        lines.append("")
        used.append(used_entry(cluster, article, org=org))
    return "\n".join(lines).strip() + "\n", used


RENDERERS = {"edu_policy": render_edu_policy, "ai_data": render_ai_data}
