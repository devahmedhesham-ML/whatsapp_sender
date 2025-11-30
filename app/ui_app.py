from __future__ import annotations

import asyncio
import datetime
import json
import os
import queue
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
sys.path.append(str(Path(__file__).resolve().parents[1]))
try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except Exception as e:  # pragma: no cover
    print("Tkinter is required to run the UI:", e, file=sys.stderr)
    raise

from src.payload_builder import TemplateConfig, build_csv_rows, write_csv, preview_payload, CtaButton
from src.send_batch import (
    BatchProgressEvent,
    BatchSendResult,
    async_run_batch_from_rows,
    ensure_logs_dir,
    run_batch_from_rows,
)
from app.template_archive import load_archive, get_template, TemplateSummary
from src.whatsapp_client import WhatsAppClient, WhatsAppConfig, MediaCache


@dataclass
class BatchJobHandle:
    thread: threading.Thread
    stop_event: threading.Event
    pause_event: threading.Event
    queue: "queue.Queue[Any]"
    log_path: Path
    alias: str
    template: str
    dry_run: bool
    total_rows: int
    window: Optional["BatchProgressWindow"] = None
    use_async: bool = False
    msg_per_sec: int = 80
from dotenv import load_dotenv


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("WhatsApp CSV Builder")
        self.minsize(920, 640)

        # State
        self.cta_buttons: List[CtaButton] = []
        self.templates: List[TemplateSummary] = []
        self.saved_templates_path = Path("templates/user_templates.json")
        self.saved_templates: Dict[str, Dict[str, Any]] = {}
        self._autosave_job: Optional[str] = None
        self._cta_drag_index: Optional[int] = None
        self._active_batch: Optional[BatchJobHandle] = None

        # Layout
        self._build_menu()
        self._build_form()
        self._build_cta_buttons()
        self._build_phone_list()
        self._build_actions()
        self._load_archive_default()
        self._load_saved_templates()

    # UI construction
    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="New", command=self._reset_form)
        file_menu.add_command(label="Open CSV...", command=self._open_csv)
        file_menu.add_command(label="Save CSV As...", command=self._save_csv)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        tpl_menu = tk.Menu(menubar, tearoff=False)
        self.saved_templates_menu = tk.Menu(tpl_menu, tearoff=False)
        tpl_menu.add_cascade(label="Saved Templates", menu=self.saved_templates_menu)
        tpl_menu.add_command(label="Reload Saved Templates", command=self._load_saved_templates)
        tpl_menu.add_separator()
        tpl_menu.add_command(label="Select from Archive...", command=self._select_from_archive)
        tpl_menu.add_command(label="Reload Archive", command=self._load_archive_default)
        menubar.add_cascade(label="Templates", menu=tpl_menu)

        media_menu = tk.Menu(menubar, tearoff=False)
        media_menu.add_command(label="Upload Media...", command=self._menu_upload_media)
        media_menu.add_command(label="Open Media Library", command=self._open_media_library)
        menubar.add_cascade(label="Media", menu=media_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="About", command=lambda: messagebox.showinfo(
            "About",
            "WhatsApp CSV Builder\n\nBuild CSV payloads for batch WhatsApp template messages.\nThis tool helps generate rows compatible with send_batch.py",
        ))
        menubar.add_cascade(label="Help", menu=help_menu)

        self.config(menu=menubar)
        self._refresh_saved_templates_menu()

    def _load_archive_default(self) -> None:
        self.templates = load_archive()
        # Optional toast
        # print(f"Loaded {len(self.templates)} templates from archive")

    def _select_from_archive(self) -> None:
        if not self.templates:
            messagebox.showwarning("No Archive", "No templates loaded. Place templates/archive.json and choose Reload Archive.")
            return
        dlg = tk.Toplevel(self)
        dlg.title("Select Template from Archive")
        dlg.transient(self)
        dlg.grab_set()

        names = [t.name for t in self.templates]
        var_name = tk.StringVar(value=names[0])
        ttk.Label(dlg, text="Template:").grid(row=0, column=0, sticky=tk.W, padx=8, pady=8)
        cb = ttk.Combobox(dlg, textvariable=var_name, values=names, state="readonly", width=40)
        cb.grid(row=0, column=1, padx=8, pady=8)

        def on_apply() -> None:
            name = var_name.get()
            sel = get_template(self.templates, name)
            if not sel:
                messagebox.showerror("Not found", f"Template {name} not found in archive")
                return
            self._apply_template_summary(sel)
            dlg.destroy()

        ttk.Button(dlg, text="Cancel", command=dlg.destroy).grid(row=1, column=0, padx=8, pady=8)
        ttk.Button(dlg, text="Apply", command=on_apply).grid(row=1, column=1, padx=8, pady=8, sticky=tk.E)

        dlg.wait_window()

    def _apply_template_summary(self, t: TemplateSummary) -> None:
        # Switch to template mode
        self.var_msg_type.set("template")
        self.var_template.set(t.name)
        # Take language as-is (e.g., 'en')
        self.var_lang.set(t.language or "en_US")
        # Header
        self.var_header_type.set(t.header_type)
        self._render_header_dynamic()
        if t.header_type in ("image", "video", "document"):
            if t.header_example_url:
                self.var_media_source.set("url")
                self._render_media_source()
                self.var_media_url.set(t.header_example_url)
        elif t.header_type == "text":
            # Archive doesn't include header text variables; leave empty
            pass

        # Clear body/url params
        self.var_body_params.set("")
        self.var_button_params.set("")

        if t.flow_buttons:
            messagebox.showinfo(
                "Template Notice",
                "This template includes Flow buttons that are not editable in this UI. "
                "Make sure the template is configured in WhatsApp Manager before sending.",
            )

    def _build_form(self) -> None:
        frm = ttk.LabelFrame(self, text="Message & Template")
        frm.pack(fill=tk.X, padx=10, pady=(10, 6))

        # Line 0: message type
        ttk.Label(frm, text="Message Type:").grid(row=0, column=0, sticky=tk.W, padx=8, pady=6)
        self.var_msg_type = tk.StringVar(value="template")
        self._trace_var_for_autosave(self.var_msg_type)
        ttk.Radiobutton(frm, text="Template", variable=self.var_msg_type, value="template", command=self._on_msg_type_change).grid(row=0, column=1, sticky=tk.W)
        ttk.Radiobutton(frm, text="Interactive (CTA)", variable=self.var_msg_type, value="interactive", command=self._on_msg_type_change).grid(row=0, column=2, sticky=tk.W)

        # Line 1: template, lang
        ttk.Label(frm, text="Template:").grid(row=1, column=0, sticky=tk.W, padx=8, pady=6)
        self.var_template = tk.StringVar()
        self._trace_var_for_autosave(self.var_template)
        ttk.Entry(frm, textvariable=self.var_template, width=40).grid(row=1, column=1, sticky=tk.W)

        ttk.Label(frm, text="Lang:").grid(row=1, column=2, sticky=tk.W, padx=(24, 8))
        self.var_lang = tk.StringVar(value="en_US")
        self._trace_var_for_autosave(self.var_lang)
        ttk.Entry(frm, textvariable=self.var_lang, width=10).grid(row=1, column=3, sticky=tk.W)

        # Line 2: header type + dynamic area
        ttk.Label(frm, text="Header Type:").grid(row=2, column=0, sticky=tk.W, padx=8, pady=6)
        self.var_header_type = tk.StringVar(value="none")
        self._trace_var_for_autosave(self.var_header_type)
        combo = ttk.Combobox(frm, textvariable=self.var_header_type, width=12, state="readonly",
                             values=["none", "text", "image", "video", "document"])
        combo.grid(row=2, column=1, sticky=tk.W)
        combo.bind("<<ComboboxSelected>>", lambda e: self._on_header_type_change())

        self.var_header_text = tk.StringVar()
        self._trace_var_for_autosave(self.var_header_text)
        self.var_media_source = tk.StringVar(value="path")
        self._trace_var_for_autosave(self.var_media_source)
        self.var_media_path = tk.StringVar()
        self._trace_var_for_autosave(self.var_media_path)
        self.var_media_url = tk.StringVar()
        self._trace_var_for_autosave(self.var_media_url)
        self.var_media_id = tk.StringVar()
        self._trace_var_for_autosave(self.var_media_id)

        self.header_dynamic_frame = ttk.Frame(frm)
        self.header_dynamic_frame.grid(row=2, column=2, columnspan=2, sticky=tk.W, padx=(24, 0))
        self._render_header_dynamic()

        # Line 3: body params (template)
        ttk.Label(frm, text="Body Params (| separated):").grid(row=3, column=0, sticky=tk.W, padx=8, pady=6)
        self.var_body_params = tk.StringVar()
        self._trace_var_for_autosave(self.var_body_params)
        ttk.Entry(frm, textvariable=self.var_body_params, width=60).grid(row=3, column=1, columnspan=3, sticky=tk.W)

        # Line 4: url button params (template)
        ttk.Label(frm, text="URL Button Params:").grid(row=4, column=0, sticky=tk.W, padx=8, pady=(6, 4))
        self.var_button_params = tk.StringVar()
        self._trace_var_for_autosave(self.var_button_params)
        ttk.Entry(frm, textvariable=self.var_button_params, width=60).grid(row=4, column=1, columnspan=3, sticky=tk.W)
        ttk.Label(frm, text="Format: group1 '|' joined, groups comma separated (e.g. A1|B2,C3)").grid(
            row=5, column=1, columnspan=3, sticky=tk.W, pady=(0, 8)
        )

        # Line 5: Interactive body/footer (works for interactive)
        inter = ttk.LabelFrame(self, text="Interactive Message (Body & Footer)")
        inter.pack(fill=tk.X, padx=10, pady=(0, 6))
        ttk.Label(inter, text="Body Text:").grid(row=0, column=0, sticky=tk.W, padx=8, pady=6)
        self.var_body_text = tk.StringVar()
        self._trace_var_for_autosave(self.var_body_text)
        ttk.Entry(inter, textvariable=self.var_body_text, width=72).grid(row=0, column=1, columnspan=3, sticky=tk.W)
        ttk.Label(inter, text="Footer Text:").grid(row=1, column=0, sticky=tk.W, padx=8, pady=6)
        self.var_footer_text = tk.StringVar()
        self._trace_var_for_autosave(self.var_footer_text)
        ttk.Entry(inter, textvariable=self.var_footer_text, width=72).grid(row=1, column=1, columnspan=3, sticky=tk.W)

        for i in range(0, 5):
            frm.columnconfigure(i, weight=1)

    def _render_header_dynamic(self) -> None:
        for w in self.header_dynamic_frame.winfo_children():
            w.destroy()
        htype = (self.var_header_type.get() or "none").lower()
        if htype == "text":
            ttk.Label(self.header_dynamic_frame, text="Header Text:").grid(row=0, column=0, sticky=tk.W)
            ttk.Entry(self.header_dynamic_frame, textvariable=self.var_header_text, width=40).grid(row=0, column=1, sticky=tk.W)
        elif htype in ("image", "video", "document"):
            ttk.Label(self.header_dynamic_frame, text="Media Source:").grid(row=0, column=0, sticky=tk.W)
            if self.var_media_source.get() not in {"path", "url", "id"}:
                self.var_media_source.set("path")
            ttk.Radiobutton(self.header_dynamic_frame, text="Local File", variable=self.var_media_source, value="path", command=self._render_media_source).grid(row=0, column=1, sticky=tk.W)
            ttk.Radiobutton(self.header_dynamic_frame, text="URL", variable=self.var_media_source, value="url", command=self._render_media_source).grid(row=0, column=2, sticky=tk.W)
            ttk.Radiobutton(self.header_dynamic_frame, text="Media ID", variable=self.var_media_source, value="id", command=self._render_media_source).grid(row=0, column=3, sticky=tk.W)

            self.media_source_frame = ttk.Frame(self.header_dynamic_frame)
            self.media_source_frame.grid(row=1, column=0, columnspan=4, sticky=tk.W)
            self._render_media_source()
        else:
            ttk.Label(self.header_dynamic_frame, text="No header").grid(row=0, column=0, sticky=tk.W)

    def _render_media_source(self) -> None:
        for w in self.media_source_frame.winfo_children():
            w.destroy()
        src = (self.var_media_source.get() or "path").lower()
        if src == "path":
            ttk.Label(self.media_source_frame, text="Media Path:").grid(row=0, column=0, sticky=tk.W)
            ttk.Entry(self.media_source_frame, textvariable=self.var_media_path, width=48).grid(row=0, column=1, sticky=tk.W)
            ttk.Button(self.media_source_frame, text="Browse", command=self._browse_media_path).grid(row=0, column=2, padx=(6, 0))
        elif src == "url":
            ttk.Label(self.media_source_frame, text="Media URL:").grid(row=0, column=0, sticky=tk.W)
            ttk.Entry(self.media_source_frame, textvariable=self.var_media_url, width=58).grid(row=0, column=1, columnspan=2, sticky=tk.W)
        else:  # id
            ttk.Label(self.media_source_frame, text="Existing Media ID:").grid(row=0, column=0, sticky=tk.W)
            ttk.Entry(self.media_source_frame, textvariable=self.var_media_id, width=58).grid(row=0, column=1, columnspan=2, sticky=tk.W)

    def _build_cta_buttons(self) -> None:
        box = ttk.LabelFrame(self, text="CTA Buttons (template: docs only, interactive: first CTA used)")
        box.pack(fill=tk.BOTH, expand=False, padx=10, pady=(0, 6))

        bar = ttk.Frame(box)
        bar.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(bar, text="Add CTA", command=self._add_cta_button).pack(side=tk.LEFT)
        ttk.Button(bar, text="Remove Selected", command=self._remove_selected_cta_button).pack(side=tk.LEFT, padx=(8, 0))

        cols = ("order", "type", "text", "url", "phone", "coupon_code")
        self.tree_cta = ttk.Treeview(box, columns=cols, show="headings", height=4, selectmode="browse")
        widths = {"order": 60, "type": 90, "text": 160, "url": 200, "phone": 140, "coupon_code": 140}
        for c in cols:
            self.tree_cta.heading(c, text=c)
            self.tree_cta.column(c, width=widths.get(c, 160), anchor=tk.W)
        self.tree_cta.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.tree_cta.bind("<ButtonPress-1>", self._on_cta_press)
        self.tree_cta.bind("<B1-Motion>", self._on_cta_drag)
        self.tree_cta.bind("<ButtonRelease-1>", self._on_cta_release)

    def _build_phone_list(self) -> None:
        frm = ttk.LabelFrame(self, text="Phone Numbers (one per line)")
        frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))
        self.txt_phones = tk.Text(frm, height=10)
        self.txt_phones.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    def _build_actions(self) -> None:
        frm = ttk.Frame(self)
        frm.pack(fill=tk.X, padx=10, pady=10)
        ttk.Button(frm, text="Preview Payload", command=self._preview_payload).pack(side=tk.LEFT)
        right = ttk.Frame(frm)
        right.pack(side=tk.RIGHT)
        ttk.Button(right, text="Generate CSV...", command=self._save_csv).pack(side=tk.RIGHT)
        ttk.Button(right, text="Send Batch...", command=self._start_send_batch).pack(side=tk.RIGHT, padx=(8, 0))
        self.var_send_dry_run = tk.BooleanVar(value=True)
        ttk.Checkbutton(right, text="Dry Run (no send)", variable=self.var_send_dry_run).pack(side=tk.RIGHT, padx=(8, 0))
        self.var_use_async = tk.BooleanVar(value=True)
        ttk.Checkbutton(right, text="Async mode", variable=self.var_use_async).pack(side=tk.RIGHT, padx=(8, 0))
        rate_frame = ttk.Frame(right)
        rate_frame.pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Label(rate_frame, text="msg/sec").pack(side=tk.RIGHT)
        self.var_msg_per_sec = tk.IntVar(value=80)
        tk.Spinbox(rate_frame, from_=1, to=80, width=4, textvariable=self.var_msg_per_sec).pack(side=tk.RIGHT, padx=(4, 0))

    # Handlers
    def _on_header_type_change(self) -> None:
        self._render_header_dynamic()

    def _browse_media_path(self) -> None:
        path = filedialog.askopenfilename(title="Select media file")
        if path:
            self.var_media_path.set(path)


    def _add_cta_button(self) -> None:
        # In interactive mode, only one CTA is supported by the API; enforce single.
        if self.var_msg_type.get() == "interactive" and len(self.cta_buttons) >= 1:
            messagebox.showwarning("Limit", "Interactive messages support only one CTA. Remove the existing one to add a new CTA.")
            return

        dlg = tk.Toplevel(self)
        dlg.title("Add CTA Button")
        dlg.transient(self)
        dlg.grab_set()

        v_type = tk.StringVar(value="url")
        v_text = tk.StringVar()
        v_url = tk.StringVar()
        v_phone = tk.StringVar()
        v_coupon = tk.StringVar()

        ttk.Label(dlg, text="Type:").grid(row=0, column=0, sticky=tk.W, padx=8, pady=6)
        cmb_type = ttk.Combobox(dlg, textvariable=v_type, values=["url", "call", "copy_code"], state="readonly", width=12)
        cmb_type.grid(row=0, column=1, sticky=tk.W)
        ttk.Label(dlg, text="Button Text:").grid(row=1, column=0, sticky=tk.W, padx=8, pady=6)
        entry_text = ttk.Entry(dlg, textvariable=v_text, width=30)
        entry_text.grid(row=1, column=1, sticky=tk.W)
        ttk.Label(dlg, text="URL (url type):").grid(row=2, column=0, sticky=tk.W, padx=8, pady=6)
        entry_url = ttk.Entry(dlg, textvariable=v_url, width=40)
        entry_url.grid(row=2, column=1, sticky=tk.W)
        ttk.Label(dlg, text="Phone (call type):").grid(row=3, column=0, sticky=tk.W, padx=8, pady=6)
        entry_phone = ttk.Entry(dlg, textvariable=v_phone, width=40)
        entry_phone.grid(row=3, column=1, sticky=tk.W)
        ttk.Label(dlg, text="Coupon code (copy_code):").grid(row=4, column=0, sticky=tk.W, padx=8, pady=6)
        entry_coupon = ttk.Entry(dlg, textvariable=v_coupon, width=30)
        entry_coupon.grid(row=4, column=1, sticky=tk.W)

        def refresh_fields(*_args: object) -> None:
            ctype = v_type.get().strip()
            entry_text.configure(state=tk.NORMAL if ctype != "copy_code" else tk.DISABLED)
            entry_url.configure(state=tk.NORMAL if ctype == "url" else tk.DISABLED)
            entry_phone.configure(state=tk.NORMAL if ctype == "call" else tk.DISABLED)
            entry_coupon.configure(state=tk.NORMAL if ctype == "copy_code" else tk.DISABLED)

        cmb_type.bind("<<ComboboxSelected>>", refresh_fields)
        refresh_fields()

        def on_ok() -> None:
            cta = CtaButton(
                type=v_type.get().strip(),
                text=v_text.get().strip(),
                url=v_url.get().strip(),
                phone=v_phone.get().strip(),
                coupon_code=v_coupon.get().strip(),
            )
            if not cta.is_complete():
                messagebox.showerror("Invalid", "Provide Button Text plus URL/Phone, or Coupon Code for copy_code buttons")
                return
            self.cta_buttons.append(cta)
            self._refresh_cta_tree(select_index=len(self.cta_buttons) - 1)
            self._mark_template_dirty()
            dlg.destroy()

        ttk.Button(dlg, text="Cancel", command=dlg.destroy).grid(row=5, column=0, padx=8, pady=10)
        ttk.Button(dlg, text="Add", command=on_ok).grid(row=5, column=1, padx=8, pady=10, sticky=tk.E)

        dlg.wait_window()

    def _remove_selected_cta_button(self) -> None:
        sel = self.tree_cta.selection()
        if not sel:
            return
        idxs = sorted((self.tree_cta.index(i) for i in sel), reverse=True)
        for i in idxs:
            del self.cta_buttons[i]
        self._refresh_cta_tree()
        self._mark_template_dirty()

    def _refresh_cta_tree(self, select_index: Optional[int] = None) -> None:
        for item in self.tree_cta.get_children():
            self.tree_cta.delete(item)
        for idx, cta in enumerate(self.cta_buttons, start=1):
            self.tree_cta.insert("", tk.END, values=(idx, cta.type, cta.text, cta.url, cta.phone, cta.coupon_code))
        if select_index is not None and self.cta_buttons:
            items = self.tree_cta.get_children()
            if 0 <= select_index < len(items):
                self.tree_cta.selection_set(items[select_index])

    def _on_cta_press(self, event: tk.Event) -> None:
        item = self.tree_cta.identify_row(event.y)
        if not item:
            self._cta_drag_index = None
            return
        self.tree_cta.selection_set(item)
        self._cta_drag_index = self.tree_cta.index(item)

    def _on_cta_drag(self, event: tk.Event) -> None:
        item = self.tree_cta.identify_row(event.y)
        if item:
            self.tree_cta.selection_set(item)

    def _on_cta_release(self, event: tk.Event) -> None:
        if self._cta_drag_index is None:
            return
        target_item = self.tree_cta.identify_row(event.y)
        if target_item:
            target_index = self.tree_cta.index(target_item)
        else:
            target_index = len(self.cta_buttons)
        self._move_cta_button(self._cta_drag_index, target_index)
        self._cta_drag_index = None

    def _move_cta_button(self, src: int, dest: int) -> None:
        if src < 0 or src >= len(self.cta_buttons):
            return
        dest = max(0, min(dest, len(self.cta_buttons)))
        cta = self.cta_buttons.pop(src)
        if dest > src:
            dest -= 1
        self.cta_buttons.insert(dest, cta)
        self._refresh_cta_tree(select_index=dest)
        self._mark_template_dirty()

    def _gather_config(self) -> TemplateConfig:
        # Body params
        body_raw = self.var_body_params.get().strip()
        body = [p.strip() for p in body_raw.split("|") if p.strip()] if body_raw else []

        # Button params groups
        btn_raw = self.var_button_params.get().strip()
        groups: List[List[str]] = []
        if btn_raw:
            for part in [p.strip() for p in btn_raw.split(",") if p.strip()]:
                grp = [p.strip() for p in part.split("|") if p.strip()]
                if grp:
                    groups.append(grp)

        header_type = (self.var_header_type.get() or "none").strip()
        media_header = header_type in ("image", "video", "document")
        cfg = TemplateConfig(
            msg_type=self.var_msg_type.get().strip(),
            template=self.var_template.get().strip(),
            lang=self.var_lang.get().strip() or "en_US",
            header_type=header_type or "none",
            header_text=self.var_header_text.get().strip() if header_type == "text" else "",
            header_media_path=self.var_media_path.get().strip() if media_header else "",
            header_media_url=self.var_media_url.get().strip() if media_header else "",
            header_media_id=self.var_media_id.get().strip() if media_header else "",
            body_params=body,
            button_params_groups=groups,
            body_text=self.var_body_text.get().strip(),
            footer_text=self.var_footer_text.get().strip(),
            ctas=list(self.cta_buttons),
        )
        ok, msg = cfg.validate()
        if not ok:
            raise ValueError(msg)
        return cfg

    def _phones_list(self) -> List[str]:
        raw = self.txt_phones.get("1.0", tk.END)
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _ensure_header_media_uploaded(self, cfg: TemplateConfig) -> bool:
        """
        Automatically upload local header media files before exporting
        so the resulting CSV references a media ID instead of a path.
        """
        htype = (cfg.header_type or "none").lower()
        if htype not in {"image", "video", "document"}:
            return True
        if (cfg.header_media_id or "").strip() or (cfg.header_media_url or "").strip():
            return True
        media_path = (cfg.header_media_path or "").strip()
        if not media_path:
            return True
        media_id = self._upload_media_file(media_path)
        if not media_id:
            return False
        cfg.header_media_id = media_id
        cfg.header_media_path = ""
        # Reflect the new state in the UI for subsequent exports/previews
        if getattr(self, "var_media_source", None) is not None:
            self.var_media_source.set("id")
            self._render_media_source()
            self.var_media_id.set(media_id)
        return True

    def _save_csv(self) -> None:
        try:
            cfg = self._gather_config()
        except ValueError as e:
            messagebox.showerror("Invalid Input", str(e))
            return

        phones = self._phones_list()
        if not phones:
            messagebox.showerror("Missing phones", "Enter at least one phone number")
            return

        if not self._ensure_header_media_uploaded(cfg):
            return

        rows = build_csv_rows(phones, cfg)

        path_str = filedialog.asksaveasfilename(
            title="Save CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="recipients.csv",
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            write_csv(path, rows, headers=cfg.csv_headers())
        except Exception as e:
            messagebox.showerror("Save failed", str(e))
            return
        messagebox.showinfo("Saved", f"Wrote {len(rows)} rows to:\n{path}")

    def _start_send_batch(self) -> None:
        if self._active_batch and self._active_batch.thread.is_alive():
            messagebox.showwarning("Batch running", "A batch send is already running. Stop it or wait for it to finish.")
            return
        phones = self._phones_list()
        if not phones:
            messagebox.showerror("Missing phones", "Enter at least one phone number")
            return
        try:
            cfg = self._gather_config()
        except ValueError as e:
            messagebox.showerror("Invalid Input", str(e))
            return
        if not self._ensure_header_media_uploaded(cfg):
            return
        rows = build_csv_rows(phones, cfg)
        if not rows:
            messagebox.showerror("No rows", "No valid recipients to send.")
            return
        env_config = self._load_env_config()
        if not env_config:
            return

        alias = self._build_csv_alias(cfg)
        log_path = ensure_logs_dir() / "ui_runs" / f"{alias}.jsonl"
        dry_run = bool(self.var_send_dry_run.get())
        total_rows = len(rows)
        use_async = bool(self.var_use_async.get())
        try:
            msg_per_sec = int(self.var_msg_per_sec.get())
        except Exception:
            msg_per_sec = 80
        msg_per_sec = max(1, min(80, msg_per_sec or 80))

        progress_queue: "queue.Queue[Any]" = queue.Queue()
        stop_event = threading.Event()
        pause_event = threading.Event()
        template_label = cfg.template or "Campaign"
        media_cache = MediaCache(Path("media_cache.json"))

        def client_factory() -> WhatsAppClient:
            return WhatsAppClient(config=env_config, media_cache=media_cache, log_requests=False)

        def progress_callback(event: BatchProgressEvent) -> None:
            progress_queue.put(("progress", event))

        def worker() -> None:
            try:
                if use_async:
                    result = asyncio.run(
                        async_run_batch_from_rows(
                            rows,
                            client_factory,
                            dry_run=dry_run,
                            msg_per_sec=msg_per_sec,
                            async_workers=None,
                            delay_ms=0,
                            log_path=log_path,
                            total_rows=total_rows,
                            progress_callback=progress_callback,
                            stop_event=stop_event,
                            pause_event=pause_event,
                        )
                    )
                else:
                    client = client_factory()
                    result = run_batch_from_rows(
                        rows,
                        client,
                        dry_run=dry_run,
                        delay_ms=0,
                        log_path=log_path,
                        total_rows=total_rows,
                        progress_callback=progress_callback,
                        stop_event=stop_event,
                        pause_event=pause_event,
                    )
                progress_queue.put(("done", result))
            except Exception as exc:
                progress_queue.put(("error", str(exc)))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        handle = BatchJobHandle(
            thread=thread,
            stop_event=stop_event,
            pause_event=pause_event,
            queue=progress_queue,
            log_path=log_path,
            alias=alias,
            template=template_label,
            dry_run=dry_run,
            total_rows=total_rows,
            use_async=use_async,
            msg_per_sec=msg_per_sec,
        )
        self._active_batch = handle
        window = BatchProgressWindow(
            parent=self,
            job_handle=handle,
            queue=progress_queue,
            total_rows=total_rows,
            alias=alias,
            template=template_label,
            dry_run=dry_run,
            log_path=log_path,
            on_finished=lambda result: self._handle_batch_finished(
                result,
                alias,
                template_label,
                log_path,
                use_async,
                msg_per_sec,
            ),
        )
        handle.window = window

    def _build_csv_alias(self, cfg: TemplateConfig) -> str:
        base = (cfg.template or "campaign").strip() or "campaign"
        safe = "".join(ch if ch.isalnum() else "_" for ch in base).strip("_") or "campaign"
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{safe}_{timestamp}"

    def _handle_batch_finished(
        self,
        result: Optional[BatchSendResult],
        alias: str,
        template: str,
        log_path: Path,
        use_async: bool,
        msg_per_sec: int,
    ) -> None:
        if result:
            self._record_batch_metrics(alias, template, result, log_path, use_async, msg_per_sec)
        self._active_batch = None

    def _record_batch_metrics(
        self,
        alias: str,
        template: str,
        result: BatchSendResult,
        log_path: Path,
        use_async: bool,
        msg_per_sec: int,
    ) -> None:
        logs_dir = ensure_logs_dir()
        logs_dir.mkdir(parents=True, exist_ok=True)
        metrics_path = logs_dir / "metrics.jsonl"
        record = {
            "alias": alias,
            "template": template,
            "csv_name": f"{alias}.csv",
            "log_path": str(log_path),
            "dry_run": result.dry_run,
            "aborted": result.aborted,
             "use_async": use_async,
             "msg_per_sec": msg_per_sec,
            "total_rows": result.total_rows,
            "sent": result.sent,
            "skipped": result.skipped,
            "errors": result.errors,
            "elapsed_seconds": result.elapsed_seconds,
            "mps": result.messages_per_second,
            "finished_at": datetime.datetime.utcnow().isoformat() + "Z",
        }
        with metrics_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def _preview_payload(self) -> None:
        phones = self._phones_list()
        if not phones:
            messagebox.showerror("Missing phones", "Enter at least one phone to preview")
            return
        try:
            cfg = self._gather_config()
        except ValueError as e:
            messagebox.showerror("Invalid Input", str(e))
            return
        payload = preview_payload(phones[0], cfg)
        # Dialog with JSON
        dlg = tk.Toplevel(self)
        dlg.title("Payload Preview (first phone)")
        dlg.minsize(600, 400)
        txt = tk.Text(dlg, wrap="word")
        txt.pack(fill=tk.BOTH, expand=True)
        txt.insert("1.0", json.dumps(payload, ensure_ascii=False, indent=2))
        txt.configure(state=tk.DISABLED)
        ttk.Button(dlg, text="Close", command=dlg.destroy).pack(pady=8)

    def _reset_form(self) -> None:
        self.var_msg_type.set("template")
        self.var_template.set("")
        self.var_lang.set("en_US")
        self.var_header_type.set("none")
        self.var_header_text.set("")
        self.var_media_source.set("path")
        self.var_media_path.set("")
        self.var_media_url.set("")
        self.var_media_id.set("")
        self._render_header_dynamic()
        self.var_body_params.set("")
        self.var_button_params.set("")
        self.var_body_text.set("")
        self.var_footer_text.set("")
        self.txt_phones.delete("1.0", tk.END)
        self.cta_buttons.clear()
        self._refresh_cta_tree()

    def _open_csv(self) -> None:
        # Basic import: populate phones and template fields from a CSV built by this UI
        path = filedialog.askopenfilename(title="Open CSV", filetypes=[("CSV files", "*.csv")])
        if not path:
            return
        import csv
        rows: List[dict] = []
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append(r)
        if not rows:
            messagebox.showwarning("Empty", "CSV has no rows")
            return

        # Use the first row to populate form; add phones list
        r0 = rows[0]
        self.var_msg_type.set(r0.get("msg_type", "template") or "template")
        self.var_template.set(r0.get("template", ""))
        self.var_lang.set(r0.get("lang", "en_US"))
        header_type_value = (r0.get("header_type", "none") or "none").strip().lower() or "none"
        self.var_header_type.set(header_type_value)
        header_text = r0.get("header_text", "") or ""
        media_path = r0.get("header_media_path", "") or ""
        media_url = r0.get("header_media_url", "") or ""
        media_id = r0.get("header_media_id", "") or ""
        self.var_header_text.set(header_text if header_type_value == "text" else "")
        if header_type_value in ("image", "video", "document"):
            self.var_media_path.set(media_path)
            self.var_media_url.set(media_url)
            self.var_media_id.set(media_id)
            if media_id:
                self.var_media_source.set("id")
            elif media_url:
                self.var_media_source.set("url")
            else:
                self.var_media_source.set("path")
        else:
            self.var_media_source.set("path")
            self.var_media_path.set("")
            self.var_media_url.set("")
            self.var_media_id.set("")
        self._render_header_dynamic()

        self.var_body_params.set(r0.get("body_params", ""))
        self.var_button_params.set(r0.get("button_params", ""))
        self.var_body_text.set(r0.get("body_text", ""))
        self.var_footer_text.set(r0.get("footer_text", ""))

        # CTA buttons present in header names cta{n}_*
        self.cta_buttons.clear()
        for n in range(0, 10):
            t = (r0.get(f"cta{n}_type", "") or "").strip()
            if not t:
                continue
            cta = CtaButton(
                type=t,
                text=(r0.get(f"cta{n}_text", "") or "").strip(),
                url=(r0.get(f"cta{n}_url", "") or "").strip(),
                phone=(r0.get(f"cta{n}_phone", "") or "").strip(),
                coupon_code=(r0.get(f"cta{n}_coupon_code", "") or "").strip(),
            )
            if cta.is_complete():
                self.cta_buttons.append(cta)
        self._refresh_cta_tree()

        # Phones
        self.txt_phones.delete("1.0", tk.END)
        for r in rows:
            p = (r.get("phone", "") or "").strip()
            if p:
                self.txt_phones.insert(tk.END, p + "\n")

    # Template persistence helpers
    def _trace_var_for_autosave(self, var: tk.StringVar) -> None:
        var.trace_add("write", self._mark_template_dirty)

    def _mark_template_dirty(self, *_args: object) -> None:
        if self._autosave_job is not None:
            try:
                self.after_cancel(self._autosave_job)
            except Exception:
                pass
        self._autosave_job = self.after(1200, self._auto_save_template)

    def _auto_save_template(self) -> None:
        self._autosave_job = None
        data = self._collect_template_form_state()
        key = self._saved_template_key(data)
        if not key:
            return
        existing = self.saved_templates.get(key)
        if existing == data:
            return
        self.saved_templates[key] = data
        self._persist_saved_templates()
        self._refresh_saved_templates_menu()

    def _collect_template_form_state(self) -> Dict[str, Any]:
        return {
            "msg_type": (self.var_msg_type.get() or "").strip(),
            "template": (self.var_template.get() or "").strip(),
            "lang": (self.var_lang.get() or "").strip(),
            "header_type": (self.var_header_type.get() or "").strip(),
            "header_text": (self.var_header_text.get() or "").strip(),
            "media_source": (self.var_media_source.get() or "").strip(),
            "media_path": (self.var_media_path.get() or "").strip(),
            "media_url": (self.var_media_url.get() or "").strip(),
            "media_id": (self.var_media_id.get() or "").strip(),
            "body_params": (self.var_body_params.get() or "").strip(),
            "button_params": (self.var_button_params.get() or "").strip(),
            "body_text": (self.var_body_text.get() or "").strip(),
            "footer_text": (self.var_footer_text.get() or "").strip(),
            "ctas": [
                {
                    "type": cta.type,
                    "text": cta.text,
                    "url": cta.url,
                    "phone": cta.phone,
                    "coupon_code": cta.coupon_code,
                }
                for cta in self.cta_buttons
            ],
        }

    def _saved_template_key(self, data: Dict[str, Any]) -> str:
        tpl = (data.get("template") or "").strip()
        if not tpl:
            return ""
        lang = (data.get("lang") or "en_US").strip() or "en_US"
        mode = (data.get("msg_type") or "template").strip() or "template"
        return f"{tpl}|{lang}|{mode}"

    def _persist_saved_templates(self) -> None:
        try:
            self.saved_templates_path.parent.mkdir(parents=True, exist_ok=True)
            payload = list(self.saved_templates.values())
            self.saved_templates_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"Failed to save templates: {e}", file=sys.stderr)

    def _load_saved_templates(self) -> None:
        data: List[Dict[str, Any]] = []
        try:
            if self.saved_templates_path.exists():
                loaded = json.loads(self.saved_templates_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    data = loaded
        except Exception as e:
            messagebox.showerror("Templates", f"Failed to read saved templates:\n{e}")
        self.saved_templates.clear()
        for item in data:
            key = self._saved_template_key(item)
            if key:
                self.saved_templates[key] = item
        self._refresh_saved_templates_menu()

    def _refresh_saved_templates_menu(self) -> None:
        menu = getattr(self, "saved_templates_menu", None)
        if not menu:
            return
        menu.delete(0, tk.END)
        if not self.saved_templates:
            menu.add_command(label="No saved templates", state=tk.DISABLED)
            return
        for key, data in sorted(
            self.saved_templates.items(),
            key=lambda kv: ((kv[1].get("template") or "").lower(), (kv[1].get("lang") or "").lower()),
        ):
            label = self._format_saved_template_label(data)
            menu.add_command(label=label, command=lambda k=key: self._load_saved_template(k))

    def _format_saved_template_label(self, data: Dict[str, Any]) -> str:
        tpl = data.get("template") or "Untitled"
        lang = data.get("lang") or "en_US"
        mode = (data.get("msg_type") or "template").lower()
        return f"{tpl} ({lang}, {mode})"

    def _load_saved_template(self, key: str) -> None:
        data = self.saved_templates.get(key)
        if not data:
            messagebox.showerror("Missing Template", "Saved template could not be found.")
            return
        self._apply_saved_template_state(data)

    def _apply_saved_template_state(self, data: Dict[str, Any]) -> None:
        self.var_msg_type.set(data.get("msg_type", "template"))
        self.var_template.set(data.get("template", ""))
        self.var_lang.set(data.get("lang", "en_US"))
        header_type = (data.get("header_type", "none") or "none").strip().lower() or "none"
        self.var_header_type.set(header_type)
        source = data.get("media_source", "")
        if source not in {"path", "url", "id"}:
            source = "path"
        self.var_media_source.set(source)
        self.var_header_text.set(data.get("header_text", "") if header_type == "text" else "")
        self.var_media_path.set(data.get("media_path", ""))
        self.var_media_url.set(data.get("media_url", ""))
        self.var_media_id.set(data.get("media_id", ""))
        self._render_header_dynamic()
        self.var_body_params.set(data.get("body_params", ""))
        self.var_button_params.set(data.get("button_params", ""))
        self.var_body_text.set(data.get("body_text", ""))
        self.var_footer_text.set(data.get("footer_text", ""))
        self.cta_buttons.clear()
        for rec in data.get("ctas", []):
            self.cta_buttons.append(CtaButton(
                type=rec.get("type", ""),
                text=rec.get("text", ""),
                url=rec.get("url", ""),
                phone=rec.get("phone", ""),
                coupon_code=rec.get("coupon_code", ""),
            ))
        self._refresh_cta_tree()

    # Media helpers
    def _load_env_config(self) -> Optional[WhatsAppConfig]:
        try:
            load_dotenv(override=False)
            token = os.getenv("WHATSAPP_TOKEN", "").strip()
            phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
            api_version = os.getenv("WHATSAPP_API_VERSION", "v20.0").strip()
            if not token or not phone_id:
                messagebox.showerror("Missing credentials", "Set WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID in .env")
                return None
            return WhatsAppConfig(token=token, phone_number_id=phone_id, api_version=api_version)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load .env: {e}")
            return None

    def _get_client(self) -> Optional[WhatsAppClient]:
        cfg = self._load_env_config()
        if not cfg:
            return None
        return WhatsAppClient(config=cfg, media_cache=MediaCache(Path("media_cache.json")), log_requests=False)

    def _upload_media_file(self, file_path: str) -> Optional[str]:
        client = self._get_client()
        if not client:
            return None
        try:
            path = Path(file_path).expanduser()
        except Exception:
            messagebox.showerror("Invalid path", f"Cannot parse media path:\n{file_path}")
            return None
        if not path.exists():
            messagebox.showerror("File not found", f"Media file not found:\n{path}")
            return None
        try:
            self.config(cursor="watch")
            self.update_idletasks()
            info = client.upload_media(path)
        except Exception as e:
            messagebox.showerror("Upload failed", str(e))
            return None
        finally:
            self.config(cursor="")
        media_id = info.get("id")
        if not media_id:
            messagebox.showerror("Upload failed", "No media ID returned")
            return None
        return str(media_id)

    def _upload_from_var_media_path(self) -> None:
        path = getattr(self, "var_media_path", tk.StringVar(value="")).get().strip()
        if not path:
            messagebox.showerror("No file", "Choose a local media file first")
            return
        self._upload_media_common(path)

    def _menu_upload_media(self) -> None:
        file_path = filedialog.askopenfilename(title="Select media file to upload")
        if not file_path:
            return
        self._upload_media_common(file_path)

    def _upload_media_common(self, file_path: str) -> None:
        media_id = self._upload_media_file(file_path)
        if not media_id:
            return
        # Set source to ID and fill in
        if getattr(self, "var_media_source", None) is not None:
            self.var_media_source.set("id")
            self._render_media_source()
            self.var_media_id.set(media_id)
        # Show result and copy option
        try:
            self.clipboard_clear()
            self.clipboard_append(media_id)
        except Exception:
            pass
        messagebox.showinfo("Uploaded", f"Media uploaded. ID copied to clipboard:\n{media_id}")

    def _open_media_library(self) -> None:
        # Load cache
        cache_path = Path("media_cache.json")
        try:
            if cache_path.exists():
                import json
                data = json.loads(cache_path.read_text(encoding="utf-8"))
            else:
                data = {}
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read media cache: {e}")
            return
        items = []
        for digest, rec in (data or {}).items():
            items.append({
                "digest": digest,
                "id": rec.get("id", ""),
                "mime_type": rec.get("mime_type", ""),
                "path": rec.get("path", ""),
                "uploaded_at": rec.get("uploaded_at", 0),
            })
        dlg = tk.Toplevel(self)
        dlg.title("Media Library")
        dlg.minsize(740, 360)
        cols = ("id", "mime_type", "path", "uploaded_at")
        tree = ttk.Treeview(dlg, columns=cols, show="headings")
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=260 if c == "path" else 140, anchor=tk.W)
        tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        for it in items:
            tree.insert("", tk.END, values=(it["id"], it["mime_type"], it["path"], it["uploaded_at"]))

        bar = ttk.Frame(dlg)
        bar.pack(fill=tk.X, padx=8, pady=(0, 8))
        def copy_id():
            sel = tree.selection()
            if not sel:
                return
            vals = tree.item(sel[0], 'values')
            mid = vals[0]
            try:
                self.clipboard_clear(); self.clipboard_append(mid)
            except Exception:
                pass
            messagebox.showinfo("Copied", f"Media ID copied:\n{mid}")
        def use_in_header():
            sel = tree.selection()
            if not sel:
                return
            vals = tree.item(sel[0], 'values')
            mid = vals[0]
            # Switch source to id
            if getattr(self, "var_media_source", None) is not None:
                self.var_media_source.set("id")
                self._render_media_source()
                self.var_media_id.set(mid)
            dlg.destroy()
        ttk.Button(bar, text="Copy ID", command=copy_id).pack(side=tk.LEFT)
        ttk.Button(bar, text="Use In Header", command=use_in_header).pack(side=tk.LEFT, padx=(8,0))
        ttk.Button(bar, text="Close", command=dlg.destroy).pack(side=tk.RIGHT)

    def _on_msg_type_change(self) -> None:
        # Currently informational; fields remain visible, but CTA section notes usage.
        pass


