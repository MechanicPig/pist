"""Local non-secret settings persisted under the ignored ``.pist`` directory."""

from pathlib import Path
from typing import Annotated, Literal

from annotated_types import MinLen
from pydantic import ValidationError

from pist.game.dialog import DIALOG_LANGUAGES
from pist.models import FrozenModel

SETTINGS_PATH = Path('.pist/settings.json')


class PistSettings(FrozenModel):
    """Persisted local presentation settings for pist."""

    theme: Literal['textual-dark', 'textual-light'] = 'textual-dark'
    dialog_languages: Annotated[tuple[str, ...], MinLen(1)] = DIALOG_LANGUAGES
    game_dir: Path | None = None
    smartsheet_url: str | None = None


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
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(settings.model_dump_json(indent=2), encoding='utf-8')
