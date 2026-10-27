"""Prepare persistent gem edits without inventing current-session collections."""

from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass

from pydantic import StrictStr, TypeAdapter

from berries.game.levels import LevelSide, MapKey
from berries.game.saves.codecs.xml import summit_gems as gems_codec
from berries.game.saves.errors import ProgressConflictError
from berries.game.saves.files.communal_helper import CommunalHelperSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.progress.level import campaign_name

type CustomSummitGemIDs = Mapping[MapKey, AbstractSet[str]]

_VANILLA = ('Celeste/7-Summit', LevelSide.A)
_STRINGS = TypeAdapter(set[StrictStr])


def scope_keys(
    keys: set[MapKey],
    *,
    campaign: str | None,
    ids: CustomSummitGemIDs | None,
) -> set[MapKey]:
    """Include explicitly supplied old-map groups in a whole-campaign scope."""
    if campaign is None or ids is None:
        return keys
    return keys | {key for key in ids if campaign_name(key[0]) == campaign}


@dataclass
class PreparedGems:
    save: MainSave
    vanilla: list[bool] | None
    communal: CommunalHelperSave | None
    custom: set[str] | None

    def apply(self) -> None:
        self.save.summit_gems = self.vanilla
        if self.communal is not None:
            self.communal.summit_gems = self.custom


def _selected(keys: set[MapKey], ids: CustomSummitGemIDs | None) -> set[str]:
    return _STRINGS.validate_python(
        set() if ids is None else {value for key in keys for value in ids.get(key, ())}
    )


def _custom_values(
    communal: CommunalHelperSave | None, ids: CustomSummitGemIDs | None
) -> set[str] | None:
    if (communal is None) != (ids is None):
        raise ValueError('Custom gem IDs and the CommunalHelper save must be supplied together.')
    if communal is None or communal.summit_gems is None:
        return None
    _ = _STRINGS.validate_python(communal.summit_gems)
    return communal.summit_gems


def is_empty(
    save: MainSave,
    keys: set[MapKey],
    *,
    ids: CustomSummitGemIDs | None,
    communal: CommunalHelperSave | None,
) -> bool:
    custom = _custom_values(communal, ids)
    if _VANILLA in keys and save.summit_gems is not None:
        gems_codec.validate(save.summit_gems, nullable=True)
        if any(save.summit_gems):
            return False
    return custom is None or not custom.intersection(_selected(keys, ids))


def prepare_clear(
    save: MainSave,
    keys: set[MapKey],
    *,
    ids: CustomSummitGemIDs | None,
    communal: CommunalHelperSave | None,
) -> PreparedGems:
    custom = _custom_values(communal, ids)
    vanilla = save.summit_gems
    if _VANILLA in keys and vanilla is not None:
        gems_codec.validate(vanilla, nullable=True)
        vanilla = [False] * gems_codec.N_GEMS
    if custom is not None:
        selected = _selected(keys, ids)
        if selected:
            custom = custom - selected
    return PreparedGems(save, vanilla, communal, custom)


def prepare_target(
    source: MainSave,
    target: MainSave,
    source_keys: set[MapKey],
    target_keys: set[MapKey],
    *,
    ids: CustomSummitGemIDs | None,
    source_communal: CommunalHelperSave | None,
    target_communal: CommunalHelperSave | None,
    overwrite: bool,
    exclusive: bool,
) -> PreparedGems:
    donor = _custom_values(source_communal, ids)
    previous = _custom_values(target_communal, ids)
    if source_communal is not None and (
        source_communal is target_communal
        or (
            source_communal.summit_gems is not None
            and target_communal is not None
            and source_communal.summit_gems is target_communal.summit_gems
        )
    ):
        raise ProgressConflictError('Source and target CommunalHelper progress overlap.')
    vanilla = target.summit_gems
    if _VANILLA in source_keys:
        gems_codec.validate(source.summit_gems, nullable=True)
        gems_codec.validate(target.summit_gems, nullable=True)
        # The supported vanilla map owns all six positions, including a nullable
        # never-collected history. No Session fields or DoNotLoad IDs are derived.
        if exclusive and vanilla is not None and any(vanilla):
            raise ProgressConflictError('Target vanilla Summit Gem progress is not empty.')
        incoming = source.summit_gems
        if overwrite:
            vanilla = None if incoming is None else incoming.copy()
        elif incoming is not None:
            vanilla = [
                a or b
                for a, b in zip(incoming, vanilla or [False] * gems_codec.N_GEMS, strict=True)
            ]
    selected_source = _selected(source_keys, ids)
    selected_target = _selected(target_keys, ids)
    if exclusive and previous is not None and previous & selected_target:
        raise ProgressConflictError('Selected target custom Summit Gem progress is not empty.')
    custom = previous
    if selected_source or selected_target:
        custom = set() if previous is None else previous.copy()
        if overwrite:
            custom -= selected_target
        if donor is not None:
            custom |= donor & selected_source
    return PreparedGems(target, vanilla, target_communal, custom)
