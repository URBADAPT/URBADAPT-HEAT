"""Manuscript map figures -- rebuilt decoupled from the notebooks.

Reproduces (cleaner versions of) the GMD manuscript's spatial-map figures, all
sharing one system: a row of FUA-framed panels on a shared colour scale, the
municipi (sub-municipal admin) boundaries overlaid, a clean FUA outline, no
title, and per-panel annotations, with a single shared colorbar.

  * ``hazard_t2m_maps`` (Fig. 2): annual-mean daily 2 m air temperature (T2M)
    for the 2020 baseline and the 2030/2040/2050 projections.
  * ``exposure_population`` (Fig. 3): 2020 exposed population, total and by the
    three age groups (<15, 15--64, 65+), on a shared logarithmic scale.
  * ``svi_components`` (Fig. 4): 2050/SSP2 Social Vulnerability Index -- its
    three dimensions and the composite -- as a per-municipio choropleth of
    population-weighted mean scores.

It reads the persisted snapshot straight off disk via :class:`CityOutputs` --
no notebook kernel, no ``cityheat`` / ``climada`` import; just numpy / pandas /
matplotlib (scipy tidies analysis-mask holes; geopandas + netCDF4 draw the
vector municipi boundaries -- all used opportunistically, degrading gracefully).

Data map (city ``slug``):
  interim/city_mask.npz               -> FUA analysis mask (bool, HxW)
  interim/t2m_hazard_2020_2050_{slug}.npz  -> years (N,), data (N, H, W) degC
  interim/pop_on_ref_{slug}_2020.npz  -> pop (H, W): total population 2020
  interim/age_on_ref_{slug}_2020.npz  -> lt15 / a15_64 / g65 (H, W): age grids
  interim/vulnerability_{slug}_SSP2_2050.npz -> thermal / foreign_abs /
        unemp_abs / svi (H, W): [0,1] vulnerability sub-scores + composite
  interim/muni_on_ref_{slug}.npz      -> muni_on_ref (H, W): municipio id grid
  interim/municipi_{slug}.gpkg        -> municipi (sub-municipal admin) polygons
  hazard/T2M_daily_mean_*_FUA_degC.nc -> grid georeferencing (x/y in EPSG:3035)

The municipi boundaries are overlaid from the vector ``municipi_{slug}.gpkg``
when geopandas is available (smooth outlines), falling back to the rasterised
``muni_on_ref_{slug}.npz`` label grid otherwise.
"""
from __future__ import annotations
from pathlib import Path
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import colors as mcolors
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from . import style as S
from .loader import CityOutputs, load_city


def _footprint(mask: np.ndarray) -> np.ndarray:
    """Solid FUA footprint: ``mask`` with interior holes filled (for a clean outline)."""
    try:
        from scipy import ndimage
        return ndimage.binary_fill_holes(mask)
    except Exception:
        return mask


def _fill_gaps(field: np.ndarray, footprint: np.ndarray) -> np.ndarray:
    """Nearest-neighbour fill of NaN gaps inside ``footprint``.

    The temperature field is spatially continuous, so filling the small
    unpopulated-cell gaps in the stored analysis mask is a display-only tidy,
    not new information. Falls back to the raw field if scipy is unavailable.
    """
    out = field.copy()
    gaps = footprint & ~np.isfinite(out)
    if not gaps.any():
        return out
    try:
        from scipy import ndimage
        idx = ndimage.distance_transform_edt(~np.isfinite(out), return_distances=False, return_indices=True)
        filled = out[tuple(idx)]
        out[gaps] = filled[gaps]
        return out
    except Exception:
        return out


def _grid_transform(co: CityOutputs) -> tuple[float, float, float, float] | None:
    """Return (x0, y0, dx, dy) mapping EPSG:3035 coords to (col, row) pixels.

    Read from any hazard NetCDF's x/y coordinate variables. The reference grid
    stores ``data[row, col]`` as ``(y_index, x_index)`` with both axes ascending,
    so ``col = (X - x0) / dx`` and ``row = (Y - y0) / dy`` place a projected point
    in the same pixel space as ``imshow``.
    """
    nc = co.latest_glob('hazard', 'T2M_daily_mean_*_FUA_degC.nc')
    if nc is None:
        return None
    try:
        from netCDF4 import Dataset
        with Dataset(nc) as ds:
            x = np.asarray(ds.variables['x'][:], dtype=float)
            y = np.asarray(ds.variables['y'][:], dtype=float)
        return (float(x[0]), float(y[0]), float(x[1] - x[0]), float(y[1] - y[0]))
    except Exception:
        return None


