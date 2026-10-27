"""Application messages preserve the structured game facts they describe."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from berries.game import campaigns as backend
from berries.game.collab import JournalRefs
from berries.game.content import ContentPath
from berries.game.everest import Dependency, Version
from berries.game.map_hiders import MapHiderIssue, MapHiderRules
from berries.game.maps import VanillaMap
from pist.campaign_catalog import CatalogLoadResult, journal_messages, missing_ref_messages
from test_support.catalog import make_catalog
from test_support.mod_factory import make_installed_mod


def test_application_catalog_formats_dependencies_and_preserves_snapshot() -> None:
    optional = make_installed_mod(
        source='zip',
        filename='Helper.zip',
        path='Helper.zip',
        metadata_name='Optional',
        metadata_version='1.0.0',
    )
    unavailable = make_installed_mod(
        source='zip',
        filename='Example.zip',
        path='Example.zip',
        metadata_name='Example',
        metadata_version='1.0.0',
        dependencies=[Dependency(name='Missing', version=Version.parse('1.0.0'))],
        optional_dependencies=[Dependency(name='Optional', version=Version.parse('2.0.0'))],
    )
    raw = make_catalog((optional, unavailable))
    catalog = CatalogLoadResult.from_catalog(raw, diagnostics=('扫描读取失败',))
    assert catalog.catalog is raw
    assert not isinstance(catalog, backend.CampaignCatalog)
    assert catalog.diagnostics[0] == '扫描读取失败'
    assert len(catalog.diagnostics) == 2
    assert 'Missing（未加载，需要 1.0.0）' in catalog.diagnostics[1]
    assert 'Optional 1.0.0（需要 2.0.0）' in catalog.diagnostics[1]


def test_application_catalog_formats_reserved_levelset_collision() -> None:
    mod = make_installed_mod(
        source='zip',
        filename='Example.zip',
        path='Example.zip',
        metadata_name='Example',
        metadata_version='1.0.0',
        maps=[ContentPath('Maps/Celeste/1-ForsakenCity.bin')],
    )
    raw = make_catalog(
        (mod,), vanilla_maps=(VanillaMap(ContentPath('Maps/1-ForsakenCity.bin'), Path('Content')),)
    )
    catalog = CatalogLoadResult.from_catalog(raw)
    assert len(catalog.diagnostics) == 1
    assert 'Example' in catalog.diagnostics[0]
    assert 'Celeste/1-ForsakenCity' in catalog.diagnostics[0]


@pytest.mark.parametrize(
    ('content', 'stage'), [('[', 'parse'), ('HelperMapsToHide: 3', 'validate')]
)
def test_application_catalog_formats_hider_settings_failure(
    tmp_path: Path, content: str, stage: str
) -> None:
    path = tmp_path / 'Saves' / 'ChroniaHelper' / 'MapHider.yaml'
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding='utf-8')
    mod = make_installed_mod(
        source='zip',
        filename='ChroniaHelper.zip',
        path='ChroniaHelper.zip',
        metadata_name='ChroniaHelper',
        metadata_version='1.0.0',
    )
    raw = make_catalog((mod,), map_hider_rules=MapHiderRules(tmp_path))
    catalog = CatalogLoadResult.from_catalog(raw)
    assert len(catalog.diagnostics) == 1
    assert str(path) in catalog.diagnostics[0]
    issue = raw.issues[0]
    assert isinstance(issue, MapHiderIssue)
    assert issue.failure.stage == stage
    if stage == 'validate':
        assert isinstance(issue.failure.cause, ValidationError)


def test_reference_messages_belong_to_application() -> None:
    assert missing_ref_messages(('Missing',), source='配置') == ('配置的地图集不存在：Missing',)
    assert journal_messages(JournalRefs((), 2), ('Missing',)) == (
        '发现 2 个没有有效 levelset 的日志入口。',
        '日志引用的地图集不存在：Missing',
    )
    assert journal_messages(JournalRefs(()), ()) == ()
