"""Numbered save locations; selecting a format performs one independent read."""

from os import PathLike
from pathlib import Path
from stat import S_ISLNK, S_ISREG

from pydantic import TypeAdapter

from .conflicts import CampaignConflictResolver, LevelConflictResolver
from .files.main import MainSave
from .files.mod_backup import ModBackupSave
from .files.save import ModSave
from .paths import (
    SlotNumber,
    mod_save_data_path,
    mod_save_path,
    parse_mod_session_name,
    save_slot_path,
)
from .unknown import UnknownFieldPolicy

_SLOT_NUMBER = TypeAdapter(SlotNumber)


class SaveSlot:
    """A location, not an eagerly loaded group of documents."""

    def __init__(self, saves_dir: str | PathLike[str], number: int) -> None:
        self._number = _SLOT_NUMBER.validate_python(number)
        self._saves_dir = Path(saves_dir)

    @property
    def number(self) -> int:
        return self._number

    @property
    def saves_dir(self) -> Path:
        return self._saves_dir

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        return self.number == other.number and self.saves_dir == other.saves_dir

    def __hash__(self) -> int:
        return hash((self.number, self.saves_dir))

    def __repr__(self) -> str:
        return f'SaveSlot({self.number})'

    def __bool__(self) -> bool:
        path = save_slot_path(self.saves_dir, self.number)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return False
        if S_ISREG(info.st_mode) or S_ISLNK(info.st_mode):
            return True
        raise ValueError(f'Expected a main save file: {path!r}.')

    def main(
        self,
        *,
        strict: bool = True,
        on_campaign_conflict: CampaignConflictResolver | None = None,
        on_level_conflict: LevelConflictResolver | None = None,
        unknown: UnknownFieldPolicy = 'error',
    ) -> MainSave:
        return MainSave.read(
            save_slot_path(self.saves_dir, self.number),
            strict=strict,
            on_campaign_conflict=on_campaign_conflict,
            on_level_conflict=on_level_conflict,
            unknown=unknown,
        )

    def backup(
        self,
        *,
        strict: bool = True,
        on_campaign_conflict: CampaignConflictResolver | None = None,
        on_level_conflict: LevelConflictResolver | None = None,
        unknown: UnknownFieldPolicy = 'error',
    ) -> ModBackupSave:
        return ModBackupSave.read(
            mod_save_data_path(self.saves_dir, self.number),
            strict=strict,
            on_campaign_conflict=on_campaign_conflict,
            on_level_conflict=on_level_conflict,
            unknown=unknown,
        )

    def mod[T: ModSave](self, save_type: type[T], /, *args: object, **kwargs: object) -> T:
        """Locate a Mod save, forwarding its parser arguments through read()."""
        name = save_type.mod_name
        return save_type.read(mod_save_path(self.saves_dir, self.number, name), *args, **kwargs)

    def mod_session_paths(self) -> dict[str, Path]:
        """Discover this slot's default Mod session files without reading them.

        Return a fresh Mod-name/path mapping in filename order. Matching follows
        host Path rules; names retain their spelling. Ignore other slots, backup
        extensions and directories. A missing/inaccessible directory raises the
        native I/O error, rather than pretending the group is empty.
        """
        result: dict[str, Path] = {}
        for path in sorted(self.saves_dir.iterdir()):
            name = parse_mod_session_name(path.name, self.number)
            if name is None:
                continue
            info = path.lstat()
            if S_ISREG(info.st_mode) or S_ISLNK(info.st_mode):
                result[name] = path
        return result
