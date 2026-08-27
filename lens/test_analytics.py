import sys
sys.path.insert(0, '.')
from analytics.market_analytics import (
    brand_nps_comparison, satisfaction_by_city,
    attribute_importance_ranking, feature_bucket_summary
)

print("=== BRAND NPS ===")
r = brand_nps_comparison("Mixer Grinder")
print(f"Brands found: {r['n_brands']}")
for b in r["brands"][:5]:
    print(f"  {b['brand']}: NPS={b['nps']}, mean={b['mean_score']}, n={b['n']}")

print()
print("=== SATISFACTION BY CITY (top 5) ===")
r2 = satisfaction_by_city("Mixer Grinder")
print(f"Overall mean: {r2['overall_mean']} (n={r2['overall_n']})")
for c in r2["cities"][:5]:
    print(f"  {c['city']}: {c['mean_satisfaction']} (n={c['n']})")

print()
print("=== TOP 5 ATTRIBUTES ===")
r3 = attribute_importance_ranking("Mixer Grinder", top_n=5)
for a in r3["top_attributes"]:
    print(f"  [{a['rank']}] {a['attribute'][:50]}: {a['mean_importance']} ({a['pct_of_max']}%)")

print()
print("=== FEATURE BUCKETS ===")
r4 = feature_bucket_summary("Mixer Grinder")
for b in r4["buckets"]:
    print(f"  {b['bucket']}: {b['mean_importance']} ({b['pct_of_max']}%, n_attrs={b['n_attributes']})")

