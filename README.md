# 아침 브리핑 (jbe-daily-briefings)

GPT 예약으로 받던 아침 브리핑을 옮긴 자체 파이프라인. 평일 아침 자료 수집 → AI 선정·작성 → 검증 → 웹페이지 발행. 웹페이지에서 그날 브리핑을 **카톡용 복사** 버튼 하나로 복사.

| 브리핑 | 설정 | 건수 | 자료 | 글 모양 |
|---|---|---|---|---|
| 교육정책 동향 | `briefings/edu_policy.json` | 최대 5건 | 언론 기사 | 이모지 형식, 원문 링크 포함 |
| AI·데이터 | `briefings/ai_data.json` | 최대 5건 | 공식 보도자료 우선 + 언론 기사 | 교육정책 동향과 같은 형식(설정 `format`) |

글 형식은 설정의 `format`으로 고른다. `edu_policy`(이모지 형식) 또는 `ai_data`(일반 텍스트: 제목·기관·게시일·핵심내용·시사점).

한 번 실행(`scripts/run_all.py`)에 두 브리핑을 차례로 만든다. 하나가 실패해도 다른 하나는 발행.

## 흐름

| 단계 | 파일 | AI 사용 | 내용 |
|---|---|---|---|
| 수집 | `scripts/collect.py` | 없음 | 언론 기사: 구글·빙 뉴스 RSS, 직전 영업일 07:00 ~ 오늘 07:00(월요일 72시간, 연휴 뒤 그만큼 더. 마감 시각은 설정 `cutoff_hour`). 공식 보도자료: 정책브리핑(korea.kr) 목록, 교육부·시도교육청(edu-news-alert 공개 news.json) — 날짜만 있어 직전 영업일 ~ 어제 날짜분. 제외 규칙, 중복 제거, 같은 사안 묶기 |
| 선정 | `scripts/write_briefing.py` | OpenRouter | 사안 목록(제목·보도량·공식 여부)에서 후보 12개 |
| 보강 | 〃 | 없음 | robots.txt 허용 언론사만 본문 발췌(근거용, 공개 안 함). 구글 중계 주소뿐인 사안은 같은 제목을 빙에서 찾아 원문 주소 확보 |
| 작성 | 〃 | OpenRouter | 내용만 JSON으로. **주소·제목·언론사·기관·날짜는 AI가 쓰지 않고 코드가 수집 자료에서 넣음** |
| 검증 | `scripts/verify.py` | 없음 | 건수·형식, 마크다운·이모지, 수집한 주소인지, 글 속 숫자가 원자료에 있는지, 기관명이 원자료에 있는지. 실패 시 오류를 알려 주고 재작성(최대 3회), 그래도 실패하면 발행 안 함 |
| 발행 | `scripts/build_index.py`, `public/index.html` | 없음 | `public/briefings/<id>/<날짜>.json` → GitHub Pages. 원문 목록은 화면에만(복사 내용에는 안 들어감) |

## 처음 설정 (한 번만)

1. 저장소 Settings → Secrets and variables → Actions
   - Secrets: `OPENROUTER_API_KEY` (부서 키)
   - Variables(선택): `LLM_MODEL` — 비우면 `google/gemini-3.5-flash`
2. Settings → Pages → Source: **GitHub Actions**
3. Actions → Morning briefing → Run workflow 로 실행 확인 (briefing `all`, force `true`면 오늘 것 다시 만듦)
4. 정시 발행용 외부 스케줄러(cron-job.org) **작업 하나** — GitHub 예약 실행은 몇 시간씩 밀림
   - 토큰: GitHub → Settings → Developer settings → Fine-grained token, 이 저장소만, 권한 `Actions: Read and write`
   - cron-job.org 작업: 평일 07:05 (Asia/Seoul), `POST https://api.github.com/repos/kain9012-bit/jbe-daily-briefings/actions/workflows/briefing.yml/dispatches`
   - 헤더: `Authorization: Bearer <토큰>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`
   - 본문: `{"ref":"main","inputs":{"briefing":"all","force":"false"}}`
   - 성공 응답: 204

예약 실행(07:15)도 보조로 걸려 있음. 그날 파일이 이미 있으면 아무것도 안 함. 휴일이면 만들지 않음.

## 내 PC에서 시험

```powershell
pip install -r requirements.txt
$env:OPENROUTER_API_KEY = "..."
python scripts/run_all.py --force              # 두 브리핑 모두
python scripts/run_all.py --only ai_data --force
python -m http.server -d public 8000           # http://localhost:8000
```

## 고치는 곳

- 검색어·제외 제목·건수·선정 기준: `briefings/*.json` (`select_rules`, `sources`, `exclude_title_patterns`)
- AI·데이터 공식 출처 기관·주제어: `briefings/ai_data.json` 의 `sources.official`
- 작성 지시문: `scripts/write_briefing.py` 의 `edu_policy_instructions()`, `ai_data_instructions()`, `STYLE`
- 글 모양: `scripts/render.py`

## 한계

- 언론 기사 후보는 구글·빙 뉴스 검색어에 걸린 것만. 빠지는 주제는 검색어 추가로 대응
- NIA·KISA·KERIS·디지털플랫폼정부위원회 자체 게시판은 직접 수집하지 않음(정책브리핑·언론 기사로만 포착)
- 정책브리핑이 해외(GitHub Actions) 접속을 막으면 그 출처만 빠짐 — 화면 수집 통계 `sources`에서 확인
- 사안 묶기는 제목 낱말 겹침 기준이라 같은 사안이 둘로 나뉘거나 다른 사안이 묶일 수 있음(선정 단계 AI가 한 번 더 거름)
- 숫자 검증은 원자료에 그 숫자가 있는지까지만. 숫자의 뜻을 바르게 옮겼는지는 사람이 확인
- 본문은 robots.txt 허용 언론사만 가져오므로, 나머지는 제목·검색 요약문만으로 작성