def _municipi_vector_lines(co: CityOutputs) -> list[np.ndarray] | None:
    """Municipi boundary rings from the vector gpkg, in imshow pixel coordinates.

    Returns a list of (K, 2) polylines (col, row), or ``None`` if geopandas / the
    gpkg / the grid transform are unavailable (caller falls back to the raster).
    """
    gpkg = co.first(f'interim/municipi_{co.slug}.gpkg')
    tf = _grid_transform(co)
    if gpkg is None or tf is None:
        return None
    x0, y0, dx, dy = tf
    try:
        import geopandas as gpd
        gdf = gpd.read_file(gpkg).to_crs(3035)
    except Exception:
        return None
    rings = []
    for geom in gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        parts = geom.geoms if geom.geom_type.startswith('Multi') else [geom]
        for poly in parts:
            for ring in [poly.exterior, *poly.interiors]:
                if ring is None:
                    continue
                a = np.asarray(ring.coords, dtype=float)
                rings.append(np.column_stack([(a[:, 0] - x0) / dx, (a[:, 1] - y0) / dy]))
    return rings or None


def _municipi_segments(labels: np.ndarray) -> np.ndarray | None:
    """Line segments for the internal boundaries between adjacent municipi.

    ``labels`` is the municipi id grid on the reference grid (0 = outside any
    municipio). Returns an (M, 2, 2) array of segment endpoints in imshow pixel
    coordinates (x = column, y = row), or ``None`` if there are none. Only edges
    where the two neighbouring cells carry different *positive* ids are drawn, so
    this shows the municipi mosaic without redrawing the outer FUA perimeter.
    """
    segs = []
    a = labels[:, :-1]
    b = labels[:, 1:]
    ii, jj = np.nonzero((a != b) & (a > 0) & (b > 0))
    for i, j in zip(ii.tolist(), jj.tolist()):
        segs.append([(j + 0.5, i - 0.5), (j + 0.5, i + 0.5)])
    a = labels[:-1, :]
    b = labels[1:, :]
    ii, jj = np.nonzero((a != b) & (a > 0) & (b > 0))
    for i, j in zip(ii.tolist(), jj.tolist()):
        segs.append([(j - 0.5, i + 0.5), (j + 0.5, i + 0.5)])
    if segs:
        return np.asarray(segs)


def _municipi_lines(co: CityOutputs) -> list[np.ndarray] | None:
    """Municipi boundary polylines in imshow pixel coords: vector, else raster."""
    lines = _municipi_vector_lines(co)
    if lines is not None:
        return lines
    path = co.first(f'interim/muni_on_ref_{co.slug}.npz')
    if path is None:
        return None
    labels = co.npz(path).get('muni_on_ref')
    if labels is None:
        return None
    segs = _municipi_segments(np.asarray(labels))
    if segs is not None:
        return list(segs)


def _prep_cmap(name: str):
    cmap = plt.get_cmap(name).copy()
    cmap.set_bad('white')
    return cmap


def _draw_raster_panel(ax, arr, *, cmap, norm, footprint, muni_lines, title, annot):
    """Draw one FUA-framed raster panel: field, FUA outline, municipi, title, annot."""
    im = ax.imshow(arr, cmap=cmap, norm=norm, interpolation='nearest')
    ax.contour(footprint.astype(float), levels=[0.5], colors=S.FUA_OUTLINE, linewidths=0.9)
    if muni_lines is not None:
        ax.add_collection(LineCollection(
            muni_lines, colors='white', linewidths=0.7, alpha=0.92,
            path_effects=[pe.withStroke(linewidth=1.5, foreground=S.FUA_OUTLINE)]))
    ax.set_title(title, pad=6)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_aspect('equal')
    for spine in ax.spines.values():
        spine.set_visible(False)
    if annot:
        ax.text(0.035, 0.03, annot, transform=ax.transAxes, ha='left', va='bottom',
                fontsize=10.5, color=S.INK, linespacing=1.4,
                bbox={'facecolor': 'white', 'alpha': 0.85, 'edgecolor': 'none', 'boxstyle': 'round,pad=0.3'})
    return im


