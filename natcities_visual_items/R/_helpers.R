# =============================================================================
# _helpers.R  --  shared utilities for the Nature Cities visual items
# =============================================================================
# Sourced by every fig*/tab*/si* script. Provides:
#   - path discovery (repo root, outputs base, output dirs)
#   - multi-city discovery (auto-detects cities with completed `tables/`)
#   - safe readers that return NULL instead of erroring on missing inputs
#   - a shared ggplot theme + fixed pathway palette
#   - save_item(): writes both PDF and PNG with consistent sizing
# -----------------------------------------------------------------------------

suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(tidyr)
  library(readr)
  library(jsonlite)
  library(stringr)
  library(forcats)
  library(scales)
  library(patchwork)
})

# ---- Paths ------------------------------------------------------------------

# Find the repo root by walking up until we see the outputs tree.
find_repo_root <- function(start = getwd()) {
  marker <- file.path("urban-heat", "outputs_variants")
  d <- normalizePath(start, winslash = "/", mustWork = FALSE)
  for (i in 1:10) {
    if (dir.exists(file.path(d, marker))) return(d)
    parent <- dirname(d)
    if (identical(parent, d)) break
    d <- parent
  }
  # Fallback: hard default for this machine/checkout.
  "C:/Users/giaco/Documents/Github/URBADAPT-HEAT"
}

REPO_ROOT   <- find_repo_root()
VARIANT     <- Sys.getenv("NATCITIES_VARIANT", "masselot_main_agnostic")

# The results tree lives outside the repo, in the Drive `juno_pull` sync of the
# cluster runs. As of 2026-09-07 the current run is `n128_results` (n=128
# uncertainty draws); it supersedes the `outputs_variants/masselot_main_agnostic`
# tree, which Drive now keeps as `outputs_variants_pre_fix_aug7/`.
# Resolution order: NATCITIES_OUTPUTS_BASE -> the Drive default -> in-repo tree.
JUNO_PULL <- "G:/Il mio Drive/adaptation_infrastructure_database/juno_pull"
DEFAULT_OUTPUTS_BASE <- file.path(JUNO_PULL, "n128_results")
OUTPUTS_BASE <- {
  ob <- Sys.getenv("NATCITIES_OUTPUTS_BASE", "")
  if (nzchar(ob)) ob
  else if (dir.exists(DEFAULT_OUTPUTS_BASE)) DEFAULT_OUTPUTS_BASE
  else file.path(REPO_ROOT, "urban-heat", "outputs_variants", VARIANT)
}

# ---- Auxiliary tree: the interim companion of the same run -------------------
# `n128_results` is a slimmed export: tables/, figures/, emulator/ and
# lcz_masked_fua.tif, but no interim/ or hazard/. Six inputs read from those two
# (baseline daily T2M, the LCZ->cooling coefficient bridge, district geometry,
# the vulnerability-quintile deaths, the greening-ambition sensitivity, and the
# per-IF-family baseline deaths), so they resolve against an auxiliary tree,
# laid out as <city>/interim/<file>, when absent from the primary one.
#
# AUX_BASE must be the interim companion of *the same run* as OUTPUTS_BASE.
# `n128_interim_for_figures` is exactly that, so model outputs can cross over
# freely. Pointed at a different run it would produce figures that silently mix
# runs -- internally inconsistent and undetectable downstream -- so the resolved
# path is announced up front, every borrowed file is listed by aux_report(), and
# a city-set mismatch against the primary tree is flagged as a warning.
AUX_BASE <- {
  ab <- Sys.getenv("NATCITIES_AUX_BASE", "")
  if (nzchar(ab)) ab else file.path(JUNO_PULL, "n128_interim_for_figures")
}

.AUX_USED <- new.env(parent = emptyenv())

# Path in the auxiliary tree, or NA if it is unusable or absent.
.aux_path <- function(city, parts, where) {
  if (!length(parts)) return(NA_character_)
  if (!nzchar(AUX_BASE) || !dir.exists(AUX_BASE)) return(NA_character_)
  p <- do.call(file.path, c(list(AUX_BASE, city, where), as.list(parts)))
  if (!file.exists(p)) return(NA_character_)
  assign(parts[[length(parts)]], TRUE, envir = .AUX_USED)
  p
}

