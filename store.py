"""로컬 누적 저장소 (SQLite) — course / 공지 / 과제 통합.

방식 (A): 원문/메타데이터를 그대로 저장하고 변경분만 증분 갱신한다.
세 엔터티(course, announcement, assignment)를 동일한 패턴으로 다루며,
값이 바뀔 때마다 공통 change_history 테이블에 누적 기록한다(덮어쓰지 않음).
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import canvas_client as cc
import snapshot as snap

DB_PATH = Path(__file__).parent / "store" / "etl.db"

# 엔터티별 변경 추적 필드 (이 값이 바뀌면 change_history 에 기록)
_TRACKED = {
    "course": ["name", "workflow_state"],
    "announcement": ["title", "posted_at", "message"],
    "assignment": ["name", "due_at", "lock_at", "unlock_at", "points_possible"],
    # 열린게시판: 본문 수정뿐 아니라 새 답글(reply_count 증가)도 변경으로 본다
    "discussion": ["title", "posted_at", "message", "reply_count"],
}
# created 이력의 new_value 로 남길 대표 필드
_HEADLINE = {
    "course": "workflow_state", "announcement": "posted_at",
    "assignment": "due_at", "discussion": "posted_at",
}


# --- 유틸 ---------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _trunc(v: Any, n: int = 300) -> Optional[str]:
    if v is None:
        return None
    s = str(v)
    return s if len(s) <= n else s[:n] + "…"


def _html_to_text(h: Optional[str]) -> str:
    if not h:
        return ""
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", h, flags=re.S)
    t = re.sub(r"<br\s*/?>", "\n", t)
    t = re.sub(r"</(p|div|li|h[1-6]|tr)>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    # 자동 동기화(cron)·MCP 서버·수동 실행이 겹칠 수 있어, 잠깐의 잠금은 기다렸다 진행한다
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def _db():
    """트랜잭션 1개를 열고 끝나면 반드시 닫는다(연결이 남아 잠금을 붙드는 것 방지)."""
    conn = _conn()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    with _db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS courses (
                id INTEGER PRIMARY KEY, name TEXT, course_code TEXT,
                term_id INTEGER, workflow_state TEXT,
                first_seen TEXT, last_synced TEXT, last_changed TEXT
            );
            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY, course_id INTEGER, course_name TEXT,
                title TEXT, posted_at TEXT, author TEXT, message TEXT, url TEXT,
                first_seen TEXT, last_synced TEXT, last_changed TEXT
            );
            CREATE TABLE IF NOT EXISTS assignments (
                id INTEGER PRIMARY KEY, course_id INTEGER, course_name TEXT,
                name TEXT, due_at TEXT, lock_at TEXT, unlock_at TEXT,
                points_possible REAL, submission_types TEXT, html_url TEXT,
                first_seen TEXT, last_synced TEXT, last_changed TEXT
            );
            CREATE TABLE IF NOT EXISTS discussions (
                id INTEGER PRIMARY KEY, course_id INTEGER, course_name TEXT,
                title TEXT, posted_at TEXT, last_reply_at TEXT, author TEXT,
                message TEXT, reply_count INTEGER, url TEXT,
                first_seen TEXT, last_synced TEXT, last_changed TEXT
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                course_id INTEGER, section TEXT, course_name TEXT,
                hash TEXT, count INTEGER, data TEXT,
                first_seen TEXT, last_synced TEXT, last_changed TEXT,
                PRIMARY KEY (course_id, section)
            );
            CREATE TABLE IF NOT EXISTS change_history (
                hist_id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT, entity_id INTEGER, course_id INTEGER,
                name TEXT, field TEXT, old_value TEXT, new_value TEXT,
                changed_at TEXT
            );
            """
        )


