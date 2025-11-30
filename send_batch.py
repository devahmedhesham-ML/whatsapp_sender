from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import time
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from dotenv import load_dotenv

from whatsapp_client import MediaCache, WhatsAppClient, WhatsAppConfig


def parse_args() -> argparse.Namespace:
    default_input = (Path(__file__).resolve().parent / "samples" / "recipients.csv")
    p = argparse.ArgumentParser(description="Batch send WhatsApp template messages from CSV")
    p.add_argument("--input", default=str(default_input), help=f"Path to CSV file (default: {default_input})")
    p.add_argument("--token", help="Override WhatsApp token (else from .env)")
    p.add_argument("--phone-number-id", help="Override WhatsApp phone number ID (else from .env)")
    p.add_argument("--api-version", default=None, help="Graph API version (default from .env or v20.0)")
    p.add_argument("--delay-ms", type=int, default=0, help="Delay between sends in milliseconds")
    p.add_argument("--max", type=int, default=None, help="Max rows to process")
    p.add_argument("--dry-run", action="store_true", help="Build payloads only; do not send")
    p.add_argument("--log-requests", action="store_true", help="Print request bodies for sends/uploads")
    p.add_argument("--async-send", action="store_true", help="Use asyncio worker pool for sending")
    p.add_argument("--msg-per-sec", type=int, default=80, help="Maximum messages per second in async mode")
    p.add_argument("--async-workers", type=int, default=None, help="Async worker count (default derived from msg-per-sec)")
    return p.parse_args()


def load_config(args: argparse.Namespace) -> WhatsAppConfig:
    load_dotenv(override=False)
    token = args.token or os.getenv("WHATSAPP_TOKEN")
    phone_id = args.phone_number_id or os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    api_version = args.api_version or os.getenv("WHATSAPP_API_VERSION", "v20.0")
    if not token or not phone_id:
        print("Missing token or phone number ID. Set in .env or pass flags.", file=sys.stderr)
        sys.exit(2)
    return WhatsAppConfig(token=token, phone_number_id=phone_id, api_version=api_version)


def ensure_logs_dir() -> Path:
    logs = Path("logs")
    logs.mkdir(parents=True, exist_ok=True)
    return logs


def parse_list(value: Optional[str], sep: str = "|") -> Optional[List[str]]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return [v.strip() for v in value.split(sep)]


def parse_button_params(raw: Optional[str]) -> Optional[List[List[str]]]:
    # Example: "A|B,C" => [["A","B"],["C"]]
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        return None
    return [parse_list(p, sep="|") or [] for p in parts]


def infer_button_params_from_cta(row: dict) -> Optional[List[List[str]]]:
    """
    When users populate CTA URL columns for template messages (cta{n}_type=url, cta{n}_url=value),
    treat those values as button dynamic parameters so they end up in the payload even if the
    button_params column is empty.
    """
    groups: List[List[str]] = []
    for n in range(0, 10):
        cta_type = (row.get(f"cta{n}_type") or "").strip().lower()
        if cta_type != "url":
            continue
        url_value = (row.get(f"cta{n}_url") or "").strip()
        if url_value:
            groups.append([url_value])
    return groups or None


@dataclass
class BatchProgressEvent:
    index: int
    phone: str
    status: str
    sent: int
    skipped: int
    errors: int
    dry_run: bool
    total: Optional[int]
    started_at: float
    timestamp: float
    message: str = ""

    @property
    def processed(self) -> int:
        return self.sent + self.skipped + self.errors

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, self.timestamp - self.started_at)

    @property
    def messages_per_second(self) -> float:
        elapsed = self.elapsed_seconds
        if elapsed <= 0:
            return 0.0
        return self.sent / elapsed


@dataclass
class BatchSendResult:
    started_at: float
    finished_at: float
    total_rows: Optional[int]
    processed: int
    sent: int
    skipped: int
    errors: int
    dry_run: bool
    aborted: bool = False

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, self.finished_at - self.started_at)

    @property
    def messages_per_second(self) -> float:
        elapsed = self.elapsed_seconds
        if elapsed <= 0:
            return 0.0
        return self.sent / elapsed


