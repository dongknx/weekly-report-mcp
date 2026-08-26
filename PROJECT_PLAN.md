# 주간일정 보고서 MCP 프로젝트 계획

- **작성일**: 2026-08-26
- **작업 디렉토리**: `D:\train\day3\mcp_test`
- **데이터 원본**: `C:\Users\20224\Desktop\202608_sec_gumi\mx-agentic-ai-day1-prd\sample-data\일정_샘플.xlsx`
- **목표**: 주차별 일정 엑셀을 읽어 주간보고서를 생성하는 개인용 MCP 서버 구축 (Claude Code / Codex 양쪽 연동)

---

## 1. 데이터 실태 분석 (실측)

### 구조

- **시트 3개 = 주차별**: `W31`, `W32`, `W33` (ISO 주차)
- **컬럼 5개** (3개 시트 모두 동일 스키마): `업무명 | 시작일 | 마감일 | 상태 | 비고`
- **총 33건** (W31: 11건, W32: 12건, W33: 10건)
- 시작일/마감일은 엑셀 날짜 타입 (`datetime`)으로 저장됨

### 상태값 분포

| 값 | 건수 | 비고 |
|---|---|---|
| `완료` | 14 | |
| `진행중` | 10 | |
| `지연` | 6 | |
| `완료 ` | 1 | 뒤쪽 공백 — 정규화 대상 |
| `진행 중` | 1 | 중간 공백 — 정규화 대상 |
| `보류` | 1 | |

### 발견된 데이터 품질 이슈

1. **표기 불일치가 의도적으로 삽입되어 있음.** W31 5행 `사내 API 문서 정리`의 비고가 `표기 오탈자 예시`로 명시됨.
   → 상태 정규화 로직은 이 프로젝트의 **필수 요구사항**.
2. **주차를 넘어가는 업무 다수.** 예: `테스트 케이스 리팩토링` (2026-07-27 ~ 2026-08-10) — W31 시트에 있으나 마감일은 W33 구간.
   → "차주 이월" 판정 로직 필요.
3. **비고 컬럼에 지연 사유가 들어있음.** (`리소스 부족`, `담당자 휴가`, `우선순위 재조정 중` 등)
   → 리스크 서술의 근거로 활용.

---

## 2. 핵심 설계 원칙

> **MCP 서버에 보고서 문장 생성을 넣지 않는다.**

| 영역 | 담당 | 내용 |
|---|---|---|
| 결정론적 영역 | **MCP 서버** | 엑셀 읽기, 상태 정규화, 집계, 지연/이월 판정. 같은 입력 → 항상 같은 숫자 |
| 서술 영역 | **LLM** (Claude/Codex) | 집계 결과를 받아 보고 문장 작성 |

**이유**: 숫자 집계를 LLM에 맡기면 주간보고에 틀린 수치가 들어간다. 계산은 코드가, 문장은 모델이 담당한다.

---

## 3. MCP 툴 설계 (4개)

| 툴 | 입력 | 출력 |
|---|---|---|
| `list_weeks()` | - | 주차 목록 + 각 주 기간/건수 |
| `get_schedule(week)` | 주차명 | 정규화된 원본 행 (검증·근거 확인용) |
| `summarize_week(week)` | 주차명 | 집계 결과 (아래 상세) |
| `build_report(week, format)` | 주차명, 포맷 | 산출물 파일 생성 후 경로 반환 (Phase 4) |
| `trace_task(keyword)` | 업무명 키워드 | 주차를 넘나드는 업무 추적 (추가) |

### `summarize_week` 출력 항목 (이 서버의 핵심)

- 상태별 카운트 + 완료율
- **지연 업무 상세**: 업무명 / 마감일 / 지연일수 / 비고(사유)
- **차주 이월 업무**: 마감일이 해당 주 종료일 초과
- **신규 착수 업무**: 시작일이 해당 주 구간 내

---

## 4. 진행 프로세스

### Phase 0 — 환경 준비

```bash
pip install openpyxl "mcp[cli]"
```

- Python 3.11.9 확인됨, `openpyxl` 기설치됨
- 엑셀 경로는 **하드코딩 금지** → `SCHEDULE_XLSX` 환경변수로 주입
  (경로에 한글과 공백이 포함되어 있어 config 레벨에서 다루는 것이 안전)

### Phase 1 — 리더/정규화 모듈 (MCP 없이 순수 Python)

**산출물**: `schedule_reader.py`

- 상태 정규화: 공백 제거 → `완료 / 진행중 / 지연 / 보류` 4종 매핑
  - 미지의 값은 **버리지 말고** `기타`로 보존 (데이터 유실 방지)
