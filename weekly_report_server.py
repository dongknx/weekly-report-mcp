"""주간일정 보고서 MCP 서버.

schedule_reader 모듈을 MCP 툴로 노출하는 얇은 래퍼다.
집계/판정 로직은 전부 schedule_reader에 있고, 이 파일은 인터페이스만 담당한다.
보고서 문장 생성은 하지 않는다 - 그건 LLM의 일이다.

실행:
    python weekly_report_server.py          # stdio 서버 (클라이언트가 실행)
    python weekly_report_server.py --check  # 툴 목록 자기점검
"""

from __future__ import annotations

import os
import sys

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

# 클라이언트가 임의의 작업 디렉토리에서 서버를 실행하므로 경로를 직접 고정한다.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import report_builder as rb  # noqa: E402
import schedule_reader as sr  # noqa: E402


def _guard(fn, *args):
    """예외를 ToolError로 변환한다.

    그대로 두면 클라이언트에 'Error executing tool ...'만 전달되어
    모델이 무엇을 잘못 넣었는지 알 수 없다. 복구 가능한 메시지를 돌려준다.
    """
    try:
        return fn(*args)
    except KeyError as e:
        raise ToolError(str(e).strip('"')) from e
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e)) from e


mcp = MCPServer(
    name="weekly-report",
    instructions=(
        "주차별 일정 엑셀에서 주간보고서 작성에 필요한 데이터를 제공한다. "
        "건수·완료율·지연일수 같은 수치는 반드시 이 서버의 집계 결과를 사용하고 "
        "직접 세거나 계산하지 말 것. 보고서 문장 작성은 모델이 담당한다."
    ),
)


@mcp.tool()
def list_weeks() -> list[dict]:
    """사용 가능한 주차 목록을 반환한다.

    각 주차의 시트명(W31 형식), ISO 연도/주차, 주 시작일(월)·종료일(일),
    등록된 업무 건수를 함께 반환한다. 어떤 주차가 있는지 모를 때 가장 먼저 호출한다.
    """
    return _guard(sr.list_weeks)


@mcp.tool()
def get_schedule(week: str) -> dict:
    """특정 주차의 업무 목록 전체를 정규화된 형태로 반환한다.

    보고서의 근거를 확인하거나 개별 업무를 인용할 때 사용한다.
    집계 수치가 필요한 경우에는 이 툴 대신 summarize_week를 사용할 것.

    Args:
        week: 주차 시트명. 예) "W31"

    Returns:
        주차 정보와 tasks 배열. 각 업무는 업무명, 시작일, 마감일,
        정규화된 상태(완료/진행중/지연/보류/기타), 원본 상태 표기, 비고,
        신규 착수 여부(is_new), 차주 이월 여부(is_carryover),
        주 종료일 기준 마감 초과일(overdue_days)을 가진다.
    """
    w = _guard(sr.get_week, week)
    return {**w.to_dict(), "tasks": [t.to_dict() for t in w.tasks]}


@mcp.tool()
def summarize_week(week: str) -> dict:
    """특정 주차를 집계한다. 주간보고서 작성 시 수치의 유일한 출처.

    상태별 건수, 완료율, 지연 업무 상세(사유 포함), 차주 이월 업무,
    그리고 표기 흔들림을 보정한 행 목록을 반환한다.
    보고서에 쓰는 모든 숫자는 이 결과에서 그대로 가져와야 한다.

    Args:
        week: 주차 시트명. 예) "W31"

    Returns:
        status_counts(상태별 건수), completion_rate(완료율 %),
        delayed(지연 업무: 마감일·초과일수·사유), carryover(차주 이월 업무),
        new_tasks(해당 주 착수 업무명), normalized_rows(정규화 보정 이력).
    """
    return _guard(sr.summarize, week)


@mcp.tool()
def trace_task(keyword: str) -> dict:
    """업무명 키워드로 여러 주차에 걸친 업무를 추적한다.

    시트가 주차별로 분리되어 있어 같은 업무가 여러 주에 등장하거나,
    마감일이 다음 주차로 넘어가도 후속 시트에 기록되지 않는 경우가 있다.
    특정 업무의 진행 경과나 이월 이력을 확인할 때 사용한다.

    Args:
        keyword: 업무명에 포함된 문자열. 공백 무시, 대소문자 무관.

    Returns:
        keyword, 매칭 건수, matches 배열(주차·업무명·기간·상태·비고).
    """
    needle = keyword.replace(" ", "").lower()
    matches = []
    for w in _guard(sr.load_weeks).values():
        for t in w.tasks:
            if needle in t.name.replace(" ", "").lower():
                matches.append(
                    {
                        "week": w.name,
                        "week_period": f"{w.start.isoformat()} ~ {w.end.isoformat()}",
                        "name": t.name,
                        "start": t.start.isoformat() if t.start else None,
                        "end": t.end.isoformat() if t.end else None,
                        "status": t.status,
                        "is_carryover": t.is_carryover,
                        "note": t.note,
                    }
                )
    return {"keyword": keyword, "match_count": len(matches), "matches": matches}


@mcp.tool()
def build_report(week: str, summary: str = "") -> dict:
    """주차 보고서를 Markdown 파일로 생성하고 저장 경로를 반환한다.

    지표·완료·진행·지연·이월 섹션은 집계 결과로 자동 채워진다.
    '요약' 섹션만 모델이 작성해 summary로 넘긴다.

    권장 순서: summarize_week로 집계를 먼저 확인하고, 그 수치를 근거로
    요약 3~4문장을 작성한 뒤 이 툴을 호출한다. summary를 비우면
    자리표시자가 들어간 초안이 생성된다.

    Args:
        week: 주차 시트명. 예) "W31"
        summary: 요약 섹션에 넣을 문장. 집계 수치와 어긋나지 않게 쓸 것.

    Returns:
        path(저장 경로), week, format, bytes, summary_included.
    """
    return _guard(rb.build, week, summary or None)


@mcp.tool()
def preview_report(week: str, summary: str = "") -> str:
    """보고서 Markdown을 파일로 저장하지 않고 문자열로 반환한다.

    저장 전에 내용을 확인하거나, 대화창에 보고서를 바로 보여줄 때 사용한다.

    Args:
        week: 주차 시트명. 예) "W31"
        summary: 요약 섹션에 넣을 문장.
    """
    return _guard(rb.render, week, summary or None)


def _check() -> int:
    """서버를 띄우지 않고 툴 등록 상태와 데이터 접근을 점검한다."""
    import asyncio

    tools = asyncio.run(mcp.list_tools())
    print(f"등록된 툴 {len(tools)}개:")
    for t in tools:
        first = (t.description or "").strip().splitlines()[0]
        print(f"  - {t.name}: {first}")

    print(f"\n엑셀 경로: {sr.resolve_path()}")
    weeks = sr.list_weeks()
    print(f"주차 {len(weeks)}개: " + ", ".join(w["week"] for w in weeks))
    s = sr.summarize(weeks[0]["week"])
    print(f"{s['week']} 집계 샘플: 총 {s['total']}건, 완료율 {s['completion_rate']}%")
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(_check())
    mcp.run(transport="stdio")
