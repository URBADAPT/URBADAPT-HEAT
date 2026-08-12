"""DEPRECATED — legacy 4-city (March2026 Burke-main) NB09 launcher.

Superseded by the agnostic 40-city path:
    scripts/run_agnostic_batch.py --notebooks 09      (local)
    scripts/juno_run_nb09.sh                          (Juno / LSF)

This module still targets notebooks/city_agnostic/March2026/<City>/ and hardcodes the
four pilots plus Copenhagen's obsolete extreme track; kept for reference only. Do NOT
use it for the masselot-main agnostic run.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

import nbformat


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK_ROOT = ROOT / "notebooks" / "city_agnostic" / "March2026"
JUPYTER = Path("/opt/anaconda3/envs/urbanheat/bin/jupyter-nbconvert")
LOG_DIR = ROOT / "outputs" / "nb09_full_logs"
TMP_DIR = Path("/private/tmp/nb09_full_notebooks")

CITY_CONFIG = {
    "lisbon": ("Lisbon", "standard"),
    "rome": ("Rome", "standard"),
    "copenhagen": ("Copenhagen", "extreme"),
    "athens": ("Athens", "standard"),
}


def log(message: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def city_notebook(city_label: str) -> Path:
    return NOTEBOOK_ROOT / city_label / f"09_uncertainty_0126_{city_label}_improved_fast.ipynb"


def make_execution_notebook(src_path: Path, run_id: str, n: int, slug: str, hazard_track: str) -> Path:
    nb = nbformat.read(src_path, as_version=4)

    suppress_cell = nbformat.v4.new_code_cell(
        "\n".join(
            [
                "import logging",
                "import warnings",
                "warnings.filterwarnings('ignore', category=FutureWarning)",
                "warnings.filterwarnings('ignore', category=RuntimeWarning)",
                "for _name in ['climada', 'climada.hazard', 'climada.hazard.base', 'SALib']:",
                "    logging.getLogger(_name).setLevel(logging.ERROR)",
                f"print('NB09 full run mode: N={n}, figures enabled, hazard_track={hazard_track}')",
            ]
        )
    )

    figure_cell = nbformat.v4.new_code_cell(
        "\n".join(
            [
                "from pathlib import Path",
                "from IPython.display import Image, Markdown, display",
                "",
                "fig_dir = Path(paths['samples']).parent",
                "fig_order = [",
                f"    'unc_distribution_aai_agg_{slug}_improved_fast.png',",
                f"    'unc_cdf_aai_agg_{slug}_improved_fast.png',",
                f"    'sens_tornado_aai_agg_{slug}_improved_fast.png',",
                f"    'unc_distribution_cba_ews_{slug}_improved_fast.png',",
                f"    'unc_cdf_cba_ews_{slug}_improved_fast.png',",
                f"    'sens_tornado_cba_ews_{slug}_improved_fast.png',",
                f"    'unc_freq_curve_{slug}_improved_fast.png',",
                f"    'unc_distribution_vuln_svi_mean_{slug}_improved_fast.png',",
                f"    'unc_distribution_vuln_2050_svi_mean_{slug}_improved_fast.png',",
                f"    'sens_tornado_vuln_svi_gap_{slug}_improved_fast.png',",
                f"    'sens_tornado_vuln_svi_mean_{slug}_improved_fast.png',",
                "]",
                "display(Markdown('## Generated NB09 figures'))",
                "shown = 0",
                "for fig_name in fig_order:",
                "    fig_path = fig_dir / fig_name",
                "    if fig_path.exists():",
                "        display(Markdown(f'### `{fig_name}`'))",
                "        display(Image(filename=str(fig_path)))",
                "        shown += 1",
                "display(Markdown(f'Generated figures displayed: **{shown}**'))",
            ]
        )
    )

    insert_at = 2 if len(nb.cells) >= 2 else len(nb.cells)
    nb.cells.insert(insert_at, suppress_cell)
    nb.cells.append(figure_cell)

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = TMP_DIR / f"{src_path.stem}_{run_id}_exec.ipynb"
    nbformat.write(nb, tmp_path)
    return tmp_path


def trim_stream_outputs(out_path: Path, max_chars: int = 24000) -> None:
    nb = nbformat.read(out_path, as_version=4)
    changed = False
    for cell in nb.cells:
        if cell.get("cell_type") != "code":
            continue
        for output in cell.get("outputs", []):
            if output.get("output_type") != "stream":
                continue
            text = output.get("text", "")
            if isinstance(text, list):
                text = "".join(text)
            if len(text) > max_chars:
                head = text[: max_chars // 2]
                tail = text[-max_chars // 2 :]
                output["text"] = (
                    head
                    + "\n\n...[stream output trimmed by nb09 full-run launcher]...\n\n"
                    + tail
                )
                changed = True
    if changed:
        nbformat.write(nb, out_path)


def run_city(slug: str, *, run_id: str, n: int, seed: int) -> None:
    city_label, hazard_track = CITY_CONFIG[slug]
    src = city_notebook(city_label)
    if not src.exists():
        raise FileNotFoundError(src)

    tmp_nb = make_execution_notebook(src, run_id, n, slug, hazard_track)
    out_name = f"09_uncertainty_0126_{city_label}_improved_fast.out.ipynb"
    out_path = src.parent / out_name
    city_log = LOG_DIR / f"nb09_full_N{n}_{run_id}_{slug}.log"

    env = os.environ.copy()
    env.update(
        {
            "URBAN_HEAT_ROOT": str(ROOT),
            "CITY": slug,
            "NB09_N": str(n),
            "NB09_SEED": str(seed),
            "NB09_MAKE_FIGURES": "1",
            "HAZARD_TRACK": hazard_track,
            "MPLBACKEND": "Agg",
            "PYDEVD_DISABLE_FILE_VALIDATION": "1",
        }
    )

    cmd = [
        str(JUPYTER),
        "--to",
        "notebook",
        "--execute",
        str(tmp_nb),
        "--output",
        out_name,
        "--output-dir",
        str(src.parent),
        "--ExecutePreprocessor.timeout=-1",
        "--ExecutePreprocessor.kernel_name=urbanheat",
    ]

    log(f"START {city_label}: source={src}")
    log(f"LOG   {city_label}: {city_log}")
    start = time.time()
    with city_log.open("w") as fh:
        fh.write(f"NB09 full run {run_id}: city={city_label}, slug={slug}, N={n}, hazard_track={hazard_track}\n")
        fh.flush()
        proc = subprocess.run(cmd, cwd=str(ROOT), env=env, stdout=fh, stderr=subprocess.STDOUT)
    elapsed = time.time() - start
    if proc.returncode != 0:
        raise RuntimeError(f"{city_label} nbconvert failed with exit code {proc.returncode}; see {city_log}")
    trim_stream_outputs(out_path)
    log(f"DONE  {city_label}: {out_path} ({elapsed/3600:.2f} h)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run March2026 NB09 improved-fast notebooks with executed outputs.")
    parser.add_argument("--n", type=int, default=int(os.environ.get("NB09_N", "512")))
    parser.add_argument("--seed", type=int, default=int(os.environ.get("NB09_SEED", "42")))
    parser.add_argument(
        "--cities",
        nargs="+",
        default=["lisbon", "rome", "copenhagen", "athens"],
        choices=sorted(CITY_CONFIG),
        help="City slugs to run, in order.",
    )
    parser.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log(f"NB09 full notebook launcher started: RUN_ID={args.run_id}, N={args.n}")
    log(f"ROOT={ROOT}")
    log(f"Cities={args.cities}")
    for slug in args.cities:
        run_city(slug, run_id=args.run_id, n=args.n, seed=args.seed)
    log("All requested city notebooks completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
