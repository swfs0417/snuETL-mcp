"""eTL 게시판 통합 리더 — '열린게시판'(OpenBoard)과 '게시판'(learningx_board) 양쪽.

eTL 의 게시판은 Canvas 토론이 아니라 LearningX LTI 다. 그런데 계정에 두 종류가 있고
과목에 따라 하나만 있거나 둘 다 있다(동서양의 종교적 지혜는 둘 다).

  - `열린게시판`(도구 3, backend='openboard'):
        /learningx/api/v1/boards/{board}/posts?course_id=...      (강의당 보드 1개)
  - `게시판`(도구 85, backend='learningx_board'):
        /learningx/api/v1/learningx_board/courses/{cid}/boards     (Q&A·피드백·과제 제출용 등 여러 개)

두 backend 를 같은 스키마로 정규화해서 돌려준다. 글의 첨부파일은 Canvas 파일 id 를
갖고 있어 다운로드도 가능하다(파일 탭에는 안 뜬다).
"""

from __future__ import annotations

import html as _html
import re
from typing import Any

import lti


def _strip(s: str | None) -> str:
    """게시판 본문/댓글 HTML 을 평문으로 정리한다(<br> 은 줄바꿈)."""
    if not s:
        return ""
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", s, flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"</(p|div|li|h[1-6]|tr)>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = _html.unescape(t).replace("﻿", "")
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


def _author(a: Any) -> str | None:
    if isinstance(a, dict):
        return a.get("user_name") or a.get("user_login")
    return a


# --- 보드 목록 ----------------------------------------------------------------


def list_boards(course_id: int) -> list[dict]:
    """강의의 게시판 목록을 두 backend 통합해 반환한다.

    각 항목: backend, board_id(문자열), title, type, use_comment.
    """
    out: list[dict] = []
    tabs = {t["label"]: t["tool_id"] for t in lti.get_tabs(course_id) if t["tool_id"]}

    for label, tool_id in tabs.items():
        # 열린게시판(OpenBoard)은 런치 리다이렉트 URL 에 board_id 가 들어 있다
        if label == "열린게시판":
            try:
                lx = lti.launch(course_id, tool_id)
                m = re.search(r"/boards/([a-f0-9]+)", lx.url)
                if m:
                    out.append({
                        "backend": "openboard", "board_id": m.group(1),
                        "title": "열린게시판", "type": "openboard",
                        "use_comment": True, "tool_id": tool_id,
                    })
            except Exception:
                pass
        elif label == "게시판":
            try:
                lx = lti.launch(course_id, tool_id)
                for b in (lx.api(f"/learningx_board/courses/{course_id}/boards") or []):
                    out.append({
                        "backend": "learningx_board", "board_id": str(b.get("id")),
                        "title": b.get("title"), "type": b.get("type"),
                        "use_comment": b.get("use_comment"), "tool_id": tool_id,
                    })
            except Exception:
                pass
    return out


def _board_entry(course_id: int, board_id: str | None) -> list[dict]:
    boards = list_boards(course_id)
    if board_id is None:
        return boards
    return [b for b in boards if b["board_id"] == str(board_id)]


# --- 글 목록 ------------------------------------------------------------------


def _post_openboard(p: dict) -> dict:
    return {
        "id": p.get("_id"),
        "title": p.get("title"),
        "author": _author(p.get("author")) or p.get("user_login"),
        "created_at": p.get("created_at"),
        "updated_at": p.get("updated_at"),
        "comment_count": p.get("comment_count"),
        "view_count": p.get("read_count"),
        "is_notice": p.get("is_notice"),
        "content": _strip(p.get("content")),
    }


def _post_lx(p: dict) -> dict:
    return {
        "id": str(p.get("id")),
        "title": p.get("title"),
        "author": p.get("user_name") or p.get("nickname"),
        "created_at": p.get("created_at"),
        "updated_at": p.get("post_updated_at") or p.get("updated_at"),
        "comment_count": p.get("comment_count"),
        "view_count": p.get("view_count"),
        "is_notice": p.get("is_notice"),
        "is_secret": p.get("is_secret"),
        "attachment_count": p.get("attachment_count"),
        "content": _strip(p.get("content")),
    }


