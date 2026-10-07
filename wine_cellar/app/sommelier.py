"""Claude integration: read a wine label photo, and pick bottles for a meal."""

import base64
import json
import logging
from datetime import date

import anthropic

log = logging.getLogger("wine.claude")

STYLES = ["red", "white", "rose", "sparkling", "dessert", "fortified", "orange"]

# Fallbacks re-run a request on another model if the primary model's safety
# classifiers decline it, instead of returning a refusal.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

LABEL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "is_wine", "name", "producer", "vintage", "style", "country", "region", "grapes",
        "abv", "food_pairings", "pairing_summary", "tasting_notes", "serving_temp_c",
        "decant_minutes", "drink_from", "drink_until", "confidence", "label_text",
    ],
    "properties": {
        "is_wine": {"type": "boolean"},
        "name": {"type": "string"},
        "producer": {"type": "string"},
        "vintage": {"type": "integer"},
        "style": {"type": "string", "enum": STYLES},
        "country": {"type": "string"},
        "region": {"type": "string"},
        "grapes": {"type": "array", "items": {"type": "string"}},
        "abv": {"type": "number"},
        "food_pairings": {"type": "array", "items": {"type": "string"}},
        "pairing_summary": {"type": "string"},
        "tasting_notes": {"type": "string"},
        "serving_temp_c": {"type": "string"},
        "decant_minutes": {"type": "integer"},
        "drink_from": {"type": "integer"},
        "drink_until": {"type": "integer"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "label_text": {"type": "string"},
    },
}

LABEL_SYSTEM = """You are an expert sommelier cataloguing bottles for a home wine cellar.
You are given a photo of a wine bottle or label. Identify the wine and describe it for the cellar record.

- name: the wine as it would appear on a wine list, e.g. "Château Musar Rouge" or "Cloudy Bay Sauvignon Blanc".
- Read the vintage from the label. Use 0 if it is non-vintage or not visible.
- Fill grapes, region, country and style from the label, and from your knowledge of the producer and appellation when the label does not say.
- food_pairings: 4-8 specific dishes or ingredients (e.g. "slow-roast lamb shoulder", "mushroom risotto"), not generic categories.
- pairing_summary: one or two sentences on what it goes with and why.
- tasting_notes: two or three sentences on the expected style and flavour.
- serving_temp_c: a range such as "16-18".
- decant_minutes: 0 if decanting is not needed.
- drink_from / drink_until: the expected drinking window as years. Use 0 for both if you cannot judge.
- abv: from the label, or 0 if not shown.
- confidence: how sure you are of the identification. If the label is unclear, give your best guess and set confidence to low.
- label_text: the main text you can read on the label.
- If the image is not a wine bottle or label, set is_wine to false and leave the other text fields empty."""

PAIRING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["advice", "picks"],
    "properties": {
        "advice": {"type": "string"},
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["bottle_id", "reason"],
                "properties": {
                    "bottle_id": {"type": "integer"},
                    "reason": {"type": "string"},
                },
            },
        },
    },
}

PAIRING_SYSTEM = """You are the sommelier for a private home cellar. The user tells you what they are eating or the occasion.
Choose up to 3 bottles from the cellar inventory provided, best match first. Only pick bottle ids that appear in the inventory.
Prefer bottles that are inside their drinking window; mention it if a pick is past its best or still young.
For each pick give a short reason (one or two sentences) that names the dish. In advice, give one or two sentences of overall guidance,
such as serving temperature or decanting. If nothing in the cellar suits the meal, return no picks and say what style to buy instead."""


class ClaudeUnavailable(RuntimeError):
    pass


class Sommelier:
    def __init__(self, api_key: str, model: str):
        self.model = model
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else None

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def _ask(self, system: str, content: list, schema: dict, max_tokens: int = 8000) -> dict:
        if not self.client:
            raise ClaudeUnavailable("No Anthropic API key configured - add one in the add-on options")
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                system=system,
                output_config={
                    "effort": "medium",
                    "format": {"type": "json_schema", "schema": schema},
                },
                messages=[{"role": "user", "content": content}],
            )
        except anthropic.AuthenticationError as e:
            raise ClaudeUnavailable("The Anthropic API key was rejected") from e
        except anthropic.RateLimitError as e:
            raise ClaudeUnavailable("Claude is rate limited right now - try again in a minute") from e
        except anthropic.APIStatusError as e:
            log.error("Claude API error %s: %s", e.status_code, e.message)
            raise ClaudeUnavailable(f"Claude API error ({e.status_code})") from e
        except anthropic.APIConnectionError as e:
            raise ClaudeUnavailable("Could not reach the Claude API - check the internet connection") from e

        if response.stop_reason == "refusal":
            raise ClaudeUnavailable("Claude declined to answer this request")
        if response.stop_reason == "max_tokens":
            raise ClaudeUnavailable("Claude's answer was cut off - try again")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            log.error("Unparseable Claude response: %r", text[:500])
            raise ClaudeUnavailable("Claude returned an unreadable answer - try again") from e

    def analyze_label(self, image: bytes, media_type: str) -> dict:
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(image).decode("ascii"),
                },
            },
            {"type": "text", "text": "Identify this wine for my cellar record."},
        ]
        info = self._ask(LABEL_SYSTEM, content, LABEL_SCHEMA)
        # The schema uses 0 for "unknown"; store those as nulls.
        for key in ("vintage", "abv", "drink_from", "drink_until"):
            if not info.get(key):
                info[key] = None
        if info.get("decant_minutes", 0) <= 0:
            info["decant_minutes"] = None
        return info

    def pair(self, meal: str, bottles: list[dict]) -> dict:
        if not bottles:
            return {"advice": "Your cellar is empty - add some bottles first.", "picks": []}
        lines = []
        for b in bottles:
            window = ""
            if b.get("drink_from") or b.get("drink_until"):
                window = f" | drink {b.get('drink_from') or '?'}-{b.get('drink_until') or '?'}"
            lines.append(
                f"id={b['id']} | {b['name']} {b.get('vintage') or 'NV'} | {b.get('producer', '')} | "
                f"{b['style']} | {', '.join(b.get('grapes') or [])} | {b.get('region', '')}, "
                f"{b.get('country', '')}{window} | pairs with: {', '.join(b.get('food_pairings') or [])}"
            )
        text = (
            f"Today is {date.today().isoformat()}.\n\n<inventory>\n" + "\n".join(lines) +
            f"\n</inventory>\n\nWhat should I open for: {meal}"
        )
        result = self._ask(PAIRING_SYSTEM, [{"type": "text", "text": text}], PAIRING_SCHEMA)
        valid = {b["id"] for b in bottles}
        result["picks"] = [p for p in result.get("picks", []) if p.get("bottle_id") in valid][:3]
        return result