# Report, once per build, how many inputs came from the auxiliary tree. The file
# list is long and uninformative once it is the expected 6-per-city, so print the
# per-pattern tally instead and only name outright gaps.
aux_report <- function() {
  k <- ls(.AUX_USED)
  if (!length(k)) return(invisible(NULL))
  message(sprintf("[inputs] %d input(s) resolved from the interim companion tree\n[inputs]   %s",
                  length(k), AUX_BASE))
}

VIS_ROOT    <- file.path(REPO_ROOT, "natcities_visual_items")
# Output dirs are env-overridable so an alternative run (e.g. a sensitivity
# variant) can write elsewhere without clobbering the main figures/tables.
FIG_DIR     <- Sys.getenv("NATCITIES_FIG_DIR", file.path(VIS_ROOT, "figures"))
TAB_DIR     <- Sys.getenv("NATCITIES_TAB_DIR", file.path(VIS_ROOT, "tables"))
dir.create(FIG_DIR, showWarnings = FALSE, recursive = TRUE)
dir.create(TAB_DIR, showWarnings = FALSE, recursive = TRUE)

# State the resolved input tree up front. Several stale trees are reachable (the
# in-repo 2-city run, the pre-fix Drive export), so a wrong resolution would
# silently render the wrong paper. Printing the path + city count makes that
# impossible to miss.
.announce_source <- function() {
  n <- length(discover_cities(require_tables = TRUE))
  message(sprintf("[inputs] %s\n[inputs] %d city(ies) with results%s",
                  OUTPUTS_BASE, n,
                  if (nzchar(Sys.getenv("NATCITIES_OUTPUTS_BASE")))
                    "  (via NATCITIES_OUTPUTS_BASE)"
                  else if (identical(OUTPUTS_BASE, DEFAULT_OUTPUTS_BASE))
                    "  (Drive juno_pull default)" else "  (in-repo default)"))
  # Flag the slimmed export explicitly: the absence of interim/ decides which
  # panels can be built at all, so it must not read as a per-city data gap.
  cs <- discover_cities(require_tables = TRUE)
  if (length(cs) && !any(dir.exists(file.path(OUTPUTS_BASE, cs, "interim")))) {
    if (!dir.exists(AUX_BASE)) {
      message("[inputs] this export has no interim/; dependent panels will skip",
              " (no companion tree)")
    } else {
      message(sprintf("[inputs] no interim/ here; resolved from the companion tree\n[inputs]   %s",
                      AUX_BASE))
      # A city present in one tree but not the other means the two are not the
      # same run, or one of them is a partial pull. Either way the figures would
      # quietly rest on two different runs, so say so.
      ac <- list.dirs(AUX_BASE, full.names = FALSE, recursive = FALSE)
      ac <- ac[ac != "" & !grepl(DRIVE_CONFLICT_RE, ac)]
      only_p <- setdiff(cs, ac)
      only_a <- setdiff(ac, cs)
      if (length(only_p))
        message(sprintf("[inputs] [warn] %d city(ies) have no interim companion: %s",
                        length(only_p), paste(only_p, collapse = ", ")))
      if (length(only_a))
        message(sprintf("[inputs] [note] %d interim folder(s) with no results: %s",
                        length(only_a), paste(only_a, collapse = ", ")))
    }
  }
}

# ---- City discovery ---------------------------------------------------------

# Google Drive resolves a sync conflict by keeping BOTH copies, named
# "<city> (1)" or "<city> 2". Left in, they would enter the figures as extra
# cities and double-count one real one, so drop them at discovery.
DRIVE_CONFLICT_RE <- "( \\(\\d+\\)| \\d+)$"

