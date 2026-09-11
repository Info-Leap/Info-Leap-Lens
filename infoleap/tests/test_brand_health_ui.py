import sys
import types
from pathlib import Path

# --- Mock Streamlit ---
st = types.ModuleType("streamlit")
st.cache_data = lambda *a, **kw: (lambda f: f) if (a and not callable(a[0])) else (a[0] if a else (lambda f: f))
st.columns = lambda n: [types.SimpleNamespace(__enter__=lambda s: s, __exit__=lambda s, *a: None) for _ in range(n)]
st.tabs = lambda labels: [types.SimpleNamespace(__enter__=lambda s: s, __exit__=lambda s, *a: None) for _ in labels]
st.session_state = {}
st.markdown = lambda *a, **kw: None
st.divider = lambda: None
st.selectbox = lambda *a, **kw: a[1][0] if len(a) > 1 else None
st.slider = lambda *a, **kw: kw.get("value", a[2] if len(a) > 2 else 0)
st.checkbox = lambda *a, **kw: False
st.info = lambda *a, **kw: None
st.warning = lambda *a, **kw: None
st.error = lambda *a, **kw: None
st.dataframe = lambda *a, **kw: None
st.plotly_chart = lambda *a, **kw: None
st.expander = lambda *a, **kw: types.SimpleNamespace(__enter__=lambda s: s, __exit__=lambda s, *a: None)
st.metric = lambda *a, **kw: None
st.spinner = lambda *a, **kw: types.SimpleNamespace(__enter__=lambda s: s, __exit__=lambda s, *a: None)
st.caption = lambda *a, **kw: None
st.radio = lambda *a, **kw: a[1][0] if len(a) > 1 else None

sys.modules["streamlit"] = st

# --- Path setup ---
project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from infoleap.analytics.driver_analysis_engine import DriverAnalysisEngine
from infoleap.analytics.bip_engine import BIPNormalizationEngine
from infoleap.analytics.can_map_engine import run_ca_pipeline

def test_driver_engine_imports():
    print("Running test_driver_engine_imports...")
    from infoleap.analytics.driver_analysis_engine import DriverAnalysisEngine
    assert DriverAnalysisEngine is not None
    print("PASS")

def test_driver_engine_get_attrs():
    print("Running test_driver_engine_get_attrs...")
    eng = DriverAnalysisEngine()
    df = eng.get_all_attributes()
    assert len(df) == 93
    print("PASS")

def test_driver_engine_run_basic():
    print("Running test_driver_engine_run_basic...")
    eng = DriverAnalysisEngine(category_codes=[1, 7]) # Ceiling Fans
    res = eng.run(driver_ids=[78, 79, 85], generate_ai_insight=False)
    assert res["status"] == "ok"
    assert "YES count" in res["summary_table"].columns
    print("PASS")

def test_bip_attr_ids_filter():
    print("Running test_bip_attr_ids_filter...")
    eng = BIPNormalizationEngine()
    matrix = eng.get_brand_attr_matrix(attr_ids=[78, 79, 85])
    assert matrix.shape[1] == 3
    print("PASS")

def test_bip_no_exploding_percentiles():
    print("Running test_bip_no_exploding_percentiles...")
    eng = BIPNormalizationEngine()
    res = eng.compute_normalization()
    p65 = res["percentiles"]["p65"]
    assert p65 <= 100
    print("PASS")

def test_ca_chi2_positive():
    print("Running test_ca_chi2_positive...")
    res = run_ca_pipeline(category_codes=list(range(1, 13)), attr_section="Brand and Price")
    assert res["status"] == "ok"
    assert res["ca_results"]["chi2_test"]["chi2"] > 0
    print("PASS")

def test_driver_summary_columns():
    print("Running test_driver_summary_columns...")
    eng = DriverAnalysisEngine()
    res = eng.run(driver_ids=[78, 79, 85], generate_ai_insight=False)
    summary = res["summary_table"]
    expected = ["YES count", "% of drivers owned", "Mean assoc %"]
    for col in expected:
        assert col in summary.columns
    print("PASS")

if __name__ == "__main__":
    try:
        test_driver_engine_imports()
        test_driver_engine_get_attrs()
        test_driver_engine_run_basic()
        test_bip_attr_ids_filter()
        test_bip_no_exploding_percentiles()
        test_ca_chi2_positive()
        test_driver_summary_columns()
        print("\nAll tests passed.")
    except Exception as e:
        print(f"\nTest failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
