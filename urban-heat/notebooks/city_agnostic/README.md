# Notebook sources and provenance

`March2026_agnostic/template/` is the current notebook source for the 40-city
Masselot-main pipeline. `scripts/run_agnostic_batch.py` selects these files and
sets `CITY` for each city config. The final NB09 workflow uses the template's
NB09 entry point and `cityheat.nb09_improved_fast_masselot_main`; its validated
N=128, seed-42, v3 campaign was run from commit
`616fbc2d5131eca0e2b375a344b2c9664946c379`. NB10 was not part of that
campaign.

The earlier `January2026/`, `March2026/`, `March2026_masselot_main/`, and
`March2026_agnostic/{Athens,Copenhagen,Lisbon,Rome}/` trees were development
snapshots, not inputs to the final 40-city run. The January tree was already
untracked. The other trees were last present together on `main` at commit
`9b02b48037ea93bbcf5459836f7ec6c0a9c6e301`; Git history retains their
contents after they are removed from the current tree. Local working copies
may still exist because this cleanup only stops Git tracking them.

The retired `create_masselot_main_notebooks.py`, `create_agnostic_notebooks.py`,
and `run_nb09_full_notebooks.py` scripts depended on those old trees. They are
also preserved in Git history. Do not use the old generators to recreate the
current template: the template received later fixes that those scripts do not
encode. For current runs, use the versioned template, city configs, and active
launchers directly.