def _render_map_grid(co: CityOutputs, out_dir: Path, stem: str, *, arrays: list[np.ndarray],
                     titles: list[str], annots: list[str], cmap_name: str, norm, cbar_label: str) -> Path:
    """Render a near-square grid of FUA-framed map panels in the house style.

    ``arrays`` are display-ready (NaN = page white); all panels share ``norm``
    and ``cmap_name``. Each panel gets a clean FUA outline, the municipi overlay,
    a title and a corner annotation; one shared horizontal colorbar sits below.
    Four panels lay out 2x2 so each map is large in the rendered figure.
    """
    footprint = _footprint(co.city_mask())
    muni_lines = _municipi_lines(co)
    cmap = _prep_cmap(cmap_name)
    n = len(arrays)
    ncols = int(np.ceil(np.sqrt(n)))
    nrows = int(np.ceil(n / ncols))
    fig, axarr = plt.subplots(nrows, ncols, figsize=(5.0 * ncols, 5.4 * nrows), constrained_layout=True, squeeze=False)
    axes = axarr.ravel()
    im = None
    for j in range(len(axes)):
        if j >= n:
            axes[j].set_visible(False)
            continue
        im = _draw_raster_panel(axes[j], arrays[j], cmap=cmap, norm=norm, footprint=footprint,
                                muni_lines=muni_lines, title=titles[j], annot=annots[j] if annots else '')
    cbar = fig.colorbar(im, ax=axarr, orientation='horizontal', fraction=0.045, pad=0.02, shrink=0.55, aspect=45)
    cbar.set_label(cbar_label, fontsize=10.5)
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(length=3, color=S.MUTED)
    return S.save_fig(fig, out_dir, stem)


