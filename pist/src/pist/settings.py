"""Local non-secret settings persisted in Pist's writable ``.pist`` directory."""

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated

from annotated_types import MinLen
from pydantic import Field, ValidationError

from berries.models import FrozenModel

from .paths import PIST_DIR

SETTINGS_PATH = PIST_DIR / 'settings.json'

type ColumnWidth = Annotated[int, Field(strict=True, gt=0)]


class RecordListMaxWidths(FrozenModel):
    """Record column widths and text limits measured in terminal display cells."""

    record_number: ColumnWidth = 6
    map_name: ColumnWidth = 40
    mod_name: ColumnWidth = 32
    status: ColumnWidth = 12
    save_slot: ColumnWidth = 6
    sync_state: ColumnWidth = 10
    confirmed_at: ColumnWidth = 19


class PistSettings(FrozenModel):
    """Persisted local presentation settings for pist."""

    theme: Annotated[str, MinLen(1)] = 'textual-dark'
    dialog_lang: Annotated[str, MinLen(1)] = 'zh-cn'
    game_dir: Path | None = None
    smartsheet_url: str | None = None
    record_list_max_widths: RecordListMaxWidths = RecordListMaxWidths()


class SettingsStore:
    """Load and save presentation settings without mixing them with credentials."""

    def __init__(self, path: Path = SETTINGS_PATH) -> None:
        self._path = path

    def load(self) -> PistSettings:
        if not self._path.is_file():
            return PistSettings()
        try:
            return PistSettings.model_validate_json(self._path.read_text(encoding='utf-8'))
        except (OSError, ValidationError) as error:
            raise ValueError(f'Invalid pist settings file: {self._path!r}') from error

    def save(self, settings: PistSettings) -> None:
        """Replace the settings file only after writing a complete candidate."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=self._path.parent, prefix='.settings-', delete=False
            ) as candidate:
                candidate_path = Path(candidate.name)
                candidate.write(settings.model_dump_json(indent=2))  # pyright: ignore[reportUnusedCallResult]
            candidate_path.replace(self._path)  # pyright: ignore[reportUnusedCallResult]
        finally:
            if candidate_path is not None:
                candidate_path.unlink(missing_ok=True)
