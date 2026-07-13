#!/usr/bin/env python3
"""Batch-run the March2026_agnostic Masselot-main notebooks (NB01..08) over many cities.

Each city is driven by the ``CITY`` environment variable (the agnostic template uses
``os.environ.setdefault("CITY", ...)``, so an externally-set CITY wins). Notebooks are
chained via the interim/ directory on disk, so they are executed independently, in
order, each in a fresh ``urbanheat`` kernel. A city stops at its first failing notebook
(so NB05 is not attempted if NB03 failed), the failure is logged, and the batch
continues to the next city — the point is to surface what breaks across all 40.

Runs are written to ``runs/agnostic_batch/<city>/<NN>.ipynb`` (executed copies, kept for
inspection); the template notebooks are never modified. A live ``summary.json`` +
``summary.md`` are updated after every city.

Env/config prerequisites (already set up 2026-07-09):
  - configs/<slug>.yml present for every city (preview_configs copied in).
  - data_manifests/<slug>_gdrive.json present (NB01 gdown sync).
  - urbanheat jupyter kernel installed.

Usage (run WITH the urbanheat python so nbconvert + kernel match):
  /opt/anaconda3/envs/urbanheat/bin/python scripts/run_agnostic_batch.py --dry-run
  /opt/anaconda3/envs/urbanheat/bin/python scripts/run_agnostic_batch.py --cities milan
  /opt/anaconda3/envs/urbanheat/bin/python scripts/run_agnostic_batch.py            # all 40
  ... --skip-completed        # resume: skip cities with a DONE marker
  ... --notebooks 01 02 03 04 # only a subset of notebooks
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # urban-heat
TEMPLATE = ROOT / "notebooks" / "city_agnostic" / "March2026_agnostic" / "template"
RUNS = ROOT / "runs" / "agnostic_batch"
KERNEL = "urbanheat"
ALL_NB = ["01", "02", "03", "04", "05", "06", "07", "08"]


def nb_path(nn: str) -> Path | None:
    hits = sorted(glob.glob(str(TEMPLATE / f"{nn}_*.ipynb")))
    return Path(hits[0]) if hits else None


def all_cities() -> list[str]:
    return sorted(os.path.basename(f)[:-4] for f in glob.glob(str(ROOT / "calibration" / "preview_configs" / "*.yml")))


def first_error(nb_out: Path) -> str:
    try:
        d = json.load(open(nb_out))
    except Exception:  # noqa: BLE001
        return ""
    for c in d.get("cells", []):
        for o in c.get("outputs", []):
            if o.get("output_type") == "error":
                ev = str(o.get("evalue", "")).splitlines()
                return f"{o.get('ename')}: {ev[0] if ev else ''}"[:300]
    return ""


def run_nb(city: str, nn: str, timeout: int) -> tuple[bool, str]:
    src = nb_path(nn)
    if src is None:
        return False, f"template notebook {nn}_*.ipynb not found"
    outdir = RUNS / city
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"{nn}.ipynb"
    env = dict(os.environ, CITY=city)
    cmd = [
        sys.executable, "-m", "jupyter", "nbconvert", "--to", "notebook", "--execute",
        f"--ExecutePreprocessor.kernel_name={KERNEL}",
        f"--ExecutePreprocessor.timeout={timeout}",
        "--output", nn, "--output-dir", str(outdir),
        str(src),
    ]
    r = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True)
    if r.returncode == 0:
        return True, ""
    return False, first_error(out) or (r.stderr or "").strip()[-300:]


def write_summary(summary: list[dict]) -> None:
    (RUNS / "summary.json").write_text(json.dumps(summary, indent=2))
    lines = ["# Agnostic batch run summary", "", f"_updated {dt.datetime.now().isoformat(timespec='seconds')}_", "",
             "| city | reached | status | failing NB | error |", "|---|---|---|---|---|"]
    for s in summary:
        status = "DONE (01-08)" if s["failed_nb"] is None and s["last_ok"] == ALL_NB[-1] else (
            "partial" if s["failed_nb"] else "in-progress")
        lines.append(f"| {s['city']} | {s['last_ok'] or '-'} | {status} | {s['failed_nb'] or '-'} | {s['error'][:120].replace('|','/')} |")
    (RUNS / "summary.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cities", nargs="*", default=None, help="city slugs (default: all 40)")
    ap.add_argument("--notebooks", nargs="*", default=ALL_NB, help="notebook numbers (default 01..08)")
    ap.add_argument("--timeout", type=int, default=7200, help="per-cell timeout seconds")
    ap.add_argument("--skip-completed", action="store_true", help="skip cities with a DONE marker")
    ap.add_argument("--workers", type=int, default=1, help="number of cities to run concurrently")
    ap.add_argument("--dry-run", action="store_true", help="validate setup without executing")
    args = ap.parse_args()

    cities = args.cities or all_cities()
    RUNS.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        print(f"ROOT: {ROOT}")
        print(f"python: {sys.executable}")
        nbc = subprocess.run([sys.executable, "-m", "jupyter", "nbconvert", "--version"], capture_output=True, text=True)
        print(f"nbconvert: {nbc.stdout.strip() or nbc.stderr.strip()}  (rc={nbc.returncode})")
        ks = subprocess.run([sys.executable, "-m", "jupyter", "kernelspec", "list"], capture_output=True, text=True)
        print(f"kernel '{KERNEL}' available: {KERNEL in ks.stdout}")
        print(f"notebooks found: {[nn for nn in args.notebooks if nb_path(nn)]} / requested {args.notebooks}")
        miss_cfg = [c for c in cities if not (ROOT / 'configs' / f'{c}.yml').exists()]
        miss_man = [c for c in cities if not (ROOT / 'data_manifests' / f'{c}_gdrive.json').exists()]
        print(f"cities: {len(cities)}  | missing config: {miss_cfg or 'none'} | missing manifest: {miss_man or 'none'}")
        return 0

    def run_one_city(city: str) -> dict:
        st = {"city": city, "last_ok": None, "failed_nb": None, "error": ""}
        for nn in args.notebooks:
            t0 = dt.datetime.now()
            ok, err = run_nb(city, nn, args.timeout)
            secs = int((dt.datetime.now() - t0).total_seconds())
            print(f"  [{city:12}] NB{nn}: {'OK ' if ok else 'FAIL'} {secs:5d}s"
                  + ("" if ok else f"  -> {err}"), flush=True)
            if ok:
                st["last_ok"] = nn
            else:
                st["failed_nb"], st["error"] = nn, err
                break
        else:
            (RUNS / city / "DONE").write_text(dt.datetime.now().isoformat())
        return st

    todo = [c for c in cities if not (args.skip_completed and (RUNS / c / "DONE").exists())]
    skipped = [c for c in cities if c not in todo]
    if skipped:
        print(f"skipping {len(skipped)} already-DONE: {skipped}", flush=True)
    print(f"running {len(todo)} cities, {max(1, args.workers)} at a time, NB {args.notebooks}", flush=True)

    summary: list[dict] = []
    lock = threading.Lock()

    def record(st: dict) -> None:
        with lock:
            summary.append(st)
            write_summary(summary)
            done = sum(1 for s in summary if s["failed_nb"] is None)
            print(f"  === {len(summary)}/{len(todo)} cities finished ({done} full 01-08) ===", flush=True)

    if args.workers <= 1:
        for city in todo:
            record(run_one_city(city))
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = [ex.submit(run_one_city, c) for c in todo]
            for fut in as_completed(futures):
                record(fut.result())

    done = sum(1 for s in summary if s["failed_nb"] is None)
    print(f"\nBatch complete: {done}/{len(summary)} reached NB{args.notebooks[-1]}. See {RUNS/'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
