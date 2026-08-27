#!/usr/bin/env Rscript
.libPaths(c(Sys.getenv("USERPROFILE"), .libPaths()))
# logistic_regression.R — logistic key-driver regression for a BINARY outcome
# (e.g. top-box NPS/CSAT recoded 9-10 -> 1). Predictors = binary imagery attrs.
# Args: <input_csv> <output_json>
# Input CSV: respondent_id, nps_score (0/1 binary outcome), [attr1, attr2, ...]
# Output JSON: {
#   "mcfadden_r2": 0.12, "n": 2386, "significant_drivers": [
#     {"attribute","coef","std_coef","odds_ratio","importance","z_stat","p_value","significant"} ] }

suppressPackageStartupMessages(library(jsonlite))

write_error <- function(msg, ...) {
  extras <- list(...)
  result <- c(list(error = msg), extras)
  write(toJSON(result, auto_unbox = TRUE), output_path)
  quit(status = 0)
}

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) stop("Usage: Rscript logistic_regression.R <input.csv> <output.json>")
input_path  <- args[1]
output_path <- args[2]

df <- tryCatch(read.csv(input_path), error = function(e) NULL)
if (is.null(df)) write_error("Failed to read input CSV")
if (!"nps_score" %in% colnames(df)) write_error("Input must have 'nps_score' (binary outcome) column")

n_before <- nrow(df)
df <- na.omit(df)
n_after <- nrow(df)
# Blind listwise deletion can silently collapse the sample (e.g. sparse/rotated
# imagery batteries) and produce a degenerate, meaningless fit. Flag it rather
# than returning a result that looks valid. See .regression_reference/AUDIT.md §3.
pct_dropped <- if (n_before > 0) round(100 * (n_before - n_after) / n_before, 1) else 0
if (pct_dropped > 20 && n_after > 0) {
  write_error(sprintf("%.1f%% of rows dropped for missing data (n=%d -> n=%d) — result would be unreliable.",
                       pct_dropped, n_before, n_after),
              n_before = n_before, n_after = n_after, pct_dropped = pct_dropped)
}
# Outcome must be binary 0/1 for logistic regression
y <- df[["nps_score"]]
uy <- unique(y)
if (!all(uy %in% c(0, 1)) || length(uy) < 2) {
  write_error("Logistic regression requires a binary 0/1 outcome (use a top-box recode).",
              levels = length(uy))
}

# 'X' = pandas RangeIndex column from to_csv(index=True); not a predictor.
attr_cols <- setdiff(colnames(df), c("X", "respondent_id", "nps_score"))
attr_cols <- attr_cols[sapply(df[attr_cols], function(x) var(x, na.rm = TRUE) > 0)]
if (length(attr_cols) < 1 || nrow(df) < 30) {
  write_error("Insufficient data", n = nrow(df), n_attrs = length(attr_cols))
}

