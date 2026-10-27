"""Direct-lxml field bindings preserve save input distinctions and diagnostics."""

from typing import Annotated, Self

import pytest
from lxml import etree
from pydantic import Field, ValidationError, field_validator, model_validator

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.codecs import values
from berries.game.saves.codecs.xml import tree
from berries.game.saves.codecs.xml.fields import MapFields, RespawnFields, XmlText
from berries.game.saves.codecs.xml.model import Xml, XmlModel, XmlReadContext
from berries.game.saves.errors import BadProgressSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.main_save_data import MainSaveData
from berries.game.saves.progress.assists import Assists
from berries.game.saves.progress.map import MapProgress
from berries.game.saves.progress.map_ref import MapRef
from berries.game.saves.progress.session import Session
from berries.game.saves.progress.session_state import AudioParameter, AudioState, PlayerInventory
from berries.game.saves.unknown import UnknownSaveFieldError, UnknownSaveFieldWarning
from berries.map_entity_id import MapEntityID
from berries.model_behavior import FieldModel, PrivateAttr
from test_support.save_docs import normal_save_xml, parsed_session


class Example(XmlModel):
    number: values.SaveCount = Field(0, alias='Number')
    title: Annotated[XmlText | None, Xml('Title', nillable=True)] = None
    names: Annotated[list[XmlText], Xml('Names/string')] = Field(default_factory=list)


@pytest.mark.parametrize('value', ('NaN', 'INF', '-INF'))
def test_frozen_snapshots_from_lenient_input_still_validate_before_output(value: str) -> None:
    root = etree.fromstring(f'<RespawnPoint><X>{value}</X><Y>0</Y></RespawnPoint>')
    fields = RespawnFields.from_xml(root, strict=False)
    with pytest.raises(ValueError, match='finite'):
        fields.to_xml('RespawnPoint')


@pytest.mark.parametrize(
    ('xml', 'title', 'present'),
    (
        ('<Example/>', None, False),
        ('<Example><Title/></Example>', '', True),
        (f'<Example xmlns:xsi="{tree.XSI}"><Title xsi:nil="true"/></Example>', None, True),
    ),
)
def test_missing_empty_and_nil_remain_distinct(xml: str, title: str | None, present: bool) -> None:
    value = Example.from_xml(tree.parse(xml).getroot())
    assert value.title == title
    assert ('title' in value.model_fields_set) == present
    restored = Example.from_xml(value.to_xml('Example', exclude_unset=True))
    assert restored == value
    assert restored.model_fields_set == value.model_fields_set


@pytest.mark.parametrize('count', (0, 1, 32, 256))
def test_map_projection_preserves_all_domain_values_and_input(count: int) -> None:
    progress = MapProgress(
        time_played=Duration.from_dotnet_ticks(170001),
        deaths=3,
        strawberries=[MapEntityID('room', i) for i in range(count)],
        checkpoints=['', 'a', 'z'],
        total_strawberries=count + 5,
    )
    progress.bests.time = Duration.from_dotnet_ticks(-1)
    progress.bests.deaths = 0
    node = progress.to_xml()
    before = etree.tostring(node)
    restored = MapProgress.from_xml(node)
    assert restored == progress
    assert etree.tostring(node) == before


@pytest.mark.parametrize(
    ('xml', 'path'),
    (
        ('<Example Future="1"/>', 'Example.@Future'),
        ('<Example><Future/></Example>', 'Example.Future'),
        ('<Example><Names Future="1"/></Example>', 'Example.Names.@Future'),
        ('<Example><Names><string Future="1"/></Names></Example>', 'Example.Names[0].@Future'),
        ('<Example><Names><string><Future/></string></Names></Example>', 'Example.Names[0].Future'),
    ),
)
def test_unknown_names_include_empty_elements_and_projection_context(xml: str, path: str) -> None:
    node = tree.parse(xml).getroot()
    with pytest.raises(UnknownSaveFieldError) as caught:
        Example.from_xml(node)
    assert caught.value.path == path
    with pytest.warns(UnknownSaveFieldWarning) as seen:
        value = Example.from_xml(node, unknown='warn')
    assert len(seen) == 1
    assert isinstance(seen[0].message, UnknownSaveFieldWarning)
    assert seen[0].message.path == path
    assert b'Future' not in etree.tostring(value.to_xml('Example'))


