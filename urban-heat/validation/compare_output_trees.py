"""Compare a rerun output tree with the reference (paper) tree, city by city.

usage: python compare_tagcheck.py REF_ROOT NEW_ROOT city [city ...]

For every CSV/JSON under <city>/tables and <city>/interim in REF (uncertainty_runs excluded):
  identical   byte-identical
  numeric-eq  same structure, every numeric value within rel 1e-9 (abs 1e-12), text equal
  differs     anything else; the largest relative numeric differences are listed
  missing     present in REF, absent in NEW
Text fields that legitimately change between runs (timestamps, paths, git/commit, hostnames,
durations) are reported separately and do not make a file 'differs'.
"""
import csv, hashlib, io, json, math, re, sys
from pathlib import Path

VOLATILE = re.compile(r"(time|date|stamp|path|dir|file|git|commit|sha|host|user|elapsed|duration|runtime|created|generated|version|cwd|variant)", re.I)
REL, ABS = 1e-9, 1e-12


def num(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        try:
            return float(x)
        except ValueError:
            return None
    return None


def flat_json(o, p=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from flat_json(v, f"{p}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from flat_json(v, f"{p}[{i}]")
    else:
        yield p, o


def flat_csv(text):
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return
    head = rows[0]
    for i, r in enumerate(rows[1:]):
        for j, v in enumerate(r):
            yield f"r{i}.{head[j] if j < len(head) else j}", v
    yield "__shape__", f"{len(rows)}x{len(head)}"


def compare(a: Path, b: Path):
    ta, tb = a.read_bytes(), b.read_bytes()
    if hashlib.sha256(ta).digest() == hashlib.sha256(tb).digest():
        return "identical", [], []
    try:
        if a.suffix == ".json":
            fa, fb = dict(flat_json(json.loads(ta))), dict(flat_json(json.loads(tb)))
        else:
            fa, fb = dict(flat_csv(ta.decode("utf-8", "replace"))), dict(flat_csv(tb.decode("utf-8", "replace")))
    except Exception as e:
        return "differs", [("parse", str(e))], []
    diffs, volatile = [], []
    for k in sorted(set(fa) | set(fb)):
        va, vb = fa.get(k, "<absent>"), fb.get(k, "<absent>")
        if va == vb:
            continue
        na, nb = num(va), num(vb)
        if na is not None and nb is not None:
            if math.isnan(na) and math.isnan(nb):
                continue
            d = abs(na - nb)
            rel = d / max(abs(na), abs(nb), 1e-300)
            if d <= ABS or rel <= REL:
                continue
            diffs.append((k, f"{va} -> {vb} (rel {rel:.2e})", rel))
        elif VOLATILE.search(k):
            volatile.append(k)
        else:
            diffs.append((k, f"{str(va)[:60]!r} -> {str(vb)[:60]!r}", math.inf))
    status = "numeric-eq" if not diffs else "differs"
    return status, sorted(diffs, key=lambda t: -t[2])[:5], volatile


def main():
    ref, new, cities = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
    total = {}
    for c in cities:
        print(f"\n######## {c}")
        for sub in ("tables", "interim"):
            for fa in sorted((ref / c / sub).glob("*")):
                if fa.suffix not in (".csv", ".json") or not fa.is_file():
                    continue
                fb = new / c / sub / fa.name
                if not fb.exists():
                    st, top, vol = "missing", [], []
                else:
                    st, top, vol = compare(fa, fb)
                total[st] = total.get(st, 0) + 1
                if st in ("identical",):
                    continue
                line = f"  [{st:10s}] {sub}/{fa.name}"
                if vol:
                    line += f"   (volatile fields: {', '.join(v.split('.')[-1] for v in vol[:4])})"
                print(line)
                for k, msg, _ in top:
                    print(f"        {k}: {msg}")
    print("\nSUMMARY", total)


if __name__ == "__main__":
    main()
