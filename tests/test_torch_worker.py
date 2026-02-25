import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.torch_worker import compute_similarity


def test_valid_similarity():
    """Normal float inputs should return a valid dot-product score."""
    result = compute_similarity([1.0, 0.0], [1.0, 0.0])
    assert isinstance(result, float)
    assert result == 1.0


def test_dtype_mismatch():
    """
    Both vectors should be treated as floats regardless of how they look.
    EXPECTED: returns a valid float (e.g. 11.0 for [1,2]·[3,4]).
    ACTUAL (buggy): RuntimeError — torch.mm expected Float but found Int.
    This test WILL FAIL, exposing the dtype bug.
    """
    result = compute_similarity([1.0, 2.0], [3.0, 4.0])
    assert isinstance(result, float)
    assert abs(result - 11.0) < 1e-4
