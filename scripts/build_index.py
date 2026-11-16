"""웹페이지가 읽을 목록 파일을 만든다.

public/briefings/index.json            브리핑 종류와 최신 날짜
public/briefings/<id>/index.json       그 브리핑의 날짜 목록(최근순)
"""

from __future__ import annotations

import json

from common import BRIEFING_DIR, PUBLIC_DIR, write_json


def main() -> int:
    kinds = []
    configs = sorted(
        (json.loads(p.read_text(encoding="utf-8")) for p in BRIEFING_DIR.glob("*.json")),
        key=lambda c: c.get("order", 99),
    )
    for config in configs:
        folder = PUBLIC_DIR / config["id"]
        entries = []
        for path in sorted(folder.glob("????-??-??.json"), reverse=True):
            payload = json.loads(path.read_text(encoding="utf-8"))
            entries.append({
                "date": payload["date"],
                "dateLabel": payload["dateLabel"],
                "items": len(payload.get("items", [])),
            })
        if entries:
            write_json(folder / "index.json", entries)
        kinds.append({
            "id": config["id"],
            "title": config["title"],
            "latest": entries[0]["date"] if entries else None,
            "count": len(entries),
        })
    write_json(PUBLIC_DIR / "index.json", kinds)
    print("[목록] " + ", ".join(f"{k['title']} {k['count']}일" for k in kinds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
