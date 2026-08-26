# weekly-report-mcp

주차별 일정 엑셀에서 주간보고서 작성용 데이터를 제공하는 개인용 MCP 서버.
Claude Code / Codex CLI 양쪽에 연결해서 쓴다.

## 설계 원칙

> MCP 서버에 보고서 문장 생성을 넣지 않는다.

| 영역 | 담당 | 내용 |
|---|---|---|
| 결정론적 영역 | MCP 서버 | 엑셀 읽기, 상태 정규화, 집계, 지연/이월 판정 |
| 서술 영역 | LLM | 집계 결과를 받아 보고 문장 작성 |

수치 계산을 LLM에 맡기면 주간보고에 틀린 숫자가 들어간다. 계산은 코드가, 문장은 모델이 한다.

## 구성

| 파일 | 역할 |
|---|---|
| `schedule_reader.py` | 엑셀 리더 / 상태 정규화 / 집계 (순수 Python, MCP 무관) |
| `weekly_report_server.py` | MCP 서버 — 위 모듈을 툴로 노출하는 얇은 래퍼 |
| `test_client.py` | stdio 프로토콜 레벨 서버 검증 |
| `verify_config.py` | 클라이언트 설정 파일(.mcp.json / config.toml) 검증 |
| `PROJECT_PLAN.md` | 진행 계획 및 검증 기록 |

## MCP 툴

| 툴 | 용도 |
|---|---|
| `list_weeks()` | 주차 목록 + 기간/건수 |
| `get_schedule(week)` | 정규화된 업무 목록 전체 (근거 확인용) |
| `summarize_week(week)` | 집계 — 보고서 수치의 유일한 출처 |
| `trace_task(keyword)` | 주차를 넘나드는 업무 추적 |

## 입력 데이터 형식

시트 하나가 한 주차(`W31`, `W32`, ...). ISO 주차 번호를 시트명으로 쓴다.

| 업무명 | 시작일 | 마감일 | 상태 | 비고 |
|---|---|---|---|---|
| 펌웨어 로그 파서 개선 | 2026-07-27 | 2026-07-31 | 완료 | |
| 테스트 케이스 리팩토링 | 2026-07-27 | 2026-08-10 | 지연 | 리소스 부족 |

상태는 `완료 / 진행중 / 지연 / 보류` 4종으로 정규화된다. `'완료 '`(뒤 공백),
`'진행 중'`(중간 공백) 같은 표기 흔들림은 자동 흡수되고, 어떤 행이 보정됐는지
`normalized_rows`로 추적된다. 미지의 값은 버리지 않고 `기타`로 보존한다.

## 설치

```bash
pip install openpyxl "mcp[cli]"
```

MCP SDK 2.x 기준이다. `FastMCP`가 `MCPServer`로 이름이 바뀌었으므로
v1 예제 코드(`from mcp.server.fastmcp import FastMCP`)는 동작하지 않는다.

## 사용법

엑셀 경로는 `SCHEDULE_XLSX` 환경변수로 지정한다.

```bash
# CLI로 직접 확인
python schedule_reader.py              # 전체 주차 요약
python schedule_reader.py W31          # 특정 주차
python schedule_reader.py W31 --rows   # 정규화된 원본 행
python schedule_reader.py W31 --json   # JSON

# 서버 자기점검
python weekly_report_server.py --check

# 검증
python test_client.py      # 서버 (stdio 프로토콜)
python verify_config.py    # 클라이언트 설정 파일
```

## 클라이언트 연결

**절대 경로를 쓴다.** 클라이언트가 프로젝트 밖 작업 디렉토리에서 서버를 띄운다.

**Claude Code** — 프로젝트 루트에 `.mcp.json`

```json
{
  "mcpServers": {
    "weekly-report": {
      "command": "<python 절대경로>",
      "args": ["<weekly_report_server.py 절대경로>"],
      "env": {
        "SCHEDULE_XLSX": "<엑셀 절대경로>",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

프로젝트 스코프 `.mcp.json`은 최초 1회 승인이 필요하다.
`claude mcp list`가 `Pending approval`이면 해당 디렉토리에서 `claude`를 실행해 승인한다.

**Codex CLI** — `~/.codex/config.toml` (JSON 아님)

```toml
[mcp_servers.weekly_report]
command = '<python 절대경로>'
args = ['<weekly_report_server.py 절대경로>']
startup_timeout_sec = 60

[mcp_servers.weekly_report.env]
SCHEDULE_XLSX = '<엑셀 절대경로>'
PYTHONIOENCODING = 'utf-8'
PYTHONUTF8 = '1'
```

기존 `config.toml`에 다른 설정이 있으면 **덮어쓰지 말고 추가**한다.
TOML 키에는 하이픈 대신 밑줄(`weekly_report`)을 쓰는 게 편하다.
Windows에서는 한글 깨짐 방지로 `PYTHONUTF8`을 지정한다.

## 진행 상황

- [x] Phase 1 — 리더/정규화 모듈
- [x] Phase 2 — MCP 서버화
- [x] Phase 3 — 클라이언트 연결
- [ ] Phase 4 — 리포트 파일 생성 (`build_report`)
- [ ] Phase 5 — 보고서 골격 프롬프트 고정
- [ ] Phase 6 — 최종 교차 검증

상세 계획과 검증 기록은 `PROJECT_PLAN.md` 참고.

## 알려진 제약

- **연도 추론** — 파일에 연도 표기가 없어 시트 내 날짜의 최소 연도로 ISO 주차를
  계산한다. 여러 연도가 섞이면 깨진다. 그 경우 시트명을 `2026-W31` 형식으로 바꿀 것.
- **`지연` 자동 판정 안 함** — 엑셀의 상태 컬럼을 신뢰한다. 마감일만으로 판정하면
  사람이 적은 `보류`나 비고의 맥락을 무시한다. 대신 `overdue_days`, `beyond_week`를
  별도 필드로 제공해 모델이 근거로 쓰게 한다.
- **시트 간 업무 연속성 없음** — 이월 업무가 다음 주 시트에 나타나지 않는다.
  `trace_task`로 우회하지만 업무명이 주마다 조금씩 달라져(`룰셋 업데이트` →
  `룰셋 재검토`) 키워드 매칭에 의존한다. 동일 업무 여부 판단은 사람/모델의 몫.