ProgressCallback = Callable[[BatchProgressEvent], None]


def _write_log_line(handle: Optional[Any], payload: Dict[str, Any], lock: Optional[threading.Lock] = None) -> None:
    if not handle:
        return
    line = json.dumps(payload) + "\n"
    if lock:
        with lock:
            handle.write(line)
    else:
        handle.write(line)


def _prepare_row(
    row: Dict[str, Any],
    client: WhatsAppClient,
) -> Tuple[str, str, Optional[Dict[str, Any]], str]:
    """
    Prepare a WhatsApp payload for a CSV row.
    Returns (status, phone, payload, message)
    where status is one of: 'ready', 'skip', 'error'
    """
    phone = (row.get("phone") or "").strip()
    if not phone:
        return "skip", "", None, "missing phone"

    msg_type = (row.get("msg_type") or "template").strip().lower()
    if msg_type == "interactive":
        body_text = (row.get("body_text") or "").strip()
        footer_text = (row.get("footer_text") or "").strip() or None

        cta_type = None
        cta_text = None
        cta_url = None
        cta_phone = None
        for n in range(0, 10):
            ct = (row.get(f"cta{n}_type") or "").strip().lower()
            if ct:
                cta_type = ct
                cta_text = (row.get(f"cta{n}_text") or "").strip() or ("View" if ct == "url" else "Call")
                cta_url = (row.get(f"cta{n}_url") or "").strip() or None
                cta_phone = (row.get(f"cta{n}_phone") or "").strip() or None
                break

        if not body_text or not cta_type:
            return "skip", phone, None, "interactive needs body_text and at least one CTA"
        if cta_type == "copy_code":
            return "skip", phone, None, "interactive CTA does not support copy_code"

        try:
            if cta_type == "call":
                payload = client.build_interactive_cta_call(
                    to=phone,
                    body_text=body_text,
                    phone_number=cta_phone or "",
                    display_text=cta_text or "Call",
                    footer_text=footer_text,
                )
            else:
                payload = client.build_interactive_cta_url(
                    to=phone,
                    body_text=body_text,
                    url=cta_url or "",
                    display_text=cta_text or "View",
                    footer_text=footer_text,
                )
        except Exception as e:
            return "error", phone, None, f"build_failed: {e}"
        return "ready", phone, payload, ""

    # default: template mode
    template = (row.get("template") or "").strip()
    lang = (row.get("lang") or "en_US").strip()
    if not template:
        return "skip", phone, None, "missing template for template msg_type"

    header_type = (row.get("header_type") or "none").strip().lower()
    header_text = (row.get("header_text") or None)
    header_media_path = (row.get("header_media_path") or "").strip() or None
    header_media_url = (row.get("header_media_url") or "").strip() or None
    header_media_id_csv = (row.get("header_media_id") or "").strip() or None
    header_media_id = header_media_id_csv
    if header_type in ("image", "video", "document"):
        if not header_media_id and header_media_path:
            try:
                media_info = client.upload_media(Path(header_media_path))
                header_media_id = media_info["id"]
            except Exception as e:
                return "error", phone, None, f"media_upload_failed: {e}"

    body_params = parse_list(row.get("body_params")) or []
    button_params = parse_button_params(row.get("button_params"))
    if not button_params:
        button_params = infer_button_params_from_cta(row)

    flow_buttons: List[dict] = []
    for n in range(0, 10):
        btype = (row.get(f"button{n}_type") or "").strip().lower()
        if btype != "flow":
            continue
        fb = {
            "index": (row.get(f"button{n}_index") or str(n)).strip(),
            "flow_token": (row.get(f"button{n}_flow_token") or "").strip(),
            "flow_action": (row.get(f"button{n}_flow_action") or "").strip(),
            "navigate_screen": (row.get(f"button{n}_navigate_screen") or "").strip(),
        }
        flow_buttons.append(fb)

    copy_code_buttons: List[dict] = []
    for n in range(0, 10):
        cta_type = (row.get(f"cta{n}_type") or "").strip().lower()
        if cta_type != "copy_code":
            continue
        coupon_code = (row.get(f"cta{n}_coupon_code") or "").strip()
        if not coupon_code:
            return "error", phone, None, f"cta{n}_coupon_code is required for copy_code buttons"
        copy_code_buttons.append({
            "index": str(len(copy_code_buttons)),
            "coupon_code": coupon_code,
        })

    try:
        components = client.build_template_components(
            header_type=header_type,
            header_text=header_text,
            header_media_id=header_media_id,
            header_media_link=header_media_url,
            body_params=body_params,
            button_params=button_params,
            button_flow=flow_buttons if flow_buttons else None,
            button_copy_code=copy_code_buttons if copy_code_buttons else None,
        )
        payload = client.build_template_payload(
            to=phone,
            template=template,
            lang=lang,
            components=components if components else None,
        )
    except Exception as e:
        return "error", phone, None, f"build_failed: {e}"

    return "ready", phone, payload, ""


