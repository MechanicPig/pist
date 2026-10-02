"""Formatting helpers for the campaign and map catalog."""

from collections.abc import Iterable, Mapping

from rich.text import Text

from berries.game import campaigns, dialog, levels, saves


def _add_line(text: Text, label: str, value: object) -> None:
    text.append(f'{label}: ', style='bold')
    text.append(str(value))
    text.append('\n')


def _alternate_name_lines(names: dict[str, str], languages: Iterable[str], *, indent: str) -> Text:
    text = Text()
    display = dialog.localized_name(names, languages)
    for lang in languages:
        if (name := names.get(lang)) is not None and name != display:
            label = '英文' if lang == 'en' else lang
            text.append(f'{indent}{label}: {name}\n', style='dim')
    return text


def maps_by_path(
    maps: Iterable[tuple[levels.Level, levels.LevelSide]],
) -> tuple[tuple[levels.Level, levels.LevelSide], ...]:
    """Return maps in a stable path order for ordinary campaigns."""
    return tuple(
        sorted(
            maps,
            key=lambda item: item[0][item[1]].file_path.as_posix().casefold(),
        )
    )


def map_detail_lines(
    level: levels.Level,
    side: levels.LevelSide,
    languages: Iterable[str],
    dialogs: Mapping[str, Mapping[str, str]],
    source_name: str,
    save_slot: saves.SaveSlot | None = None,
) -> Text:
    """Render the secondary information for one map item."""
    text = Text()
    loaded_map = level[side]
    map_info = loaded_map.map_info
    text.append_text(
        _alternate_name_lines(level.localized_names(side, dialogs), languages, indent='  ')
    )
    text.append(f'  来源: {source_name}\n', style='dim')
    text.append(f'  文件: {map_info.file_path}\n', style='dim')
    if save_slot is not None:
        stats = save_slot.get_map_stats(level, side)
        if stats is None or not stats.is_recorded:
            text.append(f'  存档 {save_slot.number}: 无记录', style='dim')
        else:
            time_played = stats.time_played.ingame_format('seconds')
            completed = '，已通关' if stats.completed else ''
            text.append(
                f'  存档 {save_slot.number}: 用时 {time_played}，死亡 {stats.deaths}{completed}',
                style='green',
            )
    else:
        text.rstrip()
    return text


def map_title(
    level: levels.Level,
    side: levels.LevelSide,
    languages: Iterable[str],
    dialogs: Mapping[str, Mapping[str, str]],
    *,
    marker: str = '• ',
) -> Text:
    """Render a map title, marking only its non-default side."""
    name = level.display_name(side, dialogs, languages)
    if side is levels.LevelSide.A:
        return Text(f'{marker}{name}')
    suffix = f' {side.value}'
    if name.casefold().endswith(suffix.casefold()):
        name = name[: -len(suffix)]
    text = Text(f'{marker}{name} ')
    text.append(f'[{side.value}面]', style='bright_black')
    return text


def format_campaign_summary(
    campaign: campaigns.Campaign,
    languages: Iterable[str],
    dialogs: Mapping[str, Mapping[str, str]],
    source_names: Iterable[str],
) -> Text:
    """Render the identity and physical sources of one selected campaign."""
    text = Text()
    _add_line(text, '名称', campaign.display_name(dialogs, languages))
    _add_line(text, '路径', campaign.directory)
    _add_line(text, '地图数', campaign.map_count)
    _add_line(text, '来源', '、'.join(dict.fromkeys(source_names)))
    return text
