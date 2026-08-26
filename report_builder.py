"""주간보고서 Markdown 렌더러.

숫자와 목록은 전부 schedule_reader의 집계 결과에서 가져온다.
이 모듈은 서술 문장을 만들지 않는다 - '요약' 섹션은 호출자(LLM)가 넘겨준
문장을 그대로 끼워넣거나, 없으면 자리표시자를 남긴다.

    python report_builder.py W31
    python report_builder.py --all
"""

from __future__ import annotations

import os
import re
import sys

import schedule_reader as sr

DEFAULT_OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

SUMMARY_PLACEHOLDER = (
    "> _요약 미작성._ 아래 지표와 항목을 근거로 3~4문장 작성 후 "
    "`build_report(week, summary=\"...\")` 로 다시 생성할 것."
)


def _md(d: str | None) -> str:
    """2026-07-31 -> 07/31 (본문 가독성용. 연도는 제목에 이미 있다)"""
    return f"{d[5:7]}/{d[8:10]}" if d else "-"


_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _prior_week(week: str, path: str | None = None) -> str | None:
    """시트 순서상 직전 주차. 첫 주차면 None."""
    names = list(sr.load_weeks(path))
    key = week.strip().upper()
    i = names.index(key) if key in names else 0
    return names[i - 1] if i > 0 else None


def allowed_numbers(week: str, path: str | None = None) -> dict[float, str]:
    """요약에 등장해도 되는 숫자와 그 출처.

    집계에서 유도되는 값만 허용한다. 전체 건수 기준 completion_rate는
    **의도적으로 제외**했다 - 요약에 그 값을 쓰는 것 자체가 규칙 위반이므로
    검증에서 걸리는 게 옳다.
    """
    s = sr.summarize(week, path)
    w = sr.get_week(week, path)
    ok: dict[float, str] = {}

    def add(v, src: str) -> None:
        if v is None:
            return
        ok.setdefault(float(v), src)

    add(s["total"], "전체 등록 건수")
    add(s["due_in_week"], "금주 마감 건수")
    add(s["done_in_week"], "금주 완료 건수")
    add(s["completion_rate_due"], "완료율(주내 마감 기준)")
    add(len(s["delayed"]), "지연 건수")
    add(len(s["on_hold"]), "보류 건수")
    add(len(s["carryover"]), "차주 이월 건수")
    add(len(s["new_tasks"]), "신규 착수 건수")
    for k, v in s["status_counts"].items():
        add(v, f"상태 '{k}' 건수")
    for d in s["delayed"]:
        add(d["overdue_days"], f"'{d['name']}' 마감 초과일")

    add(w.iso_week, "주차 번호")
    add(w.iso_year, "연도")
    for d in (w.start, w.end):
        add(d.month, "주 기간 월")
        add(d.day, "주 기간 일")

    # 커맨드가 직전 주차 비교를 지시하므로 그 값과 증감폭도 허용한다.
    prev = _prior_week(week, path)
    if prev:
        ps = sr.summarize(prev, path)
        add(ps["completion_rate_due"], f"직전 주({prev}) 완료율")
        add(round(abs(s["completion_rate_due"] - ps["completion_rate_due"]), 1),
            "직전 주 대비 증감폭(%p)")

    # "3주 연속 하락" 처럼 주차 수를 세는 표현을 허용한다.
    for n in range(1, len(sr.load_weeks(path)) + 1):
        add(n, "주차 수")

    return ok


def check_summary(week: str, summary: str,
                  path: str | None = None) -> dict:
    """요약에 집계와 무관한 숫자가 있는지 검사한다.

    Returns:
        ok(불일치 없음), unknown(집계에 없는 숫자 목록), verified(확인된 숫자와 출처).
    """
    if not summary or not summary.strip():
        return {"ok": True, "unknown": [], "verified": {}}

    ok_nums = allowed_numbers(week, path)
    found = [float(m) for m in _NUM_RE.findall(summary)]
    unknown = sorted({n for n in found if n not in ok_nums})
    verified = {n: ok_nums[n] for n in found if n in ok_nums}
    return {"ok": not unknown, "unknown": unknown, "verified": verified}


