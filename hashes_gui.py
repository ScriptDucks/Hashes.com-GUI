from __future__ import annotations

import base64
import csv
import json
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from inc.algorithms import validalgs
from inc.hashes_client import HashesApiError, HashesClient


def _default_font():
    if sys.platform == "win32":
        return ("Segoe UI", 10)
    if sys.platform == "darwin":
        return ("SF Pro Display", 10)
    return ("DejaVu Sans", 10)


def _default_font_semibold():
    if sys.platform == "win32":
        return "Segoe UI Semibold"
    if sys.platform == "darwin":
        return "Helvetica Neue Bold"
    return "DejaVu Sans"


class _ToolTip:

    def __init__(self, widget, text, delay_ms=500):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._tw: tk.Toplevel | None = None
        self._after_id: str | None = None
        widget.bind("<Enter>", self._on_enter)
        widget.bind("<Leave>", self._on_leave)

    def _on_enter(self, _e):
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _on_leave(self, _e):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        self._hide()

    def _show(self):
        self._after_id = None
        if self._tw:
            return
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._tw = tk.Toplevel(self.widget)
        self._tw.wm_overrideredirect(True)
        self._tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            self._tw,
            text=self.text,
            justify="left",
            background="#252f40",
            foreground="#e5edf9",
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=6,
            font=(_default_font()[0], 9),
        )
        label.pack()
        self._tw.update_idletasks()
        w, h = label.winfo_reqwidth(), label.winfo_reqheight()
        self._tw.wm_geometry(f"{w+4}x{h+4}+{x}+{y}")

    def _hide(self):
        if self._tw:
            self._tw.destroy()
            self._tw = None


