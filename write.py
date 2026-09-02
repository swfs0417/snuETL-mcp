"""쓰기(상호작용) 도구 — 과제 제출·토론 답글·게시판 글/댓글.

기본은 **비활성**이다. `.env` 에 `ETL_ENABLE_WRITE=1` 이 있어야 동작하고(서버도 이때만
도구를 등록한다), 모든 함수는 `confirm=True` 를 받아야 실제로 보낸다. confirm 없이
부르면 "무엇이 어디에 올라가는지" 미리보기만 돌려주므로, 사람이 확인한 뒤 다시 부르면 된다.

되돌리기 가능 여부가 다르니 주의:
  - 게시판 글/댓글: 삭제 가능(delete_board_post / delete_board_comment).
  - 토론 답글: eTL 설정에 따라 삭제가 막힐 수 있다.
  - 과제 제출: **되돌릴 수 없다**(재제출은 새 시도로 쌓인다).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import boards
import canvas_client as cc
import lti


class WriteDisabled(RuntimeError):
    """쓰기 기능이 꺼져 있을 때."""


def write_enabled() -> bool:
    return (os.environ.get("ETL_ENABLE_WRITE") or "").strip() in ("1", "true", "True")


def _require_enabled() -> None:
    if not write_enabled():
        raise WriteDisabled(
            "쓰기 기능이 꺼져 있습니다. .env 에 ETL_ENABLE_WRITE=1 을 넣고 MCP 서버를 다시 시작하세요."
        )


def _preview(action: str, where: str, what: dict, reversible: str) -> dict:
    """confirm=False 일 때 돌려줄 미리보기."""
    return {
        "confirmed": False,
        "action": action,
        "target": where,
        "payload": what,
        "reversible": reversible,
        "next": "내용이 맞으면 같은 호출에 confirm=True 를 넣어 다시 실행하세요.",
    }


def _check_files(file_paths: Optional[list[str]]) -> list[Path]:
    out = []
    for p in file_paths or []:
        path = Path(p).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")
        out.append(path)
    return out


# --- 과제 제출 ----------------------------------------------------------------


def submit_assignment(
    course_id: int,
    assignment_id: int,
    text: Optional[str] = None,
    url: Optional[str] = None,
    file_paths: Optional[list[str]] = None,
    confirm: bool = False,
) -> dict:
    """과제를 제출한다(파일/텍스트/URL). 되돌릴 수 없으므로 confirm=True 가 필요하다.

    과제가 허용하는 제출 형식(submission_types)에 맞는 인자를 하나 골라 준다.
    """
    _require_enabled()
    course = cc.get_canvas().get_course(course_id)
    assignment = course.get_assignment(assignment_id)
    allowed = getattr(assignment, "submission_types", []) or []
    files = _check_files(file_paths)

    if files:
        kind = "online_upload"
    elif text:
        kind = "online_text_entry"
    elif url:
        kind = "online_url"
    else:
        raise ValueError("text, url, file_paths 중 하나는 있어야 합니다.")
    if kind not in allowed:
        raise ValueError(
            f"이 과제가 허용하는 제출 형식은 {allowed} 입니다 (요청: {kind})."
        )

    what = {
        "type": kind,
        "files": [f.name for f in files] or None,
        "text": (text[:200] + "…") if text and len(text) > 200 else text,
        "url": url,
    }
    where = (
        f"{getattr(course, 'name', course_id)} / {getattr(assignment, 'name', assignment_id)}"
        f" (마감 {getattr(assignment, 'due_at', None)})"
    )
    if not confirm:
        return _preview("과제 제출", where, what, "되돌릴 수 없음")

    submission: dict[str, Any] = {"submission_type": kind}
    if kind == "online_upload":
        file_ids = []
        for f in files:
            ok, res = assignment.upload_to_submission(str(f))
            if not ok or "id" not in res:
                raise RuntimeError(f"파일 업로드 실패: {f.name} ({res})")
            file_ids.append(res["id"])
        submission["file_ids"] = file_ids
    elif kind == "online_text_entry":
        submission["body"] = text
    else:
        submission["url"] = url

    result = assignment.submit(submission)
    return {
        "confirmed": True,
        "action": "과제 제출",
        "target": where,
        "submission_id": getattr(result, "id", None),
        "attempt": getattr(result, "attempt", None),
        "submitted_at": getattr(result, "submitted_at", None),
        "late": getattr(result, "late", None),
        "preview_url": getattr(result, "preview_url", None),
    }


# --- 토론(Canvas) -------------------------------------------------------------


def reply_discussion(
    course_id: int,
    topic_id: int,
    message: str,
    parent_entry_id: Optional[int] = None,
    confirm: bool = False,
) -> dict:
    """토론 글에 답글을 단다. parent_entry_id 를 주면 그 댓글에 대한 대댓글이 된다."""
    _require_enabled()
    course = cc.get_canvas().get_course(course_id)
    topic = course.get_discussion_topic(topic_id)
    where = f"{getattr(course, 'name', course_id)} / 토론 '{getattr(topic, 'title', topic_id)}'"
    if not confirm:
        return _preview("토론 답글", where, {"message": message,
                                          "parent_entry_id": parent_entry_id},
                        "eTL 설정에 따라 삭제가 막힐 수 있음")

    if parent_entry_id:
        entry = next(
            (e for e in topic.get_topic_entries() if e.id == parent_entry_id), None
        )
        if entry is None:
            raise ValueError(f"답글 대상 댓글을 찾지 못했습니다 (id={parent_entry_id}).")
        created = entry.post_reply(message=message)
    else:
        created = topic.post_entry(message=message)
    return {
        "confirmed": True, "action": "토론 답글", "target": where,
        "entry_id": getattr(created, "id", None),
        "created_at": getattr(created, "created_at", None),
    }


def create_discussion(
    course_id: int, title: str, message: str, confirm: bool = False
) -> dict:
    """토론 게시판에 새 글을 만든다(학생 작성이 허용된 강의에서만 된다)."""
    _require_enabled()
    course = cc.get_canvas().get_course(course_id)
    where = f"{getattr(course, 'name', course_id)} / 토론"
    if not confirm:
        return _preview("토론 새 글", where, {"title": title, "message": message},
                        "작성자 본인 삭제 가능(설정에 따라 다름)")
    topic = course.create_discussion_topic(title=title, message=message)
    return {
        "confirmed": True, "action": "토론 새 글", "target": where,
        "topic_id": getattr(topic, "id", None),
        "url": getattr(topic, "html_url", None),
    }


# --- 게시판(LearningX) --------------------------------------------------------


def _board_ctx(course_id: int, board_id: str) -> tuple[Any, dict]:
    """(런치 세션, 보드 정보) 를 찾는다. 없는 board_id 면 에러."""
    entries = [b for b in boards.list_boards(course_id) if b["board_id"] == str(board_id)]
    if not entries:
        raise ValueError(f"게시판을 찾지 못했습니다 (course={course_id}, board={board_id}).")
    b = entries[0]
    if b["backend"] != "learningx_board":
        raise NotImplementedError(
            "'열린게시판'(OpenBoard) 쓰기는 아직 지원하지 않습니다 — '게시판' 쪽만 됩니다."
        )
    return lti.launch(course_id, b["tool_id"]), b


def _lx_post(lx, path: str, data: dict, files: list[Path] | None = None,
             field: str = "attachment_files[]") -> Any:
    """LearningX 에 multipart/form-data 로 POST 한다(웹 UI 와 같은 형식)."""
    payload = [(k, (None, v)) for k, v in data.items()]
    handles = []
    try:
        for f in files or []:
            fh = open(f, "rb")
            handles.append(fh)
            payload.append((field, (f.name, fh)))
        r = lx.session.post(
            f"{lx.base}/learningx/api/v1{path}",
            headers={"Authorization": f"Bearer {lx.xn}", "Accept": "application/json"},
            files=payload, timeout=60,
        )
    finally:
        for fh in handles:
            fh.close()
    if r.status_code >= 400:
        raise RuntimeError(f"게시판 요청 실패 {r.status_code}: {r.text[:300]}")
    try:
        return r.json()
    except Exception:
        return {"status": r.status_code}


def create_board_post(
    course_id: int,
    board_id: str,
    title: str,
    content: str,
    file_paths: Optional[list[str]] = None,
    is_secret: bool = False,
    nickname: Optional[str] = None,
    confirm: bool = False,
) -> dict:
    """게시판에 글을 올린다(과제 제출용 게시판·Q&A 등).

    is_secret=True 면 비밀글(허용된 게시판에서만). content 는 HTML 을 넣을 수 있다.
    올린 뒤에는 delete_board_post 로 지울 수 있다.
    """
    _require_enabled()
    lx, b = _board_ctx(course_id, board_id)
    files = _check_files(file_paths)
    where = f"{b['title']} (course {course_id})"
    if not confirm:
        return _preview("게시판 글쓰기", where,
                        {"title": title, "content": content,
                         "files": [f.name for f in files] or None,
                         "is_secret": is_secret},
                        "삭제 가능(delete_board_post)")

    data = {"title": title, "content": content,
            "is_notice": json.dumps(False), "is_secret": json.dumps(bool(is_secret))}
    if nickname:
        data["nickname"] = nickname
    res = _lx_post(lx, f"/learningx_board/courses/{course_id}/boards/{board_id}/posts",
                   data, files)
    return {
        "confirmed": True, "action": "게시판 글쓰기", "target": where,
        "post_id": str(res.get("id")) if isinstance(res, dict) else None,
        "created_at": res.get("created_at") if isinstance(res, dict) else None,
    }


def create_board_comment(
    course_id: int,
    board_id: str,
    post_id: str,
    content: str,
    parent_comment_id: Optional[str] = None,
    file_paths: Optional[list[str]] = None,
    confirm: bool = False,
) -> dict:
    """게시판 글에 댓글을 단다. parent_comment_id 를 주면 대댓글이 된다."""
    _require_enabled()
    lx, b = _board_ctx(course_id, board_id)
    files = _check_files(file_paths)
    where = f"{b['title']} / post {post_id}"
    if not confirm:
        return _preview("게시판 댓글", where,
                        {"content": content, "parent_comment_id": parent_comment_id,
                         "files": [f.name for f in files] or None},
                        "삭제 가능(delete_board_comment)")

    data = {"content": content}
    if parent_comment_id:
        data["parent_comment_id"] = str(parent_comment_id)
    res = _lx_post(
        lx,
        f"/learningx_board/courses/{course_id}/boards/{board_id}/posts/{post_id}/comments",
        data, files,
    )
    return {
        "confirmed": True, "action": "게시판 댓글", "target": where,
        "comment_id": str(res.get("id")) if isinstance(res, dict) else None,
        "created_at": res.get("created_at") if isinstance(res, dict) else None,
    }


def _lx_json(lx, path: str, body: dict) -> Any:
    """LearningX 는 PUT/DELETE 를 POST + _method 로 받는다(웹 UI 와 동일)."""
    r = lx.session.post(
        f"{lx.base}/learningx/api/v1{path}",
        headers={"Authorization": f"Bearer {lx.xn}", "Accept": "application/json",
                 "Content-Type": "application/json"},
        data=json.dumps(body), timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"게시판 요청 실패 {r.status_code}: {r.text[:300]}")
    try:
        return r.json()
    except Exception:
        return {"status": r.status_code}


def delete_board_post(
    course_id: int, board_id: str, post_id: str, confirm: bool = False
) -> dict:
    """게시판 글을 삭제한다(본인 글만)."""
    _require_enabled()
    lx, b = _board_ctx(course_id, board_id)
    where = f"{b['title']} / post {post_id}"
    if not confirm:
        return _preview("게시판 글 삭제", where, {"post_id": post_id}, "복구 불가")
    _lx_json(lx, f"/learningx_board/courses/{course_id}/boards/{board_id}/posts/{post_id}",
             {"_method": "DELETE"})
    return {"confirmed": True, "action": "게시판 글 삭제", "target": where}


def delete_board_comment(
    course_id: int, board_id: str, post_id: str, comment_id: str,
    confirm: bool = False,
) -> dict:
    """게시판 댓글을 삭제한다(본인 댓글만)."""
    _require_enabled()
    lx, b = _board_ctx(course_id, board_id)
    where = f"{b['title']} / post {post_id} / comment {comment_id}"
    if not confirm:
        return _preview("게시판 댓글 삭제", where, {"comment_id": comment_id}, "복구 불가")
    _lx_json(
        lx,
        f"/learningx_board/courses/{course_id}/boards/{board_id}"
        f"/posts/{post_id}/comments/{comment_id}",
        {"_method": "DELETE"},
    )
    return {"confirmed": True, "action": "게시판 댓글 삭제", "target": where}
