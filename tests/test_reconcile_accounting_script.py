import argparse

import pytest

from scripts.reconcile_accounting_v2 import _expected_hold


@pytest.mark.parametrize("value", ["abc", "", "0", "-1", "NaN", "1e999", "0.0000000000000000001"])
def test_expected_grant_hold_rejects_invalid_amounts_without_a_traceback(value):
    with pytest.raises(argparse.ArgumentTypeError):
        _expected_hold(value)


def test_expected_grant_hold_keeps_the_exact_operator_value():
    assert _expected_hold("1.250000000000000001") == "1.250000000000000001"
