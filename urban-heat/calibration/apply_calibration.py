"""
Apply the per-country calibration (calibration_params.csv) onto the 40_cities configs.
"""
import argparse, csv, os, re, subprocess, sys, difflib

REPO_DEFAULT = "/Users/armandeaboudrar-meda/Desktop/CMCC/URBADAPT-HEAT"

def git_show(repo, ref, path):
    return subprocess.run(["git","-C",repo,"show",f"{ref}:{path}"],
                          capture_output=True, text=True, check=True).stdout

def set_value(text, section, key, value, comment):
    """Replace `  key: <val>[ # old]` -> `  key: value   # comment`, ONLY inside top-level `section`.
       Returns (new_text, changed_bool). Preserves indentation. Leaves the rest of the file intact."""
    lines = text.split("\n")
    cur = None
    top = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(#.*)?$")
    kre = re.compile(rf"^(\s*){re.escape(key)}:\s*\S.*$")
    changed = False
    for i, ln in enumerate(lines):
        m = top.match(ln)
        if m: cur = m.group(1)
        if cur == section and kre.match(ln):
            indent = re.match(r"^(\s*)", ln).group(1)
            c = f"   # {comment}" if comment else ""
            lines[i] = f"{indent}{key}: {value}{c}"
            changed = True
            break
    return "\n".join(lines), changed

def ensure_income_source(text, value="observed"):
    """Insert `  source: <value>` as first child of `income:` if no `source:` already there.
       value comes from income_source_inventory.csv (observed | emulator)."""
    if value == "emulator":
        comment = "observed | emulator -- EMULATOR: still needs an emulator: block + generated <city>_p_inc.csv (repo-relative path)"
    else:
        comment = "observed | emulator (NB05 income source; see cityheat.income_source)"
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if re.match(r"^income:\s*(#.*)?$", ln):
            # scan the income block for an existing `source:`
            j = i + 1
            while j < len(lines) and (lines[j].startswith("  ") or lines[j].strip()=="" ):
                if re.match(r"^\s+source:\s", lines[j]):
                    return text, False
                # stop at next top-level key
                if re.match(r"^[A-Za-z_]", lines[j]): break
                j += 1
            lines.insert(i+1, f"  source: {value}          # {comment}")
            return "\n".join(lines), True
    return text, False

def edits_for(row, ac_basis, tree_ctx, set_fixed):
    cap_user = row[f"capex_per_user_{ac_basis}"]
    base_note = "€743 2018 EU base (product-only)" if ac_basis=="product" else "€1543 2018 installed (product+€800)"
    tcap = row[f"capex_per_tree_{tree_ctx}"]; tom = row[f"om_per_tree_{tree_ctx}"]
    tnote = "sealed/street shade" if tree_ctx=="street" else "park/low-engineering"
    # EWS 3-tier taxonomy (ews_taxonomy_assumptions Definitions v4): country -> tier -> setup CAPEX
    _ews_tier = {"IT":1,"FR":1,"ES":1,"PT":1,                                   # Tier1 epidemiological/mature -> 0
                 "DE":2,"NL":2,"BE":2,"AT":2,"HU":2,"RO":2,"SE":2,"FI":2,        # Tier2 meteorological/intermediate
                 "GR":3,"HR":3,"SI":3,"BG":3,"SK":3,"CZ":3,"PL":3,"DK":3,"IE":3,"EE":3,"LV":3,"LT":3}  # Tier3 traffic-light/weak -> full
    _ews_capex = {1: 0, 2: 75000, 3: 150000}   # 0 / intermediate(~half, expert) / full (~$150k, Ebi 2004 via Takacs)
    _ews_tname = {1: "epidemiological/mature", 2: "meteorological/intermediate", 3: "traffic-light/weak"}
    E = [
        ("ac","capex_per_user",   cap_user, f"{base_note} x PLI {row['PLI']}/100 [CALIB_AC S5]"),
        ("ac","maint_rate",       0.04,     "prEN 15459:2006 via Lot-10: 4%/yr of purchase+install [CALIB_AC S3]"),
        ("ac","lifetime_years",   12,       "Lot-10 Task4 / 2018 review; UQ range 9-16 [CALIB_AC S3]"),
        ("trees","capex_per_tree_eur",        tcap, f"{tnote} base x PLI {row['PLI']}/100 [D5.1/Prague; CALIB_AC S7]"),
        ("trees","om_per_tree_per_year_eur",  tom,  f"{tnote} O&M base x PLI/100 [D5.1/Vogt; CALIB_AC S7]"),
        ("ews","opex_per_warning_day_basic",       row["opex_basic"],       f"Chiabai 2018 €7800 Madrid x PLI/PLI_ES [CALIB_EWS S3]"),
        ("ews","opex_per_warning_day_enhanced",    row["opex_enhanced"],    f"Chiabai €14000 x PLI/PLI_ES [CALIB_EWS S3]"),
        ("ews","opex_per_warning_day_incremental", row["opex_incremental"], f"Chiabai increment €6200 x PLI/PLI_ES [CALIB_EWS S3]"),
        ("ews","usd_per_capita_per_day", 0.014, "Pavanello & Valenti 2025 (WP 35.2025) via Ebi 2004 [CALIB_EWS S6]"),
    ]
    # opex_annual_fixed: decision finalised -> always write Rao 2025 standing HHWS cost (€200k/yr, PLI-scaled)
    if row.get("opex_annual_fixed_if_enabled"):
        E.append(("ews","opex_annual_fixed", row["opex_annual_fixed_if_enabled"], "Rao 2025 standing HHWS cost €200k/yr x PLI/PLI_ES [CALIB_EWS S5]"))
    # ramp already 3 / 0.10 in config -> re-write with the Pavanello Fig 4(c) citation
    E.append(("ews","ramp_years", 3, "Pavanello & Valenti 2025 Fig 4(c): HHWWS effect emerges after ~yr3 [CALIB_EWS S6]"))
    E.append(("ews","ramp_initial_efficacy", 0.10, "Pavanello Fig 4(c): near-zero yr0-3 then ramps [CALIB_EWS S6]"))
    # new_build_vulnerability: constant EU-wide (EPBD nZEB convergence + age already differentiated in the model)
    E.append(("vulnerability","new_build_vulnerability", 0.15, "constant EU-wide: EPBD nZEB (all new builds >=2021) + age already differentiated; nZEB overheating = UQ sensitivity [CALIB]"))
    if row.get("retrofit_rate_per_year"):   # under vulnerability: -> dynamic: -> thermal_projection: (key unique per file)
        E.append(("vulnerability","retrofit_rate_per_year", row["retrofit_rate_per_year"],
                  "EC 2019 weighted renovation rate (Table2 rate x Table4 savings; winter-PE proxy) [calibration/retrofit_source_ec2019.csv]"))
    if row.get("pct_reduction_per_gvi_point"):   # under electricity_feedback: (key unique per file)
        ph = str(row.get("pct_gvi_is_placeholder","")).lower() in ("1","true","yes")
        cm = ("PLACEHOLDER = flat 0.008 (Falchetta 2026 Fig5 @~30C); recompute per-city from JJA-Tmax once "
              "city_summer_tmax.csv is filled [calibration/gen_gvi_reduction.py]") if ph else \
             "Falchetta 2026 Fig5 anchors interpolated @ city JJA-Tmax [calibration/gen_gvi_reduction.py]"
        E.append(("electricity_feedback","pct_reduction_per_gvi_point", row["pct_reduction_per_gvi_point"], cm))
    _tier = _ews_tier.get(row["country"])
    if _tier:
        E.append(("ews","capex_setup", _ews_capex[_tier],
                  f"3-tier EWS taxonomy Tier {_tier} ({_ews_tname[_tier]}): 0/75k/150k setup CAPEX; full=$150k Ebi2004 via Takacs [ews_taxonomy_v4]"))
    return E

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO_DEFAULT)
    ap.add_argument("--ref", default="origin/40_cities_implementation", help="git ref to read base configs from")
    ap.add_argument("--csv", default=os.path.join(os.path.dirname(__file__), "calibration_params.csv"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "preview_configs"))
    ap.add_argument("--inventory", default=os.path.join(os.path.dirname(__file__), "income_source_inventory.csv"),
                    help="per-city income source (observed|emulator) from the IncomeDatabase; flat 'observed' if absent")
    ap.add_argument("--ac-basis", choices=["product","installed"], default="product")
    ap.add_argument("--tree-context", choices=["street","park"], default="street")
    ap.add_argument("--set-opex-fixed", action="store_true", help="also write opex_annual_fixed (else leave config's 0)")
    ap.add_argument("--only", default="", help="comma list of cities (default all in CSV)")
    ap.add_argument("--diff", action="store_true", help="print a unified diff per city")
    args = ap.parse_args()

    rows = {r["city"]: r for r in csv.DictReader(open(args.csv))}
    inv = {}
    if os.path.exists(args.inventory):
        inv = {r["city"]: r["income_source"] for r in csv.DictReader(open(args.inventory))}
    only = [c.strip() for c in args.only.split(",") if c.strip()] or list(rows)
    os.makedirs(args.out, exist_ok=True)
    n_obs = sum(1 for c in only if inv.get(c, "observed") == "observed")
    print(f"basis: AC={args.ac_basis}  tree={args.tree_context}  opex_fixed={'on' if args.set_opex_fixed else 'left as config'}"
          f"  | income: {n_obs} observed / {len(only)-n_obs} emulator\n")
    for city in only:
        row = rows[city]; path = f"urban-heat/configs/{city}.yml"
        try:
            orig = git_show(args.repo, args.ref, path)
        except subprocess.CalledProcessError:
            print(f"  {city}: SKIP (not in {args.ref})"); continue
        text = orig; applied = []
        for section,key,val,comment in edits_for(row, args.ac_basis, args.tree_context, args.set_opex_fixed):
            text, ch = set_value(text, section, key, val, comment)
            applied.append(f"{section}.{key}={val}" if ch else f"!{section}.{key}(missing)")
        src = inv.get(city, "observed")
        text, inc = ensure_income_source(text, src)
        if inc: applied.append(f"income.source={src}")
        open(os.path.join(args.out, f"{city}.yml"), "w").write(text)
        miss = [a for a in applied if a.startswith("!")]
        print(f"  {city:12s} {len([a for a in applied if not a.startswith('!')])} edits" + (f"  MISSING:{miss}" if miss else ""))
        if args.diff:
            for dl in difflib.unified_diff(orig.split("\n"), text.split("\n"), f"a/{city}", f"b/{city}", lineterm=""):
                if dl.startswith(("+","-")) and not dl.startswith(("+++","---")): print("     "+dl)
    print(f"\npreviews -> {args.out}")

if __name__ == "__main__":
    main()