def render(week: str, summary: str | None = None, path: str | None = None) -> str:
    """주차 보고서 Markdown 문자열을 만든다."""
    s = sr.summarize(week, path)
    w = sr.get_week(week, path)

    done = [t for t in w.tasks if t.status == sr.STATUS_DONE]
    ongoing = [t for t in w.tasks if t.status == sr.STATUS_ONGOING]

    p = s["period"]
    L: list[str] = []
    L.append(f"# 주간업무보고 — {w.iso_year}년 {w.iso_week}주차 "
             f"({_md(p['start'])} ~ {_md(p['end'])})")
    L.append("")

    L.append("## 요약")
    L.append("")
    L.append(summary.strip() if summary and summary.strip() else SUMMARY_PLACEHOLDER)
    L.append("")

    L.append("## 지표")
    L.append("")
    L.append("| 항목 | 값 |")
    L.append("|---|---|")
    L.append(f"| 금주 마감 업무 | {s['due_in_week']}건 |")
    L.append(f"| 그중 완료 | {s['done_in_week']}건 |")
    L.append(f"| **완료율** | **{s['completion_rate_due']}%** |")
    L.append(f"| 전체 등록 업무 | {s['total']}건 |")
    L.append(f"| 차주 이월 | {len(s['carryover'])}건 |")
    L.append("")
    L.append("완료율은 금주 마감 업무 기준이다. 마감일이 차주 이후인 업무는 "
             "분모에서 제외한다.")
    L.append("")

    L.append(f"## 완료 ({len(done)}건)")
    L.append("")
    if done:
        for t in done:
            L.append(f"- {t.name} ({_md(t.start.isoformat() if t.start else None)}"
                     f"~{_md(t.end.isoformat() if t.end else None)})")
    else:
        L.append("_해당 없음_")
    L.append("")

    L.append(f"## 진행 중 ({len(ongoing)}건)")
    L.append("")
    if ongoing:
        for t in ongoing:
            mark = " **(차주 마감)**" if t.is_carryover else ""
            L.append(f"- {t.name} — 마감 "
                     f"{_md(t.end.isoformat() if t.end else None)}{mark}")
    else:
        L.append("_해당 없음_")
    L.append("")

    risks = ([("지연", d) for d in s["delayed"]]
             + [("보류", h) for h in s["on_hold"]])
    L.append(f"## 지연 / 리스크 ({len(risks)}건)")
    L.append("")
    if risks:
        L.append("| 구분 | 업무 | 마감 | 사유 |")
        L.append("|---|---|---|---|")
        for kind, r in risks:
            L.append(f"| {kind} | {r['name']} | {_md(r['end'])} | "
                     f"{r.get('reason') or '미기재'} |")
    else:
        L.append("_해당 없음_")
    L.append("")

    L.append(f"## 차주 이월 ({len(s['carryover'])}건)")
    L.append("")
    if s["carryover"]:
        L.append("| 업무 | 마감 | 상태 |")
        L.append("|---|---|---|")
        for c in s["carryover"]:
            L.append(f"| {c['name']} | {_md(c['end'])} | {c['status']} |")
    else:
        L.append("_해당 없음_")
    L.append("")

    # 원본 데이터 품질 이슈는 본문 노이즈이므로 최하단 주석으로 남긴다.
    L.append("---")
    L.append("")
    L.append(f"<sub>출처: `{os.path.basename(sr.resolve_path(path))}` 시트 "
             f"`{w.name}` / 집계: `summarize_week`</sub>")
    if s["normalized_rows"]:
        L.append("")
        items = ", ".join(f"{n['row']}행 `{n['raw']}`→`{n['to']}`"
                          for n in s["normalized_rows"])
        L.append(f"<sub>원본 상태 표기 보정 {len(s['normalized_rows'])}건: "
                 f"{items} — 엑셀 수정 권장</sub>")
    L.append("")

    return "\n".join(L)


def build(week: str, summary: str | None = None, out_dir: str | None = None,
          path: str | None = None, strict: bool = True) -> dict:
    """보고서를 파일로 저장하고 경로와 요약 정보를 반환한다.

    strict=True(기본)면 요약에 집계와 무관한 숫자가 있을 때 저장을 거부한다.
    "숫자는 코드가 만든다"는 원칙을 요약 문장에도 적용하기 위한 것이다.
    """
    w = sr.get_week(week, path)

    chk = check_summary(week, summary or "", path)
    if strict and not chk["ok"]:
        nums = ", ".join(f"{n:g}" for n in chk["unknown"])
        srcs = sorted({v for v in allowed_numbers(week, path).values()})
        raise ValueError(
            f"요약의 숫자 {nums} 가 {w.name} 집계에 없다. "
            f"숫자는 summarize_week 결과에서만 가져올 것. "
            f"사용 가능한 값의 출처: {', '.join(srcs)}. "
            f"전체 건수 기준 completion_rate 는 보고에 쓰지 않는다."
        )

    text = render(week, summary, path)

    target_dir = out_dir or os.environ.get("REPORT_DIR") or DEFAULT_OUT_DIR
    os.makedirs(target_dir, exist_ok=True)
    filename = f"{w.iso_year}-W{w.iso_week:02d}.md"
    full = os.path.join(target_dir, filename)
    with open(full, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

    return {
        "path": full,
        "week": w.name,
        "format": "md",
        "bytes": len(text.encode("utf-8")),
        "summary_included": bool(summary and summary.strip()),
        # 요약에 쓰인 숫자와 그 출처. 검증을 통과했다는 근거로 함께 반환한다.
        "summary_numbers_verified": {f"{k:g}": v
                                     for k, v in chk["verified"].items()},
    }


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}

    if "--all" in flags or not args:
        targets = list(sr.load_weeks())
    else:
        targets = [args[0].upper()]

    for wk in targets:
        if "--stdout" in flags:
            print(render(wk))
        else:
            r = build(wk)
            print(f"생성: {r['path']}  ({r['bytes']} bytes, "
                  f"요약 {'포함' if r['summary_included'] else '미작성'})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