# A city "counts" if its output folder has a non-empty tables/ subfolder.
discover_cities <- function(require_tables = TRUE) {
  if (!dir.exists(OUTPUTS_BASE)) return(character(0))
  cand <- list.dirs(OUTPUTS_BASE, full.names = FALSE, recursive = FALSE)
  cand <- cand[cand != ""]
  dup <- grepl(DRIVE_CONFLICT_RE, cand)
  if (any(dup)) {
    message(sprintf("  [note] ignoring %d Drive sync-conflict folder(s): %s",
                    sum(dup), paste(cand[dup], collapse = ", ")))
    cand <- cand[!dup]
  }
  if (require_tables) {
    cand <- cand[vapply(cand, function(c) {
      td <- file.path(OUTPUTS_BASE, c, "tables")
      dir.exists(td) && length(list.files(td, pattern = "\\.csv$")) > 0
    }, logical(1))]
  }
  sort(cand)
}

# Pretty display name from slug: "milan" -> "Milan".
city_label <- function(slug) {
  tools::toTitleCase(gsub("[-_]", " ", slug))
}

.announce_source()

# ---- Safe readers -----------------------------------------------------------
# Each takes a city slug + a file basename; returns NULL (with a note) if absent.

city_path <- function(city, ..., where = c("tables", "root", "figures", "interim",
                                          "hazard")) {
  where <- match.arg(where)
  base <- switch(where,
                 tables  = file.path(OUTPUTS_BASE, city, "tables"),
                 figures = file.path(OUTPUTS_BASE, city, "figures"),
                 interim = file.path(OUTPUTS_BASE, city, "interim"),
                 hazard  = file.path(OUTPUTS_BASE, city, "hazard"),
                 root    = file.path(OUTPUTS_BASE, city))
  parts <- list(...)
  p <- do.call(file.path, c(list(base), parts))
  # interim/ and hazard/ are absent from the current slimmed export; fall back to
  # the auxiliary tree for the allow-listed run-invariant inputs only.
  if (where %in% c("interim", "hazard") && !file.exists(p)) {
    a <- .aux_path(city, parts, where)
    if (!is.na(a)) return(a)
  }
  p
}

# List inputs matching `pattern` in a city's interim/ or hazard/ folder, in the
# primary tree and then the auxiliary one. Pattern-based discovery is needed
# where the filename is country-specific (the district geometry is named
# circoscrizione / kerulet / stadsdeel / ... per city).
city_files <- function(city, pattern, where = "interim") {
  f <- character(0)
  d <- file.path(OUTPUTS_BASE, city, where)
  if (dir.exists(d)) f <- list.files(d, pattern = pattern, full.names = TRUE)
  if (!length(f) && nzchar(AUX_BASE)) {
    da <- file.path(AUX_BASE, city, where)
    if (dir.exists(da)) {
      cand <- list.files(da, pattern = pattern, full.names = TRUE)
      if (length(cand)) {
        for (b in basename(cand)) assign(b, TRUE, envir = .AUX_USED)
        f <- cand
      }
    }
  }
  f
}

read_city_csv <- function(city, file, where = "tables", quiet = FALSE) {
  p <- city_path(city, file, where = where)
  if (!file.exists(p)) {
    if (!quiet) message(sprintf("  [skip] %s: missing %s", city, file))
    return(NULL)
  }
  suppressWarnings(readr::read_csv(p, show_col_types = FALSE, progress = FALSE))
}

read_city_json <- function(city, file, where = "tables", quiet = FALSE) {
  p <- city_path(city, file, where = where)
  if (!file.exists(p)) {
    if (!quiet) message(sprintf("  [skip] %s: missing %s", city, file))
    return(NULL)
  }
  # Python's json.dump writes bare Infinity/-Infinity/NaN, which are not legal
  # JSON and make jsonlite abort on the whole file (dropping the city silently).
  # These appear where a ratio divides by zero -- e.g. cost-per-death for a city
  # whose tree pathway avoids 0 deaths -- so map them to null/NA and carry on.
  txt <- readr::read_file(p)
  if (grepl("(?<![\"\\w])-?(Infinity|NaN)(?![\"\\w])", txt, perl = TRUE)) {
    if (!quiet) message(sprintf("  [note] %s: %s has non-finite JSON literals -> NA",
                                city, file))
    txt <- gsub("(?<![\"\\w])-?(Infinity|NaN)(?![\"\\w])", "null", txt, perl = TRUE)
  }
  jsonlite::fromJSON(txt, simplifyVector = TRUE)
}

