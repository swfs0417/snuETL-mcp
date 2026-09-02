"""본문 속 링크로만 제공되는 자료 찾기 — 파일 목록(Files)에 안 뜨는 파일 수집.

교수가 페이지·공지·과제 본문에 파일을 링크로만 걸어두면 Canvas 가 그 파일을
hidden 으로 두기 때문에 `course.get_files()` 목록에 나오지 않는다(eTL '파일' 탭에도
안 보인다). 이 모듈은 강의의 모든 HTML 본문(강의계획서·페이지·과제·퀴즈·공지·
토론)과 모듈 항목을 훑어서

  1) eTL 내부 파일 링크(/files/<id>)  -> 다운로드 가능한 Canvas 파일
  2) 외부 링크(구글드라이브·유튜브·LCMS·직접 파일 URL 등)

를 뽑아내고, 각각이 '어디에서 발견됐는지'(sources)와 '어떻게 걸려 있는지'(ref:
link=첨부 링크 / embed=본문에 삽입된 이미지·프레임)를 함께 돌려준다.
materials.download_course_files 가 1) 을 다운로드 목록에 합쳐 같이 받아준다.
"""

from __future__ import annotations

import copy
import html as _html
import os
import re
import time
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import unquote, urlparse

import canvas_client as cc

# eTL 내부 파일 링크: /courses/<id>/files/<fid>, /files/<fid> 등.
# /users/<id>/files/<fid> 는 남의 개인 파일이라 제외한다.
_FILE_LINK_RE = re.compile(r"/(?:(courses|users|groups)/(\d+)/)?files/(\d+)")
# <a href>·<img src>·<iframe src> 를 태그와 함께 뽑는다(첨부 링크 / 본문 삽입 구분용)
_TAG_URL_RE = re.compile(
    r"""<(a|img|iframe)\b[^>]*?\b(?:href|src)\s*=\s*["']([^"']+)["']""", re.I
)
# <a ...>링크 텍스트</a> — 파일명 추정용
_ANCHOR_RE = re.compile(
    r"""<a\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""", re.I | re.S
)

# 외부 링크 중 '바로 받을 수 있는 파일'로 볼 확장자
DOC_EXTS = {
    ".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx", ".hwp", ".hwpx",
    ".zip", ".txt", ".csv", ".md", ".jpg", ".jpeg", ".png", ".gif", ".mp3", ".mp4",
}


def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _etl_host() -> str:
    return urlparse(os.environ.get("ETL_BASE_URL", "https://myetl.snu.ac.kr")).netloc


