from fastapi import FastAPI
from pydantic import BaseModel
from typing import List
import torch

app = FastAPI(title="Price Prediction AI Service")


class PredictionRequest(BaseModel):
    features: List[float]


class PredictionResponse(BaseModel):
    prediction: float
    feature_count: int


@app.post("/predict", response_model=PredictionResponse)
def predict(request: PredictionRequest):
    # Guard: return default immediately when features list is empty
    if not request.features:
        return PredictionResponse(prediction=0.0, feature_count=0)

    # Convert input features to a PyTorch tensor
    tensor = torch.tensor(request.features, dtype=torch.float32)

    prediction = float(tensor.sum()) / len(request.features)

    return PredictionResponse(
        prediction=prediction,
        feature_count=len(request.features),
    )