def test_warning_order_matches_root_then_descendants() -> None:
    node = tree.parse(
        '<Example Future="1"><Names Future="1"><string Future="1"/></Names><Other/></Example>'
    ).getroot()
    with pytest.warns(UnknownSaveFieldWarning) as seen:
        Example.from_xml(node, unknown='warn')
    paths = []
    for item in seen:
        assert isinstance(item.message, UnknownSaveFieldWarning)
        paths.append(item.message.path)
    assert paths == [
        'Example.@Future',
        'Example.Other',
        'Example.Names.@Future',
        'Example.Names[0].@Future',
    ]


@pytest.mark.parametrize('name', ('Title', 'Names'))
def test_duplicate_single_fields_are_rejected(name: str) -> None:
    with pytest.raises(ValueError, match='Duplicate XML field'):
        Example.from_xml(tree.parse(f'<Example><{name}/><{name}/></Example>').getroot())


def test_nil_collection_ignores_known_payload_and_comments_do_not_change_text() -> None:
    node = tree.parse(
        f'<Example xmlns:xsi="{tree.XSI}"><Title>a<!--c-->b<?p x?>c</Title>'
        '<Names xsi:nil="true"><string Future="ignored"/></Names></Example>'
    ).getroot()
    value = Example.from_xml(node)
    assert value.title == 'abc'
    assert value.names == []


def test_repeated_values_are_not_deduplicated_by_binding() -> None:
    value = Example.from_xml(
        tree.parse(
            '<Example><Names><string>x</string><string>x</string></Names></Example>'
        ).getroot()
    )
    assert value.names == ['x', 'x']


def test_nil_on_nonnullable_parent_does_not_discard_its_collection_fields() -> None:
    node = tree.parse(
        f'<AreaModeStats xmlns:xsi="{tree.XSI}" xsi:nil="true">'
        '<Strawberries><EntityID Key="room:1"/></Strawberries></AreaModeStats>'
    ).getroot()
    assert MapProgress.from_xml(node).strawberries == {MapEntityID('room', 1)}


@pytest.mark.parametrize('number', ('-1', '2147483648', '1.5', 'true'))
def test_pydantic_constraints_still_apply(number: str) -> None:
    with pytest.raises(ValidationError):
        Example.from_xml(tree.parse(f'<Example Number="{number}"/>').getroot())


def test_element_layout_is_inherited_and_peer_fields_belong_to_caller() -> None:
    class Elements(XmlModel, elements=True):
        number: values.SaveCount = Field(0, alias='Number')

    class MoreElements(Elements):
        text: XmlText = Field('', alias='Text')

    value = MoreElements.from_xml(
        tree.parse('<Root Other="1"><Number>3</Number><Text>a</Text><Peer/></Root>').getroot(),
        attributes={'Other'},
        children={'Peer'},
    )
    assert value.number == 3 and value.text == 'a'
    assert etree.tostring(value.to_xml('Root')) == b'<Root><Number>3</Number><Text>a</Text></Root>'


def test_invalid_output_entity_and_fractional_tick_are_rejected() -> None:
    with pytest.raises(ValueError):
        MapFields.model_validate({'strawberries': [MapEntityID('bad:room', 1)]}).to_xml(
            'AreaModeStats'
        )
    with pytest.raises(ValueError, match='not exactly representable'):
        MapFields.model_validate({'TimePlayed': Duration.from_seconds(1, 3)}).to_xml(
            'AreaModeStats'
        )


@pytest.mark.parametrize('path', ('', 'A//B', '@A/B', '@', '@A'))
def test_invalid_or_duplicate_bindings_are_rejected(path: str) -> None:
    class Invalid(XmlModel):
        first: Annotated[XmlText, Xml(path)] = ''
        second: Annotated[XmlText, Xml(path)] = ''

    with pytest.raises(TypeError):
        Invalid.from_xml(etree.Element('Root'))


