"""Canvas LMS 클라이언트 래퍼 (SNU eTL / 표준 Canvas API).

.env 에서 ETL_BASE_URL, ETL_TOKEN 을 읽어 canvasapi.Canvas 인스턴스를 생성한다.
각 헬퍼는 canvasapi 객체를 LLM 친화적인 평범한 dict 로 변환해 반환한다.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from canvasapi import Canvas
from dotenv import load_dotenv

# cron 등 cwd 가 다른 환경에서도 확실히 찾도록 모듈 옆 .env 를 명시적으로 로드
load_dotenv(Path(__file__).resolve().parent / ".env")


@lru_cache(maxsize=1)
def get_canvas() -> Canvas:
    """환경변수로 인증된 Canvas 인스턴스를 반환한다 (프로세스당 1회 생성)."""
    base_url = os.environ.get("ETL_BASE_URL")
    token = os.environ.get("ETL_TOKEN")
    if not base_url or not token:
        raise RuntimeError(
            "ETL_BASE_URL / ETL_TOKEN 환경변수가 필요합니다 (.env 확인)."
        )
    return Canvas(base_url.rstrip("/"), token)


def _get(obj: Any, *names: str) -> Any:
    """canvasapi 객체에서 첫 번째로 존재하는 속성 값을 반환 (없으면 None)."""
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


# --- 변환 헬퍼 (canvasapi 객체 -> dict) ---------------------------------------


def course_to_dict(c: Any) -> dict:
    enrollments = _get(c, "enrollments") or []
    grade = None
    if enrollments:
        e = enrollments[0]
        grade = {
            "current_score": e.get("computed_current_score"),
            "final_score": e.get("computed_final_score"),
            "current_grade": e.get("computed_current_grade"),
        }
    return {
        "id": c.id,
        "name": _get(c, "name"),
        "course_code": _get(c, "course_code"),
        "term_id": _get(c, "enrollment_term_id"),
        "workflow_state": _get(c, "workflow_state"),
        "start_at": _get(c, "start_at"),
        "end_at": _get(c, "end_at"),
        "grade": grade,
    }


def assignment_to_dict(a: Any) -> dict:
    return {
        "id": a.id,
        "course_id": _get(a, "course_id"),
        "name": _get(a, "name"),
        "due_at": _get(a, "due_at"),
        "lock_at": _get(a, "lock_at"),
        "unlock_at": _get(a, "unlock_at"),
        "points_possible": _get(a, "points_possible"),
        "submission_types": _get(a, "submission_types"),
        "has_submitted": bool(_get(a, "has_submitted_submissions")),
        "html_url": _get(a, "html_url"),
        "updated_at": _get(a, "updated_at"),
    }


def announcement_to_dict(d: Any) -> dict:
    return {
        "id": d.id,
        "title": _get(d, "title"),
        "posted_at": _get(d, "posted_at"),
        "author": (_get(d, "author") or {}).get("display_name"),
        "message": _get(d, "message"),
        "html_url": _get(d, "html_url"),
    }


def discussion_to_dict(d: Any) -> dict:
    """토론 게시판(열린게시판) 글 1건을 dict 로 변환한다.

    eTL 은 is_announcement 를 안 내려주므로 공지/일반 구분은 호출부에 맡기고,
    답글 수(reply_count)·최근 답글 시각 등 목록에 유용한 필드를 함께 담는다.
    """
    return {
        "id": d.id,
        "title": _get(d, "title"),
        "author": (_get(d, "author") or {}).get("display_name") or _get(d, "user_name"),
        "posted_at": _get(d, "posted_at"),
        "last_reply_at": _get(d, "last_reply_at"),
        "reply_count": _get(d, "discussion_subentry_count"),
        "pinned": _get(d, "pinned"),
        "locked": _get(d, "locked"),
        "message": _get(d, "message"),
        "html_url": _get(d, "html_url"),
    }


def discussion_entry_to_dict(e: Any) -> dict:
    """토론 글의 댓글/답글 1건을 dict 로 변환한다."""
    return {
        "id": _get(e, "id"),
        "author": _get(e, "user_name"),
        "created_at": _get(e, "created_at"),
        "updated_at": _get(e, "updated_at"),
        "message": _get(e, "message"),
    }


def file_to_dict(f: Any) -> dict:
    return {
        "id": f.id,
        "display_name": _get(f, "display_name", "filename"),
        "content_type": _get(f, "content-type", "content_type"),
        "size": _get(f, "size"),
        "url": _get(f, "url"),
        "updated_at": _get(f, "updated_at"),
    }


def module_item_to_dict(it: Any) -> dict:
    return {
        "id": _get(it, "id"),
        "title": _get(it, "title"),
        "type": _get(it, "type"),  # File | ExternalUrl | Assignment | Page | ...
        "content_id": _get(it, "content_id"),  # File 이면 파일 id
        "html_url": _get(it, "html_url"),  # eTL 내 링크
        "external_url": _get(it, "external_url"),  # ExternalUrl 이면 외부 링크
    }


def module_to_dict(m: Any) -> dict:
    return {
        "id": m.id,
        "name": _get(m, "name"),  # 예: '1주차 모듈'
        "position": _get(m, "position"),
        "items": [module_item_to_dict(it) for it in m.get_module_items()],
    }


def todo_to_dict(t: Any) -> dict:
    assignment = _get(t, "assignment") or {}
    return {
        "type": _get(t, "type"),
        "course_id": _get(t, "course_id"),
        "title": assignment.get("name") or _get(t, "title"),
        "due_at": assignment.get("due_at"),
        "points_possible": assignment.get("points_possible"),
        "html_url": _get(t, "html_url"),
    }


# --- 데이터 조회 함수 ---------------------------------------------------------


def list_courses(active_only: bool = True, current_term_only: bool = False) -> list[dict]:
    """수강 강의 목록. current_term_only=True 면 이번 학기 과목만 남긴다.

    eTL 은 강의 start_at/end_at 을 안 채우지만 term_id 가 학기 순서대로 커지므로
    (2025-2=138 < 2026-3(여름)=163 < 2026-2(가을)=164), 가장 큰 term_id 를 이번 학기로 본다.
    """
    canvas = get_canvas()
    kwargs: dict[str, Any] = {"include": ["total_scores"]}
    if active_only:
        kwargs["enrollment_state"] = "active"
    courses = [course_to_dict(c) for c in canvas.get_courses(**kwargs)]
    if current_term_only:
        terms = [c["term_id"] for c in courses if c["term_id"]]
        if terms:
            latest = max(terms)
            courses = [c for c in courses if c["term_id"] == latest]
    return courses


def list_assignments(course_id: int) -> list[dict]:
    course = get_canvas().get_course(course_id)
    return [assignment_to_dict(a) for a in course.get_assignments()]


def list_announcements(course_id: int) -> list[dict]:
    course = get_canvas().get_course(course_id)
    topics = course.get_discussion_topics(only_announcements=True)
    return [announcement_to_dict(d) for d in topics]


def list_discussions(course_id: int) -> list[dict]:
    """강의의 토론 게시판(열린게시판) 글 목록을 반환한다.

    공지(announcement)와 달리 일반 토론 토픽까지 포함한다. eTL 에서 '열린게시판'에
    올라오는 글이 여기에 잡힌다. 본문 전체와 답글은 get_discussion 으로 본다.

    주의: eTL 은 only_announcements 파라미터를 명시적으로 보낼 때만 토픽을 돌려준다
    (인자 생략 시 빈 목록을 반환하는 비표준 동작). 그래서 False 를 명시한다.
    """
    course = get_canvas().get_course(course_id)
    topics = course.get_discussion_topics(only_announcements=False)
    return [discussion_to_dict(d) for d in topics]


def get_discussion(
    course_id: int, topic_id: int, include_replies: bool = True
) -> dict:
    """토론 글 1건의 본문과 댓글/답글(중첩 포함)을 반환한다.

    include_replies=False 면 본문만 빠르게 가져온다.
    """
    course = get_canvas().get_course(course_id)
    topic = course.get_discussion_topic(topic_id)
    result = discussion_to_dict(topic)
    if not include_replies:
        return result
    entries = []
    for e in topic.get_topic_entries():
        ed = discussion_entry_to_dict(e)
        replies = []
        try:
            for r in e.get_replies():
                replies.append(discussion_entry_to_dict(r))
        except Exception:
            pass  # 답글 조회 권한/형식 문제 시 본문만 유지
        ed["replies"] = replies
        entries.append(ed)
    result["entries"] = entries
    return result


def list_files(course_id: int) -> list[dict]:
    course = get_canvas().get_course(course_id)
    return [file_to_dict(f) for f in course.get_files()]


# eTL '강의계획서' 탭은 sugang.snu.ac.kr 공식 강의계획서를 iframe 으로 띄운다.
# 그 iframe URL(cc103.action) 에 과목코드·학기·분반이 들어 있고, 실제 내용은
# cc103ajax.action 이 JSON 으로 돌려준다(SSO 불필요, Referer 헤더만 필요).
_SUGANG = "https://sugang.snu.ac.kr/sugang/cc"
_SUGANG_SYLLABUS_RE = re.compile(
    r"https?://sugang\.snu\.ac\.kr/sugang/cc/cc103\.action\?[^\s\"'<>]+"
)
# 평가비율 키 -> 한글 라벨
_MRKS_LABELS = {
    "attendance": "출석", "homeWork": "과제", "mid": "중간고사",
    "final": "기말고사", "quiz": "퀴즈", "attitude": "태도", "etc": "기타",
}


def _strip_html(s: str | None) -> str | None:
    """<br> 은 줄바꿈으로, 나머지 태그는 제거해 평문으로 만든다."""
    if not s:
        return None
    import html as _h
    t = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = _h.unescape(t)
    return re.sub(r"\n{3,}", "\n\n", t).strip() or None


def _fetch_sugang_syllabus(params: dict) -> dict | None:
    """cc103ajax.action 에서 강의계획서 본문(JSON)을 받아 정리해 반환한다.

    Referer 헤더가 없으면 sugang 이 직접접근으로 보고 빈 페이지를 주므로 꼭 넣는다.
    실패 시 None 을 반환(링크만 제공으로 폴백).
    """
    import requests
    try:
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://sugang.snu.ac.kr/",
            "X-Requested-With": "XMLHttpRequest",
        })
        s.get(f"{_SUGANG}/cc103.action", params=params, timeout=20)  # 세션/Referer 맥락
        d = s.post(f"{_SUGANG}/cc103ajax.action", data=params, timeout=20).json()
    except Exception:
        return None

    plan = (d.get("LISTTAB03_LIST_PLAN") or [{}])[0]
    mrks = d.get("LISTTAB03_LIST_MRKS") or {}
    tab1 = d.get("LISTTAB01") or {}
    tab3 = d.get("LISTTAB03") or {}
    evaluation = {
        _MRKS_LABELS[k]: mrks[k]
        for k in _MRKS_LABELS
        if isinstance(mrks.get(k), (int, float)) and mrks.get(k)
    }
    attachments = [
        {
            "name": a.get("korFileNm"),
            "attach_no": a.get("korAttachNo"),
        }
        for a in (d.get("LISTTAB03_LIST_ATTACH") or [])
        if a.get("korFileNm")
    ]
    return {
        "title": tab1.get("sbjtCdAndNm"),          # 'E11.116 동서양의 종교적 지혜'
        "department": tab1.get("departmentKorNm"),
        "grading": tab3.get("mrksRelevalYnNm"),     # 상대평가/절대평가
        "grade_scale": tab1.get("mrksGvMthd"),      # A~F 등
        "times": d.get("ltTime"),
        "rooms": d.get("ltRoom"),
        "lesson_types": d.get("ltType"),
        "evaluation": evaluation,                   # {'출석':20,'과제':30,...}
        "evaluation_total": (d.get("LISTTAB03_SUM_OF_MRKS") or {}).get("sumOfMrks"),
        "attendance_rule": _strip_html(plan.get("attendRegulKorCtnt")),
        "ai_policy": _strip_html(tab3.get("genrAiUtlzKorCtnt")),
        "plan": _strip_html(plan.get("ltPlanCtnt")),  # 회차별 강의계획
        "attachments": attachments,
    }


def _sugang_params(course_id: int) -> tuple[dict, str | None, Any]:
    """eTL syllabus_body 에 박힌 sugang cc103 URL 에서 과목 파라미터를 뽑는다.

    (params, url, course) 를 돌려준다 — 강의계획서·강의교재가 같은 파라미터를 쓴다.
    """
    course = get_canvas().get_course(course_id, include=["syllabus_body"])
    body = _get(course, "syllabus_body") or ""
    m = _SUGANG_SYLLABUS_RE.search(body)
    url = m.group(0).replace("&amp;", "&").rstrip("&") if m else None
    params = dict(re.findall(r"[?&]([^=&]+)=([^&]*)", url)) if url else {}
    return params, url, course


def get_textbooks(course_id: int) -> dict:
    """'강의교재' 탭 — 교재/부교재 목록(ISBN·출판사·도서관 소장 링크)을 반환한다.

    강의교재 탭은 LearningX LTI 지만 실제로는 sugang 의 cc103bk.action 을 iframe 으로
    띄우는 것이라, 강의계획서와 같은 파라미터로 cc103bkajax.action 에서 JSON 을 받는다
    (SNU 로그인 불필요, Referer 헤더만 필요).
    """
    import requests

    params, _url, course = _sugang_params(course_id)
    out = {"course_id": course_id, "course": _get(course, "name"), "textbooks": []}
    if not params:
        out["note"] = "강의계획서 링크가 없어 교재 정보를 찾지 못했습니다."
        return out
    try:
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0",
                          "Referer": "https://sugang.snu.ac.kr/",
                          "X-Requested-With": "XMLHttpRequest"})
        d = s.post(f"{_SUGANG}/cc103bkajax.action",
                   data={**params, "lang_knd": "ko"}, timeout=20).json()
    except Exception as e:
        out["note"] = f"교재 조회 실패: {e}"
        return out
    out["textbooks"] = [
        {
            "kind": b.get("ltTeachmFgNm"),          # 주교재 / 부교재
            "type": b.get("ltTeachmTypeFgNm"),      # 도서 / 논문 등
            "title": b.get("ltTeachmNm"),
            "author": b.get("ltTeachmAuthNm"),
            "publisher": b.get("ltTeachmPubcoNm"),
            "year": b.get("ltTeachmPublshYy"),
            "isbn": b.get("ltTeachmIsbnNo"),
            "in_library": b.get("librryPosessYn") == "Y",
            "library_url": b.get("ltTeachmLibrryUrl"),
        }
        for b in (d.get("LISTTAB03_LIST_TEACHM_BOOK") or [])
    ]
    return out


def get_syllabus(course_id: int, fetch_content: bool = True) -> dict:
    """공식 강의계획서(SNU 수강신청 시스템)를 조회한다.

    eTL '강의계획서' 탭의 syllabus_body 에 박힌 cc103.action URL 에서 과목코드·학기·
    분반을 뽑아, sugang 의 cc103ajax.action 에서 실제 내용(강의시간·평가비율·회차별
    계획·첨부파일 등)을 JSON 으로 받아 정리해 돌려준다. SNU SSO 로그인은 필요 없다.
    fetch_content=False 면 본문 없이 링크/메타만 빠르게 반환한다.
    """
    params, url, course = _sugang_params(course_id)

    out = {
        "course_id": course_id,
        "course_name": _get(course, "name"),
        "course_code": params.get("sbjtCd"),  # 예: E11.116
        "lt_no": params.get("ltNo"),          # 분반 (001 등)
        "year": params.get("openSchyy"),
        "official_url": url,
    }
    if fetch_content and url:
        content = _fetch_sugang_syllabus(params)
        if content:
            out.update(content)
        else:
            out["note"] = "본문 조회 실패 — official_url 로 직접 확인하세요."
    return out


def list_modules(course_id: int) -> list[dict]:
    """강의의 주차/모듈 구조와 각 모듈의 항목을 순서대로 반환한다."""
    course = get_canvas().get_course(course_id)
    return [module_to_dict(m) for m in course.get_modules()]


# --- 사이트 화면 전체를 읽기 위한 보강 (페이지·제출·성적·수강생·퀴즈·쪽지) --------


def _raw(path: str, **params) -> Any:
    """canvasapi 가 다루기 번거로운 엔드포인트를 직접 GET 한다(리스트/딕트 그대로).

    include[] 같은 반복 파라미터를 그대로 넘기기 위해 requests 를 쓴다.
    """
    import requests

    base = (os.environ.get("ETL_BASE_URL") or "").rstrip("/")
    token = os.environ.get("ETL_TOKEN")
    r = requests.get(
        f"{base}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params, timeout=40,
    )
    if not r.ok:
        return None
    try:
        return r.json()
    except Exception:
        return None


def list_pages(course_id: int, include_body: bool = True) -> list[dict]:
    """'페이지' 탭의 위키 페이지 목록(원하면 본문 포함)을 반환한다.

    강의 안내·평가 방법 등이 페이지로만 올라오는 과목이 있어 본문까지 봐야 내용이 온전하다.
    """
    course = get_canvas().get_course(course_id)
    out = []
    try:
        pages = list(course.get_pages())
    except Exception:
        return out
    for p in pages:
        row = {
            "url": _get(p, "url"),
            "title": _get(p, "title"),
            "updated_at": _get(p, "updated_at"),
            "html_url": _get(p, "html_url"),
        }
        if include_body:
            try:
                full = course.get_page(p.url)
                row["body_html"] = _get(full, "body")
                row["body"] = _strip_html(_get(full, "body"))
            except Exception:
                row["body"] = None
        out.append(row)
    return out


def get_page(course_id: int, page_url: str) -> dict:
    """페이지 1건의 본문(평문 + 원본 HTML)을 반환한다. page_url 은 list_pages 의 url."""
    page = get_canvas().get_course(course_id).get_page(page_url)
    body = _get(page, "body")
    return {
        "url": _get(page, "url"),
        "title": _get(page, "title"),
        "updated_at": _get(page, "updated_at"),
        "html_url": _get(page, "html_url"),
        "body": _strip_html(body),
        "body_html": body,
    }


def get_submissions(course_id: int, only_graded: bool = False) -> list[dict]:
    """내 과제 제출 상태·점수·교수 코멘트를 반환한다('성적' 화면의 실제 내용).

    Canvas 의 get_grades 는 총점만 주지만, 여기서는 과제별 점수·만점·제출 시각·
    지각 여부·채점자 코멘트까지 본다. only_graded=True 면 채점된 것만.
    """
    rows = _raw(
        f"/api/v1/courses/{course_id}/students/submissions",
        **{"student_ids[]": "self",
           "include[]": ["submission_comments", "assignment"],
           "per_page": 100},
    ) or []
    out = []
    for s in rows:
        if only_graded and s.get("score") is None:
            continue
        a = s.get("assignment") or {}
        out.append({
            "assignment_id": s.get("assignment_id"),
            "assignment": a.get("name"),
            "due_at": a.get("due_at"),
            "points_possible": a.get("points_possible"),
            "score": s.get("score"),
            "grade": s.get("grade"),
            "submitted_at": s.get("submitted_at"),
            "graded_at": s.get("graded_at"),
            "late": s.get("late"),
            "missing": s.get("missing"),
            "state": s.get("workflow_state"),
            "attempt": s.get("attempt"),
            "comments": [
                {"author": c.get("author_name"), "created_at": c.get("created_at"),
                 "comment": c.get("comment")}
                for c in (s.get("submission_comments") or [])
            ],
        })
    return out


def list_people(course_id: int, include_students: bool = False) -> list[dict]:
    """'수강생' 탭 — 교수·조교(기본)와, 원하면 수강생까지 반환한다."""
    types = ["teacher", "ta", "designer"] + (["student"] if include_students else [])
    rows = _raw(f"/api/v1/courses/{course_id}/users",
                **{"enrollment_type[]": types, "include[]": ["enrollments"],
                   "per_page": 100}) or []
    role_kr = {"TeacherEnrollment": "교수", "TaEnrollment": "조교",
               "StudentEnrollment": "학생", "DesignerEnrollment": "설계자"}
    return [
        {"id": u.get("id"), "name": u.get("name"),
         "roles": [role_kr.get(e.get("type"), e.get("type"))
                   for e in (u.get("enrollments") or [])]}
        for u in rows
    ]


def list_quizzes(course_id: int) -> list[dict]:
    """'퀴즈' 탭의 퀴즈/시험 목록. 퀴즈 메뉴가 없는 과목이면 빈 목록."""
    rows = _raw(f"/api/v1/courses/{course_id}/quizzes", per_page=100)
    if not isinstance(rows, list):
        return []
    return [
        {"id": q.get("id"), "title": q.get("title"), "type": q.get("quiz_type"),
         "due_at": q.get("due_at"), "unlock_at": q.get("unlock_at"),
         "lock_at": q.get("lock_at"), "points_possible": q.get("points_possible"),
         "time_limit": q.get("time_limit"), "allowed_attempts": q.get("allowed_attempts"),
         "html_url": q.get("html_url"), "description": _strip_html(q.get("description"))}
        for q in rows
    ]


def get_course_home(course_id: int) -> dict:
    """'홈' 탭이 무엇을 보여주는지(default_view)와 첫 화면 페이지 본문을 반환한다."""
    c = _raw(f"/api/v1/courses/{course_id}") or {}
    out = {"course_id": course_id, "name": c.get("name"),
           "default_view": c.get("default_view"),  # modules|wiki|syllabus|assignments|feed
           "public_description": c.get("public_description")}
    fp = _raw(f"/api/v1/courses/{course_id}/front_page")
    if isinstance(fp, dict) and fp.get("body") is not None:
        out["front_page"] = {"title": fp.get("title"), "body": _strip_html(fp.get("body"))}
    return out


def list_conversations(limit: int = 25, include_messages: bool = False) -> list[dict]:
    """받은 쪽지함(Canvas conversations) 목록을 반환한다.

    include_messages=True 면 각 대화의 메시지 본문까지 가져온다(대화당 요청 1회).
    """
    rows = _raw("/api/v1/conversations", per_page=limit) or []
    out = []
    for c in rows[:limit]:
        row = {
            "id": c.get("id"),
            "subject": c.get("subject"),
            "last_message_at": c.get("last_message_at"),
            "message_count": c.get("message_count"),
            "state": c.get("workflow_state"),  # unread|read|archived
            "context": c.get("context_name"),
            "participants": [p.get("name") for p in (c.get("participants") or [])],
            "preview": c.get("last_message"),
        }
        if include_messages:
            d = _raw(f"/api/v1/conversations/{c.get('id')}") or {}
            row["messages"] = [
                {"author_id": m.get("author_id"), "created_at": m.get("created_at"),
                 "body": m.get("body")}
                for m in (d.get("messages") or [])
            ]
        out.append(row)
    return out


def get_upcoming() -> list[dict]:
    """마감 임박 과제/할 일 목록 (Canvas to-do)."""
    canvas = get_canvas()
    return [todo_to_dict(t) for t in canvas.get_todo_items()]


def get_grades(active_only: bool = True) -> list[dict]:
    """강의별 현재 성적 요약."""
    out = []
    for c in list_courses(active_only=active_only):
        out.append(
            {
                "course_id": c["id"],
                "name": c["name"],
                "grade": c["grade"],
            }
        )
    return out


def whoami() -> dict:
    u = get_canvas().get_current_user()
    return {"id": u.id, "name": _get(u, "name"), "login_id": _get(u, "login_id")}
