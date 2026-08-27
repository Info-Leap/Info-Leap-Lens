#!/usr/bin/env Rscript
.libPaths(c(Sys.getenv("USERPROFILE"), .libPaths()))
# anova_analysis.R — one-way ANOVA: NPS/CSAT differences across zones or segments
# Args: <input_csv> <output_json>
# Input CSV: respondent_id, segment (zone/gender/age_band), score (nps or csat)
# Output JSON: {
#   "f_stat": 4.2, "p_value": 0.006, "significant": true,
#   "group_means": [{"segment": "North", "mean": 42.1, "n": 120}],
#   "post_hoc": [{"pair": "North vs South", "p_adj": 0.02, "significant": true}]
# }

suppressPackageStartupMessages(library(jsonlite))

write_error <- function(msg, ...) {
  extras <- list(...)
  result <- c(list(error = msg), extras)
  write(toJSON(result, auto_unbox = TRUE), output_path)
  quit(status = 0)
}

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) stop("Usage: Rscript anova_analysis.R <input.csv> <output.json>")

input_path  <- args[1]
output_path <- args[2]

df <- tryCatch(read.csv(input_path), error = function(e) NULL)
if (is.null(df)) write_error("Failed to read input CSV")

required_cols <- c("segment", "score")
missing_cols  <- setdiff(required_cols, colnames(df))
if (length(missing_cols) > 0) {
  write_error(paste("Missing required columns:", paste(missing_cols, collapse = ", ")))
}

df <- na.omit(df)
df$segment <- as.factor(df$segment)

if (nrow(df) < 20 || nlevels(df$segment) < 2) {
  write_error("Insufficient data", n = nrow(df), n_segments = nlevels(df$segment))
}

result <- tryCatch({
  fit     <- aov(score ~ segment, data = df)
  s       <- summary(fit)[[1]]
  f_stat  <- round(s["segment", "F value"], 3)
  p_value <- round(s["segment", "Pr(>F)"], 4)

  # Eta-squared: SS_effect / SS_total (XLSTAT effect size)
  ss_effect <- s["segment", "Sum Sq"]
  ss_resid  <- s["Residuals", "Sum Sq"]
  eta_sq    <- round(ss_effect / (ss_effect + ss_resid), 4)

  group_means <- lapply(levels(df$segment), function(g) {
    sub <- df[df$segment == g, "score"]
    list(segment = g, mean = round(mean(sub), 2), n = length(sub), sd = round(sd(sub), 2))
  })

  # Tukey HSD post-hoc
  tukey    <- TukeyHSD(fit)$segment
  post_hoc <- lapply(seq_len(nrow(tukey)), function(i) {
    list(
      pair        = rownames(tukey)[i],
      diff        = round(tukey[i, "diff"], 2),
      p_adj       = round(tukey[i, "p adj"], 4),
      significant = tukey[i, "p adj"] < 0.05
    )
  })

  # Levene's test for equality of variances (XLSTAT assumption check)
  levene_result <- tryCatch({
    if (requireNamespace("car", quietly = TRUE)) {
      lt <- car::leveneTest(score ~ segment, data = df)
      list(f_stat = round(lt$`F value`[1], 3),
           p_value = round(lt$`Pr(>F)`[1], 4),
           equal_variance = lt$`Pr(>F)`[1] >= 0.05)
    } else NULL
  }, error = function(e) NULL)

  # Shapiro-Wilk on MODEL RESIDUALS (XLSTAT tests residuals, not group distributions)
  sw_result <- tryCatch({
    resids <- residuals(fit)
    if (length(resids) >= 3 && length(resids) <= 5000) {
      sw <- shapiro.test(resids)
      list(w_stat = round(as.numeric(sw$statistic), 4),
           p_value = round(sw$p.value, 4),
           normal  = sw$p.value >= 0.05)
    } else NULL
  }, error = function(e) NULL)

  # Welch's ANOVA (robust to unequal variances; XLSTAT offers as separate procedure)
  welch_result <- tryCatch({
    wt <- oneway.test(score ~ segment, data = df, var.equal = FALSE)
    list(f_stat      = round(as.numeric(wt$statistic), 3),
         p_value     = round(wt$p.value, 4),
         df1         = round(as.numeric(wt$parameter[1]), 2),
         df2         = round(as.numeric(wt$parameter[2]), 2),
         significant = wt$p.value < 0.05)
  }, error = function(e) NULL)

  list(
    f_stat         = f_stat,
    p_value        = p_value,
    significant    = p_value < 0.05,
    eta_squared    = eta_sq,
    n              = nrow(df),
    group_means    = group_means,
    post_hoc       = post_hoc,
    levene         = levene_result,
    shapiro_wilk   = sw_result,
    welch          = welch_result
  )
}, error = function(e) {
  list(error = conditionMessage(e), n = nrow(df))
})

write(toJSON(result, auto_unbox = TRUE), output_path)
cat("OK\n")
