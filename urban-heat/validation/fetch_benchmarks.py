"""Download independent national heat-mortality benchmarks and reduce them to annual totals.

The modelled baseline is a *climatology* (t2m_baseline_mode_default: climatology_mean
over UrbClim 2008-2017), so the right comparator is a multi-year mean of an independent
series, not a single observed year. This script fetches two such series and writes
tidy annual totals to validation/benchmarks/.

Sources, and why only these two:

  spf  - Sante publique France, "Deces attribuables a la chaleur pendant l'ete et
         pendant les vagues de chaleur (Departement)", via the Odisse open-data API.
         Departement (NUTS-3) resolution, 2014-2025, MMT-referenced attributable
         fractions. Methodologically like-for-like with Masselot and with us.

  momo - Instituto de Salud Carlos III MoMo, daily provincial deaths attributable to
         temperature excess. Province (NUTS-3) resolution. MoMo's expected baseline is
         fitted on historical mortality, so its counterfactual is a seasonal norm rather
         than the minimum-mortality temperature: it is CONSERVATIVE and systematically
         below MMT-referenced estimates. Compare ratios, not levels (compare_baseline.py
         carries a Masselot/MoMo column for exactly this reason).

Deliberately NOT included:

  Italy SISMG and EuroMOMO publish excess over a recent-years seasonal norm. That
  baseline already absorbs a typical summer's heat, so their "excess" measures the
  anomaly on top of heat, not heat itself. SISMG reported summer 2025 at -4% vs
  expected against a +1.72 C anomaly, which is only coherent under that reading.
  These cannot validate an MMT-referenced burden and are excluded on purpose.

  Ballester/Gallo/Janos (Nature Medicine 2023/2024/2025) would cover all 40 cities at
  NUTS-3, but publish code only - no per-region estimate table. Using them means
  re-running their pipeline on Eurostat weekly deaths.

Raw payloads land in validation/cache/ (gitignored; the MoMo export is ~720 MB). Only the
small annual aggregates in validation/benchmarks/ are tracked.

Usage:
    python validation/fetch_benchmarks.py                 # all sources, use cache
    python validation/fetch_benchmarks.py --source spf
    python validation/fetch_benchmarks.py --refresh       # re-download even if cached

Requires: pandas, requests
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import pandas as pd
import requests
from urllib3.exceptions import ProtocolError

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
OUT = HERE / "benchmarks"

SPF_DATASET = (
    "canicules-deces-attribuables-a-la-chaleur-pendant-l-ete-"
    "et-pendant-les-vagues-de-chaleur-departement"
)
SPF_URL = (
    f"https://odisse.santepubliquefrance.fr/api/explore/v2.1/catalog/datasets/"
    f"{SPF_DATASET}/exports/json?limit=-1"
)
MOMO_URL = "https://momo.isciii.es/public/momo/data"

EUROSTAT_POP = (
    "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
    "demo_r_pjanaggr3?format=JSON&sex=T&age=TOTAL&time={year}"
)

# Only the geographies referenced in reference_geographies.csv are kept. MoMo ships every
# Spanish province at daily resolution for every sex x age cell, so filtering during the
# stream keeps a ~720 MB download from becoming a file we have to re-parse.
SPF_DEPARTEMENTS = {"75", "69", "13", "44"}
MOMO_PROVINCES = {"M", "B", "SE"}

# Population year used to turn absolute deaths into rates. Chosen mid-period for each
# source so the denominator is representative of the years being averaged.
POP_YEAR = {"spf": 2018, "momo": 2017}


def _get(url: str, dest: Path, refresh: bool, stream: bool = False) -> Path:
    """Download `url` to `dest` unless it is already cached."""
    if dest.exists() and dest.stat().st_size > 0 and not refresh:
        print(f"  [cache] {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  [get]   {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=(30, 900)) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
    tmp.replace(dest)
    print(f"  [ok]    {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def fetch_spf(refresh: bool) -> pd.DataFrame:
    """Annual heat-attributable deaths for the four French departements we need."""
    raw = _get(SPF_URL, CACHE / "spf_departement.json", refresh)
    rows = json.loads(raw.read_text(encoding="utf-8"))
    d = pd.DataFrame(rows)
    d = d[(d.classe_age == "Tous Ages") & (d.departements.isin(SPF_DEPARTEMENTS))]
    out = (
        d.assign(year=d.annee.astype(int))
        .rename(
            columns={
                "departements": "geo_code",
                "libgeo": "geo_name",
                "dc_chaleur": "deaths",
                "dc_chaleur_inf": "deaths_lo",
                "dc_chaleur_sup": "deaths_hi",
            }
        )[["geo_code", "geo_name", "year", "deaths", "deaths_lo", "deaths_hi"]]
        .sort_values(["geo_code", "year"])
    )
    out.insert(0, "source", "spf")
    return out


def _stream_momo(dest: Path) -> tuple[int, bool]:
    """Stream the MoMo export into `dest`, keeping only the rows we need.

    Returns (rows kept, completed). The upstream export is ~720 MB, the server does not
    honour range requests, and the connection is routinely dropped partway. Rows arrive
    in date order, so a truncated read is still a *complete* series up to its cutoff -
    we therefore keep what arrived rather than discarding the transfer, and let the
    whole-year filter in fetch_momo() drop the partial tail year.
    """
    kept = 0
    completed = False
    # A fresh Session per attempt, with keep-alive disabled. Wrapping `r.raw` in a
    # TextIOWrapper instead hands the wrapper ownership of the socket: once an attempt
    # is interrupted the wrapper closes it on collection, and urllib3 then hands the
    # same dead connection to the next attempt, which fails with a bare ValueError
    # ("I/O operation on closed file") rather than a retryable network error.
    with requests.Session() as session:
        session.headers["Connection"] = "close"
        with session.get(MOMO_URL, stream=True, timeout=(30, 1800)) as r:
            r.raise_for_status()
            lines = r.iter_lines(chunk_size=1 << 20, decode_unicode=True)
            reader = csv.reader(lines)
            header = next(reader)
            with open(dest, "w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(header)
                i = {c: n for n, c in enumerate(header)}
                try:
                    for row in reader:
                        if (
                            row[i["ambito"]] == "provincia"
                            and row[i["cod_ambito"]] in MOMO_PROVINCES
                            and row[i["nombre_sexo"]] == "todos"
                            and row[i["nombre_gedad"]] == "todos"
                        ):
                            w.writerow(row)
                            kept += 1
                    completed = True
                except (
                    requests.exceptions.RequestException,
                    ProtocolError,
                    ValueError,
                    OSError,
                ) as exc:
                    print(f"  [warn]  transfer interrupted after {kept:,} kept rows: "
                          f"{type(exc).__name__}")
    return kept, completed


def fetch_momo(refresh: bool, retries: int = 3) -> pd.DataFrame:
    """Annual deaths attributable to temperature excess for three Spanish provinces."""
    filtered = CACHE / "momo_provinces.csv"
    if not filtered.exists() or refresh:
        print(f"  [get]   {MOMO_URL} (streaming filter, ~720 MB upstream)")
        filtered.parent.mkdir(parents=True, exist_ok=True)
        best_rows = -1
        for attempt in range(1, retries + 1):
            tmp = filtered.with_suffix(f".part{attempt}")
            kept, completed = _stream_momo(tmp)
            # Keep the longest transfer seen: each attempt restarts from the beginning,
            # so more rows always means more calendar coverage.
            if kept > best_rows:
                best_rows = kept
                tmp.replace(filtered)
            else:
                tmp.unlink(missing_ok=True)
            if completed:
                print(f"  [ok]    {filtered.name} ({kept:,} rows, complete)")
                break
            if attempt < retries:
                print(f"  [retry] attempt {attempt + 1}/{retries}")
        else:
            print(f"  [ok]    {filtered.name} ({best_rows:,} rows, PARTIAL - "
                  "series truncated, later years will be missing)")
    else:
        print(f"  [cache] {filtered.name}")

    d = pd.read_csv(filtered)
    d["year"] = pd.to_datetime(d.fecha_defuncion).dt.year
    # Keep only years with full coverage: the upstream file is a rolling export and its
    # last year is partial, which would silently drag an annual mean down.
    days = d.groupby("year").fecha_defuncion.nunique()
    complete = days[days >= 365].index
    dropped = sorted(set(days.index) - set(complete))
    if dropped:
        print(f"  [note]  dropping partial years {dropped}")
    d = d[d.year.isin(complete)]

    out = (
        d.groupby(["cod_ambito", "nombre_ambito", "year"], as_index=False)
        .defunciones_atrib_exc_temp.sum()
        .rename(
            columns={
                "cod_ambito": "geo_code",
                "nombre_ambito": "geo_name",
                "defunciones_atrib_exc_temp": "deaths",
            }
        )
    )
    out["deaths_lo"] = pd.NA
    out["deaths_hi"] = pd.NA
    out.insert(0, "source", "momo")
    return out.sort_values(["geo_code", "year"])


def fetch_populations(nuts3: list[str], year: int, refresh: bool) -> pd.DataFrame:
    """NUTS-3 total population from Eurostat, used as the rate denominator."""
    dest = CACHE / f"eurostat_pop_{year}.json"
    url = EUROSTAT_POP.format(year=year) + "".join(f"&geo={g}" for g in sorted(nuts3))
    raw = _get(url, dest, refresh)
    js = json.loads(raw.read_text(encoding="utf-8"))
    idx = js["dimension"]["geo"]["category"]["index"]
    pos_to_geo = {v: k for k, v in idx.items()}
    rows = [
        {"nuts3": pos_to_geo[int(pos)], "pop_year": year, "population": val}
        for pos, val in js["value"].items()
    ]
    missing = sorted(set(nuts3) - {r["nuts3"] for r in rows})
    if missing:
        print(f"  [warn]  no Eurostat population for {missing} in {year}")
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", choices=["spf", "momo", "all"], default="all")
    ap.add_argument("--refresh", action="store_true", help="re-download even if cached")
    ap.add_argument("--retries", type=int, default=3,
                    help="MoMo transfer attempts; the longest partial is kept")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    geo = pd.read_csv(HERE / "reference_geographies.csv", dtype=str)
    geo = geo[geo.source.notna()]

    want = ["spf", "momo"] if args.source == "all" else [args.source]
    for src in want:
        print(f"[{src}]")
        frame = (fetch_spf(args.refresh) if src == "spf"
                 else fetch_momo(args.refresh, args.retries))

        sub = geo[geo.source == src]
        pops = fetch_populations(sub.nuts3.tolist(), POP_YEAR[src], args.refresh)
        frame = frame.merge(
            sub[["slug", "source_geo_code", "nuts3"]].rename(
                columns={"source_geo_code": "geo_code"}
            ),
            on="geo_code",
            how="left",
        ).merge(pops, on="nuts3", how="left")

        dest = OUT / f"{src}_annual.csv"
        frame.to_csv(dest, index=False)
        span = f"{frame.year.min()}-{frame.year.max()}"
        print(f"  [write] {dest.relative_to(HERE.parent)}  {len(frame)} rows, {span}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
