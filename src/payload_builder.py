from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.whatsapp_client import WhatsAppClient


@dataclass
class CtaButton:
    # type: 'url', 'call', or 'copy_code'
    type: str = "url"
    text: str = ""
    url: str = ""
    phone: str = ""
    coupon_code: str = ""

    def is_complete(self) -> bool:
        t = (self.type or "url").lower()
        if t == "url":
            return bool(self.text.strip() and self.url.strip())
        if t == "call":
            return bool(self.text.strip() and self.phone.strip())
        if t == "copy_code":
            return bool(self.coupon_code.strip())
        return False


@dataclass
class FlowButton:
    index: str = "0"
    flow_token: str = ""
    flow_action: str = ""
    navigate_screen: str = ""

    def is_complete(self) -> bool:
        return bool(self.flow_token and self.flow_action and self.navigate_screen)

    def to_csv_columns(self, n: int) -> Dict[str, str]:
        return {
            f"button{n}_type": "flow",
            f"button{n}_index": self.index,
            f"button{n}_flow_token": self.flow_token,
            f"button{n}_flow_action": self.flow_action,
            f"button{n}_navigate_screen": self.navigate_screen,
        }


@dataclass
class TemplateConfig:
    # Message type: 'template' or 'interactive'
    msg_type: str = "template"

    # Template mode fields
    template: str = ""
    lang: str = "en_US"

    # Header
    header_type: str = "none"  # none|text|image|video|document
    header_text: str = ""
    header_media_path: str = ""
    header_media_url: str = ""
    header_media_id: str = ""

    # Template body/button params
    body_params: List[str] = field(default_factory=list)
    # For URL buttons dynamic suffix parameters (comma separated button groups, each '|' separated)
    button_params_groups: List[List[str]] = field(default_factory=list)

    # Flow buttons
    flow_buttons: List[FlowButton] = field(default_factory=list)

    # Interactive mode fields
    body_text: str = ""
    footer_text: str = ""
    ctas: List[CtaButton] = field(default_factory=list)

    def validate(self) -> Tuple[bool, str]:
        mode = (self.msg_type or "template").lower()
        if mode not in {"template", "interactive"}:
            return False, f"Invalid msg_type: {self.msg_type}"

        for i, cta in enumerate(self.ctas):
            cta_type = (cta.type or "url").lower()
            if cta_type not in {"url", "call", "copy_code"}:
                return False, f"Unsupported CTA type: {cta.type}"
            if cta_type == "copy_code" and not cta.coupon_code.strip():
                return False, f"CTA #{i+1} (copy_code) requires a coupon code"

        # Validate header selection uniformly (users often wonder about path vs URL vs id)
        htype = (self.header_type or "none").lower()
        if htype not in {"none", "text", "image", "video", "document"}:
            return False, f"Invalid header_type: {self.header_type}"
        if htype == "text" and not (self.header_text or "").strip():
            return False, "Header text is required when header_type is text"
        if htype in {"image", "video", "document"}:
            chosen = sum(1 for v in [self.header_media_path.strip(), self.header_media_url.strip(), self.header_media_id.strip()] if v)
            if chosen == 0:
                return False, "Choose one media source: Path or URL or existing Media ID"
            if chosen > 1:
                return False, "Only one media source allowed (Path OR URL OR Media ID)"

        if mode == "template":
            if not (self.template or "").strip():
                return False, "Template name is required for template messages"
            for i, fb in enumerate(self.flow_buttons):
                if not fb.is_complete():
                    return False, f"Flow button #{i+1} is incomplete"
            # button_params_groups and body_params are free-form; no further checks
            return True, ""

        # interactive mode
        if not (self.body_text or "").strip():
            return False, "Body text is required for interactive CTA messages"
        if not self.ctas:
            return False, "At least one CTA button is required for interactive messages"
        if not self.ctas[0].is_complete():
            return False, "First CTA button is incomplete"
        if (self.ctas[0].type or "url").lower() == "copy_code":
            return False, "Interactive CTA does not support copy_code buttons"
        return True, ""

    def body_params_csv(self) -> str:
        return "|".join([p.strip() for p in self.body_params if str(p).strip()])

    def button_params_csv(self) -> str:
        parts: List[str] = []
        for group in self.button_params_groups:
            g = "|".join([p.strip() for p in group if str(p).strip()])
            if g:
                parts.append(g)
        return ",".join(parts)

    def copy_code_buttons(self) -> List[Dict[str, str]]:
        buttons: List[Dict[str, str]] = []
        for cta in self.ctas:
            if (cta.type or "").lower() != "copy_code":
                continue
            coupon = (cta.coupon_code or "").strip()
            if not coupon:
                continue
            buttons.append({
                "index": str(len(buttons)),
                "coupon_code": coupon,
            })
        return buttons

    def csv_headers(self) -> List[str]:
        headers = [
            "phone",
            "msg_type",
            # Template-mode columns
            "template",
            "lang",
            "body_params",
            "header_type",
            "header_text",
            "header_media_path",
            "header_media_url",
            "header_media_id",
            "button_params",
            # Interactive-mode columns
            "body_text",
            "footer_text",
        ]
        # Flow buttons headers (template mode)
        for n, _ in enumerate(self.flow_buttons):
            headers.extend([
                f"button{n}_type",
                f"button{n}_index",
                f"button{n}_flow_token",
                f"button{n}_flow_action",
                f"button{n}_navigate_screen",
            ])
        # CTA buttons headers (include all provided)
        for n, _ in enumerate(self.ctas):
            headers.extend([
                f"cta{n}_type",
                f"cta{n}_text",
                f"cta{n}_url",
                f"cta{n}_phone",
                f"cta{n}_coupon_code",
            ])
        return headers

    def csv_row_for(self, phone: str) -> Dict[str, str]:
        row: Dict[str, str] = {
            "phone": phone.strip(),
            "msg_type": (self.msg_type or "template").strip().lower(),
            # Template-mode
            "template": (self.template or "").strip(),
            "lang": (self.lang or "").strip(),
            "body_params": self.body_params_csv(),
            "header_type": (self.header_type or "none").strip().lower(),
            "header_text": self.header_text.strip() if self.header_type == "text" else "",
            "header_media_path": self.header_media_path.strip() if self.header_media_path else "",
            "header_media_url": self.header_media_url.strip() if self.header_media_url else "",
            "header_media_id": self.header_media_id.strip() if self.header_media_id else "",
            "button_params": self.button_params_csv(),
            # Interactive-mode
            "body_text": (self.body_text or "").strip(),
            "footer_text": (self.footer_text or "").strip(),
        }
        for n, fb in enumerate(self.flow_buttons):
            row.update(fb.to_csv_columns(n))
        for n, cta in enumerate(self.ctas):
            row.update({
                f"cta{n}_type": (cta.type or "").strip(),
                f"cta{n}_text": (cta.text or "").strip(),
                f"cta{n}_url": (cta.url or "").strip(),
                f"cta{n}_phone": (cta.phone or "").strip(),
                f"cta{n}_coupon_code": (cta.coupon_code or "").strip(),
            })
        return row


