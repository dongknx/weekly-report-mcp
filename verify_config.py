"""클라이언트 설정 파일 검증.

test_client.py는 서버 자체를 검증한다. 이 스크립트는 한 단계 위 -
.mcp.json / config.toml에 적힌 command/args/env 그대로 서버를 띄워
"설정 파일이 맞는지"를 검증한다. 경로 오타나 env 누락은 여기서 잡힌다.

    python verify_config.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tomllib

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_KEY = "weekly-report"          # Claude Code (.mcp.json)
SERVER_KEY_TOML = "weekly_report"     # Codex (config.toml, TOML 키 규칙)


def load_claude_config() -> tuple[str, dict] | None:
    path = os.path.join(HERE, ".mcp.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    entry = cfg.get("mcpServers", {}).get(SERVER_KEY)
    return (path, entry) if entry else None


def load_codex_config() -> tuple[str, dict] | None:
    path = os.path.expanduser("~/.codex/config.toml")
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    entry = cfg.get("mcp_servers", {}).get(SERVER_KEY_TOML)
    return (path, entry) if entry else None


def static_checks(label: str, entry: dict) -> bool:
    """서버를 띄우기 전에 경로만으로 잡을 수 있는 문제를 먼저 본다."""
    ok = True
    cmd = entry.get("command", "")
    args = entry.get("args", [])
    env = entry.get("env", {})

    if not os.path.exists(cmd):
        print(f"  [FAIL] command 없음: {cmd}")
        ok = False
    else:
        print(f"  [ok] command: {cmd}")

    for a in args:
        if a.endswith(".py"):
            if not os.path.exists(a):
                print(f"  [FAIL] 서버 스크립트 없음: {a}")
                ok = False
            else:
                print(f"  [ok] script: {a}")

    xlsx = env.get("SCHEDULE_XLSX")
    if not xlsx:
        print("  [WARN] SCHEDULE_XLSX 미지정 - 서버 기본값으로 동작")
    elif not os.path.exists(xlsx):
        print(f"  [FAIL] 엑셀 없음: {xlsx}")
        ok = False
    else:
        print(f"  [ok] xlsx: {xlsx}")

    return ok


async def live_check(entry: dict) -> bool:
    """설정 그대로 서버를 띄워 핸드셰이크와 툴 호출을 확인한다."""
    params = StdioServerParameters(
        command=entry["command"],
        args=entry.get("args", []),
        # 클라이언트가 프로젝트 밖에서 띄울 수 있으므로 일부러 무관한 cwd 사용
        cwd=os.path.expanduser("~"),
        env={**os.environ, **entry.get("env", {})},
    )
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                tools = await session.list_tools()
                print(f"  [ok] 연결: {init.server_info.name} / "
                      f"툴 {len(tools.tools)}개")

                r = await session.call_tool("summarize_week", {"week": "W31"})
                text = "\n".join(c.text for c in r.content
                                 if getattr(c, "text", None))
                if r.is_error:
                    print(f"  [FAIL] 툴 호출 실패: {text[:120]}")
                    return False
                data = json.loads(text)
                print(f"  [ok] summarize_week(W31): 총 {data['total']}건, "
                      f"완료율 {data['completion_rate']}%")
                # Phase 1 CLI 기준값과 대조
                if data["total"] != 11 or data["status_counts"]["완료"] != 5:
                    print("  [FAIL] Phase 1 기준값과 불일치")
                    return False
                print("  [ok] Phase 1 기준값 일치 (11건 / 완료 5)")
                return True
    except Exception as e:
        print(f"  [FAIL] {type(e).__name__}: {str(e)[:200]}")
        return False


async def main() -> int:
    targets = [
        ("Claude Code (.mcp.json)", load_claude_config()),
        ("Codex (~/.codex/config.toml)", load_codex_config()),
    ]

    results = []
    for label, loaded in targets:
        print(f"\n=== {label} ===")
        if loaded is None:
            print("  [SKIP] 설정 없음")
            continue
        path, entry = loaded
        print(f"  파일: {path}")
        ok = static_checks(label, entry)
        if ok:
            ok = await live_check(entry)
        results.append((label, ok))

    print("\n=== 결과 ===")
    for label, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
