"""Persisted Mod fields shared by main saves and Everest's backup files."""

from collections.abc import Set as AbstractSet
from functools import partial
from typing import Annotated, Self

from lxml import etree
from pydantic import ConfigDict, InstanceOf

from berries.model_behavior import FieldModel, PrivateAttr

from .codecs.xml import mods as mods_codec
from .codecs.xml import session as session_codec
from .codecs.xml.model import Xml, XmlModel, XmlReadContext
from .conflicts import CampaignConflictResolver, LevelConflictResolver
from .partition import ProgressPartition
from .progress.campaign import CampaignProgress
from .progress.map_ref import MapRef
from .progress.members import CampaignMembers, LevelIndex, LevelIndexState, MembershipUpdate
from .progress.session import Session
from .progress.views import ModsProgressView
from .unknown import UnknownFieldPolicy


class ModSaveData(XmlModel, FieldModel, elements=True):
    """Own Mod values; views and indexes are derived, not persisted state."""

    model_config = ConfigDict(extra='forbid', validate_by_name=True)
    xml_tag = 'ModSaveData'

    safe_last_map_ref: Annotated[MapRef | None, Xml('LastArea_Safe')] = None
    safe_session: Annotated[
        Session | None,
        Xml(
            'CurrentSession_Safe',
            element_encoder=partial(session_codec.current_element, vanilla=False),
        ),
    ] = None
    vanilla_entry: Annotated[InstanceOf[CampaignProgress] | None, Xml(None)] = None

    _campaigns: InstanceOf[CampaignMembers] = PrivateAttr(
        alias='campaigns', default_factory=CampaignMembers
    )
    _recycled_campaigns: InstanceOf[CampaignMembers] = PrivateAttr(
        alias='recycled_campaigns',
        default_factory=partial(CampaignMembers, partition=ProgressPartition.RECYCLED),
    )
    _mods_view: ModsProgressView = PrivateAttr(compare=False, copy=False)
    _flat_levels: LevelIndexState = PrivateAttr(compare=False, copy=False)
    _recycled_flat_levels: LevelIndexState = PrivateAttr(compare=False, copy=False)

    def model_post_init(self, _: object, /) -> None:
        for name, value, expected in (
            ('campaigns', self.campaigns, ProgressPartition.MODS),
            ('recycled_campaigns', self.recycled_campaigns, ProgressPartition.RECYCLED),
        ):
            if value.partition is not expected:
                raise ValueError(f'{name} requires the {expected.value} partition.')
        self._init_indexes()

    def _init_indexes(self) -> None:
        self._flat_levels = LevelIndexState(self.campaigns)
        self._recycled_flat_levels = LevelIndexState(self.recycled_campaigns)
        self._mods_view = ModsProgressView(self, self._flat_levels.view)

    def model_post_copy(self) -> None:
        self._init_indexes()

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
        """Decode complete backup values, without retaining the input tree."""
        path = 'ModSaveData' if path is None else path
        fields, defaulted = cls._read_xml_fields(
            root,
            strict=strict,
            unknown=unknown,
            path=path,
            attributes=attributes,
            children=frozenset(mods_codec.FIELDS) | children,
        )
        fields.update(
            mods_codec.decode_campaigns(
                root,
                strict=strict,
                on_campaign_conflict=on_campaign_conflict,
                on_level_conflict=on_level_conflict,
                unknown=unknown,
                path=path,
            )
        )
        result = cls.model_validate(
            fields, context=XmlReadContext.from_element(root, strict=strict, path=path)
        )
        result.__pydantic_fields_set__.difference_update(defaulted)
        return result

    def _write_xml(self, root: etree.Element, *, exclude_unset: bool = False) -> None:
        output = etree.Element('ModSaveData')
        super()._write_xml(output, exclude_unset=exclude_unset)
        output.extend(mods_codec.encode_campaigns(self).values())
        root.extend(iter(output))

    @property
    def campaigns(self) -> CampaignMembers:
        return self._campaigns

    @property
    def recycled_campaigns(self) -> CampaignMembers:
        return self._recycled_campaigns

    @property
    def mods(self) -> ModsProgressView:
        return self._mods_view

    @property
    def flat_levels(self) -> LevelIndex:
        return self._flat_levels.view

    @property
    def recycled_flat_levels(self) -> LevelIndex:
        return self._recycled_flat_levels.view

    def install_mod_progress(self, value: ModSaveData | ModsProgressView) -> None:
        """Install explicit prepared data after checking both partition indexes."""
        if isinstance(value, ModSaveData):
            value = value.mods
        value.campaigns.validate_ownership()
        value.recycled_campaigns.validate_ownership()
        updates = self._prepare_mod_indexes(value)
        self._campaigns = value.campaigns
        self._recycled_campaigns = value.recycled_campaigns
        self.safe_last_map_ref = value.last_map_ref
        self.safe_session = value.session
        self.vanilla_entry = value.vanilla_entry
        for update in updates:
            update.apply()

    def _prepare_mod_indexes(self, value: ModsProgressView) -> tuple[MembershipUpdate, ...]:
        """Preflight the Mod partitions before installing any prepared values."""
        return (
            self._flat_levels.prepare(sources=(value.campaigns,)),
            self._recycled_flat_levels.prepare(sources=(value.recycled_campaigns,)),
        )