def classify_link(url: str) -> str:
    """외부 링크 종류를 판정한다: direct_file | google_drive | youtube | lcms | other."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "lcms.snu.ac.kr" in host:
        return "lcms"
    if "drive.google.com" in host or "docs.google.com" in host:
        return "google_drive"
    if Path(unquote(parsed.path)).suffix.lower() in DOC_EXTS:
        return "direct_file"
    return "other"


# --- 본문 수집 ----------------------------------------------------------------


def _iter_bodies(course: Any) -> Iterator[tuple[dict, str]]:
    """강의의 (출처, HTML 본문) 쌍을 순회한다. 어떤 항목이 막혀 있어도 계속 진행한다."""
    cid = course.id
    base = (os.environ.get("ETL_BASE_URL") or "").rstrip("/")

    body = getattr(course, "syllabus_body", None)
    if body:
        yield {"type": "syllabus", "title": "강의계획서(eTL)",
               "url": f"{base}/courses/{cid}/assignments/syllabus"}, body

    try:
        for p in course.get_pages():
            try:
                full = course.get_page(p.url)
            except Exception:
                continue
            yield {"type": "page", "title": getattr(full, "title", p.url),
                   "url": getattr(full, "html_url", None)}, getattr(full, "body", "") or ""
    except Exception:
        pass

    try:
        for a in course.get_assignments():
            yield {"type": "assignment", "id": a.id, "title": getattr(a, "name", None),
                   "url": getattr(a, "html_url", None)}, getattr(a, "description", "") or ""
    except Exception:
        pass

    try:
        for q in course.get_quizzes():
            yield {"type": "quiz", "id": q.id, "title": getattr(q, "title", None),
                   "url": getattr(q, "html_url", None)}, getattr(q, "description", "") or ""
    except Exception:
        pass

    # eTL 은 공지도 토론 토픽으로 돌려주므로 같은 토픽이 두 번 잡힌다 -> id 로 중복 제거
    seen_topics: set[int] = set()
    for only_ann, kind in ((True, "announcement"), (False, "discussion")):
        try:
            topics = list(course.get_discussion_topics(only_announcements=only_ann))
        except Exception:
            continue
        for d in topics:
            if d.id in seen_topics:
                continue
            seen_topics.add(d.id)
            src = {"type": kind, "id": d.id, "title": getattr(d, "title", None),
                   "url": getattr(d, "html_url", None)}
            yield src, getattr(d, "message", "") or ""
            for att in (getattr(d, "attachments", None) or []):
                if att.get("id"):  # 글에 직접 붙은 첨부파일
                    yield src, f'<a href="/files/{att["id"]}">{att.get("display_name", "")}</a>'


def _module_links(course: Any) -> Iterator[tuple[dict, str, Optional[int], Optional[str]]]:
    """모듈 항목에서 (출처, 제목, 파일id, 외부URL) 을 뽑는다.

    모듈의 File 항목은 본문 HTML 이 아니라 content_id 로만 파일을 가리키므로 따로
    훑는다(파일 목록에 안 뜨는 파일이 모듈에만 걸려 있는 경우가 있다).
    """
    try:
        modules = list(course.get_modules())
    except Exception:
        return
    for m in modules:
        try:
            items = list(m.get_module_items())
        except Exception:
            continue
        for it in items:
            title = getattr(it, "title", "") or ""
            src = {"type": "module",
                   "title": f"{getattr(m, 'name', '')} / {title}".strip(" /"),
                   "url": getattr(it, "html_url", None)}
            t = getattr(it, "type", None)
            if t == "File" and getattr(it, "content_id", None):
                yield src, title, int(it.content_id), None
            elif t == "ExternalUrl" and getattr(it, "external_url", None):
                yield src, title, None, it.external_url


# --- 스캔 ---------------------------------------------------------------------


def _add(bucket: dict, key: Any, base: dict, source: dict) -> dict:
    """같은 파일/링크가 여러 곳에 걸려 있으면 출처만 누적한다."""
    entry = bucket.setdefault(key, {**base, "sources": []})
    if source not in entry["sources"]:
        entry["sources"].append(source)
    return entry


def _scan_body(body: str, source: dict, host: str, files: dict, externals: dict) -> None:
    """본문 HTML 1건에서 파일 링크와 외부 링크를 뽑아 버킷에 넣는다."""
    text_by_url = {u: _strip_tags(t) for u, t in _ANCHOR_RE.findall(body)}
    tagged: set[str] = set()

    for tag, raw in _TAG_URL_RE.findall(body):
        url = _html.unescape(raw)
        tagged.add(url)
        ref = "link" if tag.lower() == "a" else "embed"
        m = _FILE_LINK_RE.search(url)
        if m and m.group(1) != "users":
            e = _add(files, int(m.group(3)),
                     {"id": int(m.group(3)), "ref": ref,
                      "link_text": text_by_url.get(url) or None,
                      "link_course_id": int(m.group(2)) if m.group(2) else None},
                     source)
            if ref == "link":  # 첨부 링크가 하나라도 있으면 link 로 승격
                e["ref"] = "link"
                e["link_text"] = e.get("link_text") or text_by_url.get(url)
            continue
        if url.startswith("http") and urlparse(url).netloc != host:
            _add(externals, url,
                 {"url": url, "text": text_by_url.get(url) or None,
                  "kind": classify_link(url), "ref": ref}, source)

    # 태그 밖(data-api-endpoint 등)에만 있는 파일 링크도 놓치지 않는다
    for m in _FILE_LINK_RE.finditer(body):
        if m.group(1) == "users":
            continue
        fid = int(m.group(3))
        if fid not in files:
            _add(files, fid, {"id": fid, "ref": "other", "link_text": None,
                              "link_course_id": int(m.group(2)) if m.group(2) else None},
                 source)


# 스캔 1회에 강의 본문 전체(페이지·과제·공지…)를 훑으므로 API 호출이 수십 회다.
# 목록 확인 -> dry_run -> 실제 다운로드처럼 짧은 간격의 연속 호출을 위해 잠깐 캐시한다.
_SCAN_TTL = 120  # 초
_scan_cache: dict[tuple[int, bool], tuple[float, dict]] = {}


def scan_course_links(course_id: int, resolve: bool = True, force: bool = False) -> dict:
    """강의의 모든 본문을 훑어 파일 링크와 외부 링크를 찾아낸다.

    resolve=True 면 찾은 파일 id 를 Canvas 에 조회해 이름·크기·확장자까지 채운다
    (파일 1개당 API 1회). False 면 id/출처만 빠르게 돌려준다.
    결과는 _SCAN_TTL(120초)간 캐시하며, force=True 면 캐시를 무시하고 다시 훑는다.
    """
    key = (course_id, resolve)
    hit = _scan_cache.get(key)
    if hit and not force and time.time() - hit[0] < _SCAN_TTL:
        return copy.deepcopy(hit[1])

    canvas = cc.get_canvas()
    course = canvas.get_course(course_id, include=["syllabus_body"])
    host = _etl_host()

    files: dict[int, dict] = {}
    externals: dict[str, dict] = {}

    for source, body in _iter_bodies(course):
        if body:
            _scan_body(body, source, host, files, externals)

    for source, title, fid, ext_url in _module_links(course):
        if fid is not None:
            _add(files, fid, {"id": fid, "ref": "link", "link_text": title or None,
                              "link_course_id": course_id}, source)
        elif ext_url:
            _add(externals, ext_url,
                 {"url": ext_url, "text": title or None,
                  "kind": classify_link(ext_url), "ref": "link"}, source)

    # 파일 목록(Files 탭)에 이미 뜨는 파일인지 표시 — 안 뜨는 게 이 기능의 핵심 대상
    listed: dict[int, dict] = {}
    try:
        listed = {f.id: cc.file_to_dict(f) for f in course.get_files()}
    except Exception:
        pass

    out = []
    for fid, info in sorted(files.items()):
        info["in_files_list"] = fid in listed
        if fid in listed:  # 이미 받아둔 메타데이터 재사용(추가 API 호출 없음)
            meta = listed[fid]
            info.update({k: meta.get(k) for k in ("display_name", "size", "content_type", "url")})
        elif resolve:
            info.update(resolve_file(course, fid))
        out.append(info)

    result = {
        "course_id": course_id,
        "course": getattr(course, "name", None),
        "files": out,
        "external_links": sorted(externals.values(), key=lambda e: (e["kind"], e["url"])),
        "summary": {
            "files_found": len(out),
            "not_in_files_list": sum(1 for f in out if not f["in_files_list"]),
            "downloadable": sum(
                1 for f in out if not f["in_files_list"] and not f.get("error")
            ),
            "external_links": len(externals),
        },
    }
    _scan_cache[key] = (time.time(), result)
    return copy.deepcopy(result)


def resolve_file(course: Any, fid: int) -> dict:
    """파일 id 의 메타데이터를 조회한다. 실패하면 error 만 채워 돌려준다.

    다른 강의에서 복사해온 본문이면 그 파일은 이 강의에서 못 읽는다(교수 개인 자료
    등) — 그 경우 error 를 남겨 다운로드 대상에서 빠지게 한다.
    """
    try:
        f = course.get_file(fid)
    except Exception as e:
        return {"error": str(e)}
    return {
        "display_name": getattr(f, "display_name", None) or getattr(f, "filename", None),
        "size": getattr(f, "size", None),
        "content_type": getattr(f, "content-type", None) or getattr(f, "content_type", None),
        "hidden": getattr(f, "hidden", None),
        "url": getattr(f, "url", None),
        "updated_at": getattr(f, "updated_at", None),
    }


def linked_file_objects(course: Any, include_embedded: bool = False) -> list[tuple[Any, dict]]:
    """본문 링크로만 걸린 Canvas 파일들을 (canvasapi File, 출처) 로 돌려준다.

    materials 의 다운로드 대상 계산에서 재사용한다. 조회 실패한 id(다른 강의 파일 등)와
    파일 목록에 이미 있는 파일은 제외한다. include_embedded=True 면 본문에 삽입된
    이미지(<img>)까지 포함한다(기본은 첨부 링크만).
    """
    scan = scan_course_links(course.id, resolve=False)
    out = []
    for info in scan["files"]:
        if info["in_files_list"]:
            continue
        if not include_embedded and info.get("ref") == "embed":
            continue
        try:
            f = course.get_file(info["id"])
        except Exception:
            continue  # 권한 없음 / 다른 강의 파일
        out.append((f, info["sources"][0] if info["sources"] else {}))
    return out


# --- 외부 링크 파일 내려받기 ---------------------------------------------------


def _filename_from(resp: Any, url: str) -> str:
    """Content-Disposition 또는 URL 경로에서 저장 파일명을 정한다."""
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd)
    name = unquote(m.group(1)) if m else Path(unquote(urlparse(url).path)).name
    return re.sub(r'[/\\:*?"<>|]', "_", name.strip()) or "download"


def download_external_file(url: str, dest_dir: str, overwrite: bool = False) -> dict:
    """외부 직접 파일 링크(pdf 등)를 dest_dir 에 내려받는다.

    구글드라이브·유튜브처럼 페이지형 링크는 대상이 아니다(kind='direct_file' 만 권장).
    """
    import requests

    dest = Path(dest_dir).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60, allow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0"}) as r:
        r.raise_for_status()
        path = dest / _filename_from(r, url)
        if path.exists() and not overwrite:
            return {"url": url, "path": str(path), "status": "skipped"}
        with open(path, "wb") as fh:
            for chunk in r.iter_content(1 << 16):
                fh.write(chunk)
    return {"url": url, "path": str(path), "status": "downloaded",
            "size": path.stat().st_size}
