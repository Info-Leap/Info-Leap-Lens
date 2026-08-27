#!/usr/bin/env Rscript
.libPaths(c(Sys.getenv("USERPROFILE"), .libPaths()))
# driver_regression.R — linear regression: imagery attributes -> NPS
# Args: <input_csv> <output_json>
# Input CSV: respondent_id, nps_score, [attr1, attr2, ...] (binary 0/1 association per attribute)
# Output JSON: {
#   "r_squared": 0.42, "n": 420, "significant_drivers": [
#     {"attribute": "attr_name", "coef": 0.12, "t_stat": 3.2, "p_value": 0.001, "significant": true}
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
if (length(args) < 2) stop("Usage: Rscript driver_regression.R <input.csv> <output.json>")

input_path  <- args[1]
output_path <- args[2]

df <- tryCatch(read.csv(input_path), error = function(e) NULL)
if (is.null(df)) write_error("Failed to read input CSV")

if (!"nps_score" %in% colnames(df)) write_error("Input must have 'nps_score' column")

n_before <- nrow(df)
df <- na.omit(df)
n_after <- nrow(df)
# Blind listwise deletion can silently collapse the sample and produce a degenerate,
# meaningless fit. Flag it rather than returning a result that looks valid.
# See .regression_reference/AUDIT.md §3.
pct_dropped <- if (n_before > 0) round(100 * (n_before - n_after) / n_before, 1) else 0
if (pct_dropped > 20 && n_after > 0) {
  write_error(sprintf("%.1f%% of rows dropped for missing data (n=%d -> n=%d) — result would be unreliable.",
                       pct_dropped, n_before, n_after),
              n_before = n_before, n_after = n_after, pct_dropped = pct_dropped)
}
# 'X' = the unnamed RangeIndex written by pandas to_csv(index=True); 'respondent_id'
# is the real id. Neither is a predictor — exclude both.
attr_cols <- setdiff(colnames(df), c("X", "respondent_id", "nps_score"))
attr_cols <- attr_cols[sapply(df[attr_cols], function(x) var(x, na.rm = TRUE) > 0)]

if (length(attr_cols) < 1 || nrow(df) < 20) {
  write_error("Insufficient data", n = nrow(df), n_attrs = length(attr_cols))
}