- 날짜 파싱 및 주차 기간 계산
- **검증 방법**: MCP를 씌우기 전에 CLI로 먼저 실행해 결과 확인
  (MCP 래핑 후 디버깅은 난이도가 크게 올라감)

### Phase 2 — MCP 서버화 (완료)

**산출물**: `weekly_report_server.py`, `test_client.py`

- **주의: MCP SDK 2.x에서 `FastMCP`가 `MCPServer`로 이름이 바뀌었다.**
  참고 repo(kyopark2014/mcp)의 `from mcp.server.fastmcp import FastMCP`는 v1 문법이라 동작하지 않는다.
  현재 설치 버전은 `mcp 2.1.1` → `from mcp.server.mcpserver import MCPServer`
  (v1 코드를 그대로 쓰려면 `pip install "mcp<2"`)
- 클라이언트 SDK도 snake_case로 변경: `serverInfo` → `server_info`, `isError` → `is_error`
- `transport="stdio"`
- docstring을 성실히 작성 — LLM이 툴을 선택하는 근거는 docstring이다
- 서버는 얇은 래퍼로 유지. 집계/판정 로직은 전부 `schedule_reader`에 둔다
- 예외를 `ToolError`로 변환(`_guard`). 그대로 두면 클라이언트에
  `Error executing tool ...`만 전달되어 모델이 복구할 수 없다
- `sys.path`에 서버 디렉토리를 직접 추가 — 클라이언트가 임의의 cwd에서 실행하므로 필요

### Phase 3 — 클라이언트 연결 (완료)

**산출물**: `.mcp.json`, `~/.codex/config.toml` 추가분, `verify_config.py`

서버 코드는 공유하고 설정 파일만 두 벌 작성한다. **절대 경로를 사용한다** —
클라이언트가 프로젝트 밖 작업 디렉토리에서 서버를 띄우기 때문.

**Claude Code** — `.mcp.json` (프로젝트 루트, 실제 적용본)

```json
{
  "mcpServers": {
    "weekly-report": {
      "command": "D:\python\python.exe",
      "args": ["D:\train\day3\mcp_test\weekly_report_server.py"],
      "env": {
        "SCHEDULE_XLSX": "C:\Users\20224\Desktop\202608_sec_gumi\mx-agentic-ai-day1-prd\sample-data\일정_샘플.xlsx",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

**Codex CLI** — `~/.codex/config.toml` (JSON이 아닌 TOML)

```toml
[mcp_servers.weekly_report]
command = 'D:\python\python.exe'
args = ['D:\train\day3\mcp_test\weekly_report_server.py']
startup_timeout_sec = 60

