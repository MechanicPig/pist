"""Resolve installed helper-Mod rules that hide maps from the Game menu."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import tomlkit
import yaml
from pydantic import Field, ValidationError, field_validator
from tomlkit.exceptions import ParseError

from pist.containers import FrozenCaseFoldSet
from pist.game import dialog
from pist.game.content import ContentPath
from pist.game.saves import settings_dir
from pist.models import ExternalModel, FrozenModel

type MapHiderTarget = Literal['source', 'campaign']
type MapHiderMatch = Literal['exact', 'casefold']

CHRONIA_HELPER = 'ChroniaHelper'
HELPER_TEST_MAP_HIDER = 'HelperTestMapHider'
SCUG_HELPER = 'ScugHelper'
CHRONIA_MAP_HIDER_PATH = Path(CHRONIA_HELPER) / 'MapHider.yaml'
SCUG_SETTINGS_FILENAME = f'modsettings-{SCUG_HELPER}.celeste'
SCUG_LEGACY_SETTINGS_PATH = Path('ModSettings-OBSOLETE') / f'{SCUG_HELPER}.yaml'
DEFAULT_MAP_HIDER_RULES_PATH = Path(__file__).parents[1] / 'data' / 'map_hiders.toml'


class MapHiderRule(FrozenModel):
    """One data-declared hider rule using a code-registered reader."""

    id: str
    hider: str
    reader: str
    target: MapHiderTarget
    match: MapHiderMatch
    values: tuple[str, ...] = ()
    builtin_targets: tuple[str, ...] = ()


class MapHiderRuleSet(FrozenModel):
    """The packaged map-hider rule declarations."""

    rules: tuple[MapHiderRule, ...]


class _ChroniaMapHiderSettings(ExternalModel):
    """The externally stored portion of ChroniaHelper's map hider settings."""

    targets: list[str] | None = Field(default=None, validation_alias='HelperMapsToHide')


class _ScugHelperSettings(ExternalModel):
    """The externally stored portion of ScugHelper's map visibility settings."""

    hide_showcase_maps: bool | None = Field(
        default=True,
        validation_alias='HideShowcaseMapsInMapSelect',
    )

    @field_validator('hide_showcase_maps', mode='before')
    @classmethod
    def _parse_everest_bool(cls, value: object) -> object:
        if value == '':
            return None
        if isinstance(value, str):
            match value.casefold():
                case 'true':
                    return True
                case 'false':
                    return False
        return value


@dataclass(frozen=True, slots=True)
class KnownMapHiderTargets:
    """A reader has determined the targets that its rule should hide."""

    targets: frozenset[str]


@dataclass(frozen=True, slots=True)
class UnknownMapHiderTargets:
    """A reader cannot safely determine whether its rule should hide maps."""

    diagnostic: str


type MapHiderReaderResult = KnownMapHiderTargets | UnknownMapHiderTargets


@dataclass(frozen=True, slots=True)
class ActiveMapHiderRule:
    """One active rule with its resolved target values and matching semantics."""

    target: MapHiderTarget
    values: frozenset[str] | FrozenCaseFoldSet

    def matches(self, *, source: str, campaign: str) -> bool:
        """Return whether this rule hides a map with the supplied identities."""
        value = source if self.target == 'source' else campaign
        return value in self.values


@dataclass(frozen=True, slots=True)
class MapHiderResolution:
    """All confirmed active rules plus once-per-read diagnostics."""

    rules: tuple[ActiveMapHiderRule, ...]
    diagnostics: tuple[str, ...]

    def hides(self, *, source: str, map_file: ContentPath) -> bool:
        """Return whether any confirmed rule hides this final active map."""
        campaign = dialog.campaign_dir_for_map_file(map_file).as_posix()
        return any(rule.matches(source=source, campaign=campaign) for rule in self.rules)


type MapHiderReader = Callable[[MapHiderRule, Path], MapHiderReaderResult]


class MapHiderRules:
    """Resolve configured map hiding against Pist's Everest load simulation."""

    def __init__(
        self,
        game_dir: Path,
        *,
        rules_path: Path = DEFAULT_MAP_HIDER_RULES_PATH,
    ) -> None:
        self._game_dir = game_dir
        self._rules = _load_rules(rules_path)

    def resolve(self, active_mod_names: Iterable[str]) -> MapHiderResolution:
        """Resolve active rules without allowing unknown settings to hide maps."""
        active_names = frozenset(active_mod_names)
        rules: list[ActiveMapHiderRule] = []
        diagnostics: list[str] = []
        for rule in self._rules:
            if rule.hider not in active_names:
                continue
            result = _MAP_HIDER_READERS[rule.reader](rule, self._game_dir)
            if isinstance(result, UnknownMapHiderTargets):
                diagnostics.append(f'[{rule.id}] {result.diagnostic}')
                continue
            values: frozenset[str] | FrozenCaseFoldSet = result.targets
            if rule.match == 'casefold':
                values = FrozenCaseFoldSet(values)
            rules.append(ActiveMapHiderRule(rule.target, values))
        return MapHiderResolution(tuple(rules), tuple(diagnostics))


