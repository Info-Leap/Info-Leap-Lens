import pandas as pd
import sqlite3
import sys
from pathlib import Path

# Add root to sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

from db_loader import get_db_path

def test_cross_category_logic(attr_label_substring="noise"):
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    
    # 1. Find all attributes related to "noise" or "silent"
    query_attrs = f"""
        SELECT attribute_code, attribute_label 
        FROM dim_attributes 
        WHERE attribute_label LIKE '%{attr_label_substring}%' OR attribute_label LIKE '%silent%'
    """
    df_attrs = pd.read_sql(query_attrs, conn)
    print(f"Found attributes: \n{df_attrs}")
    
    if df_attrs.empty:
        print("No matching attributes found.")
        conn.close()
        return

    attr_codes = df_attrs['attribute_code'].tolist()
    
    # 2. Get Importance scores for these attributes
    attr_placeholders = ",".join([f"'{c}'" for c in attr_codes])
    query_imp = f"""
        SELECT respondent_id, attribute_code, importance_score 
        FROM fact_attribute_importance 
        WHERE attribute_code IN ({attr_placeholders})
    """
    df_imp = pd.read_sql(query_imp, conn)
    
    # 3. Pivot to wide format (respondent_id | attr1 | attr2 | ...)
    df_wide = df_imp.pivot(index='respondent_id', columns='attribute_code', values='importance_score').dropna()
    
    if df_wide.empty:
        print("No respondents have scores for multiple 'silent' attributes.")
        conn.close()
        return

    # 4. Calculate Correlation
    corr_matrix = df_wide.corr()
    print("\nCorrelation Matrix of 'Noiseless' Importance across Categories:")
    print(corr_matrix)
    
    # 5. Business Logic: Probablity
    # If a respondent rates attr1 high (>=6), what % rate attr2 high (>=6)?
    results = []
    cols = df_wide.columns
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            c1, c2 = cols[i], cols[j]
            high_c1 = df_wide[df_wide[c1] >= 6]
            prob = (high_c1[c2] >= 6).mean() * 100
            
            l1 = df_attrs[df_attrs['attribute_code'] == c1]['attribute_label'].values[0]
            l2 = df_attrs[df_attrs['attribute_code'] == c2]['attribute_label'].values[0]
            
            results.append({
                "pair": f"{l1} vs {l2}",
                "probability_high": f"{prob:.1f}%"
            })
    
    print("\nCross-Category Transferability (If High in A, % High in B):")
    for r in results:
        print(f"- {r['pair']}: {r['probability_high']}")
        
    conn.close()

if __name__ == "__main__":
    test_cross_category_logic()