[mcp_servers.weekly_report.env]
SCHEDULE_XLSX = 'C:\Users\20224\Desktop\202608_sec_gumi\mx-agentic-ai-day1-prd\sample-data\일정_샘플.xlsx'
PYTHONIOENCODING = 'utf-8'
PYTHONUTF8 = '1'
```

**주의사항**

- Codex 설정에는 이미 `[mcp_servers.node_repl]`, `plugins` 8개, `projects`가 있었다.
  **덮어쓰지 말고 추가**해야 한다. 백업: `~/.codex/config.toml.bak-20260826`
- TOML 키에 하이픈을 쓰려면 따옴표가 필요하므로 `weekly_report`(밑줄)를 사용했다.
  Claude Code 쪽은 JSON이라 `weekly-report`(하이픈) 그대로 가능.
- TOML은 작은따옴표 literal string이라 백슬래시를 이스케이프하지 않아도 된다.
  JSON은 `\`로 이스케이프 필수.
- Windows 한글 출력 깨짐 방지로 `PYTHONIOENCODING`, `PYTHONUTF8`을 env에 지정.
- Claude Code는 프로젝트 `.mcp.json`을 **최초 1회 승인**해야 활성화된다.
  `claude mcp list`가 `Pending approval`이면 `claude`를 실행해 승인할 것.

### Phase 4 — 리포트 생성

- **Markdown부터 시작** (권장): 눈으로 즉시 검증 가능
- 내용이 확정된 후 xlsx/docx로 확장
- 원본 엑셀은 **읽기 전용**으로만 접근, 절대 수정하지 않음

### Phase 5 — 프롬프트 고정

자유 프롬프트로 매번 요청하면 주마다 보고서 형식이 달라진다.
보고서 골격을 Claude Code 스킬 또는 슬래시 커맨드로 고정한다.

**보고서 골격**:

1. 요약
2. 완료 업무
3. 진행 업무
4. 지연 / 리스크
5. 차주 계획

### Phase 6 — 검증

- W31로 생성한 리포트의 숫자를 `get_schedule` 원본과 교차 확인
- **중점 확인**: `완료 ` / `진행 중` 2건이 정상 흡수되어, 전체 완료가 **15건**으로 집계되는지
- 주차 경계를 넘는 업무가 이월 목록에 정확히 잡히는지

---

## 5. 미결정 사항

- **최종 산출물 형태**: 팀 배포용 문서(docx/xlsx) vs 본인 검토용(Markdown)
  → Phase 4 설계가 이에 따라 달라짐

---

## 6. 진행 현황

- [x] Phase 0 — 환경 준비 (Python 3.11.9 / openpyxl / mcp 2.1.1)
- [x] Phase 1 — 리더/정규화 모듈 (`schedule_reader.py`, CLI 검증 통과)
- [x] Phase 2 — MCP 서버화 (`weekly_report_server.py`, stdio 프로토콜 검증 통과)
- [x] Phase 3 — 클라이언트 연결 (양쪽 config 검증 통과 / Claude Code 승인 대기)
- [ ] Phase 4 — 리포트 생성
- [ ] Phase 5 — 프롬프트 고정
- [ ] Phase 6 — 검증

---

## 7. 검증 기록

### Phase 1 (CLI)

| 항목 | 기대 | 실측 |
|---|---|---|
| 전체 완료 (정규화 후) | 15 | 15 (5+5+5) OK |
| 전체 진행중 | 11 | 11 (3+5+3) OK |
| 지연 / 보류 / 총계 | 6 / 1 / 33 | 6 / 1 / 33 OK |

정규화 보정 2건 모두 추적됨:

- W31 6행 `사내 API 문서 정리`: `'완료 '` -> `완료`
- W32 10행 `빌드 알림 슬랙 연동`: `'진행 중'` -> `진행중`

주차 구간 교차검증: 시트명 `W31` -> ISO 31주차 -> `2026-07-27 ~ 2026-08-02`,
시트 내 최소 시작일 `2026-07-27`과 일치. 3개 시트 모두 일치.

### Phase 2 (stdio 프로토콜)

`python test_client.py` — 일부러 다른 cwd(홈 디렉토리)에서 서버를 띄워 경로 의존성까지 검증.

- 핸드셰이크 OK (`weekly-report`)
- 툴 4개 등록 확인
- `summarize_week("W31")` 결과가 Phase 1 CLI 값과 동일
- `trace_task("정적 분석")` — 3개 주차에 걸친 유사 업무 추적 확인
- `summarize_week("W99")` -> `주차 없음: 'W99' (가능: W31, W32, W33)` 로 복구 가능한 에러 반환

### 알려진 제약

- **연도 추론**: 파일에 연도 표기가 없어 시트 내 날짜의 최소 연도로 ISO 주차를 계산한다.
  여러 연도가 섞이면 깨진다. 그 경우 시트명을 `2026-W31` 형식으로 변경할 것.
- **`지연` 자동 판정 안 함**: 엑셀의 상태 컬럼을 신뢰한다. 마감일만으로 판정하면
  사람이 적은 `보류`나 비고의 맥락을 무시하게 된다. 대신 `overdue_days`,
  `beyond_week`를 별도 필드로 제공해 모델이 근거로 쓰게 한다.
- **`new_tasks` 무의미**: 33건 전부 시작일이 자기 주차 안에 있어 항상 전체 건수와 같다.
  Phase 4 보고서에서는 제외 권장.
- **시트 간 업무 연속성 없음**: W31의 이월 업무가 W32 시트에 나타나지 않는다.
  `trace_task`로 우회하지만, 업무명이 주마다 조금씩 달라서(`룰셋 업데이트` ->
  `룰셋 재검토`) 키워드 매칭에 의존한다.

### Phase 3 (설정 파일)

`python verify_config.py` — 설정 파일에 적힌 command/args/env 그대로 서버를 띄워
검증한다. `test_client.py`가 서버를 검증하는 반면 이쪽은 **설정 파일 자체**를
검증한다. 경로 오타나 env 누락은 여기서 잡힌다.

| 대상 | 정적 검사 | 핸드셰이크 | 기준값 대조 |
|---|---|---|---|
| Claude Code `.mcp.json` | PASS | 툴 4개 | 11건 / 완료 5 일치 |
| Codex `config.toml` | PASS | 툴 4개 | 11건 / 완료 5 일치 |

TOML 재파싱으로 기존 설정 보존 확인: `node_repl` 유지, plugins 8개 유지, projects 유지.

`claude mcp list` 결과:

```
weekly-report: D:\python\python.exe D:\train\day3\mcp_test\weekly_report_server.py
  - Pending approval (run `claude` to approve)
```

프로젝트 스코프 `.mcp.json`은 보안상 최초 1회 사용자 승인이 필요하다. 정상 동작이다.
