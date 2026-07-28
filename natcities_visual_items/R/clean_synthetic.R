# =============================================================================
# clean_synthetic.R  --  remove everything build_synthetic.R produced.
# Deletes the synthetic variant dir + figures_synthetic/ + tables_synthetic/.
# The REAL variant, figures/ and tables/ are never touched. Run under PowerShell:
#   Rscript clean_synthetic.R
# =============================================================================

.d <- {
  a <- commandArgs(trailingOnly = FALSE)
  f <- sub("^--file=", "", a[grep("^--file=", a)])
  if (length(f)) dirname(normalizePath(f)) else getwd()
}
VIS  <- normalizePath(file.path(.d, ".."), winslash = "/")
REPO <- normalizePath(file.path(VIS, ".."), winslash = "/")
variant <- Sys.getenv("NATCITIES_VARIANT", "masselot_main_agnostic_synthetic")

if (identical(variant, "masselot_main_agnostic"))
  stop("Refusing to delete the REAL variant dir.")

targets <- c(
  file.path(REPO, "urban-heat", "outputs_variants", variant),
  file.path(VIS, "figures_synthetic"),
  file.path(VIS, "tables_synthetic"))
for (t in targets) {
  if (dir.exists(t)) {
    unlink(t, recursive = TRUE, force = TRUE)
    message(sprintf("  [removed] %s", t))
  } else message(sprintf("  [absent]  %s", t))
}
message("Synthetic artifacts cleaned.")