# Bind a per-city extractor over all cities, tagging with `city`/`city_label`.
# `fun(city)` should return a data.frame or NULL; NULLs are dropped.
gather_cities <- function(cities, fun) {
  out <- lapply(cities, function(c) {
    df <- tryCatch(fun(c), error = function(e) {
      message(sprintf("  [error] %s: %s", c, conditionMessage(e))); NULL
    })
    if (is.null(df) || !nrow(df)) return(NULL)
    df$city <- c
    df$city_label <- city_label(c)
    df
  })
  out <- Filter(Negate(is.null), out)
  if (!length(out)) return(NULL)
  dplyr::bind_rows(out)
}

# ---- Theme & palette --------------------------------------------------------

# Fixed pathway colours reused across every figure for a coherent look.
PATHWAY_COLORS <- c(
  "Trees" = "#2E7D32",   # green  (public, hazard)
  "AC"    = "#1565C0",   # blue   (private, exposure)
  "EWS"   = "#E65100",   # orange (public, behaviour)
  "Baseline" = "#616161",
  "Portfolio" = "#6A1B9A"
)
BEARER_COLORS <- c("Public" = "#00695C", "Private" = "#C62828")

# Canonicalise the many policy label variants to a short pathway key.
pathway_key <- function(x) {
  x <- tolower(as.character(x))
  dplyr::case_when(
    str_detect(x, "tree|veg")            ~ "Trees",
    str_detect(x, "ews|warning")         ~ "EWS",
    str_detect(x, "ac|air.?cond")        ~ "AC",
    TRUE                                 ~ str_to_title(x)
  )
}

PATHWAY_LEVELS <- c("Trees", "AC", "EWS")

# Coerce to a single finite number, or NA. Guards every JSON/CSV field that can
# arrive absent, empty, or non-finite (a ratio over zero avoided deaths).
n1 <- function(x) {
  x <- suppressWarnings(as.numeric(x))
  if (length(x) != 1 || !is.finite(x)) NA_real_ else x
}

# ---- Canonical pathway rows -------------------------------------------------
# <city>_policy_effectiveness.csv carries several rows per pathway and the EWS
# row is labelled with whichever attribution wording that city's run produced --
# "EWS (marginal)" / "(counterfactual)" / "(intermediate)", split 14/14/12 across
# the 40 cities. A fixed label whitelist therefore silently dropped the EWS
# pathway for 26 cities. The three wordings carry the SAME number (each equals
# that city's "EWS (central)" row in <city>_cea_summary.csv, verified ratio 1.000
# for all 40), so they pool safely; `policy_variant` keeps the wording auditable.
#
# Within AC we take the NET row -- net of the waste-heat feedback -- as the
# headline, since the gross figure credits AC with lives its own waste heat
# takes back.
canonical_effectiveness <- function(city, ac = c("NET", "GROSS")) {
  ac <- match.arg(ac)
  d <- read_city_csv(city, sprintf("%s_policy_effectiveness.csv", city), quiet = TRUE)
  if (is.null(d) || !"policy" %in% names(d)) return(NULL)
  d$pathway <- pathway_key(d$policy)
  pick <- function(pw, rx) {
    r <- d[d$pathway == pw & grepl(rx, d$policy, ignore.case = TRUE), , drop = FALSE]
    if (!nrow(r)) return(NULL)
    r[1, , drop = FALSE]
  }
  out <- dplyr::bind_rows(
    pick("Trees", "uniform"),
    pick("AC", sprintf("\\(%s\\)", ac)),
    pick("EWS", ""))          # any EWS attribution wording
  if (is.null(out) || !nrow(out)) return(NULL)
  out$policy_variant <- sub("^[^(]*\\(?([^)]*)\\)?$", "\\1", out$policy)
  out[, c("pathway", "policy", "policy_variant", "avoided_deaths_25y",
          "pct_reduction", "deaths_per_100k_year")]
}

# <city>_cea_summary.csv is the one table whose policy labels are uniform across
# all 40 cities: Trees (base O&M) / Trees (5x O&M) / AC (GROSS) / AC (NET) /
# AC (NET + trees) / EWS (central). Preferred source for cost-effectiveness.
CEA_HEADLINE <- c("Trees (base O&M)" = "Trees", "AC (NET)" = "AC",
                  "EWS (central)" = "EWS")

