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
OUTPUTS_BASE <- file.path(REPO_ROOT, "urban-heat", "outputs_variants", VARIANT)
VIS_ROOT    <- file.path(REPO_ROOT, "natcities_visual_items")
# Output dirs are env-overridable so a synthetic/planning run can write to a
# separate location without clobbering the real figures/tables (defaults unchanged).
FIG_DIR     <- Sys.getenv("NATCITIES_FIG_DIR", file.path(VIS_ROOT, "figures"))
TAB_DIR     <- Sys.getenv("NATCITIES_TAB_DIR", file.path(VIS_ROOT, "tables"))
dir.create(FIG_DIR, showWarnings = FALSE, recursive = TRUE)
dir.create(TAB_DIR, showWarnings = FALSE, recursive = TRUE)

# ---- City discovery ---------------------------------------------------------

# A city "counts" if its output folder has a non-empty tables/ subfolder.
discover_cities <- function(require_tables = TRUE) {
  if (!dir.exists(OUTPUTS_BASE)) return(character(0))
  cand <- list.dirs(OUTPUTS_BASE, full.names = FALSE, recursive = FALSE)
  cand <- cand[cand != ""]
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

# ---- Safe readers -----------------------------------------------------------
# Each takes a city slug + a file basename; returns NULL (with a note) if absent.

city_path <- function(city, ..., where = c("tables", "root", "figures")) {
  where <- match.arg(where)
  base <- switch(where,
                 tables  = file.path(OUTPUTS_BASE, city, "tables"),
                 figures = file.path(OUTPUTS_BASE, city, "figures"),
                 root    = file.path(OUTPUTS_BASE, city))
  file.path(base, ...)
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
  jsonlite::fromJSON(p, simplifyVector = TRUE)
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
  cols <- c("city", "country", "pop_k", "warmseason_mean_t2m", "hot_days",
            "climate_cluster", "is_exemplar")
  dplyr::left_join(df, meta[, intersect(cols, names(meta))], by = "city")
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
save_table <- function(df, name, caption = NULL, label = NULL,
                       digits = 2, align = NULL) {
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
          sanitize.colnames.function = latex_colname)
  }
  message(sprintf("  [saved] %s.csv / .tex", name))
  invisible(c(csv = csv_p, tex = tex_p))
}

banner <- function(x) message("\n=== ", x, " ===")
