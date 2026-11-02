# snuETL-mcp

SNU eTL(Canvas LMS)을 MCP 도구로 노출하는 Python MCP 서버. 강의·과제·공지·성적을
조회하고, 마감일/공지를 로컬에 **누적 저장**하며, 강의 자료를 학교/학기/과목 폴더 구조에 맞춰
다운로드·정리한다.

## 구성

| 파일 | 역할 |
|------|------|
| `server.py` | FastMCP 진입점 — 도구 등록 |
| `canvas_client.py` | Canvas 인증 + raw 조회 → dict 변환 |
| `store.py` | SQLite 누적 저장소 (courses/announcements/assignments + change_history) |
| `materials.py` | 강의 자료 다운로드(폴더 구조 매핑) + 과목 폴더 정리 |
| `links.py` | 본문(페이지·공지·과제) 속 링크 자료 스캔 — 파일 탭에 안 뜨는 파일 |
| `lti.py` | LearningX LTI 공통 진입점 (도구 id 탐색·런치·API) |
| `boards.py` | 게시판·열린게시판(LTI 두 종류) 글/첨부/댓글 |
| `weekly.py` | 주차학습 항목과 내 진도·출결 상태 |
| `snapshot.py` | 강의 탭 전체를 훑는 화면 스냅샷 + 커버리지 |
| `write.py` | 쓰기 — 과제 제출·토론 답글·게시판 글/댓글 (기본 비활성) |
| `video.py` | 영상 강의(LearningX→LCMS→유튜브) 대본 추출 |
| `sync_job.py` | 자동 동기화 잡 (cron 실행, 결과를 `store/sync.log`에 누적) |
| `DESIGN.md` | 설계 문서 |

## 설정

`.env` (git 제외):

```
ETL_BASE_URL=https://myetl.snu.ac.kr
ETL_TOKEN=<개인 액세스 토큰>
ETL_DOWNLOAD_DIR=/Users/.../학교   # 강의 자료 저장 기본 경로
```

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 실행

```bash
.venv/bin/python server.py        # MCP 서버 (stdio)
.venv/bin/mcp dev server.py       # MCP Inspector 디버깅
.venv/bin/python sync_job.py      # 동기화 1회 수동 실행
```

MCP 클라이언트 등록:

```bash
claude mcp add snu-etl -- /abs/path/.venv/bin/python /abs/path/server.py
```

## 도구 (v0.1, 18개)

- **조회**: `whoami`, `list_courses`, `list_assignments`, `get_upcoming`,
  `list_announcements`, `get_grades`, `list_files`, `list_linked_files`,
  `get_submissions`(과제별 점수·코멘트), `get_attendance`, `list_weekly_items`,
  `get_textbooks`, `get_page`, `list_conversations`, `list_boards`,
  `list_board_posts`, `get_board_post`
- **화면 전체**: `get_course_snapshot`(사이트에 보이는 모든 탭을 한 번에),
  `sync_snapshot`, `get_stored_snapshot`
- **누적 저장**: `sync_all`, `sync_courses`, `sync_announcements`, `sync_assignments`,
  `get_stored_courses`, `get_stored_announcements`, `get_stored_assignments`,
  `get_change_history`
- **강의 자료**: `download_course_files`(본문 링크 자료·과제 첨부 포함),
  `organize_course_files`
- **영상 대본**: `list_lecture_videos`, `get_lecture_transcript`(eTL 영상→유튜브 자막),
  `get_youtube_transcript`(공개 유튜브 링크 범용)

### 파일 탭에 안 뜨는 자료

교수가 페이지·공지 본문에 파일을 **링크로만** 걸면 Canvas 가 그 파일을 hidden 으로
두어 eTL '파일' 탭과 `list_files` 에 나오지 않는다. `list_linked_files` 가
강의계획서·페이지·과제·퀴즈·공지·토론 본문과 모듈 항목을 훑어 이런 파일과 외부
링크(구글드라이브·유튜브·LCMS·직접 파일 URL)를 찾아내고, `download_course_files`
는 기본값(`include_linked=True`)으로 이들을 함께 내려받는다(출처는 `via` 에 표시).

