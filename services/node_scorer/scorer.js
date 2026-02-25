'use strict';

/**
 * node-scorer-svc — Computes a weighted relevance score for price items.
 *
 * BUG: When items array is empty, `total / items.length` = 0/0 = NaN.
 * The function returns { score: NaN } instead of a safe default.
 * This is NOT handled and propagates silently through the pipeline.
 */
function scoreItems(items) {
  if (items.length === 0) return { score: 0, count: 0 };

  let total = 0;
  for (const item of items) {
    total += item.value * item.weight;
  }

  const avg = total / items.length;

  return { score: avg, count: items.length };
}

module.exports = { scoreItems };
