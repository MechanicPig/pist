"""Main-save domain values and coordinated active/recycled SID indexes."""

from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from copy import deepcopy
from typing import Annotated, Self

from lxml import etree
from pydantic import ConfigDict, Field, InstanceOf, StrictBool

from berries.game.duration import Duration
from berries.game.levels import MapKey
from berries.game.vanilla import VANILLA_SIDS
from berries.model_behavior import PrivateAttr

from .codecs import values as values_codec
from .codecs.values import SaveBool, SaveCount
from .codecs.xml import mods as mods_codec
from .codecs.xml import session as session_codec
from .codecs.xml import summit_gems as gems_codec
from .codecs.xml import tree as xml_tree
from .codecs.xml import vanilla as vanilla_codec
from .codecs.xml.fields import XmlText, XmlTimerDuration
from .codecs.xml.model import Xml, XmlModel, XmlReadContext
from .conflicts import CampaignConflictResolver, LevelConflictResolver
from .errors import ProgressConflictError
from .mod_save_data import ModSaveData
from .progress.assists import Assists
from .progress.campaign import CampaignProgress
from .progress.level import LevelProgress, UnresolvedLevelProgress
from .progress.map_ref import MapRef
from .progress.members import (
    CampaignMembers,
    IndexDelta,
    LevelIndex,
    LevelIndexState,
    LevelMembers,
    MembershipUpdate,
)
from .progress.session import Session
from .progress.views import ModsProgressView, VanillaProgressView
from .unknown import UnknownFieldPolicy

_PROGRESS_FIELDS = frozenset((*vanilla_codec.FIELDS, *mods_codec.FIELDS, 'CurrentSession'))


def _vanilla_levels() -> LevelMembers:
    return LevelMembers('Celeste', (LevelProgress(sid, id=i) for i, sid in enumerate(VANILLA_SIDS)))


