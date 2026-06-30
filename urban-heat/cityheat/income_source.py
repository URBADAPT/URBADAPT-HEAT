"""Income-source resolution for the AC downscaling step (NB05).

Two sources, selected by ``cfg['income']['source']``:

* ``observed`` (default) -- the city's measured sub-city income table.
* ``emulator``           -- the within-city income emulator's per-zone prediction
  (``income_emulator`` deploy_predict / income_index_predictions.csv).

THE correctness rule (learned the hard way): the income table must be keyed the
**same way the zones are matched** (``cfg['zones']['match_by']``).  NB05 builds
its ``zone_code`` with ``to_cap5`` for ``match_by='code'`` and ``norm_name`` for
``match_by in ('name', 'spatial_join')``.  If the income side is keyed
differently (e.g. ``to_cap5`` applied to a district *name* -> NaN), the join
silently produces zero matches and every zone collapses to the city mean -- a
flat income field with no AC gradient, and only an easy-to-miss print.

This module centralises that logic (so it is unit-tested, not duplicated across
five generated notebooks) and adds a **loud coverage assert** so a key mismatch
fails instead of degrading silently.

The ``to_cap5`` / ``norm_name`` helpers are byte-identical to the ones in NB05
cell-21, so observed-mode behaviour is unchanged.
"""
from __future__ import annotations

import re
import unicodedata

import numpy as np
import pandas as pd

# Danish (and a few Nordic) letters NB05 transliterates before ASCII-folding.
_DK_TRANSLIT = str.maketrans({"Ø": "O", "ø": "o", "Å": "A", "å": "a", "Æ": "AE", "æ": "ae"})


def norm_name(s) -> str:
    """Normalise a place name to a stable join key (NB05 cell-21 convention)."""
    s = "" if pd.isna(s) else str(s)
    s = s.translate(_DK_TRANSLIT)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip().replace("/", " ")
    s = re.sub(r"[-_]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s


def to_cap5(s):
    """Normalise a CAP/postal code to a 5-digit string, or NaN (NB05 convention)."""
    s = "" if pd.isna(s) else str(s)
    s = re.sub(r"\D", "", s)
    return np.nan if not s else s.zfill(5)[-5:]


def zone_key(values, match_by: str) -> pd.Series:
    """Build the join key exactly as NB05 builds its ``zone_code``.

    ``code`` -> ``to_cap5`` (numeric postal codes);
    ``name`` / ``spatial_join`` -> ``norm_name`` (text identity).
    """
    v = pd.Series(list(values))
    if match_by == "code":
        return v.map(to_cap5)
    return v.map(norm_name)


def resolve_income_inputs(cfg: dict) -> dict:
    """Resolve the effective income inputs from ``cfg['income']['source']``.

    Returns a spec dict; ``observed`` reproduces the prior config-driven inputs
    (so behaviour is unchanged), ``emulator`` points at the emulator CSV and
    forces ``aggregation='mean'`` / ``format='tabular'`` (the emulator index is
    already a per-zone mean, one row per zone -- never a total to divide by count).
    """
    inc = cfg.get("income", {}) or {}
    source = str(inc.get("source", "observed")).lower()

    if source == "emulator":
        emu = inc.get("emulator", {}) or {}
        if "csv" not in emu:
            raise ValueError("income.source=emulator requires income.emulator.csv")
        return {
            "source": "emulator",
            "csv": emu["csv"],
            "city": emu.get("city"),
            "city_aliases": emu.get("city_aliases") or [emu.get("city")],
            "columns": emu.get("columns", {
                "city": "city", "zone_id": "subcity_code",
                "income": "income_index_pred", "count": "pop_zone"}),
            "aggregation": emu.get("aggregation", "mean"),
            "format": "tabular",
        }

    return {
        "source": "observed",
        "csv": cfg.get("files", {}).get("income_csv"),
        "city": None,
        "city_aliases": inc.get("city_aliases"),
        "columns": inc.get("columns", {}),
        "aggregation": inc.get("aggregation", "mean"),
        "format": inc.get("format", "tabular"),
    }


def load_emulator_inc_agg(spec: dict, zone_match: str, zone_codes=None,
                          min_coverage: float = 0.95) -> pd.DataFrame:
    """Build ``inc_agg[zone_code, inc_mean, inc_w]`` from the emulator CSV.

    The income side is keyed with the SAME normaliser the zones use
    (``zone_key(.., zone_match)``), so it joins to NB05's ``zone_code``.  If
    ``zone_codes`` is given, the share of modelled zones that find a match is
    checked and an error is raised below ``min_coverage`` -- so a key-type
    mismatch (e.g. emulator codes vs name-matched zones) fails loudly instead of
    silently filling the city mean.
    """
    cols = spec["columns"]
    df = pd.read_csv(spec["csv"], dtype={cols["zone_id"]: str})

    aliases = [str(a).upper() for a in (spec.get("city_aliases") or [spec.get("city")]) if a is not None]
    if aliases:
        df = df[df[cols["city"]].astype(str).str.upper().isin(aliases)].copy()
    if df.empty:
        raise ValueError(f"emulator income: no rows for city aliases {aliases} in {spec['csv']}")

    df["zone_code"] = zone_key(df[cols["zone_id"]], zone_match)
    df = df.dropna(subset=["zone_code"])
    df["inc_mean"] = pd.to_numeric(df[cols["income"]], errors="coerce")
    df = df.dropna(subset=["inc_mean"])

    # aggregation is 'mean' for the emulator (one row/zone); group is a safety net.
    inc_agg = df.groupby("zone_code", as_index=False)["inc_mean"].mean()
    if inc_agg.empty:
        raise ValueError(
            f"emulator income produced 0 keyed zones under match_by='{zone_match}'. "
            f"The emulator subcity_code does not normalise to a usable key -- check "
            f"that its key TYPE (code vs name) matches the zones' match_by.")
    lo, hi = inc_agg["inc_mean"].quantile([0.01, 0.99])
    inc_agg["inc_w"] = inc_agg["inc_mean"].clip(lo, hi)

    if zone_codes is not None:
        zk = set(pd.Series(list(zone_codes)).dropna())
        matched = zk & set(inc_agg["zone_code"])
        cov = len(matched) / max(1, len(zk))
        if cov < min_coverage:
            raise ValueError(
                f"emulator income matched only {len(matched)}/{len(zk)} modelled zones "
                f"({cov:.0%} < min_coverage {min_coverage:.0%}) under match_by='{zone_match}'. "
                f"Check that the emulator boundary keys align with the AC zone keys "
                f"(same identifier type), or lower min_coverage deliberately.")

    return inc_agg
