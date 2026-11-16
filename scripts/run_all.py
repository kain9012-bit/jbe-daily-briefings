"""브리핑 전체를 차례로 만든다. 외부 스케줄러가 한 번 부르면 이것이 돈다.

- 휴일이면 아무것도 하지 않는다.
- 그날 파일이 이미 있으면 건너뛴다(--force 로 다시 만듦).
- 한 브리핑이 실패해도 다음 브리핑은 계속 만든다. 하나라도 실패하면 끝에 실패 코드로 끝난다.

실행: python scripts/run_all.py [--only edu_policy] [--force]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
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

    today = now_kst().date()
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
        if (PUBLIC_DIR / bid / f"{today}.json").exists() and not args.force:
            print(f"[전체] {config['title']} 오늘 것 있음 — 건너뜀")
            continue
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
