"""In-process background jobs (spec 13.3, 7.16). Upgrade path: RQ or Celery.

A job is a row in ``jobs`` plus a function running on a small thread pool. The function
gets a ``JobContext``: call ``progress(stage, fraction, message)`` as work advances and
``cancelled()`` (or ``check()``) to stop early. The row is the source of truth; a
condition variable only wakes the event streams.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterator, Optional

from sqlalchemy import update

from . import schemas
from .db import Database, JobRow, dumps, loads, new_id, now_iso, owned
from .errors import body_for_exception, error_body, log_exception, not_found

TERMINAL = frozenset({"done", "failed", "cancelled", "awaiting_input"})


class JobCancelled(BaseException):
    """Raised inside a job once cancel was requested. A BaseException on purpose: the
    pipeline's fail-soft ``except Exception`` blocks must not swallow it."""


class JobContext:
    def __init__(self, manager: "JobManager", job_id: str, user_id: str):
        self._manager = manager
        self.job_id = job_id
        self.user_id = user_id
        self._last = (None, -1.0, 0.0)  # stage, fraction, time of the last stored update

    def cancelled(self) -> bool:
        return self._manager.is_cancelled(self.job_id)

    def check(self) -> None:
        if self.cancelled():
            raise JobCancelled()

    def progress(self, stage: str, fraction: float = 0.0, message: str = "") -> None:
        self.check()
        try:
            fraction = min(1.0, max(0.0, float(fraction)))
        except (TypeError, ValueError):
            fraction = 0.0
        stage_was, fraction_was, at = self._last
        now = time.monotonic()
        if stage == stage_was and abs(fraction - fraction_was) < 0.01 and now - at < 0.2:
            return  # a chatty stage: do not write the row for every frame
        self._last = (stage, fraction, now)
        self._manager.update(self.job_id, stage=stage, progress=fraction, message=message or None)


JobFn = Callable[[JobContext], Optional[dict[str, str]]]


class JobManager:
    def __init__(self, db: Database, workers: int = 2):
        self.db = db
        self._workers = workers
        self._executor: Optional[ThreadPoolExecutor] = None
        self._cond = threading.Condition()
        self._versions: dict[str, int] = {}
        self._cancelled: set[str] = set()
        self._active: set[str] = set()

    # --- lifecycle ---------------------------------------------------------

    def recover(self) -> int:
        """Jobs left unfinished by a previous process can never finish: fail them, retryable."""
        error = dumps(error_body("interrupted", "The server restarted before this job finished.", "Run it again.", True))
        with self.db.session() as s:
            result = s.execute(
                update(JobRow).where(JobRow.status.in_(("queued", "running")))
                .values(status="failed", error=error, message="Interrupted by a server restart.", updated_at=now_iso())
            )
            return result.rowcount or 0

    def shutdown(self) -> None:
        with self._cond:
            self._cancelled |= self._active  # running jobs stop at their next progress call
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    # --- running -----------------------------------------------------------

    def submit(self, user_id: str, type: str, fn: JobFn, *, message: Optional[str] = None,
               on_cancel: Optional[Callable[[], None]] = None) -> str:
        """Queue ``fn``. It returns a JobResult dict ({kind, id}) or None. ``on_cancel``
        runs if the job is cancelled, whether or not ``fn`` ever started."""
        job_id = new_id()
        now = now_iso()
        with self.db.session() as s:
            s.add(JobRow(id=job_id, user_id=user_id, type=type, status="queued", progress=0.0,
                         message=message, created_at=now, updated_at=now))
        with self._cond:
            self._active.add(job_id)
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=self._workers, thread_name_prefix="orin-job")
            executor = self._executor
        executor.submit(self._run, job_id, user_id, fn, on_cancel)
        return job_id

    def _run(self, job_id: str, user_id: str, fn: JobFn, on_cancel: Optional[Callable[[], None]]) -> None:
        ctx = JobContext(self, job_id, user_id)
        try:
            ctx.check()
            self.update(job_id, status="running")
            result = fn(ctx)
            ctx.check()
        except JobCancelled:
            self.update(job_id, status="cancelled", message="Cancelled.")  # no-op when cancel() already did it
            if on_cancel is not None:
                try:
                    on_cancel()
                except Exception as exc:
                    log_exception(f"job {job_id} cancel hook", exc)
        except Exception as exc:
            body = body_for_exception(exc)
            if body["code"] == "internal_error":
                log_exception(f"job {job_id}", exc)
            self.update(job_id, status="failed", error=dumps(body), message=body["message"])
        else:
            self.update(job_id, status="done", progress=1.0, result_ref=dumps(result) if result else None)
        finally:
            with self._cond:
                self._active.discard(job_id)
                self._cancelled.discard(job_id)

    def update(self, job_id: str, **fields: Any) -> bool:
        """Change a live job and wake its streams. A job at a terminal status is frozen."""
        with self.db.session() as s:
            row = s.get(JobRow, job_id)
            if row is None or row.status in TERMINAL:
                return False
            if "progress" in fields:  # never let the bar run backwards
                fields["progress"] = max(row.progress or 0.0, fields["progress"])
            for key, value in fields.items():
                setattr(row, key, value)
            row.updated_at = now_iso()
        with self._cond:
            self._versions[job_id] = self._versions.get(job_id, 0) + 1
            self._cond.notify_all()
        return True

    def is_cancelled(self, job_id: str) -> bool:
        with self._cond:
            return job_id in self._cancelled

    # --- reading -----------------------------------------------------------

    @staticmethod
    def to_schema(row: JobRow) -> schemas.Job:
        return schemas.Job(
            id=row.id, type=row.type, status=row.status,  # type: ignore[arg-type]
            progress=round(float(row.progress or 0.0), 4), stage=row.stage, message=row.message,
            error=loads(row.error), result=loads(row.result_ref),
            created_at=row.created_at, updated_at=row.updated_at,
        )

    def get(self, user_id: str, job_id: str) -> schemas.Job:
        with self.db.session() as s:
            row = owned(s, JobRow, user_id, job_id)
            if row is None:
                raise not_found("That job")
            return self.to_schema(row)

    def cancel(self, user_id: str, job_id: str) -> schemas.Job:
        job = self.get(user_id, job_id)
        if job.status not in TERMINAL:
            with self._cond:
                self._cancelled.add(job_id)
            self.update(job_id, status="cancelled", message="Cancelled.")
        return self.get(user_id, job_id)

    def events(self, user_id: str, job_id: str, heartbeat_s: float = 15.0) -> Iterator[Optional[schemas.Job]]:
        """The job now, then again on every change, ending at a terminal status.
        Yields None as a keep-alive when nothing changed for ``heartbeat_s``."""
        with self._cond:
            seen = self._versions.get(job_id, 0)
        job = self.get(user_id, job_id)
        yield job
        sent_at = time.monotonic()
        while job.status not in TERMINAL:
            with self._cond:
                self._cond.wait_for(lambda: self._versions.get(job_id, 0) != seen, timeout=1.0)
                seen = self._versions.get(job_id, 0)
            latest = self.get(user_id, job_id)  # also re-read on timeout, in case a wake-up was missed
            if latest != job:
                job = latest
                sent_at = time.monotonic()
                yield job
            elif time.monotonic() - sent_at > heartbeat_s:
                sent_at = time.monotonic()
                yield None
