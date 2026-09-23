import pytest

from pist.containers import CaseFoldDict, FrozenCaseFoldSet


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