class BatchProgressWindow(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Tk,
        *,
        job_handle: BatchJobHandle,
        queue: "queue.Queue[Any]",
        total_rows: int,
        alias: str,
        template: str,
        dry_run: bool,
        log_path: Path,
        on_finished: Callable[[Optional[BatchSendResult]], None],
    ) -> None:
        super().__init__(parent)
        self.title("Batch Send Progress")
        self.job_handle = job_handle
        self.queue = queue
        self.total_rows = total_rows
        self.alias = alias
        self.log_path = log_path
        self.on_finished = on_finished
        self._finished = False
        self._reported_finish = False
        self._paused = False
        self._close_on_finish = False

        self.protocol("WM_DELETE_WINDOW", self._on_exit)
        self._build_ui(template, dry_run)
        self.after(200, self._poll_queue)

    def _build_ui(self, template: str, dry_run: bool) -> None:
        self.minsize(520, 280)
        header = ttk.Frame(self)
        header.pack(fill=tk.X, padx=12, pady=(10, 6))
        ttk.Label(header, text=f"Template: {template}", font=("TkDefaultFont", 10, "bold")).pack(anchor=tk.W)
        ttk.Label(header, text=f"CSV alias: {self.alias}.csv").pack(anchor=tk.W)
        ttk.Label(header, text=f"Log file: {self.log_path}").pack(anchor=tk.W)
        mode_text = "Dry Run (no API calls)" if dry_run else "Live send"
        ttk.Label(header, text=f"Mode: {mode_text}").pack(anchor=tk.W, pady=(2, 0))
        ttk.Label(
            header,
            text=f"Async: {'Yes' if self.job_handle.use_async else 'No'} | msg/sec: {self.job_handle.msg_per_sec}",
        ).pack(anchor=tk.W)

        prog = ttk.Frame(self)
        prog.pack(fill=tk.X, padx=12, pady=(0, 6))
        maximum = max(self.total_rows, 1)
        self.progress = ttk.Progressbar(prog, maximum=maximum)
        self.progress.pack(fill=tk.X)
        self.var_progress = tk.StringVar(value=f"0/{self.total_rows} rows")
        ttk.Label(prog, textvariable=self.var_progress).pack(anchor=tk.E)

        stats = ttk.Frame(self)
        stats.pack(fill=tk.X, padx=12, pady=(0, 6))
        self.var_counts = tk.StringVar(value="Sent: 0 | Errors: 0 | Skipped: 0")
        self.var_speed = tk.StringVar(value="Elapsed: 0.0s | MPS: 0.0")
        self.var_status = tk.StringVar(value="Preparing batch...")
        ttk.Label(stats, textvariable=self.var_counts).pack(anchor=tk.W)
        ttk.Label(stats, textvariable=self.var_speed).pack(anchor=tk.W)
        ttk.Label(stats, textvariable=self.var_status, wraplength=500).pack(anchor=tk.W, pady=(4, 0))

        buttons = ttk.Frame(self)
        buttons.pack(fill=tk.X, padx=12, pady=(8, 12))
        self.btn_stop = ttk.Button(buttons, text="Stop", command=self._on_stop)
        self.btn_stop.pack(side=tk.LEFT)
        self.btn_continue = ttk.Button(buttons, text="Continue", command=self._on_continue, state=tk.DISABLED)
        self.btn_continue.pack(side=tk.LEFT, padx=(8, 0))
        self.btn_exit = ttk.Button(buttons, text="Exit", command=self._on_exit)
        self.btn_exit.pack(side=tk.RIGHT)

    def _poll_queue(self) -> None:
        if self._finished:
            return
        while True:
            try:
                kind, payload = self.queue.get_nowait()
            except queue.Empty:
                break
            if kind == "progress" and isinstance(payload, BatchProgressEvent):
                self._update_progress(payload)
            elif kind == "done" and isinstance(payload, BatchSendResult):
                self._handle_done(payload)
            elif kind == "error":
                self._handle_error(str(payload))
        if not self._finished:
            self.after(200, self._poll_queue)

    def _update_progress(self, event: BatchProgressEvent) -> None:
        total = self.total_rows or event.total or 0
        if total <= 0:
            total = max(event.processed, 1)
        self.progress.configure(maximum=max(total, 1))
        self.progress["value"] = min(event.processed, self.progress["maximum"])
        self.var_progress.set(f"{event.processed}/{total} rows")
        self.var_counts.set(f"Sent: {event.sent} | Errors: {event.errors} | Skipped: {event.skipped}")
        self.var_speed.set(f"Elapsed: {event.elapsed_seconds:.1f}s | MPS: {event.messages_per_second:.2f}")
        phone = event.phone or "n/a"
        status = event.status.upper()
        message = f"{status}: {phone}"
        if event.message:
            message += f" ({event.message})"
        self.var_status.set(message)

    def _handle_done(self, result: BatchSendResult) -> None:
        self._finished = True
        self.job_handle.pause_event.clear()
        self.btn_stop.configure(state=tk.DISABLED)
        self.btn_continue.configure(state=tk.DISABLED)
        self.btn_exit.configure(text="Close", state=tk.NORMAL)
        summary = (
            f"Batch complete. Sent={result.sent}, Errors={result.errors}, Skipped={result.skipped}, "
            f"Elapsed={result.elapsed_seconds:.1f}s, MPS={result.messages_per_second:.2f}"
        )
        if result.aborted:
            summary += " (aborted)"
        self.var_status.set(summary)
        self.var_counts.set(f"Sent: {result.sent} | Errors: {result.errors} | Skipped: {result.skipped}")
        self.var_speed.set(f"Elapsed: {result.elapsed_seconds:.1f}s | MPS: {result.messages_per_second:.2f}")
        if not self._reported_finish:
            self._reported_finish = True
            self.on_finished(result)
        if self._close_on_finish:
            self.destroy()

    def _handle_error(self, message: str) -> None:
        self._finished = True
        self.btn_stop.configure(state=tk.DISABLED)
        self.btn_continue.configure(state=tk.DISABLED)
        self.btn_exit.configure(text="Close", state=tk.NORMAL)
        self.var_status.set(f"Batch error: {message}")
        if not self._reported_finish:
            self._reported_finish = True
            self.on_finished(None)
        messagebox.showerror("Batch failed", message, parent=self)
        if self._close_on_finish:
            self.destroy()

    def _on_stop(self) -> None:
        if self._finished or self._paused:
            return
        if not self._confirm_action("Pause Batch", "Stop will pause the batch. Continue?"):
            return
        self.job_handle.pause_event.set()
        self._paused = True
        self.btn_stop.configure(state=tk.DISABLED)
        self.btn_continue.configure(state=tk.NORMAL)
        self.var_status.set("Paused. Press Continue to resume.")

    def _on_continue(self) -> None:
        if self._finished or not self._paused:
            return
        self.job_handle.pause_event.clear()
        self._paused = False
        self.btn_stop.configure(state=tk.NORMAL)
        self.btn_continue.configure(state=tk.DISABLED)
        self.var_status.set("Resuming batch...")

    def _on_exit(self) -> None:
        if self._finished:
            self.destroy()
            return
        if not self._confirm_action("Stop Batch", "Exit will stop the current batch. Do you want to proceed?"):
            return
        self.job_handle.pause_event.clear()
        self.job_handle.stop_event.set()
        self._close_on_finish = True
        self.var_status.set("Stopping batch...")
        self.btn_stop.configure(state=tk.DISABLED)
        self.btn_continue.configure(state=tk.DISABLED)
        self.btn_exit.configure(state=tk.DISABLED)

    def _confirm_action(self, title: str, message: str) -> bool:
        dlg = tk.Toplevel(self)
        dlg.title(title)
        dlg.transient(self)
        dlg.grab_set()
        ttk.Label(dlg, text=message, wraplength=360).pack(padx=16, pady=12)
        result = {"value": False}

        def choose(val: bool) -> None:
            result["value"] = val
            dlg.destroy()

        buttons = ttk.Frame(dlg)
        buttons.pack(fill=tk.X, pady=(0, 12))
        btn_yes = ttk.Button(buttons, text="Yes", command=lambda: choose(True))
        btn_yes.pack(side=tk.RIGHT, padx=(8, 12))
        btn_no = ttk.Button(buttons, text="No", command=lambda: choose(False))
        btn_no.pack(side=tk.RIGHT)
        btn_no.focus_set()
        dlg.bind("<Return>", lambda *_: choose(False))
        dlg.protocol("WM_DELETE_WINDOW", lambda: choose(False))
        dlg.resizable(False, False)
        dlg.wait_window()
        return result["value"]


def main() -> None:  # pragma: no cover
    app = App()
    app.mainloop()


if __name__ == "__main__":  # pragma: no cover
    main()
