"""AI가 쓴 내용(JSON)을 카톡용 일반 글자로 조립한다.

기사 주소·언론사·날짜는 AI가 아니라 수집 자료에서 코드가 넣는다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from common import korean_date_label

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"


def pick_article(cluster: dict[str, Any]) -> dict[str, Any]:
    """대표 기사: 원문 주소가 있는 것 우선, 그다음 이른 보도."""
    articles = cluster["articles"]
    direct = [a for a in articles if a.get("direct")]
    return (direct or articles)[0]


def short_date(iso: str) -> str:
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    return f"{moment.month}.{moment.day}."


def render_edu_policy(title: str, day: date, draft: dict[str, Any], clusters: dict[str, dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    lines: list[str] = [f"📚 {title}", korean_date_label(day), ""]
    lines += ["📌 오늘 한 줄", draft["today_line"].strip(), ""]
    lines += ["🏷 키워드", " ".join(f"#{k.strip().lstrip('#').replace(' ', '')}" for k in draft["keywords"]), ""]
    lines += ["📈 정책 흐름", draft["policy_flow"].strip(), ""]

    used: list[dict[str, Any]] = []
    for index, item in enumerate(draft["items"]):
        cluster = clusters[item["cluster_id"]]
        article = pick_article(cluster)
        stars = "⭐⭐⭐" if int(item["stars"]) >= 3 else "⭐⭐"
        lines.append(f"{stars} {CIRCLED[index]} {item['headline'].strip()}")
        lines.append(f"({article['publisher']}, {short_date(article['published'])})")
        lines.append("기사 핵심")
        lines += [f"• {c.strip()}" for c in item["core"]]
        lines.append("📌 왜 중요한가")
        lines += [f"• {w.strip()}" for w in item["why"]]
        lines.append(f"🔗 {article['url']}")
        lines.append("")
        used.append({
            "clusterId": item["cluster_id"],
            "stars": int(item["stars"]),
            "headline": item["headline"].strip(),
            "title": article["title"],
            "publisher": article["publisher"],
            "published": article["published"],
            "url": article["url"],
            "relatedCount": cluster["size"],
        })

    lines.append("💡 인사이트")
    lines += [f"• {i.strip()}" for i in draft["insights"]]
    return "\n".join(lines).strip() + "\n", used
