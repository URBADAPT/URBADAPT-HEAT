from pathlib import Path

def make_P(base_dir):
    base = Path(base_dir)
    def P(rel):
        p = base / rel
        if not p.exists():
            raise FileNotFoundError(f"Missing: {p}")
        return p
    return P

def ensure_out(base_dir):
    base = Path(base_dir)
    def OUTP(rel):
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    return OUTP
