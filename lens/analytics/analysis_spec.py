"""
AnalysisSpec — config-driven regression + CAN MAP pipeline spec.

Usage
-----
    from lens.analytics.analysis_spec import AnalysisSpec, RegressionConfig, CANMapConfig

    spec = AnalysisSpec.nps_drivers(brands=["Bajaj", "Crompton"])
    # or
    spec = AnalysisSpec.trial_drivers()
    # or fully custom:
    spec = AnalysisSpec(
        awareness_gate_stages=["TOM", "SPONT", "AIDED"],
        regression=RegressionConfig(dv_source="awareness_stage", dv_stage="EVER_USED"),
        can_map=CANMapConfig(attr_source="regression_significant"),
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RegressionConfig:
    dv_source: str = "nps"
    # "nps"              → fact_brand_nps.nps_score
    # "csat"             → fact_satisfaction.score
    # "ever_tried"       → fact_brand_awareness WHERE stage=dv_stage, binary 0/1
    # "awareness_stage"  → same, dv_stage configurable
    dv_stage: Optional[str] = None          # e.g. "EVER_USED" for trial drivers
    topbox_threshold: Optional[int] = None  # None=OLS continuous; int=logistic binary
    iv_source: str = "imagery"              # "imagery" | "importance" | "both"
    attr_ids: Optional[list] = None         # None=all attrs; explicit list=subset
    exclude_brand_ids: list = field(default_factory=list)

    @property
    def regression_type(self) -> str:
        binary = (
            self.topbox_threshold is not None
            or self.dv_source in ("ever_tried", "awareness_stage")
        )
        return "logistic_regression" if binary else "driver_regression"

    @property
    def dv_is_binary(self) -> bool:
        return self.regression_type == "logistic_regression"


@dataclass
class CANMapConfig:
    attr_source: str = "top_n"
    # "all"                    → all attrs (current default)
    # "top_n"                  → top N by association %
    # "regression_significant" → p < sig_threshold from regression output
    # "explicit_list"          → use attr_list
    attr_list: list = field(default_factory=list)   # attr_ids if explicit_list
    top_n: int = 30
    sig_threshold: float = 0.05
    respondent_universe: str = "aware"
    # "aware"      → same awareness_gate_stages as AnalysisSpec
    # "tried"      → EVER_USED stage only
    # "considered" → CONSIDERATION stage only
    # "all"        → no filter (current behavior)


@dataclass
class AnalysisSpec:
    # Universe
    brands: Optional[list] = None
    brand_ids: Optional[list] = None
    awareness_gate_stages: list = field(
        default_factory=lambda: ["TOM", "SPONT", "AIDED", "EVER_USED", "CONSIDERATION"]
    )
    exclude_brand_ids: list = field(default_factory=list)

    # Pipeline steps (None = skip)
    regression: Optional[RegressionConfig] = None
    can_map: Optional[CANMapConfig] = None

    # ── Preset factories ──────────────────────────────────────────────────────

    @classmethod
    def nps_drivers(cls, brands=None) -> "AnalysisSpec":
        """Top-box NPS DV, imagery IV, full awareness gate, driven CAN MAP."""
        return cls(
            brands=brands,
            awareness_gate_stages=["TOM", "SPONT", "AIDED", "EVER_USED", "CONSIDERATION"],
            regression=RegressionConfig(dv_source="nps", topbox_threshold=9),
            can_map=CANMapConfig(attr_source="regression_significant", sig_threshold=0.05),
        )

    @classmethod
    def trial_drivers(cls, brands=None, exclude_brand_ids=None) -> "AnalysisSpec":
        """Binary trial DV (EVER_USED), imagery IV, aided-aware gate — replicates Akshayakalpa Excel pipeline."""
        return cls(
            brands=brands,
            awareness_gate_stages=["TOM", "SPONT", "AIDED"],
            exclude_brand_ids=exclude_brand_ids or [],
            regression=RegressionConfig(
                dv_source="awareness_stage",
                dv_stage="EVER_USED",
                iv_source="imagery",
            ),
            can_map=CANMapConfig(attr_source="regression_significant", sig_threshold=0.05),
        )

    @classmethod
    def csat_drivers(cls, brands=None) -> "AnalysisSpec":
        """CSAT DV, imagery IV, last-purchased gate."""
        return cls(
            brands=brands,
            awareness_gate_stages=["LAST_PURCHASED"],
            regression=RegressionConfig(dv_source="csat"),
            can_map=CANMapConfig(attr_source="regression_significant"),
        )

    @classmethod
    def perception_map_only(cls, brands=None, top_n=30) -> "AnalysisSpec":
        """CAN MAP only — no regression. Replicates current default behavior."""
        return cls(
            brands=brands,
            awareness_gate_stages=[],
            regression=None,
            can_map=CANMapConfig(attr_source="top_n", top_n=top_n),
        )

    @classmethod
    def from_project_config(cls, config: dict) -> "AnalysisSpec":
        """Build from project_config DB table values (key-value dict from DB)."""
        dv_source = config.get("dv_source", "nps")
        topbox_raw = config.get("dv_topbox_threshold", "9")
        try:
            topbox = int(topbox_raw) if topbox_raw and str(topbox_raw) not in ("0", "None", "") else None
        except (ValueError, TypeError):
            topbox = 9
        stages_raw = config.get("awareness_gate_stages", "TOM,SPONT,AIDED,EVER_USED,CONSIDERATION")
        stages = [s.strip() for s in stages_raw.split(",") if s.strip()] if stages_raw else []
        excl_raw = config.get("exclude_brand_ids", "")
        try:
            exclude = [int(x.strip()) for x in excl_raw.split(",") if x.strip()] if excl_raw else []
        except (ValueError, TypeError):
            exclude = []
        return cls(
            awareness_gate_stages=stages,
            exclude_brand_ids=exclude,
            regression=RegressionConfig(
                dv_source=dv_source,
                topbox_threshold=topbox,
                iv_source=config.get("iv_source", "imagery"),
            ),
            can_map=CANMapConfig(attr_source="regression_significant"),
        )
