"""'주차학습'(LearningX modulebuilder) — 주차별 학습 항목과 내 진행/출결 상태.

eTL 화면의 '주차학습' 탭은 Canvas 모듈을 LearningX 가 다시 그린 것이라, 항목 구성은
Canvas 모듈과 같지만 **학생 본인의 상태**(완료 여부·출결 인정 여부·마감)가 함께 온다.
Canvas API 로는 안 보이는 정보라 여기서만 읽을 수 있다.

  GET /learningx/api/v1/courses/{cid}/modules
      -> [{week_position, title, module_items: [{content_type, completed,
           attendance_status, content_data{due_at,...}}]}]

content_type: attachment(파일) | external_url(외부링크) | assignment(과제) |
              quiz(퀴즈) | attendance_item(영상강의/출결) | wiki_page(페이지)
"""

from __future__ import annotations

from typing import Any

import lti

# 출결 상태 코드 -> 한글
ATTENDANCE_LABELS = {
    "attendance": "출석",
    "late": "지각",
    "absent": "결석",
    "early_leave": "조퇴",
    "none": "미인정",
}


def _item(module: dict, it: dict) -> dict:
    cd = it.get("content_data") or {}
    icd = cd.get("item_content_data") or {}
    status = it.get("attendance_status")
    return {
        "week": module.get("week_position"),
        "module": module.get("title"),
        "position": it.get("position"),
        "item_id": it.get("module_item_id"),
        "type": it.get("content_type"),
        "title": it.get("title") or cd.get("title"),
        "content_id": it.get("content_id"),   # attachment=Canvas 파일 id, assignment=과제 id 등
        "url": it.get("url"),                 # external_url 이면 실제 링크
        "completed": it.get("completed"),
        "attendance_status": status,
        "attendance": ATTENDANCE_LABELS.get(status, status),
        "due_at": cd.get("due_at"),
        "late_at": cd.get("late_at"),
        "unlock_at": cd.get("unlock_at") or module.get("unlock_at"),
        "duration_sec": icd.get("duration"),
        "lcms_content_id": icd.get("content_id"),
    }


def list_weekly_items(course_id: int) -> list[dict]:
    """주차학습 탭의 모든 항목을 주차·순서대로 평탄화해 반환한다(내 완료/출결 상태 포함)."""
    lx = lti.launch_kind(course_id, "weekly")
    out = []
    for m in (lx.api(f"/courses/{course_id}/modules") or []):
        for it in m.get("module_items", []):
            out.append(_item(m, it))
    return out


def get_attendance(course_id: int) -> dict:
    """영상강의(출결 대상) 항목의 내 출결 상태를 모아 요약과 함께 반환한다."""
    items = [i for i in list_weekly_items(course_id) if i["type"] == "attendance_item"]
    summary: dict[str, int] = {}
    for i in items:
        key = i["attendance"] or "미확인"
        summary[key] = summary.get(key, 0) + 1
    return {
        "course_id": course_id,
        "summary": summary,
        "total": len(items),
        "items": [
            {k: i[k] for k in ("week", "title", "attendance", "completed", "due_at",
                               "duration_sec", "item_id")}
            for i in items
        ],
    }


def progress_summary(course_id: int) -> dict:
    """주차학습 전체의 완료 여부 요약(완료 판정이 가능한 항목만 센다)."""
    items = list_weekly_items(course_id)
    determinable = [i for i in items if i["completed"] is not None]
    done = sum(1 for i in determinable if i["completed"])
    by_type: dict[str, int] = {}
    for i in items:
        by_type[i["type"]] = by_type.get(i["type"], 0) + 1
    return {
        "course_id": course_id,
        "items_total": len(items),
        "by_type": by_type,
        "completable": len(determinable),
        "completed": done,
        "incomplete": [
            {"week": i["week"], "title": i["title"], "type": i["type"], "due_at": i["due_at"]}
            for i in determinable if not i["completed"]
        ],
    }
