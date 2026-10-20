"""Running the template notebooks one after another by hand must work like the batch runs."""
from __future__ import annotations

import json
import re
from pathlib import Path

from cityheat import nb10_summary_masselot_main as nb10

TEMPLATES = Path(__file__).resolve().parents[2] / "notebooks" / "city_agnostic" / "March2026_agnostic" / "template"


def test_notebooks_share_one_default_city():
    # Each notebook runs in its own kernel, so a different default city in one of them
    # silently mixes two cities in an interactive run.
    defaults = {}
    for nb_path in sorted(TEMPLATES.glob("*.ipynb")):
        nb = json.loads(nb_path.read_text(encoding="utf-8"))
        code = "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
        for m in re.finditer(r"os\.environ\.setdefault\(\s*[\"']CITY[\"']\s*,\s*[\"']([^\"']+)[\"']", code):
            defaults.setdefault(m.group(1).strip().lower(), []).append(nb_path.name)
    assert len(defaults) == 1, defaults


def _campaign(tab_dir: Path, name: str, n: int, when: str) -> Path:
    d = tab_dir / "uncertainty_runs" / name
    d.mkdir(parents=True)
    (d / "CAMPAIGN_COMPLETE.json").write_text(json.dumps({"n_samples": n, "completed_at_utc": when}))
    return d


def test_nb10_reads_the_headline_nb09_campaign(tmp_path, monkeypatch):
    monkeypatch.delenv("NB09_CAMPAIGN_ID", raising=False)
    tab = tmp_path / "tables"
    assert nb10._resolve_uq_dir(tab) == tab / "uncertainty_improved_fast"  # nothing yet: legacy location
    old = _campaign(tab, "n128_seed42_v3_aaaaaaaaaaaa", 128, "2026-10-19T10:00:00+00:00")
    _campaign(tab, "n64_seed42_v3_bbbbbbbbbbbb", 64, "2026-10-20T10:00:00+00:00")
    (tab / "uncertainty_runs" / "n128_seed42_v3_unfinished").mkdir()
    assert nb10._resolve_uq_dir(tab) == old  # the largest complete campaign, not the newest
    new = _campaign(tab, "n128_seed42_v3_cccccccccccc", 128, "2026-10-21T10:00:00+00:00")
    assert nb10._resolve_uq_dir(tab) == new
    monkeypatch.setenv("NB09_CAMPAIGN_ID", "n128_seed42_v3_aaaaaaaaaaaa")
    assert nb10._resolve_uq_dir(tab) == old  # an explicit campaign wins
