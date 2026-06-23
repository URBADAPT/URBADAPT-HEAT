from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ADDED_OK_COLUMNS = {"mean_intensity_citymask_degC", "citymean_citymask_degC"}
IGNORE_SUFFIXES = {".png", ".pdf", ".svg", ".jpg", ".jpeg", ".html", ".log", ".gpkg"}


def repo_root() -> Path:
    start = Path(__file__).resolve()
    for cand in [start, *start.parents]:
        if (cand / "cityheat").is_dir() and (cand / "notebooks").is_dir():
            return cand
    raise RuntimeError("Could not locate urban-heat repo root.")


def rel_files(root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    if not root.exists():
        return out
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() not in IGNORE_SUFFIXES:
            out[str(p.relative_to(root))] = p
    return out


def _close(a, b, rtol, atol) -> bool:
    a = np.asarray(a, dtype="float64")
    b = np.asarray(b, dtype="float64")
    if a.shape != b.shape:
        return False
    return bool(np.allclose(a, b, rtol=rtol, atol=atol, equal_nan=True))


def cmp_csv(fa: Path, fb: Path, rtol, atol) -> list[str]:
    msgs: list[str] = []
    try:
        da = pd.read_csv(fa)
        db = pd.read_csv(fb)
    except Exception as e:  # noqa: BLE001
        return [f"CSV read error: {e}"]
    added = [c for c in da.columns if c not in db.columns]
    dropped = [c for c in db.columns if c not in da.columns]
    for c in added:
        if c in ADDED_OK_COLUMNS:
            msgs.append(f"ADDED-COLUMN (ok): {c}")
        else:
            msgs.append(f"UNEXPECTED ADDED COLUMN: {c}")
    for c in dropped:
        msgs.append(f"MISSING COLUMN: {c}")
    if len(da) != len(db):
        msgs.append(f"ROW COUNT {len(da)} vs {len(db)}")
    common = [c for c in db.columns if c in da.columns]
    n = min(len(da), len(db))
    for c in common:
        sa, sb = da[c].iloc[:n], db[c].iloc[:n]
        if pd.api.types.is_numeric_dtype(sb) and pd.api.types.is_numeric_dtype(sa):
            if not _close(sa.to_numpy(), sb.to_numpy(), rtol, atol):
                diff = np.nanmax(np.abs(sa.to_numpy(dtype="float64") - sb.to_numpy(dtype="float64")))
                msgs.append(f"NUMERIC DIFF col={c} max|Δ|={diff:.6g}")
        else:
            if not sa.astype(str).equals(sb.astype(str)):
                msgs.append(f"NON-NUMERIC DIFF col={c}")
    return msgs


def cmp_npz(fa: Path, fb: Path, rtol, atol) -> list[str]:
    msgs: list[str] = []
    try:
        za = np.load(fa, allow_pickle=True)
        zb = np.load(fb, allow_pickle=True)
    except Exception as e:  # noqa: BLE001
        return [f"NPZ read error: {e}"]
    ka, kb = set(za.files), set(zb.files)
    for k in kb - ka:
        msgs.append(f"MISSING ARRAY: {k}")
    for k in ka - kb:
        msgs.append(f"EXTRA ARRAY: {k}")
    for k in ka & kb:
        try:
            if not _close(za[k], zb[k], rtol, atol):
                msgs.append(f"ARRAY DIFF: {k}")
        except Exception:  # noqa: BLE001
            if not np.array_equal(za[k], zb[k]):
                msgs.append(f"ARRAY (non-numeric) DIFF: {k}")
    return msgs


def _json_diff(a, b, path, rtol, atol, out):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in set(a) | set(b):
            if k not in a:
                out.append(f"JSON missing {path}/{k}")
            elif k not in b:
                out.append(f"JSON extra {path}/{k}")
            else:
                _json_diff(a[k], b[k], f"{path}/{k}", rtol, atol, out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"JSON len {path}: {len(a)} vs {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            _json_diff(x, y, f"{path}[{i}]", rtol, atol, out)
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if not math.isclose(float(a), float(b), rel_tol=rtol, abs_tol=atol):
            out.append(f"JSON num {path}: {a} vs {b}")
    elif a != b:
        out.append(f"JSON value {path}: {a!r} vs {b!r}")


def cmp_json(fa: Path, fb: Path, rtol, atol) -> list[str]:
    try:
        a = json.load(open(fa))
        b = json.load(open(fb))
    except Exception as e:  # noqa: BLE001
        return [f"JSON read error: {e}"]
    out: list[str] = []
    _json_diff(a, b, "", rtol, atol, out)
    return out


def cmp_h5(fa: Path, fb: Path, rtol, atol) -> list[str]:
    try:
        import h5py
    except Exception:
        return []  # h5py unavailable -> skip deep compare (existence already checked)
    msgs: list[str] = []
    try:
        with h5py.File(fa, "r") as ha, h5py.File(fb, "r") as hb:
            # CLIMADA stores intensity under .../intensity (data/indices/indptr). Compare sums cheaply.
            for key in ("intensity/data", "fraction/data"):
                if key in ha and key in hb:
                    va, vb = ha[key][()], hb[key][()]
                    if va.shape != vb.shape or not np.allclose(np.nansum(va), np.nansum(vb), rtol=rtol, atol=atol):
                        msgs.append(f"H5 {key}: shape/sum differ ({va.shape} vs {vb.shape})")
    except Exception as e:  # noqa: BLE001
        msgs.append(f"H5 read note: {e}")
    return msgs


def compare_city(new_root: Path, ref_root: Path, rtol, atol) -> tuple[int, int, list[str]]:
    fa, fb = rel_files(new_root), rel_files(ref_root)
    report: list[str] = []
    ok = bad = 0
    only_new = sorted(set(fa) - set(fb))
    only_ref = sorted(set(fb) - set(fa))
    for r in only_ref:
        report.append(f"  MISSING in agnostic: {r}")
        bad += 1
    for r in only_new:
        report.append(f"  EXTRA in agnostic:   {r}")
    for r in sorted(set(fa) & set(fb)):
        suf = Path(r).suffix.lower()
        if suf == ".csv":
            msgs = cmp_csv(fa[r], fb[r], rtol, atol)
        elif suf == ".npz":
            msgs = cmp_npz(fa[r], fb[r], rtol, atol)
        elif suf == ".json":
            msgs = cmp_json(fa[r], fb[r], rtol, atol)
        elif suf in (".h5", ".hdf5"):
            msgs = cmp_h5(fa[r], fb[r], rtol, atol)
        else:
            continue
        hard = [m for m in msgs if "ok)" not in m]
        if hard:
            bad += 1
            report.append(f"  DIFF {r}")
            for m in msgs:
                report.append(f"        - {m}")
        else:
            ok += 1
            if msgs:
                report.append(f"  OK   {r}  ({'; '.join(msgs)})")
    return ok, bad, report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cities", nargs="*", default=["rome", "athens", "lisbon", "copenhagen"])
    ap.add_argument("--variant-new", default="masselot_main_agnostic")
    ap.add_argument("--variant-ref", default="masselot_main")
    ap.add_argument("--rtol", type=float, default=1e-6)
    ap.add_argument("--atol", type=float, default=1e-8)
    args = ap.parse_args()
    root = repo_root()
    base = root / "outputs_variants"
    total_bad = 0
    for city in args.cities:
        new_root = base / args.variant_new / city
        ref_root = base / args.variant_ref / city
        print(f"\n=== {city}: {args.variant_new} vs {args.variant_ref} ===")
        if not ref_root.exists():
            print(f"  (no backup outputs at {ref_root} — skipped)")
            continue
        if not new_root.exists():
            print(f"  (no agnostic outputs at {new_root} — run the agnostic notebooks first)")
            continue
        ok, bad, report = compare_city(new_root, ref_root, args.rtol, args.atol)
        for line in report:
            print(line)
        print(f"  -> {ok} files match, {bad} files differ/missing")
        total_bad += bad
    print(f"\n{'PASS: agnostic outputs match the backup within tolerance.' if total_bad == 0 else f'FAIL: {total_bad} differing/missing file(s) — see above.'}")
    raise SystemExit(1 if total_bad else 0)


if __name__ == "__main__":
    main()
