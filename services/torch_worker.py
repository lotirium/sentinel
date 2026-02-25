"""
torch-inference-worker: similarity scoring between a query vector and key vectors.
"""
import torch


def compute_similarity(query: list, keys: list) -> float:
    """
    Compute dot-product similarity between a query vector and a keys vector.

    BUG: keys tensor is created with dtype=torch.int32.
    torch.mm requires both tensors to have the same floating-point dtype.
    This raises: RuntimeError: expected scalar type Float but found Int
    """
    q = torch.tensor([query], dtype=torch.float32)
    k = torch.tensor([keys], dtype=torch.float32)
    result = torch.mm(q, k.T)
    return float(result[0][0])
