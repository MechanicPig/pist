"""Encode the original game's fixed, position-indexed chapter protocol."""

from typing import TYPE_CHECKING

from lxml import etree

from berries.game.saves.errors import BadProgressSave
from berries.game.vanilla import VANILLA_SIDS

from . import campaign as campaign_codec
from . import tree as xml_tree

if TYPE_CHECKING:
    from berries.game.saves.progress.campaign import CampaignProgress
    from berries.game.saves.progress.views import VanillaProgressView

FIELDS = ('Areas', 'Poem', 'UnlockedAreas', 'TotalStrawberries')


def validate_progress_input(root: etree.Element, campaign: CampaignProgress, *, path: str) -> None:
    """Check normal vanilla structure before missing/duplicate input is lost."""
    for name in FIELDS:
        node = xml_tree.child(root, name)
        if node is None or xml_tree.is_nil(node):
            raise BadProgressSave(f'Missing or nil normal main-save field {name} at {path}.')
    areas = xml_tree.child(root, 'Areas')
    assert areas is not None
    if tuple(campaign.levels) != VANILLA_SIDS or sum(
        1 for _ in areas.iterchildren('AreaStats')
    ) != len(VANILLA_SIDS):
        raise BadProgressSave('Normal vanilla progress requires all 11 chapters in order.')


def encode_progress(campaign: CampaignProgress | VanillaProgressView) -> etree.Element:
    """Require all vanilla slots in game order and encode their derived numeric IDs."""
    if campaign.name != 'Celeste' or tuple(campaign.levels) != VANILLA_SIDS:
        raise BadProgressSave(
            'Vanilla progress must contain all 11 chapters in VANILLA_SIDS order. '
            'Repair the chapter collection explicitly before writing.'
        )
    root = campaign_codec.encode_progress(campaign)
    areas = xml_tree.child(root, 'Areas')
    assert areas is not None
    nodes = tuple(areas.iterchildren('AreaStats'))
    if len(nodes) != len(VANILLA_SIDS):
        raise BadProgressSave('Resolve unrecognized vanilla AreaStats before writing.')
    for i, node in enumerate(nodes):
        node.set('ID', str(i))
        node.attrib.pop('SID', None)  # pyright: ignore[reportUnusedCallResult]
    return root