def _log(conn, etype, eid, course_id, name, field, old, new, ts) -> None:
    conn.execute(
        """INSERT INTO change_history
        (entity_type, entity_id, course_id, name, field, old_value, new_value, changed_at)
        VALUES (?,?,?,?,?,?,?,?)""",
        (etype, eid, course_id, name, field, _trunc(old), _trunc(new), ts),
    )


def _upsert(conn, table, etype, values, name_field, ts) -> str:
    """엔터티 1건을 upsert 하고 변경 이력을 누적. 'new'|'changed'|'unchanged' 반환."""
    eid = values["id"]
    name = values.get(name_field)
    existing = conn.execute(f"SELECT * FROM {table} WHERE id=?", (eid,)).fetchone()
    cols = list(values.keys())

    if existing is None:
        allcols = cols + ["first_seen", "last_synced", "last_changed"]
        conn.execute(
            f"INSERT INTO {table} ({','.join(allcols)}) "
            f"VALUES ({','.join(['?'] * len(allcols))})",
            [values[c] for c in cols] + [ts, ts, ts],
        )
        _log(conn, etype, eid, values.get("course_id"), name, "created",
             None, values.get(_HEADLINE[etype]), ts)
        return "new"

    diffs = [
        (f, existing[f], values.get(f))
        for f in _TRACKED[etype]
        if (existing[f] or None) != (values.get(f) or None)
    ]
    if diffs:
        setcols = [c for c in cols if c != "id"]
        conn.execute(
            f"UPDATE {table} SET {','.join(f'{c}=?' for c in setcols)}, "
            f"last_synced=?, last_changed=? WHERE id=?",
            [values[c] for c in setcols] + [ts, ts, eid],
        )
        for f, old, new in diffs:
            _log(conn, etype, eid, values.get("course_id"), name, f, old, new, ts)
        return "changed"

    conn.execute(f"UPDATE {table} SET last_synced=? WHERE id=?", (ts, eid))
    return "unchanged"


def _targets(course_id: Optional[int], active_only: bool,
             current_term_only: bool = False) -> list[tuple[int, str]]:
    if course_id is not None:
        course = cc.get_canvas().get_course(course_id)
        return [(course_id, getattr(course, "name", str(course_id)))]
    return [(c["id"], c["name"]) for c in cc.list_courses(
        active_only=active_only, current_term_only=current_term_only)]


def _summary(counts: dict, **extra) -> dict:
    return {"synced_at": _now(), **extra, **counts}


# --- 동기화 (Sync) ------------------------------------------------------------


def _sync_per_course(
    table: str, etype: str, name_field: str,
    fetch_rows, course_id: Optional[int], active_only: bool,
) -> dict:
    """과목마다 (조회 → 짧은 트랜잭션 쓰기)를 반복한다.

    네트워크 대기 동안 DB 트랜잭션을 붙잡으면 동시에 도는 cron·MCP 서버가
    'database is locked' 로 실패한다. 그래서 조회를 먼저 끝내고 쓸 때만 연결을 연다.
    """
    init_db()
    ts = _now()
    counts = {"new": 0, "changed": 0, "unchanged": 0}
    targets = _targets(course_id, active_only)
    for cid, cname in targets:
        rows = fetch_rows(cid, cname)
        if not rows:
            continue
        with _db() as conn:
            for values in rows:
                counts[_upsert(conn, table, etype, values, name_field, ts)] += 1
    return _summary(counts, courses=len(targets))


def sync_courses(active_only: bool = True) -> dict:
    """수강 강의 목록을 저장소에 누적 반영한다."""
    init_db()
    ts = _now()
    counts = {"new": 0, "changed": 0, "unchanged": 0}
    with _db() as conn:
        for c in cc.list_courses(active_only=active_only):
            values = {
                "id": c["id"], "name": c["name"], "course_code": c["course_code"],
                "term_id": c["term_id"], "workflow_state": c["workflow_state"],
            }
            counts[_upsert(conn, "courses", "course", values, "name", ts)] += 1
    return _summary(counts)


