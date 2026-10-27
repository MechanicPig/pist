"""Load immediately and coalesce pending slot selections into the latest target."""

import asyncio
from collections.abc import Awaitable, Callable

from pist.saves import SaveProgress, SaveProgressReader


class SaveSlotSwitch:
    """Own the target selection; never cancel a thread just to start another read."""

    def __init__(
        self,
        reader: SaveProgressReader,
        number: int | None,
        *,
        on_state: Callable[[int | None, bool], None],
        on_loaded: Callable[[SaveProgress], Awaitable[None]],
        on_error: Callable[[ValueError], None],
    ) -> None:
        self._reader = reader
        self.target = number
        self.pending = False
        self._loaded_number = number
        self._numbers: list[int] = []
        self._on_state = on_state
        self._on_loaded = on_loaded
        self._on_error = on_error
        self._revision = 0
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    def request(self, step: int) -> None:
        """Move relative to the latest target, not the last displayed snapshot."""
        if self._closed:
            return
        # One key-repeat burst shares a directory snapshot. Re-scanning all Mod
        # save filenames per key would still stall the UI without parsing any XML.
        if not self.pending:
            try:
                self._numbers = self._reader.available_numbers()
            except ValueError as error:
                self._on_error(error)
                return
        numbers = self._numbers
        if not numbers:
            self._on_error(ValueError('未找到存档。'))
            return
        if self.target is None:
            number = numbers[0] if step > 0 else numbers[-1]
        else:
            index = numbers.index(self.target) if self.target in numbers else 0
            number = numbers[(index + step) % len(numbers)]
        self.target = number
        self.pending = True
        self._revision += 1
        self._on_state(number, True)
        if self._task is None:
            self._task = asyncio.create_task(self._load_latest())

    async def _load_latest(self) -> None:
        try:
            while True:
                revision = self._revision
                number = self.target
                assert number is not None
                try:
                    slot = await asyncio.to_thread(self._reader.load, number)
                except ValueError as error:
                    if revision != self._revision:
                        continue
                    self.target = self._loaded_number
                    self.pending = False
                    self._on_state(self.target, False)
                    self._on_error(error)
                    return
                if revision != self._revision:
                    continue
                await self._on_loaded(slot)
                self._loaded_number = slot.number
                if revision != self._revision:
                    continue
                self.pending = False
                self._on_state(slot.number, False)
                return
        finally:
            self._task = None

    def close(self) -> None:
        """Stop UI updates on exit; an active read thread may finish without applying."""
        self._closed = True
        if self._task is not None:
            self._task.cancel()  # pyright: ignore[reportUnusedCallResult]
