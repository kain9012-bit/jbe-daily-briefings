"""브리핑 공통 도구: 기간 계산, 경로, 글자 정리."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
BRIEFING_DIR = ROOT / "briefings"
DATA_DIR = ROOT / "data"
PUBLIC_DIR = ROOT / "public" / "briefings"

WEEKDAYS = "월화수목금토일"


def now_kst() -> datetime:
    return datetime.now(KST)


def is_business_day(day: date) -> bool:
    import holidays  # 설치 안 된 환경에서 다른 기능은 쓸 수 있게 늦게 부른다

    return day.weekday() < 5 and day not in holidays.KR(years=day.year)


def previous_business_day(day: date) -> date:
    cursor = day - timedelta(days=1)
    while not is_business_day(cursor):
        cursor -= timedelta(days=1)
    return cursor


def collection_window(end: datetime) -> tuple[datetime, datetime]:
    """직전 영업일 같은 시각부터 지금까지. 평일 24시간, 월요일 72시간, 연휴 뒤는 그만큼 더."""
    start_day = previous_business_day(end.date())
    start = datetime.combine(start_day, end.timetz())
    return start, end


def load_config(briefing_id: str) -> dict[str, Any]:
    path = BRIEFING_DIR / f"{briefing_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def data_path(briefing_id: str, day: date, name: str) -> Path:
    folder = DATA_DIR / briefing_id / day.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    return folder / name


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def clean_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = text.replace("&nbsp;", " ").replace("&quot;", '"').replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'")
    return re.sub(r"\s+", " ", text).strip()


def korean_date_label(day: date) -> str:
    return f"{day.year}.{day.month:02d}.{day.day:02d}.({WEEKDAYS[day.weekday()]})"
