import pytest

from pist.map_entrances import (
    EntranceValue,
    MapEntranceRegion,
    MapEntranceRule,
    MapEntranceSource,
)


def _region() -> MapEntranceRegion:
    return MapEntranceRegion(x=EntranceValue(value=0), y=EntranceValue(value=0))


@pytest.mark.parametrize(
    'model',
    (
        lambda: EntranceValue(attr=''),
        lambda: MapEntranceRule(
            source=MapEntranceSource.ENTITY,
            name='',
            region=_region(),
        ),
        lambda: MapEntranceRule(
            source=MapEntranceSource.ENTITY,
            name='Example/Entrance',
            target_attr='',
            region=_region(),
        ),
    ),
)
def test_map_entrance_rules_reject_blank_attribute_and_entity_names(model) -> None:
    with pytest.raises(ValueError):
        model()