class AsyncRateLimiter:
    def __init__(self, rate: int) -> None:
        self.rate = max(1, rate)
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] >= 1.0:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.rate:
                    self._timestamps.append(now)
                    return
                wait_for = 1.0 - (now - self._timestamps[0])
            await asyncio.sleep(max(wait_for, 0.01))


def _send_payload_sync(
    client: WhatsAppClient,
    payload: Dict[str, Any],
    *,
    dry_run: bool,
    log_handle: Optional[Any],
    log_lock: Optional[threading.Lock],
) -> Tuple[str, str]:
    if dry_run:
        _write_log_line(log_handle, {"status": "dry_run", "payload": payload}, log_lock)
        return "dry_run", ""
    try:
        resp = client.send_message(payload)
        _write_log_line(log_handle, {"status": "sent", "response": resp}, log_lock)
        return "sent", ""
    except Exception as e:
        message = f"send_failed: {e}"
        _write_log_line(log_handle, {"status": "error", "reason": message, "payload": payload}, log_lock)
        return "error", message


def run_batch_from_rows(
    rows: Iterable[Dict[str, Any]],
    client: WhatsAppClient,
    *,
    dry_run: bool,
    delay_ms: int = 0,
    log_path: Optional[Path] = None,
    total_rows: Optional[int] = None,
    progress_callback: Optional[ProgressCallback] = None,
    stop_event: Optional[threading.Event] = None,
    pause_event: Optional[threading.Event] = None,
) -> BatchSendResult:
    """
    Process already-built CSV rows, send WhatsApp messages, and optionally report progress.
    """
    started_at = time.time()
    stats = BatchSendResult(
        started_at=started_at,
        finished_at=started_at,
        total_rows=total_rows,
        processed=0,
        sent=0,
        skipped=0,
        errors=0,
        dry_run=dry_run,
        aborted=False,
    )
    total_count = total_rows
    rows_sequence: Optional[Sequence[Dict[str, Any]]] = rows if isinstance(rows, Sequence) else None
    if total_count is None and rows_sequence is not None:
        total_count = len(rows_sequence)

    log_handle = None
    log_lock: Optional[threading.Lock] = None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("w", encoding="utf-8")
        log_lock = threading.Lock()

    def emit(status: str, phone: str, message: str = "", row_index: int = 0) -> None:
        if not progress_callback:
            return
        evt = BatchProgressEvent(
            index=row_index,
            phone=phone,
            status=status,
            sent=stats.sent,
            skipped=stats.skipped,
            errors=stats.errors,
            dry_run=dry_run,
            total=total_count,
            started_at=started_at,
            timestamp=time.time(),
            message=message,
        )
        progress_callback(evt)

    try:
        iterable = rows_sequence if rows_sequence is not None else list(rows)
        if rows_sequence is None and total_count is None:
            try:
                total_count = len(iterable)  # type: ignore[arg-type]
            except Exception:
                total_count = None
        for idx, row in enumerate(iterable, start=1):
            if stop_event and stop_event.is_set():
                stats.aborted = True
                break

            while pause_event and pause_event.is_set():
                if stop_event and stop_event.is_set():
                    stats.aborted = True
                    break
                time.sleep(0.2)
            if stats.aborted:
                break

            status, phone, payload, message = _prepare_row(row, client)
            if status == "skip":
                stats.skipped += 1
                _write_log_line(log_handle, {"status": "skip", "reason": message, "row": row}, log_lock)
                emit("skip", phone, message, idx)
                continue
            if status == "error":
                stats.errors += 1
                _write_log_line(log_handle, {"status": "error", "reason": message, "row": row}, log_lock)
                emit("error", phone, message, idx)
                continue
            if payload is None:
                continue

            send_status, send_message = _send_payload_sync(
                client,
                payload,
                dry_run=dry_run,
                log_handle=log_handle,
                log_lock=log_lock,
            )
            if send_status in {"sent", "dry_run"}:
                stats.sent += 1
                stats.processed += 1
                emit(send_status, phone, row_index=idx)
            else:
                stats.errors += 1
                emit("error", phone, send_message, idx)

            if send_status in {"sent", "dry_run"} and delay_ms > 0 and not stats.aborted:
                time.sleep(delay_ms / 1000.0)
    finally:
        if log_handle:
            log_handle.close()
        stats.finished_at = time.time()

    return stats


