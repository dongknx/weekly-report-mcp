"""MCP 서버 stdio 연동 점검용 클라이언트.

클라이언트(Claude Code/Codex)에 연결하기 전에 프로토콜 레벨에서
핸드셰이크와 툴 호출이 동작하는지 확인한다.

    python test_client.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))


def _text(result) -> str:
    parts = [c.text for c in result.content if getattr(c, "text", None)]
    return "\n".join(parts)


async def main() -> int:
    params = StdioServerParameters(
        command=sys.executable,
        args=[os.path.join(HERE, "weekly_report_server.py")],
        # 일부러 다른 작업 디렉토리에서 띄워 경로 의존성을 검증한다.
        cwd=os.path.expanduser("~"),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            info = init.server_info
            print(f"연결됨: {info.name} v{info.version or '-'}")

            tools = await session.list_tools()
            print(f"툴 {len(tools.tools)}개: " + ", ".join(t.name for t in tools.tools))

            print("\n--- list_weeks ---")
            r = await session.call_tool("list_weeks", {})
            print(_text(r)[:300])

            print("\n--- summarize_week(W31) ---")
            r = await session.call_tool("summarize_week", {"week": "W31"})
            data = json.loads(_text(r))
            print(f"총 {data['total']}건, 완료율 {data['completion_rate']}%")
            print(f"상태별: {data['status_counts']}")
            print(f"지연 {len(data['delayed'])}건, 이월 {len(data['carryover'])}건, "
                  f"정규화 보정 {len(data['normalized_rows'])}건")

            print("\n--- trace_task('정적 분석') ---")
            r = await session.call_tool("trace_task", {"keyword": "정적 분석"})
            tr = json.loads(_text(r))
            for m in tr["matches"]:
                print(f"  {m['week']}: {m['name']} [{m['status']}] "
                      f"{m['start']}~{m['end']}")

            print("\n--- 에러 처리: summarize_week(W99) ---")
            r = await session.call_tool("summarize_week", {"week": "W99"})
            print(f"isError={r.is_error}: {_text(r)[:120]}")

    print("\n검증 완료")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
