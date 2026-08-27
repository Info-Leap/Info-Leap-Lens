#!/usr/bin/env Rscript
.libPaths(c(Sys.getenv("USERPROFILE"), .libPaths()))
# random_forest.R — regression random forest key-driver analysis (XLSTAT
# "Classification and regression random forests", Regression forest type).
# Args: <input_csv> <output_json>
# Input CSV: respondent_id, nps_score (numeric outcome), [attr1, attr2, ...]
# Output JSON: {
#   "oob_mse","oob_rmse","oob_r2","var_dv","ntree","mtry","n","n_attrs",
#   "missing_data_method","importance": [{"attribute","pct_inc_mse","inc_node_purity"}],
#   "oob_error_curve": [{"ntree","mse"}], "pred_vs_actual": {"x":[...],"y":[...]}
# }
#
# Missing-data note (transparency — see .regression_reference/AUDIT.md §9):
# XLSTAT imputes missing predictor cells with its proprietary "Nearest neighbor"
# method before growing the forest. R's randomForest has no equivalent built in;
# we use rfImpute-style na.roughfix (per-column median for numeric, mode for
# factor) instead — a documented methodology difference, not a bug. Combined
# with a different RNG stream, this means OOB R²/RMSE and variable-importance
# VALUES will not bit-match XLSTAT — only be in the same ballpark with a
# directionally similar importance ranking. Exact parity is not the target
# for this engine (unlike the OLS/logistic engines, which do match XLSTAT
# cell-for-cell).

suppressPackageStartupMessages({
  library(jsonlite)
  library(randomForest)
})

write_error <- function(msg, ...) {
  extras <- list(...)
  result <- c(list(error = msg), extras)
  write(toJSON(result, auto_unbox = TRUE), output_path)
  quit(status = 0)
}

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) stop("Usage: Rscript random_forest.R <input.csv> <output.json>")
input_path  <- args[1]
output_path <- args[2]

df <- tryCatch(read.csv(input_path), error = function(e) NULL)
if (is.null(df)) write_error("Failed to read input CSV")
if (!"nps_score" %in% colnames(df)) write_error("Input must have 'nps_score' column")

# 'X' = unnamed RangeIndex written by pandas to_csv(index=True); not a predictor.
attr_cols <- setdiff(colnames(df), c("X", "respondent_id", "nps_score"))
attr_cols <- attr_cols[sapply(df[attr_cols], function(x) var(x, na.rm = TRUE) > 0)]

if (length(attr_cols) < 1 || nrow(df) < 30) {
  write_error("Insufficient data", n = nrow(df), n_attrs = length(attr_cols))
}

n_before <- nrow(df)
missing_pct <- round(100 * sum(is.na(df[c(attr_cols, "nps_score")])) /
                        (nrow(df) * (length(attr_cols) + 1)), 2)

result <- tryCatch({
  model_df <- df[, c("nps_score", attr_cols)]
  # Drop rows missing the outcome itself — cannot impute the DV.
  model_df <- model_df[!is.na(model_df$nps_score), ]
  n_after_dv <- nrow(model_df)

  # Impute missing predictors (median/mode) — see header note re: XLSTAT's
  # Nearest-neighbor method being unavailable in randomForest.
  imputed_any <- any(is.na(model_df[attr_cols]))
  if (imputed_any) {
    model_df <- na.roughfix(model_df)
  }

  # R default for regression forests: floor(p/3). XLSTAT's workbook run used a
  # user-set Mtry=4 for its specific 29-predictor case — that's a parameter
  # choice, not a formula, so we use R's own default here rather than hardcode 4.
  mtry_val  <- max(1, floor(length(attr_cols) / 3))
  ntree_val <- 200
  set.seed(123456789)

  fit <- randomForest(nps_score ~ ., data = model_df, ntree = ntree_val,
                       mtry = mtry_val, importance = TRUE, keep.forest = TRUE)

  oob_mse  <- fit$mse[ntree_val]
  oob_rmse <- sqrt(oob_mse)
  var_dv   <- var(model_df$nps_score)
  oob_r2   <- 1 - oob_mse / var_dv

  imp <- importance(fit, type = 1, scale = FALSE)   # %IncMSE (raw, unscaled — matches XLSTAT's "Mean increase error")
  imp2 <- importance(fit, type = 2)                 # IncNodePurity
  imp_names <- rownames(imp)
  importance_list <- lapply(seq_along(imp_names), function(i) {
    list(attribute = imp_names[i],
         pct_inc_mse = round(as.numeric(imp[i, 1]), 4),
         inc_node_purity = round(as.numeric(imp2[i, 1]), 4))
  })
  importance_list <- importance_list[order(sapply(importance_list, function(x) x$pct_inc_mse), decreasing = TRUE)]

  # OOB error convergence curve — downsampled to <=100 points.
  n_pts <- ntree_val
  max_pts <- 100
  idx <- if (n_pts > max_pts) unique(round(seq(1, n_pts, length.out = max_pts))) else seq_len(n_pts)
  oob_curve <- lapply(idx, function(i) list(ntree = i, mse = round(fit$mse[i], 5)))

  # Predicted (OOB) vs actual — downsampled to <=300 points, sorted by actual.
  pred_vs_actual <- tryCatch({
    actual_y <- model_df$nps_score
    pred_y   <- fit$predicted  # OOB predictions
    ord      <- order(actual_y)
    n_p      <- length(actual_y)
    mp       <- 300
    pidx     <- if (n_p > mp) round(seq(1, n_p, length.out = mp)) else seq_len(n_p)
    list(x = round(as.numeric(actual_y[ord][pidx]), 4),
         y = round(as.numeric(pred_y[ord][pidx]), 4))
  }, error = function(e) NULL)

  # Correlation matrix of predictors (post-imputation) — same block XLSTAT shows
  # alongside the RF report. Skipped when p > 40.
  correlation_matrix <- tryCatch({
    if (length(attr_cols) >= 2 && length(attr_cols) <= 40) {
      cm <- cor(model_df[attr_cols], use = "pairwise.complete.obs")
      cm[] <- round(cm, 4)
      list(
        attributes = attr_cols,
        matrix = lapply(seq_len(nrow(cm)), function(i) as.list(unname(cm[i, ])))
      )
    } else NULL
  }, error = function(e) NULL)

  list(
    oob_mse              = round(oob_mse, 6),
    oob_rmse             = round(oob_rmse, 6),
    oob_r2                = round(oob_r2, 6),
    var_dv               = round(var_dv, 6),
    ntree                = ntree_val,
    mtry                 = mtry_val,
    n                    = n_after_dv,
    n_before             = n_before,
    n_attrs              = length(attr_cols),
    missing_pct          = missing_pct,
    missing_data_method  = if (imputed_any) "median/mode imputation (na.roughfix) — see AUDIT.md §9 for deviation from XLSTAT's Nearest-neighbor method" else "none (no missing values)",
    importance           = importance_list,
    oob_error_curve      = oob_curve,
    pred_vs_actual       = pred_vs_actual,
    correlation_matrix   = correlation_matrix
  )
}, error = function(e) {
  list(error = conditionMessage(e), n = nrow(df))
})

write(toJSON(result, auto_unbox = TRUE), output_path)
cat("OK\n")
