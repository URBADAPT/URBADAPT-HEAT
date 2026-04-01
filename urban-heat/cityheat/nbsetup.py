#setup 
from pathlib import Path
import os
import yaml

def find_repo_root(start: Path | None = None) -> Path:
    """Locate the urban-heat repo root.

    Resolution order:
    1. URBAN_HEAT_ROOT env var (if set and valid)
    2. Upward traversal from *start* (default: cwd)
    """
    env = os.environ.get("URBAN_HEAT_ROOT")
    if env:
        root = Path(env).expanduser().resolve()
        if (root / "cityheat").is_dir() and (root / "configs").is_dir():
            return root

    start = (start or Path.cwd()).resolve()
    if start.is_file():
        start = start.parent
    for cand in [start, *start.parents]:
        if (cand / "cityheat").is_dir() and (cand / "configs").is_dir():
            return cand
    raise RuntimeError(
        f"Could not locate repo root from {start}. "
        "Run from inside the repo or set URBAN_HEAT_ROOT."
    )

def bootstrap(city: str):
    PROJECT = find_repo_root()
    CFG = PROJECT / "configs" / f"{city.lower()}.yml"
    if not CFG.exists():
        raise FileNotFoundError(f"Config not found: {CFG}")

    with open(CFG, "r") as f:
        cfg = yaml.safe_load(f)

    city_name = str(cfg.get("city_name", city))
    os.environ["CITY"] = city_name

    wp_iso3 = cfg.get("wp_iso3")
    if wp_iso3:
        os.environ["WP_ISO3"] = str(wp_iso3).upper()

    wp_country = cfg.get("wp_country")
    if wp_country:
        os.environ["WP_COUNTRY"] = str(wp_country)

    OUT = PROJECT / "outputs" / city
    OUT.mkdir(parents=True, exist_ok=True)
    INT = OUT / "interim"
    INT.mkdir(parents=True, exist_ok=True)

    BASE = PROJECT / "data" / city
    p_LCZ     = BASE / "LCZ"
    p_UrbClim = BASE / "UrbClim"
    p_GVI     = BASE / "gvi"
    p_CoolEff = BASE / "CoolingEff"

    return {
        "PROJECT": PROJECT, "CITY": city_name, "CFG": CFG, "cfg": cfg,
        "OUT": OUT, "INT": INT, "BASE": BASE,
        "p_LCZ": p_LCZ, "p_UrbClim": p_UrbClim, "p_GVI": p_GVI, "p_CoolEff": p_CoolEff,
    }