def _load_hazard_cube(co: CityOutputs) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (years, data, mask); data is masked to the FUA (NaN outside)."""
    mask = co.city_mask()
    arr = co.npz(co.require('T2M hazard cube', f'interim/t2m_hazard_2020_2050_{co.slug}.npz'))
    years = np.asarray(arr['years']).astype(int)
    data = np.asarray(arr['data']).astype(float)
    order = np.argsort(years)
    years = years[order]
    data = data[order]
    data = np.where(mask[None, :, :], data, np.nan)
    return years, data, mask


def fig_hazard_t2m_maps(co: CityOutputs, out_dir: Path, *, fill_gaps: bool = True) -> Path:
    """Figure 2: annual-mean daily T2M maps per target year across the FUA."""
    years, data, mask = _load_hazard_cube(co)
    footprint = _footprint(mask)
    disp = [_fill_gaps(data[i], footprint) if fill_gaps else data[i] for i in range(len(years))]
    finite = np.concatenate([a[np.isfinite(a)] for a in disp])
    norm = mcolors.Normalize(vmin=float(finite.min()), vmax=float(finite.max()))
    means = [float(np.nanmean(data[i])) for i in range(len(years))]
    base_mean = means[0]
    titles = [str(int(y)) for y in years.tolist()]
    annots = []
    for i in range(len(years)):
        lines = [f'$\\bar{{T}}$ = {means[i]:.1f} °C']
        if i != 0:
            lines.append(f'$\\Delta$ +{means[i] - base_mean:.1f} °C')
        annots.append('\n'.join(lines))
    return _render_map_grid(co, out_dir, f'fig_hazard_t2m_maps_{co.slug}',
                            arrays=disp, titles=titles, annots=annots, cmap_name=S.CMAP_T2M, norm=norm,
                            cbar_label='Annual mean daily 2 m air temperature (°C)')


def _fmt_count(n: float) -> str:
    if n >= 1e6:
        return f'{n / 1e6:.2f} M'
    if n >= 1e3:
        return f'{n / 1e3:.0f} k'
    return f'{n:.0f}'


def fig_exposure_population(co: CityOutputs, out_dir: Path) -> Path:
    """Figure 3: 2020 exposed population -- total and by age group -- log scale."""
    mask = co.city_mask()
    pop = co.npz(co.require('population 2020', f'interim/pop_on_ref_{co.slug}_2020.npz'))['pop'].astype(float)
    age = co.npz(co.require('age grid 2020', f'interim/age_on_ref_{co.slug}_2020.npz'))
    layers = [
        ('(a) Total population', pop),
        ('(b) Children (<15)', age['lt15'].astype(float)),
        ('(c) Working age (15–64)', age['a15_64'].astype(float)),
        ('(d) Elderly (65+)', age['g65'].astype(float)),
    ]
    total = float(np.nansum(np.where(mask, pop, 0.0)))
    arrays = []
    titles = []
    annots = []
    for title, layer in layers:
        arrays.append(np.where(mask & (layer > 0), layer, np.nan))
        titles.append(title)
        count = float(np.nansum(np.where(mask, layer, 0.0)))
        share = '' if title.endswith('Total population') or total <= 0 else f'  ({count / total * 100:.0f}%)'
        annots.append(f'N = {_fmt_count(count)}{share}')
    pooled = np.concatenate([a[np.isfinite(a)] for a in arrays])
    pooled = pooled[pooled > 0]
    vmax = float(pooled.max())
    norm = mcolors.LogNorm(vmin=min(1.0, vmax * 0.5), vmax=vmax)
    return _render_map_grid(co, out_dir, f'fig_exposure_population_{co.slug}',
                            arrays=arrays, titles=titles, annots=annots, cmap_name=S.CMAP_POP, norm=norm,
                            cbar_label='Population (people per 100 m cell)')


def _popweighted_by_muni(field: np.ndarray, labels: np.ndarray, pop: np.ndarray, mask: np.ndarray) -> dict[int, float]:
    """Population-weighted mean of ``field`` within each municipio id."""
    valid = mask & np.isfinite(field) & np.isfinite(pop) & (pop > 0)
    out = {}
    for mid in np.unique(labels[valid & (labels > 0)]).tolist():
        sel = valid & (labels == mid)
        w = pop[sel]
        out[int(mid)] = float(np.sum(field[sel] * w) / np.sum(w))
    return out


def _render_choropleth_grid(gdf, out_dir: Path, stem: str, *, value_cols: list[str], titles: list[str],
                            annots: list[str], cmap_name: str, norm, cbar_label: str) -> Path:
    """Render a 2x2 grid of municipio choropleths in the house style.

    ``gdf`` carries one column per panel in ``value_cols`` (already in a
    projected CRS). No main title; per-panel titles and a corner annotation;
    municipi drawn with a white-on-dark edge; one shared horizontal colorbar.
    """
    n = len(value_cols)
    ncols = int(np.ceil(np.sqrt(n)))
    nrows = int(np.ceil(n / ncols))
    fig, axarr = plt.subplots(nrows, ncols, figsize=(5.0 * ncols, 5.4 * nrows), constrained_layout=True, squeeze=False)
    axes = axarr.ravel()
    for j in range(len(axes)):
        ax = axes[j]
        if j >= n:
            ax.set_visible(False)
            continue
        gdf.plot(column=value_cols[j], cmap=cmap_name, norm=norm, ax=ax, edgecolor='none',
                 missing_kwds={'color': '#e5e7eb'})
        gdf.boundary.plot(ax=ax, color=S.FUA_OUTLINE, linewidth=1.6)
        gdf.boundary.plot(ax=ax, color='white', linewidth=0.7)
        ax.set_title(titles[j], pad=6)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_aspect('equal')
        for spine in ax.spines.values():
            spine.set_visible(False)
        if annots and annots[j]:
            ax.text(0.035, 0.03, annots[j], transform=ax.transAxes, ha='left', va='bottom',
                    fontsize=10.5, color=S.INK,
                    bbox={'facecolor': 'white', 'alpha': 0.85, 'edgecolor': 'none', 'boxstyle': 'round,pad=0.3'})
    sm = plt.cm.ScalarMappable(cmap=cmap_name, norm=norm)
    cbar = fig.colorbar(sm, ax=axarr, orientation='horizontal', fraction=0.045, pad=0.02, shrink=0.55, aspect=45)
    cbar.set_label(cbar_label, fontsize=10.5)
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(length=3, color=S.MUTED)
    return S.save_fig(fig, out_dir, stem)


def fig_svi_components(co: CityOutputs, out_dir: Path) -> Path:
    """Figure 4: 2050/SSP2 SVI dimensions + composite, per-municipio choropleth."""
    import geopandas as gpd
    mask = co.city_mask()
    vul = co.npz(co.require('SVI 2050 SSP2', f'interim/vulnerability_{co.slug}_SSP2_2050.npz'))
    labels = np.asarray(co.npz(co.require('municipio id grid', f'interim/muni_on_ref_{co.slug}.npz'))['muni_on_ref'])
    pop_path = co.first(f'interim/pop_on_ref_{co.slug}_SSP2_2050.npz') or co.first(f'interim/pop_on_ref_{co.slug}_2020.npz')
    pop = co.npz(pop_path)['pop'].astype(float)
    dims = [
        ('(a) Thermal vulnerability (building vintage)', 'thermal', 'thermal'),
        ('(b) Social vulnerability (foreign-born)', 'foreign', 'foreign_abs'),
        ('(c) Economic vulnerability (non-employment)', 'unemp', 'unemp_abs'),
        ('(d) Composite SVI', 'svi', 'svi'),
    ]
    gdf = gpd.read_file(co.require('municipi polygons', f'interim/municipi_{co.slug}.gpkg')).to_crs(3035)
    titles = []
    cols = []
    annots = []
    all_vals = []
    for title, col, key in dims:
        field = np.asarray(vul[key]).astype(float)
        by_muni = _popweighted_by_muni(field, labels, pop, mask)
        gdf[col] = gdf['muni_id'].map(by_muni)
        titles.append(title)
        cols.append(col)
        all_vals.extend(v for v in by_muni.values() if np.isfinite(v))
        city = float(np.nansum(np.where(mask & np.isfinite(field), field * pop, 0.0))
                     / np.nansum(np.where(mask & np.isfinite(field), pop, 0)))
        annots.append(f'city mean = {city:.2f}')
    lo, hi = min(all_vals), max(all_vals)
    norm = mcolors.Normalize(vmin=lo, vmax=hi)
    return _render_choropleth_grid(
        gdf, out_dir, f'fig_svi_components_{co.slug}',
        value_cols=cols, titles=titles, annots=annots, cmap_name=S.CMAP_VULN, norm=norm,
        cbar_label='Vulnerability score (0–1, population-weighted per municipio; higher = more vulnerable)')


def _city_mean(field: np.ndarray, pop: np.ndarray, mask: np.ndarray) -> float:
    """Population-weighted city mean of ``field`` over the analysis mask."""
    sel = mask & np.isfinite(field) & np.isfinite(pop) & (pop > 0)
    return float(np.sum(field[sel] * pop[sel]) / np.sum(pop[sel]))


def fig_ac_penetration(co: CityOutputs, out_dir: Path, *, year: int = 2050) -> Path:
    """Figure 6: AC coverage maps -- baseline, income-targeted policy, uplift."""
    mask = co.city_mask()
    footprint = _footprint(mask)
    muni_lines = _municipi_lines(co)
    arr = co.npz(co.require('AC coverage maps', f'ac_coverage_maps_{co.slug}.npz'))
    years = list(np.asarray(arr['years']).astype(int))
    i = years.index(year)
    base = np.where(mask, arr['coverage_base_3d'][i].astype(float) * 100.0, np.nan)
    policy = np.where(mask, arr['coverage_policy_3d'][i].astype(float) * 100.0, np.nan)
    uplift = np.where(mask & np.isfinite(base) & np.isfinite(policy), policy - base, np.nan)
    pop = co.npz(co.first(f'interim/pop_on_ref_{co.slug}_SSP2_{year}.npz')
                 or co.require('population', f'interim/pop_on_ref_{co.slug}_2020.npz'))['pop'].astype(float)
    cov_cmap = _prep_cmap(S.CMAP_AC)
    up_cmap = _prep_cmap(S.CMAP_UPLIFT)
    cov_pooled = np.concatenate([base[np.isfinite(base)], policy[np.isfinite(policy)]])
    cov_norm = mcolors.Normalize(vmin=float(cov_pooled.min()), vmax=float(cov_pooled.max()))
    up_norm = mcolors.Normalize(vmin=0.0, vmax=float(np.nanmax(uplift)))
    panels = [
        (base, cov_cmap, cov_norm, '(a) Baseline coverage', f'city mean = {_city_mean(base, pop, mask):.0f}%'),
        (policy, cov_cmap, cov_norm, '(b) Income-targeted policy', f'city mean = {_city_mean(policy, pop, mask):.0f}%'),
        (uplift, up_cmap, up_norm, '(c) Policy uplift (Δ)', f'city mean = +{_city_mean(uplift, pop, mask):.1f} pp'),
    ]
    fig = plt.figure(figsize=(15.0, 6.4))
    fig.subplots_adjust(left=0.01, right=0.99, top=0.94, bottom=0.17, wspace=0.05)
    ims = []
    for k, (a, cmap, norm, title, annot) in enumerate(panels):
        ax = fig.add_subplot(1, 3, k + 1)
        ims.append(_draw_raster_panel(ax, a, cmap=cmap, norm=norm, footprint=footprint,
                                      muni_lines=muni_lines, title=title, annot=annot))
    cax1 = fig.add_axes([0.11, 0.1, 0.38, 0.028])
    cb1 = fig.colorbar(ims[0], cax=cax1, orientation='horizontal')
    cb1.set_label('AC coverage (% of households)', fontsize=10.5)
    cax2 = fig.add_axes([0.71, 0.1, 0.2, 0.028])
    cb2 = fig.colorbar(ims[2], cax=cax2, orientation='horizontal')
    cb2.set_label('Uplift (percentage points)', fontsize=10.5)
    for cb in (cb1, cb2):
        cb.outline.set_visible(False)
        cb.ax.tick_params(length=3, color=S.MUTED)
    return S.save_fig(fig, out_dir, f'fig_ac_penetration_maps_{co.slug}')


def _load_pilots(co: CityOutputs) -> list[CityOutputs]:
    """Load the pilot cities that exist under the same output variant as ``co``."""
    variant = co.out.parent.name
    cos = []
    for slug in S.PILOT_CITIES:
        try:
            cos.append(load_city(slug, variant=variant))
        except FileNotFoundError:
            pass
    return cos


def fig_policy_levers(co: CityOutputs, out_dir: Path) -> Path:
    """Section 4: 25-year adaptation-benefit trajectories, one panel per pilot city.

    Each panel overlays the three policies' annual avoided deaths on one axis:
    air conditioning (gross, interpolated between target years), EWS (net), and
    urban trees. A single shared legend sits in a reserved band below the grid.
    """
    cos = _load_pilots(co)
    fig, axarr = plt.subplots(2, 2, figsize=(12.0, 9.0), squeeze=False)
    axes = axarr.ravel()
    for k, ax in enumerate(axes):
        if k >= len(cos):
            ax.set_visible(False)
            continue
        city = cos[k]
        ews = city.csv(city.require('EWS benefits', f'tables/ews_benefits_25y_{city.slug}.csv'))
        ews = ews[ews['scenario'] == 'central'].sort_values('year')
        trees = city.csv(city.require('Trees benefits', f'tables/trees_benefits_25y_{city.slug}.csv')).sort_values('year')
        ac = city.csv(city.require('AC avoided deaths', f'interim/annual_heat_deaths_climada_avoided_AC_{city.slug}.csv')).sort_values('year')
        yrs = ews['year'].to_numpy()
        ac_annual = np.interp(yrs, ac['year'].to_numpy(), ac['overall'].to_numpy())
        ax.plot(yrs, ac_annual, color=S.POLICY_COLORS['AC'], lw=2.2, label='Air conditioning')
        ax.plot(ews['year'], ews['net_avoided_deaths'], color=S.POLICY_COLORS['EWS'], lw=2.2, label='Early warning system')
        ax.plot(trees['year'], trees['trees_only_dynamic'], color=S.POLICY_COLORS['Trees'], lw=2.2, label='Urban trees')
        ax.set_title(S.CITY_NAMES.get(city.slug, city.city_name))
        ax.set_xlabel('Year')
        ax.set_ylabel('Avoided deaths per year')
        ax.margins(x=0.02)
        ax.grid(True, axis='y', color=S.GRID, linewidth=0.7, alpha=0.6)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.legend(handles, labels, loc='lower center', ncol=3, frameon=False, fontsize=10.5, bbox_to_anchor=(0.5, 0.01))
    return S.save_fig(fig, out_dir, 'fig_policy_levers')


def fig_cba_dashboard(co: CityOutputs, out_dir: Path) -> Path:
    """Section 4: cross-city cost-effectiveness frontier + cost-per-death bars."""
    cos = _load_pilots(co)
    policies = [('Trees (base O&M)', 'Trees'), ('AC (NET)', 'AC'), ('EWS (central)', 'EWS')]
    recs = []
    for city in cos:
        cea = city.csv(city.require('CEA summary', f'tables/{city.slug}_cea_summary.csv')).set_index('Policy')
        for row, short in policies:
            if row in cea.index:
                r = cea.loc[row]
                recs.append((city.slug, short, float(r['Avoided_Deaths_25y']),
                             float(r['PV_Cost_EUR']), float(r['Cost_per_Death_EUR'])))
    fig, (axA, axB) = plt.subplots(1, 2, figsize=(14.0, 6.2), constrained_layout=True)
    for slug, short, av, cost, _ in recs:
        axA.scatter(av, cost, s=95, color=S.POLICY_COLORS[short], marker=S.CITY_MARKERS[slug],
                    edgecolor='white', linewidth=0.8, zorder=3)
    axA.set_xscale('log')
    axA.set_yscale('log')
    axA.set_xlabel('Avoided deaths over 25 years')
    axA.set_ylabel('Present-value cost (€)')
    axA.set_title('(a) Cost-effectiveness frontier')
    pol_h = [Line2D([], [], marker='o', ls='', color=S.POLICY_COLORS[s], label=s) for s in ('Trees', 'AC', 'EWS')]
    city_h = [Line2D([], [], marker=S.CITY_MARKERS[c.slug], ls='', color=S.INK, label=S.CITY_NAMES[c.slug]) for c in cos]
    leg = axA.legend(handles=pol_h, title='Policy', loc='upper left', fontsize=9)
    axA.add_artist(leg)
    axA.legend(handles=city_h, title='City', loc='lower right', fontsize=9)
    cities = [c.slug for c in cos]
    x = np.arange(len(cities))
    width = 0.26
    for i, short in enumerate(('Trees', 'AC', 'EWS')):
        vals = [next((cpd for sl, sh, _, _, cpd in recs if sl == c and sh == short), np.nan) for c in cities]
        axB.bar(x + (i - 1) * width, vals, width=width, color=S.POLICY_COLORS[short], label=short)
    axB.set_yscale('log')
    axB.set_xticks(x)
    axB.set_xticklabels([S.CITY_NAMES[c] for c in cities])
    axB.set_ylabel('Cost per avoided death (€)')
    axB.set_title('(b) Cost per avoided death')
    axB.legend(title='Policy', fontsize=9)
    return S.save_fig(fig, out_dir, 'fig_cba_dashboard')


FIGURES = {
    'hazard_t2m_maps': fig_hazard_t2m_maps,
    'exposure_population': fig_exposure_population,
    'svi_components': fig_svi_components,
    'ac_penetration': fig_ac_penetration,
    'policy_levers': fig_policy_levers,
    'cba_dashboard': fig_cba_dashboard,
}
