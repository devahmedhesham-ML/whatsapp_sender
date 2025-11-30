from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class TemplateSummary:
    name: str
    language: str
    header_type: str  # none|text|image|video|document
    header_example_url: Optional[str]
    flow_buttons: List[Dict[str, Any]]  # [{text, flow_id, flow_action, navigate_screen}]


def _map_header_format(fmt: Optional[str]) -> str:
    if not fmt:
        return "none"
    f = fmt.upper()
    if f == "TEXT":
        return "text"
    if f in ("IMAGE", "VIDEO", "DOCUMENT"):
        return f.lower()
    return "none"


def parse_template_summary(raw: Dict[str, Any]) -> TemplateSummary:
    name = raw.get("name", "")
    language = raw.get("language", "en_US")
    header_type = "none"
    header_url: Optional[str] = None
    flow_buttons: List[Dict[str, Any]] = []

    for comp in raw.get("components", []) or []:
        ctype = (comp.get("type") or "").upper()
        if ctype == "HEADER":
            header_type = _map_header_format(comp.get("format"))
            ex = comp.get("example") or {}
            handles = ex.get("header_handle") or []
            if isinstance(handles, list) and handles:
                header_url = handles[0]
        elif ctype == "BUTTONS":
            for b in comp.get("buttons", []) or []:
                if (b.get("type") or "").upper() == "FLOW":
                    flow_buttons.append({
                        "text": b.get("text"),
                        "flow_id": b.get("flow_id"),
                        "flow_action": b.get("flow_action"),
                        "navigate_screen": b.get("navigate_screen"),
                    })

    return TemplateSummary(
        name=name,
        language=language,
        header_type=header_type,
        header_example_url=header_url,
        flow_buttons=flow_buttons,
    )


def load_archive(path: Path = Path("templates/archive.json")) -> List[TemplateSummary]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("data") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []
        return [parse_template_summary(x) for x in items]
    except Exception:
        return []


def get_template(templates: List[TemplateSummary], name: str) -> Optional[TemplateSummary]:
    for t in templates:
        if t.name == name:
            return t
    return None