def list_board_posts(
    course_id: int,
    board_id: str | None = None,
    page_size: int = 50,
    page_index: int = 1,
) -> list[dict]:
    """게시판 글 목록. board_id 생략 시 강의의 모든 게시판을 훑는다.

    각 게시판별로 {board, total, posts} 를 담아 돌려준다. 본문 전체와 첨부·댓글은
    get_board_post 로 본다(목록의 content 는 backend 에 따라 비어 있을 수 있다).
    """
    results = []
    for b in _board_entry(course_id, board_id):
        lx = lti.launch(course_id, b["tool_id"])
        if b["backend"] == "openboard":
            d = lx.api(f"/boards/{b['board_id']}/posts",
                       {"course_id": course_id, "page_index": page_index,
                        "page_size": page_size}) or {}
            posts = [_post_openboard(p) for p in (d.get("posts") or [])]
            total = d.get("total_count")
        else:
            d = lx.api(
                f"/learningx_board/courses/{course_id}/boards/{b['board_id']}/posts",
                {"page_index": page_index, "page_size": page_size}) or {}
            posts = [_post_lx(p) for p in (d.get("items") or [])]
            total = (d.get("pagination") or {}).get("total")
        results.append({"board": {k: b[k] for k in ("backend", "board_id", "title", "type")},
                        "total": total, "posts": posts})
    return results


def get_board_post(
    course_id: int, post_id: str, board_id: str | None = None,
    include_comments: bool = True,
) -> dict:
    """게시판 글 1건의 본문·첨부파일·댓글을 반환한다.

    board_id 를 생략하면 강의의 게시판들을 차례로 뒤져 해당 글을 찾는다.
    첨부파일에는 Canvas file id 가 들어 있어 그대로 다운로드할 수 있다.
    """
    for b in _board_entry(course_id, board_id):
        lx = lti.launch(course_id, b["tool_id"])
        if b["backend"] == "openboard":
            p = lx.api(f"/boards/{b['board_id']}/posts/{post_id}", {"course_id": course_id})
            if not isinstance(p, dict) or not p:
                continue
            out = _post_openboard(p)
            out["attachments"] = _attachments(p)
            if include_comments:
                cj = lx.api(f"/boards/{b['board_id']}/posts/{post_id}/comments",
                            {"course_id": course_id})
                comments = cj if isinstance(cj, list) else ((cj or {}).get("comments") or [])
                out["comments"] = [_comment(c) for c in comments]
        else:
            p = lx.api(
                f"/learningx_board/courses/{course_id}/boards/{b['board_id']}/posts/{post_id}")
            if not isinstance(p, dict) or not p:
                continue
            out = _post_lx(p)
            out["attachments"] = _attachments(p)
            if include_comments:
                out["comments"] = [_comment(c) for c in (p.get("comments") or [])]
        out["board"] = {k: b[k] for k in ("backend", "board_id", "title", "type")}
        return out
    raise RuntimeError(f"게시판 글을 찾지 못했습니다 (course={course_id}, post={post_id}).")


def _attachments(p: dict) -> list[dict]:
    out = []
    for a in (p.get("attachments") or []):
        out.append({
            "filename": a.get("filename") or a.get("name"),
            "size": a.get("filesize"),
            "canvas_file_id": a.get("canvas_file_id"),
            "url": a.get("url"),
        })
    return out


def _comment(c: dict) -> dict:
    a = c.get("author") if isinstance(c.get("author"), dict) else None
    return {
        "author": (a or {}).get("user_name") or c.get("user_name") or c.get("user_login"),
        "dept": (a or {}).get("dept_name"),
        "created_at": c.get("created_at"),
        "comment": _strip(c.get("comment") or c.get("content")),
    }


def board_attachments(course_id: int, page_size: int = 100) -> list[dict]:
    """강의의 모든 게시판 글에 붙은 첨부파일을 훑어 (Canvas file id 포함) 반환한다.

    글 1건씩 상세 조회하므로 글 수만큼 요청이 나간다 — 다운로드 옵션에서만 쓴다.
    """
    out = []
    for grp in list_board_posts(course_id, page_size=page_size):
        b = grp["board"]
        for p in grp["posts"]:
            if p.get("attachment_count") == 0:
                continue
            try:
                full = get_board_post(course_id, p["id"], b["board_id"],
                                      include_comments=False)
            except Exception:
                continue
            for a in full.get("attachments") or []:
                out.append({**a, "post_id": p["id"], "post_title": p.get("title"),
                            "author": p.get("author"), "board": b["title"]})
    return out
