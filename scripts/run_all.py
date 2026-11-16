"""브리핑 전체를 차례로 만든다. 외부 스케줄러가 한 번 부르면 이것이 돈다.

- 휴일이면 아무것도 하지 않는다.
- 마감 시각(기본 07:00) 전이면 만들지 않는다(--force 로 강제).
- 그날 파일이 마감 뒤 자료로 이미 있으면 건너뛴다(--force 로 다시 만듦).
  마감 전 자료로 만들어진 파일이면 다시 만든다.
- 한 브리핑이 실패해도 다음 브리핑은 계속 만든다. 하나라도 실패하면 끝에 실패 코드로 끝난다.

실행: python scripts/run_all.py [--only edu_policy] [--force]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from common import BRIEFING_DIR, PUBLIC_DIR, is_business_day, now_kst

SCRIPTS = Path(__file__).resolve().parent


def step(*args: str) -> bool:
    result = subprocess.run([sys.executable, str(SCRIPTS / args[0]), *args[1:]])
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="브리핑 id 하나만(기본: 전체)")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    now = now_kst()
    today = now.date()
    if not is_business_day(today) and not args.force:
        print(f"[전체] {today} 휴일 — 만들지 않음")
        return 0

    configs = sorted(
        (json.loads(p.read_text(encoding="utf-8")) for p in BRIEFING_DIR.glob("*.json")),
        key=lambda c: c.get("order", 99),
    )
    failed = []
    for config in configs:
        bid = config["id"]
        if args.only and args.only not in ("all", bid):
            continue
        cutoff = now.replace(hour=int(config.get("cutoff_hour", 7)), minute=0, second=0, microsecond=0)
        # 마감 전 실행은 기간이 덜 찬 브리핑을 만들고, 그 뒤 정시 실행이 '오늘 것 있음'으로
        # 건너뛰게 된다(2026-11-17 00:12 시험 실행에서 발생). 마감 전에는 강제 실행만 허용.
        if now < cutoff and not args.force:
            print(f"[전체] {config['title']} 마감({cutoff:%H:%M}) 전 — 만들지 않음")
            continue
        existing = PUBLIC_DIR / bid / f"{today}.json"
        if existing.exists() and not args.force:
            window_end = json.loads(existing.read_text(encoding="utf-8")).get("window", {}).get("end", "")
            if window_end and datetime.fromisoformat(window_end) >= cutoff:
                print(f"[전체] {config['title']} 오늘 것 있음 — 건너뜀")
                continue
            print(f"[전체] {config['title']} 오늘 것이 마감 전 자료로 만들어짐 — 다시 만듦")
        print(f"\n===== {config['title']} =====")
        ok = (
            step("collect.py", "--briefing", bid)
            and step("write_briefing.py", "--briefing", bid, "--date", today.isoformat())
            and step("verify.py", "--briefing", bid, "--date", today.isoformat())
        )
        if not ok:
            failed.append(config["title"])
            # 검증을 통과 못 한 발행본이 남지 않게 지운다
            leftover = PUBLIC_DIR / bid / f"{today}.json"
            if leftover.exists():
                leftover.unlink()
    step("build_index.py")
    if failed:
        print(f"\n[전체] 실패: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("\n[전체] 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