def test_main_data_binds_its_own_fields_and_nested_domain_values() -> None:
    data = MainSaveData.from_xml(
        tree.parse(
            normal_save_xml(
                '<SaveData><Name>Player</Name><Time>170000</Time><TotalDeaths>3</TotalDeaths>'
                '<Assists><GameSpeed>8</GameSpeed></Assists><LastArea ID="7" Mode="BSide"/>'
                '<Flags><string>z</string><string>a</string></Flags>'
                '<SummitGems><boolean>true</boolean><boolean>false</boolean><boolean>false</boolean>'
                '<boolean>false</boolean><boolean>false</boolean><boolean>false</boolean></SummitGems>'
                '</SaveData>'
            )
        ).getroot()
    )
    assert isinstance(data, XmlModel)
    assert data.name == 'Player' and data.deaths == 3
    assert isinstance(data.assists, Assists) and data.assists.game_speed == 8
    assert isinstance(data.last_map_ref, MapRef) and data.last_map_ref.level_id == 7
    assert data.flags == {'z', 'a'}
    assert data.summit_gems == [True, False, False, False, False, False]
    assert data.last_save_text is None
    root = data.to_xml('SaveData')
    assert root.findtext('TotalDeaths') == '3'
    assert root.findtext('Assists/GameSpeed') == '8'
    assert root.find('LastArea') is not None
    assert root.find('LastSave') is None
    assert [item.text for item in root.findall('Flags/string')] == ['a', 'z']


def test_failed_decode_and_write_do_not_modify_inputs_or_edited_values() -> None:
    data = MainSaveData(name='kept')
    root = tree.parse(
        normal_save_xml('<SaveData><Name>changed</Name><TotalDeaths>-1</TotalDeaths></SaveData>')
    ).getroot()
    before = etree.tostring(root)
    with pytest.raises(ValidationError):
        MainSaveData.from_xml(root)
    assert etree.tostring(root) == before
    assert data.name == 'kept' and data.deaths == 0
    data.deaths = -1
    with pytest.raises(ValidationError):
        data.to_xml('SaveData')
    assert data.deaths == -1


def test_xml_missing_override_does_not_change_blank_constructor_or_presence() -> None:
    data = MainSaveData()
    assert data.last_save_text == '0001-01-01T00:00:00'
    read = MainSaveData.from_xml(etree.Element('SaveData'), strict=False)
    assert read.last_save_text is None
    assert 'last_save_text' not in read.model_fields_set


def test_existing_model_write_does_not_reconstruct_runtime_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save = MainSave()
    mods = save.mods

    def unexpected_init(self: MainSave, **data: object) -> None:
        raise AssertionError('Field validation must not construct a new MainSave.')

    monkeypatch.setattr(MainSave, '__init__', unexpected_init)
    save.deaths = 3
    assert '<TotalDeaths>3</TotalDeaths>' in save.tostring()
    assert save.mods is mods


def test_field_and_model_validators_are_not_skipped_by_read_or_output_validation() -> None:
    class Checked(XmlModel):
        number: int = Field(default=0, alias='Number')

        @field_validator('number')
        @classmethod
        def double(cls, value: int) -> int:
            return value * 2

        @model_validator(mode='after')
        def increment(self) -> Self:
            self.number += 1
            return self

    value = Checked.from_xml(tree.parse('<Root Number="3"/>').getroot())
    assert value.number == 7
    value.number = 4
    assert value.to_xml('Root').get('Number') == '9'
    assert value.number == 4


def test_summit_gem_output_does_not_coerce_non_boolean_edits() -> None:
    data = MainSaveData()
    data.summit_gems = [1] * 6  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(ValidationError):
        data.to_xml('SaveData')


def test_fixed_domain_models_bind_directly_and_preserve_constructor_defaults() -> None:
    assert isinstance(Assists(), XmlModel)
    assert Assists().game_speed == 10
    assert Assists.from_xml(etree.Element('Assists')).game_speed == 0
    inventory = PlayerInventory(dashes=2, backpack=True)
    assert PlayerInventory.from_xml(inventory.to_xml()) == inventory
    assert inventory.to_xml().tag == 'Inventory'
    ref = MapRef(level_id=7)
    assert hash(ref) == hash(MapRef(level_id=7))
    with pytest.raises(ValidationError):
        ref.level_id = 8


