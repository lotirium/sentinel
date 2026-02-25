from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_valid_input():
    """Happy path: a normal list of floats should return 200 and the correct mean."""
    response = client.post("/predict", json={"features": [10.0, 20.0]})
    assert response.status_code == 200
    data = response.json()
    assert data["prediction"] == 15.0
    assert data["feature_count"] == 2


def test_empty_input():
    """
    Sends an empty feature list.
    EXPECTED (desired): 200 OK with a sensible default prediction.
    ACTUAL (buggy):     500 Internal Server Error due to unhandled RuntimeError
                        raised by torch.mean() on an empty tensor.
    This test WILL FAIL, exposing the bug.
    """
    response = client.post("/predict", json={"features": []})
    # This assertion fails because the server crashes with 500
    assert response.status_code == 200