result <- tryCatch({
  formula_str <- paste("nps_score ~", paste(paste0("`", attr_cols, "`"), collapse = " + "))
  fit <- glm(as.formula(formula_str), data = df, family = binomial())
  s   <- summary(fit)
  coefs_full <- as.data.frame(s$coefficients)
  intercept  <- round(coefs_full[1, "Estimate"], 4)
  coefs <- coefs_full[-1, ]  # drop intercept for the driver list
  clean_names <- gsub("^`|`$", "", rownames(coefs))

  # Standardised logit coefficient (XLSTAT standard) = b * sd(x) * sqrt(3)/pi
  std_betas <- sapply(seq_len(nrow(coefs)), function(i) {
    nm <- clean_names[i]
    sx <- if (nm %in% colnames(df)) sd(df[[nm]], na.rm = TRUE) else NA
    if (is.na(sx)) NA else coefs[i, "Estimate"] * sx * (sqrt(3) / pi)
  })
  abs_std <- abs(std_betas)
  tot_abs <- sum(abs_std, na.rm = TRUE)
  imp_pct <- if (tot_abs > 0) abs_std / tot_abs * 100 else rep(0, length(abs_std))

  # McFadden pseudo-R²
  ll_full  <- as.numeric(logLik(fit))
  null_fit <- glm(nps_score ~ 1, data = df, family = binomial())
  ll_null  <- as.numeric(logLik(null_fit))
  mcf      <- if (ll_null != 0) 1 - (ll_full / ll_null) else NA

  # Likelihood Ratio Test — overall model significance (XLSTAT "ANOVA" equivalent for logistic)
  lrt_chi2 <- round(-2 * (ll_null - ll_full), 4)
  lrt_df   <- length(attr_cols)
  lrt_p    <- round(1 - pchisq(lrt_chi2, df = lrt_df), 6)

  # AIC / BIC (model selection criteria)
  aic_val  <- round(AIC(fit), 4)
  bic_val  <- round(BIC(fit), 4)

  # Classification table at default 0.5 threshold
  fitted_probs_cls <- fitted(fit)
  pred_class <- as.integer(fitted_probs_cls >= 0.5)
  tp <- sum(pred_class == 1 & y == 1); tn <- sum(pred_class == 0 & y == 0)
  fp <- sum(pred_class == 1 & y == 0); fn <- sum(pred_class == 0 & y == 1)
  n_total <- length(y)
  sensitivity  <- if ((tp + fn) > 0) round(tp / (tp + fn), 4) else NA
  specificity  <- if ((tn + fp) > 0) round(tn / (tn + fp), 4) else NA
  accuracy     <- round((tp + tn) / n_total, 4)
  ppv          <- if ((tp + fp) > 0) round(tp / (tp + fp), 4) else NA
  npv          <- if ((tn + fn) > 0) round(tn / (tn + fn), 4) else NA

  # Cox & Snell R² and Nagelkerke R² (XLSTAT standard logistic fit indices)
  n_obs   <- nrow(df)
  cs_r2   <- 1 - exp((2 / n_obs) * (ll_null - ll_full))
  max_cs  <- 1 - exp((2 / n_obs) * ll_null)
  nag_r2  <- if (!is.na(max_cs) && max_cs > 0) cs_r2 / max_cs else NA

  # AUC (concordance statistic = ROC-AUC for binary outcome)
  fitted_probs <- fitted(fit)
  auc_val <- tryCatch({
    pos_probs <- fitted_probs[y == 1]; neg_probs <- fitted_probs[y == 0]
    if (length(pos_probs) > 0 && length(neg_probs) > 0)
      mean(outer(pos_probs, neg_probs, ">") + 0.5 * outer(pos_probs, neg_probs, "=="))
    else NA
  }, error = function(e) NA)

  # ROC curve points (XLSTAT "ROC Curve" chart) — 50-step threshold sweep so the
  # JSON payload stays small regardless of n (don't return per-observation points).
  roc_points <- tryCatch({
    thresholds <- seq(1, 0, length.out = 50)
    roc <- lapply(thresholds, function(th) {
      pred <- as.integer(fitted_probs >= th)
      tp_r <- sum(pred == 1 & y == 1); fn_r <- sum(pred == 0 & y == 1)
      fp_r <- sum(pred == 1 & y == 0); tn_r <- sum(pred == 0 & y == 0)
      tpr <- if ((tp_r + fn_r) > 0) tp_r / (tp_r + fn_r) else 0
      fpr <- if ((fp_r + tn_r) > 0) fp_r / (fp_r + tn_r) else 0
      list(threshold = round(th, 3), tpr = round(tpr, 4), fpr = round(fpr, 4))
    })
    list(fpr = sapply(roc, function(r) r$fpr), tpr = sapply(roc, function(r) r$tpr))
  }, error = function(e) NULL)

  # Probabilities chart (XLSTAT "Probabilities" chart) — predicted prob sorted
  # ascending with the aligned actual outcome, downsampled to <=300 evenly-spaced
  # points across the sorted order (not a truncation) so shape is preserved at any n.
  prob_chart <- tryCatch({
    ord <- order(fitted_probs)
    sorted_pred <- fitted_probs[ord]
    sorted_actual <- y[ord]
    n_pts <- length(sorted_pred)
    max_pts <- 300
    idx <- if (n_pts > max_pts) round(seq(1, n_pts, length.out = max_pts)) else seq_len(n_pts)
    list(
      x      = seq_along(idx),
      pred   = round(as.numeric(sorted_pred[idx]), 4),
      actual = as.integer(sorted_actual[idx])
    )
  }, error = function(e) NULL)

  # Hosmer-Lemeshow goodness-of-fit (10 decile groups, XLSTAT default)
  hl_result <- tryCatch({
    probs_ord <- order(fitted_probs)
    grp       <- cut(seq_along(probs_ord), breaks = 10, labels = FALSE)[order(probs_ord)]
    obs_pos   <- as.numeric(tapply(y, grp, sum))
    exp_pos   <- as.numeric(tapply(fitted_probs, grp, sum))
    ns        <- as.numeric(tapply(y, grp, length))
    exp_neg   <- ns - exp_pos
    obs_neg   <- ns - obs_pos
    chi_hl    <- sum((obs_pos - exp_pos)^2 / pmax(exp_pos, 0.5) +
                     (obs_neg - exp_neg)^2 / pmax(exp_neg, 0.5), na.rm = TRUE)
    list(chi2 = round(chi_hl, 3), df = 8L,
         p_value = round(1 - pchisq(chi_hl, df = 8), 4),
         good_fit = (1 - pchisq(chi_hl, df = 8)) >= 0.05)
  }, error = function(e) NULL)

  # VIF — skip when p > 40: O(p^3) computation, too slow for large predictor sets.
  vif_vals <- tryCatch({
    if (length(attr_cols) > 1 && length(attr_cols) <= 40 &&
        requireNamespace("car", quietly = TRUE)) {
      ols_proxy <- lm(as.formula(formula_str), data = df)
      v <- car::vif(ols_proxy)
      as.list(round(v, 3))
    } else NULL
  }, error = function(e) NULL)

  # Type II analysis (XLSTAT "Type II analysis") — per-predictor LR chi-square via
  # single-term deletion (drop1), independent of entry order. Skip when p > 40:
  # drop1() refits the model once per predictor, O(p) glm() calls.
  type2_vals <- tryCatch({
    if (length(attr_cols) <= 40) {
      d1 <- drop1(fit, test = "Chisq")
      d1 <- d1[-1, , drop = FALSE]  # drop <none> row
      nm <- gsub("^`|`$", "", rownames(d1))
      lapply(seq_len(nrow(d1)), function(i) {
        list(attribute = nm[i],
             df = as.integer(d1[i, "Df"]),
             lr_chi2 = round(d1[i, "LRT"], 4),
             p_value = round(d1[i, "Pr(>Chi)"], 6))
      })
    } else NULL
  }, error = function(e) NULL)

  # Influence diagnostics (XLSTAT "Influence diagnostics") — Cook's distance +
  # leverage (hat values) summarised to the top-N most influential observations
  # rather than a full per-row dump (n can be thousands). DFBeta computed only
  # for this same top-20 subset (dfbeta() is O(n*p), bounding it to 20 rows
  # keeps the payload small regardless of n).
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
          names(dfb_row) <- clean_names[match(names(dfb_row), rownames(coefs))]
          names(dfb_row)[1] <- "(Intercept)"
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
  # Pearson correlation, pairwise-complete. Skipped when p > 40 (O(p^2) cells,
  # unreadable UI beyond that anyway).
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

  # Global significance tests (XLSTAT "Test of H0" block) — XLSTAT reports 3
  # overall-model tests: -2 Log-Likelihood (already have as lrt_chi2/lrt_df/lrt_p),
  # Score test, and Wald test. Same df as the LRT (df = n predictors).
  global_tests <- tryCatch({
    score_chi2 <- tryCatch({
      sc <- anova(null_fit, fit, test = "Rao")
      round(sc[["Rao"]][2], 4)
    }, error = function(e) NA)
    wald_chi2 <- tryCatch({
      b   <- coef(fit)[-1]
      V   <- vcov(fit)[-1, -1, drop = FALSE]
      as.numeric(round(t(b) %*% solve(V) %*% b, 4))
    }, error = function(e) NA)
    df_g <- length(attr_cols)
    list(
      minus2ll = list(chi2 = lrt_chi2, df = df_g, p_value = lrt_p),
      score    = if (!is.na(score_chi2)) list(chi2 = score_chi2, df = df_g,
                     p_value = round(1 - pchisq(score_chi2, df = df_g), 6)) else NULL,
      wald     = if (!is.na(wald_chi2)) list(chi2 = wald_chi2, df = df_g,
                     p_value = round(1 - pchisq(wald_chi2, df = df_g), 6)) else NULL
    )
  }, error = function(e) NULL)

  drivers <- lapply(seq_len(nrow(coefs)), function(i) {
    est <- coefs[i, "Estimate"]; se <- coefs[i, "Std. Error"]
    # Quasi-complete separation can drive |est| huge; exp() -> Inf -> invalid JSON
    # (toJSON emits "Inf", json.load fails). Clamp before exponentiating.
    est_c <- min(max(est, -15), 15)
    se_c  <- min(se, 5)
    nm_i  <- clean_names[i]
    sx_i  <- if (nm_i %in% colnames(df)) sd(df[[nm_i]], na.rm = TRUE) else NA
    # 95% CI for the standardized coefficient (XLSTAT chart: coef bar + CI whiskers),
    # same transform used to build std_betas above (coef * sd(x)).
    std_ci_low  <- if (is.na(sx_i)) NULL else round((est - 1.96 * se) * sx_i, 4)
    std_ci_high <- if (is.na(sx_i)) NULL else round((est + 1.96 * se) * sx_i, 4)
    list(
      attribute      = clean_names[i],
      coef           = round(est, 4),
      std_error      = round(se, 4),
      std_coef       = if (is.na(std_betas[i])) NULL else round(std_betas[i], 4),
      std_coef_ci_low  = std_ci_low,
      std_coef_ci_high = std_ci_high,
      odds_ratio     = round(exp(est_c), 3),
      or_ci_low      = round(exp(est_c - 1.96 * se_c), 3),
      or_ci_high     = round(exp(est_c + 1.96 * se_c), 3),
      importance     = round(ifelse(is.na(imp_pct[i]), 0, imp_pct[i]), 2),
      z_stat         = round(coefs[i, "z value"], 3),
      p_value        = round(coefs[i, "Pr(>|z|)"], 4),
      significant    = coefs[i, "Pr(>|z|)"] < 0.05
    )
  })
  drivers <- drivers[order(sapply(drivers, function(x) x$importance), decreasing = TRUE)]

  list(
    mcfadden_r2         = round(ifelse(is.na(mcf), 0, mcf), 4),
    r_squared           = round(ifelse(is.na(mcf), 0, mcf), 4),  # alias for shared renderer
    cox_snell_r2        = round(ifelse(is.na(cs_r2), 0, cs_r2), 4),
    nagelkerke_r2       = round(ifelse(is.na(nag_r2), 0, nag_r2), 4),
    auc                 = round(ifelse(is.na(auc_val), 0, auc_val), 4),
    ll_full             = round(ll_full, 4),
    ll_null             = round(ll_null, 4),
    lrt_chi2            = lrt_chi2,
    lrt_df              = lrt_df,
    lrt_p               = lrt_p,
    aic                 = aic_val,
    bic                 = bic_val,
    sensitivity         = ifelse(is.na(sensitivity), 0, sensitivity),
    specificity         = ifelse(is.na(specificity), 0, specificity),
    accuracy            = accuracy,
    ppv                 = ifelse(is.na(ppv), 0, ppv),
    npv                 = ifelse(is.na(npv), 0, npv),
    confusion           = list(tp = tp, tn = tn, fp = fp, fn = fn),
    hosmer_lemeshow     = hl_result,
    roc_points          = roc_points,
    prob_chart          = prob_chart,
    type2               = type2_vals,
    influence           = influence_diag,
    correlation_matrix  = correlation_matrix,
    global_tests        = global_tests,
    intercept           = intercept,
    vif                 = vif_vals,
    n                   = nrow(df),
    n_before_na_omit    = n_before,
    pct_dropped_na      = pct_dropped,
    n_attrs             = length(attr_cols),
    base_rate           = round(mean(y), 4),
    significant_drivers = drivers
  )
}, error = function(e) {
  list(error = conditionMessage(e), n = nrow(df))
})

write(toJSON(result, auto_unbox = TRUE), output_path)
cat("OK\n")