def test_repeated_domain_bindings_keep_order_and_nil_collection_semantics() -> None:
    value = AudioState.from_xml(
        tree.parse(
            '<Audio><Music Event="music"><Parameters>'
            '<MEP Key="same" Value="1"/><MEP Key="same" Value="2"/>'
            '</Parameters></Music><Ambience/></Audio>'
        ).getroot()
    )
    assert value.music.parameters == [
        AudioParameter(key='same', value=1.0),
        AudioParameter(key='same', value=2.0),
    ]
    assert AudioState.from_xml(value.to_xml()) == value
    assert value.to_xml().find('AmbienceVolume') is not None
    nil = AudioState.from_xml(
        tree.parse(
            f'<Audio xmlns:xsi="{tree.XSI}"><Music><Parameters xsi:nil="true">'
            '<MEP Value="not a number"/></Parameters></Music></Audio>'
        ).getroot()
    )
    assert nil.music.parameters == []


def test_direct_domain_output_revalidates_nested_edits_without_modifying_values() -> None:
    value = AudioState()
    parameter = AudioParameter(value=0.5)
    value.music.parameters.append(parameter)
    parameter.value = 'invalid'  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(ValidationError):
        value.to_xml()
    assert parameter.value == 'invalid'
    inventory = PlayerInventory()
    inventory.dashes = -1
    with pytest.raises(ValidationError):
        inventory.to_xml()
    assert inventory.dashes == -1


def test_xml_output_without_default_tag_requires_explicit_name() -> None:
    class Value(XmlModel):
        value: int = 1

    with pytest.raises(TypeError, match='element name'):
        Value().to_xml()


@pytest.mark.parametrize('kind', ['missing', 'nil', 'empty'])
def test_nil_as_missing_uses_field_factory_and_preserves_presence(kind: str) -> None:
    calls = []

    def make_values() -> list[int]:
        calls.append(1)
        return [7]

    class Example(XmlModel):
        values: Annotated[list[int], Xml('Values/number', nil_as_missing=True)] = Field(
            default_factory=make_values
        )

    root = etree.Element('Example')
    if kind != 'missing':
        node = etree.SubElement(root, 'Values')
        if kind == 'nil':
            node.set(f'{{{tree.XSI}}}nil', 'true')
            etree.SubElement(node, 'number').text = 'ignored invalid value'
    value = Example.from_xml(root)
    assert value.values == ([] if kind == 'empty' else [7])
    assert calls == ([] if kind == 'empty' else [1])
    assert ('values' in value.model_fields_set) == (kind == 'empty')


def test_nested_xml_models_infer_single_optional_and_list_bindings() -> None:
    class Item(XmlModel):
        value: int

    class Example(XmlModel):
        item: Item
        optional: Annotated[Item | None, Xml('Optional')] = None
        items: Annotated[list[Item], Xml('Items/Item')] = Field(default_factory=list)
        optional_items: Annotated[list[Item] | None, Xml('OptionalItems/Item')] = None

    source = etree.fromstring(
        '<Example><item value="1"/><Items><Item value="2"/><Item value="3"/></Items></Example>'
    )
    value = Example.from_xml(source)
    assert value.item == Item(value=1)
    assert value.optional is None and value.optional_items is None
    assert value.items == [Item(value=2), Item(value=3)]
    assert Example.from_xml(value.to_xml('Example')) == value
    etree.SubElement(source, 'Optional', value='4')
    etree.SubElement(source, 'OptionalItems')
    restored = Example.from_xml(source)
    assert restored.optional == Item(value=4) and restored.optional_items == []
    assert Example.from_xml(restored.to_xml('Example')) == restored


def test_explicit_nested_encoder_overrides_only_the_inferred_encoder() -> None:
    class Item(XmlModel):
        value: int

    def encode(item: Item) -> etree.Element:
        return etree.Element('Custom', value=str(item.value + 1))

    class Example(XmlModel):
        item: Annotated[Item, Xml('Item', element_encoder=encode)]

    value = Example.from_xml(etree.fromstring('<Example><Item value="2"/></Example>'))
    assert value.item.value == 2
    node = value.to_xml('Example').find('Item')
    assert node is not None and node.get('value') == '3'


