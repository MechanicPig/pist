import json

import pytest
import yaml
from pydantic import ValidationError

from berries.game.everest import MANIFEST_ADAPTER, Dependency, ModMetadata, Version


def test_manifest_boundary_builds_hashable_values_without_changing_json_shape() -> None:
    manifest = MANIFEST_ADAPTER.validate_python(
        yaml.load(
            """
- Name: Example
  Version: 1.2.3
  Dependencies:
    - Name: Required
      Version: 1.0
  OptionalDependencies:
    - Name: Optional
      Version: 2.0
  DLL: bin/Example.dll
  FutureField: retained-by-external-protocol
""",
            Loader=yaml.BaseLoader,
        )
    )
    metadata = manifest[0]
    assert isinstance(metadata.dependencies, tuple)
    assert isinstance(metadata.optional_dependencies, tuple)
    assert metadata.dependencies == (Dependency(name='Required', version=Version.parse('1.0')),)
    assert metadata.optional_dependencies == (
        Dependency(name='Optional', version=Version.parse('2.0')),
    )
    encoded = MANIFEST_ADAPTER.dump_json(manifest)
    assert json.loads(encoded)[0]['dependencies'] == [{'name': 'Required', 'version': '1.0'}]
    restored = MANIFEST_ADAPTER.validate_json(encoded)[0]
    assert restored == metadata
    assert hash(restored) == hash(metadata)
    assert {metadata: 'owner'}[restored] == 'owner'


def test_manifest_models_reject_field_mutation_including_nested_dependencies() -> None:
    metadata = ModMetadata(
        name='Example',
        dependencies=(Dependency(name='Required'),),
        optional_dependencies=(Dependency(name='Optional'),),
    )
    for target, field, value in (
        (metadata, 'name', 'Changed'),
        (metadata, 'dependencies', ()),
        (metadata.dependencies[0], 'name', 'Changed'),
        (metadata.optional_dependencies[0], 'name', 'Changed'),
    ):
        with pytest.raises(ValidationError) as error:
            setattr(target, field, value)
        assert error.value.errors()[0]['type'] == 'frozen_instance'


def test_manifest_does_not_borrow_mutable_dependency_input_lists() -> None:
    required = [{'name': 'Required'}]
    optional = [{'name': 'Optional'}]
    metadata = ModMetadata.model_validate(
        {'name': 'Example', 'dependencies': required, 'optional_dependencies': optional}
    )
    required[0]['name'] = 'Changed'
    optional.clear()
    assert metadata.dependencies == (Dependency(name='Required'),)
    assert metadata.optional_dependencies == (Dependency(name='Optional'),)
    changed = ModMetadata(name='Example', dependencies=(Dependency(name='Other'),))
    assert len({metadata: 'first', changed: 'second'}) == 2
    empty = ModMetadata(name='Example')
    assert empty.dependencies == empty.optional_dependencies == ()
    assert {empty: 'empty'}[ModMetadata(name='Example')] == 'empty'
