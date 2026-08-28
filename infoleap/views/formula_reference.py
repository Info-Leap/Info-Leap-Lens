"""
InfoLeap Pulse — Formula Reference
Actual scoring formulas, BQ mappings, and calculation methodology.
"""
import streamlit as st
from infoleap.utils.ui_styles import inject_pulse_styles, sidebar_context_block

inject_pulse_styles()
sidebar_context_block()

st.markdown("""
<div style="background:linear-gradient(135deg,#1e1b4b 0%,#4c1d95 60%,#7c3aed 100%);
     border-radius:14px;padding:20px 26px 16px;margin-bottom:18px;">
  <h1 style="margin:0;font-size:1.5rem;font-weight:900;color: #e5e7eb;">Formula Reference</h1>
  <p style="margin:5px 0 0;color:rgba(255,255,255,0.6);font-size:0.82rem;">
    Actual formulas used in Brand Health, Qual Intelligence, and Signal calculations
  </p>
</div>
""", unsafe_allow_html=True)


def _formula_block(title, content):
    st.markdown(f"### {title}")
    st.code(content, language="text")


# ── Quantitative Brand Health ─────────────────────────────────────────────────
st.header("1. Quantitative Brand Health (Survey Data)")

_formula_block("Awareness Funnel", """
Top of Mind (TOM)          = Count(bq1a respondents who mentioned brand) / Total N × 100
Spontaneous Awareness      = Count(bq1a ∪ bq1b) / Total N × 100
Aided Awareness            = Count(bq1a ∪ bq1b ∪ bq1c) / Total N × 100

Source tables: fact_brand_awareness (stage = 'TOM', 'Spontaneous', 'Aided')
Filter: rank=1 for TOM, any rank for Spontaneous/Aided
""")

_formula_block("NPS (Net Promoter Score)", """
NPS = (Promoters% - Detractors%) × 100

Where:
  Promoters  = respondents with nps_score >= 9
  Passives   = respondents with nps_score 7-8
  Detractors = respondents with nps_score <= 6

Source table: fact_brand_nps
Minimum N for display: 30 respondents per brand
""")

_formula_block("Behavioural Funnel", """
Trial       (bq1d): Respondents who have ever used the brand
Retention   (bq1e): Respondents currently using the brand
Conversion  = (Ever Used / Aided Awareness) × 100  — awareness-to-trial efficiency
Retention % = (Current Use / Ever Used) × 100        — ability to retain trialists
""")

_formula_block("BIP Strategic Quadrant", """
X-axis (Importance)  = bq3a series attribute importance scores (1-7 scale, normalized 0-100%)
Y-axis (Association) = bq3b series: % of respondents associating brand with attribute

Quadrant definitions:
  High Importance + High Association → Table Stakes (must maintain)
  High Importance + Low Association  → Growth Opportunity (prioritise)
  Low Importance  + High Association → Niche/Nice-to-have
  Low Importance  + Low Association  → Low Priority
""")


# ── Qualitative Brand Intelligence ────────────────────────────────────────────
st.header("2. Qualitative Brand Intelligence (IDI Transcripts)")

_formula_block("Brand Satisfaction Score (0-100)", """
Component 1: NPS Signal      (weight 0.40, max 40 pts)
  promoter   → 40 pts
  passive    → 20 pts
  detractor  → 0 pts
  unclear    → 15 pts

Component 2: Emotional Arc   (weight 0.35, max 35 pts)
  positive   → 35 pts
  neutral    → 20 pts
  negative   → 0 pts

Component 3: Relationship    (weight 0.25, max 25 pts)
  honeymoon          → 25 pts
  settled_satisfied  → 22 pts
  resigned_acceptance→ 12 pts
  strained           → 6 pts
  at_risk            → 2 pts
  churned_mentally   → 0 pts

TOTAL = min(100, C1 + C2 + C3)
""")

_formula_block("Brand Risk Score (0-100)", """
High-severity pain pts × 14  (capped at 5 pain pts = max 70 pts)
Product blame ratio  × 30    (0-1.0 ratio, max 30 pts)
Switching flag       + 22    (binary: switching_consideration = True)
Won't recommend      + 15    (binary: advocacy = 'wont_recommend')

TOTAL = min(100, sum of above)

High-severity = pain_point.severity in ('high', 'critical')
Product blame ratio = product_blame_instances / (self_blame + product_blame)
""")

