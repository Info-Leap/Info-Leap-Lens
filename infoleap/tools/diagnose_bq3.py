import json
from skills.bq3_engine import BQ3AnalyticalEngine

def test_engine():
    engine = BQ3AnalyticalEngine()
    print("🚀 Initializing BQ3 Engine...")
    
    # Test for Ceiling Fans
    results = engine.get_quadrant_data("Ceiling Fans")
    print("\n[CEILING FANS DRIVERS]")
    # Get top drivers by relative impact
    top_drivers = sorted(results, key=lambda x: x['relative_impact'], reverse=True)[:3]
    for d in top_drivers:
        print(f"- {d['attribute']}: {d['relative_impact']:.1f}% impact")

if __name__ == "__main__":
    test_engine()
