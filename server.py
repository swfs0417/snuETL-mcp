"""SNU eTL (Canvas) MCP 서버 — Phase 1: 읽기 전용 도구.

실행:
    .venv/bin/python server.py            # stdio 모드 (Claude Desktop 등 연결용)
    .venv/bin/mcp dev server.py           # MCP Inspector 로 디버깅
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

import boards
import canvas_client as cc
import links
import materials
import schedule
import snapshot as snap
import store
import video
import weekly
import write

mcp = FastMCP("snu-etl")


@mcp.tool()
def list_courses(active_only: bool = True) -> list[dict]:
    """수강 중인 강의 목록을 반환한다. active_only=False 면 지난 학기 포함."""
    return cc.list_courses(active_only=active_only)


@mcp.tool()
def list_assignments(course_id: int) -> list[dict]:
    """특정 강의(course_id)의 과제 목록과 마감일을 반환한다."""
    return cc.list_assignments(course_id)


@mcp.tool()
def get_upcoming() -> list[dict]:
    """마감이 다가오는 과제·할 일(To-Do) 목록을 반환한다 (전체 강의 통합)."""
    return cc.get_upcoming()


@mcp.tool()
def list_announcements(course_id: int) -> list[dict]:
    """특정 강의(course_id)의 공지사항 목록을 반환한다."""
    return cc.list_announcements(course_id)


@mcp.tool()
def list_discussions(course_id: int) -> list[dict]:
    """특정 강의(course_id)의 토론 게시판(열린게시판) 글 목록을 반환한다.

    공지(list_announcements)와 별개로, eTL '열린게시판'에 올라오는 글이 여기에 잡힌다.
    각 글의 제목·작성자·작성일·답글 수를 주며, 본문과 답글 전체는 get_discussion 으로 본다.
    """
    return cc.list_discussions(course_id)


@mcp.tool()
def get_discussion(
    course_id: int, topic_id: int, include_replies: bool = True
) -> dict:
    """토론 게시판 글 1건(topic_id)의 본문과 댓글·답글(중첩 포함)을 반환한다.

    topic_id 는 list_discussions 가 돌려준 각 글의 id 다.
    include_replies=False 면 답글 없이 본문만 빠르게 가져온다.
    """
    return cc.get_discussion(course_id, topic_id, include_replies=include_replies)


@mcp.tool()
def list_boards(course_id: int) -> list[dict]:
    """특정 강의(course_id)의 게시판 목록을 반환한다('게시판'·'열린게시판' 양쪽).

    eTL 게시판은 Canvas 토론이 아니라 LearningX LTI 이고 계정에 두 종류가 있다
    (Q&A·학습 피드백·과제 제출용 게시판 등). 각 항목의 board_id 를 list_board_posts 에 넘긴다.
    """
    return boards.list_boards(course_id)


@mcp.tool()
def list_board_posts(
    course_id: int,
    board_id: str | None = None,
    page_size: int = 50,
    page_index: int = 1,
) -> list[dict]:
    """게시판 글 목록을 반환한다. board_id 생략 시 그 강의의 모든 게시판을 훑는다.

    '열린게시판'(OpenBoard)과 '게시판'(learningx_board)을 같은 형식으로 돌려준다.
    본문 전체·첨부파일·댓글은 get_board_post 로 본다(목록의 content 는 비어 있을 수 있다).
    """
    return boards.list_board_posts(
        course_id, board_id=board_id, page_size=page_size, page_index=page_index
    )


@mcp.tool()
def get_board_post(
    course_id: int, post_id: str, board_id: str | None = None,
    include_comments: bool = True,
) -> dict:
    """게시판 글 1건의 본문·첨부파일·댓글을 반환한다(보완 신청 명단·과제 제출글 등).

    post_id 는 list_board_posts 가 돌려준 각 글의 id. board_id 를 생략하면 강의의
    게시판들을 차례로 뒤진다. 첨부파일에는 Canvas file id 가 들어 있어 다운로드도 된다.
    """
    return boards.get_board_post(
        course_id, post_id, board_id=board_id, include_comments=include_comments
    )


@mcp.tool()
def get_grades(active_only: bool = True) -> list[dict]:
    """강의별 현재 성적(점수) 요약을 반환한다."""
    return cc.get_grades(active_only=active_only)


@mcp.tool()
def list_files(course_id: int) -> list[dict]:
    """특정 강의(course_id)의 강의자료 파일 목록과 다운로드 URL을 반환한다."""
    return cc.list_files(course_id)


@mcp.tool()
def list_linked_files(course_id: int, force: bool = False) -> dict:
    """강의 '파일' 목록에 안 뜨고 본문에 링크로만 걸린 자료를 찾아 반환한다.

    교수가 페이지·공지·과제 본문에 파일을 링크로만 걸면 Canvas 가 그 파일을 hidden 으로
    두어 list_files(파일 탭)에는 안 나온다. 이 도구는 강의계획서·페이지·과제·퀴즈·공지·
    토론 본문과 모듈 항목을 모두 훑어서

      - files: 발견된 eTL 파일(이름·크기·확장자, in_files_list=False 면 파일 탭에 없는 것,
        ref='link' 는 첨부 링크·'embed' 는 본문 삽입 이미지, sources 는 발견 위치)
      - external_links: 외부 링크(kind=direct_file|google_drive|youtube|lcms|other)

    를 돌려준다. 실제 저장은 download_course_files(include_linked=True, 기본값)가 한다.
    결과는 120초간 캐시된다 — 방금 올라온 자료를 확인하려면 force=True.
    """
    return links.scan_course_links(course_id, force=force)


@mcp.tool()
def get_course_snapshot(course_id: int, sections: list[str] | None = None) -> dict:
    """강의 화면 전체를 한 번에 읽는다 — 실제 사이트에 접속했을 때 보이는 내용 전부.

    강의 메뉴(tabs)를 기준으로 홈·공지·강의계획서·강의교재·수강생·모듈·주차학습·게시판·
    과제·성적(내 점수/코멘트)·출결·파일(본문 링크 자료 포함)·페이지를 모두 훑어 담고,
    탭별로 읽기 성공 여부(ok/empty/error)와 건수를 coverage 로 함께 준다.
    sections 로 일부만 고를 수 있다(예 ['grades','attendance','pages']).
    과목당 10~30초 걸리므로, 반복 조회는 sync_snapshot + get_stored_snapshot 를 쓴다.
    """
    return snap.get_course_snapshot(course_id, sections=sections)


@mcp.tool()
def get_submissions(course_id: int, only_graded: bool = False) -> list[dict]:
    """내 과제 제출 상태·점수·교수 코멘트를 반환한다('성적' 화면의 실제 내용).

    get_grades 가 총점만 주는 것과 달리 과제별 점수·만점·제출 시각·지각 여부와
    채점자 코멘트("2. no second order derivative (-10)" 같은 감점 사유)까지 준다.
    """
    return cc.get_submissions(course_id, only_graded=only_graded)


@mcp.tool()
def get_attendance(course_id: int) -> dict:
    """영상강의 출결 상태(출석/지각/결석)와 요약을 반환한다.

    eTL '강의/출결' 화면의 내용으로, Canvas API 로는 안 보이고 LearningX 에만 있다.
    """
    return weekly.get_attendance(course_id)


@mcp.tool()
def list_weekly_items(course_id: int) -> list[dict]:
    """'주차학습' 탭의 주차별 항목 전체(파일·영상·과제·퀴즈·링크)를 순서대로 반환한다.

    각 항목에 내 완료 여부(completed)·출결 상태·마감이 함께 온다 — 사이트 화면과 동일하다.
    """
    return weekly.list_weekly_items(course_id)


@mcp.tool()
def get_textbooks(course_id: int) -> dict:
    """'강의교재' 탭 — 주교재·부교재 목록(ISBN·출판사·도서관 소장 링크)을 반환한다."""
    return cc.get_textbooks(course_id)


@mcp.tool()
def get_page(course_id: int, page_url: str) -> dict:
    """강의 '페이지' 1건의 본문을 반환한다. page_url 은 페이지 주소의 마지막 조각.

    페이지 목록은 get_course_snapshot(course_id, sections=['pages']) 로 본다.
    """
    return cc.get_page(course_id, page_url)


@mcp.tool()
def list_conversations(limit: int = 25, include_messages: bool = False) -> list[dict]:
    """받은 쪽지함(Canvas 대화) 목록을 반환한다. include_messages=True 면 본문까지."""
    return cc.list_conversations(limit=limit, include_messages=include_messages)


@mcp.tool()
def list_modules(course_id: int) -> list[dict]:
    """특정 강의(course_id)의 주차/모듈 구조와 각 모듈 항목을 순서대로 반환한다.

    '1주차 모듈' 같은 주차 단위로 파일·과제·외부링크가 묶여 있어,
    주차별 강의록 정리나 노션/문서 적재의 골격으로 쓴다.
    """
    return cc.list_modules(course_id)


@mcp.tool()
def get_syllabus(course_id: int, fetch_content: bool = True) -> dict:
    """특정 강의(course_id)의 공식 강의계획서(SNU 수강신청 시스템)를 조회한다.

    eTL '강의계획서' 탭이 띄우는 sugang.snu.ac.kr 공식 강의계획서에서 강의시간·강의실·
    평가비율(출석/과제/시험 등)·회차별 강의계획·첨부파일과 official_url 을 가져온다.
    SNU 로그인 없이 조회되며, fetch_content=False 면 링크/메타만 빠르게 반환한다.
    """
    return cc.get_syllabus(course_id, fetch_content=fetch_content)


@mcp.tool()
def whoami() -> dict:
    """현재 토큰의 소유자(로그인 사용자) 정보를 반환한다."""
    return cc.whoami()


# --- 누적 저장 (Storage) — course / 공지 / 과제 통합 --------------------------


@mcp.tool()
def sync_all(active_only: bool = True, snapshot: bool = True) -> dict:
    """eTL 내용을 한 번에 조회해 로컬 저장소에 누적 반영한다(강의·공지·과제 + 화면 전체).

    snapshot=True(기본)면 이번 학기 과목의 모든 탭(성적·출결·게시판·페이지·주차학습·
    파일 등)까지 읽어 사이트와 같은 상태로 맞춘다 — 과목당 10~30초 걸린다.
    공지·과제만 빠르게 갱신하려면 snapshot=False.
    """
    return store.sync_all(active_only=active_only, snapshot=snapshot)


@mcp.tool()
def sync_courses(active_only: bool = True) -> dict:
    """수강 강의 목록을 저장소에 누적 반영한다."""
    return store.sync_courses(active_only=active_only)


@mcp.tool()
def sync_announcements(course_id: int | None = None, active_only: bool = True) -> dict:
    """공지를 저장소에 누적 반영한다(본문 텍스트 저장, 수정 시 이력 기록).

    course_id 생략 시 활성 강의 전체를 동기화한다.
    """
    return store.sync_announcements(course_id=course_id, active_only=active_only)


@mcp.tool()
def sync_discussions(course_id: int | None = None, active_only: bool = True) -> dict:
    """열린게시판(토론) 글을 저장소에 누적 반영한다(본문 저장, 수정·새 답글 시 이력 기록).

    course_id 생략 시 활성 강의 전체를 동기화한다.
    """
    return store.sync_discussions(course_id=course_id, active_only=active_only)


@mcp.tool()
def sync_assignments(course_id: int | None = None, active_only: bool = True) -> dict:
    """과제를 저장소에 누적 반영한다. 마감일이 바뀌면 변경 이력을 누적 저장한다.

    course_id 생략 시 활성 강의 전체를 동기화한다.
    """
    return store.sync_assignments(course_id=course_id, active_only=active_only)


@mcp.tool()
def sync_snapshot(
    course_id: int | None = None,
    active_only: bool = True,
    sections: list[str] | None = None,
    current_term_only: bool = True,
) -> dict:
    """강의 화면 전체를 읽어 저장소에 반영하고, 지난번과 달라진 것을 이력에 남긴다.

    "업데이트하면 실제 사이트와 같은 내용"을 담당하는 도구다. 탭 단위 섹션(공지·과제·
    성적·출결·게시판·파일·페이지…)을 통째로 저장하고, 항목이 추가·수정·삭제되면
    change_history 에 무엇이 바뀌었는지(제목까지) 기록한다.
    course_id 생략 시 이번 학기 강의 전체(과목당 10~30초). 지난 학기까지 포함하려면
    current_term_only=False.
    """
    return store.sync_snapshot(
        course_id=course_id, active_only=active_only, sections=sections,
        current_term_only=current_term_only,
    )


@mcp.tool()
def get_stored_snapshot(course_id: int, section: str | None = None) -> dict:
    """저장된 강의 화면 스냅샷을 반환한다(API 호출 없이 캐시 조회).

    section 생략 시 모든 섹션. 각 섹션에 마지막 동기화·변경 시각이 함께 온다.
    먼저 sync_snapshot 으로 채워두어야 한다.
    """
    return store.get_stored_snapshot(course_id, section=section)


@mcp.tool()
def get_stored_courses() -> list[dict]:
    """로컬 저장소의 강의 목록을 반환한다 (API 호출 없이 캐시 조회)."""
    return store.get_stored_courses()


@mcp.tool()
def get_stored_announcements(course_id: int | None = None) -> list[dict]:
    """로컬 저장소의 공지를 최신순으로 반환한다 (API 호출 없이 캐시 조회)."""
    return store.get_stored_announcements(course_id=course_id)


@mcp.tool()
def get_stored_discussions(course_id: int | None = None) -> list[dict]:
    """로컬 저장소의 열린게시판(토론) 글을 최신순으로 반환한다 (API 호출 없이 캐시 조회)."""
    return store.get_stored_discussions(course_id=course_id)


@mcp.tool()
def get_stored_assignments(course_id: int | None = None) -> list[dict]:
    """로컬 저장소의 과제를 마감일 순으로 반환한다 (API 호출 없이 캐시 조회)."""
    return store.get_stored_assignments(course_id=course_id)


@mcp.tool()
def get_change_history(
    entity_type: str | None = None,
    course_id: int | None = None,
    entity_id: int | None = None,
) -> list[dict]:
    """변경 이력을 시간순으로 반환한다.

    entity_type: 'course' | 'announcement' | 'discussion' | 'assignment' | 'section'
    (생략 시 전체). 마감일 변경·공지 수정·게시판 새 글/답글이 기록되며, 'section' 은
    sync_snapshot 이 남기는 화면 단위 변경(예: '성적' 섹션에 추가 2건)이다.
    """
    return store.get_change_history(
        entity_type=entity_type, course_id=course_id, entity_id=entity_id
    )


# --- 강의록 (Materials) -------------------------------------------------------


@mcp.tool()
def download_course_files(
    course_id: int,
    dest_dir: str | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    skip_video: bool = False,
    include_ext: list[str] | None = None,
    exclude_ext: list[str] | None = None,
    max_size_mb: float | None = None,
    only_file_ids: list[int] | None = None,
    exclude_file_ids: list[int] | None = None,
    include_linked: bool = True,
    include_embedded_images: bool = False,
    include_external_links: bool = False,
) -> dict:
    """강의 자료를 의예과 폴더 구조(<base>/<학기>/<과목명>/)에 맞춰 다운로드한다.

    dest_dir 로 base 를 바꿀 수 있다(생략 시 ETL_DOWNLOAD_DIR 또는 의예과 기본 경로).
    이미 있는 과목 폴더는 재사용하고, 같은 크기 파일은 건너뛴다(증분).

    어떤 파일을 받을지 취사선택하는 필터(영상 등 제외에 사용):
      - skip_video: 영상 확장자(mp4·mov·mkv 등) 제외.
      - include_ext: 이 확장자만 받음(예 ['pdf','pptx']).
      - exclude_ext: 이 확장자 제외.
      - max_size_mb: 이 용량(MB) 초과 파일 제외(대용량 영상 차단).
      - only_file_ids: 이 파일 id 만 받음(콕 집어 선택; 다른 필터 무시).
      - exclude_file_ids: 이 파일 id 제외.

    파일 탭에 안 뜨고 본문에 링크로만 걸린 자료도 기본으로 함께 받는다:
      - include_linked(기본 True): 페이지·공지·과제·모듈 본문의 eTL 파일 링크까지 수집
        (각 파일의 출처는 결과의 via 에 'page: 평가 안내' 식으로 표시).
      - include_embedded_images: 본문에 삽입된 이미지(<img>)까지 받음(기본 제외).
      - include_external_links: 외부 사이트의 직접 파일 링크(pdf 등)도 받음(기본 제외).
    무엇이 링크로만 걸려 있는지는 list_linked_files 로 미리 확인할 수 있다.

    먼저 dry_run=True 로 호출하면 각 파일의 id·크기(MB)·확장자와 현재 필터
    적용 시 선택 여부(selected)를 목록으로 돌려준다 → 사용자에게 보여주고
    고르게 한 뒤, only_file_ids 등으로 실제 다운로드하면 된다.
    """
    return materials.download_course_files(
        course_id, dest_dir=dest_dir, overwrite=overwrite, dry_run=dry_run,
        skip_video=skip_video, include_ext=include_ext, exclude_ext=exclude_ext,
        max_size_mb=max_size_mb, only_file_ids=only_file_ids,
        exclude_file_ids=exclude_file_ids, include_linked=include_linked,
        include_embedded_images=include_embedded_images,
        include_external_links=include_external_links,
    )


@mcp.tool()
def organize_course_files(
    course_id: int,
    dest_dir: str | None = None,
    mapping: dict | None = None,
    dry_run: bool = False,
) -> dict:
    """snuETL-mcp이 받은 강의 파일을 '강의록'/'과제물' 하위 폴더로 분류 이동한다.

    Canvas 파일 목록으로 대상만 계산하므로 사용자의 다른 작업물은 건드리지 않는다.
    규칙 기반 분류가 애매하면 mapping={파일명: '강의록'|'과제물'}로 덮어쓸 수 있다(LLM 판단).
    dry_run=True면 이동 계획만 반환한다.
    """
    return materials.organize_course_files(
        course_id, dest_dir=dest_dir, mapping=mapping, dry_run=dry_run
    )


# --- 영상 강의 대본 (Video transcript) ---------------------------------------


@mcp.tool()
def list_lecture_videos(course_id: int, resolve_source: bool = True) -> list[dict]:
    """특정 강의(course_id)의 영상 강의(lecture_attendance) 목록을 주차·차시 순으로 반환한다.

    eTL 영상은 Canvas 파일이 아니라 LearningX LTI(lecture_attendance) 항목이라 list_files
    로는 안 잡힌다. 이 도구가 LTI 런치를 거쳐 영상 항목을 가져오고, 각 항목의
    attendance_item_id(get_lecture_transcript 에 넘기는 id)·제목·길이와 실제 출처
    (youtube_url/video_id, source_type)를 해석해 준다. SNU 로그인 불필요.
    resolve_source=False 면 LCMS 출처 해석을 생략해 더 빠르다.
    """
    return video.list_lecture_videos(course_id, resolve_source=resolve_source)


@mcp.tool()
def get_lecture_transcript(
    course_id: int,
    item_id: int,
    languages: list[str] | None = None,
    timestamps: bool = False,
    stt: bool = True,
    whisper_model: str | None = None,
    force: bool = False,
) -> dict:
    """eTL 영상 강의 1개(item_id)의 대본(자막)과 메타데이터를 반환한다.

    item_id 는 list_lecture_videos 의 attendance_item_id(또는 Canvas 모듈항목 id)다.
    출처가 유튜브면 유튜브 자막(자동 생성 포함)을 바로 가져오고, 네이티브 LCMS 영상이면
    실제 MP4 에서 오디오만 뽑아 Whisper(STT)로 전사한다(stt=False 면 전사 생략하고 안내만).
    languages 선호 순서(기본 ['ko','en']; STT 는 languages[0]), timestamps=True 면 세그먼트별
    시각도 준다. whisper_model 로 모델 변경, force=True 면 캐시 무시 재전사. STT 는 영상당
    수 분이 걸리며(슬라이드 강의는 large-v3-turbo로 ~실시간의 8~10배 속도) 결과는 캐시된다.
    """
    return video.get_lecture_transcript(
        course_id, item_id, languages=languages, timestamps=timestamps,
        stt=stt, whisper_model=whisper_model, force=force,
    )


@mcp.tool()
def get_youtube_transcript(
    url: str, languages: list[str] | None = None, timestamps: bool = False
) -> dict:
    """공개 유튜브 영상의 대본(자막)을 반환한다 (교수가 직접 올린 링크 등 범용).

    url 은 유튜브 주소 또는 11자 영상ID. eTL 강의 항목이 아니라 본문/공지에 박힌 외부
    유튜브 링크를 바로 처리할 때 쓴다. languages 선호 순서(기본 ['ko','en']),
    timestamps=True 면 세그먼트별 시작시각도 함께 준다.
    """
    return video.get_youtube_transcript(url, languages=languages, timestamps=timestamps)


@mcp.tool()
def get_lcms_transcript(
    em_url_or_id: str,
    languages: list[str] | None = None,
    timestamps: bool = False,
    stt: bool = True,
    whisper_model: str | None = None,
    force: bool = False,
) -> dict:
    """SNU LCMS 영상(lcms.snu.ac.kr/em/{id} 링크 또는 content_id)의 대본을 반환한다(범용).

    eTL 강의 항목이 아니라 공지·모듈 본문에 박힌 LCMS em 링크를 바로 처리할 때 쓴다(예:
    출결 미반영 동영상). 유튜브 출처면 자막을, 네이티브 MP4면 ffmpeg+Whisper STT 로 전사한다.
    stt/whisper_model/force/timestamps 동작은 get_lecture_transcript 과 동일하며 결과는 캐시된다.
    """
    return video.get_lcms_transcript(
        em_url_or_id, languages=languages, timestamps=timestamps,
        stt=stt, whisper_model=whisper_model, force=force,
    )


# --- 일정 (Schedule) — 목표 1 지원 -------------------------------------------


@mcp.tool()
def get_schedule_events(
    start: str | None = None,
    end: str | None = None,
    kinds: list[str] | None = None,
    course_id: int | None = None,
) -> list[dict]:
    """저장소의 구조화된 마감일(과제 due_at 등)을 공용 일정 스키마로 반환한다.

    start/end: 'YYYY-MM-DD' 또는 ISO8601. due 기준으로 [start, end] 범위만(생략 가능).
    kinds: 'assignment'|'quiz'|'exam' 중 일부(생략 시 전체).
    course_id: 지정 시 해당 강의만(생략 시 전체).
    Canvas 가 날짜를 필드로 가진 것만 다루므로 추측이 아니라 정확한 값이다.
    공지 본문 속 일정은 extract_events_from_announcements 를 쓴다.
    먼저 sync_assignments(또는 sync_all)로 저장소를 채워두어야 한다.
    """
    return schedule.get_schedule_events(
        start=start, end=end, kinds=kinds, course_id=course_id
    )


@mcp.tool()
def extract_events_from_announcements(course_id: int | None = None) -> list[dict]:
    """공지 본문에서 일정 후보(날짜·시간·키워드+문맥)를 추출해 반환한다(확정 아님).

    정규식으로 'M월 D일'·숫자 날짜·시간·일정 키워드를 가진 줄만 골라 문맥과 함께 준다.
    상대표현·교시 등 맥락 해석과 최종 확정은 호출하는 LLM 이 한다(needs_confirmation=True).
    캘린더 적재 전 반드시 사람/LLM 확인을 거치는 것을 전제로 한다.
    먼저 sync_announcements(또는 sync_all)로 저장소를 채워두어야 한다.
    """
    return schedule.extract_events_from_announcements(course_id=course_id)


# --- 쓰기 (Write) — .env 의 ETL_ENABLE_WRITE=1 일 때만 등록 ---------------------
#
# 실수 방지를 위해 두 겹으로 막는다: (1) 이 게이트가 없으면 도구가 아예 안 보이고,
# (2) 각 도구는 confirm=True 없이는 미리보기만 돌려준다.

if write.write_enabled():

    @mcp.tool()
    def submit_assignment(
        course_id: int,
        assignment_id: int,
        text: str | None = None,
        url: str | None = None,
        file_paths: list[str] | None = None,
        confirm: bool = False,
    ) -> dict:
        """과제를 제출한다(파일/텍스트/URL 중 과제가 허용하는 형식으로).

        **되돌릴 수 없다.** confirm=False(기본)면 무엇이 어느 과제에 제출될지 미리보기만
        돌려주니, 사용자에게 보여주고 승인받은 뒤 confirm=True 로 다시 호출해야 한다.
        file_paths 는 로컬 절대경로 목록.
        """
        return write.submit_assignment(
            course_id, assignment_id, text=text, url=url,
            file_paths=file_paths, confirm=confirm,
        )

    @mcp.tool()
    def reply_discussion(
        course_id: int, topic_id: int, message: str,
        parent_entry_id: int | None = None, confirm: bool = False,
    ) -> dict:
        """토론 글(topic_id)에 답글을 단다. parent_entry_id 를 주면 대댓글이 된다.

        confirm=False 면 미리보기만 돌려준다(다른 사람에게 보이는 글이므로 승인 필요).
        """
        return write.reply_discussion(
            course_id, topic_id, message,
            parent_entry_id=parent_entry_id, confirm=confirm,
        )

    @mcp.tool()
    def create_discussion(
        course_id: int, title: str, message: str, confirm: bool = False
    ) -> dict:
        """토론 게시판에 새 글을 만든다(학생 작성이 허용된 강의에서만).

        confirm=False 면 미리보기만 돌려준다.
        """
        return write.create_discussion(course_id, title, message, confirm=confirm)

    @mcp.tool()
    def create_board_post(
        course_id: int,
        board_id: str,
        title: str,
        content: str,
        file_paths: list[str] | None = None,
        is_secret: bool = False,
        nickname: str | None = None,
        confirm: bool = False,
    ) -> dict:
        """'게시판'(LearningX)에 글을 올린다 — 과제 제출용 게시판·Q&A 등.

        board_id 는 list_boards 가 돌려준 값. content 는 HTML 가능, file_paths 는 첨부할
        로컬 파일 경로. is_secret=True 면 비밀글(허용된 게시판만).
        confirm=False 면 미리보기만 돌려준다. 올린 글은 delete_board_post 로 지울 수 있다.
        """
        return write.create_board_post(
            course_id, board_id, title, content, file_paths=file_paths,
            is_secret=is_secret, nickname=nickname, confirm=confirm,
        )

    @mcp.tool()
    def create_board_comment(
        course_id: int,
        board_id: str,
        post_id: str,
        content: str,
        parent_comment_id: str | None = None,
        file_paths: list[str] | None = None,
        confirm: bool = False,
    ) -> dict:
        """게시판 글에 댓글을 단다(감상문 제출 등). parent_comment_id 를 주면 대댓글.

        confirm=False 면 미리보기만 돌려준다.
        """
        return write.create_board_comment(
            course_id, board_id, post_id, content,
            parent_comment_id=parent_comment_id, file_paths=file_paths, confirm=confirm,
        )

    @mcp.tool()
    def delete_board_post(
        course_id: int, board_id: str, post_id: str, confirm: bool = False
    ) -> dict:
        """게시판 글을 삭제한다(본인 글). confirm=False 면 미리보기만."""
        return write.delete_board_post(course_id, board_id, post_id, confirm=confirm)

    @mcp.tool()
    def delete_board_comment(
        course_id: int, board_id: str, post_id: str, comment_id: str,
        confirm: bool = False,
    ) -> dict:
        """게시판 댓글을 삭제한다(본인 댓글). confirm=False 면 미리보기만."""
        return write.delete_board_comment(
            course_id, board_id, post_id, comment_id, confirm=confirm
        )


if __name__ == "__main__":
    mcp.run()
