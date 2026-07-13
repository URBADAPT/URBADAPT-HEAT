#!/usr/bin/env python3
"""Wire per-city PROVIDE climate-delta CSVs (T2MmeanDeltas) into city manifests.

Unlike the DRMKC/GVI vulnerability tables (single all-country files, one shared Drive
ID), the climate deltas are city-specific: each city has its own
``climate_change_provide_markups_bands.csv`` + ``_gcm.csv`` with its own Drive IDs.

Reads a JSON mapping ``{slug: [bands_id, gcm_id]}`` (gcm_id optional) and inserts (or
replaces) these two entries per manifest:
    T2MmeanDeltas/climate_change_provide_markups_bands.csv   (read by NB02)
    T2MmeanDeltas/climate_change_provide_markups_gcm.csv     (read by NB09)
Formatting is preserved (targeted text edit). Idempotent.

Usage:
    python scripts/wire_t2mdeltas_manifests.py --ids-json /path/to/t2m_ids.json
    python scripts/wire_t2mdeltas_manifests.py --ids-json ... --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFESTS = HERE.parent / "data_manifests"

BANDS_DEST = "T2MmeanDeltas/climate_change_provide_markups_bands.csv"
GCM_DEST = "T2MmeanDeltas/climate_change_provide_markups_gcm.csv"
ANCHOR_DEST = "vulnerability/ESTAT_OBS-VALUE-Y_1564_2021_V2.tiff"


def _set_or_flag(text: str, dest: str, new_id: str) -> tuple[str, bool]:
    pat = re.compile(r'\{[^{}]*"dest"\s*:\s*"' + re.escape(dest) + r'"[^{}]*\}')
    m = pat.search(text)
    if not m:
        return text, False
    entry = re.sub(r'"id"\s*:\s*"[^"]*"', f'"id": "{new_id}"', m.group(0))
    return text[: m.start()] + entry + text[m.end() :], True


def _fmt_entry(new_id: str, dest: str, multiline: bool, indent: str) -> str:
    if not multiline:
        return f'{{ "type": "file", "id": "{new_id}", "dest": "{dest}" }},'
    fi = indent + "  "
    return ("{\n" + f'{fi}"type": "file",\n' + f'{fi}"id": "{new_id}",\n'
            + f'{fi}"dest": "{dest}"\n' + indent + "},")


def _insert_entries(text: str, entries: list[tuple[str, str]]) -> str:
    apat = re.compile(r'\{[^{}]*"dest"\s*:\s*"' + re.escape(ANCHOR_DEST) + r'"[^{}]*\}')
    m = apat.search(text)
    if not m:
        raise RuntimeError(f"anchor entry ({ANCHOR_DEST}) not found")
    multiline = "\n" in m.group(0)
    line_start = text.rfind("\n", 0, m.start()) + 1
    indent = text[line_start : m.start()]
    ins = m.end()
    if ins < len(text) and text[ins] == ",":
        ins += 1
    block = "".join("\n" + indent + _fmt_entry(nid, dest, multiline, indent) for dest, nid in entries)
    return text[:ins] + block + text[ins:]


def process(path: Path, bands_id: str, gcm_id: str | None, dry_run: bool) -> str:
    text = path.read_text()
    actions, to_insert = [], []
    pairs = [(BANDS_DEST, bands_id)]
    if gcm_id:
        pairs.append((GCM_DEST, gcm_id))
    for dest, nid in pairs:
        text, replaced = _set_or_flag(text, dest, nid)
        if replaced:
            actions.append(f"replaced[{dest.split('/')[-1]}]")
        else:
            to_insert.append((dest, nid))
            actions.append(f"inserted[{dest.split('/')[-1]}]")
    if to_insert:
        text = _insert_entries(text, to_insert)
    json.loads(text)  # validate
    if not dry_run:
        path.write_text(text)
    return ", ".join(actions)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ids-json", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    mapping = json.load(open(args.ids_json))
    print(f"{'[DRY-RUN] ' if args.dry_run else ''}wiring {len(mapping)} city manifests")
    rc = 0
    for slug, ids in sorted(mapping.items()):
        man = MANIFESTS / f"{slug}_gdrive.json"
        if not man.exists():
            print(f"  {slug:14} ERROR: manifest not found ({man.name})")
            rc = 1
            continue
        bands_id = ids[0] if ids else None
        gcm_id = ids[1] if len(ids) > 1 else None
        if not bands_id:
            print(f"  {slug:14} ERROR: no bands id")
            rc = 1
            continue
        try:
            act = process(man, bands_id, gcm_id, args.dry_run)
            note = "" if gcm_id else "   (!! gcm id MISSING — only bands wired)"
            print(f"  {slug:14} {act}{note}")
        except Exception as e:  # noqa: BLE001
            print(f"  {slug:14} ERROR: {type(e).__name__}: {e}")
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