def sync_announcements(course_id: Optional[int] = None, active_only: bool = True) -> dict:
    """공지를 저장소에 누적 반영한다(본문은 텍스트로 정리해 저장, 수정 시 이력 기록)."""
    def rows(cid, cname):
        course = cc.get_canvas().get_course(cid)
        out = []
        for d in course.get_discussion_topics(only_announcements=True):
            a = cc.announcement_to_dict(d)
            out.append({
                "id": a["id"], "course_id": cid, "course_name": cname,
                "title": a["title"], "posted_at": a["posted_at"],
                "author": a["author"], "message": _html_to_text(a["message"]),
                "url": a["html_url"],
            })
        return out

    return _sync_per_course("announcements", "announcement", "title", rows,
                            course_id, active_only)


def sync_discussions(course_id: Optional[int] = None, active_only: bool = True) -> dict:
    """열린게시판(토론) 글을 저장소에 누적 반영한다.

    본문은 텍스트로 정리해 저장하고, 본문 수정·새 답글(reply_count 증가) 시 이력을 남긴다.
    eTL 은 only_announcements 를 명시할 때만 토픽을 돌려주므로 False 를 명시한다.
    """
    def rows(cid, cname):
        course = cc.get_canvas().get_course(cid)
        out = []
        for d in course.get_discussion_topics(only_announcements=False):
            t = cc.discussion_to_dict(d)
            out.append({
                "id": t["id"], "course_id": cid, "course_name": cname,
                "title": t["title"], "posted_at": t["posted_at"],
                "last_reply_at": t["last_reply_at"], "author": t["author"],
                "message": _html_to_text(t["message"]),
                "reply_count": t["reply_count"], "url": t["html_url"],
            })
        return out

    return _sync_per_course("discussions", "discussion", "title", rows,
                            course_id, active_only)


def sync_assignments(course_id: Optional[int] = None, active_only: bool = True) -> dict:
    """과제를 저장소에 누적 반영한다(마감일 변경 시 이력 기록)."""
    def rows(cid, cname):
        course = cc.get_canvas().get_course(cid)
        out = []
        for ass in course.get_assignments():
            a = cc.assignment_to_dict(ass)
            out.append({
                "id": a["id"], "course_id": cid, "course_name": cname,
                "name": a["name"], "due_at": a["due_at"], "lock_at": a["lock_at"],
                "unlock_at": a["unlock_at"], "points_possible": a["points_possible"],
                "submission_types": json.dumps(a["submission_types"], ensure_ascii=False),
                "html_url": a["html_url"],
            })
        return out

    return _sync_per_course("assignments", "assignment", "name", rows,
                            course_id, active_only)


def sync_all(active_only: bool = True, snapshot: bool = True) -> dict:
    """강의/공지/과제와 강의 화면 전체 스냅샷을 한 번에 동기화한다.

    snapshot=True(기본)면 이번 학기 과목의 모든 탭(성적·출결·게시판·페이지·주차학습 등)을
    읽어 사이트와 같은 상태로 맞춘다(과목당 10~30초). 빠르게 끝내려면 snapshot=False.
    """
    out = {
        "courses": sync_courses(active_only=active_only),
        "announcements": sync_announcements(active_only=active_only),
        "discussions": sync_discussions(active_only=active_only),
        "assignments": sync_assignments(active_only=active_only),
    }
    if snapshot:
        out["snapshot"] = sync_snapshot(active_only=active_only, current_term_only=True)
    return out


# --- 조회 (Read, API 호출 없이 캐시) ------------------------------------------


def _query(table: str, course_id: Optional[int], order: str) -> list[dict]:
    init_db()
    q = f"SELECT * FROM {table}"
    params: tuple = ()
    if course_id is not None and table != "courses":
        q += " WHERE course_id = ?"
        params = (course_id,)
    q += f" ORDER BY {order}"
    with _db() as c:
        return [dict(r) for r in c.execute(q, params).fetchall()]


