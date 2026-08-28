import pandas as pd
import numpy as np
from sklearn.linear_model import BayesianRidge
from sklearn.impute import SimpleImputer
import json
import sqlite3
import sys
from pathlib import Path

# Add oxdata root to sys.path
current_dir = Path(__file__).resolve().parent
oxdata_root = current_dir.parent
project_root = oxdata_root.parent

if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from infoleap.db_loader import get_db_path
import infoleap.sheets_logger as sheets_logger
from infoleap.utils.constants import resolve_category

class BQ3AnalyticalEngine:
    def __init__(self, metadata_path='config/bq3_metadata.json'):
        self.db_path = get_db_path()
        self.metadata_path = oxdata_root / metadata_path
        self.metadata = self._load_metadata()
        self.cat_to_mq6 = self.metadata.get('categories', {})

    def _load_metadata(self):
        with open(self.metadata_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def analyze_drivers(self, target_brand_name, category_name, zone_name=None, gender=None, age_band=None, session_id="na"):
        conn = sqlite3.connect(self.db_path)
        
        # Robust Category Mapping
        db_cat = resolve_category(category_name)
        cat_match = None
        
        # Check metadata categories
        clean_input = db_cat.lower().replace('/ mixie', '').replace('owners', '').strip().rstrip('s')
        for c in self.cat_to_mq6.keys():
            clean_target = c.lower().rstrip('s')
            if clean_input in clean_target or clean_target in clean_input:
                cat_match = c
                break

        if not cat_match:
            conn.close()
            return {"error": f"Category '{category_name}' (DB: {db_cat}) not recognized in BQ3 metadata."}

        applicable_codes = [code for code, info in self.metadata['attributes'].items() 
                           if cat_match in info.get('applicable_categories', [])]

        if not applicable_codes:
            conn.close()
            return {"error": f"No attributes found for category '{cat_match}'."}

        # Build parameterized WHERE clause â€” prevents SQL injection from brand/zone/gender/age values.
        # own_view is constructed from trusted internal logic (not user input), so table name is safe.
        nps_conditions = ["brand_name = ?"]
        nps_params: list = [target_brand_name]
        if zone_name:
            nps_conditions.append("zone_name = ?")
            nps_params.append(zone_name)
        if gender:
            nps_conditions.append("LOWER(gender) = ?")
            nps_params.append(gender.lower())
        if age_band:
            nps_conditions.append("age_band = ?")
            nps_params.append(age_band)

        where_clause = " AND ".join(nps_conditions)

        # Category isolation for NPS via Bridge Table
        own_view = "v_kitchen_ownership" if "Mixer" in db_cat else "v_room_appliances"
        nps_params.append(db_cat)  # for appliance_name = ?
        nps_query = f"""
            SELECT respondent_id, nps_score
            FROM v_brand_nps
            WHERE {where_clause}
            AND respondent_id IN (SELECT respondent_id FROM {own_view} WHERE appliance_name = ?)
        """
        df_nps = pd.read_sql(nps_query, conn, params=nps_params)

        if len(df_nps) < 50:  # min respondents for statistical reliability
            conn.close()
            return {"error": f"Insufficient NPS data (n={len(df_nps)}) for {target_brand_name} in {db_cat}."}

        # attr codes come from trusted metadata JSON â€” safe to use as placeholders
        attr_placeholders = ",".join(["?" for _ in applicable_codes])
        cat_like = f"%{cat_match.lower().rstrip('s')}%"
        query_imp = f"""
            SELECT respondent_id, attribute_code, importance_score
            FROM fact_attribute_importance
            WHERE attribute_code IN ({attr_placeholders})
            AND LOWER(category_name) LIKE ?
        """
        df_imp = pd.read_sql(query_imp, conn, params=applicable_codes + [cat_like])
        conn.close()

        if df_imp.empty: return {"error": f"No importance data found for {cat_match}."}
        df_imp_wide = df_imp.pivot(index='respondent_id', columns='attribute_code', values='importance_score')
        df_final = df_nps.set_index('respondent_id').join(df_imp_wide, how='inner')
        if len(df_final) < 5: return {"error": f"Insufficient overlap (n={len(df_final)})."}

        surviving_attrs = [c for c in applicable_codes if c in df_final.columns and not df_final[c].isna().all()]
        X = df_final[surviving_attrs]
        y = df_final['nps_score']

        X_imputed = pd.DataFrame(SimpleImputer(strategy='mean').fit_transform(X), columns=surviving_attrs)
        model = BayesianRidge().fit(X_imputed, y)

        results = []
        for i, col in enumerate(surviving_attrs):
            results.append({
                "code": col,
                "label": self.metadata['attributes'].get(col, {}).get('label', col),
                "stated_importance": float(X_imputed[col].mean()),
                "derived_importance": float(model.coef_[i])
            })

        df_res = pd.DataFrame(results)
        s_med, d_med = df_res['stated_importance'].median(), df_res['derived_importance'].median()
        df_res['quadrant'] = df_res.apply(lambda r: 'Top Most' if r['stated_importance']>s_med and r['derived_importance']>d_med else ('Top' if r['derived_importance']>d_med else ('Big' if r['stated_importance']>s_med else 'Other')), axis=1)
        
        total_beta = df_res['derived_importance'].abs().sum()
        df_res['relative_impact_pct'] = ((df_res['derived_importance'].abs() / total_beta) * 100).round(1) if total_beta > 0 else 0
        df_res = df_res.sort_values(by='derived_importance', ascending=False)
        
        return {
            "category": category_name, "target": target_brand_name, "sample_size": len(df_final),
            "r_squared": float(model.score(X_imputed, y)), 
            "drivers": df_res.head(10).to_dict(orient='records')
        }

    def find_brands_by_driver(self, attribute_keyword, category_name):
        conn = sqlite3.connect(self.db_path)
        matches = [ (c, i['label']) for c, i in self.metadata['attributes'].items() if attribute_keyword.lower() in i['label'].lower() ]
        if not matches:
            conn.close()
            return {"error": f"No attributes matching '{attribute_keyword}'."}
            
        attr_code = matches[0][0]
        # Robustly identify brands in this category by joining with ownership views
        own_view = "v_kitchen_ownership" if any(k in category_name.lower() for k in ["mixer", "kettle", "oven", "purifier", "cooker"]) else "v_room_appliances"
        brands_query = f"""
            SELECT DISTINCT n.brand_name 
            FROM v_brand_nps n
            JOIN {own_view} o ON n.respondent_id = o.respondent_id
            WHERE LOWER(o.appliance_name) LIKE '%{category_name.lower().rstrip('s')}%'
        """
        brands = [r[0] for r in conn.execute(brands_query).fetchall()]
        
        market_results = []
        for b in brands[:5]: # Limit to top 5 for snappy latency
            print(f"  [BQ3] Analyzing market drivers for {b}...")
            res = self.analyze_drivers(b, category_name)
            if "error" not in res:
                driver = next((d for d in res['drivers'] if d['code'] == attr_code), None)
                if driver:
                    market_results.append({
                        "brand": b, "attribute": driver['label'],
                        "relative_impact": driver['relative_impact_pct'],
                        "rank_within_brand": [d['label'] for d in res['drivers']].index(driver['label']) + 1
                    })
        conn.close()
        if not market_results:
            return {"error": f"No brands in '{category_name}' have enough data for a Bayesian driver analysis on this attribute."}
            
        df = pd.DataFrame(market_results).sort_values(by='relative_impact', ascending=False)
        return {"attribute": matches[0][1], "category": category_name, "market_data": df.to_dict(orient='records')}

    def analyze_cross_category_transferability(self, attribute_substring="noise"):
        conn = sqlite3.connect(self.db_path)
        matches = [(c, i['label']) for c, i in self.metadata['attributes'].items() if attribute_substring.lower() in i['label'].lower()]
        if not matches:
            conn.close()
            return {"error": f"No attributes matching '{attribute_substring}'."}
        
        attr_placeholders = ",".join([f"'{c[0]}'" for c in matches])
        df = pd.read_sql(f"SELECT i.respondent_id, i.category_name, i.importance_score, r.city_name FROM fact_attribute_importance i JOIN v_respondents r ON i.respondent_id = r.respondent_id WHERE i.attribute_code IN ({attr_placeholders})", conn)
        conn.close()
        
        if df.empty: return {"error": "No data."}
        segment_avg = df.groupby(['category_name', 'city_name'])['importance_score'].mean().unstack(level=0)
        if segment_avg.shape[1] < 2: return {"error": "Attribute only in one category."}
        
        return {
            "attribute_found": matches[0][1],
            "correlation_matrix": segment_avg.corr().to_dict(),
            "logic": "Segment-level correlation (City) identifying cross-category valuation patterns."
        }