result <- tryCatch({
  formula_str <- paste("nps_score ~", paste(paste0("`", attr_cols, "`"), collapse = " + "))
  fit <- lm(as.formula(formula_str), data = df)
  s   <- summary(fit)
  coefs_full <- as.data.frame(s$coefficients)
  intercept  <- round(coefs_full[1, "Estimate"], 4)
  coefs <- coefs_full[-1, ]  # drop intercept for the driver list

  # Standardised (beta) coefficients: b_i * sd(x_i) / sd(y) — makes drivers on
  # different scales comparable. |std beta| share = relative importance (XLSTAT).
  sd_y   <- sd(df[["nps_score"]], na.rm = TRUE)
  raw_names <- rownames(coefs)
  clean_names <- gsub("^`|`$", "", raw_names)
  std_betas <- sapply(seq_len(nrow(coefs)), function(i) {
    nm <- clean_names[i]
    sx <- if (nm %in% colnames(df)) sd(df[[nm]], na.rm = TRUE) else NA
    if (is.na(sx) || is.na(sd_y) || sd_y == 0) NA
    else coefs[i, "Estimate"] * sx / sd_y
  })
  abs_std <- abs(std_betas)
  tot_abs <- sum(abs_std, na.rm = TRUE)
  imp_pct <- if (tot_abs > 0) abs_std / tot_abs * 100 else rep(0, length(abs_std))

  drivers <- lapply(seq_len(nrow(coefs)), function(i) {
    est <- coefs[i, "Estimate"]; se <- coefs[i, "Std. Error"]
    list(
      attribute   = clean_names[i],
      coef        = round(est, 4),
      std_error   = round(se, 4),
      std_coef    = if (is.na(std_betas[i])) NULL else round(std_betas[i], 4),
      importance  = round(ifelse(is.na(imp_pct[i]), 0, imp_pct[i]), 2),
      t_stat      = round(coefs[i, "t value"], 3),
      p_value     = round(coefs[i, "Pr(>|t|)"], 4),
      ci_low      = round(est - 1.96 * se, 4),
      ci_high     = round(est + 1.96 * se, 4),
      significant = coefs[i, "Pr(>|t|)"] < 0.05
    )
  })
  # Sort by relative importance (|std beta|)
  drivers <- drivers[order(sapply(drivers, function(x) x$importance), decreasing = TRUE)]

  # Goodness of fit + ANOVA decomposition (XLSTAT-style)
  fstat <- as.numeric(s$fstatistic[1]); f_df1 <- as.numeric(s$fstatistic[2]); f_df2 <- as.numeric(s$fstatistic[3])
  f_p <- tryCatch(pf(fstat, f_df1, f_df2, lower.tail = FALSE), error = function(e) NA)
  ss_resid <- sum(residuals(fit)^2)
  ss_total <- sum((df[["nps_score"]] - mean(df[["nps_score"]]))^2)
  ss_model <- ss_total - ss_resid

  # VIF — skip when p > 40: O(p^3) computation, too slow for large predictor sets.
  vif_vals <- tryCatch({
    if (length(attr_cols) > 1 && length(attr_cols) <= 40 &&
        requireNamespace("car", quietly = TRUE)) {
      v <- car::vif(fit)
      as.list(round(v, 3))
    } else NULL
  }, error = function(e) NULL)

  # AIC / BIC
  aic_val <- round(AIC(fit), 4)
  bic_val <- round(BIC(fit), 4)

  # Predicted vs actual scatter (OLS equivalent of XLSTAT's logistic "Probabilities"
  # chart) — downsampled to <=300 evenly-spaced points (sorted by actual) so the
  # payload stays small regardless of n.
  pred_vs_actual <- tryCatch({
    actual_y <- df[["nps_score"]]
    pred_y   <- fitted(fit)
    ord      <- order(actual_y)
    n_pts    <- length(actual_y)
    max_pts  <- 300
    idx      <- if (n_pts > max_pts) round(seq(1, n_pts, length.out = max_pts)) else seq_len(n_pts)
    list(
      x      = round(as.numeric(actual_y[ord][idx]), 4),
      y      = round(as.numeric(pred_y[ord][idx]), 4)
    )
  }, error = function(e) NULL)

  # Type II analysis (XLSTAT "Type II analysis") — per-predictor F-test via
  # single-term deletion (drop1), independent of entry order.
  type2_vals <- tryCatch({
    if (length(attr_cols) <= 40) {
      d1 <- drop1(fit, test = "F")
      d1 <- d1[-1, , drop = FALSE]  # drop <none> row
      nm <- gsub("^`|`$", "", rownames(d1))
      lapply(seq_len(nrow(d1)), function(i) {
        list(attribute = nm[i],
             df = as.integer(d1[i, "Df"]),
             f_stat = round(d1[i, "F value"], 4),
             p_value = round(d1[i, "Pr(>F)"], 6))
      })
    } else NULL
  }, error = function(e) NULL)

  # Influence diagnostics (XLSTAT "Influence diagnostics") — Cook's distance +
  # leverage summarised to the top-N most influential observations. DFBeta added
  # only for this same top-20 subset to bound payload size.
  influence_diag <- tryCatch({
    cooksd <- cooks.distance(fit)
    hatv   <- hatvalues(fit)
    n_obs2 <- length(cooksd)
    p_pred <- length(attr_cols) + 1
    leverage_cutoff <- 2 * p_pred / n_obs2
    cooksd_cutoff   <- 4 / n_obs2
    ord <- order(cooksd, decreasing = TRUE)
    topn <- head(ord, 20)
    dfb <- tryCatch(dfbeta(fit)[topn, , drop = FALSE], error = function(e) NULL)
    list(
      top_influential = lapply(seq_along(topn), function(k) {
        i <- topn[k]
        entry <- list(obs = as.integer(i), cooks_d = round(cooksd[i], 5),
             leverage = round(hatv[i], 5),
             high_leverage = unname(hatv[i] > leverage_cutoff),
             high_cooks_d = unname(cooksd[i] > cooksd_cutoff))
        if (!is.null(dfb)) {
          dfb_row <- as.list(round(dfb[k, ], 5))
          names(dfb_row) <- c("(Intercept)", clean_names)[seq_along(dfb_row)]
          entry$dfbeta <- dfb_row
        }
        entry
      }),
      leverage_cutoff = round(leverage_cutoff, 5),
      cooksd_cutoff = round(cooksd_cutoff, 5),
      n_high_leverage = sum(hatv > leverage_cutoff),
      n_high_cooks_d = sum(cooksd > cooksd_cutoff)
    )
  }, error = function(e) NULL)

  # Correlation matrix of predictors (XLSTAT "Correlation matrix" block) —
  # Pearson correlation, pairwise-complete. Skipped when p > 40.
  correlation_matrix <- tryCatch({
    if (length(attr_cols) >= 2 && length(attr_cols) <= 40) {
      cm <- cor(df[attr_cols], use = "pairwise.complete.obs")
      cm[] <- round(cm, 4)
      list(
        attributes = attr_cols,
        matrix = lapply(seq_len(nrow(cm)), function(i) as.list(unname(cm[i, ])))
      )
    } else NULL
  }, error = function(e) NULL)

  # Shapiro-Wilk normality test on residuals (XLSTAT OLS assumption check)
  sw_result <- tryCatch({
    resids <- residuals(fit)
    if (length(resids) >= 3 && length(resids) <= 5000) {
      sw <- shapiro.test(resids)
      list(w_stat = round(as.numeric(sw$statistic), 4),
           p_value = round(sw$p.value, 4),
           normal  = sw$p.value >= 0.05)
    } else NULL
  }, error = function(e) NULL)

  list(
    r_squared           = round(s$r.squared, 4),
    adj_r_squared       = round(s$adj.r.squared, 4),
    f_statistic         = round(fstat, 3),
    f_p_value           = round(ifelse(is.na(f_p), 0, f_p), 6),
    rmse                = round(s$sigma, 4),
    aic                 = aic_val,
    bic                 = bic_val,
    df_model            = f_df1,
    df_resid            = f_df2,
    ss_model            = round(ss_model, 3),
    ss_resid            = round(ss_resid, 3),
    ss_total            = round(ss_total, 3),
    shapiro_wilk        = sw_result,
    pred_vs_actual      = pred_vs_actual,
    type2               = type2_vals,
    influence           = influence_diag,
    correlation_matrix  = correlation_matrix,
    intercept           = intercept,
    n                   = nrow(df),
    n_before_na_omit    = n_before,
    pct_dropped_na      = pct_dropped,
    n_attrs             = length(attr_cols),
    vif                 = vif_vals,
    significant_drivers = drivers
  )
}, error = function(e) {
  list(error = conditionMessage(e), n = nrow(df))
})

write(toJSON(result, auto_unbox = TRUE), output_path)
cat("OK\n")
