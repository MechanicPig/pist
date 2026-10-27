import asyncio
from pathlib import Path
from threading import Event

import pytest

from pist.saves import SaveProgress, SaveProgressReader
from pist.ui.maps.browser.save_switch import SaveSlotSwitch
from test_support.saves import make_save_progress


def test_save_switch_reports_discovery_failure_without_changing_selection() -> None:
    class Reader(SaveProgressReader):
        def available_numbers(self) -> list[int]:
            raise ValueError('Cannot list directory')

    states: list[tuple[int | None, bool]] = []
    errors: list[ValueError] = []

    async def apply(slot: SaveProgress) -> None:
        pytest.fail('A discovery error must not apply a snapshot.')

    switch = SaveSlotSwitch(
        Reader(Path('unused')),
        0,
        on_state=lambda number, pending: states.append((number, pending)),
        on_loaded=apply,
        on_error=errors.append,
    )
    switch.request(1)
    assert switch.target == 0 and not switch.pending
    assert not states and len(errors) == 1


def test_save_switch_coalesces_queued_requests_relative_to_latest_target() -> None:
    discoveries = 0

    class Reader(SaveProgressReader):
        def available_numbers(self) -> list[int]:
            nonlocal discoveries
            discoveries += 1
            return list(range(4 if discoveries == 1 else 5))

        def load(self, number: int) -> SaveProgress:
            reads.append(number)
            return make_save_progress(number, {})

    reads: list[int] = []
    applied: list[int | None] = []
    states: list[tuple[int | None, bool]] = []
    errors: list[ValueError] = []

    async def check() -> None:
        done = asyncio.Event()

        async def apply(slot: SaveProgress) -> None:
            applied.append(slot.number)
            done.set()

        switch = SaveSlotSwitch(
            Reader(Path('unused')),
            0,
            on_state=lambda n, p: states.append((n, p)),
            on_loaded=apply,
            on_error=errors.append,
        )
        try:
            switch.request(1)
            switch.request(1)
            switch.request(1)
            assert switch.target == 3 and switch.pending
            assert not reads
            await asyncio.wait_for(done.wait(), 2)
            assert reads == [3] and applied == [3]
            assert states == [(1, True), (2, True), (3, True), (3, False)]
            assert not switch.pending and not errors
            assert discoveries == 1
            done.clear()
            switch.request(1)
            await asyncio.wait_for(done.wait(), 2)
            assert discoveries == 2 and reads == [3, 4]
            assert applied == [3, 4]
        finally:
            switch.close()

    asyncio.run(check())


@pytest.mark.parametrize('first_fails', (False, True))
def test_save_switch_discards_obsolete_result_without_overlapping_reads(first_fails: bool) -> None:
    started, release = Event(), Event()
    active = 0
    max_active = 0
    reads: list[int] = []
    applied: list[int | None] = []
    errors: list[ValueError] = []

    class Reader(SaveProgressReader):
        def available_numbers(self) -> list[int]:
            return [0, 1, 2, 3]

        def load(self, number: int) -> SaveProgress:
            nonlocal active, max_active
            active += 1
            max_active = max(active, max_active)
            reads.append(number)
            try:
                if number == 1:
                    started.set()
                    if not release.wait(3):
                        raise AssertionError('Read was not released.')
                    if first_fails:
                        raise ValueError('obsolete failure')
                return make_save_progress(number, {})
            finally:
                active -= 1

    async def check() -> None:
        done = asyncio.Event()

        async def apply(slot: SaveProgress) -> None:
            applied.append(slot.number)
            done.set()

        switch = SaveSlotSwitch(
            Reader(Path('unused')),
            0,
            on_state=lambda *_: None,
            on_loaded=apply,
            on_error=errors.append,
        )
        try:
            switch.request(1)
            assert await asyncio.to_thread(started.wait, 2)
            switch.request(1)
            switch.request(1)
            # A pending read must not prevent the event loop from processing selection.
            await asyncio.sleep(0)
            assert switch.target == 3 and reads == [1]
            release.set()
            await asyncio.wait_for(done.wait(), 2)
            assert reads == [1, 3] and applied == [3]
            assert max_active == 1 and not errors
        finally:
            release.set()
            switch.close()

    asyncio.run(check())


def test_save_switch_failure_restores_last_loaded_number() -> None:
    errors: list[ValueError] = []
    states: list[tuple[int | None, bool]] = []

    class Reader(SaveProgressReader):
        def available_numbers(self) -> list[int]:
            return [0, 1]

        def load(self, number: int) -> SaveProgress:
            raise ValueError(f'Invalid slot {number}')

    async def check() -> None:
        failed = asyncio.Event()

        async def apply(slot: SaveProgress) -> None:
            raise AssertionError('Invalid slot was applied.')

        def report(error: ValueError) -> None:
            errors.append(error)
            failed.set()

        switch = SaveSlotSwitch(
            Reader(Path('unused')),
            0,
            on_state=lambda n, p: states.append((n, p)),
            on_loaded=apply,
            on_error=report,
        )
        try:
            switch.request(1)
            await asyncio.wait_for(failed.wait(), 2)
            assert switch.target == 0 and not switch.pending
            assert states == [(1, True), (0, False)]
            assert len(errors) == 1
        finally:
            switch.close()

    asyncio.run(check())


def test_save_switch_close_drops_active_result_and_further_requests() -> None:
    started, release = Event(), Event()
    reads: list[int] = []
    applied: list[int | None] = []

    class Reader(SaveProgressReader):
        def available_numbers(self) -> list[int]:
            return [0, 1]

        def load(self, number: int) -> SaveProgress:
            reads.append(number)
            started.set()
            if not release.wait(3):
                raise AssertionError('Read was not released.')
            return make_save_progress(number, {})

    async def check() -> None:
        async def apply(slot: SaveProgress) -> None:
            applied.append(slot.number)

        switch = SaveSlotSwitch(
            Reader(Path('unused')),
            0,
            on_state=lambda *_: None,
            on_loaded=apply,
            on_error=lambda _: None,
        )
        try:
            switch.request(1)
            assert await asyncio.to_thread(started.wait, 2)
            switch.close()
            switch.request(1)
        finally:
            release.set()
            await asyncio.sleep(0)

    asyncio.run(check())
    assert reads == [1] and not applied
