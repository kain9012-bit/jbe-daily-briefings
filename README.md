# 아침 브리핑 (jbe-daily-briefings)

GPT 예약으로 받던 아침 브리핑을 옮긴 자체 파이프라인. 평일 아침 기사 수집 → AI 선정·작성 → 검증 → 웹페이지 발행. 웹페이지에서 그날 브리핑을 **카톡용 복사** 버튼 하나로 복사.

- 1단계: 교육정책 동향 브리핑(`briefings/edu_policy.json`)
- 2단계(예정): AI·데이터 브리핑 — 같은 틀에 설정 파일 추가

## 흐름

| 단계 | 파일 | AI 사용 | 내용 |
|---|---|---|---|
| 수집 | `scripts/collect.py` | 없음 | 구글·빙 뉴스 RSS, 직전 영업일 같은 시각부터(월요일 72시간, 연휴 뒤 그만큼 더), 제외 규칙, 중복 제거, 같은 사안 묶기 |
| 선정 | `scripts/write_briefing.py` | OpenRouter | 사안 목록(제목·보도량)에서 후보 12개 |
| 보강 | 〃 | 없음 | robots.txt 허용 언론사만 본문 발췌(근거용, 공개 안 함). 구글 중계 주소뿐인 사안은 같은 제목을 빙에서 찾아 원문 주소 확보 |
| 작성 | 〃 | OpenRouter | 최대 5건, 내용만 JSON으로. **주소·언론사·날짜는 AI가 쓰지 않고 코드가 수집 자료에서 넣음** |
| 검증 | `scripts/verify.py` | 없음 | 건수·형식, 마크다운 기호, 수집한 주소인지, 글 속 숫자가 원자료에 있는지. 실패 시 오류를 알려 주고 재작성(최대 3회), 그래도 실패하면 발행 안 함 |
| 발행 | `scripts/build_index.py`, `public/index.html` | 없음 | `public/briefings/<id>/<날짜>.json` → GitHub Pages |

## 처음 설정 (한 번만)

1. GitHub에 공개 저장소 `kain9012-bit/jbe-daily-briefings` 생성 후 이 폴더 올리기
   ```powershell
   cd C:\work\jbe-daily-briefings
   git init -b main
   git add .
   git commit -m "init: morning briefing pipeline"
   git remote add origin https://github.com/kain9012-bit/jbe-daily-briefings.git
   git push -u origin main
   ```
2. 저장소 Settings → Secrets and variables → Actions
   - Secrets: `OPENROUTER_API_KEY` (부서 키)
   - Variables(선택): `LLM_MODEL` — 비우면 `google/gemini-3.5-flash`
3. Settings → Pages → Source: **GitHub Actions**
4. Actions → Morning briefing → Run workflow 로 첫 실행 확인
5. 정시 발행용 외부 스케줄러(cron-job.org) — GitHub 예약 실행은 몇 시간씩 밀림
   - 토큰: GitHub → Settings → Developer settings → Fine-grained token, 이 저장소만, 권한 `Actions: Read and write`
   - cron-job.org 작업: 평일 07:20 (Asia/Seoul), `POST https://api.github.com/repos/kain9012-bit/jbe-daily-briefings/actions/workflows/briefing.yml/dispatches`
   - 헤더: `Authorization: Bearer <토큰>`, `Accept: application/vnd.github+json`
   - 본문: `{"ref":"main","inputs":{"briefing":"edu_policy","force":"false"}}`

예약 실행(07:10)도 보조로 걸려 있음. 그날 파일이 이미 있으면 아무것도 안 함.

## 내 PC에서 시험

```powershell
pip install -r requirements.txt
python scripts/collect.py --briefing edu_policy
$env:OPENROUTER_API_KEY = "..."
python scripts/write_briefing.py --briefing edu_policy
python scripts/build_index.py
python -m http.server -d public 8000   # http://localhost:8000
```

## 고치는 곳

- 검색어·제외 제목·건수: `briefings/edu_policy.json`
- 선정·작성 지시문: `scripts/write_briefing.py` 의 `select()`, `write()`, `STYLE`
- 카톡 글 모양(이모지·순서): `scripts/render.py`

## 한계

- 기사 후보는 구글·빙 뉴스 검색어에 걸린 것만. 빠지는 주제는 검색어 추가로 대응
- 사안 묶기는 제목 낱말 겹침 기준이라 같은 사안이 둘로 나뉘거나 다른 사안이 묶일 수 있음(선정 단계 AI가 한 번 더 거름)
- 숫자 검증은 원자료에 그 숫자가 있는지까지만. 숫자의 뜻을 바르게 옮겼는지는 사람이 확인
- 본문은 robots.txt 허용 언론사만 가져오므로, 나머지는 제목·검색 요약문만으로 작성
