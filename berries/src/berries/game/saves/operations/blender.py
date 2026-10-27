"""Validate and detach the exact Blender subset participating in a transfer."""

from copy import copy
from dataclasses import dataclass

from berries.game.saves.codecs import values as values_codec
from berries.game.saves.codecs.yaml import enders_blender as blender_codec
from berries.game.saves.errors import ProgressConflictError
from berries.game.saves.files.enders_blender import EndersBlenderSave
from berries.game.saves.rooms import RoomStats


@dataclass
class TransferData:
    keys: frozenset[str]
    overwrite: bool
    first_clear: dict[str, RoomStats]
    last_saved: dict[str, RoomStats]
    room_names: dict[str, dict[str, str]]
    room_colors: dict[str, dict[str, int]]


def _prepare_stats(stats: RoomStats) -> RoomStats:
    """Detach validated transfer fields while retaining their presence metadata."""
    stats.validate_output()
    fields = blender_codec.RoomFields(
        room_order=stats.room_order,
        room_deaths=stats.room_deaths,
        room_times=stats.room_times,
        room_rta_times=stats.room_rta_times,
        room_strawberries=stats.room_strawberries,
        pause_flags=stats.pause_flags,
    )
    for time in (*fields.room_times.values(), *fields.room_rta_times.values()):
        values_codec.validate_duration(time)
    result = copy(stats)
    result.room_order = fields.room_order
    result.room_deaths = fields.room_deaths
    result.room_times = fields.room_times
    result.room_rta_times = fields.room_rta_times
    result.room_strawberries = fields.room_strawberries
    result.pause_flags = fields.pause_flags
    return result


def _state_ids(save: EndersBlenderSave, keys: set[str]) -> set[int]:
    result = set()
    for records in (save.first_clear, save.last_saved):
        for key in keys:
            value = records.get(key)
            if value is not None:
                result.update(
                    map(
                        id,
                        (
                            value,
                            value.room_order,
                            value.room_deaths,
                            value.room_times,
                            value.room_rta_times,
                            value.room_strawberries,
                            value.pause_flags,
                        ),
                    )
                )
    for group in (save.room_names, save.room_colors):
        result.update(id(value) for key, value in group.items() if key in keys)
    return result


def prepare(
    source: EndersBlenderSave,
    target: EndersBlenderSave,
    keys: set[str],
    *,
    target_keys: set[str],
    overwrite: bool,
) -> TransferData:
    def collections(save: EndersBlenderSave) -> set[int]:
        return set(map(id, (save.first_clear, save.last_saved, save.room_names, save.room_colors)))

    if collections(source) & collections(target):
        raise ProgressConflictError('Source and target share a Blender collection.')
    if _state_ids(source, keys) & _state_ids(target, target_keys):
        raise ProgressConflictError('Source and target Blender records overlap.')
    target_has_data = False
    for key in target_keys:
        if any(
            key in group
            for group in (
                target.first_clear,
                target.last_saved,
                target.room_names,
                target.room_colors,
            )
        ):
            target_has_data = True
            if not overwrite:
                raise ProgressConflictError(f'Target Blender record already exists: {key!r}.')
    first = {
        key: _prepare_stats(source.first_clear[key]) for key in source.first_clear if key in keys
    }
    last = {key: _prepare_stats(source.last_saved[key]) for key in source.last_saved if key in keys}
    names = blender_codec.parse_room_names(
        {k: v for k, v in source.room_names.items() if k in keys}
    )
    colors = blender_codec.parse_room_colors(
        {k: v for k, v in source.room_colors.items() if k in keys}
    )
    if first or last or names or colors or (overwrite and target_has_data):
        source.validate_transfer(keys)
        target.validate_transfer(target_keys)
    return TransferData(frozenset(target_keys), overwrite, first, last, names, colors)


def apply(
    source: EndersBlenderSave,
    target: EndersBlenderSave,
    data: TransferData,
    *,
    remove_source: bool,
) -> None:
    if data.overwrite:
        for key in data.keys:
            for records in (target.first_clear, target.last_saved):
                if key in records:
                    del records[key]
            target.room_names.pop(key, None)  # pyright: ignore[reportUnusedCallResult]
            target.room_colors.pop(key, None)  # pyright: ignore[reportUnusedCallResult]
    for origin, destination, records in (
        (source.first_clear, target.first_clear, data.first_clear),
        (source.last_saved, target.last_saved, data.last_saved),
    ):
        for key, stats in records.items():
            destination[key] = stats
            if remove_source:
                del origin[key]
    _copy_dict(source.room_names, target.room_names, data.room_names, remove_source=remove_source)
    _copy_dict(
        source.room_colors, target.room_colors, data.room_colors, remove_source=remove_source
    )


def _copy_dict[T](
    source: dict[str, dict[str, T]],
    target: dict[str, dict[str, T]],
    values: dict[str, dict[str, T]],
    *,
    remove_source: bool,
) -> None:
    for key, value in values.items():
        target[key] = value
        if remove_source:
            del source[key]
