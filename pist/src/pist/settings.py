"""Local non-secret settings persisted in Pist's writable ``.pist`` directory."""

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal

from annotated_types import MinLen
from pydantic import Field, ValidationError

from berries.game.dialog import DIALOG_LANGUAGES
from berries.models import FrozenModel
from pist.paths import PIST_DIR

SETTINGS_PATH = PIST_DIR / 'settings.json'

type ColumnWidth = Annotated[int, Field(strict=True, gt=0)]


class RecordListMaxWidths(FrozenModel):
    """Record column widths and text limits measured in terminal display cells."""

    record_number: ColumnWidth = 6
    map_name: ColumnWidth = 40
    mod_name: ColumnWidth = 32
    status: ColumnWidth = 12
    save_slot: ColumnWidth = 6
    sync_state: ColumnWidth = 8


class PistSettings(FrozenModel):
    """Persisted local presentation settings for pist."""

    theme: Literal['textual-dark', 'textual-light'] = 'textual-dark'
    dialog_languages: Annotated[tuple[str, ...], MinLen(1)] = DIALOG_LANGUAGES
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
                candidate.write(settings.model_dump_json(indent=2))
            candidate_path.replace(self._path)
        finally:
            if candidate_path is not None:
                candidate_path.unlink(missing_ok=True)
