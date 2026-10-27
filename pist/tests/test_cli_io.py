"""CLI boundary I/O stays off the event-loop thread."""

import asyncio
from pathlib import Path
from threading import get_ident
from unittest.mock import AsyncMock, Mock

import pytest

from pist import __main__ as cli
from pist.credentials.store import CredentialStore
from pist.smartsheet.models import InspectionReport


@pytest.mark.parametrize('blank', (None, 0, 1, 2))
def test_credential_prompts_run_in_order_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, blank: int | None
) -> None:
    thread = get_ident()
    values = [' client ', ' token ', ' open ']
    if blank is not None:
        values[blank] = ' '
    prompts: list[str] = []

    def answer(prompt: str) -> str:
        assert get_ident() != thread
        prompts.append(prompt)
        return values[len(prompts) - 1]

    monkeypatch.setattr('builtins.input', answer)
    monkeypatch.setattr(cli, 'getpass', answer)
    store = AsyncMock(spec=CredentialStore)
    if blank is None:
        asyncio.run(cli.set_credentials(store))
        store.save_credentials.assert_awaited_once_with('client', 'token', 'open')
    else:
        with pytest.raises(ValueError, match='cannot be empty'):
            asyncio.run(cli.set_credentials(store))
        store.save_credentials.assert_not_awaited()
    assert prompts == ['Client ID: ', 'Access Token (input hidden): ', 'Open ID: ']


def test_inspection_writes_utf8_report_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread = get_ident()
    report = InspectionReport(file_id='中文', sheets=[])
    client = Mock()
    client.inspect = AsyncMock(return_value=report)
    monkeypatch.setattr(cli, 'TencentSmartSheetClient', lambda _: client)
    output_dir = tmp_path / 'reports'
    mkdir = Path.mkdir
    write_text = Path.write_text
    writes: list[Path] = []

    def make_dir(path: Path, *, parents: bool, exist_ok: bool) -> None:
        assert get_ident() != thread
        mkdir(path, parents=parents, exist_ok=exist_ok)

    def write(path: Path, data: str, *, encoding: str) -> int:
        assert get_ident() != thread
        writes.append(path)
        return write_text(path, data, encoding=encoding)

    monkeypatch.setattr(Path, 'mkdir', make_dir)
    monkeypatch.setattr(Path, 'write_text', write)
    asyncio.run(
        cli.inspect_sheet(Mock(spec=CredentialStore), 'file', record_limit=3, output_dir=output_dir)
    )
    client.inspect.assert_awaited_once_with('file', record_limit=3)
    assert len(writes) == 1 and writes[0].parent == output_dir
    assert InspectionReport.model_validate_json(writes[0].read_bytes()) == report
    assert '中文' in writes[0].read_text(encoding='utf-8')
