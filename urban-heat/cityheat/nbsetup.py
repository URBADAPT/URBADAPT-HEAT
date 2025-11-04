from pathlib import Path
import yaml

def find_repo_root(start: Path | None = None) -> Path:
    start = start or Path.cwd()
    for cand in [start, *start.parents]:
        if (cand / "cityheat").is_dir() and (cand / "configs").is_dir():
            return cand
    raise RuntimeError(
        f"Could not locate repo root from {start}. "
        "Expected to find folders 'cityheat' and 'configs' in a parent."
    )

def bootstrap(city: str):
    PROJECT = find_repo_root()
    CFG = PROJECT / "configs" / f"{city.lower()}.yml"
    if not CFG.exists():
        raise FileNotFoundError(f"Config not found: {CFG}")

    with open(CFG, "r") as f:
        cfg = yaml.safe_load(f)

    OUT = PROJECT / "outputs" / city
    OUT.mkdir(parents=True, exist_ok=True)
    INT = OUT / "interim"
    INT.mkdir(parents=True, exist_ok=True)

    BASE = PROJECT / "data" / city
    p_LCZ     = BASE / "LCZ"
    p_UrbClim = BASE / "UrbClim"
    p_GVI     = BASE / "gviRome"
    p_CoolEff = BASE / "CoolingEff"

    return {
        "PROJECT": PROJECT, "CITY": city, "CFG": CFG, "cfg": cfg,
        "OUT": OUT, "INT": INT, "BASE": BASE,
        "p_LCZ": p_LCZ, "p_UrbClim": p_UrbClim, "p_GVI": p_GVI, "p_CoolEff": p_CoolEff,
    }
