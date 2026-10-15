"""The NB08 portfolio solver against brute force, and on the Rome case that exposed the grid."""
from __future__ import annotations

import itertools

import numpy as np

from cityheat.portfolio import efficient_frontier, max_benefit_mix, min_cost_per_death_mix

# Rome, GMD pilot tree: full-scale PV cost (EUR) and 25-year avoided deaths.
ROME_COST = np.array([294.78e6, 1297.67e6, 25.087e6])   # trees, AC, EWS
ROME_BEN = np.array([122.99, 511.71, 648.10])


def _grid(n, step):
    g = np.round(np.arange(0.0, 1.0 + 1e-9, step), 10)
    return np.array(list(itertools.product(g, repeat=n)))


def test_rome_trees_stay_fully_funded_across_the_budget_sweep():
    # The 0.04 grid reported trees at 72, 92, 60, 96, 64, 84 % across these budgets.
    for budget in np.linspace(500e6, 2000e6, 8):
        s = max_benefit_mix(ROME_COST, ROME_BEN, budget)
        assert s[0] == 1.0 and s[2] == 1.0
        assert ROME_COST @ s <= budget * (1 + 1e-12)
        assert s[1] == min(1.0, (budget - ROME_COST[0] - ROME_COST[2]) / ROME_COST[1])


def test_max_benefit_beats_every_grid_point():
    rng = np.random.default_rng(0)
    shares = _grid(3, 0.05)
    for _ in range(200):
        c = rng.uniform(1, 100, 3)
        b = rng.uniform(-5, 50, 3)
        budget = rng.uniform(0, c.sum() * 1.2)
        lo = rng.choice([0.0, 0.2], 3)
        hi = rng.choice([1.0, 0.8], 3)
        s = max_benefit_mix(c, b, budget, lower=lo, upper=hi)
        ok = (shares @ c <= budget) & np.all(shares >= lo - 1e-12, axis=1) & np.all(shares <= hi + 1e-12, axis=1)
        if s is None:
            assert lo @ c > budget
            continue
        assert c @ s <= budget * (1 + 1e-9) + 1e-9
        assert np.all(s >= lo - 1e-12) and np.all(s <= hi + 1e-12)
        if ok.any():
            assert b @ s >= (shares[ok] @ b).max() - 1e-9


def test_min_cost_per_death_beats_every_grid_point():
    rng = np.random.default_rng(1)
    shares = _grid(3, 0.05)
    for _ in range(200):
        c = rng.uniform(1, 100, 3)
        b = rng.uniform(1, 50, 3)
        budget = rng.uniform(c.min(), c.sum() * 1.2)
        floor = 0.5 * (b @ max_benefit_mix(c, b, budget))
        s = min_cost_per_death_mix(c, b, budget, floor)
        assert s is not None
        assert c @ s <= budget * (1 + 1e-9) and b @ s >= floor * (1 - 1e-9)
        ben = shares @ b
        ok = (shares @ c <= budget) & (ben >= floor) & (ben > 0)
        if ok.any():
            assert (c @ s) / (b @ s) <= ((shares[ok] @ c) / ben[ok]).min() + 1e-9


def test_min_cost_per_death_ties_resolve_to_the_largest_mix():
    # Every scale of EWS alone has the same cost per death; the floor is met inside it.
    s = min_cost_per_death_mix(ROME_COST, ROME_BEN, 1.5e9, 0.5 * 1282.8)
    np.testing.assert_allclose(s, [0.0, 0.0, 1.0])
    # A floor beyond EWS adds exactly enough of the next-cheapest policy (trees).
    s = min_cost_per_death_mix(ROME_COST, ROME_BEN, 1.5e9, 700.0)
    np.testing.assert_allclose(s, [(700.0 - 648.10) / 122.99, 0.0, 1.0])
    assert min_cost_per_death_mix(ROME_COST, ROME_BEN, 1.5e9, 5000.0) is None


def test_frontier_adds_policies_cheapest_per_death_first():
    v = efficient_frontier(ROME_COST, ROME_BEN)
    np.testing.assert_array_equal(np.array(v), [[0, 0, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1]])
    # A policy that avoids no deaths is never on the frontier.
    v = efficient_frontier(ROME_COST, [122.99, -3.0, 648.10])
    np.testing.assert_array_equal(np.array(v), [[0, 0, 0], [0, 0, 1], [1, 0, 1]])


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"ALL {len(fns)} TESTS PASSED")
