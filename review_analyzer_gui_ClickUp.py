import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import pandas as pd
from datetime import datetime, timedelta
import re
import os
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from tkinter import font
import json
import requests
from datetime import datetime
from tkinter import simpledialog

# Import from your scraper script
try:
    from google_maps_scraper import (
        scrape_reviews_function,
        scrape_reviews_function_recent_with_text,
        process_reviews_function,
        save_reviews_function,
        detect_review_language,
        GoogleMapsReviewScraper,
        ReviewTextProcessor,
    )
    SCRAPER_AVAILABLE = True
except ImportError as e:
    print(f"Warning: Could not import scraper functions: {e}")
    SCRAPER_AVAILABLE = False

try:
    from batch_date_window_to_clickup import (
        CONFIG_FILE as BATCH_LOCATIONS_CONFIG,
        DATE_WINDOW_OPTIONS,
        LocationConfig,
        build_task_payload,
        date_window_choice_to_days,
        get_priority_from_rating,
        resolve_clickup_list_id_by_name,
    )
    BATCH_MODULE_AVAILABLE = True
except ImportError as e:
    print(f"Warning: Could not import batch_date_window_to_clickup: {e}")
    BATCH_MODULE_AVAILABLE = False
    BATCH_LOCATIONS_CONFIG = Path(__file__).resolve().parent / "last_7_days_batch_config.json"
    LocationConfig = None
    build_task_payload = None
    resolve_clickup_list_id_by_name = None

    def get_priority_from_rating(rating):
        try:
            rating_num = float(rating)
            if rating_num <= 2:
                return 1
            if rating_num <= 3:
                return 2
            if rating_num <= 4:
                return 3
            return 4
        except Exception:
            return 3

    DATE_WINDOW_OPTIONS = [
        "Last week",
        "Last 2 weeks",
        "Last 3 weeks",
        "Last 1 month",
        "Last 2 months",
        "Last 3 months",
        "Last 4 months",
        "Last 5 months",
        "Last 6 months",
    ]

    def date_window_choice_to_days(choice: str) -> int:
        m = {
            "Last week": 7,
            "Last 2 weeks": 14,
            "Last 3 weeks": 21,
            "Last 1 month": 30,
            "Last 2 months": 60,
            "Last 3 months": 90,
            "Last 4 months": 120,
            "Last 5 months": 150,
            "Last 6 months": 180,
        }
        return max(1, int(m.get((choice or "").strip(), 7)))


def _gui_scrape_one_location(args):
    """
    Scrape and process a single place URL in its own browser session.
    Intended for ThreadPoolExecutor: two workers = two Chrome instances in parallel.
    """
    loc_name, url, num_reviews, mode, days_back = args
    if not SCRAPER_AVAILABLE:
        return {"name": loc_name, "reviews": [], "error": f"{loc_name}: scraper not loaded"}
    if not url:
        return {"name": loc_name, "reviews": [], "error": f"{loc_name}: missing URL"}
    try:
        if mode == "date_window" and days_back is not None:
            reviews = scrape_reviews_function_recent_with_text(url, days_back)
        else:
            reviews = scrape_reviews_function(url, num_reviews)
        if not reviews:
            return {"name": loc_name, "reviews": [], "error": f"{loc_name}: no reviews returned"}
        processed = process_reviews_function(reviews)
        for r in processed:
            r["location_name"] = loc_name
            r["mall_name"] = loc_name
        return {"name": loc_name, "reviews": processed, "error": None}
    except Exception as e:
        return {"name": loc_name, "reviews": [], "error": f"{loc_name}: {e}"}


class ReviewAnalyzerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Google Maps Review Analyzer & Scraper")
        self.root.geometry("1000x800")
        self.root.configure(bg='#f0f0f0')

        # Data storage
        self.reviews_df = None
        self.filtered_reviews = None
        self.all_reviews = []

        self.makan_locations = []
        self.competitor_locations = []
        self.clickup_config = {}
        self.last_scrape_locations = []
        self.last_scrape_meta = {}
        self._load_batch_locations_config()

        # Configure style
        self.setup_styles()
        self.setup_ui()

        # ClickUp data storage
        self.workspace_data = {}
        self.space_data = {}
        self.list_data = {}
        self.makan_group_list_id = None
        self.competitor_group_list_id = None

    def setup_styles(self):
        """Setup custom styles for the GUI"""
        self.style = ttk.Style()
        self.style.theme_use('clam')

        # Custom colors
        self.style.configure('Title.TLabel', font=('Arial', 16, 'bold'), background='#f0f0f0')
        self.style.configure('Header.TLabel', font=('Arial', 12, 'bold'), background='#f0f0f0')
        self.style.configure('Custom.TButton', font=('Arial', 10, 'bold'))

    def setup_ui(self):
        """Setup the GUI interface"""
        # Create notebook for tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True, padx=10, pady=10)

        # Tab 1: Scrape New Reviews
        self.setup_scraper_tab()

        # Tab 2: Analyze Existing Reviews
        self.setup_analyzer_tab()

        # Tab 3: ClickUp Integration
        self.setup_clickup_tab()

    def setup_scraper_tab(self):
        """Setup the review scraping tab"""
        scraper_frame = ttk.Frame(self.notebook)
        self.notebook.add(scraper_frame, text="Scrape Reviews")

        # Title
        title_label = ttk.Label(scraper_frame, text="Google Maps Review Scraper", style='Title.TLabel')
        title_label.pack(pady=(10, 20))

        # Input frame
        input_frame = ttk.LabelFrame(scraper_frame, text="Scraping Parameters", padding="15")
        input_frame.pack(fill='x', padx=20, pady=10)

        # Locations from last_7_days_batch_config.json (no manual URL)
        loc_outer = ttk.LabelFrame(
            input_frame,
            text="Locations (last_7_days_batch_config.json)",
            padding="10",
        )
        loc_outer.pack(fill='x', pady=(0, 15))

        cfg_hint = (
            f"Loaded {len(self.makan_locations)} MAKAN and {len(self.competitor_locations)} competitor location(s)."
            if BATCH_LOCATIONS_CONFIG.exists()
            else f"Config not found: {BATCH_LOCATIONS_CONFIG.name} (place it next to this script)."
        )
        self.locations_config_hint = ttk.Label(loc_outer, text=cfg_hint, font=('Arial', 9), foreground='#333')
        self.locations_config_hint.pack(anchor='w', pady=(0, 8))

        src_frame = ttk.Frame(loc_outer)
        src_frame.pack(fill='x', pady=(0, 6))
        ttk.Label(src_frame, text="Location group:", style='Header.TLabel').pack(side='left')
        self.location_source_var = tk.StringVar(value="makan")
        ttk.Radiobutton(
            src_frame,
            text="MAKAN's Locations",
            variable=self.location_source_var,
            value="makan",
            command=self._on_location_source_change,
        ).pack(side='left', padx=(12, 0))
        ttk.Radiobutton(
            src_frame,
            text="Competitors' Locations",
            variable=self.location_source_var,
            value="competitor",
            command=self._on_location_source_change,
        ).pack(side='left', padx=(12, 0))

        scope_frame = ttk.Frame(loc_outer)
        scope_frame.pack(fill='x', pady=(0, 6))
        ttk.Label(scope_frame, text="Scrape:", style='Header.TLabel').pack(side='left')
        self.location_scope_var = tk.StringVar(value="one")
        ttk.Radiobutton(
            scope_frame,
            text="One location",
            variable=self.location_scope_var,
            value="one",
            command=self._on_location_scope_change,
        ).pack(side='left', padx=(12, 0))
        ttk.Radiobutton(
            scope_frame,
            text="Multiple locations",
            variable=self.location_scope_var,
            value="many",
            command=self._on_location_scope_change,
        ).pack(side='left', padx=(12, 0))
        ttk.Radiobutton(
            scope_frame,
            text="All locations in this group",
            variable=self.location_scope_var,
            value="all",
            command=self._on_location_scope_change,
        ).pack(side='left', padx=(12, 0))

        self.location_pick_frame = ttk.Frame(loc_outer)
        self.location_pick_frame.pack(fill='both', expand=True)

        one_fr = ttk.Frame(self.location_pick_frame)
        ttk.Label(one_fr, text="Location:", font=('Arial', 10)).pack(anchor='w')
        self.location_one_var = tk.StringVar()
        self.location_one_combo = ttk.Combobox(
            one_fr,
            textvariable=self.location_one_var,
            state="readonly",
            width=78,
            font=('Arial', 10),
        )
        self.location_one_combo.pack(fill='x', pady=(4, 0))
        self._location_one_frame = one_fr

        many_fr = ttk.Frame(self.location_pick_frame)
        ttk.Label(
            many_fr,
            text="Select locations (Ctrl+click or Shift+click for multiple):",
            font=('Arial', 10),
        ).pack(anchor='w')
        many_inner = ttk.Frame(many_fr)
        many_inner.pack(fill='both', expand=True, pady=(4, 0))
        many_scroll = ttk.Scrollbar(many_inner)
        many_scroll.pack(side='right', fill='y')
        self.location_many_listbox = tk.Listbox(
            many_inner,
            height=8,
            selectmode=tk.EXTENDED,
            exportselection=False,
            font=('Arial', 10),
            yscrollcommand=many_scroll.set,
        )
        self.location_many_listbox.pack(side='left', fill='both', expand=True)
        many_scroll.config(command=self.location_many_listbox.yview)
        self._location_many_frame = many_fr

        all_fr = ttk.Frame(self.location_pick_frame)
        self.location_all_label = ttk.Label(all_fr, text="", font=('Arial', 10))
        self.location_all_label.pack(anchor='w')
        self._location_all_frame = all_fr

        self._on_location_source_change()
        self._on_location_scope_change()

        # Scrape mode
        mode_frame = ttk.Frame(input_frame)
        mode_frame.pack(fill='x', pady=(0, 10))
        ttk.Label(mode_frame, text="Scrape mode:", style='Header.TLabel').pack(side='left')
        self.scrape_mode_var = tk.StringVar(value="count")
        ttk.Radiobutton(
            mode_frame,
            text="By review count",
            variable=self.scrape_mode_var,
            value="count",
            command=self._on_scrape_mode_change,
        ).pack(side='left', padx=(10, 0))
        ttk.Radiobutton(
            mode_frame,
            text="By date range (reviews with text only)",
            variable=self.scrape_mode_var,
            value="date_window",
            command=self._on_scrape_mode_change,
        ).pack(side='left', padx=(10, 0))

        date_win_row = ttk.Frame(input_frame)
        date_win_row.pack(fill='x', pady=(0, 10))
        ttk.Label(date_win_row, text="Date range:", style='Header.TLabel').pack(side='left')
        self.date_window_var = tk.StringVar(
            value=DATE_WINDOW_OPTIONS[0] if DATE_WINDOW_OPTIONS else "Last week"
        )
        self.date_window_combo = ttk.Combobox(
            date_win_row,
            textvariable=self.date_window_var,
            values=DATE_WINDOW_OPTIONS,
            state="readonly",
            width=42,
            font=('Arial', 10),
        )
        self.date_window_combo.pack(side='left', padx=(10, 0))
        ttk.Label(
            date_win_row,
            text="(used only in “By date range” mode; months ≈ 30 days)",
            font=('Arial', 9),
            foreground='#555',
        ).pack(side='left', padx=(10, 0))
        self._date_window_row = date_win_row

        # Number of reviews
        reviews_frame = ttk.Frame(input_frame)
        reviews_frame.pack(fill='x', pady=(0, 15))

        ttk.Label(reviews_frame, text="Number of Reviews:", style='Header.TLabel').pack(side='left')
        self.num_reviews_var = tk.StringVar(value="50")
        self.num_reviews_spinbox = ttk.Spinbox(reviews_frame, from_=1, to=10000, textvariable=self.num_reviews_var, width=10)
        self.num_reviews_spinbox.pack(side='left', padx=(10, 0))
        ttk.Label(
            reviews_frame,
            text="(used only in “By review count” mode)",
            font=('Arial', 9),
            foreground='#555',
        ).pack(side='left', padx=(10, 0))

        # Scrape button
        self.scrape_button = ttk.Button(input_frame, text="Start Scraping",
                                       command=self.start_scraping, style='Custom.TButton')
        self.scrape_button.pack(pady=(10, 0))

        # Progress bar
        self.progress_var = tk.StringVar(value="Ready to scrape...")
        ttk.Label(input_frame, textvariable=self.progress_var).pack(pady=(10, 0))

        self.progress_bar = ttk.Progressbar(input_frame, mode='indeterminate')
        self.progress_bar.pack(fill='x', pady=(5, 0))

        # Results frame
        results_frame = ttk.LabelFrame(scraper_frame, text="Scraped Reviews", padding="15")
        results_frame.pack(fill='both', expand=True, padx=20, pady=10)

        # Reviews text display
        self.scraped_text = scrolledtext.ScrolledText(results_frame, height=15, width=80,
                                                     font=('Arial', 9), wrap=tk.WORD)
        self.scraped_text.pack(fill='both', expand=True, pady=(0, 10))

        # Save buttons frame
        save_frame = ttk.Frame(results_frame)
        save_frame.pack(fill='x')

        ttk.Button(save_frame, text="Save to CSV", command=self.save_scraped_reviews).pack(side='left', padx=(0, 10))
        ttk.Button(save_frame, text="Use for Analysis", command=self.use_for_analysis).pack(side='left')

        self._on_scrape_mode_change()

    def _on_scrape_mode_change(self):
        """Enable count spinbox only when scraping by fixed number; date combo only for date window."""
        if getattr(self, "num_reviews_spinbox", None) is None:
            return
        mode = self.scrape_mode_var.get()
        if mode == "count":
            self.num_reviews_spinbox.state(["!disabled"])
        else:
            self.num_reviews_spinbox.state(["disabled"])

        if getattr(self, "date_window_combo", None) is not None:
            if mode == "date_window":
                self.date_window_combo.state(["!disabled"])
            else:
                self.date_window_combo.state(["disabled"])

    def _load_batch_locations_config(self):
        """Load MAKAN/competitor locations and ClickUp settings from last_7_days_batch_config.json."""
        self.makan_locations = []
        self.competitor_locations = []
        self.clickup_config = {}
        path = BATCH_LOCATIONS_CONFIG
        if not path.exists():
            return
        try:
            with path.open("r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.clickup_config = cfg.get("clickup") or {}

            def _parse_loc(item):
                name = str(item.get("name", "")).strip()
                url = str(item.get("url", "")).strip()
                if not name or not url:
                    return None
                list_id = item.get("clickup_list_id")
                return {
                    "name": name,
                    "url": url,
                    "clickup_list_id": (str(list_id).strip() or None) if list_id is not None else None,
                    "tag": (str(item.get("tag", "")).strip() or None),
                }

            for item in cfg.get("makan_locations") or []:
                loc = _parse_loc(item)
                if loc:
                    self.makan_locations.append(loc)
            for item in cfg.get("competitor_locations") or []:
                loc = _parse_loc(item)
                if loc:
                    self.competitor_locations.append(loc)
        except Exception as e:
            print(f"Warning: could not load {path}: {e}")

    def _prefill_clickup_token_from_config(self):
        token = str(self.clickup_config.get("api_token", "")).strip()
        if token and token != "PASTE_CLICKUP_TOKEN_HERE" and getattr(self, "clickup_token_entry", None):
            self.clickup_token_entry.delete(0, tk.END)
            self.clickup_token_entry.insert(0, token)

    def _location_group_for_name(self, name: str):
        if any(loc["name"] == name for loc in self.competitor_locations):
            return "competitor"
        return "makan"

    def _location_record_by_name(self, name: str):
        for loc in self.makan_locations + self.competitor_locations:
            if loc["name"] == name:
                return loc
        return None

    def _update_clickup_scrape_summary(self):
        if not getattr(self, "clickup_scrape_summary_var", None):
            return
        if not self.last_scrape_locations:
            self.clickup_scrape_summary_var.set("No scrape yet — run Scrape Reviews first.")
            return
        meta = self.last_scrape_meta or {}
        group = meta.get("group_label", "Locations")
        scope = meta.get("scope_label", "")
        mode = meta.get("mode_label", "")
        names = [n for n, _ in self.last_scrape_locations]
        preview = ", ".join(names[:4])
        if len(names) > 4:
            preview += f", … (+{len(names) - 4} more)"
        reviews_n = len(self.all_reviews) if self.all_reviews else 0
        self.clickup_scrape_summary_var.set(
            f"{reviews_n} review(s) from {len(names)} {group} location(s) ({scope}; {mode}). "
            f"Locations: {preview}"
        )

    def load_clickup_from_config(self):
        """Pre-fill token and resolve workspace/space/lists from batch config names."""
        self._load_batch_locations_config()
        self._prefill_clickup_token_from_config()

        workspace_name = str(self.clickup_config.get("workspace_name", "")).strip()
        space_name = str(self.clickup_config.get("space_name", "")).strip()
        makan_list_name = str(self.clickup_config.get("makan_list_name", "")).strip()
        competitor_list_name = str(self.clickup_config.get("competitor_list_name", "")).strip()

        token = self.clickup_token_entry.get().strip()
        if not token:
            messagebox.showerror("Error", "Set clickup.api_token in the config file or enter a token.")
            return

        if not workspace_name or not space_name:
            messagebox.showwarning(
                "Config",
                "Set clickup.workspace_name and clickup.space_name in the config, "
                "or use Load Workspaces / Spaces / Lists manually.",
            )
            return

        if not BATCH_MODULE_AVAILABLE:
            messagebox.showerror("Error", "batch_date_window_to_clickup.py is required for config-based list routing.")
            return

        try:
            headers = {"Authorization": token, "Content-Type": "application/json"}
            teams_resp = requests.get("https://api.clickup.com/api/v2/team", headers=headers, timeout=20)
            if teams_resp.status_code != 200:
                raise RuntimeError(f"Failed to load workspaces ({teams_resp.status_code})")
            teams = teams_resp.json().get("teams", [])
            self.workspace_data = {t["name"]: t["id"] for t in teams}
            self.workspace_combo["values"] = list(self.workspace_data.keys())
            if workspace_name in self.workspace_data:
                self.workspace_var.set(workspace_name)

            team_id = self.workspace_data.get(workspace_name)
            if not team_id:
                raise RuntimeError(f"Workspace not found: {workspace_name}")

            spaces_resp = requests.get(
                f"https://api.clickup.com/api/v2/team/{team_id}/space", headers=headers, timeout=20
            )
            if spaces_resp.status_code != 200:
                raise RuntimeError(f"Failed to load spaces ({spaces_resp.status_code})")
            spaces = spaces_resp.json().get("spaces", [])
            self.space_data = {s["name"]: s["id"] for s in spaces}
            self.space_combo["values"] = list(self.space_data.keys())
            if space_name in self.space_data:
                self.space_var.set(space_name)

            self.makan_group_list_id = None
            self.competitor_group_list_id = None
            if makan_list_name:
                self.makan_group_list_id = resolve_clickup_list_id_by_name(
                    token, workspace_name, space_name, makan_list_name
                )
            if competitor_list_name:
                self.competitor_group_list_id = resolve_clickup_list_id_by_name(
                    token, workspace_name, space_name, competitor_list_name
                )

            resolved = []
            if makan_list_name and self.makan_group_list_id:
                resolved.append(f"MAKAN → {makan_list_name}")
                self.list_data[makan_list_name] = self.makan_group_list_id
            if competitor_list_name and self.competitor_group_list_id:
                resolved.append(f"Competitors → {competitor_list_name}")
                self.list_data[competitor_list_name] = self.competitor_group_list_id

            list_names = list(self.list_data.keys())
            self.list_combo["values"] = list_names
            if list_names:
                self.list_var.set(list_names[0])

            msg = "Loaded ClickUp settings from config."
            if resolved:
                msg += "\n" + "\n".join(resolved)
            messagebox.showinfo("Success", msg)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load ClickUp settings from config:\n{e}")

    def _active_location_pool(self):
        if self.location_source_var.get() == "competitor":
            return self.competitor_locations
        return self.makan_locations

    def _on_location_source_change(self):
        pool = self._active_location_pool()
        names = [loc["name"] for loc in pool]
        self.location_one_combo["values"] = names
        if names:
            if self.location_one_var.get() not in names:
                self.location_one_var.set(names[0])
        else:
            self.location_one_var.set("")

        self.location_many_listbox.delete(0, tk.END)
        for loc in pool:
            self.location_many_listbox.insert(tk.END, loc["name"])

        n = len(pool)
        group = "Competitors" if self.location_source_var.get() == "competitor" else "MAKAN"
        self.location_all_label.config(
            text=f"All {n} {group} location(s) will be scraped in order." if n else f"No {group} locations in config."
        )

    def _on_location_scope_change(self):
        for fr in (
            getattr(self, "_location_one_frame", None),
            getattr(self, "_location_many_frame", None),
            getattr(self, "_location_all_frame", None),
        ):
            if fr is not None:
                fr.pack_forget()
        scope = self.location_scope_var.get()
        if scope == "one":
            self._location_one_frame.pack(fill='x', expand=True)
        elif scope == "many":
            self._location_many_frame.pack(fill='both', expand=True)
        else:
            self._location_all_frame.pack(fill='x', expand=True)

    def _collect_scrape_locations(self):
        """Return list of (name, url) for the current UI selection, or None if invalid."""
        pool = self._active_location_pool()
        group = "MAKAN's Locations" if self.location_source_var.get() == "makan" else "Competitors' Locations"
        if not pool:
            messagebox.showerror(
                "Error",
                f"No locations configured for {group}.\n"
                f"Add entries under makan_locations / competitor_locations in:\n{BATCH_LOCATIONS_CONFIG}",
            )
            return None

        scope = self.location_scope_var.get()
        if scope == "all":
            return [(loc["name"], loc["url"]) for loc in pool]

        if scope == "one":
            name = (self.location_one_var.get() or "").strip()
            if not name:
                messagebox.showerror("Error", "Choose a location from the dropdown.")
                return None
            loc = next((x for x in pool if x["name"] == name), None)
            if not loc:
                messagebox.showerror("Error", "Selected location was not found in config.")
                return None
            return [(loc["name"], loc["url"])]

        indices = self.location_many_listbox.curselection()
        if not indices:
            messagebox.showerror(
                "Error",
                "Select one or more locations in the list (Ctrl+click or Shift+click).",
            )
            return None
        out = []
        for i in indices:
            if 0 <= i < len(pool):
                loc = pool[i]
                out.append((loc["name"], loc["url"]))
        return out

    def setup_analyzer_tab(self):
        """Setup the review analysis tab"""
        analyzer_frame = ttk.Frame(self.notebook)
        self.notebook.add(analyzer_frame, text="Analyze Reviews")

        # Title
        title_label = ttk.Label(analyzer_frame, text="Review Keyword & Time Filter", style='Title.TLabel')
        title_label.pack(pady=(10, 20))

        # Input frame
        filter_frame = ttk.LabelFrame(analyzer_frame, text="Filter Parameters", padding="15")
        filter_frame.pack(fill='x', padx=20, pady=10)

        # Load CSV button
        load_frame = ttk.Frame(filter_frame)
        load_frame.pack(fill='x', pady=(0, 15))

        ttk.Button(load_frame, text="Load CSV File", command=self.load_csv_file,
                style='Custom.TButton').pack(side='left')

        self.file_label = ttk.Label(load_frame, text="No file loaded", foreground='red')
        self.file_label.pack(side='left', padx=(15, 0))

        # Language filter
        lang_frame = ttk.Frame(filter_frame)
        lang_frame.pack(fill='x', pady=(0, 15))

        ttk.Label(lang_frame, text="Language Filter:", style='Header.TLabel').pack(side='left')

        self.language_var = tk.StringVar(value="all")
        lang_options = [("All Languages", "all"), ("English Only", "english"),
                    ("Arabic Only", "arabic"), ("Mixed Content", "mixed")]

        for text, value in lang_options:
            ttk.Radiobutton(lang_frame, text=text, variable=self.language_var,
                        value=value).pack(side='left', padx=(10, 0))

        # Keyword input
        ttk.Label(filter_frame, text="Keyword to Search:", style='Header.TLabel').pack(anchor='w', pady=(15, 5))
        self.keyword_entry = ttk.Entry(filter_frame, font=('Arial', 11), width=40)
        self.keyword_entry.pack(anchor='w', pady=(0, 15))

        # Time period frame
        time_frame = ttk.Frame(filter_frame)
        time_frame.pack(fill='x', pady=(0, 15))

        ttk.Label(time_frame, text="Time Period (days from today):", style='Header.TLabel').pack(side='left')
        self.days_var = tk.StringVar(value="30")
        self.days_spinbox = ttk.Spinbox(time_frame, from_=1, to=365, textvariable=self.days_var, width=10)
        self.days_spinbox.pack(side='left', padx=(10, 0))

        # Max results
        max_frame = ttk.Frame(filter_frame)
        max_frame.pack(fill='x', pady=(0, 15))

        ttk.Label(max_frame, text="Max Results:", style='Header.TLabel').pack(side='left')
        self.max_results_var = tk.StringVar(value="100")
        self.max_results_spinbox = ttk.Spinbox(max_frame, from_=1, to=1000, textvariable=self.max_results_var, width=10)
        self.max_results_spinbox.pack(side='left', padx=(10, 0))

        # Search button
        self.search_button = ttk.Button(filter_frame, text="Search Reviews",
                                    command=self.search_reviews, style='Custom.TButton')
        self.search_button.pack(pady=(10, 0))

        # Results frame
        results_frame = ttk.LabelFrame(analyzer_frame, text="Search Results", padding="15")
        results_frame.pack(fill='both', expand=True, padx=20, pady=10)

        # Results summary
        self.results_label = ttk.Label(results_frame, text="Load a CSV file and enter search criteria",
                                     style='Header.TLabel')
        self.results_label.pack(pady=(0, 10))

        # Results text display
        self.results_text = scrolledtext.ScrolledText(results_frame, height=15, width=80,
                                                     font=('Arial', 9), wrap=tk.WORD)
        self.results_text.pack(fill='both', expand=True, pady=(0, 10))

        # Export button frame
        export_frame = ttk.Frame(results_frame)
        export_frame.pack(fill='x')

        ttk.Button(export_frame, text="Export Filtered Results (CSV)",
                command=self.export_filtered_results, style='Custom.TButton').pack(side='left')

    def setup_clickup_tab(self):
        """Setup the ClickUp integration tab"""
        clickup_frame = ttk.Frame(self.notebook)
        self.notebook.add(clickup_frame, text="ClickUp Integration")

        # Title
        title_label = ttk.Label(clickup_frame, text="ClickUp Integration", style='Title.TLabel')
        title_label.pack(pady=(10, 20))

        # API Token input
        token_frame = ttk.LabelFrame(clickup_frame, text="API Token", padding="15")
        token_frame.pack(fill='x', padx=20, pady=10)

        ttk.Label(token_frame, text="ClickUp API Token:", style='Header.TLabel').pack(anchor='w')
        self.clickup_token_entry = ttk.Entry(token_frame, font=('Arial', 10), width=60, show="*")
        self.clickup_token_entry.pack(fill='x', pady=(5, 0))

        # Workspace selection
        workspace_frame = ttk.LabelFrame(clickup_frame, text="Workspace", padding="15")
        workspace_frame.pack(fill='x', padx=20, pady=10)

        ttk.Label(workspace_frame, text="Workspace:", style='Header.TLabel').pack(side='left')
        self.workspace_var = tk.StringVar()
        self.workspace_combo = ttk.Combobox(workspace_frame, textvariable=self.workspace_var,
                                        width=30, state="readonly")
        self.workspace_combo.pack(side='left', padx=(10, 0))

        ttk.Button(workspace_frame, text="Load Workspaces",
                command=self.load_clickup_workspaces).pack(side='left', padx=(10, 0))

        # Space selection
        space_frame = ttk.LabelFrame(clickup_frame, text="Space", padding="15")
        space_frame.pack(fill='x', padx=20, pady=10)

        ttk.Label(space_frame, text="Space:", style='Header.TLabel').pack(side='left')
        self.space_var = tk.StringVar()
        self.space_combo = ttk.Combobox(space_frame, textvariable=self.space_var,
                                    width=30, state="readonly")
        self.space_combo.pack(side='left', padx=(10, 0))

        ttk.Button(space_frame, text="Load Spaces",
                command=self.load_clickup_spaces).pack(side='left', padx=(10, 0))

        # List selection
        list_frame = ttk.LabelFrame(clickup_frame, text="List", padding="15")
        list_frame.pack(fill='x', padx=20, pady=10)

        ttk.Label(list_frame, text="List:", style='Header.TLabel').pack(side='left')
        self.list_var = tk.StringVar()
        self.list_combo = ttk.Combobox(list_frame, textvariable=self.list_var,
                                    width=30, state="readonly")
        self.list_combo.pack(side='left', padx=(10, 0))

        ttk.Button(list_frame, text="Load Lists",
                command=self.load_clickup_lists).pack(side='left', padx=(10, 0))

        # Scrape context (mirrors Scrape Reviews tab choices)
        scrape_ctx_frame = ttk.LabelFrame(clickup_frame, text="Last Scrape (upload source)", padding="15")
        scrape_ctx_frame.pack(fill='x', padx=20, pady=10)

        self.clickup_scrape_summary_var = tk.StringVar(value="No scrape yet — run Scrape Reviews first.")
        ttk.Label(
            scrape_ctx_frame,
            textvariable=self.clickup_scrape_summary_var,
            font=('Arial', 9),
            wraplength=900,
        ).pack(anchor='w')

        # Data selection and upload
        upload_frame = ttk.LabelFrame(clickup_frame, text="Data Selection & Upload", padding="15")
        upload_frame.pack(fill='x', padx=20, pady=10)

        src_row = ttk.Frame(upload_frame)
        src_row.pack(fill='x', pady=(0, 8))
        ttk.Label(src_row, text="Upload:", style='Header.TLabel').pack(side='left')
        self.data_type_var = tk.StringVar(value="scraped")
        ttk.Radiobutton(
            src_row,
            text="Last scraped reviews (per location)",
            variable=self.data_type_var,
            value="scraped",
        ).pack(side='left', padx=(10, 0))
        ttk.Radiobutton(
            src_row,
            text="Filtered analysis results",
            variable=self.data_type_var,
            value="filtered",
        ).pack(side='left', padx=(10, 0))
        ttk.Radiobutton(
            src_row,
            text="All loaded data (CSV / scraper)",
            variable=self.data_type_var,
            value="all",
        ).pack(side='left', padx=(10, 0))

        list_row = ttk.Frame(upload_frame)
        list_row.pack(fill='x')
        ttk.Label(
            list_row,
            text="List routing: scraped uploads use MAKAN / competitor list names from config; manual list below is fallback.",
            font=('Arial', 9),
            foreground='#555',
        ).pack(anchor='w')

        cfg_row = ttk.Frame(upload_frame)
        cfg_row.pack(fill='x', pady=(8, 0))
        ttk.Button(
            cfg_row,
            text="Load ClickUp settings from config",
            command=self.load_clickup_from_config,
        ).pack(side='left')

        # Upload buttons
        button_frame = ttk.Frame(clickup_frame)
        button_frame.pack(fill='x', padx=20, pady=(10, 0))

        ttk.Button(button_frame, text="Upload to ClickUp",
                command=self.upload_to_clickup, style='Custom.TButton').pack(side='left')
        ttk.Button(button_frame, text="Save as CSV",
                command=self.save_data_choice, style='Custom.TButton').pack(side='left', padx=(10, 0))
        ttk.Button(button_frame, text="Test Connection",
                command=self.test_clickup_connection, style='Custom.TButton').pack(side='left')

        self._prefill_clickup_token_from_config()

        # Results frame
        results_frame = ttk.LabelFrame(clickup_frame, text="ClickUp Integration Results", padding="15")
        results_frame.pack(fill='both', expand=True, padx=20, pady=10)

        # Results summary
        self.clickup_status_label = ttk.Label(results_frame, text="ClickUp Integration Status",
                                     style='Header.TLabel')
        self.clickup_status_label.pack(pady=(0, 10))

        # Results text display
        self.clickup_status_text = scrolledtext.ScrolledText(results_frame, height=10, width=80,
                                                      font=('Arial', 9), wrap=tk.WORD)
        self.clickup_status_text.pack(fill='both', expand=True, pady=(0, 10))

        # Export button frame
        export_frame = ttk.Frame(results_frame)
        export_frame.pack(fill='x')

    def start_scraping(self):
        """Start the scraping process in a separate thread"""
        if not SCRAPER_AVAILABLE:
            messagebox.showerror("Error", "Scraper functions not available. Make sure google_maps_scraper.py is in the same directory.")
            return

        locations = self._collect_scrape_locations()
        if not locations:
            return

        group_label = "MAKAN" if self.location_source_var.get() == "makan" else "Competitor"
        scope_map = {"one": "one location", "many": "multiple locations", "all": "all locations in group"}
        scope_label = scope_map.get(self.location_scope_var.get(), self.location_scope_var.get())
        mode = self.scrape_mode_var.get()
        if mode == "date_window":
            mode_label = f"date range: {self.date_window_var.get()}"
        else:
            mode_label = f"review count: {self.num_reviews_var.get()}"
        self.last_scrape_locations = list(locations)
        self.last_scrape_meta = {
            "group": self.location_source_var.get(),
            "group_label": group_label,
            "scope_label": scope_label,
            "mode": mode,
            "mode_label": mode_label,
        }
        self._update_clickup_scrape_summary()
        num_reviews = None
        days_back = None
        if mode == "count":
            try:
                num_reviews = int(self.num_reviews_var.get())
            except ValueError:
                messagebox.showerror("Error", "Please enter a valid number of reviews")
                return
        else:
            label = (self.date_window_var.get() or "").strip()
            try:
                days_back = date_window_choice_to_days(label)
            except KeyError:
                messagebox.showerror("Error", "Choose a valid date range from the list.")
                return

        # Disable button and start progress
        self.scrape_button.config(state='disabled')
        self.progress_bar.start()
        self.progress_var.set("Scraping reviews...")

        # Start scraping in separate thread
        scraping_thread = threading.Thread(
            target=self.scrape_worker,
            args=(locations, num_reviews, mode, days_back),
        )
        scraping_thread.daemon = True
        scraping_thread.start()

    def scrape_worker(self, locations, num_reviews, mode, days_back=None):
        """Worker: scrape one or more configured locations and merge processed reviews."""
        combined = []
        errors = []
        total = len(locations)
        try:
            if total <= 1:
                for idx, (loc_name, url) in enumerate(locations, start=1):
                    self.root.after(
                        0,
                        lambda i=idx, t=total, n=loc_name: self.progress_var.set(f"Scraping {n} ({i}/{t})..."),
                    )
                    res = _gui_scrape_one_location((loc_name, url, num_reviews, mode, days_back))
                    if res.get("error"):
                        errors.append(res["error"])
                    else:
                        combined.extend(res["reviews"])
            else:
                self.root.after(
                    0,
                    lambda t=total: self.progress_var.set(
                        f"Scraping {t} locations — 2 parallel browser windows (tabs)…"
                    ),
                )
                payloads = [(name, url, num_reviews, mode, days_back) for name, url in locations]
                results_by_name = {}
                completed = 0
                with ThreadPoolExecutor(max_workers=2) as executor:
                    future_to_name = {
                        executor.submit(_gui_scrape_one_location, p): p[0] for p in payloads
                    }
                    for fut in as_completed(future_to_name):
                        loc_key = future_to_name[fut]
                        completed += 1
                        c_done, c_total = completed, total
                        try:
                            res = fut.result()
                        except Exception as e:
                            errors.append(f"{loc_key}: {e}")
                            self.root.after(
                                0,
                                lambda d=c_done, n=c_total: self.progress_var.set(
                                    f"Parallel scrape: {d}/{n} locations finished…"
                                ),
                            )
                            continue
                        if res.get("error"):
                            errors.append(res["error"])
                        results_by_name[res["name"]] = res.get("reviews") or []
                        self.root.after(
                            0,
                            lambda d=c_done, n=c_total: self.progress_var.set(
                                f"Parallel scrape: {d}/{n} locations finished (2 at a time)…"
                            ),
                        )
                for loc_name, _url in locations:
                    combined.extend(results_by_name.get(loc_name, []))

            if combined:
                self.all_reviews = combined
                self.root.after(0, self._update_clickup_scrape_summary)
                self.root.after(0, self.update_scraped_results)
                if errors:
                    msg = "\n".join(errors[:12])
                    if len(errors) > 12:
                        msg += f"\n… and {len(errors) - 12} more"
                    self.root.after(0, lambda m=msg: messagebox.showwarning("Partial result", m))
            else:
                err_txt = "\n".join(errors) if errors else "No reviews returned for any location."
                self.root.after(0, lambda t=err_txt: messagebox.showerror("Error", f"No reviews were scraped.\n{t}"))

        except Exception as e:
            self.root.after(0, lambda err=str(e): messagebox.showerror("Error", f"Scraping failed: {err}"))

        finally:
            self.root.after(0, self.scraping_finished)

    def scraping_finished(self):
        """Clean up after scraping"""
        self.scrape_button.config(state='normal')
        self.progress_bar.stop()
        self.progress_var.set("Scraping completed!")

    def update_scraped_results(self):
        """Update the scraped results display"""
        self.scraped_text.delete(1.0, tk.END)

        if self.all_reviews:
            result_text = f"Successfully scraped {len(self.all_reviews)} reviews:\n"
            result_text += "=" * 50 + "\n\n"

            for i, review in enumerate(self.all_reviews[:5], 1):  # Show first 5
                result_text += f"Review {i}:\n"
                if review.get("location_name"):
                    result_text += f"Location: {review.get('location_name')}\n"
                result_text += f"Name: {review.get('name', 'N/A')}\n"
                result_text += f"Date: {review.get('date', 'N/A')}\n"
                result_text += f"Rating: {review.get('rating', 'N/A')}\n"
                result_text += f"Text: {review.get('text', 'N/A')[:200]}{'...' if len(review.get('text', '')) > 200 else ''}\n"
                # Show link if available
                if 'link' in review and review.get('link'):
                    result_text += f"Link: {review.get('link')}\n"
                result_text += "-" * 50 + "\n\n"

            if len(self.all_reviews) > 5:
                result_text += f"... and {len(self.all_reviews) - 5} more reviews"

            self.scraped_text.insert(1.0, result_text)


    def use_for_analysis(self):
        """Use scraped reviews for analysis"""
        if not self.all_reviews:
            messagebox.showerror("Error", "No reviews available")
            return

        # Convert to DataFrame
        self.reviews_df = pd.DataFrame(self.all_reviews)
        self.file_label.config(text=f"{len(self.all_reviews)} reviews loaded from scraper", foreground='green')

        # Switch to analysis tab
        self.notebook.select(1)
        messagebox.showinfo("Success", f"Loaded {len(self.all_reviews)} reviews for analysis")

    def load_csv_file(self):
        """Load reviews from a CSV file for analysis"""
        filename = filedialog.askopenfilename(
            title="Select a CSV File",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )

        if not filename:
            # User canceled the dialog
            return

        try:
            self.reviews_df = pd.read_csv(filename)

            # Basic validation: check for a 'text' or 'date' column
            if 'text' not in self.reviews_df.columns or 'date' not in self.reviews_df.columns:
                 messagebox.showwarning("Warning", "The CSV file may not be in the correct format. Missing 'text' or 'date' columns.")

            num_reviews = len(self.reviews_df)
            self.file_label.config(text=f"{num_reviews} reviews loaded from {os.path.basename(filename)}", foreground='green')
            messagebox.showinfo("Success", f"Successfully loaded {num_reviews} reviews.")

            # Clear previous search results
            self.results_text.config(state='normal')
            self.results_text.delete(1.0, tk.END)
            self.results_text.insert(1.0, "CSV file loaded. Enter search criteria and click 'Search Reviews'.")
            self.results_text.config(state='disabled')
            self.results_label.config(text="Ready to search")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load or read CSV file:\n{e}")
            self.file_label.config(text="Failed to load file", foreground='red')



    def save_scraped_reviews(self):
        """Save scraped reviews to CSV"""
        if not self.all_reviews:
            messagebox.showerror("Error", "No reviews to save")
            return

        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Save Reviews As"
        )

        if filename:
            try:
                # Convert the list of dictionaries to a pandas DataFrame
                scraped_df = pd.DataFrame(self.all_reviews)

                # Save the DataFrame directly to a CSV file
                scraped_df.to_csv(filename, index=False, encoding='utf-8-sig')

                messagebox.showinfo("Success", f"Reviews saved to {filename}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save reviews: {str(e)}")

    def parse_date(self, date_str):
        """Parse date string to datetime object with proper debugging"""
        if not date_str or date_str == 'N/A':
            return None

        try:
            # Clean the date string
            original_date_str = date_str
            date_str = date_str.strip().lower()

            # Handle relative dates first
            if 'ago' in date_str:
                now = datetime.now()

                # Extract number and time unit with more specific patterns
                if 'minute' in date_str:
                    minutes = re.findall(r'(\d+)\s*minute', date_str)
                    if minutes:
                        result = now - timedelta(minutes=int(minutes[0]))
                        print(f"DEBUG: '{original_date_str}' -> {result} (parsed as {minutes[0]} minutes ago)")
                        return result

                elif 'hour' in date_str:
                    hours = re.findall(r'(\d+)\s*hour', date_str)
                    if hours:
                        result = now - timedelta(hours=int(hours[0]))
                        print(f"DEBUG: '{original_date_str}' -> {result} (parsed as {hours[0]} hours ago)")
                        return result

                elif 'day' in date_str and 'week' not in date_str:  # Make sure it's not "a week" which contains "a"
                    days = re.findall(r'(\d+)\s*day', date_str)
                    if days:
                        result = now - timedelta(days=int(days[0]))
                        print(f"DEBUG: '{original_date_str}' -> {result} (parsed as {days[0]} days ago)")
                        return result
                    elif 'a day ago' in date_str:
                        result = now - timedelta(days=1)
                        print(f"DEBUG: '{original_date_str}' -> {result} (parsed as 1 day ago)")
                        return result

                elif 'week' in date_str:
                    weeks = re.findall(r'(\d+)\s*week', date_str)
                    if weeks:
                        result = now - timedelta(weeks=int(weeks[0]))  # Use weeks parameter
                        print(f"DEBUG: '{original_date_str}' -> {result} (parsed as {weeks[0]} weeks ago)")
                        return result
                    elif 'a week ago' in date_str:
                        result = now - timedelta(weeks=1)  # Use weeks parameter
                        print(f"DEBUG: '{original_date_str}' -> {result} (parsed as 1 week ago)")
                        return result

                elif 'month' in date_str:
                    months = re.findall(r'(\d+)\s*month', date_str)
                    if months:
                        result = now - timedelta(days=int(months[0])*30)  # Approximate months as 30 days
                        print(f"DEBUG: '{original_date_str}' -> {result} (parsed as {months[0]} months ago)")
                        return result
                    elif 'a month ago' in date_str:
                        result = now - timedelta(days=30)
                        print(f"DEBUG: '{original_date_str}' -> {result} (parsed as 1 month ago)")
                        return result

            # Try standard date formats
            date_formats = [
                '%Y-%m-%d',
                '%d/%m/%Y',
                '%m/%d/%Y',
                '%d-%m-%Y',
                '%B %d, %Y',
                '%b %d, %Y',
            ]

            for fmt in date_formats:
                try:
                    result = datetime.strptime(date_str.strip(), fmt)
                    print(f"DEBUG: '{original_date_str}' -> {result} (parsed with format {fmt})")
                    return result
                except ValueError:
                    continue

            print(f"DEBUG: Could not parse date: '{original_date_str}'")
            return None

        except Exception as e:
            print(f"DEBUG: Error parsing date '{date_str}': {e}")
            return None

    def search_reviews(self):
        """Search reviews based on keyword and time period with proper date filtering"""
        if self.reviews_df is None:
            messagebox.showerror("Error", "Please load a CSV file first")
            return

        keyword_input = self.keyword_entry.get().strip()
        keywords = [k.strip() for k in keyword_input.split(',') if k.strip()]

        # If no keywords are provided, do not apply keyword filtering
        if not keywords and keyword_input:
            messagebox.showerror("Error", "Please enter a keyword to search, or leave blank to skip keyword filtering.")
            return

        try:
            days = int(self.days_var.get())
            max_results = int(self.max_results_var.get())
            language_filter = self.language_var.get()
        except ValueError:
            messagebox.showerror("Error", "Please enter valid numbers for days and max results")
            return

        # Show debug info
        print(f"DEBUG: Starting search for {keywords}")
        print(f"DEBUG: Dataset has {len(self.reviews_df)} total reviews")
        print(f"DEBUG: Looking for reviews from last {days} days")

        # Calculate cutoff date
        cutoff_date = datetime.now() - timedelta(days=days)
        print(f"DEBUG: Cutoff date: {cutoff_date.strftime('%Y-%m-%d %H:%M:%S')}")

        filtered_df = self.reviews_df.copy()

        # Step 1: Filter by time period
        date_filtered = []
        skipped_unparseable = 0
        skipped_too_old = 0

        for index, row in filtered_df.iterrows():
            date_str = row['date']
            review_date = self.parse_date(date_str)

            if review_date is None:
                # Skip reviews with unparseable dates
                skipped_unparseable += 1
                print(f"DEBUG: Skipped unparseable date: '{date_str}'")
                continue

            # Check if review is within time period
            if review_date >= cutoff_date:
                date_filtered.append(row)
                print(f"DEBUG: INCLUDED - Date: '{date_str}' -> {review_date.strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                skipped_too_old += 1
                print(f"DEBUG: EXCLUDED (too old) - Date: '{date_str}' -> {review_date.strftime('%Y-%m-%d %H:%M:%S')}")

        print(f"DEBUG: Date filtering results:")
        print(f"  - Total reviews: {len(filtered_df)}")
        print(f"  - After date filter: {len(date_filtered)}")
        print(f"  - Skipped (unparseable dates): {skipped_unparseable}")
        print(f"  - Skipped (too old): {skipped_too_old}")

        if date_filtered:
            date_filtered_df = pd.DataFrame(date_filtered)
        else:
            date_filtered_df = pd.DataFrame(columns=filtered_df.columns)
            print("DEBUG: No reviews found within the specified time period!")

        # Apply keyword filtering if keywords are provided
        if keywords:
            # Combine multiple keyword searches with OR logic
            keyword_condition = False
            for kw in keywords:
                kw_lower = kw.lower()
                # Apply keyword filter to date_filtered_df, not the original filtered_df
                keyword_condition |= (date_filtered_df['text'].str.lower().str.contains(kw_lower, na=False) |
                                      date_filtered_df['name'].str.lower().str.contains(kw_lower, na=False))
            filtered_df = date_filtered_df[keyword_condition] # Update filtered_df here
            print(f"DEBUG: After keyword filter ({keywords}), {len(filtered_df)} reviews remain")
        else:
            # If no keywords, proceed with date_filtered_df
            filtered_df = date_filtered_df.copy()

        # Step 3: Filter by language
        if language_filter != "all":
            language_filtered = []
            for review in filtered_df.to_dict('records'):
                text = str(review.get('text', ''))
                detected_lang = detect_review_language(text)

                if ((language_filter == "english" and detected_lang == "english") or
                    (language_filter == "arabic" and detected_lang == "arabic") or
                    (language_filter == "mixed" and detected_lang == "mixed")):
                    language_filtered.append(review)

            filtered_df = pd.DataFrame(language_filtered)
            print(f"DEBUG: After language filter ({language_filter}): {len(filtered_df)} reviews")

        # Step 4: Limit results
        if len(filtered_df) > max_results:
            self.filtered_reviews = filtered_df[:max_results].to_dict('records')
        else:
            self.filtered_reviews = filtered_df.to_dict('records')

        # Display results
        self.display_search_results(keyword_input, days, language_filter)

        print(f"DEBUG: Final results: {len(self.filtered_reviews)} reviews found")

        # Force UI update
        self.root.update_idletasks()

    def display_search_results(self, keyword_input, days, language_filter):
        """Display search results in the text widget"""
        # Check if results_text widget exists
        if not hasattr(self, 'results_text'):
            print("ERROR: results_text widget not found!")
            messagebox.showerror("Error", "Results display area not found. Please restart the application.")
            return

        self.results_text.delete(1.0, tk.END)

        # Create language filter description
        lang_desc = {
            "all": "all languages",
            "english": "English only",
            "arabic": "Arabic only",
            "mixed": "mixed content"
        }.get(language_filter, language_filter)

        if not self.filtered_reviews:
            result_text = f"No reviews found containing '{keyword_input}' in {lang_desc} from the last {days} days.\n\n"
            result_text += f"DEBUG INFO:\n"
            result_text += f"- Total reviews in dataset: {len(self.reviews_df)}\n"
            result_text += f"- Search keyword: '{keyword_input}'\n"
            result_text += f"- Time filter: {days} days\n"
            result_text += f"- Language filter: {lang_desc}\n"
            result_text += f"- Number of reviews after all filters: 0\n\n"

            self.results_label.config(text="No results found")
        else:
            result_text = f"Found {len(self.filtered_reviews)} reviews containing '{keyword_input}' in {lang_desc} from the last {days} days:\n"
            result_text += "=" * 80 + "\n\n"

            for i, review in enumerate(self.filtered_reviews, 1):
                review_text = review.get('text', 'N/A') # Changed from 'text' to 'review'
                title_text = review.get('name', 'N/A') # Changed from 'name' to 'title'
                detected_lang = detect_review_language(review_text)

                result_text += f"Review {i}: [{detected_lang.upper()}]\n"
                result_text += f"Name: {title_text}\n" # Changed from 'Name' to 'Title'
                result_text += f"Date: {review.get('date', 'N/A')}\n"
                result_text += f"Rating: {review.get('rating', 'N/A')}\n"
                result_text += f"Review: {review_text}\n"
                result_text += "\n" # Add an extra newline for better readability between reviews

            self.results_label.config(text=f"{len(self.filtered_reviews)} results found")

        self.results_text.config(state='normal') # Ensure the widget is editable
        self.results_text.delete(1.0, tk.END)
        self.results_text.insert(1.0, result_text)
        print(f"DEBUG: Inserted {len(result_text)} characters into results_text")

        # Force update of the text widget
        self.results_text.config(state='disabled')

    def export_filtered_results(self):
        """Export filtered results to CSV"""
        if not self.filtered_reviews:
            messagebox.showerror("Error", "No filtered results to export")
            return

        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Export Filtered Results As"
        )

        if filename:
            try:
                filtered_df = pd.DataFrame(self.filtered_reviews)
                filtered_df.to_csv(filename, index=False, encoding='utf-8-sig')
                messagebox.showinfo("Success", f"Filtered results exported to {filename}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to export results: {str(e)}")

    def test_clickup_connection(self):
        """Test ClickUp API connection"""
        token = self.clickup_token_entry.get().strip()
        if not token:
            messagebox.showerror("Error", "Please enter your ClickUp API token")
            return

        try:
            headers = {"Authorization": token}
            response = requests.get("https://api.clickup.com/api/v2/user", headers=headers, timeout=10)

            if response.status_code == 200:
                user_data = response.json()
                username = user_data.get('user', {}).get('username', 'Unknown')
                messagebox.showinfo("Success", f"Connected to ClickUp!\nUser: {username}")
                self.clickup_status_label.config(text=f"Connected as: {username}", foreground='green')
            else:
                messagebox.showerror("Error", f"Failed to connect to ClickUp.\nStatus: {response.status_code}")
                self.clickup_status_label.config(text=f"Connection failed: {response.status_code}", foreground='red')

        except requests.exceptions.Timeout:
            messagebox.showerror("Error", "Connection timed out. Please check your internet connection or try again later.")
            self.clickup_status_label.config(text="Connection timed out", foreground='red')
        except Exception as e:
            messagebox.showerror("Error", f"Connection failed: {str(e)}")
            self.clickup_status_label.config(text=f"Connection failed: {str(e)}", foreground='red')

    def _mall_name_for_review(self, review, fallback=None):
        """Return the mall/location name stored on a review during scraping."""
        for key in ("location_name", "mall_name", "Location", "location"):
            val = str(review.get(key) or "").strip()
            if val and val.lower() not in ("n/a", "unknown", "unknown location"):
                return val
        if fallback:
            return fallback
        if len(self.last_scrape_locations) == 1:
            return self.last_scrape_locations[0][0]
        return ""

    def _reviews_have_mall_names(self, reviews):
        return any(self._mall_name_for_review(r) for r in reviews)

    def _ensure_clickup_lists_resolved(self, token: str) -> bool:
        """Resolve MAKAN/competitor list IDs from config when not loaded yet."""
        if self.makan_group_list_id or self.competitor_group_list_id:
            return True
        if not BATCH_MODULE_AVAILABLE or not resolve_clickup_list_id_by_name:
            return False

        self._load_batch_locations_config()
        workspace_name = str(self.clickup_config.get("workspace_name", "")).strip()
        space_name = str(self.clickup_config.get("space_name", "")).strip()
        makan_list_name = str(self.clickup_config.get("makan_list_name", "")).strip()
        competitor_list_name = str(self.clickup_config.get("competitor_list_name", "")).strip()
        if not workspace_name or not space_name:
            return False

        try:
            if makan_list_name:
                self.makan_group_list_id = resolve_clickup_list_id_by_name(
                    token, workspace_name, space_name, makan_list_name
                )
                self.list_data[makan_list_name] = self.makan_group_list_id
            if competitor_list_name:
                self.competitor_group_list_id = resolve_clickup_list_id_by_name(
                    token, workspace_name, space_name, competitor_list_name
                )
                self.list_data[competitor_list_name] = self.competitor_group_list_id
            list_names = list(self.list_data.keys())
            if list_names and getattr(self, "list_combo", None):
                self.list_combo["values"] = list_names
                if not self.list_var.get().strip():
                    self.list_var.set(list_names[0])
            return bool(self.makan_group_list_id or self.competitor_group_list_id)
        except Exception as e:
            print(f"Warning: could not auto-resolve ClickUp lists: {e}")
            return False

    def _resolve_list_id_for_location(self, loc_name: str, token: str):
        """Pick ClickUp list id for a location using config (per-loc → group → default → manual)."""
        self._ensure_clickup_lists_resolved(token)

        loc = self._location_record_by_name(loc_name)
        if loc and loc.get("clickup_list_id"):
            return loc["clickup_list_id"], "per-location config"

        group = self._location_group_for_name(loc_name)
        if group == "competitor" and self.competitor_group_list_id:
            return self.competitor_group_list_id, "competitor list (config)"
        if group == "makan" and self.makan_group_list_id:
            return self.makan_group_list_id, "MAKAN list (config)"

        default_list_id = str(self.clickup_config.get("default_list_id", "")).strip() or None
        if default_list_id:
            return default_list_id, "default_list_id (config)"

        list_name = self.list_var.get().strip()
        if list_name and list_name in self.list_data:
            return self.list_data[list_name], f"manual list ({list_name})"

        return None, None

    def _validate_clickup_token(self, token: str):
        headers = {"Authorization": token}
        response = requests.get("https://api.clickup.com/api/v2/user", headers=headers, timeout=15)
        if response.status_code == 200:
            return True, response.json().get("user", {}).get("username", "Unknown")
        err = response.json().get("err", response.text[:120]) if response.text else response.status_code
        return False, str(err)

    def _build_clickup_task_payload(self, mall_name: str, review: dict, review_index: int, status: str):
        if BATCH_MODULE_AVAILABLE and LocationConfig is not None:
            location = LocationConfig(name=mall_name, url="")
            return build_task_payload(location, review, status, review_index)

        description = (
            f"Review from {mall_name}\n\n"
            f"Name: {review.get('name', 'N/A')}\n"
            f"Date: {review.get('date', 'N/A')}\n"
            f"Rating: {review.get('rating', 'N/A')}\n"
            f"Review: {review.get('text', 'N/A')}\n"
        )
        review_link = review.get("link") or review.get("review_link") or review.get("url")
        if review_link and str(review_link).strip() not in ("", "N/A"):
            description += f"Link: {review_link}\n"
        return {
            "name": f"{mall_name} - Review {review_index}"[:255],
            "description": description,
            "status": status,
            "priority": get_priority_from_rating(str(review.get("rating", "N/A"))),
            "tags": [],
        }

    def _upload_reviews_with_mall_names(
        self,
        token: str,
        reviews: list,
        status: str,
        fallback_mall_name: str = "",
        fixed_list_id: str = None,
        progress_bar=None,
        progress_label=None,
        progress_window=None,
    ):
        """Upload each review using its own scraped mall name; numbering restarts per mall."""
        headers = {"Authorization": token, "Content-Type": "application/json"}
        mall_counters = {}
        success = 0
        failed = 0
        resolved_lists = {}

        for review in reviews:
            mall_name = self._mall_name_for_review(review, fallback=fallback_mall_name)
            if not mall_name:
                failed += 1
                self.clickup_status_text.insert(
                    tk.END,
                    "Skipped review: no mall name (re-scrape with location selected).\n",
                )
                continue

            if fixed_list_id:
                list_id, route = fixed_list_id, "manual list"
            else:
                if mall_name not in resolved_lists:
                    resolved_lists[mall_name] = self._resolve_list_id_for_location(mall_name, token)
                list_id, route = resolved_lists[mall_name]
                if not list_id:
                    raise RuntimeError(
                        f"No ClickUp list for '{mall_name}'. "
                        "Click 'Load ClickUp settings from config' or pick a list."
                    )

            mall_counters[mall_name] = mall_counters.get(mall_name, 0) + 1
            task_data = self._build_clickup_task_payload(
                mall_name, review, mall_counters[mall_name], status
            )
            response = requests.post(
                f"https://api.clickup.com/api/v2/list/{list_id}/task",
                headers=headers,
                json=task_data,
                timeout=20,
            )
            if response.status_code == 200:
                success += 1
                self.clickup_status_text.insert(tk.END, f"Uploaded: {task_data['name']}\n")
            else:
                failed += 1
                err = ""
                try:
                    err = response.json().get("err", "")
                except Exception:
                    err = response.text[:80]
                self.clickup_status_text.insert(
                    tk.END,
                    f"Failed: {task_data['name']} ({response.status_code}{': ' + err if err else ''})\n",
                )
                if response.status_code == 401:
                    raise RuntimeError(
                        "ClickUp token is invalid. Update the API token in the ClickUp tab "
                        "or in last_7_days_batch_config.json, then try again."
                    )

            if progress_bar is not None:
                progress_bar["value"] = progress_bar["value"] + 1
                progress_label.config(
                    text=f"Uploading… ({int(progress_bar['value'])}/{int(progress_bar['maximum'])})"
                )
                progress_window.update()

        return success, failed

    def upload_to_clickup(self):
        """Upload reviews to ClickUp — each task uses the mall name from scraping."""
        token = self.clickup_token_entry.get().strip()
        if not token or token == "PASTE_CLICKUP_TOKEN_HERE":
            messagebox.showerror("Error", "Please enter a valid ClickUp API token")
            return

        ok, auth_info = self._validate_clickup_token(token)
        if not ok:
            messagebox.showerror(
                "Invalid ClickUp token",
                f"ClickUp rejected the API token ({auth_info}).\n\n"
                "Paste a valid personal API token in the ClickUp tab, or update "
                "clickup.api_token in last_7_days_batch_config.json.",
            )
            self.clickup_status_label.config(text=f"Token invalid: {auth_info}", foreground="red")
            return

        data_type = self.data_type_var.get()
        clickup_status = str(self.clickup_config.get("status", "to do")).strip() or "to do"
        fallback_mall_name = ""
        fixed_list_id = None

        if data_type == "scraped":
            if not self.all_reviews:
                messagebox.showerror("Error", "No scraped reviews to upload. Run Scrape Reviews first.")
                return
            reviews = list(self.all_reviews)
            data_name = "Last scraped reviews (per mall)"
        elif data_type == "filtered":
            if not self.filtered_reviews:
                messagebox.showerror("Error", "No filtered reviews to upload. Please search first.")
                return
            reviews = list(self.filtered_reviews)
            data_name = "Filtered analysis results"
            if not self._reviews_have_mall_names(reviews):
                list_name = self.list_var.get().strip()
                if not list_name or list_name not in self.list_data:
                    messagebox.showerror("Error", "Select a ClickUp list for filtered uploads.")
                    return
                fixed_list_id = self.list_data[list_name]
                fallback_mall_name = simpledialog.askstring(
                    "Mall name",
                    "Enter the mall name for these filtered reviews:",
                    parent=self.root,
                ) or ""
                if not fallback_mall_name:
                    messagebox.showerror("Error", "Mall name is required when reviews have no location.")
                    return
        else:
            if self.reviews_df is None or len(self.reviews_df) == 0:
                messagebox.showerror("Error", "No data to upload. Load a CSV or scrape reviews first.")
                return
            reviews = self.reviews_df.to_dict("records")
            data_name = "All loaded data"
            if not self._reviews_have_mall_names(reviews):
                list_name = self.list_var.get().strip()
                if not list_name or list_name not in self.list_data:
                    messagebox.showerror("Error", "Select a ClickUp list for this upload.")
                    return
                fixed_list_id = self.list_data[list_name]
                fallback_mall_name = simpledialog.askstring(
                    "Mall name",
                    "Enter the mall name for these reviews:",
                    parent=self.root,
                ) or ""
                if not fallback_mall_name:
                    messagebox.showerror("Error", "Mall name is required when reviews have no location.")
                    return

        total_reviews = len(reviews)
        if total_reviews == 0:
            messagebox.showerror("Error", "No reviews to upload.")
            return

        self._ensure_clickup_lists_resolved(token)

        progress_window = None
        try:
            progress_window = tk.Toplevel(self.root)
            progress_window.title("Uploading to ClickUp")
            progress_window.geometry("420x110")
            progress_window.transient(self.root)
            progress_window.grab_set()

            progress_label = ttk.Label(progress_window, text=f"Uploading {data_name}...")
            progress_label.pack(pady=10)
            progress_bar = ttk.Progressbar(
                progress_window, length=320, mode="determinate", maximum=max(1, total_reviews)
            )
            progress_bar.pack(pady=10)

            self.clickup_status_label.config(
                text=f"Uploading {data_name} as {auth_info}…", foreground="blue"
            )
            self.clickup_status_text.delete(1.0, tk.END)
            self.clickup_status_text.insert(
                1.0,
                f"Starting upload of {total_reviews} review(s) — each task named after its mall…\n",
            )
            self.clickup_status_text.insert(tk.END, "=" * 50 + "\n")

            successful_uploads, failed_uploads = self._upload_reviews_with_mall_names(
                token=token,
                reviews=reviews,
                status=clickup_status,
                fallback_mall_name=fallback_mall_name,
                fixed_list_id=fixed_list_id,
                progress_bar=progress_bar,
                progress_label=progress_label,
                progress_window=progress_window,
            )

            progress_window.destroy()
            progress_window = None

            if successful_uploads == total_reviews:
                messagebox.showinfo("Success", f"Successfully uploaded {successful_uploads} reviews to ClickUp!")
                self.clickup_status_label.config(
                    text=f"Upload complete: {successful_uploads} reviews", foreground="green"
                )
            elif successful_uploads > 0:
                messagebox.showwarning(
                    "Partial Success",
                    f"Uploaded {successful_uploads} out of {total_reviews} reviews "
                    f"({failed_uploads} failed).",
                )
                self.clickup_status_label.config(
                    text=f"Upload partial: {successful_uploads}/{total_reviews}", foreground="orange"
                )
            else:
                messagebox.showerror("Upload failed", "No reviews were uploaded. See the log for details.")
                self.clickup_status_label.config(text="Upload failed", foreground="red")

            self.clickup_status_text.insert(tk.END, "=" * 50 + "\n")
            self.clickup_status_text.insert(
                tk.END,
                f"Final summary: {successful_uploads}/{total_reviews} uploaded, {failed_uploads} failed.\n",
            )

        except requests.exceptions.Timeout:
            if progress_window is not None:
                progress_window.destroy()
            messagebox.showerror("Error", "Upload timed out. Check your connection and try again.")
            self.clickup_status_label.config(text="Upload timed out", foreground="red")
        except Exception as e:
            if progress_window is not None:
                progress_window.destroy()
            messagebox.showerror("Error", f"Failed to upload to ClickUp: {str(e)}")
            self.clickup_status_label.config(text=f"Upload error: {str(e)}", foreground="red")

    def save_data_choice(self):
        """Save scraped, filtered, or all data based on user choice"""
        data_type = self.data_type_var.get()

        if data_type == "scraped":
            self.save_scraped_reviews()
        elif data_type == "filtered":
            if not self.filtered_reviews:
                messagebox.showerror("Error", "No filtered reviews to save")
                return
            self.export_filtered_results()
        else:  # all data
            if self.reviews_df is None or len(self.reviews_df) == 0:
                messagebox.showerror("Error", "No review data to save")
                return

            filename = filedialog.asksaveasfilename(
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                title="Save All Reviews As"
            )

            if filename:
                try:
                    self.reviews_df.to_csv(filename, index=False, encoding='utf-8-sig')
                    messagebox.showinfo("Success", f"All reviews saved to {filename}")
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to save reviews: {str(e)}")

    def get_clickup_headers(self):
        """Get headers for ClickUp API requests"""
        token = self.clickup_token_entry.get().strip()
        if not token:
            messagebox.showerror("Error", "Please enter your ClickUp API token")
            return None

        return {
            "Authorization": token,
            "Content-Type": "application/json"
        }

    def load_clickup_workspaces(self):
        """Load ClickUp workspaces"""
        headers = self.get_clickup_headers()
        if not headers:
            return

        try:
            response = requests.get("https://api.clickup.com/api/v2/team", headers=headers)

            if response.status_code == 200:
                teams = response.json()["teams"]
                workspace_names = [(team["name"], team["id"]) for team in teams]

                self.workspace_combo["values"] = [name for name, _ in workspace_names]
                self.workspace_data = {name: team_id for name, team_id in workspace_names}

                messagebox.showinfo("Success", f"Loaded {len(workspace_names)} workspaces")
            else:
                messagebox.showerror("Error", f"Failed to load workspaces: {response.text}")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to connect to ClickUp: {str(e)}")

    def load_clickup_spaces(self):
        """Load ClickUp spaces for selected workspace"""
        headers = self.get_clickup_headers()
        if not headers:
            return

        workspace_name = self.workspace_var.get()
        if not workspace_name or workspace_name not in self.workspace_data:
            messagebox.showerror("Error", "Please select a workspace first")
            return

        team_id = self.workspace_data[workspace_name]

        try:
            response = requests.get(f"https://api.clickup.com/api/v2/team/{team_id}/space",
                                headers=headers)

            if response.status_code == 200:
                spaces = response.json()["spaces"]
                space_names = [(space["name"], space["id"]) for space in spaces]

                self.space_combo["values"] = [name for name, _ in space_names]
                self.space_data = {name: space_id for name, space_id in space_names}

                messagebox.showinfo("Success", f"Loaded {len(space_names)} spaces")
            else:
                messagebox.showerror("Error", f"Failed to load spaces: {response.text}")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load spaces: {str(e)}")

    def load_clickup_lists(self):
        """Load ClickUp lists for selected space"""
        headers = self.get_clickup_headers()
        if not headers:
            return

        space_name = self.space_var.get()
        if not space_name or space_name not in self.space_data:
            messagebox.showerror("Error", "Please select a space first")
            return

        space_id = self.space_data[space_name]

        try:
            response = requests.get(f"https://api.clickup.com/api/v2/space/{space_id}/list",
                                headers=headers)

            if response.status_code == 200:
                lists = response.json()["lists"]
                list_names = [(lst["name"], lst["id"]) for lst in lists]

                self.list_combo["values"] = [name for name, _ in list_names]
                self.list_data = {name: list_id for name, list_id in list_names}

                messagebox.showinfo("Success", f"Loaded {len(list_names)} lists")
            else:
                messagebox.showerror("Error", f"Failed to load lists: {response.text}")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load lists: {str(e)}")

def main():
    root = tk.Tk()
    app = ReviewAnalyzerGUI(root)

    # Center window on screen
    root.update_idletasks()
    width = root.winfo_width()
    height = root.winfo_height()
    x = (root.winfo_screenwidth() // 2) - (width // 2)
    y = (root.winfo_screenheight() // 2) - (height // 2)
    root.geometry(f'{width}x{height}+{x}+{y}')

    root.mainloop()

if __name__ == "__main__":
    main()