"""LearningX LTI 외부도구 공통 진입점 — 도구 찾기 · 런치 · API 호출.

eTL 의 '주차학습·게시판·강의/출결·강의교재' 메뉴는 Canvas 가 아니라 LearningX(유비온)
LTI 외부도구다. 이들을 읽으려면 (1) 강의별 외부도구 id 를 알아내고 (2) sessionless
launch 로 받은 OAuth 서명 폼을 POST 해 LearningX 세션(xn_api_token)을 얻어야 한다.

도구 id 를 찾는 곳이 중요하다: `GET /api/v1/courses/{cid}/external_tools` 는 학생
토큰에서 **빈 배열**을 준다(2026-09-02 전 과목 확인). 대신 `GET .../tabs` 가
`context_external_tool_<도구id>` 로 id 를 그대로 준다 — tabs 가 '학생이 실제로 보는
메뉴'의 정답지이므로 여기서 찾는다.

세션은 런치 비용(요청 3회)이 있으므로 프로세스 단위로 (강의, 도구) 별 캐시한다.
"""

from __future__ import annotations

import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

# 도구 종류 -> (탭 라벨 후보, 계정 공통 도구 id 후보).
# 라벨은 과목마다 다를 수 있어 id 후보를 폴백으로 둔다(2026-2 기준 id 고정).
TOOL_KINDS: dict[str, tuple[tuple[str, ...], tuple[int, ...]]] = {
    "attendance": (("강의/출결", "영상강의", "온라인강의", "출결"), (48,)),
    "weekly": (("주차학습", "주차 학습"), (83,)),
    "board": (("게시판", "열린게시판"), (85, 3)),
    "materials": (("강의교재", "교재"), (150,)),
}

_LAUNCH_TTL = 900  # 초 — LearningX 세션 재사용 시간
_sessions: dict[tuple[int, int], tuple[float, "Launch"]] = {}
_tabs_cache: dict[int, tuple[float, list[dict]]] = {}


def env() -> tuple[str, str]:
    base = os.environ.get("ETL_BASE_URL")
    token = os.environ.get("ETL_TOKEN")
    if not base or not token:
        raise RuntimeError("ETL_BASE_URL / ETL_TOKEN 환경변수가 필요합니다 (.env 확인).")
    return base.rstrip("/"), token


class LaunchForm(HTMLParser):
    """LTI sessionless_launch 가 돌려주는 자동제출 폼(action + hidden 필드)을 파싱."""

    def __init__(self) -> None:
        super().__init__()
        self.action: str | None = None
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list) -> None:
        d = dict(attrs)
        if tag == "form" and d.get("action"):
            self.action = d["action"]
        if tag == "input" and "name" in d:
            self.fields[d["name"]] = d.get("value", "")


class Launch:
    """런치된 LearningX 세션 (base, requests 세션, xn_api_token, 최종 URL)."""

    def __init__(self, base: str, session: requests.Session, xn: str, url: str,
                 tool_id: int) -> None:
        self.base, self.session, self.xn, self.url, self.tool_id = (
            base, session, xn, url, tool_id
        )

    def api(self, path: str, params: dict | None = None) -> Any:
        """/learningx/api/v1 하위 경로를 GET 한다. 실패(비 200/비 JSON)면 None."""
        r = self.session.get(
            f"{self.base}/learningx/api/v1{path}",
            headers={"Authorization": f"Bearer {self.xn}", "Accept": "application/json"},
            params=params or {}, timeout=20,
        )
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except Exception:
            return None


def get_tabs(course_id: int, force: bool = False) -> list[dict]:
    """강의 메뉴(탭) 목록 — 학생이 실제로 보는 메뉴가 무엇인지의 정답지."""
    hit = _tabs_cache.get(course_id)
    if hit and not force and time.time() - hit[0] < 600:
        return hit[1]
    base, token = env()
    r = requests.get(
        f"{base}/api/v1/courses/{course_id}/tabs",
        headers={"Authorization": f"Bearer {token}"},
        params={"per_page": 100}, timeout=20,
    )
    tabs = r.json() if r.ok and isinstance(r.json(), list) else []
    out = []
    for t in tabs:
        m = re.search(r"context_external_tool_(\d+)", t.get("id") or "")
        out.append({
            "id": t.get("id"),
            "label": t.get("label"),
            "type": t.get("type"),
            "hidden": bool(t.get("hidden")),
            "url": t.get("html_url"),
            "tool_id": int(m.group(1)) if m else None,
        })
    _tabs_cache[course_id] = (time.time(), out)
    return out


def find_tool(course_id: int, kind: str) -> int | None:
    """탭 목록에서 해당 종류의 LTI 도구 id 를 찾는다(라벨 우선, 알려진 id 폴백)."""
    labels, known_ids = TOOL_KINDS[kind]
    tools = [t for t in get_tabs(course_id) if t["tool_id"]]
    for t in tools:
        if (t["label"] or "").strip() in labels:
            return t["tool_id"]
    for t in tools:
        if t["tool_id"] in known_ids:
            return t["tool_id"]
    return None


def launch(course_id: int, tool_id: int, force: bool = False) -> Launch:
    """LTI 런치로 LearningX 세션을 만든다(같은 강의·도구는 15분간 재사용)."""
    key = (course_id, tool_id)
    hit = _sessions.get(key)
    if hit and not force and time.time() - hit[0] < _LAUNCH_TTL:
        return hit[1]

    base, token = env()
    headers = {"Authorization": f"Bearer {token}"}
    s = requests.Session()
    r = s.get(f"{base}/api/v1/courses/{course_id}/external_tools/sessionless_launch",
              headers=headers, params={"id": tool_id}, timeout=20)
    url = (r.json() or {}).get("url") if r.ok else None
    if not url:
        raise RuntimeError(f"LTI 런치 URL 을 받지 못했습니다 (tool={tool_id}, {r.status_code}).")
    p = LaunchForm()
    p.feed(s.get(url, headers=headers, timeout=20).text)
    if not p.action:
        raise RuntimeError(f"LTI 런치 폼을 파싱하지 못했습니다 (tool={tool_id}).")
    resp = s.post(p.action, data=p.fields, allow_redirects=True, timeout=30)
    xn = s.cookies.get("xn_api_token")
    if not xn:
        raise RuntimeError(f"LearningX 세션 토큰(xn_api_token)을 받지 못했습니다 (tool={tool_id}).")
    lx = Launch(base, s, xn, resp.url, tool_id)
    _sessions[key] = (time.time(), lx)
    return lx


def launch_kind(course_id: int, kind: str, force: bool = False) -> Launch:
    """도구 종류(attendance/weekly/board/materials)로 바로 런치한다."""
    tid = find_tool(course_id, kind)
    if tid is None:
        raise RuntimeError(f"이 강의에는 '{kind}' LTI 메뉴가 없습니다 (course={course_id}).")
    return launch(course_id, tid, force=force)
