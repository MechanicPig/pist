"""Run locally saved edits in an independent, serial synchronization queue."""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable

from textual.app import App
from textual.message import Message
from textual.worker import Worker

from pist.records.models import MapRecord


class RecordSyncChanged(Message):
    """A background attempt finished; visible record lists can reload persisted state."""


class BackgroundRecordSync:
    """Keep accepted local edits alive independently of subsequent editor workers."""

    def __init__(self, app: App[None], submit: Callable[[MapRecord], Awaitable[bool]]) -> None:
        self.app = app
        self._submit = submit
        self._queue: deque[MapRecord] = deque()
        self._pending_ids: set[int] = set()
        self._worker: Worker[None] | None = None
        self._wait_tasks: set[asyncio.Task[None]] = set()

    @property
    def pending(self) -> int:
        return len(self._pending_ids)

    def contains(self, local_id: int | None) -> bool:
        return local_id in self._pending_ids

    def enqueue(self, record: MapRecord) -> None:
        assert record.local_id is not None
        if self.contains(record.local_id):
            raise ValueError('该记录仍在同步队列中，不能重复提交。')
        self._pending_ids.add(record.local_id)
        self._queue.append(record)
        if self._worker is None or self._worker.is_finished:
            self._worker = self.app.run_worker(
                self._drain(),
                name='record-sync',
                group='record-sync',
                exclusive=False,
            )

    async def wait(self) -> None:
        """Wait for existing edits before explicit synchronization or renumbering."""
        if self._worker is not None and not self._worker.is_finished:
            # Cancelling a waiter must not propagate into an accepted save's worker.
            task = asyncio.create_task(self._worker.wait())
            self._wait_tasks.add(task)
            task.add_done_callback(self._finish_wait)
            # asyncio.wait does not cancel its inputs when this caller is cancelled.
            # Unlike shield in Python 3.14, it does not log a detached exception
            # independently of our result ownership callback.
            await asyncio.wait((task,))  # pyright: ignore[reportUnusedCallResult]
            task.result()

    def _finish_wait(self, task: asyncio.Task[None]) -> None:
        """Release a completed wait even when its original caller was cancelled."""
        self._wait_tasks.discard(task)
        if not task.cancelled():
            # Observe detached failures to avoid unhandled-task diagnostics; reading
            # the exception does not prevent active awaiters from receiving it.
            task.exception()  # pyright: ignore[reportUnusedCallResult]

    async def _drain(self) -> None:
        try:
            while self._queue:
                record = self._queue[0]
                await self._submit(record)  # pyright: ignore[reportUnusedCallResult]
                self._queue.popleft()  # pyright: ignore[reportUnusedCallResult]
                assert record.local_id is not None
                self._pending_ids.remove(record.local_id)
                for screen in self.app.screen_stack:
                    screen.post_message(RecordSyncChanged())  # pyright: ignore[reportUnusedCallResult]
        except asyncio.CancelledError:
            self.app.notify(
                f'后台同步被中断，{self.pending} 条记录仍保存在本地，远端结果需重新核对。',
                severity='warning',
            )
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self.app.notify(f'后台同步停止，未完成记录仍保存在本地：{error}', severity='error')
        finally:
            self._queue.clear()
            self._pending_ids.clear()
