"""Complete AreaModeStats values, independent of their input XML."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Self

from lxml import etree

from berries.game.duration import Duration
from berries.game.saves.codecs import values as values_codec
from berries.game.saves.codecs.xml import fields as xml_fields
from berries.game.saves.unknown import UnknownFieldPolicy
from berries.map_entity_id import MapEntityID

_ZERO = Duration()


@dataclass
class PersonalBests:
    """Stored values, including records not established by SingleRunCompleted.

    Positive time and the enclosing map's SingleRunCompleted flag determine
    whether these values represent established records. Raw values are never
    discarded merely because that flag is false or a time is nonpositive.
    """

    time: Duration = _ZERO
    full_clear_time: Duration = _ZERO
    deaths: int = 0
    dashes: int = 0

    def reset(self) -> None:
        self.time = self.full_clear_time = _ZERO
        self.deaths = self.dashes = 0

    @classmethod
    def from_xml(
        cls, root: etree.Element, *, strict: bool = False, path: str = 'AreaModeStats'
    ) -> Self:
        fields = xml_fields.PersonalBestFields.model_validate(dict(root.attrib))
        time = Duration.from_dotnet_ticks(fields.time)
        full_clear_time = Duration.from_dotnet_ticks(fields.full_clear_time)
        if strict:
            values_codec.validate_timer_duration(time, path=f'{path}.BestTime')
            values_codec.validate_timer_duration(full_clear_time, path=f'{path}.BestFullClearTime')
        return cls(
            time,
            full_clear_time,
            fields.deaths,
            fields.dashes,
        )


class MapProgress:
    """One side's complete mutable progress, without implicit parent updates."""

    def __init__(
        self,
        *,
        time_played: Duration = _ZERO,
        deaths: int = 0,
        completed: bool = False,
        single_run_completed: bool = False,
        full_clear: bool = False,
        heart: bool = False,
        strawberries: Iterable[MapEntityID] = (),
        checkpoints: Iterable[str] = (),
        bests: PersonalBests | None = None,
        total_strawberries: int | None = None,
    ) -> None:
        self.time_played = time_played
        self.deaths = deaths
        self.completed = completed
        self.single_run_completed = single_run_completed
        self.full_clear = full_clear
        self.heart = heart
        self.strawberries = set(strawberries)
        self.checkpoints = set(checkpoints)
        self.bests = PersonalBests() if bests is None else bests
        self._berry_offset = (
            0 if total_strawberries is None else total_strawberries - len(self.strawberries)
        )

    @property
    def total_strawberries(self) -> int:
        """Stored count, retaining the historical offset as entity membership changes."""
        return len(self.strawberries) + self._berry_offset

    @total_strawberries.setter
    def total_strawberries(self, value: int) -> None:
        self._berry_offset = value - len(self.strawberries)

    def reset(self) -> None:
        """Reset progress while retaining the independent historical berry-count offset."""
        self.time_played = _ZERO
        self.deaths = 0
        self.completed = self.single_run_completed = self.full_clear = self.heart = False
        self.strawberries.clear()
        self.checkpoints.clear()
        self.bests.reset()

    @classmethod
    def from_xml(
        cls,
        root: etree.Element,
        *,
        strict: bool = False,
        unknown: UnknownFieldPolicy = 'error',
        path: str = 'AreaModeStats',
    ) -> Self:
        fields = xml_fields.MapFields.from_xml(root, strict=strict, unknown=unknown, path=path)
        return cls(
            time_played=fields.time_played,
            deaths=fields.deaths,
            completed=fields.completed,
            single_run_completed=fields.single_run_completed,
            full_clear=fields.full_clear,
            heart=fields.heart_collected,
            strawberries=fields.strawberries,
            checkpoints=fields.checkpoints,
            bests=PersonalBests(
                fields.best_time,
                fields.best_full_clear_time,
                fields.best_deaths,
                fields.best_dashes,
            ),
            total_strawberries=fields.total_strawberries,
        )

    def _output_fields(self) -> xml_fields.MapFields:
        return xml_fields.MapFields(
            TimePlayed=self.time_played,
            Deaths=self.deaths,
            Completed=self.completed,
            SingleRunCompleted=self.single_run_completed,
            FullClear=self.full_clear,
            HeartGem=self.heart,
            TotalStrawberries=self.total_strawberries,
            BestTime=self.bests.time,
            BestFullClearTime=self.bests.full_clear_time,
            BestDeaths=self.bests.deaths,
            BestDashes=self.bests.dashes,
            strawberries=sorted(self.strawberries, key=str),
            checkpoints=sorted(self.checkpoints),
        )

    def validate_output(self) -> None:
        """Validate all output values without building XML or changing progress."""
        fields = self._output_fields()
        fields.validate_output()

    def to_xml(self) -> etree.Element:
        fields = self._output_fields()
        return fields.to_xml('AreaModeStats')

    def tostring(self) -> bytes:
        return etree.tostring(self.to_xml(), encoding='utf-8')

    def berry_offset(self) -> int:
        return self._berry_offset

    def __eq__(self, other: object) -> bool:
        """Compare stored progress values, not object identities."""
        if not isinstance(other, MapProgress) or type(self) is not type(other):
            return NotImplemented
        if self is other:
            return True
        return (
            self.time_played,
            self.deaths,
            self.completed,
            self.single_run_completed,
            self.full_clear,
            self.heart,
            self.total_strawberries,
            self.strawberries,
            self.checkpoints,
            self.bests,
        ) == (
            other.time_played,
            other.deaths,
            other.completed,
            other.single_run_completed,
            other.full_clear,
            other.heart,
            other.total_strawberries,
            other.strawberries,
            other.checkpoints,
            other.bests,
        )