async def async_run_batch_from_rows(
    rows: Iterable[Dict[str, Any]],
    client_factory: Callable[[], WhatsAppClient],
    *,
    dry_run: bool,
    msg_per_sec: int = 80,
    async_workers: Optional[int] = None,
    delay_ms: int = 0,
    log_path: Optional[Path] = None,
    total_rows: Optional[int] = None,
    progress_callback: Optional[ProgressCallback] = None,
    stop_event: Optional[threading.Event] = None,
    pause_event: Optional[threading.Event] = None,
) -> BatchSendResult:
    started_at = time.time()
    stats = BatchSendResult(
        started_at=started_at,
        finished_at=started_at,
        total_rows=total_rows,
        processed=0,
        sent=0,
        skipped=0,
        errors=0,
        dry_run=dry_run,
        aborted=False,
    )
    rows_list = list(rows)
    stats.total_rows = total_rows if total_rows is not None else len(rows_list)

    log_handle = None
    log_lock: Optional[threading.Lock] = None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("w", encoding="utf-8")
        log_lock = threading.Lock()

    limiter = AsyncRateLimiter(max(1, msg_per_sec or 1))
    if async_workers and async_workers > 0:
        worker_count = async_workers
    else:
        worker_count = min(max(1, msg_per_sec // 4 or 1), 32)

    queue: asyncio.Queue[Optional[Tuple[int, Dict[str, Any]]]] = asyncio.Queue()
    for idx, row in enumerate(rows_list, start=1):
        queue.put_nowait((idx, row))
    for _ in range(worker_count):
        queue.put_nowait(None)

    stats_lock = asyncio.Lock()

    def emit(status: str, phone: str, message: str = "", row_index: int = 0) -> None:
        if not progress_callback:
            return
        evt = BatchProgressEvent(
            index=row_index,
            phone=phone,
            status=status,
            sent=stats.sent,
            skipped=stats.skipped,
            errors=stats.errors,
            dry_run=dry_run,
            total=stats.total_rows,
            started_at=started_at,
            timestamp=time.time(),
            message=message,
        )
        progress_callback(evt)

    async def update_stats(status: str, phone: str, message: str, idx: int) -> None:
        async with stats_lock:
            if status in {"sent", "dry_run"}:
                stats.sent += 1
                stats.processed += 1
            elif status == "skip":
                stats.skipped += 1
            elif status == "error":
                stats.errors += 1
        emit(status, phone, message, idx)

    async def worker() -> None:
        client = client_factory()
        try:
            while True:
                item = await queue.get()
                if item is None:
                    queue.task_done()
                    break

                idx, row = item
                if stop_event and stop_event.is_set():
                    stats.aborted = True
                    queue.task_done()
                    continue

                while pause_event and pause_event.is_set():
                    if stop_event and stop_event.is_set():
                        stats.aborted = True
                        break
                    await asyncio.sleep(0.2)
                if stats.aborted:
                    queue.task_done()
                    continue

                status, phone, payload, message = await asyncio.to_thread(_prepare_row, row, client)
                if status == "skip":
                    _write_log_line(log_handle, {"status": "skip", "reason": message, "row": row}, log_lock)
                    await update_stats("skip", phone, message, idx)
                    queue.task_done()
                    continue
                if status == "error":
                    _write_log_line(log_handle, {"status": "error", "reason": message, "row": row}, log_lock)
                    await update_stats("error", phone, message, idx)
                    queue.task_done()
                    continue
                if payload is None:
                    queue.task_done()
                    continue

                await limiter.acquire()
                send_status, send_message = await asyncio.to_thread(
                    _send_payload_sync,
                    client,
                    payload,
                    dry_run=dry_run,
                    log_handle=log_handle,
                    log_lock=log_lock,
                )

                if send_status in {"sent", "dry_run"} and delay_ms > 0:
                    await asyncio.sleep(delay_ms / 1000.0)

                await update_stats(
                    send_status if send_status in {"sent", "dry_run"} else "error",
                    phone,
                    send_message,
                    idx,
                )
                queue.task_done()
        finally:
            try:
                client.session.close()
            except Exception:
                pass

    tasks = [asyncio.create_task(worker()) for _ in range(worker_count)]
    await queue.join()
    for task in tasks:
        await task

    stats.finished_at = time.time()
    return stats
def main() -> None:
    args = parse_args()
    config = load_config(args)
    logs_dir = ensure_logs_dir()
    out_path = logs_dir / ("dry_run_" if args.dry_run else "sent_") / f"out_{int(time.time())}.jsonl"

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Input CSV not found: {input_path}", file=sys.stderr)
        sys.exit(2)

    rows: List[Dict[str, Any]] = []
    with input_path.open(newline="", encoding="utf-8") as f_in:
        reader = csv.DictReader(f_in)
        for row in reader:
            rows.append(row)
            if args.max is not None and len(rows) >= args.max:
                break

    media_cache = MediaCache(Path("media_cache.json"))
    if args.async_send:
        msg_per_sec = max(1, min(80, args.msg_per_sec or 80))
        def client_factory() -> WhatsAppClient:
            return WhatsAppClient(config=config, media_cache=media_cache, log_requests=args.log_requests)

        result = asyncio.run(
            async_run_batch_from_rows(
                rows,
                client_factory,
                dry_run=args.dry_run,
                msg_per_sec=msg_per_sec,
                async_workers=args.async_workers,
                delay_ms=args.delay_ms,
                log_path=out_path,
                total_rows=len(rows),
            )
        )
    else:
        client = WhatsAppClient(config=config, media_cache=media_cache, log_requests=args.log_requests)
        result = run_batch_from_rows(
            rows,
            client,
            dry_run=args.dry_run,
            delay_ms=args.delay_ms,
            log_path=out_path,
            total_rows=len(rows),
        )
    summary = (
        f"Done. processed={result.processed} sent={result.sent} errors={result.errors} "
        f"skipped={result.skipped} elapsed={result.elapsed_seconds:.2f}s "
        f"mps={result.messages_per_second:.2f}"
    )
    if result.aborted:
        summary += " (aborted)"
    print(summary)
    print(f"Output: {out_path}")


if __name__ == "__main__":
    main()
