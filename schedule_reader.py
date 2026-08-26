"""주간일정 엑셀 리더 / 정규화 모듈.

MCP와 무관한 순수 Python 모듈이다. 엑셀 읽기, 상태 정규화, 집계까지
결정론적으로 처리하고, 보고서 문장 생성은 하지 않는다.

CLI 검증:
    python schedule_reader.py              # 전체 주차 요약
    python schedule_reader.py W31          # 특정 주차 요약
    python schedule_reader.py W31 --rows   # 정규화된 원본 행
    python schedule_reader.py W31 --json   # JSON 출력
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, asdict, field
from datetime import date, datetime

import openpyxl

DEFAULT_XLSX = (
    r"C:\Users\20224\Desktop\202608_sec_gumi\mx-agentic-ai-day1-prd"
    r"\sample-data\일정_샘플.xlsx"
)

# 정규화 후 사용하는 표준 상태값. '기타'는 미지의 값을 버리지 않고 보존하기 위한 슬롯.
STATUS_DONE = "완료"
STATUS_ONGOING = "진행중"
STATUS_DELAYED = "지연"
STATUS_HOLD = "보류"
STATUS_OTHER = "기타"

STATUS_ORDER = [STATUS_DONE, STATUS_ONGOING, STATUS_DELAYED, STATUS_HOLD, STATUS_OTHER]

# 공백을 모두 제거한 형태를 키로 삼는다. ('완료 ', '진행 중' 같은 표기 흔들림 흡수)
_STATUS_MAP = {
    "완료": STATUS_DONE,
    "종료": STATUS_DONE,
    "진행중": STATUS_ONGOING,
    "진행": STATUS_ONGOING,
    "지연": STATUS_DELAYED,
    "보류": STATUS_HOLD,
    "홀드": STATUS_HOLD,
}

_WEEK_RE = re.compile(r"^W(\d{1,2})$", re.IGNORECASE)


def normalize_status(raw) -> tuple[str, bool]:
    """상태값을 표준 4종으로 정규화한다.

    Returns:
        (정규화된 상태, 원본 표기가 표준형과 달랐는지 여부)
    """
    if raw is None:
        return STATUS_OTHER, True
    text = str(raw)
    # 전각 공백까지 포함해 모든 공백 제거
    key = re.sub(r"\s+", "", text.replace("\u3000", " "))
    status = _STATUS_MAP.get(key, STATUS_OTHER)
    return status, text != status


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


@dataclass
class Task:
    sheet: str
    row: int
    name: str
    start: date | None
    end: date | None
    status: str
    status_raw: str
    normalized: bool            # 원본 상태 표기가 표준형과 달랐는가
    note: str | None
    is_new: bool = False        # 해당 주에 착수
    is_carryover: bool = False  # 마감일이 주 종료일 이후 -> 차주 이월
    overdue_days: int = 0       # 주 종료일 기준 마감 초과 일수

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"] = self.start.isoformat() if self.start else None
        d["end"] = self.end.isoformat() if self.end else None
        return d


@dataclass
class Week:
    name: str
    iso_year: int
    iso_week: int
    start: date
    end: date
    tasks: list[Task] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "week": self.name,
            "iso_year": self.iso_year,
            "iso_week": self.iso_week,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "task_count": len(self.tasks),
        }


def resolve_path(path: str | None = None) -> str:
    """엑셀 경로 결정: 인자 > SCHEDULE_XLSX 환경변수 > 기본값."""
    resolved = path or os.environ.get("SCHEDULE_XLSX") or DEFAULT_XLSX
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"일정 엑셀을 찾을 수 없음: {resolved}")
    return resolved


def _week_bounds(sheet_name: str, sheet_dates: list[date]) -> tuple[int, int, date, date]:
    """시트명(W31)과 시트 내 날짜로 주차 구간을 계산한다."""
    m = _WEEK_RE.match(sheet_name.strip())
    if not m:
        raise ValueError(f"주차 형식이 아닌 시트명: {sheet_name!r} (예: W31)")
    iso_week = int(m.group(1))
    # 연도 표기가 파일에 없으므로 시트 내 날짜에서 추론한다.
    years = [d.year for d in sheet_dates if d]
    iso_year = min(years) if years else date.today().year
    start = date.fromisocalendar(iso_year, iso_week, 1)  # 월요일
    end = date.fromisocalendar(iso_year, iso_week, 7)    # 일요일
    return iso_year, iso_week, start, end


def load_weeks(path: str | None = None) -> dict[str, Week]:
    """엑셀 전체를 읽어 주차별 Week 객체로 반환한다. 원본은 읽기만 한다."""
    xlsx = resolve_path(path)
    wb = openpyxl.load_workbook(xlsx, data_only=True, read_only=True)
    weeks: dict[str, Week] = {}

    for ws in wb.worksheets:
        rows: list[Task] = []
        for idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if row is None or all(c is None or str(c).strip() == "" for c in row):
                continue  # 빈 행 스킵
            cells = list(row) + [None] * (5 - len(row))
            name = str(cells[0]).strip() if cells[0] is not None else ""
            if not name:
                continue
            status, changed = normalize_status(cells[3])
            note = str(cells[4]).strip() if cells[4] is not None else None
            rows.append(
                Task(
                    sheet=ws.title,
                    row=idx,
                    name=name,
                    start=_as_date(cells[1]),
                    end=_as_date(cells[2]),
                    status=status,
                    status_raw="" if cells[3] is None else str(cells[3]),
                    normalized=changed,
                    note=note or None,
                )
            )

        sheet_dates = [d for t in rows for d in (t.start, t.end) if d]
        iso_year, iso_week, w_start, w_end = _week_bounds(ws.title, sheet_dates)

        for t in rows:
            if t.start and w_start <= t.start <= w_end:
                t.is_new = True
            if t.end and t.end > w_end:
                t.is_carryover = True
            if t.end and t.end < w_end:
                t.overdue_days = (w_end - t.end).days

        weeks[ws.title] = Week(ws.title, iso_year, iso_week, w_start, w_end, rows)

    wb.close()
    return weeks


def list_weeks(path: str | None = None) -> list[dict]:
    return [w.to_dict() for w in load_weeks(path).values()]


def get_week(week: str, path: str | None = None) -> Week:
    weeks = load_weeks(path)
    key = week.strip().upper()
    if key not in weeks:
        raise KeyError(f"주차 없음: {week!r} (가능: {', '.join(weeks)})")
    return weeks[key]


def summarize(week: str, path: str | None = None) -> dict:
    """주차 집계. 숫자 계산은 전부 여기서 끝낸다 (LLM에 맡기지 않는다)."""
    w = get_week(week, path)
    counts = {s: 0 for s in STATUS_ORDER}
    for t in w.tasks:
        counts[t.status] += 1
    total = len(w.tasks)
    done = counts[STATUS_DONE]

    delayed = [t for t in w.tasks if t.status == STATUS_DELAYED]
    carryover = [t for t in w.tasks if t.is_carryover and t.status != STATUS_DONE]

    return {
        "week": w.name,
        "period": {"start": w.start.isoformat(), "end": w.end.isoformat()},
        "total": total,
        "status_counts": counts,
        "completion_rate": round(done / total * 100, 1) if total else 0.0,
        "delayed": [
            {
                "name": t.name,
                "end": t.end.isoformat() if t.end else None,
                "overdue_days": t.overdue_days,
                "beyond_week": t.is_carryover,
                "reason": t.note,
            }
            for t in delayed
        ],
        "carryover": [
            {
                "name": t.name,
                "end": t.end.isoformat() if t.end else None,
                "status": t.status,
                "note": t.note,
            }
            for t in carryover
        ],
        "new_tasks": [t.name for t in w.tasks if t.is_new],
        # 정규화로 보정된 행 - 데이터 품질 추적용
        "normalized_rows": [
            {"row": t.row, "name": t.name, "raw": t.status_raw, "to": t.status}
            for t in w.tasks
            if t.normalized
        ],
    }


def _print_summary(s: dict) -> None:
    print(f"[{s['week']}] {s['period']['start']} ~ {s['period']['end']}  총 {s['total']}건")
    counts = ", ".join(f"{k} {v}" for k, v in s["status_counts"].items() if v)
    print(f"  상태: {counts}  (완료율 {s['completion_rate']}%)")
    print(f"  신규 착수: {len(s['new_tasks'])}건")
    if s["delayed"]:
        print(f"  지연 {len(s['delayed'])}건:")
        for d in s["delayed"]:
            tail = f" - {d['reason']}" if d["reason"] else ""
            if d["beyond_week"]:
                mark = " (마감일이 주 종료일 이후)"
            elif d["overdue_days"] == 0:
                mark = " (마감일 = 주 종료일)"
            else:
                mark = f" (주 종료일 기준 {d['overdue_days']}일 초과)"
            print(f"    - {d['name']} / 마감 {d['end']}{mark}{tail}")
    if s["carryover"]:
        print(f"  차주 이월 {len(s['carryover'])}건:")
        for c in s["carryover"]:
            print(f"    - {c['name']} / 마감 {c['end']} [{c['status']}]")
    if s["normalized_rows"]:
        print(f"  정규화 보정 {len(s['normalized_rows'])}건:")
        for n in s["normalized_rows"]:
            print(f"    - {n['row']}행 {n['name']}: {n['raw']!r} -> {n['to']}")
    print()


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}

    weeks = load_weeks()
    targets = [args[0].upper()] if args else list(weeks)

    if "--rows" in flags:
        for wk in targets:
            w = get_week(wk)
            print(f"=== {w.name} ({w.start} ~ {w.end}) ===")
            for t in w.tasks:
                flag = ("N" if t.is_new else "-") + ("C" if t.is_carryover else "-")
                note = f"  # {t.note}" if t.note else ""
                print(f"  {t.row:>3} [{flag}] {t.status:<4} {t.name}  "
                      f"{t.start}~{t.end}{note}")
            print()
        return 0

    summaries = [summarize(wk) for wk in targets]
    if "--json" in flags:
        payload = summaries if len(summaries) > 1 else summaries[0]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    for s in summaries:
        _print_summary(s)

    if len(summaries) > 1:
        grand = sum(s["total"] for s in summaries)
        print(f"전체 {grand}건 / {len(summaries)}개 주차")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
