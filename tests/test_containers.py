import pytest
from berries.containers import CaseFoldDict, FrozenCaseFoldSet


class CaseFoldableKey:
    def __init__(self, value: str) -> None:
        self.value = value

    def casefold(self) -> str:
        return self.value.casefold()


def test_case_fold_dict_accepts_case_foldable_keys() -> None:
    values = CaseFoldDict[str]()
    key = CaseFoldableKey('Straße')

    values[key] = 'value'  # type: ignore[index]

    assert key in values
    assert values[key] == 'value'  # type: ignore[index]
    assert values.get(key) == 'value'  # type: ignore[arg-type]
    assert values.pop(key) == 'value'  # type: ignore[arg-type]


def test_case_fold_dict_handles_non_case_foldable_keys_as_mapping_keys() -> None:
    values = CaseFoldDict[str]()
    key = object()

    assert key not in values
    assert values.get(key, 'default') == 'default'  # type: ignore[arg-type]
    assert values.pop(key, 'default') == 'default'  # type: ignore[arg-type]
    with pytest.raises(KeyError):
        values[key]  # type: ignore[index]
    with pytest.raises(KeyError):
        del values[key]  # type: ignore[arg-type]
    with pytest.raises(KeyError):
        values.pop(key)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match='not case-foldable'):
        values[key] = 'value'  # type: ignore[index]


def test_case_fold_dict_pop_distinguishes_missing_default_from_none() -> None:
    values: CaseFoldDict[object] = CaseFoldDict()

    assert values.pop('missing', None) is None
    with pytest.raises(KeyError):
        values.pop('missing')


def test_frozen_case_fold_set() -> None:
    values = FrozenCaseFoldSet({'Straße', 'Everest'})

    assert 'STRASSE' in values
    assert 'everest' in values
    assert 'Celeste' not in values
    assert len(values) == 2
    assert repr(FrozenCaseFoldSet()) == 'FrozenCaseFoldSet()'
    assert repr(FrozenCaseFoldSet({'Everest'})) == "FrozenCaseFoldSet({'everest'})"
    assert repr(FrozenCaseFoldSet({'frozenset'})) == "FrozenCaseFoldSet({'frozenset'})"
    with pytest.raises(TypeError, match='not case-foldable'):
        FrozenCaseFoldSet([1])  # type: ignore[list-item]
