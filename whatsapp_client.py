from __future__ import annotations

import hashlib
import json
import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import threading

import requests


@dataclass
class WhatsAppConfig:
    token: str
    phone_number_id: str
    api_version: str = "v20.0"
    base_url: str = "https://graph.facebook.com"

    @property
    def messages_url(self) -> str:
        return f"{self.base_url}/{self.api_version}/{self.phone_number_id}/messages"

    @property
    def media_url(self) -> str:
        return f"{self.base_url}/{self.api_version}/{self.phone_number_id}/media"


class MediaCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._cache: Dict[str, Any] = {}
        self._lock = threading.Lock()
        if self.path.exists():
            try:
                self._cache = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self._cache = {}

    def get(self, digest: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._cache.get(digest)

    def set(self, digest: str, data: Dict[str, Any]) -> None:
        with self._lock:
            self._cache[digest] = data
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8")


class WhatsAppClient:
    def __init__(self, config: WhatsAppConfig, media_cache: Optional[MediaCache] = None, *, log_requests: bool = False) -> None:
        self.config = config
        self.media_cache = media_cache or MediaCache(Path("media_cache.json"))
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.config.token}",
        })
        self.log_requests = log_requests

    def _hash_file(self, file_path: Path) -> str:
        h = hashlib.sha256()
        with file_path.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return f"sha256:{h.hexdigest()}"

    def upload_media(self, file_path: Path, mime_type: Optional[str] = None) -> Dict[str, Any]:
        file_path = file_path.resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"Media not found: {file_path}")

        digest = self._hash_file(file_path)
        cached = self.media_cache.get(digest)
        if cached and "id" in cached:
            return cached

        if not mime_type:
            mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"

        files = {
            "file": (file_path.name, file_path.open("rb"), mime_type),
        }
        data = {
            "messaging_product": "whatsapp",
            "type": mime_type,
        }

        if self.log_requests:
            print(f"[upload_media] → POST {self.config.media_url} data=" + json.dumps(data) + f" file={file_path.name} mime={mime_type}")
        resp = self.session.post(self.config.media_url, files=files, data=data, timeout=60)
        if self.log_requests:
            preview = ""
            try:
                preview = resp.text[:500]
            except Exception:
                preview = "<unreadable>"
            print(f"[upload_media] ← {resp.status_code} body={preview}")
        if resp.status_code >= 400:
            raise RuntimeError(f"Media upload failed: {resp.status_code} {resp.text}")
        data = resp.json()
        media_id = data.get("id")
        if self.log_requests:
            print(f"[upload_media] Uploaded media_id={media_id}")
        if not media_id:
            raise RuntimeError(f"Media upload response missing id: {data}")

        record = {
            "id": media_id,
            "mime_type": mime_type,
            "path": str(file_path),
            "uploaded_at": int(time.time()),
        }
        self.media_cache.set(digest, record)
        return record

    def build_template_components(
        self,
        *,
        header_type: str = "none",
        header_text: Optional[str] = None,
        header_media_id: Optional[str] = None,
        header_media_link: Optional[str] = None,
        body_params: Optional[List[str]] = None,
        button_params: Optional[List[List[str]]] = None,
        button_flow: Optional[List[Dict[str, str]]] = None,
        button_copy_code: Optional[List[Dict[str, str]]] = None,
    ) -> List[Dict[str, Any]]:
        components: List[Dict[str, Any]] = []

        # Header component
        htype = (header_type or "none").lower()
        if htype != "none":
            header: Dict[str, Any] = {"type": "header", "parameters": []}
            if htype == "text":
                if header_text is None:
                    raise ValueError("header_type=text requires header_text")
                header["parameters"].append({"type": "text", "text": str(header_text)})
            elif htype in ("image", "video", "document"):
                media_key = htype
                media_payload: Dict[str, Any] = {}
                if header_media_id:
                    media_payload["id"] = header_media_id
                elif header_media_link:
                    media_payload["link"] = header_media_link
                else:
                    raise ValueError(f"header_type={htype} requires media id or link")
                header["parameters"].append({"type": htype, media_key: media_payload})
            else:
                raise ValueError(f"Unsupported header_type: {header_type}")
            components.append(header)

        # Body component
        if body_params:
            body = {"type": "body", "parameters": [{"type": "text", "text": str(x)} for x in body_params]}
            components.append(body)

        # Button components (URL dynamic parameters only)
        if button_params:
            for idx, params in enumerate(button_params):
                if not params:
                    continue
                components.append({
                    "type": "button",
                    "sub_type": "url",
                    "index": str(idx),
                    "parameters": [{"type": "text", "text": str(p)} for p in params],
                })

        # Button components (Flow actions)
        if button_flow:
            for fb in button_flow:
                idx = str(fb.get("index", "0"))
                flow_token = fb.get("flow_token")
                flow_action = fb.get("flow_action")
                navigate_screen = fb.get("navigate_screen")
                if not flow_token or not flow_action or not navigate_screen:
                    raise ValueError("button_flow requires flow_token, flow_action, navigate_screen")
                components.append({
                    "type": "button",
                    "sub_type": "flow",
                    "index": idx,
                    "parameters": [
                        {
                            "type": "action",
                            "action": {
                                "flow_token": str(flow_token),
                                "flow_action_data": {
                                    "flow_action": str(flow_action),
                                    "navigate_screen": str(navigate_screen),
                                },
                            },
                        }
                    ],
                })

        # Button components (coupon copy)
        if button_copy_code:
            for btn in button_copy_code:
                idx = str(btn.get("index", "0"))
                coupon_code = btn.get("coupon_code")
                if not coupon_code:
                    raise ValueError("button_copy_code entries require coupon_code")
                components.append({
                    "type": "button",
                    "sub_type": "COPY_CODE",
                    "index": idx,
                    "parameters": [
                        {
                            "type": "coupon_code",
                            "coupon_code": str(coupon_code),
                        }
                    ],
                })

        return components

    def build_template_payload(
        self,
        *,
        to: str,
        template: str,
        lang: str = "en_US",
        components: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": lang},
            },
        }
        if components:
            payload["template"]["components"] = components
        return payload

    def send_message(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.log_requests:
            print(f"[send_message] → POST {self.config.messages_url} json=" + json.dumps(payload))
        resp = self.session.post(self.config.messages_url, json=payload, headers=headers, timeout=60)
        if self.log_requests:
            preview = ""
            try:
                preview = resp.text[:500]
            except Exception:
                preview = "<unreadable>"
            print(f"[send_message] ← {resp.status_code} body={preview}")
        if resp.status_code >= 400:
            raise RuntimeError(f"Send failed: {resp.status_code} {resp.text}")
        return resp.json()

    # Interactive CTA helpers (non-template)
    def build_interactive_cta_url(
        self,
        *,
        to: str,
        body_text: str,
        url: str,
        display_text: str = "View",
        footer_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        interactive: Dict[str, Any] = {
            "type": "cta_url",
            "body": {"text": str(body_text)},
            "action": {
                "name": "cta_url",
                "parameters": {
                    "display_text": str(display_text or "View"),
                    "url": str(url),
                },
            },
        }
        if footer_text:
            interactive["footer"] = {"text": str(footer_text)}
        return {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": interactive,
        }

    def build_interactive_cta_call(
        self,
        *,
        to: str,
        body_text: str,
        phone_number: str,
        display_text: str = "Call",
        footer_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        interactive: Dict[str, Any] = {
            "type": "cta_call",
            "body": {"text": str(body_text)},
            "action": {
                "name": "cta_call",
                "parameters": {
                    "display_text": str(display_text or "Call"),
                    "phone_number": str(phone_number),
                },
            },
        }
        if footer_text:
            interactive["footer"] = {"text": str(footer_text)}
        return {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": interactive,
        }
