import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.data_pipeline import process_batch


def test_normal_records():
    """Records with a 'price' key should be processed correctly."""
    records = [{"price": 100.0}, {"price": 200.0}]
    out = process_batch(records)
    assert len(out) == 2
    assert abs(out[0]["processed_price"] - 110.0) < 1e-4
    assert abs(out[1]["processed_price"] - 220.0) < 1e-4


def test_missing_price_field():
    """
    Records without a 'price' key should default to 0.0, not raise KeyError.
    EXPECTED: [{"processed_price": 0.0}]
    ACTUAL (buggy): KeyError: 'price'
    This test WILL FAIL, exposing the missing-key bug.
    """
    records = [{"amount": 99.0}]
    out = process_batch(records)
    assert len(out) == 1
    assert out[0]["processed_price"] == 0.0
