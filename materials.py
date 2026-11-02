"""강의 자료를 <학교>/<학기>/<한글 과목명>/에 저장한다."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Optional

import canvas_client as cc
import links

# 다운로드 기본 경로 fallback. 실제 경로는 .env 의 ETL_DOWNLOAD_DIR 로 지정한다.
SNU_BASE = os.path.expanduser(os.environ.get("ETL_DOWNLOAD_DIR", "") or "./downloads")

# eTL: 1=1학기, 2=2학기, 3=여름, 4=겨울.
_TERM_MAP = {1: "1", 2: "2", 3: "S", 4: "W"}
_ALIASES_PATH = Path(__file__).with_name("course_names.json")


def _safe(name: Optional[str]) -> str:
    safe = re.sub(r'[/\\:*?"<>|]', "_", (name or "untitled")).strip()
    return "untitled" if safe in {"", ".", ".."} else safe


def _norm(s: Optional[str]) -> str:
    """폴더명 매칭용 정규화 (Unicode NFC + 공백 제거).

    macOS 는 한글 파일명을 NFD 로 저장하므로 NFC 로 통일해 비교한다.
    """
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", s or ""))


def clean_course_name(name: Optional[str]) -> str:
    """eTL 강의명에서 앞 학기코드와 뒤 분반 번호를 제거한다."""
    n = re.sub(r"^\d{4}-\d+\s+", "", name or "")
    n = re.sub(r"\s*\(\d+\)\s*$", "", n)
    return n.strip()


def display_course_name(name: Optional[str]) -> str:
    """course_names.json의 이름을 폴더·일정·캘린더에 공통 적용한다."""
    aliases = json.loads(_ALIASES_PATH.read_text(encoding="utf-8"))
    if not isinstance(aliases, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) or not v.strip()
        for k, v in aliases.items()
    ):
        raise ValueError("course_names.json은 과목명: 한글 이름 형식의 객체여야 합니다.")
    clean = clean_course_name(name)
    return {_norm(k): v for k, v in aliases.items()}.get(_norm(clean), clean)


def _term_folder(course_name: str) -> Optional[str]:
    """eTL 강의명 접두(2025-2 등)를 사용자 학기 폴더명으로 변환한다."""
    m = re.match(r"^(\d{4})-(\d+)", course_name or "")
    if not m:
        return None
    year, code = m.group(1), int(m.group(2))
    return f"{year}-{_TERM_MAP.get(code, str(code))}"


def resolve_course_dir(course_name: str, base: str) -> tuple[Path, str]:
    """과목의 저장 폴더를 결정한다. (경로, 'existing'|'computed') 반환.

    해당 학기 안에서 같은 과목명 폴더를 재사용한다.
    """
    base_p = Path(base).expanduser()
    clean = display_course_name(course_name)  # 학기코드·분반 제거 + 별칭 치환(공용)
    target = _norm(clean)
    tf = _term_folder(course_name)

    matches = []
    if base_p.exists():
        for term in base_p.iterdir():
            if not term.is_dir() or (tf and term.name != tf):
                continue
            for cdir in term.iterdir():
                if cdir.is_dir() and _norm(cdir.name) == target:
                    matches.append(cdir)

    if len(matches) == 1:
        return matches[0], "existing"
    if matches:  # 여러 개면 학기 폴더로 구분 (NFC 정규화 비교)
        tf_n = unicodedata.normalize("NFC", tf) if tf else None
        for m in matches:
            if tf_n and unicodedata.normalize("NFC", m.parent.name) == tf_n:
                return m, "existing"
    # 신규: 계산된 학기 폴더 사용
    if tf:
        return base_p / tf / _safe(clean), "computed"
    return base_p / _safe(clean), "computed"


# 영상 등 받기 싫을 수 있는 큰 미디어 확장자 (skip_video=True 시 제외 대상)
VIDEO_EXTS = {
    ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".ts", ".m2ts", ".vob", ".ogv",
}


def _ext(name: str) -> str:
    """파일명에서 소문자 확장자(점 포함)를 반환한다. 없으면 ''."""
    return Path(name).suffix.lower()


def _norm_exts(exts: Optional[list[str]]) -> Optional[set[str]]:
    """확장자 목록을 정규화한다('mp4'·'.MP4' -> '.mp4'). None 이면 None."""
    if exts is None:
        return None
    out = set()
    for e in exts:
        e = (e or "").strip().lower()
        if not e:
            continue
        out.add(e if e.startswith(".") else "." + e)
    return out


def _passes_filter(
    f,
    target: Path,
    *,
    include_ext: Optional[set[str]],
    exclude_ext: Optional[set[str]],
    max_size_mb: Optional[float],
    skip_video: bool,
    only_ids: Optional[set[int]],
    exclude_ids: Optional[set[int]],
) -> bool:
    """파일이 싱크 대상에 포함되는지 판정한다(취사선택 필터).

    only_ids 가 주어지면 그 목록만이 유일한 기준이다(명시 선택 우선).
    그 외에는 exclude_ids → skip_video → include/exclude_ext → max_size 순으로 거른다.
    """
    fid = getattr(f, "id", None)
    if only_ids is not None:
        return fid in only_ids
    if exclude_ids and fid in exclude_ids:
        return False
    ext = _ext(target.name)
    if skip_video and ext in VIDEO_EXTS:
        return False
    if include_ext is not None and ext not in include_ext:
        return False
    if exclude_ext and ext in exclude_ext:
        return False
    if max_size_mb is not None:
        size = getattr(f, "size", None)
        if size is not None and size > max_size_mb * 1024 * 1024:
            return False
    return True


def _file_info(f, target: Path, root: Path, selected: bool, via: Optional[dict] = None) -> dict:
    """미리보기/리포트용 파일 메타데이터. via 는 본문 링크로 찾은 경우의 출처."""
    size = getattr(f, "size", None)
    try:
        rel = str(target.relative_to(root))
    except ValueError:
        rel = target.name
    return {
        "id": getattr(f, "id", None),
        "name": target.name,
        "rel_path": rel,
        "ext": _ext(target.name),
        "size_mb": round(size / 1024 / 1024, 1) if size else None,
        "selected": selected,
        "via": f"{via.get('type')}: {via.get('title')}" if via else None,
    }


def _course_targets(
    course_id: int,
    base: Optional[str],
    include_linked: bool = True,
    include_embedded_images: bool = False,
    preserve_folders: bool = False,
):
    """(course, course_name, root, how, [(file, target_path, via), ...]) 를 계산한다.

    다운로드와 정리(organize)가 동일한 대상 경로 계산을 공유하도록 분리.
    include_linked=True 면 파일 목록(Files)에 안 뜨고 페이지·공지·과제 본문에 링크로만
    걸린 파일까지 합친다(via 에 출처를 담는다). 이런 파일은 Canvas 폴더 정보가 없으므로
    과목 폴더 최상위에 저장한다.
    """
    canvas = cc.get_canvas()
    course = canvas.get_course(course_id)
    course_name = getattr(course, "name", str(course_id))
    base = base or os.environ.get("ETL_DOWNLOAD_DIR") or SNU_BASE
    root, how = resolve_course_dir(course_name, base)

    folder_map: dict[int, str] = {}
    try:
        for fol in course.get_folders():
            folder_map[fol.id] = getattr(fol, "full_name", "") or ""
    except Exception:
        pass

    targets = []
    seen_ids = set()
    for f in course.get_files():
        seen_ids.add(f.id)
        disp = _safe(getattr(f, "display_name", None) or getattr(f, "filename", f"file_{f.id}"))
        full = folder_map.get(getattr(f, "folder_id", None), "")
        rel = re.sub(r"^course files/?", "", full) if preserve_folders else ""
        if Path(rel).is_absolute() or ".." in Path(rel).parts:
            raise ValueError(f"잘못된 Canvas 폴더 경로: {full}")
        target = (root / rel / disp) if rel else (root / disp)
        targets.append((f, target, None))

    # 파일 목록에 안 뜨고 본문(페이지·공지·과제·모듈)에 링크로만 걸린 파일도 포함
    if include_linked:
        try:
            for f, via in links.linked_file_objects(
                course, include_embedded=include_embedded_images
            ):
                if f.id in seen_ids:
                    continue
                seen_ids.add(f.id)
                disp = _safe(
                    getattr(f, "display_name", None)
                    or getattr(f, "filename", f"file_{f.id}")
                )
                targets.append((f, root / disp, via))
        except Exception:
            pass  # 링크 스캔 실패해도 파일 목록 다운로드는 진행

    paths = set()
    for _, target, _ in targets:
        key = unicodedata.normalize("NFC", str(target)).casefold()
        if key in paths:
            raise ValueError(f"같은 저장 경로의 파일이 여러 개입니다: {target}")
        paths.add(key)
    return course, course_name, root, how, targets


def download_course_files(
    course_id: int,
    dest_dir: Optional[str] = None,
    overwrite: bool = False,
    dry_run: bool = False,
    skip_video: bool = False,
    include_ext: Optional[list[str]] = None,
    exclude_ext: Optional[list[str]] = None,
    max_size_mb: Optional[float] = None,
    only_file_ids: Optional[list[int]] = None,
    exclude_file_ids: Optional[list[int]] = None,
    include_linked: bool = True,
    include_embedded_images: bool = False,
    include_external_links: bool = False,
) -> dict:
    """강의 자료를 학교 폴더 구조(<base>/<학기>/<과목명>/)에 맞춰 다운로드한다.

    dest_dir 로 base 를 바꿀 수 있다(생략 시 ETL_DOWNLOAD_DIR 또는 SNU_BASE).
    같은 크기 파일이 이미 있으면 건너뛴다(증분).

    취사선택(어떤 파일을 받을지) 필터:
      - skip_video: 영상 확장자(mp4·mov 등)를 제외.
      - include_ext: 이 확장자만 받는다(예 ['pdf','pptx']). '.' 유무 무관.
      - exclude_ext: 이 확장자는 제외.
      - max_size_mb: 이 용량(MB)을 넘는 파일은 제외(영상 등 대용량 차단에 유용).
      - only_file_ids: 이 파일 id 만 받는다(콕 집어 선택, 다른 필터 무시).
      - exclude_file_ids: 이 파일 id 는 제외.
    파일 id·크기는 dry_run=True 나 list_files 로 미리 확인할 수 있다.

    본문 링크로만 걸린 자료(파일 목록에 안 뜨는 것):
      - include_linked(기본 True): 페이지·공지·과제·모듈 본문에 링크로 걸린 eTL 파일을
        함께 받는다. 각 파일의 출처는 via 로 표시된다.
      - include_embedded_images: 본문에 삽입된 이미지(<img>)까지 받는다(기본 제외).
      - include_external_links: 외부 사이트의 직접 파일 링크(pdf 등)도 받는다(기본 제외).
        구글드라이브·유튜브 같은 페이지형 링크는 대상이 아니며 목록으로만 보고한다.

    dry_run=True 면 다운로드 없이 결정된 경로와 파일 목록(필터 적용 결과 selected
    표시)을 반환하므로, 무엇을 받을지 사용자에게 보여주고 고르게 할 수 있다.
    """
    course, course_name, root, how, targets = _course_targets(
        course_id, dest_dir, include_linked=include_linked,
        include_embedded_images=include_embedded_images,
    )

    inc = _norm_exts(include_ext)
    exc = _norm_exts(exclude_ext)
    only_ids = set(only_file_ids) if only_file_ids is not None else None
    excl_ids = set(exclude_file_ids) if exclude_file_ids else None

    selected, excluded = [], []
    for f, target, via in targets:
        ok = _passes_filter(
            f, target, include_ext=inc, exclude_ext=exc, max_size_mb=max_size_mb,
            skip_video=skip_video, only_ids=only_ids, exclude_ids=excl_ids,
        )
        (selected if ok else excluded).append((f, target, via))

    ext_links = _external_file_links(course_id) if include_external_links else []

    if dry_run:
        files = [_file_info(f, t, root, True, v) for f, t, v in selected]
        files += [_file_info(f, t, root, False, v) for f, t, v in excluded]
        out = {
            "course": course_name, "resolved_dir": str(root), "match": how,
            "selected": len(selected), "excluded": len(excluded),
            "linked_only": sum(1 for f, t, v in selected if v),
            "files": files,
        }
        if include_external_links:
            out["external_files"] = ext_links
        return out

    downloaded, skipped, errors = [], [], []
    for f, target, via in selected:
        target.parent.mkdir(parents=True, exist_ok=True)
        size = getattr(f, "size", None)
        if target.exists() and not overwrite and (
            size is None or target.stat().st_size == size
        ):
            skipped.append(str(target))
            continue
        try:
            f.download(str(target))
            downloaded.append(str(target))
        except Exception as e:
            errors.append({"file": target.name, "error": str(e)})

    result = {
        "course": course_name, "resolved_dir": str(root), "match": how,
        "downloaded": len(downloaded), "skipped": len(skipped),
        "linked_only": sum(1 for f, t, v in selected if v),
        "excluded_by_filter": len(excluded), "errors": errors,
    }
    if include_external_links:
        ext_results = []
        for e in ext_links:
            try:
                ext_results.append({**links.download_external_file(
                    e["url"], str(root), overwrite=overwrite), "via": e["via"]})
            except Exception as exc:
                ext_results.append({"url": e["url"], "status": "error", "error": str(exc)})
        result["external_files"] = ext_results
    return result


def _external_file_links(course_id: int) -> list[dict]:
    """외부 사이트에 걸린 '직접 파일 링크'(pdf 등)만 추린다."""
    try:
        scan = links.scan_course_links(course_id, resolve=False)
    except Exception:
        return []
    out = []
    for e in scan["external_links"]:
        if e["kind"] != "direct_file":
            continue
        src = (e["sources"] or [{}])[0]
        out.append({"url": e["url"], "text": e.get("text"),
                    "via": f"{src.get('type')}: {src.get('title')}"})
    return out


# --- 정리 (Organize) — snuETL-mcp 이 받은 파일만 분류 이동 -----------------------

def organize_course_files(
    course_id: int,
    dest_dir: Optional[str] = None,
    mapping: Optional[dict] = None,
    dry_run: bool = False,
) -> dict:
    """Canvas 파일을 과목 폴더 바로 아래로 정리한다.

    Canvas 파일 목록으로 대상 경로를 재계산하므로 사용자의 다른 작업물(코드·venv 등)은
    건드리지 않는다. mapping={파일명: 카테고리} 를 주면 해당 파일만 하위 폴더로 옮긴다.
    dry_run=True 면 이동 계획만 반환한다. 이미 목적지에 있는 파일은 건너뛴다.
    """
    mapping = mapping or {}
    _, course_name, root, _, targets = _course_targets(
        course_id, dest_dir, preserve_folders=True
    )
    cats = {"강의록", "과제물"}

    planned, moved, skipped, missing = [], [], [], []
    for f, target, _via in targets:
        if not target.exists():
            missing.append(str(target))
            continue
        category = mapping.get(target.name, "")
        if category not in cats | {""}:
            raise ValueError("mapping 값은 '강의록', '과제물', 빈 문자열만 가능합니다.")
        dest = root / category / target.name
        if dest.resolve() == target.resolve():
            skipped.append(str(target))
            continue
        # 충돌 시 숫자 접미사
        if dest.exists() and dest.resolve() != target.resolve():
            i = 2
            while (root / category / f"{dest.stem} ({i}){dest.suffix}").exists():
                i += 1
            dest = root / category / f"{dest.stem} ({i}){dest.suffix}"
        planned.append({"from": str(target), "to": str(dest), "category": category})
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                target.rename(dest)
                moved.append(str(dest))
            except Exception as e:
                missing.append(f"{target} (move failed: {e})")

    return {
        "course": course_name, "root": str(root), "dry_run": dry_run,
        "lecture": sum(1 for p in planned if p["category"] == "강의록"),
        "assignment": sum(1 for p in planned if p["category"] == "과제물"),
        "skipped_already_organized": len(skipped),
        "missing_not_downloaded": len(missing),
        "plan": planned,
    }
