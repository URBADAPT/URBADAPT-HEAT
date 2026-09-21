"""Shared visual style for the URBADAPT-HEAT manuscript figures.

Centralises the palette, matplotlib rcParams and a ``save_fig`` helper so every
figure in this package reads as one consistent system. Pure matplotlib -- no
dependency on ``cityheat`` / ``climada`` -- so the figure package stays fully
decoupled from the pipeline.
"""
from __future__ import annotations
from pathlib import Path
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.figure import Figure

INK = '#1f2933'
MUTED = '#6b7280'
GRID = '#d1d5db'
FUA_OUTLINE = '#111827'
CMAP_T2M = 'inferno'
CMAP_POP = 'viridis'
CMAP_VULN = 'plasma'
CMAP_AC = 'cividis'
CMAP_UPLIFT = 'YlGn'
POLICY_COLORS = {
    'Trees': '#2f7d4f',
    'AC': '#cf5c36',
    'EWS': '#4263eb',
}
PILOT_CITIES = ['rome', 'athens', 'lisbon', 'copenhagen']
CITY_NAMES = {
    'rome': 'Rome',
    'athens': 'Athens',
    'lisbon': 'Lisbon',
    'copenhagen': 'Copenhagen',
}
CITY_MARKERS = {
    'rome': 'o',
    'athens': 's',
    'lisbon': '^',
    'copenhagen': 'D',
}


def apply_style() -> None:
    """Install the shared rcParams. Call once before building figures."""
    mpl.rcParams.update({
        'figure.facecolor': 'white',
        'axes.facecolor': 'white',
        'savefig.facecolor': 'white',
        'font.family': 'DejaVu Sans',
        'font.size': 11,
        'axes.titlesize': 13,
        'axes.titleweight': 'bold',
        'axes.titlecolor': INK,
        'axes.labelsize': 10.5,
        'axes.labelcolor': INK,
        'axes.edgecolor': '#9aa3af',
        'axes.linewidth': 0.9,
        'axes.spines.top': False,
        'axes.spines.right': False,
        'xtick.color': MUTED,
        'ytick.color': MUTED,
        'xtick.labelsize': 9.5,
        'ytick.labelsize': 9.5,
        'legend.frameon': False,
        'legend.fontsize': 9,
        'figure.titlesize': 16,
        'figure.titleweight': 'bold',
        'figure.dpi': 120,
    })


def save_fig(fig: Figure, out_dir: Path, stem: str, *, dpi: int = 300) -> Path:
    """Write ``fig`` to ``out_dir/stem.png`` (and .pdf) and close it."""
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / f'{stem}.png'
    fig.savefig(png, dpi=dpi, bbox_inches='tight')
    fig.savefig(out_dir / f'{stem}.pdf', bbox_inches='tight')
    plt.close(fig)
    return png
