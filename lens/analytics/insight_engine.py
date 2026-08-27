import os
from google import genai

class InsightEngine:
    """
    Strategic engine for generating market research insights and executive narratives.
    
    This engine identifies funnel leaks, awareness gaps, and unmet needs based on 
    brand health data, and uses generative AI to synthesize these into professional summaries.
    """

    def _get_raw_insights(self, brand_data):
        """
        Internal method to generate raw rule-based insights.
        
        Supports both direct counts/percentages and dashboard-friendly keys:
        - Awareness: 'aided' or 'aided_pct', 'tom' or 'tom_pct'
        - Usage: 'current_use' or 'current_use_pct', 'ever_used' or 'ever_used_pct'
        - Imagery: 'imagery' list with 'attribute', 'importance_pct', 'association_pct'
        """
        insights = []
        for brand in brand_data.get("brands", []):
            brand_name = brand.get("brand_name", "Unknown Brand")
            
            # 1. Retention Leak (Ever Used vs Current Use)
            # Threshold: Current Use < 40% of Ever Used
            ever_used = brand.get("ever_used") if brand.get("ever_used") is not None else brand.get("ever_used_pct", 0)
            current_use = brand.get("current_use") if brand.get("current_use") is not None else brand.get("current_use_pct", 0)
            if ever_used > 10:
                retention_rate = current_use / ever_used
                if retention_rate < 0.4:
                    insights.append({
                        "type": "Retention Leak",
                        "category": "Retention",
                        "brand": brand_name,
                        "finding": f"{brand_name} has a significant retention leak: only {retention_rate:.1%} of ever-users are current users."
                    })
            
            # 2. Awareness Gap (Aided vs TOM)
            # Threshold: TOM < 5% of Aided
            aided = brand.get("aided") if brand.get("aided") is not None else brand.get("aided_pct", 0)
            tom = brand.get("tom") if brand.get("tom") is not None else brand.get("tom_pct", 0)
            if aided > 20:
                awareness_ratio = tom / aided
                if awareness_ratio < 0.05:
                    insights.append({
                        "type": "Awareness Gap",
                        "category": "Awareness",
                        "brand": brand_name,
                        "finding": f"{brand_name} suffers from an Awareness Gap. While aided awareness is high ({aided:.1f}%), Top-of-Mind recall is only {tom:.1f}%."
                    })
            
            # 3. Unmet Needs (High Importance vs Low Association)
            # Threshold: Importance > 70% and Association < 20%
            for img in brand.get("imagery", []):
                imp = img.get("importance_pct", 0)
                asc = img.get("association_pct", 0)
                attr = img.get("attribute", "Unknown Attribute")
                if imp > 70 and asc < 20:
                    insights.append({
                        "type": "Unmet Need",
                        "category": "Imagery",
                        "brand": brand_name,
                        "finding": f"Critical Unmet Need for {brand_name}: '{attr}' is highly important to consumers ({imp:.1f}%), yet the brand has low association ({asc:.1f}%)."
                    })
        return insights

    def get_categorized_insights(self, brand_data, target_brand, polish=False, api_key=None):
        """
        Returns a dictionary of insights grouped by category (Awareness, Retention, Imagery).
        """
        all_insights = self.analyze(brand_data, polish=polish, api_key=api_key)
        brand_insights = [i for i in all_insights if i["brand"] == target_brand]
        
        categories = {"Awareness": [], "Retention": [], "Imagery": []}
        for i in brand_insights:
            cat = i.get("category", "General")
            if cat in categories:
                categories[cat].append(i["finding"])
            else:
                categories.setdefault(cat, []).append(i["finding"])
        return categories

    def _get_client(self, api_key=None):
        """Initializes and returns the GenAI client."""
        if not api_key:
            api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
            
        if not api_key:
            return None
            
        try:
            return genai.Client(api_key=api_key)
        except Exception:
            return None

    def _build_stat_narrative(self, brand_data, target_brand):
        """
        Rule-based narrative from awareness + NPS data.
        Used as both a standalone fallback and the prompt body for Gemini.
        """
        brands = brand_data.get("brands", [])
        base_n = brand_data.get("base_n", 1)
        brand  = next((b for b in brands if b.get("brand_name") == target_brand), None)
        if not brand:
            return f"No data found for {target_brand} in this segment."

        # Awareness rank
        sorted_aided = sorted(brands, key=lambda x: x.get("aided_pct", 0), reverse=True)
        aided_rank   = next((i + 1 for i, b in enumerate(sorted_aided)
                             if b["brand_name"] == target_brand), None)
        sorted_tom   = sorted(brands, key=lambda x: x.get("tom_pct", 0), reverse=True)
        tom_rank     = next((i + 1 for i, b in enumerate(sorted_tom)
                             if b["brand_name"] == target_brand), None)

        aided_pct = brand.get("aided_pct", 0)
        tom_pct   = brand.get("tom_pct",   0)
        spont_pct = brand.get("spont_pct", 0)
        nps       = brand.get("nps")
        nps_base  = brand.get("nps_base",  0)

        # Spontaneous conversion (what fraction of aided audience recalls spontaneously)
        spont_conv = round(spont_pct / aided_pct * 100, 0) if aided_pct else 0
        tom_of_spont = round(tom_pct / spont_pct * 100, 0) if spont_pct else 0

        # NPS context
        NPS_INDUSTRY_AVG = 45
        nps_line = ""
        if nps is not None and nps_base >= 30:
            vs_ind = round(nps - NPS_INDUSTRY_AVG, 1)
            sign = "+" if vs_ind >= 0 else ""
            nps_line = (
                f" From a loyalty standpoint, {target_brand} scores NPS {nps:+.0f} "
                f"(n={nps_base:,}), which is {sign}{vs_ind} pts vs. the consumer durables "
                f"industry average of {NPS_INDUSTRY_AVG}."
            )
        elif nps_base < 30:
            nps_line = f" NPS data is sparse for {target_brand} in this segment (n={nps_base})."

        # Awareness gap flag
        awareness_gap = ""
        if aided_pct > 20 and spont_pct / max(aided_pct, 1) < 0.30:
            awareness_gap = (
                f" However, {target_brand}'s spontaneous conversion is low — only "
                f"{spont_conv:.0f}% of aided-aware consumers recall the brand unprompted — "
                f"indicating a salience gap that advertising investment could address."
            )

        narrative = (
            f"{target_brand} ranks #{aided_rank} by aided awareness ({aided_pct}% of "
            f"{base_n:,} respondents) and #{tom_rank} by Top-of-Mind recall ({tom_pct}%). "
            f"Of those who can recognise the brand, {spont_conv:.0f}% recall it spontaneously, "
            f"and {tom_of_spont:.0f}% of spontaneous recallers name it first."
            f"{awareness_gap}{nps_line}"
        )
        return narrative

    def generate_executive_narrative(self, brand_data, target_brand, api_key=None):
        """
        Produces an executive summary for target_brand.
        Builds a stat-based narrative first; then optionally polishes via Gemini.
        """
        raw_findings_list = [
            i["finding"]
            for i in self._get_raw_insights(brand_data)
            if i["brand"] == target_brand
        ]
        stat_narrative = self._build_stat_narrative(brand_data, target_brand)

        # Combine stat summary with any rule-based gap findings
        if raw_findings_list:
            combined = stat_narrative + " " + " ".join(raw_findings_list)
        else:
            combined = stat_narrative

        client = self._get_client(api_key)
        if not client:
            return combined

        try:
            prompt = (
                f"You are a senior market research analyst. "
                f"Rewrite the following data summary for '{target_brand}' as a single, "
                f"professional executive paragraph (3-4 sentences). "
                f"Preserve all numbers. Keep it actionable and concise.\n\n"
                f"{combined}"
            )
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
            )
            return response.text.strip()
        except Exception:
            return combined

    def polish_insight(self, raw_finding, api_key=None):
        """
        Uses Gemini to rewrite the finding in professional market research language.
        """
        client = self._get_client(api_key)
        if not client:
            return raw_finding
            
        try:
            prompt = (
                "You are a senior market research analyst. Phrasing should be professional, concise, and actionable. "
                "Rewrite the following raw market research finding into a professional strategic observation: "
                f"'{raw_finding}'"
            )
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
            )
            return response.text.strip()
        except Exception:
            return raw_finding

    def analyze(self, brand_data, polish=False, api_key=None):
        """
        Processes brand health data to identify strategic gaps.
        If polish=True, uses Gemini to refine the phrasing.
        """
        insights = self._get_raw_insights(brand_data)
        
        if polish:
            for i in insights:
                i["finding"] = self.polish_insight(i["finding"], api_key=api_key)
        
        return insights
