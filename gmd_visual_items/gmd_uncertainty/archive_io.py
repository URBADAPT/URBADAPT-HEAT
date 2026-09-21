"""Read and validate the final four-pilot GMD NB09 archive without extracting it."""

from __future__ import annotations

import hashlib
import hmac
import json
import tarfile
from pathlib import Path

import pandas as pd


ROOT = "outputs_variants/masselot_main_agnostic"
SCHEMA = "3.0-explicit-policy-trajectories"


def verify_checksum(archive: Path) -> None:
    """Require the adjacent SHA-256 sidecar to identify this exact archive."""
    sidecar = archive.with_suffix(".sha256")
    fields = sidecar.read_text(encoding="utf-8").strip().split()
    if len(fields) != 2:
        raise ValueError(f"Expected one SHA-256 entry in {sidecar}")
    expected, filename = fields
    if filename != archive.name:
        raise ValueError(f"Checksum names {filename!r}, not {archive.name!r}")

    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if not hmac.compare_digest(digest.hexdigest(), expected.lower()):
        raise ValueError(f"SHA-256 mismatch for {archive}")


def _member_stream(tar: tarfile.TarFile, member: str):
    try:
        info = tar.getmember(member)
    except KeyError as exc:
        raise FileNotFoundError(f"Missing archive member: {member}") from exc
    if not info.isfile():
        raise ValueError(f"Expected a regular file in archive: {member}")
    stream = tar.extractfile(info)
    if stream is None:
        raise FileNotFoundError(f"Could not read archive member: {member}")
    return stream


def read_csv(tar: tarfile.TarFile, member: str) -> pd.DataFrame:
    """Read one CSV directly from the compressed archive."""
    with _member_stream(tar, member) as stream:
        return pd.read_csv(stream)


def load_samples(
    tar: tarfile.TarFile,
    city: str,
    campaign: str,
    n: int,
    *,
    branch: str | None = None,
) -> tuple[str, pd.DataFrame]:
    """Validate a completed city campaign and return its sample table."""
    prefix = f"{ROOT}/{city}/tables/uncertainty_runs/{campaign}"
    if branch is not None:
        if branch not in {"burke_polynomial", "burke_powerlaw"}:
            raise ValueError(f"Unsupported conditional branch: {branch}")
        prefix = f"{prefix}/burke_sensitivity/{branch}"
    with _member_stream(tar, f"{prefix}/CAMPAIGN_COMPLETE.json") as stream:
        marker = json.load(stream)

    actual = (
        marker.get("slug"),
        marker.get("campaign_id"),
        marker.get("n_samples"),
        marker.get("output_schema_version"),
    )
    expected = (city, campaign, n, SCHEMA)
    if actual != expected:
        raise ValueError(f"Unexpected completion marker for {city}: {actual!r}")

    samples = read_csv(tar, f"{prefix}/unc_samples_{city}_improved_fast.csv")
    if "sample_idx" not in samples.columns:
        raise ValueError(f"Missing sample_idx for {city}/{campaign}")
    if len(samples) != n or samples["sample_idx"].nunique(dropna=False) != n:
        raise ValueError(f"{city}/{campaign}: expected {n} distinct samples")
    if branch is not None:
        if "if_family" not in samples.columns or not samples["if_family"].eq(branch).all():
            raise ValueError(f"{city}/{branch}: samples do not all use the conditional IF")
    return prefix, samples