_formula_block("Brand Opportunity Score (0-100)", """
High-opp aspiration gaps × 28  (commercial_opportunity = 'high')
Medium-opp gaps          × 12  (commercial_opportunity = 'medium')
Unspoken needs           × 7   (each inferred unspoken need)

TOTAL = min(100, sum of above)

Aspiration gap = gap between what consumer wishes existed and current reality
Unspoken need = inferred from workarounds, never directly stated
""")

_formula_block("Verbatim Verification Score (BM25-based)", """
For each verbatim_quote field in a matrix:
  1. Exact substring match in source .md transcript → found=True, confidence=high
  2. First 40 chars match → found=True, confidence=high
  3. First 25 chars match → found=True, confidence=medium
  4. Word overlap ≥ 60% over 20-word window → found=True, confidence=medium
  5. No match → found=False (paraphrase/hallucination)

Quality Score = (Verified quotes / Total quotes) × 100

Thresholds:
  ≥80%  = excellent
  60-79% = good
  30-59% = poor
  <30%   = critical (excluded from findings generation)
""")


# ── CoinDCX Concept Testing ───────────────────────────────────────────────────
st.header("3. CoinDCX Concept Testing Scores")

_formula_block("Comprehension Score (1-10)", """
9-10: Explains mechanism accurately in own words + correct benchmark comparison
      e.g., "100g becomes 105-107g per year, better than SGB 2.5%"
7-8:  Gets core idea (extra gold units), one conceptual gap remains
5-6:  Understands safety OR returns but not both
3-4:  Feature-level only — "you get more gold somehow" without mechanism
1-2:  Fundamental misunderstanding (thinks it is cash interest FD, not gold units)

Evidence required: LLM must cite specific transcript moment that sets the score.
""")

_formula_block("Adoption Intent Score (1-10)", """
9-10: Would invest this week or this month; cites specific rupee amount
7-8:  Would try after one specific clarification; trial amount mentioned
5-6:  Maybe; needs more proof; no specific amount or timeline
3-4:  Interesting but passive; no intent to act
1-2:  Explicit rejection — "no", "never", active discomfort

NPS mapping: 8+ → promoter | 6-7 → passive | ≤5 → detractor
""")

_formula_block("Adoption Readiness Score (0-100)", """
Intent score (0-10) × 5           = 0-50 pts
Comprehension score (0-10) × 3    = 0-30 pts
Timeline bonus:
  timeline = '0-3m'  → +20 pts
  timeline = '3-6m'  → +10 pts
  other              → +0 pts

TOTAL = min(100, sum of above)
""")

_formula_block("Trust Gap Risk Score (0-100)", """
High crypto trust gap %  × 40  (% of segment with gap='high')
Medium crypto trust gap% × 15  (% of segment with gap='medium')
High-severity pain pts   × 3 each

TOTAL = min(100, sum of above)

crypto_trust_gap = 'high' means respondent explicitly linked
CoinDCX's crypto identity to reduced gold product credibility.
Verbatim evidence required to assign 'high'.
""")

_formula_block("Severity Rules (Universal — applies to all projects)", """
critical: Explicit deal-breaker language. "never", "no way", "can't trust".
          3+ reinforcing statements. Blocks adoption entirely.
high:     Strong hesitation requiring resolution. "I need to know", "worried about".
          2+ reinforcing statements.
medium:   Conditional acceptance. "depends on", "if they fix", "would consider if".
          1-2 mentions.
low:      Single passive mention. Does not affect adoption behaviour.
          "would be nice if..."
""")


# ── BM25 Passage Search ───────────────────────────────────────────────────────
st.header("4. BM25 Passage Search")

_formula_block("BM25 Relevance Scoring", """
BM25(q, d) = Σ_i IDF(qi) × (f(qi,d) × (k1+1)) / (f(qi,d) + k1×(1 - b + b×dl/avgdl))

Parameters used:
  k1 = 1.5  (term frequency saturation)
  b  = 0.75 (length normalisation)

Boosts applied:
  Exact phrase match in passage: +30 pts
  Pain point passage:            +4 pts
  Decision signal passage:       +3 pts
  High quality matrix (≥80%):   +2 pts
  Brand/segment filter match:   +2 pts

Hindi support: prefix matching for transliteration variations
  (e.g. "garlic" matches "garlick", "garlc")
""")
