"""Serialize WeChat UI sends and the post-send idle chat restoration.

The idle callback runs on the same worker as send operations. While it is
switching chats, submissions wait at the condition boundary instead of racing
the UI. A grace period coalesces sends arriving just after the prior send.
"""
from __future__ import annotations

import heapq
import itertools
import logging
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class SendQueueClosed(RuntimeError):
    pass


class _Item:
    def __init__(self, priority: int, sequence: int, operation: Callable[[], Any], source: str):
        self.priority = priority
        self.sequence = sequence
        self.operation = operation
        self.source = source
        self.done = threading.Event()
        self.result: Any = None
        self.error: Optional[BaseException] = None


class PrioritySendQueue:
    PRINT_PRIORITY = 0
    NORMAL_PRIORITY = 100
    BULK_SOURCE = "print-bulk-image"
    # A reserved class below every finite priority, not just a large number.
    BULK_PRIORITY = float("inf")
    POLICY_VERSION = "bulk-last-v1"

    def __init__(self, name: str = "wechat-send-queue", idle_grace_seconds: float = 2.0):
        self._heap = []
        self._sequence = itertools.count()
        self._condition = threading.Condition(threading.RLock())
        self._closed = False
        self._stopping = False
        self._active = 0
        self._waiting_submissions = 0
        self._submitters_lock = threading.Lock()
        self._idle_callback: Optional[Callable[[], Any]] = None
        self._idle_notified = False
        self._ever_had_task = False
        self._idle_deadline: Optional[float] = None
        self._idle_grace_seconds = max(0.0, float(idle_grace_seconds))
        self._worker = threading.Thread(target=self._run, name=name, daemon=True)

    def set_idle_callback(self, callback: Optional[Callable[[], Any]]) -> None:
        with self._condition:
            self._idle_callback = callback if callable(callback) else None
            self._condition.notify_all()
        logger.info("send queue idle callback %s", "installed" if callable(callback) else "cleared")
        # Installing a callback on an empty queue is NOT an idle transition.

    def notify_when_idle(self) -> None:
        """Request a check after the grace period, never execute UI off-worker."""
        with self._condition:
            if self._ever_had_task and not self._closed and not self._stopping:
                self._idle_deadline = time.monotonic() + self._idle_grace_seconds
                self._condition.notify_all()

    def start(self) -> None:
        with self._condition:
            if not self._worker.is_alive() and not self._closed:
                self._worker.start()

    def submit(self, operation: Callable[[], Any], *, priority: int = NORMAL_PRIORITY, source: str = "bot") -> Any:
        if not callable(operation):
            raise TypeError("operation must be callable")
        source = str(source or "bot").strip().lower()
        if source == self.BULK_SOURCE:
            priority = self.BULK_PRIORITY
        else:
            try:
                priority = int(priority)
            except (TypeError, ValueError, OverflowError):
                priority = self.NORMAL_PRIORITY
        item = _Item(priority, next(self._sequence), operation, source)
        # Register the submitter before taking the queue condition. The idle
        # callback also holds that condition while switching the UI; without
        # this early registration, a request arriving at the same instant can
        # be invisible to the idle check and race the chat switch.
        with self._submitters_lock:
            self._waiting_submissions += 1
        try:
            with self._condition:
                if self._closed:
                    raise SendQueueClosed("send queue is closed")
                self._idle_deadline = None
                self._idle_notified = False
                self._ever_had_task = True
                self.start()
                heapq.heappush(self._heap, (item.priority, item.sequence, item))
                self._condition.notify_all()
        finally:
            with self._submitters_lock:
                self._waiting_submissions -= 1
            with self._condition:
                self._condition.notify_all()
        item.done.wait()
        if item.error is not None:
            raise item.error
        return item.result

    def _run(self) -> None:
        while True:
            item = None
            with self._condition:
                while item is None:
                    if self._stopping and not self._heap:
                        return
                    if self._heap:
                        _, _, item = heapq.heappop(self._heap)
                        self._idle_deadline = None
                        self._active += 1
                        break
                    if (
                        not self._stopping
                        and self._ever_had_task
                        and self._idle_callback is not None
                        and not self._idle_notified
                        and self._idle_deadline is not None
                        and self._waiting_submissions == 0
                    ):
                        remaining = self._idle_deadline - time.monotonic()
                        if remaining <= 0:
                            # This lock is also required by submit(). During
                            # callback() the actual queue remains empty and no
                            # new send can race the WeChat UI search/switch.
                            self._idle_notified = True
                            self._idle_deadline = None
                            logger.info("send queue became idle; invoking idle callback active=%s pending=%s waiting=%s", self._active, len(self._heap), self._waiting_submissions)
                            try:
                                self._idle_callback()
                            except BaseException:
                                logger.exception("send queue idle callback raised")
                            self._condition.notify_all()
                            continue
                        self._condition.wait(remaining)
                    else:
                        self._condition.wait()
            try:
                try:
                    item.result = item.operation()
                except BaseException as exc:
                    item.error = exc
            finally:
                with self._condition:
                    self._active -= 1
                    item.done.set()
                    if not self._heap and not self._stopping:
                        self._idle_deadline = time.monotonic() + self._idle_grace_seconds
                    self._condition.notify_all()

    def stop(self, drain: bool = True) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._idle_deadline = None
            if drain:
                while self._heap or self._active:
                    self._condition.wait()
            else:
                while self._heap:
                    _, _, item = heapq.heappop(self._heap)
                    item.error = SendQueueClosed("send queue stopped before dispatch")
                    item.done.set()
            self._stopping = True
            self._condition.notify_all()
        if self._worker is not threading.current_thread() and self._worker.ident is not None:
            self._worker.join(timeout=10.0)

    def snapshot(self) -> dict:
        """Read-only policy/queue health; no message bodies or UI operations."""
        with self._condition:
            bulk = sum(item.source == self.BULK_SOURCE for _, _, item in self._heap)
            return {
                "policy_version": self.POLICY_VERSION,
                "bulk_source": self.BULK_SOURCE,
                "bulk_is_lowest": True,
                "pending": len(self._heap),
                "pending_bulk": bulk,
                "pending_non_bulk": len(self._heap) - bulk,
                "active": self._active,
                "closed": self._closed,
                "ui_operation_preemptible": False,
            }

    @property
    def pending(self) -> int:
        with self._condition:
            return len(self._heap)