def test_inferred_nested_decoder_receives_strict_policy() -> None:
    class Example(XmlModel):
        session: Annotated[Session, Xml('Session')]

    node = parsed_session('Pack/Map', LevelSide.A).to_xml()
    node.set('Time', '1')
    root = etree.Element('Example')
    root.append(node)
    with pytest.raises(ValueError, match='17 ms'):
        Example.from_xml(root, strict=True)
    value = Example.from_xml(root, strict=False)
    assert value.session.time_played == Duration.from_dotnet_ticks(1)


@pytest.mark.parametrize(
    'xml',
    (
        '<Example/>',
        '<Example><Group/></Example>',
        f'<Example xmlns:xsi="{tree.XSI}"><Group><Value xsi:nil="true"/></Group></Example>',
    ),
)
def test_strict_required_binding_precedes_read_defaults(xml: str) -> None:
    class Required(XmlModel):
        value: Annotated[int, Xml('Group/Value', strict_required=True, missing=7)] = 3

    root = etree.fromstring(xml)
    assert Required().value == 3
    with pytest.raises(BadProgressSave, match='Missing or nil'):
        Required.from_xml(root, strict=True)
    if root.find('Group/Value') is None:
        assert Required.from_xml(root).value == 7


def test_read_context_is_transient_and_read_defaults_are_not_explicit_inputs() -> None:
    seen = []

    class ContextFields(XmlModel):
        number: int = Field(alias='Number')
        side: Annotated[int, Xml('@Side', missing=2)] = 1

        def model_post_init(self, context: object, /) -> None:
            seen.append(context)

    root = etree.fromstring('<Embedded Number="4"/>')
    value = ContextFields.from_xml(root, strict=True, path='Parent.Child')
    context = seen.pop()
    assert isinstance(context, XmlReadContext)
    assert context.strict and context.path == 'Parent.Child' and context.tag == 'Embedded'
    assert value.model_dump() == {'number': 4, 'side': 2}
    assert value.model_fields_set == {'number'}
    value.number = 5
    assert value.to_xml('Embedded').get('Number') == '5'
    assert not seen


def test_private_input_annotations_bind_readonly_values_and_validate_output() -> None:
    class Readonly(XmlModel, FieldModel):
        _number: Annotated[values.SaveCount, Xml('@Number')] = PrivateAttr(
            alias='number', default=0
        )

        @property
        def number(self) -> int:
            return self._number

    value = Readonly.from_xml(etree.fromstring('<Readonly Number="2"/>'))
    assert value.number == 2
    assert value.to_xml('Readonly', exclude_unset=True).get('Number') == '2'
    assert Readonly().to_xml('Readonly', exclude_unset=True).get('Number') is None
    with pytest.raises(ValueError):
        Readonly.from_xml(etree.fromstring('<Readonly Number="-1"/>'))
    with pytest.raises(AttributeError):
        value.number = 3  # pyright: ignore[reportAttributeAccessIssue]


def test_private_nested_model_bindings_are_inferred_without_rebuilding_unbound_inputs() -> None:
    calls = []

    def make_values() -> list[int]:
        calls.append(1)
        return []

    class Nested(XmlModel, FieldModel):
        _child: Annotated[PlayerInventory, Xml('Inventory')] = PrivateAttr(alias='child')
        _values: list[int] = PrivateAttr(alias='values', default_factory=make_values)

        @property
        def child(self) -> PlayerInventory:
            return self._child

    value = Nested.from_xml(
        etree.fromstring('<Nested><Inventory><Dashes>2</Dashes></Inventory></Nested>')
    )
    assert value.child.dashes == 2 and len(calls) == 1
    value.child.dashes = -1
    with pytest.raises(ValueError):
        value.to_xml('Nested')
    value.child.dashes = 3
    assert value.to_xml('Nested').findtext('Inventory/Dashes') == '3'
    assert len(calls) == 1
