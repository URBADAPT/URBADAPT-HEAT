"""CLI: render the URBADAPT-HEAT manuscript figures for a city, straight from disk.

Currently produces the hazard T2M maps (manuscript Figure 2). Reads the persisted
pipeline snapshot -- no notebook kernel, no ``cityheat`` / ``climada`` import.

Usage (from the ``gmd_visual_items/`` directory, in the ``urbanheat`` conda env)::

    python -m workflow_figures.make_figures --city rome
    python -m workflow_figures.make_figures --city athens --variant masselot_main

Output -> ``gmd_visual_items/`` (sibling of ``urban-heat/``) as PNG + PDF;
filenames carry the city slug so cities don't collide. Override with ``--out``.
"""
from __future__ import annotations
import argparse
import sys
import traceback
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from workflow_figures import figures, style
    from workflow_figures.loader import DEFAULT_VARIANT, find_repo_root, load_city
else:
    from . import figures, style
    from .loader import DEFAULT_VARIANT, find_repo_root, load_city

DEFAULT_OUT_DIR = find_repo_root().parent / 'gmd_visual_items'


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--city', default='rome', help='city slug (default: rome)')
    ap.add_argument('--variant', default=DEFAULT_VARIANT, help=f'output variant (default: {DEFAULT_VARIANT})')
    ap.add_argument('--figures', nargs='*', default=sorted(figures.FIGURES), metavar='NAME',
                    choices=sorted(figures.FIGURES), help='figure keys to render (default: all)')
    ap.add_argument('--out', default=None, help=f'output dir (default: {DEFAULT_OUT_DIR})')
    args = ap.parse_args(argv)
    style.apply_style()
    co = load_city(args.city, variant=args.variant)
    out_dir = Path(args.out) if args.out else DEFAULT_OUT_DIR
    print(f'City   : {co.city_name} ({co.slug})')
    print(f'Source : {co.out}')
    print(f'Output : {out_dir}\n')
    ok = []
    failed = []
    for key in args.figures:
        fn = figures.FIGURES[key]
        try:
            path = fn(co, out_dir)
            print(f'  [{key}] {path.name}')
            ok.append(key)
        except Exception as exc:
            print(f'  [{key}] FAILED: {exc}')
            traceback.print_exc()
            failed.append(key)
    print(f'\nDone: {len(ok)} figure(s) rendered' + (f', {len(failed)} failed ({failed})' if failed else ''))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
