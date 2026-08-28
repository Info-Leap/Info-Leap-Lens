import json
import os
import re
import asyncio
from typing import List, Dict, Optional, Tuple

FAQ_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "golden_faqs.json")

class SemanticRouter:
    def __init__(self):
        self.faqs = []
        if os.path.exists(FAQ_PATH):
            with open(FAQ_PATH, "r", encoding='utf-8') as f:
                self.faqs = json.load(f)
        
    def find_match(self, question: str) -> Optional[Dict]:
        """Fast keyword-based match (Legacy/Fallback)."""
        q_low = question.lower()
        best_match = None
        highest_score = 0
        
        for faq in self.faqs:
            for example in faq["questions"]:
                q_words = set(re.findall(r'\w+', q_low))
                ex_words = set(re.findall(r'\w+', example.lower()))
                score = len(q_words.intersection(ex_words))
                if score > highest_score:
                    highest_score = score
                    best_match = faq
        
        return best_match if highest_score >= 4 else None # High threshold for legacy

    async def find_match_neural(self, question: str, subagent_call_func) -> Optional[Dict]:
        """
        Uses an LLM to map the query to an intent, bridging grammatical errors.
        """
        from difflib import SequenceMatcher
        
        q_low = question.lower()
        
        # 1. Pre-filter (Top 15 based on character-level similarity + word overlap)
        scored_faqs = []
        for faq in self.faqs:
            max_score = 0
            # Check intent name and questions
            targets = [faq['intent']] + faq['questions']
            for ex in targets:
                ex_low = ex.lower()
                # Word overlap score
                q_words = set(re.findall(r'\w+', q_low))
                ex_words = set(re.findall(r'\w+', ex_low))
                word_score = len(q_words.intersection(ex_words)) * 2 # Double weight for word matches
                
                # Char-level similarity
                char_score = SequenceMatcher(None, q_low, ex_low).ratio() * 10
                
                total_score = word_score + char_score
                if total_score > max_score: max_score = total_score
            scored_faqs.append((max_score, faq))
        
        scored_faqs.sort(key=lambda x: x[0], reverse=True)
        top_candidates = [item[1] for item in scored_faqs[:15]]
        
        if not top_candidates: return None

        # 2. Neural Mapping (Direct Mapping)
        candidate_summary = "\n".join([f"- ID: {i} | Intent: {c['intent']} | Example: {c['questions'][0]}" for i, c in enumerate(top_candidates)])
        
        prompt = (
            f"User Query: '{question}'\n\n"
            f"Map this query to the BEST matching research intent. Handle spelling/grammar errors.\n"
            f"If the query is asking about a BRAND or CATEGORY not in the examples, it might still match the INTENT type.\n\n"
            f"CANDIDATES:\n{candidate_summary}\n\n"
            "Rules:\n"
            "1. Return ONLY the ID (number).\n"
            "2. If NO match is found, return 'NONE'.\n"
            "3. Be strict about entities. If user asks about 'Bajaj' and example is 'Crompton', it's only a match if the intent is generic or can be adapted."
        )
        
        res = await subagent_call_func(prompt, "Neural Intent Mapper")
        res = res.strip().upper()
        
        if "NONE" in res:
            return None
        
        try:
            match_id = int(re.search(r'\d+', res).group(0))
            if 0 <= match_id < len(top_candidates):
                return top_candidates[match_id]
        except:
            pass
            
        return None

    def get_few_shot_context(self, question: str, limit: int = 3) -> str:
        """Returns string representation of top matches for prompt injection."""
        q_low = question.lower()
        scored_faqs = []
        
        for faq in self.faqs:
            q_words = set(re.findall(r'\w+', q_low))
            ex_words = set(re.findall(r'\w+', faq["questions"][0].lower()))
            score = len(q_words.intersection(ex_words))
            scored_faqs.append((score, faq))
            
        scored_faqs.sort(key=lambda x: x[0], reverse=True)
        
        examples = []
        for score, faq in scored_faqs[:limit]:
            if score > 0:
                viz_hint = f" | Visual: {faq['preferred_visual']}" if faq.get("preferred_visual") else ""
                ans = faq.get("model_answer", "Data point available.")
                ex = f"Q: {faq['questions'][0]}\nIdeal Answer: {ans}\nTool: {faq['tool']}({faq.get('parameters', faq.get('instruction'))}){viz_hint}"
                examples.append(ex)
                
        if not examples: return ""
        
        return "\n--- EXAMPLES OF PERFECT ANSWERS ---\n" + "\n\n".join(examples) + "\n--- END EXAMPLES ---"

# Singleton
router = SemanticRouter()
