"""Phase 6 - 통합 회귀 검증.

test_client.py(서버) / verify_config.py(설정)를 포함해 지금까지의 검증 기준을
한 번에 돌린다. 데이터나 코드를 고친 뒤 이 스크립트만 통과하면 회귀가 없다.

    python verify_all.py

기준값은 원본 엑셀(일정_샘플.xlsx, 33건/3주차)에 맞춰 하드코딩되어 있다.
다른 엑셀을 쓰면 EXPECT를 갱신할 것.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import report_builder as rb
import schedule_reader as sr

HERE = os.path.dirname(os.path.abspath(__file__))

# 원본 엑셀 기준값. Phase 1에서 실측해 확정했다.
EXPECT = {
    "weeks": ["W31", "W32", "W33"],
    "total": 33,
    "status_totals": {"완료": 15, "진행중": 11, "지연": 6, "보류": 1, "기타": 0},
    "normalized_count": 2,          # '완료 ', '진행 중'
    "per_week": {
        # 주차: (기간시작, 총건수, 주내마감, 주내완료, 완료율_주내, 완료율_전체, 이월)
        "W31": ("2026-07-27", 11, 6, 5, 83.3, 45.5, 5),
        "W32": ("2026-08-03", 12, 7, 5, 71.4, 41.7, 5),
        # W33은 is_carryover 4건이지만 그중 1건('펌웨어 릴리즈 노트 자동 생성',
        # 마감 08/17)이 이미 완료라 이월 목록에서는 제외된다.
        "W33": ("2026-08-10", 10, 6, 4, 66.7, 50.0, 3),
    },
    "tools": ["list_weeks", "get_schedule", "summarize_week", "trace_task",
              "build_report", "preview_report", "check_summary"],
}

results: list[tuple[str, str, bool, str]] = []


def check(group: str, name: str, ok: bool, detail: str = "") -> bool:
    results.append((group, name, ok, detail))
    return ok


# --- 1. 데이터 무결성 -------------------------------------------------------

def check_data() -> None:
    g = "1. 데이터"
    weeks = sr.load_weeks()
    check(g, "주차 목록", list(weeks) == EXPECT["weeks"], str(list(weeks)))

    total = sum(len(w.tasks) for w in weeks.values())
    check(g, "전체 업무 건수", total == EXPECT["total"],
          f"{total} (기대 {EXPECT['total']})")

    agg = {k: 0 for k in EXPECT["status_totals"]}
    norm = 0
    for w in weeks.values():
        for t in w.tasks:
            agg[t.status] += 1
            norm += 1 if t.normalized else 0
    check(g, "상태별 합계", agg == EXPECT["status_totals"], str(agg))
    check(g, "정규화 보정 건수", norm == EXPECT["normalized_count"],
          f"{norm}건 (기대 {EXPECT['normalized_count']})")

    # 정규화가 실제로 표기 흔들림을 흡수했는지 (원본 표기와 결과가 다른 행)
    fixed = [(w.name, t.row, t.status_raw, t.status)
             for w in weeks.values() for t in w.tasks if t.normalized]
    ok = all(raw.strip().replace(" ", "") == to for _, _, raw, to in fixed)
    check(g, "보정 방향 타당성", ok,
          "; ".join(f"{s} {r}행 {raw!r}->{to}" for s, r, raw, to in fixed))

    # 시트명 ISO 주차와 실제 날짜가 서로를 검증하는지
    bad = []
    for w in weeks.values():
        starts = [t.start for t in w.tasks if t.start]
        if starts and min(starts) != w.start:
            bad.append(f"{w.name}: 시트 {w.start} vs 최소시작 {min(starts)}")
    check(g, "주차 구간 교차검증", not bad, "; ".join(bad) or "3개 시트 일치")


# --- 2. 집계 지표 -----------------------------------------------------------

def check_metrics() -> None:
    g = "2. 지표"
    rates = []
    for wk, exp in EXPECT["per_week"].items():
        start, total, due, due_done, rate_due, rate_all, carry = exp
        s = sr.summarize(wk)
        ok = (s["period"]["start"] == start
              and s["total"] == total
              and s["due_in_week"] == due
              and s["done_in_week"] == due_done
              and s["completion_rate_due"] == rate_due
              and s["completion_rate"] == rate_all
              and len(s["carryover"]) == carry)
        check(g, f"{wk} 집계", ok,
              f"{s['period']['start']} / {s['total']}건 / 주내 "
              f"{s['done_in_week']}/{s['due_in_week']} = "
              f"{s['completion_rate_due']}% / 이월 {len(s['carryover'])}")
        rates.append(s["completion_rate_due"])

    # 두 지표가 실제로 다른 결론을 주는지 (지표 분리의 근거)
    alls = [sr.summarize(w)["completion_rate"] for w in EXPECT["per_week"]]
    monotonic_due = all(rates[i] > rates[i + 1] for i in range(len(rates) - 1))
    monotonic_all = all(alls[i] > alls[i + 1] for i in range(len(alls) - 1))
    check(g, "주내 기준 완료율 3주 연속 하락", monotonic_due,
          " -> ".join(f"{r}%" for r in rates))
    check(g, "전체 기준은 단조 추세 아님 (지표 분리 근거)", not monotonic_all,
          " -> ".join(f"{r}%" for r in alls))

    # 완료된 업무는 마감일이 주 이후라도 이월 목록에서 빠져야 한다.
    for wk in EXPECT["per_week"]:
        w = sr.get_week(wk)
        s = sr.summarize(wk)
        beyond_done = [t.name for t in w.tasks
                       if t.is_carryover and t.status == sr.STATUS_DONE]
        names = {c["name"] for c in s["carryover"]}
        check(g, f"{wk} 완료 업무는 이월 제외",
              not (set(beyond_done) & names),
              f"마감 주 이후 {len([t for t in w.tasks if t.is_carryover])}건 중 "
              f"완료 {len(beyond_done)}건 제외 -> 이월 {len(names)}건")

    # 지연/보류가 섞이지 않는지
    for wk in EXPECT["per_week"]:
        s = sr.summarize(wk)
        names_d = {d["name"] for d in s["delayed"]}
        names_h = {h["name"] for h in s["on_hold"]}
        check(g, f"{wk} 지연/보류 분리", not (names_d & names_h),
              f"지연 {len(names_d)} / 보류 {len(names_h)}")


# --- 3. 보고서 산출물 -------------------------------------------------------

def check_report() -> None:
    g = "3. 보고서"
    for wk in EXPECT["per_week"]:
        s = sr.summarize(wk)
        body = rb.render(wk, summary="검증용 요약 문장.")

        # 본문 숫자가 집계와 일치하는지 (md를 파싱해서 대조)
        pairs = {
            "금주 마감": s["due_in_week"],
            "그중 완료": s["done_in_week"],
            "차주 이월": len(s["carryover"]),
        }
        ok = all(f"| {k} 업무 | {v}건 |" in body or f"| {k} | {v}건 |" in body
                 for k, v in pairs.items())
        ok = ok and f"**{s['completion_rate_due']}%**" in body
        check(g, f"{wk} 지표표 일치", ok, str(pairs))

        # 섹션 건수가 집계와 일치하는지
        def sec(title: str) -> int | None:
            m = re.search(rf"## {title} \((\d+)건\)", body)
            return int(m.group(1)) if m else None

        risks = len(s["delayed"]) + len(s["on_hold"])
        counts = {
            "완료": s["status_counts"]["완료"],
            "진행 중": s["status_counts"]["진행중"],
            "지연 / 리스크": risks,
            "차주 이월": len(s["carryover"]),
        }
        bad = [f"{k}: md {sec(k)} vs 집계 {v}"
               for k, v in counts.items() if sec(k) != v]
        check(g, f"{wk} 섹션 건수 일치", not bad, "; ".join(bad) or str(counts))

        # 전체 기준 완료율이 본문에 새어나오지 않았는지
        leaked = f"{s['completion_rate']}%" in body and \
            s["completion_rate"] != s["completion_rate_due"]
        check(g, f"{wk} 왜곡 지표 미노출", not leaked,
              f"completion_rate {s['completion_rate']}% 본문 부재")

        # 요약 주입 및 자리표시자 제거
        check(g, f"{wk} 요약 주입", "검증용 요약 문장." in body
              and "요약 미작성" not in body)

    # 같은 입력이면 같은 출력 (생성 시각 등 비결정 요소가 없는지)
    a = rb.render("W31", summary="X")
    b = rb.render("W31", summary="X")
    check(g, "렌더링 결정론", a == b, "2회 렌더 결과 동일")


# --- 4. MCP 프로토콜 --------------------------------------------------------

async def check_protocol() -> None:
    g = "4. 프로토콜"
    params = StdioServerParameters(
        command=sys.executable,
        args=[os.path.join(HERE, "weekly_report_server.py")],
        cwd=os.path.expanduser("~"),   # 프로젝트 밖에서 기동
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )

    def txt(r) -> str:
        return "\n".join(c.text for c in r.content if getattr(c, "text", None))

    async with stdio_client(params) as (rd, wr):
        async with ClientSession(rd, wr) as s:
            init = await s.initialize()
            check(g, "핸드셰이크", init.server_info.name == "weekly-report",
                  init.server_info.name)

            tools = await s.list_tools()
            names = [t.name for t in tools.tools]
            check(g, "툴 등록", sorted(names) == sorted(EXPECT["tools"]),
                  f"{len(names)}개: {', '.join(names)}")

            # 모든 툴에 docstring이 있는지 (모델이 툴을 고르는 근거)
            nodoc = [t.name for t in tools.tools
                     if not (t.description or "").strip()]
            check(g, "툴 설명 존재", not nodoc,
                  "; ".join(nodoc) or f"{len(tools.tools)}개 모두 보유")

            r = await s.call_tool("summarize_week", {"week": "W31"})
            d = json.loads(txt(r))
            check(g, "summarize_week 결과 = 로컬 집계",
                  d == sr.summarize("W31"), "동일")

            r = await s.call_tool("preview_report",
                                  {"week": "W31", "summary": "요약."})
            check(g, "preview_report 저장 안 함",
                  "주간업무보고" in txt(r), f"{len(txt(r))}자 반환")

            r = await s.call_tool("build_report",
                                  {"week": "W33", "summary": "요약."})
            out = json.loads(txt(r))
            check(g, "build_report 파일 생성", os.path.exists(out["path"]),
                  out["path"])

            for bad_args, label in [({"week": "W99"}, "없는 주차"),
                                    ({"week": ""}, "빈 주차")]:
                r = await s.call_tool("summarize_week", bad_args)
                msg = txt(r)
                actionable = r.is_error and ("가능:" in msg or "찾을 수 없" in msg)
                check(g, f"에러 복구성 ({label})", actionable, msg[:70])


# --- 5. 클라이언트 설정 -----------------------------------------------------

def check_configs() -> None:
    g = "5. 설정"
    import tomllib

    mcp_json = os.path.join(HERE, ".mcp.json")
    if os.path.exists(mcp_json):
        with open(mcp_json, encoding="utf-8") as f:
            e = json.load(f)["mcpServers"]["weekly-report"]
        ok = (os.path.exists(e["command"])
              and all(os.path.exists(a) for a in e["args"] if a.endswith(".py"))
              and os.path.exists(e["env"]["SCHEDULE_XLSX"]))
        check(g, "Claude Code .mcp.json 경로", ok, e["command"])
    else:
        check(g, "Claude Code .mcp.json 존재", False, "파일 없음")

    toml_path = os.path.expanduser("~/.codex/config.toml")
    if os.path.exists(toml_path):
        with open(toml_path, "rb") as f:
            cfg = tomllib.load(f)
        e = cfg.get("mcp_servers", {}).get("weekly_report")
        check(g, "Codex config.toml 등록", bool(e),
              ", ".join(cfg.get("mcp_servers", {})))
        if e:
            ok = (os.path.exists(e["command"])
                  and os.path.exists(e["args"][0])
                  and os.path.exists(e["env"]["SCHEDULE_XLSX"]))
            check(g, "Codex 경로 유효", ok, e["command"])
        # 기존 설정 보존 확인
        check(g, "Codex 기존 설정 보존", "node_repl" in cfg.get("mcp_servers", {}),
              f"plugins {len(cfg.get('plugins', {}))}개")
    else:
        check(g, "Codex config.toml 존재", False, "파일 없음")

    # 슬래시 커맨드 / 프롬프트
    cmd = os.path.join(HERE, ".claude", "commands", "weekly-report.md")
    check(g, "Claude 슬래시 커맨드", os.path.exists(cmd), cmd)
    if os.path.exists(cmd):
        body = open(cmd, encoding="utf-8").read()
        check(g, "커맨드 frontmatter", body.startswith("---\n"))
        check(g, "커맨드가 완료율 지표 지정", "completion_rate_due" in body)

    repo_prompt = os.path.join(HERE, "codex-prompts", "weekly-report.md")
    home_prompt = os.path.expanduser("~/.codex/prompts/weekly-report.md")
    check(g, "Codex 프롬프트 배치", os.path.exists(home_prompt), home_prompt)
    if os.path.exists(repo_prompt) and os.path.exists(home_prompt):
        same = (open(repo_prompt, encoding="utf-8").read()
                == open(home_prompt, encoding="utf-8").read())
        check(g, "저장소 사본과 동기화", same, "일치" if same else "내용 다름")


# --- 실행 ------------------------------------------------------------------

def check_summary_guard() -> None:
    """요약 숫자 검증 - '숫자는 코드가 만든다'를 요약 문장에도 적용한다."""
    g = "6. 요약 검증"
    s31 = sr.summarize("W31")
    s32 = sr.summarize("W32")

    def rejected(week: str, summary: str) -> tuple[bool, str]:
        try:
            rb.build(week, summary)
            return False, "저장됨(거부 실패)"
        except ValueError as e:
            return True, str(e)[:60]

    ok, msg = rejected("W31", "금주 마감 20건 중 19건 완료(95.0%)로 초과 달성했다.")
    check(g, "집계에 없는 숫자 거부", ok, msg)

    # 전체 건수 기준 완료율은 보고 규칙 위반이므로 거부되어야 한다.
    ok, msg = rejected("W31", f"금주 완료율 {s31['completion_rate']}%.")
    check(g, "왜곡 지표(completion_rate) 거부", ok, msg)

    good = (f"금주 마감 {s31['due_in_week']}건 중 {s31['done_in_week']}건 완료"
            f"({s31['completion_rate_due']}%). "
            f"지연 {len(s31['delayed'])}건은 차주로 이월된다.")
    try:
        r = rb.build("W31", good)
        check(g, "정상 요약 통과", True,
              f"검증 {len(r['summary_numbers_verified'])}개 숫자")
        check(g, "숫자 출처 반환",
              r["summary_numbers_verified"].get(str(s31["completion_rate_due"]))
              == "완료율(주내 마감 기준)",
              str(r["summary_numbers_verified"]))
    except ValueError as e:
        check(g, "정상 요약 통과", False, str(e)[:80])

    # 커맨드가 지시하는 직전 주 비교 문장이 통과해야 한다.
    delta = round(abs(s32["completion_rate_due"] - s31["completion_rate_due"]), 1)
    trend = (f"금주 마감 {s32['due_in_week']}건 중 {s32['done_in_week']}건 완료"
             f"({s32['completion_rate_due']}%)로 직전 주 "
             f"{s31['completion_rate_due']}%에서 {delta}%p 하락했다.")
    try:
        rb.build("W32", trend)
        check(g, "직전 주 값·증감폭 허용", True, f"{delta}%p")
    except ValueError as e:
        check(g, "직전 주 값·증감폭 허용", False, str(e)[:80])

    # "3주 연속" 같은 주차 수 표현
    try:
        rb.build("W33", "완료율이 3주 연속 하락했다.")
        check(g, "주차 수 표현 허용", True, "'3주 연속'")
    except ValueError as e:
        check(g, "주차 수 표현 허용", False, str(e)[:80])

    # 우회 경로가 살아있는지 (수동 판단이 필요한 경우용)
    try:
        rb.build("W31", "마감 99건.", strict=False)
        check(g, "strict=False 우회", True, "저장됨")
    except ValueError:
        check(g, "strict=False 우회", False, "우회 불가")

    # 요약 없으면 검증을 건너뛰어야 한다 (초안 생성 경로)
    c = rb.check_summary("W31", "")
    check(g, "빈 요약은 검증 생략", c["ok"] and not c["unknown"], str(c["unknown"]))


async def main() -> int:
    check_data()
    check_metrics()
    check_report()
    check_summary_guard()
    await check_protocol()
    check_configs()

    group = None
    for gname, name, ok, detail in results:
        if gname != group:
            print(f"\n=== {gname} ===")
            group = gname
        mark = "ok  " if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""))

    failed = [r for r in results if not r[2]]
    print(f"\n{'=' * 40}")
    print(f"{len(results) - len(failed)}/{len(results)} 통과")
    if failed:
        print("실패:")
        for _, name, _, detail in failed:
            print(f"  - {name}: {detail}")
    print("\n미검증 (수동 확인 필요):")
    print("  - /weekly-report 슬래시 커맨드 실제 실행")
    print("    .mcp.json 승인 후 확인. allowed-tools의 mcp__weekly-report__* 이름 검증 포함")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