class HashesGuiApp(tk.Tk):
    CONFIG_PATH = Path(__file__).with_name("gui_settings.json")

    BG = "#0f131b"
    SURFACE = "#171d27"
    SURFACE_ALT = "#1e2735"
    SURFACE_ELEVATED = "#252f40"
    BORDER = "#35425a"
    TEXT = "#e5edf9"
    MUTED = "#9faec6"
    ACCENT = "#5c87ff"
    ACCENT_HOVER = "#476fe0"

    def __init__(self) -> None:
        super().__init__()
        self.title("Hashes.com Gui | Script Ducks")
        self.geometry("1320x860")
        self.minsize(1080, 720)
        # try to start maximized
        try:
            self.state("zoomed")
        except tk.TclError:
            try:
                self.attributes("-zoomed", True)
            except tk.TclError:
                pass
        self.configure(bg=self.BG)

        self.config_data = self._load_config()
        self.client = HashesClient(api_key=self.config_data.get("api_key", ""))
        self.jobs_cache: list[dict[str, Any]] = []
        self.filtered_jobs: list[dict[str, Any]] = []
        self.job_index: dict[str, dict[str, Any]] = {}
        self.lookup_results: list[dict[str, str]] = []
        self.algorithms: dict[str, str] = dict(validalgs)
        self.jobs_loading = False
        self.jobs_sort_column = self.config_data.get("jobs_table_sort_column", "created")
        self.jobs_sort_desc = bool(self.config_data.get("jobs_table_sort_desc", True))
        self._layout_save_after_id: str | None = None
        self._last_layout_snapshot: dict[str, Any] = {}

        self._configure_style()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._bootstrap)

    def _configure_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass  # clam not available on some systems
        fam, size = _default_font()
        self.option_add("*Font", f"{{{fam}}} {size}")
        style.configure(".", background=self.BG, foreground=self.TEXT)
        style.configure("TFrame", background=self.BG)
        style.configure("Card.TFrame", background=self.SURFACE, bordercolor=self.BORDER, relief="flat")
        style.configure("TLabelframe", background=self.BG, foreground=self.TEXT)
        style.configure("TLabelframe.Label", background=self.BG, foreground=self.MUTED)
        style.configure(
            "Panel.TLabelframe",
            background=self.SURFACE,
            foreground=self.MUTED,
            bordercolor=self.BORDER,
            relief="flat",
        )
        style.configure("Panel.TLabelframe.Label", background=self.SURFACE, foreground=self.MUTED)
        style.configure("TLabel", background=self.BG, foreground=self.TEXT)
        style.configure("Muted.TLabel", background=self.BG, foreground=self.MUTED)
        style.configure("Title.TLabel", background=self.SURFACE, foreground=self.TEXT, font=(_default_font_semibold(), 13))
        style.configure(
            "TButton",
            background=self.SURFACE_ELEVATED,
            foreground=self.TEXT,
            bordercolor=self.BORDER,
            relief="flat",
            focusthickness=0,
            padding=(10, 6),
        )
        style.map(
            "TButton",
            background=[("active", self.BORDER), ("pressed", self.SURFACE_ALT)],
            foreground=[("disabled", "#6f7d95")],
        )
        style.configure("Accent.TButton", background=self.ACCENT, foreground="#ffffff")
        style.map(
            "Accent.TButton",
            background=[("active", self.ACCENT_HOVER), ("pressed", self.ACCENT_HOVER)],
        )
        style.configure(
            "TEntry",
            fieldbackground=self.SURFACE,
            foreground=self.TEXT,
            bordercolor=self.BORDER,
            insertcolor=self.TEXT,
            padding=5,
        )
        style.configure(
            "TCombobox",
            fieldbackground=self.SURFACE,
            background=self.SURFACE,
            foreground=self.TEXT,
            bordercolor=self.BORDER,
            arrowsize=14,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", self.SURFACE)],
            selectbackground=[("readonly", self.SURFACE)],
            selectforeground=[("readonly", self.TEXT)],
        )
        style.configure(
            "TNotebook",
            background=self.BG,
            borderwidth=0,
            tabmargins=(2, 4, 2, 0),
        )
        style.configure(
            "TNotebook.Tab",
            background=self.SURFACE_ALT,
            foreground=self.MUTED,
            padding=(20, 10),
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", self.SURFACE_ELEVATED), ("active", self.SURFACE_ELEVATED)],
            foreground=[("selected", self.TEXT), ("active", self.TEXT)],
        )
        style.configure(
            "Treeview",
            background=self.SURFACE,
            fieldbackground=self.SURFACE,
            foreground=self.TEXT,
            bordercolor=self.BORDER,
            rowheight=27,
        )
        style.map(
            "Treeview",
            background=[("selected", self.ACCENT)],
            foreground=[("selected", "#ffffff")],
        )
        style.configure(
            "Treeview.Heading",
            background=self.SURFACE,
            foreground=self.TEXT,
            relief="flat",
            bordercolor=self.BORDER,
            font=(_default_font_semibold(), 10),
        )
        style.configure("TCheckbutton", background=self.BG, foreground=self.TEXT)
        style.map("TCheckbutton", foreground=[("disabled", "#7f8ca4")])
        style.configure(
            "Vertical.TScrollbar",
            background=self.SURFACE_ELEVATED,
            troughcolor=self.SURFACE,
            bordercolor=self.BORDER,
            arrowcolor=self.MUTED,
        )
        style.configure(
            "Horizontal.TScrollbar",
            background=self.SURFACE_ELEVATED,
            troughcolor=self.SURFACE,
            bordercolor=self.BORDER,
            arrowcolor=self.MUTED,
        )

    def _build_ui(self) -> None:
        self.api_key_var = tk.StringVar(value=self.config_data.get("api_key", ""))
        self.status_var = tk.StringVar(value="Ready.")

        top = ttk.Frame(self, style="Card.TFrame", padding=(14, 12))
        top.pack(fill="x", padx=16, pady=(14, 8))

        ttk.Label(top, text="Author: Script Ducks   |   Hashes.com Gui", style="Title.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 10)
        )

        ttk.Label(top, text="API Key").grid(row=1, column=0, padx=(0, 8), sticky="w")
        self.api_entry = ttk.Entry(
            top,
            textvariable=self.api_key_var,
            show="*",
            width=58,
        )
        self.api_entry.grid(row=1, column=1, sticky="ew")

        ttk.Button(top, text="Save Key", command=self._save_api_key).grid(
            row=1, column=2, padx=(0, 8), sticky="ew"
        )
        top.columnconfigure(1, weight=1)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=16, pady=(2, 6))

        self.jobs_tab = ttk.Frame(self.notebook)
        self.hash_tools_tab = ttk.Frame(self.notebook)
        self.account_tab = ttk.Frame(self.notebook)

        self.notebook.add(self.jobs_tab, text="Jobs Explorer")
        self.notebook.add(self.hash_tools_tab, text="Hash Tools")
        self.notebook.add(self.account_tab, text="Account")

        self._build_jobs_tab()
        self._build_hash_tools_tab()
        self._build_account_tab()

        status_frame = ttk.Frame(self, style="Card.TFrame", padding=(10, 8))
        status_frame.pack(fill="x", padx=16, pady=(2, 12))
        ttk.Label(status_frame, textvariable=self.status_var, style="Muted.TLabel").pack(
            side="left"
        )

    def _build_jobs_tab(self) -> None:
        self.jobs_currency_var = tk.StringVar(value=self.config_data.get("jobs_currency", "All"))
        self.jobs_alg_var = tk.StringVar(value=self.config_data.get("jobs_algorithm", "All"))
        self.jobs_min_left_var = tk.StringVar(value=self.config_data.get("jobs_min_left", "0"))
        self.jobs_stats_var = tk.StringVar(value="No jobs loaded.")

        stats_frame = ttk.Frame(self.jobs_tab, style="Card.TFrame", padding=(10, 8))
        stats_frame.pack(side="bottom", fill="x", padx=8, pady=(0, 8))
        ttk.Label(stats_frame, textvariable=self.jobs_stats_var, style="Muted.TLabel").pack(
            anchor="w"
        )

        controls = ttk.Frame(self.jobs_tab, style="Card.TFrame", padding=(10, 10))
        controls.pack(fill="x", pady=(8, 8), padx=8)

        ttk.Label(controls, text="Currency").grid(row=0, column=0, sticky="w")
        self.jobs_currency_combo = ttk.Combobox(
            controls,
            textvariable=self.jobs_currency_var,
            values=["All", "BTC", "XMR", "LTC"],
            width=12,
            state="readonly",
        )
        self.jobs_currency_combo.grid(
            row=0, column=1, padx=(6, 12), sticky="w"
        )
        self.jobs_currency_combo.bind("<<ComboboxSelected>>", lambda _event: self._apply_job_filters())

        ttk.Label(controls, text="Algorithm").grid(row=0, column=2, sticky="w")
        self.jobs_alg_combo = ttk.Combobox(
            controls,
            textvariable=self.jobs_alg_var,
            values=self._job_algorithm_options(),
            width=44,
        )
        self.jobs_alg_combo.grid(
            row=0, column=3, padx=(6, 12), sticky="w"
        )
        self.jobs_alg_combo.bind("<<ComboboxSelected>>", lambda _event: self._apply_job_filters())
        self.jobs_alg_combo.bind("<KeyRelease>", self._on_jobs_alg_key)

        ttk.Label(controls, text="Min Left").grid(row=0, column=4, sticky="w")
        min_left_entry = ttk.Entry(controls, textvariable=self.jobs_min_left_var, width=8)
        min_left_entry.grid(row=0, column=5, padx=(6, 12), sticky="w")
        min_left_entry.bind("<KeyRelease>", lambda _event: self._apply_job_filters())

        ttk.Button(controls, text="Refresh", style="Accent.TButton", command=self.refresh_jobs).grid(
            row=0, column=6, sticky="ew"
        )

        row2 = ttk.Frame(self.jobs_tab, style="Card.TFrame", padding=(10, 10))
        row2.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(row2, text="Copy Selected IDs", command=self._copy_selected_job_ids).pack(
            side="left", padx=(0, 10)
        )
        ttk.Button(row2, text="Export CSV", command=self._export_jobs_csv).pack(
            side="left", padx=(0, 10)
        )
        ttk.Button(
            row2, text="Download Selected Left Lists", command=self._download_selected_jobs
        ).pack(side="left")

        self.jobs_content_pane = ttk.Panedwindow(self.jobs_tab, orient="horizontal")
        self.jobs_content_pane.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        table_card = ttk.Frame(self.jobs_content_pane, style="Card.TFrame", padding=(8, 8))
        details_card = ttk.Frame(self.jobs_content_pane, style="Card.TFrame", padding=(10, 10))
        details_card.configure(width=390)
        self.jobs_content_pane.add(table_card, weight=4)
        self.jobs_content_pane.add(details_card, weight=2)
        self.jobs_content_pane.bind("<ButtonRelease-1>", self._on_layout_interaction, add="+")

        table_card.columnconfigure(0, weight=1)
        table_card.rowconfigure(0, weight=1)

        tree_container = ttk.Frame(table_card)
        tree_container.grid(row=0, column=0, sticky="nsew")
        tree_container.columnconfigure(0, weight=1)
        tree_container.rowconfigure(0, weight=1)

        self.jobs_columns = ("id", "created", "algorithm", "total", "found", "left", "currency", "price", "hints")
        self.jobs_tree = ttk.Treeview(tree_container, columns=self.jobs_columns, show="headings")
        jobs_col_config = {
            "id": ("ID", 90, 30),
            "created": ("Created", 130, 50),
            "algorithm": ("Algorithm", 280, 50),
            "total": ("Total", 95, 30),
            "found": ("Found", 95, 30),
            "left": ("Left", 95, 30),
            "currency": ("Currency", 90, 40),
            "price": ("Price / USD", 190, 60),
            "hints": ("Hints", 120, 40),
        }
        self.jobs_column_titles = {c: jobs_col_config[c][0] for c in self.jobs_columns}
        saved_widths = self._load_column_widths("jobs_table_columns", {c: jobs_col_config[c][1] for c in self.jobs_columns})
        for col in self.jobs_columns:
            title, def_w, min_w = jobs_col_config[col]
            self.jobs_tree.heading(col, text=title, anchor="w", command=lambda c=col: self._on_jobs_heading_click(c))
            stretch = col == "hints"
            self.jobs_tree.column(col, width=int(saved_widths.get(col, def_w)), minwidth=min_w, anchor="w", stretch=stretch)
        self._refresh_jobs_heading_labels()

        y_scroll = ttk.Scrollbar(tree_container, orient="vertical", command=self.jobs_tree.yview)
        x_scroll = ttk.Scrollbar(
            table_card, orient="horizontal", command=self.jobs_tree.xview
        )
        self.jobs_tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        self.jobs_tree.pack(side="left", fill="both", expand=True)
        y_scroll.pack(side="right", fill="y")
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.jobs_tree.bind("<<TreeviewSelect>>", self._on_job_selected)
        self.jobs_tree.bind("<ButtonRelease-1>", self._on_layout_interaction, add="+")
        self.jobs_tree.tag_configure("odd", background="#202b3a")
        self.jobs_tree.tag_configure("even", background="#1a2331")

        details_card.columnconfigure(0, weight=1)
        details_card.rowconfigure(1, weight=1)
        header_row = ttk.Frame(details_card)
        header_row.grid(row=0, column=0, sticky="ew")
        ttk.Label(header_row, text="Selected Job Details", style="Muted.TLabel").pack(
            side="left"
        )
        ttk.Button(
            header_row, text="Quack", command=self._on_quack_clicked
        ).pack(side="right", padx=(8, 0))
        self._duck_photo: tk.PhotoImage | None = None
        self._duck_placeholder = tk.Frame(
            details_card, bg=self.SURFACE, highlightthickness=1, highlightbackground=self.BORDER
        )
        self._duck_placeholder.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self._duck_label = tk.Label(
            self._duck_placeholder,
            text="Loading duck…",
            bg=self.SURFACE,
            fg=self.MUTED,
            font=(_default_font()[0], 10),
        )
        self._duck_label.pack(expand=True, fill="both", padx=8, pady=8)
        self.job_details = scrolledtext.ScrolledText(
            details_card,
            wrap="word",
            bg=self.SURFACE,
            fg=self.TEXT,
            insertbackground=self.TEXT,
            relief="flat",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=self.BORDER,
            padx=8,
            pady=8,
        )
        self.job_details.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.job_details.insert("1.0", "Select a job row to view details.")
        self.job_details.configure(state="disabled")
        self.job_details.grid_remove()
        self._load_random_duck()

        self.after(200, self._restore_jobs_pane_sash)

    def _build_hash_tools_tab(self) -> None:
        wrapper = ttk.Frame(self.hash_tools_tab, style="Card.TFrame", padding=(8, 8))
        wrapper.pack(fill="both", expand=True, padx=8, pady=(8, 8))
        wrapper.columnconfigure(0, weight=1)
        wrapper.columnconfigure(1, weight=1)
        wrapper.rowconfigure(0, weight=0)
        wrapper.rowconfigure(1, weight=1)

        identify_card = ttk.LabelFrame(wrapper, text="Identifier", style="Panel.TLabelframe")
        identify_card.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=(0, 0), pady=(0, 10))
        identify_card.columnconfigure(1, weight=1)

        self.identify_var = tk.StringVar()
        self.identify_extended_var = tk.BooleanVar(value=False)
        ttk.Label(identify_card, text="Hash").grid(row=0, column=0, padx=(8, 8), pady=8, sticky="w")
        ttk.Entry(identify_card, textvariable=self.identify_var).grid(
            row=0, column=1, padx=(0, 8), pady=8, sticky="ew"
        )
        ext_cb = ttk.Checkbutton(
            identify_card, text="Extended", variable=self.identify_extended_var
        )
        ext_cb.grid(row=0, column=2, padx=(0, 8), pady=8)
        _ToolTip(ext_cb, "Return all possible matching algorithms (expert mode)\ninstead of only the most likely match.")
        ttk.Button(
            identify_card, text="Identify", style="Accent.TButton", command=self._identify_hash
        ).grid(row=0, column=3, padx=(0, 8), pady=8)

        self.identify_results = scrolledtext.ScrolledText(
            identify_card,
            height=8,
            wrap="word",
            bg=self.SURFACE,
            fg=self.TEXT,
            insertbackground=self.TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=self.BORDER,
            padx=8,
            pady=8,
        )
        self.identify_results.grid(row=1, column=0, columnspan=4, sticky="nsew", padx=8, pady=(0, 8))

        input_card = ttk.LabelFrame(wrapper, text="Lookup Input", style="Panel.TLabelframe")
        input_card.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        input_card.rowconfigure(1, weight=1)
        input_card.columnconfigure(0, weight=1)

        controls = ttk.Frame(input_card)
        controls.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))
        ttk.Button(controls, text="Load File", command=self._load_lookup_file).pack(side="left")
        ttk.Button(
            controls, text="Run Lookup", style="Accent.TButton", command=self._run_lookup
        ).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Clear", command=self._clear_lookup_input).pack(
            side="left", padx=(8, 0)
        )
        self.lookup_include_algorithm_var = tk.BooleanVar(value=True)
        inc_alg_cb = ttk.Checkbutton(
            controls,
            text="Include algorithm",
            variable=self.lookup_include_algorithm_var,
        )
        inc_alg_cb.pack(side="right")
        _ToolTip(inc_alg_cb, "When saving lookup results, append the algorithm\nname to each line (hash:salt:plaintext:algorithm).")

        self.lookup_input = scrolledtext.ScrolledText(
            input_card,
            wrap="word",
            bg=self.SURFACE,
            fg=self.TEXT,
            insertbackground=self.TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=self.BORDER,
            padx=8,
            pady=8,
        )
        self.lookup_input.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        output_card = ttk.LabelFrame(wrapper, text="Lookup Results", style="Panel.TLabelframe")
        output_card.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        output_card.columnconfigure(0, weight=1)
        output_card.rowconfigure(1, weight=1)

        self.lookup_summary_var = tk.StringVar(value="No lookup performed yet.")
        bar = ttk.Frame(output_card)
        bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))
        ttk.Label(bar, textvariable=self.lookup_summary_var, style="Muted.TLabel").pack(side="left")
        ttk.Button(bar, text="Save Results", command=self._save_lookup_results).pack(side="right")

        lookup_columns = ("hash", "salt", "plaintext", "algorithm")
        self.lookup_tree = ttk.Treeview(output_card, columns=lookup_columns, show="headings")
        self.lookup_columns = lookup_columns
        lookup_cols = {"hash": (260, 50), "salt": (180, 40), "plaintext": (220, 50), "algorithm": (220, 50)}
        saved = self._load_column_widths("lookup_table_columns", {k: v[0] for k, v in lookup_cols.items()})
        lookup_col_list = list(lookup_cols.keys())
        for col, (def_w, min_w) in lookup_cols.items():
            self.lookup_tree.heading(col, text=col.capitalize() if col != "plaintext" else "Plaintext", anchor="w")
            stretch = col == lookup_col_list[-1]
            self.lookup_tree.column(col, width=int(saved.get(col, def_w)), minwidth=min_w, anchor="w", stretch=stretch)

        lookup_yscroll = ttk.Scrollbar(output_card, orient="vertical", command=self.lookup_tree.yview)
        lookup_xscroll = ttk.Scrollbar(output_card, orient="horizontal", command=self.lookup_tree.xview)
        self.lookup_tree.configure(yscrollcommand=lookup_yscroll.set, xscrollcommand=lookup_xscroll.set)
        self.lookup_tree.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=(0, 8))
        lookup_yscroll.grid(row=1, column=1, sticky="ns", padx=(0, 8), pady=(0, 8))
        lookup_xscroll.grid(row=2, column=0, sticky="ew", padx=(8, 0), pady=(0, 8))
        output_card.rowconfigure(1, weight=1)
        output_card.columnconfigure(0, weight=1)
        self.lookup_tree.bind("<ButtonRelease-1>", self._on_layout_interaction, add="+")
        self.lookup_tree.tag_configure("odd", background="#202b3a")
        self.lookup_tree.tag_configure("even", background="#1a2331")

    def _build_account_tab(self):
        frame = ttk.Frame(self.account_tab, style="Card.TFrame", padding=(8, 8))
        frame.pack(fill="both", expand=True, padx=8, pady=8)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        top = ttk.Frame(frame)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.balance_summary_var = tk.StringVar(value="Click refresh to load balances.")
        ttk.Button(top, text="Refresh Balance", style="Accent.TButton", command=self.refresh_balance).pack(
            side="left"
        )
        ttk.Label(top, textvariable=self.balance_summary_var, style="Muted.TLabel").pack(
            side="left", padx=(12, 0)
        )

        self.balance_columns = ("currency", "amount", "usd")
        self.balance_tree = ttk.Treeview(frame, columns=self.balance_columns, show="headings")
        bal_cols = {"currency": (160, 50), "amount": (220, 60), "usd": (220, 60)}
        saved = self._load_column_widths("balance_table_columns", {k: v[0] for k, v in bal_cols.items()})
        bal_col_list = list(bal_cols.keys())
        for col, (def_w, min_w) in bal_cols.items():
            self.balance_tree.heading(col, text=col.upper() if col == "usd" else col.capitalize(), anchor="w")
            stretch = col == bal_col_list[-1]
            self.balance_tree.column(col, width=int(saved.get(col, def_w)), minwidth=min_w, anchor="w", stretch=stretch)
        bal_y_scroll = ttk.Scrollbar(frame, orient="vertical", command=self.balance_tree.yview)
        bal_x_scroll = ttk.Scrollbar(frame, orient="horizontal", command=self.balance_tree.xview)
        self.balance_tree.configure(yscrollcommand=bal_y_scroll.set, xscrollcommand=bal_x_scroll.set)
        self.balance_tree.grid(row=1, column=0, sticky="nsew")
        bal_y_scroll.grid(row=1, column=1, sticky="ns")
        bal_x_scroll.grid(row=2, column=0, sticky="ew")
        self.balance_tree.bind("<ButtonRelease-1>", self._on_layout_interaction, add="+")
        self.balance_tree.tag_configure("odd", background="#202b3a")
        self.balance_tree.tag_configure("even", background="#1a2331")

    def _bootstrap(self):
        self._set_status("Ready. Enter API key to begin.")
        if self.client.api_key:
            self.refresh_jobs(show_feedback=False)
            self.refresh_balance(show_feedback=False)
        self._update_algorithms_file()

    def _update_algorithms_file(self):
        alg_path = Path(__file__).parent / "inc" / "algorithms.py"

        def worker() -> tuple[bool, dict[str, str]]:
            try:
                return self.client.fetch_and_update_algorithms_file(alg_path)
            except Exception:
                return False, {}

        def on_success(result: tuple[bool, dict[str, str]]) -> None:
            ok, fetched = result
            if ok and fetched:
                self.algorithms = fetched
                self._refresh_job_filter_options(self.jobs_cache)
                self._apply_job_filters()

        self._run_background(worker, on_success, task_name="Update algorithms")

    def _save_api_key(self) -> None:
        self.client.set_api_key(self.api_key_var.get())
        self._save_config()
        if self.client.api_key:
            self._set_status("API key saved.")
        else:
            self._set_status("API key cleared.")

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)

    def _require_api_key(self) -> bool:
        if self.client.api_key:
            return True
        key = self.api_key_var.get().strip()
        if key:
            self.client.set_api_key(key)
            return True
        messagebox.showwarning("API key required", "Please enter and save your API key first.")
        return False

    def _run_background(
        self,
        worker: Any,
        on_success: Any,
        *,
        task_name: str,
        on_finally: Any | None = None,
    ) -> None:
        def runner() -> None:
            try:
                result = worker()
            except Exception as exc:
                self.after(0, lambda e=exc: self._handle_task_error(task_name, e))
            else:
                self.after(0, lambda r=result: on_success(r))
            finally:
                if on_finally:
                    self.after(0, on_finally)

        threading.Thread(target=runner, daemon=True).start()

    def _handle_task_error(self, task_name: str, exc: Exception) -> None:
        message = str(exc)
        if isinstance(exc, HashesApiError):
            display = message
        else:
            display = f"Unexpected error: {message}"
        self._set_status(f"{task_name} failed.")
        messagebox.showerror(task_name, display)

    def _on_quack_clicked(self) -> None:
        self.jobs_tree.selection_set(())
        self._show_duck_placeholder()
        self._load_random_duck()

    def _load_random_duck(self) -> None:
        def worker() -> tuple[str, bytes] | None:
            try:
                r = requests.get(
                    "https://random-d.uk/api/random",
                    params={"type": "gif"},
                    timeout=10,
                )
                r.raise_for_status()
                data = r.json()
                url = data.get("url")
                if not url:
                    return None
                img_r = requests.get(url, timeout=10)
                img_r.raise_for_status()
                return url, img_r.content
            except Exception:
                return None

        def on_success(result: tuple[str, bytes] | None) -> None:
            if not result:
                self._duck_label.config(text="Select a job row to view details.")
                return
            _url, img_bytes = result
            try:
                b64 = base64.b64encode(img_bytes).decode("ascii")
                photo = tk.PhotoImage(data=b64)
            except (tk.TclError, ValueError):
                self._duck_label.config(text="Select a job row to view details.")
                return
            self._duck_photo = photo
            self._duck_label.config(image=photo, text="")
            self._duck_label.image = photo

        self._run_background(worker, on_success, task_name="Load duck")

    def _show_duck_placeholder(self) -> None:
        self.job_details.grid_remove()
        self._duck_placeholder.grid(row=1, column=0, sticky="nsew", pady=(6, 0))

    def _show_job_details(self) -> None:
        self._duck_placeholder.grid_remove()
        self.job_details.grid(row=1, column=0, sticky="nsew", pady=(6, 0))

    def refresh_jobs(self, show_feedback: bool = True) -> None:
        if self.jobs_loading:
            return
        if not self._require_api_key():
            return
        self.client.set_api_key(self.api_key_var.get())

        self.jobs_loading = True
        if show_feedback:
            self._set_status("Loading jobs...")

        def worker() -> list[dict[str, Any]]:
            return self.client.get_jobs(sortby="createdAt", reverse=True)

        def on_success(result: list[dict[str, Any]]) -> None:
            self.jobs_cache = result
            self.job_index = {str(job.get("id")): job for job in result}
            self._refresh_job_filter_options(result)
            self._apply_job_filters()
            self._set_status(f"Loaded {len(result)} jobs.")

        self._run_background(
            worker,
            on_success,
            task_name="Refresh jobs",
            on_finally=lambda: setattr(self, "jobs_loading", False),
        )

    def _on_jobs_alg_key(self, event):
        query = self.jobs_alg_var.get().strip()
        if not query:
            self.jobs_alg_combo.configure(values=self._job_algorithm_options())
            self._apply_job_filters()
            return
        opts = self._job_algorithm_options()
        if query.lower() == "all":
            self.jobs_alg_combo.configure(values=opts)
            self._apply_job_filters()
            return
        first = query[0]
        if first.isdigit():
            filtered = [o for o in opts if o != "All" and o.split(" - ", 1)[0].strip().startswith(query)]
        else:
            q = query.lower()
            filtered = [o for o in opts if o != "All" and q in o.split(" - ", 1)[-1].lower()]
        self.jobs_alg_combo.configure(values=(["All"] + filtered) if filtered else opts)
        self._apply_job_filters()

    def _apply_job_filters(self) -> None:
        selected_currency = self.jobs_currency_var.get().strip().upper()
        alg_filter_mode, alg_filter_value = self._get_algorithm_filter()
        min_left = self._safe_int(self.jobs_min_left_var.get(), 0)

        filtered: list[dict[str, Any]] = []
        for job in self.jobs_cache:
            currency = str(job.get("currency", "")).upper()
            if selected_currency and selected_currency != "ALL" and currency != selected_currency:
                continue

            algorithm_id = str(job.get("algorithmId", ""))
            algorithm_name = str(job.get("algorithmName", "")).lower()
            if alg_filter_mode == "id" and alg_filter_value:
                if not algorithm_id.startswith(alg_filter_value):
                    continue
            elif alg_filter_mode == "name" and alg_filter_value:
                if alg_filter_value not in algorithm_name:
                    continue
            elif alg_filter_mode == "exact" and alg_filter_value:
                if algorithm_id != alg_filter_value:
                    continue

            left = self._safe_int(job.get("leftHashes"), 0)
            if left < min_left:
                continue
            filtered.append(job)

        self.filtered_jobs = filtered
        self._render_jobs(filtered)
        self._refresh_jobs_stats_display()

    def _render_jobs(self, jobs: list[dict[str, Any]]) -> None:
        ordered_jobs = self._sorted_jobs(jobs)
        self.jobs_tree.delete(*self.jobs_tree.get_children())
        for idx, job in enumerate(ordered_jobs):
            hints = str(job.get("hints", "")).strip()
            row_tag = "even" if idx % 2 == 0 else "odd"
            self.jobs_tree.insert(
                "",
                "end",
                iid=str(job.get("id")),
                values=(
                    str(job.get("id", "")),
                    self._format_date(job.get("createdAt")),
                    str(job.get("algorithmName", "")),
                    str(job.get("totalHashes", "")),
                    str(job.get("foundHashes", "")),
                    str(job.get("leftHashes", "")),
                    str(job.get("currency", "")),
                    f"{job.get('pricePerHash', '0')} / ${job.get('pricePerHashUsd', '0')}",
                    "Yes" if hints else "No",
                ),
                tags=(row_tag,),
            )

    def _on_jobs_heading_click(self, column: str) -> None:
        if self.jobs_sort_column == column:
            self.jobs_sort_desc = not self.jobs_sort_desc
        else:
            self.jobs_sort_column = column
            self.jobs_sort_desc = column in {"id", "created", "total", "found", "left", "price"}
        self._refresh_jobs_heading_labels()
        self._render_jobs(self.filtered_jobs)

    def _refresh_jobs_heading_labels(self) -> None:
        arrow = " \u25be" if self.jobs_sort_desc else " \u25b4"
        for column, title in self.jobs_column_titles.items():
            display = f"{title}{arrow}" if column == self.jobs_sort_column else title
            self.jobs_tree.heading(
                column,
                text=display,
                anchor="w",
                command=lambda col=column: self._on_jobs_heading_click(col),
            )

    def _sorted_jobs(self, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        key_functions = {
            "id": lambda row: self._safe_int(row.get("id"), 0),
            "created": lambda row: self._sort_datetime(row.get("createdAt")),
            "algorithm": lambda row: str(row.get("algorithmName", "")).lower(),
            "total": lambda row: self._safe_int(row.get("totalHashes"), 0),
            "found": lambda row: self._safe_int(row.get("foundHashes"), 0),
            "left": lambda row: self._safe_int(row.get("leftHashes"), 0),
            "currency": lambda row: str(row.get("currency", "")).lower(),
            "price": lambda row: self._safe_float(row.get("pricePerHashUsd"), 0.0),
            "hints": lambda row: 1 if str(row.get("hints", "")).strip() else 0,
        }
        key_fn = key_functions.get(
            self.jobs_sort_column, lambda row: self._sort_datetime(row.get("createdAt"))
        )
        return sorted(jobs, key=key_fn, reverse=self.jobs_sort_desc)

    def _on_job_selected(self, _event: tk.Event) -> None:
        selected = self.jobs_tree.selection()
        if not selected:
            self._show_duck_placeholder()
            self._refresh_jobs_stats_display()
            return
        job = self.job_index.get(selected[0])
        if not job:
            self._show_job_details()
            self._set_job_details("Details unavailable.")
            self._refresh_jobs_stats_display()
            return
        self._show_job_details()
        self._refresh_jobs_stats_display()
        lines = [
            f"Job ID: {job.get('id', '-')}",
            f"Created: {job.get('createdAt', '-')}",
            f"Last update: {job.get('lastUpdate', '-')}",
            f"Algorithm: {job.get('algorithmName', '-')}",
            f"Algorithm ID: {job.get('algorithmId', '-')}",
            f"Total hashes: {job.get('totalHashes', '-')}",
            f"Found hashes: {job.get('foundHashes', '-')}",
            f"Left hashes: {job.get('leftHashes', '-')}",
            f"Max cracks needed: {job.get('maxCracksNeeded', '-')}",
            f"Currency: {job.get('currency', '-')}",
            f"Price per hash: {job.get('pricePerHash', '-')}",
            f"Price per hash (USD): {job.get('pricePerHashUsd', '-')}",
            f"Left list path: {job.get('leftList', '-')}",
            "",
            "Hints:",
            str(job.get("hints", "")).strip() or "No hints available.",
        ]
        self._set_job_details("\n".join(lines))

    def _set_job_details(self, text: str) -> None:
        self.job_details.configure(state="normal")
        self.job_details.delete("1.0", "end")
        self.job_details.insert("1.0", text)
        self.job_details.configure(state="disabled")

    def _refresh_jobs_stats_display(self) -> None:
        selected_ids = self.jobs_tree.selection()
        if selected_ids:
            selected_jobs = [self.job_index[sid] for sid in selected_ids if sid in self.job_index]
            if selected_jobs:
                self._update_jobs_stats(selected_jobs, label="Selected jobs")
                return
        self._update_jobs_stats(self.filtered_jobs, label="Visible jobs")

    def _update_jobs_stats(self, jobs: list[dict[str, Any]], *, label: str = "Visible jobs") -> None:
        if not jobs:
            self.jobs_stats_var.set("No matching jobs.")
            return
        total_left = 0
        total_found = 0
        total_estimated_usd = 0.0
        crypto_totals: dict[str, float] = {}

        for job in jobs:
            found = self._safe_int(job.get("foundHashes"), 0)
            left = self._safe_int(job.get("leftHashes"), 0)
            max_needed = self._safe_int(job.get("maxCracksNeeded"), 0)
            total_left += left
            total_found += found

            needed_left = max(max_needed - found, 0) if found > 0 else max_needed
            usd_price = self._safe_float(job.get("pricePerHashUsd"), 0.0)
            total_estimated_usd += usd_price * needed_left

            currency = str(job.get("currency", "UNKNOWN")).upper()
            crypto_totals[currency] = crypto_totals.get(currency, 0.0) + (
                self._safe_float(job.get("pricePerHash"), 0.0) * needed_left
            )

        crypto_bits = ", ".join(
            f"{currency}: {amount:.7f}"
            for currency, amount in sorted(crypto_totals.items(), key=lambda item: item[0])
        )
        self.jobs_stats_var.set(
            f"{label}: {len(jobs)} | Left: {total_left} | Found: {total_found} | "
            f"Est. USD value: ${total_estimated_usd:.3f} | {crypto_bits}"
        )

    def _copy_selected_job_ids(self) -> None:
        selected = self.jobs_tree.selection()
        if not selected:
            messagebox.showinfo("Copy IDs", "Select one or more jobs first.")
            return
        ids = ",".join(selected)
        self.clipboard_clear()
        self.clipboard_append(ids)
        self._set_status(f"Copied {len(selected)} job ID(s).")

    def _export_jobs_csv(self) -> None:
        if not self.filtered_jobs:
            messagebox.showinfo("Export CSV", "No jobs to export.")
            return
        path = filedialog.asksaveasfilename(
            title="Export jobs to CSV",
            defaultextension=".csv",
            filetypes=[("CSV file", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        fieldnames = [
            "id",
            "createdAt",
            "lastUpdate",
            "algorithmName",
            "algorithmId",
            "totalHashes",
            "foundHashes",
            "leftHashes",
            "maxCracksNeeded",
            "currency",
            "pricePerHash",
            "pricePerHashUsd",
            "leftList",
            "hints",
        ]
        try:
            with open(path, "w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
                writer.writeheader()
                for row in self.filtered_jobs:
                    writer.writerow({name: row.get(name, "") for name in fieldnames})
            self._set_status(f"Exported {len(self.filtered_jobs)} rows to {Path(path).name}.")
        except OSError as exc:
            messagebox.showerror("Export CSV", f"Failed to write CSV: {exc}")

    def _download_selected_jobs(self) -> None:
        selected_ids = list(self.jobs_tree.selection())
        if not selected_ids:
            messagebox.showinfo("Download", "Select one or more jobs first.")
            return
        selected_jobs = [self.job_index[sid] for sid in selected_ids if sid in self.job_index]
        if not selected_jobs:
            messagebox.showerror("Download", "Could not map selected rows to job data.")
            return
        path = filedialog.asksaveasfilename(
            title="Save merged left-lists file",
            defaultextension=".txt",
            initialfile="left_hashes.txt",
            filetypes=[("Text file", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        self._set_status("Downloading selected left lists...")

        def worker() -> tuple[int, list[tuple[int, str]]]:
            def progress(
                current_index: int,
                total_jobs: int,
                downloaded: int,
                total_size: int,
                current_job: dict[str, Any],
            ) -> None:
                if total_size > 0:
                    pct = (downloaded / total_size) * 100
                    msg = (
                        f"Downloading job {current_index}/{total_jobs} "
                        f"(ID {current_job.get('id')}): {pct:.1f}%"
                    )
                else:
                    msg = (
                        f"Downloading job {current_index}/{total_jobs} "
                        f"(ID {current_job.get('id')}): {downloaded} bytes"
                    )
                self.after(0, lambda m=msg: self._set_status(m))

            return self.client.download_left_lists(selected_jobs, path, on_progress=progress)

        def on_success(result: tuple[int, list[tuple[int, str]]]) -> None:
            byte_count, failed = result
            success_count = len(selected_jobs) - len(failed)
            self._set_status(
                f"Downloaded {success_count} left-list(s), {byte_count} bytes total."
            )
            if failed:
                failed_ids = ", ".join(str(jid) for jid, _ in failed)
                details = "\n".join(f"  Job {jid}: {err}" for jid, err in failed[:5])
                if len(failed) > 5:
                    details += f"\n  ... and {len(failed) - 5} more"
                messagebox.showwarning(
                    "Download complete (some failed)",
                    f"Saved {success_count} job left-list(s) to:\n{path}\n\n"
                    f"Failed ({len(failed)}): {failed_ids}\n\n{details}",
                )
            else:
                messagebox.showinfo(
                    "Download complete",
                    f"Saved {len(selected_jobs)} job left-list(s) to:\n{path}",
                )

        self._run_background(worker, on_success, task_name="Download left lists")

    def _identify_hash(self) -> None:
        hash_value = self.identify_var.get().strip()
        if not hash_value:
            messagebox.showwarning("Identifier", "Enter a hash value first.")
            return
        self.identify_results.delete("1.0", "end")
        self.identify_results.insert("1.0", "Running identifier...")
        self._set_status("Identifying hash...")

        def worker() -> list[str]:
            return self.client.identify_hash(hash_value, self.identify_extended_var.get())

        def on_success(results: list[str]) -> None:
            self.identify_results.delete("1.0", "end")
            if results:
                self.identify_results.insert("1.0", "\n".join(results))
            else:
                self.identify_results.insert("1.0", "No matching algorithms returned.")
            self._set_status("Identifier completed.")

        self._run_background(worker, on_success, task_name="Identifier")

    def _run_lookup(self) -> None:
        if not self._require_api_key():
            return
        self.client.set_api_key(self.api_key_var.get())

        raw = self.lookup_input.get("1.0", "end")
        cleaned = self._dedupe_hashes(raw.splitlines())
        if not cleaned:
            messagebox.showwarning("Lookup", "Enter at least one hash.")
            return
        if len(cleaned) > 250:
            messagebox.showwarning("Lookup", "Please limit lookup requests to 250 hashes.")
            return

        self.lookup_summary_var.set(f"Running lookup for {len(cleaned)} hash(es)...")
        self._set_status("Running hash lookup...")

        def worker() -> dict[str, Any]:
            return self.client.lookup_hashes(cleaned)

        def on_success(payload: dict[str, Any]) -> None:
            self.lookup_results = list(payload.get("founds", []))
            self.lookup_tree.delete(*self.lookup_tree.get_children())
            for idx, result in enumerate(self.lookup_results):
                row_tag = "even" if idx % 2 == 0 else "odd"
                self.lookup_tree.insert(
                    "",
                    "end",
                    values=(
                        result.get("hash", ""),
                        result.get("salt", ""),
                        result.get("plaintext", ""),
                        result.get("algorithm", ""),
                    ),
                    tags=(row_tag,),
                )
            found = len(self.lookup_results)
            total = int(payload.get("count", len(cleaned)))
            cost = payload.get("cost", "?")
            self.lookup_summary_var.set(f"Found {found}/{total} hashes. Cost: {cost} credits.")
            self._set_status(f"Lookup complete: {found}/{total} found.")

        self._run_background(worker, on_success, task_name="Hash lookup")

    def _save_lookup_results(self) -> None:
        if not self.lookup_results:
            messagebox.showinfo("Save Results", "No lookup results available.")
            return
        path = filedialog.asksaveasfilename(
            title="Save lookup results",
            defaultextension=".txt",
            initialfile="lookup_results.txt",
            filetypes=[("Text file", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        include_algorithm = self.lookup_include_algorithm_var.get()
        try:
            with open(path, "w", encoding="utf-8") as output:
                for row in self.lookup_results:
                    hash_value = str(row.get("hash", ""))
                    salt = str(row.get("salt", ""))
                    plain = str(row.get("plaintext", ""))
                    algorithm = str(row.get("algorithm", ""))
                    parts = [hash_value]
                    if salt:
                        parts.append(salt)
                    parts.append(plain)
                    if include_algorithm:
                        parts.append(algorithm)
                    output.write(":".join(parts) + "\n")
            self._set_status(f"Saved {len(self.lookup_results)} lookup result(s).")
        except OSError as exc:
            messagebox.showerror("Save Results", f"Failed to write file: {exc}")

    def _load_lookup_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Load hash file",
            filetypes=[("Text file", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as source:
                content = source.read().strip()
        except OSError as exc:
            messagebox.showerror("Load File", f"Failed to open file: {exc}")
            return
        self.lookup_input.delete("1.0", "end")
        self.lookup_input.insert("1.0", content)
        count = len([line for line in content.splitlines() if line.strip()])
        self._set_status(f"Loaded {count} line(s) from {Path(path).name}.")

    def _clear_lookup_input(self) -> None:
        self.lookup_input.delete("1.0", "end")
        self.lookup_tree.delete(*self.lookup_tree.get_children())
        self.lookup_results = []
        self.lookup_summary_var.set("No lookup performed yet.")

    def refresh_balance(self, show_feedback: bool = True) -> None:
        if not self._require_api_key():
            return
        self.client.set_api_key(self.api_key_var.get())
        if show_feedback:
            self._set_status("Loading account balance...")

        def worker() -> tuple[list[tuple[str, str, str]], float]:
            raw = self.client.get_balance()
            rows: list[tuple[str, str, str]] = []
            total_usd = 0.0
            for currency, amount in raw.items():
                amount_str = str(amount)
                numeric = self._safe_float(amount_str, 0.0)
                if numeric > 0:
                    usd = self.client.convert_to_usd(numeric, currency)
                    total_usd += self._safe_float(usd.replace("$", ""), 0.0)
                else:
                    usd = "$0.00"
                rows.append((currency, amount_str, usd))
            rows.sort(key=lambda row: row[0])
            return rows, total_usd

        def on_success(result: tuple[list[tuple[str, str, str]], float]) -> None:
            rows, total_usd = result
            self.balance_tree.delete(*self.balance_tree.get_children())
            for idx, row in enumerate(rows):
                row_tag = "even" if idx % 2 == 0 else "odd"
                self.balance_tree.insert("", "end", values=row, tags=(row_tag,))
            self.balance_summary_var.set(
                f"Currencies: {len(rows)} | Approx total USD value: ${total_usd:.3f}"
            )
            self._set_status("Balance loaded.")

        self._run_background(worker, on_success, task_name="Load balance")

    def _load_config(self) -> dict[str, Any]:
        if not self.CONFIG_PATH.exists():
            return {}
        try:
            return json.loads(self.CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_config(self) -> None:
        jobs_widths = self._current_tree_widths(self.jobs_tree, self.jobs_columns)
        lookup_widths = self._current_tree_widths(self.lookup_tree, self.lookup_columns)
        balance_widths = self._current_tree_widths(self.balance_tree, self.balance_columns)
        config = {
            "api_key": self.api_key_var.get().strip(),
            "jobs_currency": self.jobs_currency_var.get().strip(),
            "jobs_algorithm": self.jobs_alg_var.get().strip(),
            "jobs_min_left": self.jobs_min_left_var.get().strip(),
            "jobs_table_sort_column": self.jobs_sort_column,
            "jobs_table_sort_desc": bool(self.jobs_sort_desc),
            "jobs_table_columns": jobs_widths,
            "lookup_table_columns": lookup_widths,
            "balance_table_columns": balance_widths,
            "jobs_pane_sash": self._current_jobs_pane_sash(),
        }
        try:
            self.CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
            self.config_data = dict(config)
            self._last_layout_snapshot = self._capture_layout_snapshot()
        except OSError:
            pass  # don't block close if we can't write config

    def _on_close(self) -> None:
        self._save_config()
        self.destroy()

    def _on_layout_interaction(self, _event: tk.Event | None = None) -> None:
        if self._layout_save_after_id:
            self.after_cancel(self._layout_save_after_id)
        self._layout_save_after_id = self.after(220, self._save_layout_if_changed)

    def _save_layout_if_changed(self) -> None:
        self._layout_save_after_id = None
        current = self._capture_layout_snapshot()
        if current != self._last_layout_snapshot:
            self._save_config()

    def _capture_layout_snapshot(self) -> dict[str, Any]:
        return {
            "jobs_table_columns": self._current_tree_widths(self.jobs_tree, self.jobs_columns),
            "lookup_table_columns": self._current_tree_widths(self.lookup_tree, self.lookup_columns),
            "balance_table_columns": self._current_tree_widths(
                self.balance_tree, self.balance_columns
            ),
            "jobs_pane_sash": self._current_jobs_pane_sash(),
        }

    def _job_algorithm_options(self) -> list[str]:
        options = ["All"]
        for alg_id in sorted(self.algorithms, key=self._sort_algorithm_key):
            options.append(f"{alg_id} - {self.algorithms[alg_id]}")
        return options

    def _load_column_widths(
        self, config_key: str, defaults: dict[str, int]
    ) -> dict[str, int]:
        loaded = self.config_data.get(config_key, {})
        if not isinstance(loaded, dict):
            loaded = {}
        widths: dict[str, int] = {}
        for column, default_width in defaults.items():
            value = loaded.get(column, default_width)
            parsed = self._safe_int(value, default_width)
            widths[column] = max(25, min(parsed, 800))
        return widths

    def _current_tree_widths(
        self, tree: ttk.Treeview, columns: tuple[str, ...]
    ) -> dict[str, int]:
        widths: dict[str, int] = {}
        for column in columns:
            widths[column] = self._safe_int(tree.column(column, "width"), 0)
        return widths

    def _current_jobs_pane_sash(self) -> int | None:
        try:
            return int(self.jobs_content_pane.sashpos(0))
        except (AttributeError, tk.TclError, ValueError):
            return None

    def _restore_jobs_pane_sash(self) -> None:
        saved = self.config_data.get("jobs_pane_sash")
        if saved is None:
            self._last_layout_snapshot = self._capture_layout_snapshot()
            return
        target = self._safe_int(saved, -1)
        if target <= 0:
            self._last_layout_snapshot = self._capture_layout_snapshot()
            return
        try:
            pane_width = self.jobs_content_pane.winfo_width()
            if pane_width <= 0:
                self.after(150, self._restore_jobs_pane_sash)
                return

            clamped = max(280, min(target, max(320, pane_width - 320)))
            self.jobs_content_pane.sashpos(0, clamped)
        except tk.TclError:
            pass
        self._last_layout_snapshot = self._capture_layout_snapshot()

    def _refresh_job_filter_options(self, jobs: list[dict[str, Any]]) -> None:
        current_currency = self.jobs_currency_var.get().strip() or "All"
        current_alg = self.jobs_alg_var.get().strip() or "All"

        currencies = sorted({str(job.get("currency", "")).upper() for job in jobs if job.get("currency")})
        currency_values = ["All"] + currencies
        self.jobs_currency_combo.configure(values=currency_values)
        self.jobs_currency_var.set(current_currency if current_currency in currency_values else "All")

        alg_values = self._job_algorithm_options()
        self.jobs_alg_combo.configure(values=alg_values)
        if current_alg in alg_values:
            self.jobs_alg_var.set(current_alg)
        elif " - " in current_alg:
            self.jobs_alg_var.set("All")

    def _get_algorithm_filter(self) -> tuple[str | None, str | None]:
        selected = self.jobs_alg_var.get().strip()
        if not selected or selected == "All":
            return None, None
        if " - " in selected:
            return "exact", selected.split(" - ", 1)[0].strip()
        if selected[0].isdigit():
            return "id", selected
        return "name", selected.lower()

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _format_date(raw: Any) -> str:
        if not raw:
            return "-"
        value = str(raw)
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
        except ValueError:
            return value

    @staticmethod
    def _sort_datetime(raw: Any) -> tuple[int, datetime | str]:
        value = str(raw) if raw is not None else ""
        try:
            return (0, datetime.strptime(value, "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            return (1, value)

    @staticmethod
    def _dedupe_hashes(lines: list[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for line in lines:
            cleaned = line.strip()
            if cleaned and cleaned not in seen:
                unique.append(cleaned)
                seen.add(cleaned)
        return unique

    @staticmethod
    def _sort_algorithm_key(value: str) -> tuple[int, str]:
        try:
            return (0, f"{int(value):08d}")
        except ValueError:
            return (1, value)


def main() -> None:
    app = HashesGuiApp()
    app.mainloop()


if __name__ == "__main__":
    main()
