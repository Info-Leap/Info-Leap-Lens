"""
OxData API Server â€” FastAPI
============================
Exposes the OxData analytics pipeline as HTTP endpoints for the React frontend.

Endpoints:
  POST /api/chat           â€” NL Q&A via researcher_agent
  POST /api/feedback       â€” record user rating
  GET  /api/brand-health   â€” brand health metrics (awareness funnel + NPS)
  GET  /api/funnel-compare â€” multi-brand funnel comparison
  GET  /api/brand-imagery/correlation  â€” brand co-awareness heatmap
  GET  /api/brand-imagery/zone-matrix  â€” brand Ã— zone matrix for positioning map
  GET  /api/health         â€” liveness check

Run with:
  uvicorn oxdata.api_server:app --host 0.0.0.0 --port 8001 --reload
"""

from __future__ import annotations

import os
import sys
import time
import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# â”€â”€ path bootstrap â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
BASE_DIR = Path(__file__).parent
ROOT_DIR = BASE_DIR.parent
sys.path.insert(0, str(ROOT_DIR))

load_dotenv(BASE_DIR / ".env")

from infoleap.analytics.brand_imagery_engine import BrandImageryEngine

logger = logging.getLogger("oxdata.api")
logging.basicConfig(level=logging.INFO)

# â”€â”€ App â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
app = FastAPI(title="OxData API", version="3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_engine: BrandImageryEngine | None = None


def get_engine() -> BrandImageryEngine:
    global _engine
    if _engine is None:
        _engine = BrandImageryEngine()
    return _engine


# â”€â”€ Pydantic request/response models â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    session_id: str = ""


class FeedbackRequest(BaseModel):
    session_id: str
    message_index: int
    rating: int
    comment: str = ""


# â”€â”€ Chat endpoint (calls researcher_agent) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.post("/api/chat")
async def chat(req: ChatRequest):
    """Route NL query through the researcher agent."""
    try:
        from infoleap.researcher_agent import run_autonomous_research_async
        user_msg = req.messages[-1].content if req.messages else ""
        history = [{"role": m.role, "content": m.content} for m in req.messages[:-1]]
        t0 = time.time()
        # Iterate through the async generator to get the final result
        result = {}
        async for chunk in run_autonomous_research_async(user_msg, history):
            result = chunk
        latency = int((time.time() - t0) * 1000)
        return {
            "text": result.get("answer", ""),
            "chart": result.get("chart"),
            "skill": result.get("skill", "general"),
            "sql": result.get("sql", ""),
            "reasoning": result.get("reasoning", ""),
            "synthesis": result.get("synthesis", ""),
            "row_count": result.get("row_count", 0),
            "complexity": result.get("complexity", "simple"),
            "plan_reasoning": result.get("plan_reasoning", ""),
            "plan_steps": result.get("plan_steps", []),
            "validation_ok": result.get("validation_ok", True),
            "validation_warnings": result.get("validation_warnings", ""),
            "error": result.get("error", ""),
            "latency_ms": latency,
        }
    except Exception as e:
        logger.exception("Chat error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/feedback")
async def feedback(req: FeedbackRequest):
    """Record user feedback."""
    logger.info(f"Feedback: session={req.session_id} idx={req.message_index} rating={req.rating}")
    return {"status": "ok"}


# â”€â”€ Brand Health endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.get("/api/brand-health")
def brand_health(
    zone: str = Query("all"),
    gender: str = Query("all"),
    age_band: str = Query("all"),
    city: str = Query("all"),
    brand: str | None = Query(None),
):
    """Return full brand health dataset filtered by segment."""
    try:
        engine = get_engine()
        data = engine.get_brand_health(zone=zone, gender=gender, age_band=age_band, city=city)
        if brand:
            # Also return zone breakdown for the selected brand
            if data["status"] == "success":
                base_n = data["base_n"]
                zone_data = engine.get_zone_breakdown(brand, base_n)
                city_nps  = engine.get_city_nps(brand)
                data["zone_data"] = zone_data
                data["city_nps"]  = city_nps
        return data
    except Exception as e:
        logger.exception("brand-health error")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/funnel-compare")
def funnel_compare(
    brands: str = Query(..., description="Comma-separated brand names"),
    segment_type: str = Query("overall"),
    segment_values: str = Query("", description="Comma-separated segment values"),
):
    """Return funnel comparison data for multiple brands Ã— segments."""
    try:
        engine = get_engine()
        brand_list = [b.strip() for b in brands.split(",") if b.strip()]
        seg_vals   = [v.strip() for v in segment_values.split(",") if v.strip()]
        data = engine.get_funnel_comparison(
            brands=brand_list,
            segment_type=segment_type,
            segment_values=seg_vals or None,
        )
        return {"status": "success", "data": data}
    except Exception as e:
        logger.exception("funnel-compare error")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/brand-imagery/correlation")
def brand_correlation(top_n: int = Query(15)):
    """Return brand co-awareness correlation matrix."""
    try:
        engine = get_engine()
        data = engine.get_brand_correlation_matrix(top_n=top_n)
        return {"status": "success", "data": data}
    except Exception as e:
        logger.exception("correlation error")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/brand-imagery/zone-matrix")
def zone_matrix(top_n: int = Query(20)):
    """Return brand Ã— zone TOM%/NPS matrix for positioning map."""
    try:
        engine = get_engine()
        data = engine.get_brand_zone_matrix(top_n=top_n)
        return {"status": "success", "data": data}
    except Exception as e:
        logger.exception("zone-matrix error")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/driver-analysis")
async def get_driver_analysis(
    product: str = Query("All"),
    driver_ids: str = Query("78,79,80,85,87"),
    compare_by: str = Query("overall"),
    top_brands: int = Query(10),
    percentile_threshold: int = Query(65),
    generate_ai: bool = Query(False),
):
    """Brand Driver Analysis â€” BIP + CA on user-selected attribute subset."""
    try:
        from infoleap.analytics.driver_analysis_engine import DriverAnalysisEngine
        ids = [int(x.strip()) for x in driver_ids.split(",") if x.strip().isdigit()]
        if not ids:
            return JSONResponse(status_code=400, content={"status": "error", "detail": "No valid driver_ids"})

        cats = PRODUCT_CODES.get(product, PRODUCT_CODES["All"])
        engine = DriverAnalysisEngine(category_codes=cats)
        result = engine.run(
            driver_ids=ids,
            compare_by=compare_by,
            top_brands=top_brands,
            percentile_threshold=float(percentile_threshold),
            product_label=product,
            generate_ai_insight=generate_ai,
        )
        if result.get("status") != "ok":
            return JSONResponse(status_code=404, content={"status": result.get("status"), "message": result.get("message", "")})

        bip_t = result["bip_overall"]["tables"]
        t14 = bip_t.get("table14_significance", {})
        yes_no = {}
        if hasattr(t14, "iterrows"):
            for brand, row in t14.iterrows():
                yes_no[brand] = {attr: bool(v == "YES") for attr, v in row.items()}

        sm = result.get("summary_table")
        return {
            "status": "ok",
            "driver_labels": result["driver_labels"],
            "compare_by": result["compare_by"],
            "percentiles": bip_t.get("percentiles", {}),
            "yes_no_map": yes_no,
            "summary": sm.to_dict("records") if sm is not None and not sm.empty else [],
            "ca_f1_pct": float(result["ca_overall"]["ca_results"]["eigenvalues"]["Inertia_%"].iloc[0])
                         if result["ca_overall"].get("status") == "ok" else 0,
            "ai_insight": result.get("ai_insight", ""),
        }
    except Exception as e:
        logger.exception("driver-analysis error")
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "3.0"}


