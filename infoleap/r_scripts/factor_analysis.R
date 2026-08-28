#!/usr/bin/env Rscript
.libPaths(c(Sys.getenv("USERPROFILE"), .libPaths()))
# factor_analysis.R — EFA on brand imagery attributes (discover underlying dimensions)
# Args: <input_csv> <output_json>
# Input CSV: respondent_id, [attr1, attr2, ...] (binary 0/1 or 0–10 ratings)
# Output JSON: {
#   "n_factors": 3, "variance_explained": [0.28, 0.19, 0.14],
#   "factors": [
#     {"factor": 1, "name": "F1", "top_attrs": [{"attr": "innovative", "loading": 0.82}, ...]}
#   ]
# }

suppressPackageStartupMessages(library(jsonlite))

write_error <- function(msg, ...) {
  extras <- list(...)
  result <- c(list(error = msg), extras)
  write(toJSON(result, auto_unbox = TRUE), output_path)
  quit(status = 0)
}

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) stop("Usage: Rscript factor_analysis.R <input.csv> <output.json>")

input_path  <- args[1]
output_path <- args[2]

df <- tryCatch(read.csv(input_path, row.names = 1), error = function(e) NULL)
if (is.null(df)) write_error("Failed to read input CSV")

df <- df[, sapply(df, is.numeric), drop = FALSE]
df <- na.omit(df)

# Remove zero-variance columns (factanal fails on them)
df <- df[, apply(df, 2, var) > 0, drop = FALSE]

n_items <- ncol(df)
n_resp  <- nrow(df)

if (n_items < 4 || n_resp < 30) {
  write_error("Need ≥4 attributes and ≥30 respondents", n_items = n_items, n = n_resp)
}

result <- tryCatch({
  # Determine n_factors via parallel analysis heuristic: keep factors with eigenvalue > 1
  ev      <- eigen(cor(df))$values
  n_facts <- max(1, min(sum(ev > 1), floor(n_items / 3), 8))

  fa_fit       <- factanal(df, factors = n_facts, rotation = "varimax", scores = "none")
  loadings_mat <- fa_fit$loadings[,]

  var_exp <- colSums(loadings_mat^2) / n_items

  factors_out <- lapply(seq_len(n_facts), function(fi) {
    loads <- loadings_mat[, fi]
    ord   <- order(abs(loads), decreasing = TRUE)
    top_n <- min(6, length(loads))
    list(
      factor    = fi,
      name      = paste0("F", fi),
      variance  = round(var_exp[fi], 4),
      top_attrs = lapply(ord[seq_len(top_n)], function(ai) {
        list(attr = names(loads)[ai], loading = round(loads[ai], 3))
      })
    )
  })

  list(
    n_factors           = n_facts,
    n_items             = n_items,
    n_respondents       = n_resp,
    variance_explained  = round(var_exp, 4),
    total_var_explained = round(sum(var_exp), 4),
    factors             = factors_out
  )
}, error = function(e) {
  list(error = conditionMessage(e), n_items = n_items, n_respondents = n_resp)
})

write(toJSON(result, auto_unbox = TRUE), output_path)
cat("OK\n")