# Below this many avoided deaths over 25 years a pathway is treated as having no
# measurable benefit in that city (Madrid and Sevilla greening), so its cost per
# death is reported as "no measurable benefit" instead of a 1e10 euro artefact.
NO_BENEFIT_DEATHS <- 0.5

# Below this share of summer LCZ-months with a non-zero greening-cooling
# coefficient, a city's tree-pathway numbers are driven by how complete its
# coefficient bridge is rather than by the city. Used to mark (not drop) such
# cities wherever a greening quantity is plotted. See the README.
COEF_MIN_PCT <- 25

read_cea <- function(city, headline_only = TRUE) {
  d <- read_city_csv(city, sprintf("%s_cea_summary.csv", city), quiet = TRUE)
  if (is.null(d) || !"Policy" %in% names(d)) return(NULL)
  if (headline_only) {
    d <- d[d$Policy %in% names(CEA_HEADLINE), , drop = FALSE]
    if (!nrow(d)) return(NULL)
    d$pathway <- unname(CEA_HEADLINE[d$Policy])
  } else {
    d$pathway <- pathway_key(d$Policy)
  }
  # A pathway that avoids ~0 deaths yields Inf (or an absurd 1e10) euros per
  # death. Flag it rather than plotting or tabulating a meaningless number.
  d$cost_per_death <- suppressWarnings(as.numeric(d$Cost_per_Death_EUR))
  d$no_benefit <- !is.finite(d$cost_per_death) |
    suppressWarnings(as.numeric(d$Avoided_Deaths_25y)) < NO_BENEFIT_DEATHS
  d$cost_per_death[d$no_benefit] <- NA_real_
  d
}

# ---- CBA summary ------------------------------------------------------------
# The per-city cost-benefit JSON, flattened to the fields the figures use. Every
# field goes through n1(), so a city missing one loses that column only, not its
# whole row (which would drop the city from the figure).
read_cba <- function(city) {
  j <- read_city_json(city, sprintf("%s_cba_summary.json", city), quiet = TRUE)
  if (is.null(j)) return(NULL)
  wh <- j$vegetation_feedbacks$lambda_y_waste_heat
  data.frame(
    trees_alone   = n1(j$benefits$trees$avoided_deaths_25y),
    trees_ontop   = n1(j$benefits$trees$on_top_of_ac_25y),
    ac_gross      = n1(j$benefits$ac$gross_25y),
    ac_net        = n1(j$benefits$ac$net_25y),
    ac_penalty    = n1(j$benefits$ac$waste_heat_penalty_25y),
    ac_net_trees  = n1(j$benefits$ac_with_trees_interaction$net_25y),
    penalty_trees = n1(wh$penalty_with_trees_25y),
    ews_avoided   = n1(j$benefits$ews$avoided_deaths_25y),
    trees_pv      = n1(j$costs$trees$pv_total_base),
    ac_pv         = n1(j$costs$ac$pv_total),
    ews_pv        = n1(j$costs$ews$pv_total),
    delta_gvi     = n1(j$parameters$delta_gvi),
    discount_rate = n1(j$parameters$discount_rate))
}

# ---- Scales -----------------------------------------------------------------

# Euro axis on a log10 scale, labelled in readable magnitudes (10k, 1M, 100M).
eur_log_scale <- function(name, ...) {
  scale_y_log10(name = name, labels = function(v)
    ifelse(is.na(v), "",
    ifelse(v >= 1e6, paste0(scales::comma(v / 1e6, accuracy = 1), "M"),
    ifelse(v >= 1e3, paste0(scales::comma(v / 1e3, accuracy = 1), "k"),
           scales::comma(v, accuracy = 1)))), ...)
}

# Shared cluster colour scale, so every panel legend is identical and the
# collected patchwork guide stays a single entry.
cluster_scale <- function(name = "Climate cluster") {
  scale_color_manual(values = CLUSTER_COLORS, name = name, drop = FALSE,
                     guide = guide_legend(override.aes =
                       list(size = 3, alpha = 1, shape = 16, linetype = 0)))
}

