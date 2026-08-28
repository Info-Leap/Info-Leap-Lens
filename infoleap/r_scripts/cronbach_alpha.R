#!/usr/bin/env Rscript
.libPaths(c(Sys.getenv("USERPROFILE"), .libPaths()))
# cronbach_alpha.R — reliability of brand imagery attribute ratings
# Args: <input_csv> <output_json>
# Input CSV cols: respondent_id, [attr1, attr2, ...] (binary 0/1 association)
# Output JSON: {"alpha": 0.82, "n_items": 15, "n_respondents": 420, "ci_low": 0.79, "ci_high": 0.85}

suppressPackageStartupMessages(library(jsonlite))

write_error <- function(msg, ...) {
  extras <- list(...)
  result <- c(list(error = msg), extras)
  write(toJSON(result, auto_unbox = TRUE), output_path)
  quit(status = 0)
}

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) stop("Usage: Rscript cronbach_alpha.R <input.csv> <output.json>")

input_path  <- args[1]
output_path <- args[2]

df <- tryCatch(read.csv(input_path, row.names = 1), error = function(e) NULL)
if (is.null(df)) write_error("Failed to read input CSV")

df <- df[, sapply(df, is.numeric), drop = FALSE]
df <- na.omit(df)

n_items <- ncol(df)
n_resp  <- nrow(df)

if (n_items < 2 || n_resp < 10) {
  write_error("Insufficient data", n_items = n_items, n_respondents = n_resp)
}

result <- tryCatch({
  item_vars  <- apply(df, 2, var)
  total_var  <- var(rowSums(df))

  if (total_var == 0) stop("Zero total variance — all items identical")

  alpha <- (n_items / (n_items - 1)) * (1 - sum(item_vars) / total_var)
  alpha <- max(0, min(1, alpha))

  se_alpha <- sqrt((2 * n_items * (1 - alpha)^2) / ((n_items - 1) * n_resp))
  ci_low   <- max(0, alpha - 1.96 * se_alpha)
  ci_high  <- min(1, alpha + 1.96 * se_alpha)

  interpretation <- if (alpha >= 0.90) "Excellent"
    else if (alpha >= 0.80) "Good"
    else if (alpha >= 0.70) "Acceptable"
    else if (alpha >= 0.60) "Questionable"
    else "Poor"

  list(
    alpha          = round(alpha, 4),
    ci_low         = round(ci_low, 4),
    ci_high        = round(ci_high, 4),
    n_items        = n_items,
    n_respondents  = n_resp,
    interpretation = interpretation
  )
}, error = function(e) {
  list(error = conditionMessage(e), n_items = n_items, n_respondents = n_resp)
})

write(toJSON(result, auto_unbox = TRUE), output_path)
cat("OK\n")
