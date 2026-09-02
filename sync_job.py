#!/usr/bin/env python
"""자동 동기화 잡 — cron/launchd 등 스케줄러가 주기 실행한다.

store.sync_all() 을 돌리고 결과를 store/sync.log 에 JSON 한 줄로 누적 기록한다.
성공 0 / 실패 1 로 종료한다.

스냅샷까지 포함하면 한 번에 수 분이 걸려 다음 실행과 겹칠 수 있으므로, 잠금 파일로
동시 실행을 막는다(겹치면 그 회차는 건너뛴다) — 겹치면 SQLite 가 'database is locked'
로 실패한다.

수동 실행:  .venv/bin/python sync_job.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import store

LOG = Path(__file__).resolve().parent / "store" / "sync.log"
LOCK = Path(__file__).resolve().parent / "store" / "sync.lock"
STALE_SEC = 3600  # 이 시간보다 오래된 잠금은 죽은 프로세스의 것으로 보고 무시


def _acquire() -> bool:
    """잠금을 잡으면 True. 이미 도는 중이면 False."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < STALE_SEC:
        return False
    LOCK.write_text(str(os.getpid()))
    return True


def main() -> int:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not _acquire():
        line = {"ts": ts, "ok": True, "skipped": "already running"}
        print(json.dumps(line, ensure_ascii=False))
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        return 0
    try:
        result = store.sync_all()
        line = {"ts": ts, "ok": True, "result": result}
        code = 0
    except Exception as e:
        line = {"ts": ts, "ok": False, "error": str(e), "trace": traceback.format_exc()}
        code = 1

    LOCK.unlink(missing_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in line.items() if k != "trace"}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