def build_csv_rows(phones: Iterable[str], config: TemplateConfig) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    seen = set()
    for raw in phones:
        p = (raw or "").strip()
        if not p:
            continue
        # Avoid duplicates within one export batch
        if p in seen:
            continue
        seen.add(p)
        rows.append(config.csv_row_for(p))
    return rows


def write_csv(path: Path, rows: List[Dict[str, str]], headers: Optional[List[str]] = None) -> None:
    import csv

    if not rows:
        raise ValueError("No rows to write")
    fieldnames = headers or list(rows[0].keys())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def preview_payload(phone: str, config: TemplateConfig) -> Dict[str, Any]:
    """
    Build a WhatsApp payload (without sending) using the existing
    WhatsAppClient helper methods. No token is needed just to build.
    """
    dummy_client = WhatsAppClient(config=type("_C", (), {"token": "", "phone_number_id": "", "api_version": "v20.0", "messages_url": "", "media_url": ""})())  # type: ignore

    mode = (config.msg_type or "template").lower()
    if mode == "interactive":
        # Use the first CTA for preview
        cta = config.ctas[0] if config.ctas else CtaButton()
        cta_type = (cta.type or "url").lower()
        if cta_type == "copy_code":
            raise ValueError("Interactive CTA does not support copy_code buttons. Switch to template mode.")
        if cta_type == "call":
            return dummy_client.build_interactive_cta_call(
                to=phone,
                body_text=config.body_text or "",
                phone_number=cta.phone or "",
                display_text=cta.text or "Call",
                footer_text=(config.footer_text or None),
            )
        else:
            return dummy_client.build_interactive_cta_url(
                to=phone,
                body_text=config.body_text or "",
                url=cta.url or "",
                display_text=cta.text or "View",
                footer_text=(config.footer_text or None),
            )

    # Default: template
    components = dummy_client.build_template_components(
        header_type=config.header_type,
        header_text=config.header_text or None,
        header_media_id=(config.header_media_id or None),
        header_media_link=(config.header_media_url or None),
        body_params=config.body_params or None,
        button_params=(config.button_params_groups or None),
        button_flow=[
            {
                "index": fb.index,
                "flow_token": fb.flow_token,
                "flow_action": fb.flow_action,
                "navigate_screen": fb.navigate_screen,
            }
            for fb in config.flow_buttons
        ]
        or None,
        button_copy_code=config.copy_code_buttons() or None,
    )
    payload = dummy_client.build_template_payload(
        to=phone,
        template=config.template,
        lang=config.lang or "en_US",
        components=components if components else None,
    )
    return payload
