"""강의 전체 스냅샷 — '실제 사이트에 접속했을 때 보이는 것'을 한 번에 읽는다.

기준은 `GET /api/v1/courses/{id}/tabs` 다. 이게 학생이 실제로 보는 메뉴의 정답지이므로,
탭을 하나씩 돌면서 해당 내용을 읽어 모으고, 못 읽는 탭은 그대로 표시한다(coverage).
따라서 "사이트엔 있는데 MCP 엔 없는 것"이 결과에 드러난다.

탭 -> 섹션 매핑
  홈=home · 공지=announcements · 강의계획서=syllabus · 수강생=people · 모듈=modules
  과제=assignments · 성적=grades(내 점수·코멘트) · 파일=files(+본문 링크 자료)
  페이지=pages(본문 포함) · 퀴즈=quizzes · 토론=discussions
  주차학습=weekly(LTI) · 강의/출결=attendance(LTI) · 게시판/열린게시판=boards(LTI)
  강의교재=textbooks (LTI 지만 실제로는 sugang cc103bk iframe)
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

import boards
import canvas_client as cc
import links
import lti
import weekly

# 내부 탭 id -> 섹션 이름
_TAB_SECTIONS = {
    "home": "home",
    "announcements": "announcements",
    "syllabus": "syllabus",
    "people": "people",
    "modules": "modules",
    "assignments": "assignments",
    "grades": "grades",
    "files": "files",
    "pages": "pages",
    "quizzes": "quizzes",
    "discussions": "discussions",
}
# 외부도구(LTI) 탭 라벨 -> 섹션 이름
_TOOL_SECTIONS = {
    "주차학습": "weekly",
    "강의/출결": "attendance",
    "게시판": "boards",
    "열린게시판": "boards",
    "강의교재": "textbooks",
}
# 사이트엔 있으나 아직 읽는 법을 모르는 섹션 (없으면 빈 dict)
UNSUPPORTED: dict[str, str] = {}


def _files(course_id: int) -> dict:
    """파일 탭 + 본문 링크로만 걸린 자료를 함께 담는다."""
    scan = links.scan_course_links(course_id)
    return {
        "files": cc.list_files(course_id),
        "linked_only": [f for f in scan["files"] if not f["in_files_list"]],
        "external_links": scan["external_links"],
    }


FETCHERS: dict[str, Callable[[int], Any]] = {
    "home": cc.get_course_home,
    "announcements": cc.list_announcements,
    "syllabus": cc.get_syllabus,
    "people": cc.list_people,
    "modules": cc.list_modules,
    "assignments": cc.list_assignments,
    "grades": cc.get_submissions,
    "files": _files,
    "pages": cc.list_pages,
    "quizzes": cc.list_quizzes,
    "discussions": cc.list_discussions,
    "weekly": weekly.list_weekly_items,
    "attendance": weekly.get_attendance,
    "boards": boards.list_board_posts,
    "textbooks": cc.get_textbooks,
}


def _count(section: str, data: Any) -> int | None:
    """섹션 데이터의 '건수'를 센다(화면에 몇 개가 있는지 감 잡는 용도)."""
    if data is None:
        return None
    if isinstance(data, list):
        if section == "boards":
            return sum(len(g.get("posts") or []) for g in data)
        return len(data)
    if section == "files":
        return len(data.get("files") or []) + len(data.get("linked_only") or [])
    if section == "attendance":
        return data.get("total")
    if section == "textbooks":
        return len(data.get("textbooks") or [])
    return 1


def _empty(section: str, data: Any, count: int | None) -> bool:
    if data is None:
        return True
    return not count if isinstance(count, int) else False


def course_sections(course_id: int) -> list[dict]:
    """이 강의의 탭 구성과, 각 탭을 읽을 섹션 이름을 짝지어 돌려준다."""
    out = []
    for t in lti.get_tabs(course_id):
        if t["hidden"]:
            continue
        if t["tool_id"]:
            section = _TOOL_SECTIONS.get((t["label"] or "").strip())
        else:
            section = _TAB_SECTIONS.get(t["id"])
        out.append({"tab": t["label"], "tab_id": t["id"], "section": section,
                    "tool_id": t["tool_id"]})
    return out


def get_course_snapshot(
    course_id: int, sections: list[str] | None = None
) -> dict:
    """강의의 모든 탭을 훑어 한 번에 읽는다(사이트 화면 = 이 결과).

    sections 로 일부만 고를 수 있다(예 ['grades','attendance']). 결과의 tabs 에는
    탭별 상태(ok/empty/unsupported/error)와 건수가 들어가 커버리지가 그대로 보인다.
    """
    plan = course_sections(course_id)
    course = cc.get_canvas().get_course(course_id)
    result: dict[str, Any] = {
        "course_id": course_id,
        "course": getattr(course, "name", None),
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tabs": [],
        "sections": {},
    }

    done: set[str] = set()
    for item in plan:
        section = item["section"]
        row = {"tab": item["tab"], "section": section}
        if section is None:
            row["status"] = "unmapped"
        elif section in UNSUPPORTED:
            row["status"] = "unsupported"
            row["note"] = UNSUPPORTED[section]
        elif sections is not None and section not in sections:
            row["status"] = "skipped"
        elif section in done:
            row["status"] = "merged"  # 게시판/열린게시판처럼 한 섹션에 합쳐진 탭
        else:
            t0 = time.time()
            try:
                data = FETCHERS[section](course_id)
                count = _count(section, data)
                result["sections"][section] = data
                row["status"] = "empty" if _empty(section, data, count) else "ok"
                row["count"] = count
            except Exception as e:
                row["status"] = "error"
                row["error"] = f"{type(e).__name__}: {e}"
            row["sec"] = round(time.time() - t0, 1)
            done.add(section)
        result["tabs"].append(row)

    ok = [r for r in result["tabs"] if r["status"] in ("ok", "empty", "merged")]
    result["coverage"] = {
        "tabs_total": len(result["tabs"]),
        "read": len(ok),
        "skipped": sum(1 for r in result["tabs"] if r["status"] == "skipped"),
        "not_read": [
            {"tab": r["tab"], "status": r["status"], "note": r.get("note") or r.get("error")}
            for r in result["tabs"] if r["status"] not in ("ok", "empty", "merged", "skipped")
        ],
    }
    return result
