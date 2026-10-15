"""Exact budget-constrained mixes of linearly scalable adaptation policies (NB08).

NB08 treats each full policy (trees, AC, EWS) as a unit whose present-value cost and
avoided deaths scale linearly with the share deployed, ``0 <= s_j <= 1``. Under that
model the benefit-maximising mix is a fractional knapsack, solved exactly by funding the
policies in order of avoided deaths per euro until the budget runs out.

The grid search this replaces got that wrong whenever two policies had similar deaths per
euro (trees and AC in Rome: 2.40 vs 2.54 M EUR per death): the winning grid point was the
one that happened to fill the budget most tightly, so the reported shares jumped between
neighbouring budgets although the benefit barely changed.

Costs are taken to be non-negative. A policy that avoids no deaths is never funded unless
a lower bound forces it.
"""
from __future__ import annotations

import numpy as np


def _arrays(costs, benefits, lower=None, upper=None):
    c = np.asarray(costs, dtype=float)
    b = np.asarray(benefits, dtype=float)
    lo = np.zeros_like(c) if lower is None else np.asarray(lower, dtype=float)
    hi = np.ones_like(c) if upper is None else np.asarray(upper, dtype=float)
    return c, b, lo, hi


def _by_cost_per_death(c, b, idx):
    """Indices with positive benefit, cheapest per avoided death first (stable on ties)."""
    keep = [i for i in idx if b[i] > 0]
    return sorted(keep, key=lambda i: c[i] / b[i])


def max_benefit_mix(costs, benefits, budget, lower=None, upper=None):
    """Shares in ``[lower, upper]`` maximising ``benefits @ s`` subject to ``costs @ s <= budget``.

    Returns None when the lower bounds alone cost more than the budget.
    """
    c, b, lo, hi = _arrays(costs, benefits, lower, upper)
    s = lo.copy()
    left = float(budget) - float(c @ s)
    if left < -1e-9 * max(abs(float(budget)), 1.0):
        return None
    for i in _by_cost_per_death(c, b, range(len(c))):
        room = hi[i] - s[i]
        take = room if c[i] <= 0 else min(room, max(left, 0.0) / c[i])
        s[i] += take
        left -= take * c[i]
    return s


def min_cost_per_death_mix(costs, benefits, budget, min_benefit, upper=None):
    """Shares minimising ``(costs @ s) / (benefits @ s)`` subject to ``benefits @ s >= min_benefit``.

    Along the cheapest-per-death funding order the average cost per death can only rise
    once a costlier policy enters, so the minimum sits where the benefit floor is first met.
    The one exception is a floor met inside the first (cheapest) policy: every scale of it
    then has the same cost per death, and the largest affordable scale is returned -- the
    most deaths avoided at the best attainable cost per death. Policies tied with it on cost
    per death are funded alongside it.

    Returns None when no affordable mix reaches ``min_benefit``.
    """
    c, b, _, hi = _arrays(costs, benefits, upper=upper)
    order = _by_cost_per_death(c, b, range(len(c)))
    s = np.zeros_like(c)
    if not order:
        return s if min_benefit <= 0 else None
    best = c[order[0]] / b[order[0]]
    left, got = float(budget), 0.0
    for i in order:
        tied = np.isclose(c[i] / b[i], best, rtol=1e-12, atol=0.0)
        room = hi[i] - s[i]
        if c[i] > 0:
            room = min(room, max(left, 0.0) / c[i])
        if not tied:
            if got >= min_benefit:
                break
            room = min(room, (min_benefit - got) / b[i])
        s[i] += room
        left -= room * c[i]
        got += room * b[i]
    if got < min_benefit * (1 - 1e-12):
        return None
    return s


def efficient_frontier(costs, benefits, upper=None):
    """Vertices of the cost-benefit Pareto frontier, as a list of share arrays.

    With linear costs and benefits the frontier is the path the benefit-maximising mix
    traces as the budget rises from zero: it starts at no policy and adds each policy in
    full, cheapest per avoided death first. Points between two vertices are efficient too.
    """
    c, b, _, hi = _arrays(costs, benefits, upper=upper)
    s = np.zeros_like(c)
    vertices = [s.copy()]
    for i in _by_cost_per_death(c, b, range(len(c))):
        if hi[i] <= 0:
            continue
        s[i] = hi[i]
        vertices.append(s.copy())
    return vertices