class MainSaveData(ModSaveData, elements=True):
    """Editable domain values, live SID indexes and session coordination."""

    model_config = ConfigDict(validate_by_name=True)
    xml_tag = 'SaveData'

    name: Annotated[XmlText, Xml('Name', strict_required=True)] = 'Madeline'
    time_played: Annotated[XmlTimerDuration, Xml('Time', strict_required=True)] = Field(
        default_factory=Duration
    )
    deaths: Annotated[SaveCount, Xml('TotalDeaths', strict_required=True)] = 0
    total_goldenberries: Annotated[
        SaveCount, Xml('TotalGoldenStrawberries', strict_required=True)
    ] = 0
    assist_mode: Annotated[SaveBool, Xml('AssistMode', strict_required=True)] = False
    variant_mode: Annotated[SaveBool, Xml('VariantMode', strict_required=True)] = False
    cheat_mode: Annotated[SaveBool, Xml('CheatMode')] = False
    version: Annotated[XmlText | None, Xml('Version')] = None
    theo_sister_name: Annotated[XmlText | None, Xml('TheoSisterName')] = None
    total_jumps: Annotated[SaveCount, Xml('TotalJumps')] = 0
    total_wall_jumps: Annotated[SaveCount, Xml('TotalWallJumps')] = 0
    total_dashes: Annotated[SaveCount, Xml('TotalDashes')] = 0
    revealed_chapter9: Annotated[SaveBool, Xml('RevealedChapter9')] = False
    has_modded_save_data: Annotated[SaveBool, Xml('HasModdedSaveData')] = False
    assists: Annotated[Assists, Xml('Assists')] = Field(default_factory=Assists)
    flags: Annotated[set[XmlText], Xml('Flags/string')] = Field(default_factory=set)
    last_map_ref: Annotated[MapRef, Xml('LastArea')] = Field(default_factory=MapRef)
    last_save_text: Annotated[XmlText | None, Xml('LastSave', missing=None)] = Field(
        default='0001-01-01T00:00:00', frozen=True
    )

    summit_gems: Annotated[
        list[StrictBool] | None,
        Xml(
            'SummitGems', many=False, decoder=gems_codec.from_xml, element_encoder=gems_codec.to_xml
        ),
    ] = None
    unresolved_levels: Annotated[list[InstanceOf[UnresolvedLevelProgress]], Xml(None)] = Field(
        default_factory=list
    )
    unlocked_levels: Annotated[SaveCount, Xml(None)] = 0
    total_strawberries: Annotated[SaveCount, Xml(None)] = 0
    poem: Annotated[list[XmlText], Xml(None)] = Field(default_factory=list)
    vanilla_session: Annotated[
        Session | None,
        Xml(
            'CurrentSession',
            element_encoder=lambda session: session_codec.current_element(session, vanilla=True),
        ),
    ] = None
    _levels: InstanceOf[LevelMembers] = PrivateAttr(alias='levels', default_factory=_vanilla_levels)
    _vanilla_view: VanillaProgressView = PrivateAttr(compare=False, copy=False)
    _combined_flat_levels: LevelIndexState = PrivateAttr(compare=False, copy=False)

    @property
    def levels(self) -> LevelMembers:
        return self._levels

    def model_post_init(self, context: object, /) -> None:
        if (
            isinstance(context, XmlReadContext)
            and context.strict
            and self.assist_mode
            and self.variant_mode
        ):
            raise ValueError('Normal main saves cannot enable both AssistMode and VariantMode.')
        super().model_post_init(context)

    def _init_indexes(self) -> None:
        if self._levels.name != 'Celeste':
            raise ValueError('Vanilla members must belong to Celeste.')
        self._vanilla_view = VanillaProgressView(self)
        super()._init_indexes()
        self._combined_flat_levels = LevelIndexState(self.vanilla, self.mods.campaigns)

    @property
    def flat_levels(self) -> LevelIndex:
        return self._combined_flat_levels.view

    def _prepare_mod_indexes(self, value: ModsProgressView) -> tuple[MembershipUpdate, ...]:
        return (
            *super()._prepare_mod_indexes(value),
            self._combined_flat_levels.prepare(sources=(self.vanilla, value.campaigns)),
        )

    @classmethod
    def from_xml(
        cls,
        root: etree.Element,
        *,
        strict: bool = True,
        on_campaign_conflict: CampaignConflictResolver | None = None,
        on_level_conflict: LevelConflictResolver | None = None,
        unknown: UnknownFieldPolicy = 'error',
        path: str | None = None,
        attributes: AbstractSet[str] = frozenset(),
        children: AbstractSet[str] = frozenset(),
    ) -> Self:
        """Decode complete main-save values, including both session branches."""
        path = 'SaveData' if path is None else path
        fields, defaulted = cls._read_xml_fields(
            root,
            strict=strict,
            unknown=unknown,
            path=path,
            attributes=attributes,
            children=_PROGRESS_FIELDS | children,
        )
        vanilla = CampaignProgress.from_main_xml(
            root, strict=strict, on_conflict=on_level_conflict, unknown=unknown, path=path
        )
        mod_fields = mods_codec.decode_campaigns(
            root,
            strict=strict,
            on_campaign_conflict=on_campaign_conflict,
            on_level_conflict=on_level_conflict,
            vanilla=vanilla,
            unknown=unknown,
            path=path,
        )
        fields.update(mod_fields)
        fields.update(
            levels=vanilla.levels,
            unresolved_levels=vanilla.unresolved_levels,
            unlocked_levels=vanilla.unlocked_levels,
            total_strawberries=vanilla.total_strawberries,
            poem=vanilla.poem,
        )
        context = XmlReadContext.from_element(root, strict=strict, path=path)
        result = cls.model_validate(fields, context=context)
        result.__pydantic_fields_set__.difference_update(defaulted)
        return result

    def _modded_save_data_for_output(self) -> bool:
        return values_codec.parse_bool(self.has_modded_save_data)

    def _xml_value(self, name: str) -> object:
        if name != 'vanilla_session':
            return super()._xml_value(name)
        other = self.vanilla_session
        selected = self.mods.session
        if (
            selected is not None
            and other is not None
            and (other.sid, other.side) == (selected.sid, selected.side)
            and (changed := list(selected.iter_changed_fields()))
        ):
            other = deepcopy(other)
            for field in changed:
                setattr(other, field, getattr(selected, field))
        return other

    def _write_xml(self, root: etree.Element, *, exclude_unset: bool = False) -> None:
        """Encode all modeled main-save data, without editing the domain values."""
        vanilla = vanilla_codec.encode_progress(self.vanilla)
        fields = mods_codec.encode_campaigns(self, vanilla=self.vanilla)
        # Scalar validation happens before the complete output is attached.
        output = etree.Element('SaveData')
        # This root coordinates its own complete layout, rather than emitting
        # the backup layout inherited from ModSaveData as well.
        XmlModel._write_xml(self, output, exclude_unset=exclude_unset)  # noqa: SLF001
        marker = xml_tree.child(output, 'HasModdedSaveData')
        has_modded_save_data = self._modded_save_data_for_output()
        if marker is None and has_modded_save_data:
            marker = etree.SubElement(output, 'HasModdedSaveData')
        if marker is not None:
            marker.text = str(has_modded_save_data).lower()
        output.extend(iter(vanilla))
        output.extend(fields.values())
        root.extend(iter(output))

    @staticmethod
    def install_campaigns(
        *changes: tuple[MainSaveData, CampaignProgress | VanillaProgressView],
    ) -> None:
        """Install prepared campaigns across roots after every root/index preflight.

        Each root receives one replacement of the same campaign. This only
        installs members and marks Mod edits; cumulative counters and
        companion documents belong to the calling operation's prepared values.
        Shared Mod membership slots cannot receive multiple requests, including
        identical candidates; separate vanilla root slots remain independent.
        """
        prepared = tuple(
            (save, campaign.snapshot() if isinstance(campaign, VanillaProgressView) else campaign)
            for save, campaign in changes
        )
        if len({id(save) for save, _ in changes}) != len(changes):
            raise ValueError('Each save may receive only one prepared campaign replacement.')
        if len({campaign.name for _, campaign in changes}) > 1:
            raise ValueError('Prepared replacements must have the same campaign name.')
        targets: set[tuple[int, str]] = set()
        for save, campaign in prepared:
            owner = save if campaign.name == 'Celeste' else save.mods.campaigns
            target = id(owner), campaign.name
            if target in targets:
                raise ProgressConflictError(
                    f'Multiple prepared replacements target the same campaign membership: '
                    f'{campaign.name!r}.'
                )
            targets.add(target)
        vanilla: list[tuple[MainSaveData, CampaignProgress, tuple[IndexDelta, ...]]] = []
        modded: list[tuple[CampaignMembers, CampaignProgress]] = []
        for save, campaign in prepared:
            if campaign.name == 'Celeste':
                campaign.levels.validate_ownership()
                vanilla.append(
                    (
                        save,
                        campaign,
                        save.levels._prepare_replacement(save.vanilla, campaign.levels),  # noqa: SLF001
                    )
                )
            else:
                for retained in save.mods.campaigns.values():
                    if retained.name != campaign.name:
                        save.mods.campaigns.validate_member(retained)
                save.mods.recycled_campaigns.validate_ownership()
                modded.append((save.mods.campaigns, campaign))
        CampaignMembers.update_together(*modded)
        for save, campaign, updates in vanilla:
            save._levels = campaign.levels  # noqa: SLF001
            save.unlocked_levels = campaign.unlocked_levels
            save.total_strawberries = campaign.total_strawberries
            save.poem = campaign.poem
            save.unresolved_levels = campaign.unresolved_levels
            for update in updates:
                update.apply()
        for save, campaign in prepared:
            if campaign.name != 'Celeste':
                save.has_modded_save_data = True

    @property
    def vanilla(self) -> VanillaProgressView:
        return self._vanilla_view

    def replace_vanilla_levels(
        self, values: Iterable[LevelProgress] | Mapping[str, LevelProgress]
    ) -> None:
        replacement = (
            values
            if isinstance(values, LevelMembers) and values.name == 'Celeste'
            else LevelMembers('Celeste', values.values() if isinstance(values, Mapping) else values)
        )
        replacement.validate_ownership()
        updates = self.levels._prepare_replacement(self.vanilla, replacement)  # noqa: SLF001
        self._levels = replacement
        for update in updates:
            update.apply()

    def install_mod_progress(self, value: ModSaveData | ModsProgressView) -> None:
        super().install_mod_progress(value)
        self.has_modded_save_data = True

    @property
    def needs_mod_recovery(self) -> bool:
        return (
            not self.has_modded_save_data
            and self.mods.partition_size() <= 1
            and self.mods.partition_size(recycled=True) == 0
        )

    @property
    def last_map(self) -> MapKey | None:
        return self.last_map_ref.resolve_key(vanilla=True)

    @property
    def current_map(self) -> MapKey | None:
        if self.mods.has_session_data:
            return self.mods.current_map
        value = self.vanilla_session
        if value is None or not value.in_level:
            return None
        return value.sid, value.side

    @property
    def session(self) -> Session | None:
        if self.mods.has_session_data:
            return self.mods.session
        return self.vanilla_session

    @session.setter
    def session(self, value: Session | None) -> None:
        if value is None:
            self.vanilla_session = None
            self.mods.session = None
        elif (value.sid, value.side) == self.current_map:
            if self.mods.has_session_data:
                self.mods.session = value
            else:
                self.vanilla_session = value
        else:
            self.mods.session = value
            self.vanilla_session = deepcopy(value) if value.sid in VANILLA_SIDS else None
            self.has_modded_save_data = True