def _load_rules(path: Path) -> tuple[MapHiderRule, ...]:
    """Load validated declarations and reject unknown reader names eagerly."""
    try:
        rules = MapHiderRuleSet.model_validate(tomlkit.parse(path.read_text(encoding='utf-8')))
    except (OSError, ParseError, ValidationError) as error:
        raise ValueError(f'Invalid map hider rules: {path!r}') from error
    unknown = sorted({rule.reader for rule in rules.rules} - _MAP_HIDER_READERS.keys())
    if unknown:
        names = ', '.join(unknown)
        raise ValueError(f'Unknown map hider reader in {path!r}: {names}')
    duplicate_ids = sorted(
        rule_id for rule_id, count in Counter(rule.id for rule in rules.rules).items() if count > 1
    )
    if duplicate_ids:
        raise ValueError(f'Duplicate map hider rule IDs in {path!r}: {", ".join(duplicate_ids)}')
    for rule in rules.rules:
        match rule.reader:
            case 'static' | 'scug_showcase' if not rule.values:
                raise ValueError(f'Map hider rule requires values: {rule.id}')
            case 'chronia_map_hider' if not rule.builtin_targets:
                raise ValueError(f'Map hider rule requires builtin_targets: {rule.id}')
    return rules.rules


def _static_reader(rule: MapHiderRule, _: Path) -> MapHiderReaderResult:
    """Return static targets maintained with the rule data."""
    return KnownMapHiderTargets(frozenset(rule.values))


def _chronia_map_hider_reader(rule: MapHiderRule, game_dir: Path) -> MapHiderReaderResult:
    """Read ChroniaHelper's custom global-save list with its missing-value semantics."""
    path = settings_dir(game_dir) / CHRONIA_MAP_HIDER_PATH
    try:
        content = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return KnownMapHiderTargets(frozenset(rule.builtin_targets))
    except OSError as error:
        return UnknownMapHiderTargets(f'无法读取 ChroniaHelper 地图隐藏设置：{path}（{error}）')
    try:
        raw = yaml.load(content, Loader=yaml.BaseLoader)
    except yaml.YAMLError as error:
        return UnknownMapHiderTargets(f'无法解析 ChroniaHelper 地图隐藏设置：{path}（{error}）')
    try:
        settings = _ChroniaMapHiderSettings.model_validate({} if raw is None else raw)
    except ValidationError:
        return UnknownMapHiderTargets(f'ChroniaHelper 地图隐藏设置结构无效：{path}')
    if settings.targets is None:
        return KnownMapHiderTargets(frozenset(rule.builtin_targets))
    return KnownMapHiderTargets(frozenset(settings.targets))


def _scug_showcase_reader(rule: MapHiderRule, game_dir: Path) -> MapHiderReaderResult:
    """Read ScugHelper's Everest settings and retain its bool-default behavior."""
    path = settings_dir(game_dir) / SCUG_SETTINGS_FILENAME
    if not path.is_file():
        path = game_dir / 'Everest' / SCUG_LEGACY_SETTINGS_PATH
    try:
        content = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return KnownMapHiderTargets(frozenset(rule.values))
    except OSError as error:
        return UnknownMapHiderTargets(f'无法读取 ScugHelper 设置：{path}（{error}）')
    try:
        raw = yaml.load(content, Loader=yaml.BaseLoader)
    except yaml.YAMLError as error:
        return UnknownMapHiderTargets(f'无法解析 ScugHelper 设置：{path}（{error}）')
    try:
        settings = _ScugHelperSettings.model_validate({} if raw is None else raw)
    except ValidationError:
        return UnknownMapHiderTargets(f'ScugHelper 隐藏地图设置无效：{path}')
    if settings.hide_showcase_maps is not True:
        return KnownMapHiderTargets(frozenset())
    return KnownMapHiderTargets(frozenset(rule.values))


_MAP_HIDER_READERS: Mapping[str, MapHiderReader] = {
    'static': _static_reader,
    'chronia_map_hider': _chronia_map_hider_reader,
    'scug_showcase': _scug_showcase_reader,
}