# Climate-cluster palette (hot -> cool) and canonical ordering.
CLUSTER_COLORS <- c("Hot" = "#C62828", "Temperate" = "#F9A825", "Cool" = "#1565C0")
CLUSTER_LEVELS <- c("Hot", "Temperate", "Cool")

# Load the cached city metadata (climate metrics + clusters). Run 00_city_meta.R
# first if it is missing.
load_city_meta <- function() {
  p <- file.path(TAB_DIR, "city_metadata.csv")
  if (!file.exists(p)) {
    message("city_metadata.csv not found -- run 00_city_meta.R first.")
    return(NULL)
  }
  m <- suppressWarnings(readr::read_csv(p, show_col_types = FALSE))
  m$climate_cluster <- factor(m$climate_cluster, levels = CLUSTER_LEVELS)
  m
}

# Left-join climate metadata onto a per-city data frame (must have `city`).
attach_meta <- function(df, meta = load_city_meta()) {
  if (is.null(meta) || is.null(df)) return(df)
  cols <- c("city", "country", "pop_k", "pop_k_model", "pop_k_config",
            "warmseason_mean_t2m", "hot_days", "climate_cluster", "is_exemplar",
            "coef_lst_coverage_pct",
            # LCZ morphology descriptors (external annotations, never inputs to
            # the climate clusters or the outcome-based archetypes).
            "lcz_compact_pct", "lcz_open_pct", "lcz_other_built_pct",
            "lcz_built_pct", "lcz_diversity")
  dplyr::left_join(df, meta[, intersect(cols, names(meta))], by = "city")
}

# City population (thousands) as used by the model itself: the sum of the
# per-region exposure population in <city>_policy_comparison.csv. This is the
# denominator the mortality figures are actually computed on, so it is the
# correct one for per-100k / per-capita normalisation -- the hand-entered
# `pop_k` in configs/<city>.yml is an approximation that is absent for some
# cities and off by >2x for others (e.g. porto, city-proper vs FUA).
city_pop_k_model <- function(city) {
  d <- read_city_csv(city, sprintf("%s_policy_comparison.csv", city), quiet = TRUE)
  if (is.null(d) || !"population" %in% names(d)) return(NA_real_)
  p <- sum(as.numeric(d$population), na.rm = TRUE) / 1000
  if (!is.finite(p) || p <= 0) NA_real_ else round(p, 1)
}

# Deaths per 100k using approximate city population (pop_k, thousands).
per_100k <- function(deaths, pop_k) deaths * 100 / pop_k

theme_natcities <- function(base_size = 11) {
  theme_minimal(base_size = base_size) +
    theme(
      plot.title      = element_text(face = "bold", size = rel(1.05)),
      plot.subtitle   = element_text(color = "grey30", size = rel(0.9)),
      plot.tag        = element_text(face = "bold", size = rel(1.2)),
      axis.title      = element_text(color = "grey20"),
      panel.grid.minor = element_blank(),
      panel.grid.major.x = element_blank(),
      legend.position = "bottom",
      legend.title    = element_text(size = rel(0.9)),
      strip.text      = element_text(face = "bold"),
      plot.margin     = margin(8, 10, 8, 8)
    )
}

# ---- Saving -----------------------------------------------------------------

# Writes <FIG_DIR>/<name>.pdf and .png. `plot` may be a ggplot or patchwork.
save_item <- function(plot, name, width = 8, height = 5, dpi = 300) {
  pdf_p <- file.path(FIG_DIR, paste0(name, ".pdf"))
  png_p <- file.path(FIG_DIR, paste0(name, ".png"))
  ggsave(pdf_p, plot, width = width, height = height, device = cairo_pdf)
  ggsave(png_p, plot, width = width, height = height, dpi = dpi, bg = "white")
  message(sprintf("  [saved] %s  (%.0fx%.0f)", name, width, height))
  invisible(c(pdf = pdf_p, png = png_p))
}

