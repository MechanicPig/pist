"""Formatting helpers for the campaign and map catalog."""

from collections.abc import Iterable, Mapping

from rich.text import Text

from berries.game import campaigns, levels, localization
from berries.game.duration import GAME_TIMER_SECONDS_FORMAT
from pist import map_names
from pist.save_progress import is_recorded
from pist.saves import SaveProgress


def _add_line(text: Text, label: str, value: object) -> None:
    text.append(f'{label}: ', style='bold')  # pyright: ignore[reportUnusedCallResult]
    text.append(str(value))  # pyright: ignore[reportUnusedCallResult]
    text.append('\n')  # pyright: ignore[reportUnusedCallResult]


def _alternate_name_lines(names: dict[str, str], lang: str, *, indent: str) -> Text:
    text = Text()
    display = localization.select_translation(names, lang)
    if lang != 'en' and (name := names.get('en')) is not None and name != display:
        text.append(f'{indent}英文: {name}\n', style='dim')  # pyright: ignore[reportUnusedCallResult]
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
    lang: str,
    dialogs: Mapping[str, Mapping[str, str]],
    source_name: str,
) -> Text:
    """Render slot-independent secondary information for one map item."""
    text = Text()
    loaded_map = level[side]
    map_info = loaded_map
    text.append_text(  # pyright: ignore[reportUnusedCallResult]
        _alternate_name_lines(map_names.localized_names(level, side, dialogs), lang, indent='  ')
    )
    text.append(f'  来源: {source_name}\n', style='dim')  # pyright: ignore[reportUnusedCallResult]
    text.append(f'  文件: {map_info.file_path}\n', style='dim')  # pyright: ignore[reportUnusedCallResult]
    text.rstrip()
    return text


def map_save_stats(sid: str, side: levels.LevelSide, save_slot: SaveProgress | None) -> Text:
    """Render only the slot-dependent statistics, independently of map metadata."""
    text = Text()
    if save_slot is not None:
        stats = save_slot.get_map_stats(sid, side)
        if stats is None or not is_recorded(stats):
            text.append(f'  存档 {save_slot.number}: 无记录', style='dim')  # pyright: ignore[reportUnusedCallResult]
        else:
            time_played = format(stats.time_played, GAME_TIMER_SECONDS_FORMAT)
            completed = '，已通关' if stats.completed else ''
            text.append(  # pyright: ignore[reportUnusedCallResult]
                f'  存档 {save_slot.number}: 用时 {time_played}，死亡 {stats.deaths}{completed}',
                style='green',
            )
    return text


def map_title(
    level: levels.Level,
    side: levels.LevelSide,
    lang: str,
    dialogs: Mapping[str, Mapping[str, str]],
    *,
    marker: str = '• ',
) -> Text:
    """Render a map title, marking only its non-default side."""
    name = map_names.display_name(level, side, dialogs, lang)
    if side is levels.LevelSide.A:
        return Text(f'{marker}{name}')
    suffix = f' {side.name}'
    if name.casefold().endswith(suffix.casefold()):
        name = name[: -len(suffix)]
    text = Text(f'{marker}{name} ')
    text.append(f'[{side.name}面]', style='bright_black')  # pyright: ignore[reportUnusedCallResult]
    return text


def format_campaign_summary(
    campaign: campaigns.Campaign,
    lang: str,
    dialogs: Mapping[str, Mapping[str, str]],
    source_names: Iterable[str],
) -> Text:
    """Render the identity and physical sources of one selected campaign."""
    text = Text()
    _add_line(text, '名称', map_names.campaign_name(campaign, dialogs, lang))
    _add_line(text, '路径', campaign.directory)
    _add_line(text, '地图数', campaign.map_count)
    _add_line(text, '来源', '、'.join(dict.fromkeys(source_names)))
    return text
