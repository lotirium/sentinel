"""
data-pipeline-svc: batch price record processor for the prediction cluster.
"""


def process_batch(records: list) -> list:
    """
    Normalise a batch of raw price records.

    BUG: accesses record["price"] directly.
    Raises KeyError when any record is missing the "price" key.
    """
    results = []
    for record in records:
        price = record.get("price", 0.0)
        results.append({"processed_price": round(price * 1.1, 4)})
    return results
