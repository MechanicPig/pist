import pytest

from pist.containers import CaseFoldDict


def test_case_fold_dict_pop_distinguishes_missing_default_from_none() -> None:
    values: CaseFoldDict[object] = CaseFoldDict()

    assert values.pop('missing', None) is None
    with pytest.raises(KeyError):
        values.pop('missing')