# Writes a data.frame as both CSV and an \input-able LaTeX table.
# A longtable that breaks across pages loses its column header on page 2 unless
# the head is declared with \\endfirsthead / \\endhead. xtable does not emit
# those, so duplicate the toprule/header/midrule block it did write.
.longtable_repeat_header <- function(path) {
  x <- readLines(path, warn = FALSE)
  i_top <- which(grepl("\\\\toprule", x))[1]
  i_mid <- which(grepl("\\\\midrule", x))[1]
  if (is.na(i_top) || is.na(i_mid) || i_mid <= i_top) return(invisible(FALSE))
  head_block <- x[i_top:i_mid]          # toprule, header row(s), midrule
  out <- append(x, c("\\endfirsthead", head_block, "\\endhead"),
                after = i_mid)
  writeLines(out, path)
  invisible(TRUE)
}
# `size` is passed straight to xtable (e.g. "\footnotesize"): the wide city
# tables overflow the text block at normal size, and a longtable cannot be
# scaled with \resizebox because it breaks across pages.

# `longtable = TRUE` emits a longtable instead of a tabular inside a float: a
# 40-row city table overflows a page and a float cannot break. The caller must
# then NOT wrap the input in a table environment; the longtable carries its own
# caption and label, exactly like tables/ews_taxonomy_table.
save_table <- function(df, name, caption = NULL, label = NULL,
                       digits = 2, align = NULL, longtable = FALSE,
                       size = NULL) {
  csv_p <- file.path(TAB_DIR, paste0(name, ".csv"))
  tex_p <- file.path(TAB_DIR, paste0(name, ".tex"))
  readr::write_csv(df, csv_p)
  if (requireNamespace("xtable", quietly = TRUE)) {
    # Escape LaTeX specials in headers (%, &, _, #) but keep utf8 chars like euro.
    latex_colname <- function(x) {
      x <- gsub("([%&#_])", "\\\\\\1", x)
      x
    }
    xt <- xtable::xtable(df, caption = caption, label = label, digits = digits,
                         align = align)
    print(xt, file = tex_p, include.rownames = FALSE, booktabs = TRUE,
          caption.placement = "top",
          sanitize.colnames.function = latex_colname,
          floating = !longtable,
          tabular.environment = if (longtable) "longtable" else "tabular",
          size = size)
    if (longtable) .longtable_repeat_header(tex_p)
  }
  message(sprintf("  [saved] %s.csv / .tex", name))
  invisible(c(csv = csv_p, tex = tex_p))
}

banner <- function(x) message("\n=== ", x, " ===")

# Pick the rows a reader would ask about: the n_high largest and n_low smallest
# values of `col`. Labelling all 40 cities in a panel this size is unreadable,
# but labelling only the top of the range hides the other tail, which in these
# figures is usually the more surprising end. Non-finite values are dropped, and
# the result is deduplicated on `city` so a city that is extreme on two criteria
# is not labelled twice.
standouts <- function(d, col, n_high = 3, n_low = 2) {
  if (is.null(d) || !nrow(d) || !col %in% names(d)) return(d[0, , drop = FALSE])
  x <- suppressWarnings(as.numeric(d[[col]]))
  d <- d[is.finite(x), , drop = FALSE]
  x <- x[is.finite(x)]
  if (!nrow(d)) return(d)
  i <- c(order(-x)[seq_len(min(n_high, length(x)))],
         order(x)[seq_len(min(n_low, length(x)))])
  out <- d[unique(i), , drop = FALSE]
  if ("city" %in% names(out)) out <- out[!duplicated(out$city), , drop = FALSE]
  out
}

# Shared label geom for the cross-city scatters. max.overlaps = Inf matters:
# ggrepel silently DROPS labels past the default of 10, so a panel would quietly
# lose the very cities it was meant to name.
repel_city <- function(data, size = 2.7) {
  ggrepel::geom_text_repel(data = data, aes(label = city_label), size = size,
                           color = "grey25", seed = 1, min.segment.length = 0,
                           box.padding = 0.35, point.padding = 0.2,
                           segment.color = "grey60", segment.size = 0.25,
                           max.overlaps = Inf, show.legend = FALSE)
}

# A panel with no usable input becomes an empty slot in the composite figure.
# Left silent that reads as a layout choice, so say out loud which panel went
# missing and why -- a dropped panel is a result the caption must not claim.
blank_panel <- function(tag, why) {
  message(sprintf("  [warn] panel %s left blank: %s", tag, why))
  patchwork::plot_spacer()
}
