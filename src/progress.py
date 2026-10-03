"""
Thread-safe progress tracking for a running analysis job.

The web API starts an analysis in a background thread and returns a job id
immediately; the browser then polls GET /api/progress/<job_id> to show the
user what the pipeline is actually doing instead of a static spinner. This
module is deliberately small and framework-free so the pipeline can depend on
it without importing Flask.
"""
import threading
import time


class ProgressTracker:
    """Collects timestamped, stage-tagged events from the pipeline.

    The pipeline emits events while it works; the web layer snapshots them on
    every poll. Thread-safety matters because a sentence's citations run
    concurrently inside a ThreadPoolExecutor.
    """

    def __init__(self) -> None:
        self._events: list[dict] = []
        self._lock = threading.Lock()

    def emit(self, stage: str, ref: int | None, message: str) -> None:
        with self._lock:
            self._events.append({
                "t": time.time(),
                "stage": stage,
                "ref": ref,
                "message": message,
            })

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self._events)