# â”€â”€ CAN MAP + BIP Endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

PRODUCT_CODES = {
    "All":           list(range(1, 13)),
    "Ceiling Fans":  [1, 7],
    "Air Cooler":    [2, 8],
    "Mixer Grinder": [3, 9],
    "LED Batten":    [4, 10],
    "Water Heater":  [5, 11],
    "Water Pumps":   [6, 12],
}

@app.get("/api/can-map")
def can_map(
    product: str = Query("All"),
    attr_section: str | None = Query(None),
    top_brands: int = Query(10),
    top_attrs: int = Query(25),
):
    """Return Correspondence Analysis (CAN MAP) results."""
    try:
        from infoleap.analytics.can_map_engine import run_ca_pipeline
        cats = PRODUCT_CODES.get(product, PRODUCT_CODES["All"])
        section = None if not attr_section or attr_section == "All" else attr_section
        
        result = run_ca_pipeline(
            category_codes=cats,
            attr_section=section,
            top_brands=top_brands,
            top_attrs=top_attrs
        )
        
        if result["status"] != "ok":
            return result

        return {
            "status": result["status"],
            "brands": result["map_data"]["brands"],
            "attrs": result["map_data"]["attrs"],
            "eigenvalues": result["ca_results"]["eigenvalues"].reset_index().to_dict(orient="records"),
            "chi2_test": result["ca_results"]["chi2_test"],
            "total_inertia": result["ca_results"]["total_inertia"],
        }
    except Exception as e:
        logger.exception("can-map error")
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})


@app.get("/api/bip")
def bip(
    product: str = Query("All"),
    attr_section: str | None = Query(None),
    percentile_threshold: int = Query(65),
):
    """Return Brand Image Profiling (BIP) results."""
    try:
        from infoleap.analytics.bip_engine import BIPNormalizationEngine
        cats = PRODUCT_CODES.get(product, PRODUCT_CODES["All"])
        section = None if not attr_section or attr_section == "All" else attr_section
        
        engine = BIPNormalizationEngine(category_codes=cats, percentile_threshold=percentile_threshold)
        result = engine.run(attr_section=section)
        
        sig_df = result.get("significance")
        t14 = result["tables"]["table14_significance"]
        
        # yes_no_map: brand -> {attr -> bool(val=="YES")}
        yes_no_map = {}
        for brand in t14.index:
            yes_no_map[brand] = {attr: bool(t14.at[brand, attr] == "YES") for attr in t14.columns}

        return {
            "status": "success",
            "percentiles": result["tables"]["percentiles"],
            "significance_list": sig_df.to_dict(orient="records") if sig_df is not None else [],
            "yes_no_map": yes_no_map,
            "n_brands": len(t14.index),
            "n_attrs": len(t14.columns),
        }
    except Exception as e:
        logger.exception("bip error")
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})