```
include_embedded_images=True   # 본문 삽입 이미지(<img>)까지
include_external_links=True    # 외부 사이트 직접 파일 링크(pdf 등)까지
```

### 사이트 화면 전체 읽기

`GET /courses/{id}/tabs`(학생이 실제로 보는 메뉴)를 기준으로 모든 탭을 훑는다.
탭별로 읽기 성공 여부와 건수를 `coverage` 로 함께 주므로, "사이트엔 있는데 못 읽는 것"이
결과에 그대로 드러난다.

| 탭 | 읽는 경로 |
|---|---|
| 홈·공지·과제·모듈·페이지·수강생·퀴즈 | Canvas API |
| 성적 | `students/submissions` (과제별 점수·만점·지각·채점 코멘트) |
| 강의계획서 / 강의교재 | sugang `cc103ajax` / `cc103bkajax` (Referer 필요, SSO 불필요) |
| 주차학습 / 강의·출결 | LearningX LTI — 항목별 완료·출결 상태 |
| 게시판 / 열린게시판 | LearningX LTI 두 종류 모두 |
| 파일 | 파일 탭 + 본문 링크 자료(hidden) |

`sync_snapshot` 은 이 스냅샷을 SQLite 에 저장하고, 다음 동기화 때 달라진 항목을
`change_history` 에 남긴다(예: `성적 / 추가 2 → "Homework 3; Homework 4"`).
`sync_all` 은 기본으로 이번 학기 과목의 스냅샷까지 갱신한다(`snapshot=False` 로 생략).

## 쓰기(상호작용)

기본은 **꺼져 있다**. `.env` 에 `ETL_ENABLE_WRITE=1` 을 넣고 서버를 다시 시작해야
쓰기 도구가 등록된다. 그 위에 한 겹 더: 모든 쓰기 도구는 `confirm=True` 가 없으면
**무엇이 어디에 올라가는지 미리보기만** 돌려준다.

| 도구 | 되돌리기 |
|---|---|
| `submit_assignment`(파일/텍스트/URL) | **불가** — 재제출은 새 시도로 쌓임 |
| `reply_discussion`, `create_discussion` | eTL 설정에 따라 다름 |
| `create_board_post` / `create_board_comment` | `delete_board_post` / `delete_board_comment` |

출결·영상 시청 기록 쓰기는 넣지 않았다(시청하지 않은 강의를 시청 처리하는 용도가 되어
출결 기록 위조가 된다). 진도 확인은 읽기 쪽 `get_attendance`·`list_weekly_items` 로 한다.

## 자동 실행

`sync_job.py`를 cron으로 매시 실행하여 공지·마감 변경을 자동 누적한다:

```
0 * * * * /abs/.venv/bin/python /abs/sync_job.py >> /abs/store/cron.out 2>&1 # snuETL-mcp-sync
```

## 자료 저장 규칙

`ETL_DOWNLOAD_DIR`를 학교 폴더로 지정하면 `2026-2/실험언어학/파일명` 형태로
저장합니다. 여름과 겨울학기는 각각 `2026-S`, `2026-W`입니다.
강의록·과제물 하위 폴더는 자동으로 만들지 않습니다.
같은 과목이 다른 학기에 있어도 해당 학기 폴더에 저장합니다.

`course_names.json`에 eTL 과목명과 한글 이름을 추가하세요.
학기 접두사와 분반 번호는 자동 제거하며, 매핑하지 않은 이름은 그대로 사용합니다.
현재 `Experimental Linguistics`는 `실험언어학`으로 설정되어 있습니다.
JSON은 호출할 때 읽으므로 이름 변경 후 서버를 다시 시작할 필요가 없습니다.
이 설정은 일정과 캘린더의 과목명에도 적용됩니다.