# --- 사이트 화면 전체 스냅샷 동기화 -------------------------------------------


# 볼 때마다 바뀌어서 '변경'으로 잡히면 곤란한 필드(조회수 등)
_VOLATILE = {"view_count", "user_view_count", "read_count", "bookmark"}


def _stable(data: Any) -> Any:
    """조회수처럼 내용과 무관하게 변하는 값을 제거한 사본을 만든다(변경 판정용)."""
    if isinstance(data, dict):
        return {k: _stable(v) for k, v in data.items() if k not in _VOLATILE}
    if isinstance(data, list):
        return [_stable(v) for v in data]
    return data


def _ids(data: Any) -> dict[str, str]:
    """리스트형 섹션에서 {항목 id: 항목 요약 해시} 를 만든다(추가/삭제/수정 판정용)."""
    out: dict[str, str] = {}
    if not isinstance(data, list):
        return out
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or item.get("url") or item.get("item_id")
                  or item.get("assignment_id") or i)
        out[key] = hashlib.sha1(
            json.dumps(item, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
    return out


def _label(data: Any, key: str) -> str:
    """변경 이력 메시지에 쓸 항목 제목을 찾는다."""
    if isinstance(data, list):
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            k = str(item.get("id") or item.get("url") or item.get("item_id")
                    or item.get("assignment_id") or i)
            if k == key:
                return str(item.get("title") or item.get("name")
                           or item.get("assignment") or key)
    return key


def sync_snapshot(
    course_id: Optional[int] = None,
    active_only: bool = True,
    sections: Optional[list[str]] = None,
    current_term_only: bool = True,
) -> dict:
    """강의 화면 전체(탭 단위 섹션)를 읽어 저장하고, 지난번과 달라진 부분을 이력에 남긴다.

    course_id 생략 시 이번 학기 강의 전체(과목당 10~30초 걸리므로 기본이 이번 학기다.
    current_term_only=False 로 지난 학기까지 포함할 수 있다).
    sections 로 일부 섹션만 갱신할 수도 있다(예 ['announcements','grades']).
    """
    init_db()
    ts = _now()
    targets = _targets(course_id, active_only, current_term_only=current_term_only)
    counts = {"new": 0, "changed": 0, "unchanged": 0}
    per_course = []

    for cid, cname in targets:
        # 스냅샷 조회(과목당 10~30초)는 트랜잭션 밖에서 — 그 동안 DB 를 잠그면 안 된다
        try:
            snapshot_data = snap.get_course_snapshot(cid, sections=sections)
        except Exception as e:
            per_course.append({"course": cname, "error": str(e)})
            continue

        changed_sections = []
        with _db() as conn:
            for section, raw in (snapshot_data.get("sections") or {}).items():
                data = _stable(raw)
                blob = json.dumps(data, ensure_ascii=False, sort_keys=True)
                h = hashlib.sha1(blob.encode()).hexdigest()
                cnt = len(data) if isinstance(data, list) else 1
                row = conn.execute(
                    "SELECT hash, data, count FROM snapshots WHERE course_id=? AND section=?",
                    (cid, section),
                ).fetchone()

                if row is None:
                    conn.execute(
                        """INSERT INTO snapshots
                        (course_id, section, course_name, hash, count, data,
                         first_seen, last_synced, last_changed)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                        (cid, section, cname, h, cnt, blob, ts, ts, ts),
                    )
                    _log(conn, "section", cid, cid, f"{cname} / {section}",
                         "created", None, f"{cnt}건", ts)
                    counts["new"] += 1
                    continue

                if row["hash"] == h:
                    conn.execute(
                        "UPDATE snapshots SET last_synced=? WHERE course_id=? AND section=?",
                        (ts, cid, section))
                    counts["unchanged"] += 1
                    continue

                # 무엇이 달라졌는지 항목 단위로 요약
                try:
                    old_data = json.loads(row["data"])
                except Exception:
                    old_data = None
                old_ids, new_ids = _ids(old_data), _ids(data)
                added = [k for k in new_ids if k not in old_ids]
                removed = [k for k in old_ids if k not in new_ids]
                edited = [k for k in new_ids if k in old_ids and new_ids[k] != old_ids[k]]
                detail = ", ".join(
                    part for part in (
                        f"추가 {len(added)}" if added else "",
                        f"수정 {len(edited)}" if edited else "",
                        f"삭제 {len(removed)}" if removed else "",
                    ) if part
                ) or "내용 변경"
                titles = [_label(data, k) for k in (added + edited)[:5]]
                conn.execute(
                    """UPDATE snapshots SET course_name=?, hash=?, count=?, data=?,
                       last_synced=?, last_changed=? WHERE course_id=? AND section=?""",
                    (cname, h, cnt, blob, ts, ts, cid, section),
                )
                _log(conn, "section", cid, cid, f"{cname} / {section}", detail,
                     f"{row['count']}건",
                     f"{cnt}건 — " + "; ".join(titles) if titles else f"{cnt}건", ts)
                counts["changed"] += 1
                changed_sections.append({"section": section, "detail": detail,
                                         "items": titles})

        per_course.append({
            "course": cname,
            "read": snapshot_data["coverage"]["read"],
            "tabs": snapshot_data["coverage"]["tabs_total"],
            "not_read": snapshot_data["coverage"]["not_read"],
            "changed": changed_sections,
        })
    return _summary(counts, courses=len(targets), detail=per_course)


def get_stored_snapshot(course_id: int, section: Optional[str] = None) -> dict:
    """저장된 강의 화면 스냅샷을 돌려준다(API 호출 없이 캐시 조회).

    section 생략 시 모든 섹션. 각 섹션에 마지막 동기화·변경 시각이 함께 온다.
    """
    init_db()
    q = "SELECT * FROM snapshots WHERE course_id=?"
    args: list[Any] = [course_id]
    if section:
        q += " AND section=?"
        args.append(section)
    with _db() as conn:
        rows = conn.execute(q, args).fetchall()
    out = {"course_id": course_id, "sections": {}}
    for r in rows:
        out["course"] = r["course_name"]
        try:
            data = json.loads(r["data"])
        except Exception:
            data = None
        out["sections"][r["section"]] = {
            "last_synced": r["last_synced"], "last_changed": r["last_changed"],
            "data": data,
        }
    return out


def get_stored_courses() -> list[dict]:
    return _query("courses", None, "term_id DESC, name")


def get_stored_announcements(course_id: Optional[int] = None) -> list[dict]:
    return _query("announcements", course_id, "posted_at DESC")


def get_stored_discussions(course_id: Optional[int] = None) -> list[dict]:
    return _query("discussions", course_id, "posted_at DESC")


def get_stored_assignments(course_id: Optional[int] = None) -> list[dict]:
    return _query("assignments", course_id, "(due_at IS NULL), due_at")


def get_change_history(
    entity_type: Optional[str] = None,
    course_id: Optional[int] = None,
    entity_id: Optional[int] = None,
) -> list[dict]:
    """변경 이력(마감일/공지·게시판 수정 등)을 시간순으로 반환한다.

    entity_type: 'course' | 'announcement' | 'discussion' | 'assignment' (생략 시 전체).
    """
    init_db()
    q = "SELECT * FROM change_history"
    conds, params = [], []
    if entity_type:
        conds.append("entity_type = ?"); params.append(entity_type)
    if course_id is not None:
        conds.append("course_id = ?"); params.append(course_id)
    if entity_id is not None:
        conds.append("entity_id = ?"); params.append(entity_id)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY changed_at, hist_id"
    with _db() as c:
        return [dict(r) for r in c.execute(q, params).fetchall()]
