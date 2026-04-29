"""
quick_reply.py — Fast template-based ATC responses.

Instead of calling the LLM for every reply, common ATC responses (readback
correct, roger, handoffs, etc.) can be composed from pre-defined templates in
data/quick_reply_templates.json.  This cuts latency from ~1-3 s to <50 ms.

Usage
-----
Pilot types / speaks something simple:
    matched = QuickReplyEngine.auto_match(pilot_text, role, context)
    if matched:
        event_bus.emit('llm_response_generated', matched, None)
        return   # skip LLM call

Or the dashboard sends an explicit template ID:
    text = QuickReplyEngine.render(template_id, context)
    event_bus.emit('llm_response_generated', text, None)
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

# ── Load templates once at import time ───────────────────────────────────────
_DATA_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'quick_reply_templates.json')

def _load() -> dict:
    try:
        with open(_DATA_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"QuickReply: failed to load templates — {e}")
        return {"templates": [], "categories": {}}

_DB = _load()
_TEMPLATES: list[dict] = _DB.get('templates', [])
_CATEGORIES: dict = _DB.get('categories', {})

# Index by id for fast lookup
_BY_ID: dict[str, dict] = {t['id']: t for t in _TEMPLATES}


class QuickReplyEngine:

    # ── Public API ────────────────────────────────────────────────────────────

    @classmethod
    def all_templates(cls) -> list[dict]:
        """Return all template definitions (for the dashboard button list)."""
        return _TEMPLATES

    @classmethod
    def categories(cls) -> dict:
        return _CATEGORIES

    @classmethod
    def templates_for_role(cls, role: str) -> list[dict]:
        """Return templates applicable to the current ATC role."""
        role_key = cls._extract_role_key(role)
        return [
            t for t in _TEMPLATES
            if any(role_key in r for r in t.get('roles', []))
        ]

    @classmethod
    def render(cls, template_id: str, context: dict, lang: str = 'en') -> Optional[str]:
        """
        Render a template by ID with the given context variables.

        context keys used:
            callsign, freq, runway, wind, squawk, alt, hdg, spd, qnh, role, waypoint

        Returns None if template_id is unknown.
        """
        tmpl = _BY_ID.get(template_id)
        if not tmpl:
            return None

        # Pick the right language template
        tpl_str = tmpl.get(f'template_{lang}') or tmpl.get('template_en', '')
        if not tpl_str:
            return None

        # Fill variables — leave unfilled placeholders in brackets so
        # the pilot can see what's missing in the UI
        def _fill(m: re.Match) -> str:
            key = m.group(1)
            val = context.get(key, f'[{key}]')
            return str(val) if val else f'[{key}]'

        return re.sub(r'\{(\w+)\}', _fill, tpl_str)

    @classmethod
    def auto_match(cls, pilot_text: str, role: str, context: dict,
                   lang: str = 'en') -> Optional[str]:
        """
        Try to find a template whose triggers match pilot_text and whose roles
        include the current controller role.  Returns a rendered string or None.

        Only matches simple acknowledgement / handoff templates to avoid
        over-riding substantive clearance decisions that need the LLM.

        When pilot_text contains CJK characters, triggers_zh is preferred and
        the response is rendered in Chinese (template_zh).
        """
        text_lower = pilot_text.lower()
        role_key = cls._extract_role_key(role)

        # Detect Chinese input → use Chinese triggers and response language
        is_chinese = bool(re.search(r'[一-鿿㐀-䶿]', pilot_text))
        if is_chinese:
            lang = 'zh'

        # Only auto-apply acknowledgement-category templates
        AUTO_CATEGORIES = {'acknowledgement'}

        for tmpl in _TEMPLATES:
            if tmpl.get('category') not in AUTO_CATEGORIES:
                continue
            # Role check
            if not any(role_key in r for r in tmpl.get('roles', [])):
                continue
            # Trigger keyword check — prefer zh triggers for Chinese input
            if is_chinese:
                triggers = tmpl.get('triggers_zh', []) + tmpl.get('triggers', [])
            else:
                triggers = tmpl.get('triggers', [])
            if triggers and not any(kw in text_lower for kw in triggers):
                continue
            # Match found
            rendered = cls.render(tmpl['id'], context, lang)
            if rendered:
                return rendered

        return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_role_key(full_role: str) -> str:
        """
        'ZBAA Ground' → 'Ground'
        'Beijing Approach' → 'Approach'
        """
        for key in ['Ground', 'Tower', 'Approach', 'Departure', 'Center',
                    'Clearance Delivery', 'Unicom', 'Emergency']:
            if key in full_role:
                return key
        return full_role

    @classmethod
    def build_context_from_shared(cls, shared_ctx: dict) -> dict:
        """
        Build a template context dict from shared_context so callers don't
        need to extract fields manually.
        """
        ac = shared_ctx.get('aircraft', {})
        atc = shared_ctx.get('atc_state', {})
        env = shared_ctx.get('environment', {})
        issued = atc.get('issued_instructions', {})

        # Best guess at current runway from issued instructions or environment
        runway = (issued.get('departure_runway')
                  or shared_ctx.get('navigation', {}).get('active_runway', ''))

        # Wind from METAR — try to extract "31008KT" → "310 at 8"
        wind = cls._extract_wind(env.get('metar', ''))

        return {
            'callsign':  ac.get('callsign', ''),
            'freq':      atc.get('current_frequency', ''),
            'role':      atc.get('current_frequency_label', ''),
            'runway':    runway,
            'wind':      wind,
            'squawk':    issued.get('squawk', ''),
            'alt':       issued.get('cleared_altitude', str(ac.get('altitude', ''))),
            'hdg':       issued.get('assigned_heading', str(ac.get('heading', ''))),
            'spd':       issued.get('assigned_speed', str(ac.get('airspeed', ''))),
            'qnh':       str(env.get('qnh', '')),
            'waypoint':  '',   # filled from UI when needed
        }

    @staticmethod
    def _extract_wind(metar: str) -> str:
        if not metar:
            return ''
        m = re.search(r'\b(\d{3})(\d{2,3})(?:G\d+)?KT\b', metar or '')
        if m:
            return f"{m.group(1)} at {m.group(2)}"
        return ''
