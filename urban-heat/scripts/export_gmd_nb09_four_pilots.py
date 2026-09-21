#!/usr/bin/env python3
"""Export the verified four-pilot GMD NB09 outputs without rerunning NB09.

The bundle contains four reused N=128 Masselot-headline city campaigns and
the twelve completed GMD pilot campaigns: four N=64 Masselot headline, four
N=128 Burke polynomial, and four N=128 Burke power-law. Original relative
``outputs_variants/...`` paths are preserved in the archive.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path


PILOTS = ("athens", "copenhagen", "lisbon", "rome")
ENGINE_COMMIT = "616fbc2d5131eca0e2b375a344b2c9664946c379"
GMD_LAUNCHER_COMMIT = "02f50220fa9cd63dd46440e773ddedd606cea7be"
SCHEMA = "3.0-explicit-policy-trajectories"
HEADLINE_128 = "n128_seed42_v3_616fbc2d5131"
HEADLINE_64 = "gmd4_n64_seed42_v3_616fbc2d5131"
BURKE_128 = "gmd4_n128_burke_seed42_v3_616fbc2d5131"
FINAL_MARKER = Path(
    "runs/agnostic_nb09/gmd_pilots/"
    "gmd4_n64_n128_burke_seed42_v3_616fbc2d5131.ok"
)
CENTRAL_MARKER = Path(
    f"runs/agnostic_nb09/central_preflight/v3_{ENGINE_COMMIT}.ok"
)
ARCHIVE_NAME = "gmd_nb09_four_pilots_complete_v3_616fbc2.tgz"
README_NAME = "README_USE_FOR_GMD.txt"
SUMMARY_NAME = "validation_summary.txt"
CHECKSUM_NAME = "gmd_nb09_four_pilots_complete_v3_616fbc2.sha256"
GATE_COPY_NAME = "GMD_FINAL_GATE_PASS.ok"


@dataclass(frozen=True)
class Selection:
    city: str
    label: str
    n: int
    campaign: str
    scope: str
    family: str | None
    source_commit: str
    relative_path: Path


def selections() -> list[Selection]:
    items: list[Selection] = []
    for city in PILOTS:
        base = Path("outputs_variants/masselot_main_agnostic") / city / "tables/uncertainty_runs"
        items.extend(
            [
                Selection(
                    city, "N128 Masselot headline (reused)", 128, HEADLINE_128,
                    "masselot_headline", None, ENGINE_COMMIT, base / HEADLINE_128,
                ),
                Selection(
                    city, "N64 Masselot headline (new)", 64, HEADLINE_64,
                    "masselot_headline", None, GMD_LAUNCHER_COMMIT, base / HEADLINE_64,
                ),
                Selection(
                    city, "N128 Burke polynomial (new)", 128, BURKE_128,
                    "burke_sensitivity", "burke_polynomial", GMD_LAUNCHER_COMMIT,
                    base / BURKE_128 / "burke_sensitivity/burke_polynomial",
                ),
                Selection(
                    city, "N128 Burke powerlaw (new)", 128, BURKE_128,
                    "burke_sensitivity", "burke_powerlaw", GMD_LAUNCHER_COMMIT,
                    base / BURKE_128 / "burke_sensitivity/burke_powerlaw",
                ),
            ]
        )
    return items


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def count_csv(path: Path, *, qa_status: str | None = None) -> int:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        count = 0
        for row in reader:
            if qa_status is not None and row.get("status", "").lower() != qa_status:
                raise ValueError(f"Non-{qa_status} QA row in {path}: {row}")
            count += 1
    return count


def check_selection(root: Path, item: Selection) -> str:
    directory = root / item.relative_path
    if not directory.is_dir():
        raise FileNotFoundError(f"Missing campaign directory: {directory}")
    slug = item.city
    paths = {
        "completion": directory / "CAMPAIGN_COMPLETE.json",
        "manifest": directory / f"run_manifest_{slug}_improved_fast.json",
        "samples": directory / f"unc_samples_{slug}_improved_fast.csv",
        "lhs_design": directory / f"lhs_design_{slug}_improved_fast.csv",
        "sample_qa": directory / f"sample_mathematical_qa_{slug}_improved_fast.csv",
        "aggregate_qa": directory / f"uq_output_qa_{slug}_improved_fast.csv",
        "trajectories": directory / f"unc_policy_trajectories_25y_{slug}_improved_fast.csv",
    }
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"Missing {name}: {path}")

    marker = load_json(paths["completion"])
    manifest = load_json(paths["manifest"])
    expected_manifest = {
        "output_schema_version": SCHEMA,
        "campaign_id": item.campaign,
        "slug": slug,
        "n_samples": item.n,
        "seed": 42,
        "lhs_scope": item.scope,
        "lhs_scope_family": item.family,
    }
    for key, expected in expected_manifest.items():
        if manifest.get(key) != expected:
            raise ValueError(f"Manifest {key} differs in {directory}: {manifest.get(key)!r} != {expected!r}")
    if manifest.get("git", {}).get("commit") != item.source_commit:
        raise ValueError(f"Unexpected source commit in {paths['manifest']}")
    if marker.get("campaign_id") != item.campaign or marker.get("n_samples") != item.n:
        raise ValueError(f"Invalid campaign completion marker: {paths['completion']}")
    if marker.get("campaign_signature") != manifest.get("campaign_signature"):
        raise ValueError(f"Completion/manifest signature mismatch: {directory}")

    if count_csv(paths["samples"]) != item.n or count_csv(paths["lhs_design"]) != item.n:
        raise ValueError(f"Sample/LHS row count mismatch: {directory}")
    if count_csv(paths["trajectories"]) != item.n * 25 * 8:
        raise ValueError(f"25-year trajectory count mismatch: {directory}")
    count_csv(paths["sample_qa"], qa_status="pass")
    count_csv(paths["aggregate_qa"], qa_status="ok")

    recorded = marker.get("output_sha256", {})
    for key, path in (
        ("samples", paths["samples"]),
        ("lhs_design", paths["lhs_design"]),
        ("sample_qa", paths["sample_qa"]),
        ("aggregate_qa", paths["aggregate_qa"]),
        ("trajectories", paths["trajectories"]),
        ("run_manifest", paths["manifest"]),
    ):
        if recorded.get(key) != sha256(path):
            raise ValueError(f"Completion-marker SHA-256 mismatch for {key}: {path}")

    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Refusing non-self-contained symlink: {path}")
    return f"PASS  {item.city:<11} {item.label:<34} {item.n:>3} draws  {item.relative_path}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", type=Path, default=Path(__file__).resolve().parents[1],
        help="urban-heat repository directory containing outputs_variants/",
    )
    parser.add_argument(
        "--output", type=Path, required=True,
        help="New export directory outside the repository; existing export files are never overwritten",
    )
    args = parser.parse_args()
    root = args.repo.resolve()
    destination = args.output.resolve()
    if not (root / "outputs_variants").is_dir():
        raise SystemExit(f"Not an urban-heat output root: {root}")
    if destination == root or root in destination.parents:
        raise SystemExit("Choose an export directory outside the urban-heat repository")

    gate = root / FINAL_MARKER
    central_gate = root / CENTRAL_MARKER
    if not gate.is_file() or gate.read_text(encoding="utf-8").splitlines()[0] != "PASS":
        raise SystemExit(f"Final four-pilot validation gate is missing or not PASS: {gate}")
    if not central_gate.is_file() or central_gate.read_text(encoding="utf-8").splitlines()[0] != ENGINE_COMMIT:
        raise SystemExit(f"Validated all-city central gate is missing or invalid: {central_gate}")

    chosen = selections()
    reports = [check_selection(root, item) for item in chosen]
    if len(chosen) != 16 or len({item.relative_path for item in chosen}) != 16:
        raise SystemExit("Expected 16 distinct city-scope directories")

    destination.mkdir(parents=True, exist_ok=True)
    names = (ARCHIVE_NAME, CHECKSUM_NAME, README_NAME, SUMMARY_NAME, GATE_COPY_NAME)
    present = [name for name in names if (destination / name).exists()]
    if present:
        raise SystemExit(f"Refusing to overwrite existing export files in {destination}: {present}")

    readme = (
        "AUTHORITATIVE FOUR-PILOT GMD NB09 UNCERTAINTY PACKAGE\n\n"
        "Pilot cities: Athens, Copenhagen, Lisbon, Rome. Seed: 42. Schema: v3.\n"
        f"N=128 Masselot headline: {HEADLINE_128} (4 city directories reused, not rerun).\n"
        f"N=64 Masselot headline: {HEADLINE_64} (4 new city directories).\n"
        f"N=128 Burke polynomial and power-law: {BURKE_128} "
        "(8 new city/IF directories, with the IF family pinned and other applicable inputs sampled).\n"
        f"Validated NB09 engine commit: {ENGINE_COMMIT}.\n"
        f"GMD pilot launch commit: {GMD_LAUNCHER_COMMIT}.\n"
        "All 16 city-scope runs have completion markers, N samples, 25-year trajectories, "
        "passing QA, matching manifests and critical-file hashes.\n\n"
        "This archive is self-contained for the GMD NB09 uncertainty results, but it does "
        "not contain the deterministic NB01-08 source tables needed for other GMD figures.\n"
        "The N=128 Masselot files are the same authoritative four-city subset shared in "
        "_CURRENT_FINAL_NB09_N128_v3_616fbc2_2026-09-17.\n"
        "Original paths under outputs_variants/ are preserved. Extract into a clean "
        "directory or separate checkout; do not unpack over an active working tree without review.\n"
        "This is an output-data package, not a figure-generation script. Check that the "
        "GMD reporting workflow points to these campaign paths when rerendering figures.\n"
    )
    summary = (
        "GMD FOUR-PILOT NB09 EXPORT VALIDATION\n"
        f"Final GMD gate: {gate.read_text(encoding='utf-8').strip()}\n"
        f"All-city central gate: {central_gate.read_text(encoding='utf-8').strip()}\n"
        "Selected city-scope directories: 16/16\n"
        "LHS draws: 4 x 128 Masselot reused + 4 x 64 Masselot new + "
        "8 x 128 Burke new = 1792 draw results.\n"
        "Every selected run: manifest, completion marker, CSV row counts, QA statuses and "
        "critical SHA-256 hashes verified.\n\n"
        + "\n".join(reports) + "\n"
    )

    with tempfile.TemporaryDirectory(prefix=".gmd_export_", dir=destination) as temp_name:
        temp = Path(temp_name)
        (temp / README_NAME).write_text(readme, encoding="utf-8")
        (temp / SUMMARY_NAME).write_text(summary, encoding="utf-8")
        shutil.copy2(gate, temp / GATE_COPY_NAME)
        archive = temp / ARCHIVE_NAME
        members = [str(item.relative_path) for item in chosen]
        command = [
            "tar", "-czf", str(archive), "-C", str(root),
            *members, str(FINAL_MARKER), str(CENTRAL_MARKER),
            "-C", str(temp), README_NAME, SUMMARY_NAME, GATE_COPY_NAME,
        ]
        subprocess.run(command, check=True)

        expected_markers = {
            f"{item.relative_path}/CAMPAIGN_COMPLETE.json" for item in chosen
        }
        with tarfile.open(archive, mode="r:gz") as handle:
            found = {member.name.removeprefix("./") for member in handle.getmembers()}
        missing = expected_markers - found
        if missing:
            raise RuntimeError(f"Archive missing {len(missing)} completion markers: {sorted(missing)}")
        if README_NAME not in found or str(FINAL_MARKER) not in found:
            raise RuntimeError("Archive missing README or final gate marker")

        checksum_text = f"{sha256(archive)}  {ARCHIVE_NAME}\n"
        (temp / CHECKSUM_NAME).write_text(checksum_text, encoding="utf-8")
        for name in names:
            os.replace(temp / name, destination / name)

    print("PASS: 16/16 city-scope runs verified and bundled")
    print(f"Export directory: {destination}")
    for name in names:
        path = destination / name
        print(f"  {name}: {path.stat().st_size:,} bytes")
    print(f"SHA-256: {checksum_text.strip()}")


if __name__ == "__main__":
    main()
