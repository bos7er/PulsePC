"""Interfejs graficzny PulsePC: Tkinter + psutil, rejestr tylko HKCU Run."""

from __future__ import annotations

import datetime as dt
import json
import os
import queue
import sys
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .registry import RunEntry, disable_user_run, list_user_run, restore_user_run
from .core import (
    MIN_FILE_AGE_DAYS,
    ScanResult,
    bytes_human,
    clean_temp,
    recommendations,
    report_payload,
    running_processes,
    scan_temp,
    snapshot,
    startup_entries,
    user_temp_dir,
)

BG = "#0B1020"
SIDEBAR = "#0F172A"
CARD = "#172239"
CARD_SECONDARY = "#1B2A42"
LINE = "#263651"
TEXT = "#F1F5FF"
MUTED = "#9BACBF"
ACCENT = "#5A9BFF"
GREEN = "#36D6A4"
ORANGE = "#FFBE76"


class PulsePC:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(f"PulsePC — Optymalizator Windows {__version__}")
        self.root.geometry("1150x755")
        self.root.minsize(1000, 680)
        self.root.configure(bg=BG)
        # Ikona dostępna także po spakowaniu przez PyInstaller (onefile).
        try:
            resource_base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
            self.root.iconbitmap(str(resource_base / "assets" / "pulsepc.ico"))
        except (OSError, tk.TclError):
            pass
        try:
            # Lepsze skalowanie na Windows bez zależności zewnętrznych.
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError, ImportError):
            pass

        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pulsepc")
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy: set[str] = set()
        self.live = True
        self.active_page = "panel"
        self.data: dict | None = None
        self.processes: list[dict] = []
        self.startups: list[dict] = []
        self.reg_entries: list[RunEntry] = []
        self.scan: ScanResult | None = None
        self.root.protocol("WM_DELETE_WINDOW", self._close)

        self._style_tables()
        self._build_shell()
        self.navigate("panel")
        self.root.after(120, self._read_messages)
        self.root.after(10000, self._refresh_periodically)

    def run(self) -> None:
        self.root.mainloop()

    def _style_tables(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Pulse.Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                        rowheight=32, borderwidth=0, font=("Segoe UI", 10))
        style.map("Pulse.Treeview", background=[("selected", "#2B5184")],
                  foreground=[("selected", "#FFFFFF")])
        style.configure("Pulse.Treeview.Heading", background=CARD_SECONDARY, foreground="#CFE0F8",
                        font=("Segoe UI Semibold", 10), padding=(10, 10), relief="flat")
        style.map("Pulse.Treeview.Heading", background=[("active", "#314566")])
        style.configure("Pulse.Horizontal.TProgressbar", troughcolor=LINE, background=ACCENT,
                        bordercolor=LINE, lightcolor=ACCENT, darkcolor=ACCENT, thickness=9)
        style.configure("Pulse.Vertical.TScrollbar", background=LINE, troughcolor=SIDEBAR,
                        borderwidth=0, arrowsize=13)

    def _build_shell(self) -> None:
        self.sidebar = tk.Frame(self.root, bg=SIDEBAR, width=236)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self.main = tk.Frame(self.root, bg=BG)
        self.main.pack(side="left", fill="both", expand=True)

        tk.Label(self.sidebar, text="◉", fg=ACCENT, bg=SIDEBAR,
                 font=("Segoe UI", 30)).pack(anchor="w", padx=25, pady=(26, 0))
        tk.Label(self.sidebar, text="PulsePC", fg=TEXT, bg=SIDEBAR,
                 font=("Segoe UI Semibold", 24)).pack(anchor="w", padx=25)
        tk.Label(self.sidebar, text="SYSTEM DASHBOARD", fg=MUTED, bg=SIDEBAR,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=26, pady=(1, 34))
        self.nav_buttons: dict[str, tk.Button] = {}
        for page, label in (
            ("panel", "▦    Panel główny"),
            ("procesy", "☷    Procesy"),
            ("czyszczenie", "◇    Czyszczenie"),
            ("autostart", "↗    Autostart"),
            ("rejestr", "▤    Rejestr Windows"),
            ("porady", "✦    Rekomendacje"),
        ):
            button = tk.Button(self.sidebar, text=label, command=lambda p=page: self.navigate(p),
                               bg=SIDEBAR, fg="#BECDE0", activebackground=CARD_SECONDARY,
                               activeforeground=TEXT, bd=0, relief="flat", anchor="w",
                               padx=23, pady=13, font=("Segoe UI", 11), cursor="hand2")
            button.pack(fill="x", padx=12, pady=3)
            self.nav_buttons[page] = button
        tk.Frame(self.sidebar, bg=LINE, height=1).pack(fill="x", padx=22, pady=(30, 19))
        tk.Label(self.sidebar, text="KONTROLOWANE ZMIANY", fg=GREEN, bg=SIDEBAR,
                 font=("Segoe UI Semibold", 10)).pack(anchor="w", padx=25)
        tk.Label(self.sidebar, text="Tylko HKCU Run\nKopia przed każdą zmianą.",
                 fg=MUTED, bg=SIDEBAR, justify="left", font=("Segoe UI", 9)).pack(
                     anchor="w", padx=25, pady=(6, 0))
        tk.Label(self.sidebar, text=f"v{__version__}", fg="#667890", bg=SIDEBAR,
                 font=("Segoe UI", 9)).pack(side="bottom", anchor="w", padx=26, pady=20)

        top = tk.Frame(self.main, bg=BG)
        top.pack(fill="x", padx=30, pady=(22, 10))
        self.title = tk.Label(top, text="", bg=BG, fg=TEXT, font=("Segoe UI Semibold", 25))
        self.title.pack(side="left")
        self.refresh_button = self._button(top, "↻ Odśwież", self.refresh_current, primary=True)
        self.refresh_button.pack(side="right", pady=5)
        self.subtitle = tk.Label(self.main, text="", bg=BG, fg=MUTED,
                                 font=("Segoe UI", 10), anchor="w")
        self.subtitle.pack(fill="x", padx=31)
        self.content = tk.Frame(self.main, bg=BG)
        self.content.pack(fill="both", expand=True, padx=29, pady=(20, 8))
        footer = tk.Frame(self.main, bg=BG)
        footer.pack(fill="x", padx=30, pady=(0, 17))
        tk.Frame(footer, bg=LINE, height=1).pack(fill="x", pady=(0, 9))
        self.status = tk.Label(footer, text="Gotowy", bg=BG, fg=MUTED,
                               font=("Segoe UI", 9), anchor="w")
        self.status.pack(fill="x")

    def _button(self, parent: tk.Widget, text: str, command, primary: bool = False) -> tk.Button:
        return tk.Button(parent, text=text, command=command,
                         bg=ACCENT if primary else CARD_SECONDARY,
                         fg="#081426" if primary else TEXT,
                         activebackground="#8AB8FF" if primary else "#314566",
                         activeforeground="#071020" if primary else TEXT,
                         bd=0, relief="flat", padx=17, pady=10,
                         font=("Segoe UI Semibold", 10), cursor="hand2")

    def _card(self, parent: tk.Widget, padx: int = 18, pady: int = 16) -> tk.Frame:
        panel = tk.Frame(parent, bg=CARD, padx=padx, pady=pady, highlightbackground=LINE,
                         highlightthickness=1)
        return panel

    def _label(self, parent: tk.Widget, value: str, size: int = 11,
               color: str = TEXT, bold: bool = False, **kwargs) -> tk.Label:
        font = ("Segoe UI Semibold" if bold else "Segoe UI", size)
        return tk.Label(parent, text=value, bg=parent["bg"], fg=color, font=font, **kwargs)

    def navigate(self, page: str) -> None:
        self.active_page = page
        for key, button in self.nav_buttons.items():
            button.configure(bg=CARD_SECONDARY if key == page else SIDEBAR,
                             fg=TEXT if key == page else "#BECDE0")
        for child in self.content.winfo_children():
            child.destroy()

        details = {
            "panel": ("Panel główny", "Aktualny stan komputera — rzeczywiste pomiary"),
            "procesy": ("Procesy", "Przegląd wykorzystania pamięci i procesora · tylko odczyt"),
            "czyszczenie": ("Bezpieczne czyszczenie", "Wyłącznie pliki z TEMP użytkownika starsze niż 7 dni"),
            "autostart": ("Autostart", "Przegląd programów uruchamianych przy logowaniu"),
            "rejestr": ("Rejestr Windows", "Wyłączanie i przywracanie autostartu użytkownika z kopią .reg"),
            "porady": ("Rekomendacje", "Wskazówki oparte na pomiarach, bez ryzykownych trików"),
        }
        heading, tagline = details[page]
        self.title.configure(text=heading)
        self.subtitle.configure(text=tagline)
        {
            "panel": self._page_panel,
            "procesy": self._page_processes,
            "czyszczenie": self._page_clean,
            "autostart": self._page_autostart,
            "rejestr": self._page_registry,
            "porady": self._page_tips,
        }[page]()
        if page == "panel" and self.data is None:
            self._submit("snapshot", snapshot)
        elif page == "procesy" and not self.processes:
            self._submit("processes", running_processes)
        elif page == "autostart":
            self._submit("startups", startup_entries)
        elif page == "rejestr":
            self._submit("registry", list_user_run)
        elif page == "porady" and self.data is None:
            self._submit("snapshot", snapshot)

    def _page_panel(self) -> None:
        outer = self.content
        metrics = tk.Frame(outer, bg=BG)
        metrics.pack(fill="x")
        for col in range(4):
            metrics.grid_columnconfigure(col, weight=1, uniform="metrics")
        self.metric_values: dict[str, tk.Label] = {}
        self.metric_bars: dict[str, ttk.Progressbar] = {}
        for i, (name, description) in enumerate((
            ("CPU", "Użycie procesora"),
            ("RAM", "Pamięć operacyjna"),
            ("DYSK", "Dysk systemowy"),
            ("UPTIME", "Od uruchomienia"),
        )):
            block = self._card(metrics)
            block.grid(row=0, column=i, sticky="nsew", padx=(0, 10 if i < 3 else 0))
            self._label(block, description.upper(), 9, MUTED, True).pack(anchor="w")
            value = self._label(block, "—", 26, TEXT, True)
            value.pack(anchor="w", pady=(14, 5))
            self.metric_values[name] = value
            if name != "UPTIME":
                bar = ttk.Progressbar(block, style="Pulse.Horizontal.TProgressbar",
                                      orient="horizontal", mode="determinate", maximum=100)
                bar.pack(fill="x", pady=(12, 5))
                self.metric_bars[name] = bar
            else:
                self._label(block, "Od ostatniego startu", 9, MUTED).pack(anchor="w", pady=(12, 5))

        center = self._card(outer, 22, 20)
        center.pack(fill="x", pady=(19, 15))
        self._label(center, "Informacje o komputerze", 15, TEXT, True).pack(anchor="w", pady=(0, 15))
        self.specs = self._label(center, "Pobieranie danych…", 11, MUTED,
                                 justify="left", anchor="w")
        self.specs.pack(fill="x")
        bottom = self._card(outer, 22, 18)
        bottom.pack(fill="x")
        self._label(bottom, "Co robi PulsePC?", 14, TEXT, True).pack(anchor="w", pady=(0, 8))
        self._label(bottom,
                    "Pokazuje użycie zasobów, procesy i autostart. Pozwala przejrzeć oraz ręcznie usunąć "
                    "stare pliki z TEMP użytkownika. Obsługuje wybrane wpisy autostartu z kopią rejestru. "
                    "Nie obiecuje cudownego wzrostu FPS.",
                    10, MUTED, wraplength=760, justify="left").pack(anchor="w", fill="x")
        actions = tk.Frame(outer, bg=BG)
        actions.pack(fill="x", pady=(17, 0))
        self._button(actions, "Zapisz raport JSON", self._save_report).pack(side="left")
        if self.data:
            self._update_panel()

    def _update_panel(self) -> None:
        if not self.data or self.active_page != "panel":
            return
        d = self.data
        uptime = d["uptime_seconds"]
        values = {
            "CPU": f'{d["cpu_percent"]:.0f}%',
            "RAM": f'{d["ram_percent"]:.0f}%',
            "DYSK": f'{d["disk_percent"]:.0f}%',
            "UPTIME": f"{uptime // 86400} d  {(uptime % 86400) // 3600} h",
        }
        for name, value in values.items():
            self.metric_values[name].configure(text=value)
        self.metric_bars["CPU"]["value"] = d["cpu_percent"]
        self.metric_bars["RAM"]["value"] = d["ram_percent"]
        self.metric_bars["DYSK"]["value"] = d["disk_percent"]
        self.specs.configure(text=(
            f'System:  {d["system"]}\n'
            f'Procesor:  {d["processor"]}  ·  rdzenie {d["physical_cores"]} / wątki {d["logical_cores"]}\n'
            f'RAM:  {bytes_human(d["ram_used"])} / {bytes_human(d["ram_total"])}\n'
            f'Dysk ({d["disk_drive"]}):  zajęte {bytes_human(d["disk_used"])} / '
            f'{bytes_human(d["disk_total"])}  ·  wolne {bytes_human(d["disk_free"])}\n'
            f'Ostatni pomiar:  {d["captured_at"]}'
        ))

    def _tree(self, parent: tk.Widget, fields: list[tuple[str, str, int]]) -> ttk.Treeview:
        frame = tk.Frame(parent, bg=CARD)
        frame.pack(fill="both", expand=True)
        names = [x[0] for x in fields]
        tree = ttk.Treeview(frame, columns=names, show="headings", style="Pulse.Treeview", selectmode="browse")
        for name, text, width in fields:
            tree.heading(name, text=text)
            tree.column(name, width=width, minwidth=60, stretch=(name == names[-1]))
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview,
                               style="Pulse.Vertical.TScrollbar")
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return tree

    def _page_processes(self) -> None:
        toolbar = tk.Frame(self.content, bg=BG)
        toolbar.pack(fill="x", pady=(0, 14))
        self._label(toolbar, "Wyszukaj nazwę procesu:", 10, MUTED).pack(side="left", padx=(0, 12))
        self.process_search = tk.StringVar()
        self.process_search.trace_add("write", lambda *_: self._fill_processes())
        tk.Entry(toolbar, textvariable=self.process_search, bg=CARD, fg=TEXT, insertbackground=TEXT,
                 relief="flat", bd=9, font=("Segoe UI", 10), width=26).pack(side="left")
        self.count_processes = self._label(toolbar, "", 9, MUTED)
        self.count_processes.pack(side="right")
        body = self._card(self.content, 12, 12)
        body.pack(fill="both", expand=True)
        self.process_tree = self._tree(body, [
            ("pid", "PID", 90), ("name", "Nazwa", 360),
            ("ram", "RAM", 140), ("cpu", "CPU (chwilowe)", 150),
        ])
        self._label(self.content,
                    "Lista tylko do odczytu. Pomiar użycia CPU przez proces jest orientacyjny i może wymagać ponownego odświeżenia.",
                    9, MUTED).pack(anchor="w", pady=(11, 0))
        self._fill_processes()

    def _fill_processes(self) -> None:
        if self.active_page != "procesy":
            return
        query = self.process_search.get().casefold().strip()
        self.process_tree.delete(*self.process_tree.get_children())
        filtered = [p for p in self.processes if query in p["name"].casefold()]
        for item in filtered:
            self.process_tree.insert("", "end", values=(
                item["pid"], item["name"], bytes_human(item["memory_bytes"]),
                f'{item["cpu_percent"]:.1f}%',
            ))
        self.count_processes.configure(text=f"Wyświetlono: {len(filtered)}")

    def _page_clean(self) -> None:
        root = user_temp_dir()
        header = self._card(self.content, 20, 16)
        header.pack(fill="x", pady=(0, 14))
        self._label(header, "Zakres skanowania", 14, TEXT, True).pack(anchor="w")
        target = str(root) if root else "Nie wykryto katalogu TEMP użytkownika"
        self._label(header, target, 10, MUTED, wraplength=680, justify="left").pack(
            anchor="w", pady=(6, 8))
        self._label(header, "Tylko pliki starsze niż 7 dni. Nie usuwamy folderów, plików systemowych ani danych przeglądarek.",
                    10, GREEN, wraplength=720, justify="left").pack(anchor="w")
        buttons = tk.Frame(self.content, bg=BG)
        buttons.pack(fill="x", pady=(0, 13))
        self.scan_btn = self._button(buttons, "Skanuj TEMP", self._scan_temp, primary=True)
        self.scan_btn.pack(side="left")
        self.clean_btn = self._button(buttons, "Usuń znalezione pliki", self._confirm_clean)
        self.clean_btn.pack(side="left", padx=(12, 0))
        if not self.scan or not self.scan.files or not root:
            self.clean_btn.configure(state="disabled")
        if not root:
            self.scan_btn.configure(state="disabled")
        self.clean_info = self._label(self.content, "Uruchom skanowanie, aby zobaczyć podgląd.", 10, MUTED)
        self.clean_info.pack(anchor="w", pady=(0, 10))
        body = self._card(self.content, 12, 12)
        body.pack(fill="both", expand=True)
        self.clean_tree = self._tree(body, [
            ("name", "Plik", 330), ("size", "Rozmiar", 125),
            ("modified", "Ostatnia zmiana", 180),
        ])
        self._fill_scan()

    def _fill_scan(self) -> None:
        if self.active_page != "czyszczenie":
            return
        self.clean_tree.delete(*self.clean_tree.get_children())
        if not self.scan:
            return
        for f in self.scan.files[:150]:
            try:
                modified = dt.datetime.fromtimestamp(f.modified).strftime("%Y-%m-%d %H:%M")
            except (OSError, ValueError, OverflowError):
                modified = "—"
            self.clean_tree.insert("", "end", values=(Path(f.path).name, bytes_human(f.size), modified))
        limitation = " (osiągnięto limit skanu!)" if self.scan.limited else ""
        self.clean_info.configure(text=(
            f"Znaleziono {len(self.scan.files)} plików · potencjalnie do odzyskania "
            f"{bytes_human(self.scan.bytes_total)} · podgląd 150 największych{limitation}"
        ))
        self.clean_btn.configure(state="normal" if self.scan.files else "disabled")

    def _page_autostart(self) -> None:
        info = self._card(self.content, 18, 15)
        info.pack(fill="x", pady=(0, 14))
        self._label(info, "Aplikacje uruchamiane z Windows", 14, TEXT, True).pack(anchor="w")
        self._label(info, "Wpisy Run z rejestru. To niepełna lista wszystkich mechanizmów autostartu.",
                    10, MUTED, wraplength=690, justify="left").pack(anchor="w", pady=(6, 0))
        toolbar = tk.Frame(self.content, bg=BG)
        toolbar.pack(fill="x", pady=(0, 13))
        self._button(toolbar, "Otwórz ustawienia Autostartu", self._open_startup_settings).pack(side="left")
        self.startup_count = self._label(toolbar, "", 10, MUTED)
        self.startup_count.pack(side="right")
        body = self._card(self.content, 12, 12)
        body.pack(fill="both", expand=True)
        self.startup_tree = self._tree(body, [
            ("name", "Aplikacja", 190), ("scope", "Zakres", 150),
            ("command", "Polecenie (tylko odczyt)", 500),
        ])
        self._fill_startups()

    def _fill_startups(self) -> None:
        if self.active_page != "autostart":
            return
        self.startup_tree.delete(*self.startup_tree.get_children())
        for item in self.startups:
            self.startup_tree.insert("", "end", values=(item["name"], item["scope"], item["command"]))
        self.startup_count.configure(text=f"Wpisy: {len(self.startups)}")

    def _page_registry(self) -> None:
        """Modyfikujemy jedynie wpisy Run należące do bieżącego użytkownika."""
        info = self._card(self.content, 18, 15)
        info.pack(fill="x", pady=(0, 14))
        self._label(info, "Rejestr: autostart użytkownika (HKCU)", 14, TEXT, True).pack(anchor="w")
        self._label(
            info,
            "Możesz wyłączyć niepotrzebny wpis autostartu i później go przywrócić. "
            "Przed wyłączeniem zapisujemy osobną kopię .reg. "
            "Nie usuwamy losowych wpisów rejestru ani ustawień Windows.",
            10, MUTED, wraplength=730, justify="left",
        ).pack(anchor="w", pady=(8, 0))
        toolbar = tk.Frame(self.content, bg=BG)
        toolbar.pack(fill="x", pady=(0, 13))
        self.reg_disable_btn = self._button(toolbar, "Wyłącz zaznaczony", self._disable_registry)
        self.reg_disable_btn.pack(side="left")
        self.reg_restore_btn = self._button(toolbar, "Przywróć zaznaczony", self._restore_registry)
        self.reg_restore_btn.pack(side="left", padx=(10, 0))
        self.reg_count = self._label(toolbar, "", 10, MUTED)
        self.reg_count.pack(side="right")
        body = self._card(self.content, 12, 12)
        body.pack(fill="both", expand=True)
        self.registry_tree = self._tree(body, [
            ("name", "Nazwa", 180), ("state", "Stan", 110),
            ("kind", "Typ", 110), ("command", "Polecenie", 415),
        ])
        self.registry_tree.bind("<<TreeviewSelect>>", lambda _event: self._reg_buttons())
        self._label(
            self.content,
            "Zmiany działają dopiero przy kolejnym logowaniu. Wyłączanie nie odinstalowuje aplikacji. "
            "Wpisy systemowe HKLM są tylko do odczytu w zakładce Autostart.",
            9, MUTED, wraplength=750, justify="left",
        ).pack(anchor="w", pady=(11, 0))
        self._fill_registry()

    def _selected_registry(self) -> RunEntry | None:
        selected = self.registry_tree.selection()
        if not selected:
            return None
        try:
            return self.reg_entries[int(selected[0])]
        except (ValueError, IndexError):
            return None

    def _reg_buttons(self) -> None:
        if self.active_page != "rejestr":
            return
        entry = self._selected_registry()
        can_change = not ("disable_registry" in self.busy or "restore_registry" in self.busy)
        self.reg_disable_btn.configure(state="normal" if entry and entry.enabled and can_change else "disabled")
        self.reg_restore_btn.configure(state="normal" if entry and not entry.enabled and can_change else "disabled")

    def _fill_registry(self) -> None:
        if self.active_page != "rejestr":
            return
        self.registry_tree.delete(*self.registry_tree.get_children())
        for index, entry in enumerate(self.reg_entries):
            self.registry_tree.insert("", "end", iid=str(index), values=(
                entry.name, "Włączony" if entry.enabled else "Wyłączony",
                "REG_SZ" if entry.kind == 1 else "REG_EXPAND_SZ", entry.command,
            ))
        self.reg_count.configure(text=f"Wpisy: {len(self.reg_entries)}")
        self._reg_buttons()

    def _disable_registry(self) -> None:
        entry = self._selected_registry()
        if not entry or not entry.enabled:
            return
        if not messagebox.askyesno(
            "Potwierdź zmianę rejestru",
            f"Wyłączyć autostart '{entry.name}'?\n\n"
            "Zrobimy kopię .reg w AppData/Local/PulsePC/KopieRejestru.\n"
            "Wyłączenie tego wpisu może wpłynąć na działanie aplikacji.\n"
            "Możesz go przywrócić w tej zakładce.",
            icon="warning",
        ):
            return
        self._submit("disable_registry", disable_user_run, entry)
        self._reg_buttons()

    def _restore_registry(self) -> None:
        entry = self._selected_registry()
        if not entry or entry.enabled:
            return
        if not messagebox.askyesno(
            "Przywróć wpis autostartu",
            f"Przywrócić '{entry.name}' do autostartu bieżącego użytkownika?",
        ):
            return
        self._submit("restore_registry", restore_user_run, entry)
        self._reg_buttons()

    def _page_tips(self) -> None:
        self.tips_holder = tk.Frame(self.content, bg=BG)
        self.tips_holder.pack(fill="both", expand=True)
        self._fill_tips()

    def _fill_tips(self) -> None:
        if self.active_page != "porady":
            return
        for widget in self.tips_holder.winfo_children():
            widget.destroy()
        if not self.data:
            self._label(self.tips_holder, "Pobieranie pomiarów…", 12, MUTED).pack(anchor="w")
            return
        for tip in recommendations(self.data, self.scan):
            card = self._card(self.tips_holder, 19, 12)
            card.pack(fill="x", pady=(0, 10))
            color = GREEN if tip["level"] == "ok" else ORANGE if tip["level"] == "uwaga" else ACCENT
            self._label(card, "●  " + tip["title"], 12, color, True).pack(anchor="w")
            self._label(card, tip["text"], 10, MUTED, wraplength=710,
                        justify="left").pack(anchor="w", pady=(7, 0))

    def refresh_current(self) -> None:
        if self.active_page in ("panel", "porady"):
            self._submit("snapshot", snapshot)
        elif self.active_page == "procesy":
            self._submit("processes", running_processes)
        elif self.active_page == "czyszczenie":
            self._scan_temp()
        elif self.active_page == "autostart":
            self._submit("startups", startup_entries)
        elif self.active_page == "rejestr":
            self._submit("registry", list_user_run)

    def _refresh_periodically(self) -> None:
        if not self.live:
            return
        if self.active_page == "panel":
            self._submit("snapshot", snapshot)
        self.root.after(10000, self._refresh_periodically)

    def _submit(self, job: str, fn, *args) -> None:
        if job in self.busy or not self.live:
            return
        self.busy.add(job)
        self.status.configure(text=f"Przetwarzanie: {job}…", fg=ACCENT)

        def worker() -> None:
            try:
                self.messages.put((job, fn(*args)))
            except Exception as exc:
                self.messages.put(("error", (job, str(exc))))
        self.executor.submit(worker)

    def _read_messages(self) -> None:
        if not self.live:
            return
        try:
            while True:
                job, result = self.messages.get_nowait()
                if job == "error":
                    origin, reason = result
                    self.busy.discard(origin)
                    self.status.configure(text=f"Błąd ({origin}): {reason}", fg=ORANGE)
                    if origin in ("clean", "scan", "disable_registry", "restore_registry"):
                        messagebox.showerror("PulsePC", str(reason))
                    if origin in ("disable_registry", "restore_registry"):
                        self._reg_buttons()
                    continue
                self.busy.discard(job)
                self.status.configure(text="Gotowe · dane lokalne, nic nie wysyłamy do internetu", fg=MUTED)
                if job == "snapshot":
                    self.data = result
                    self._update_panel()
                    self._fill_tips()
                elif job == "processes":
                    self.processes = result
                    self._fill_processes()
                elif job == "startups":
                    self.startups = result
                    self._fill_startups()
                elif job == "registry":
                    self.reg_entries = result
                    self._fill_registry()
                elif job == "disable_registry":
                    messagebox.showinfo("PulsePC", f"Wpis wyłączony.\nKopia bezpieczeństwa: {result}")
                    self._submit("registry", list_user_run)
                elif job == "restore_registry":
                    messagebox.showinfo("PulsePC", "Wpis autostartu został przywrócony.")
                    self._submit("registry", list_user_run)
                elif job == "scan":
                    self.scan = result
                    self._fill_scan()
                elif job == "clean":
                    self.scan = None
                    details = result
                    messagebox.showinfo("Wynik czyszczenia",
                                        f"Usunięto: {details.removed_count} plików\n"
                                        f"Odzyskane miejsce: {bytes_human(details.removed_bytes)}\n"
                                        f"Pominięte: {details.skipped_count}\n"
                                        f"Nieudane (np. pliki używane): {details.error_count}")
                    if self.active_page == "czyszczenie":
                        self._scan_temp()
        except queue.Empty:
            pass
        self.root.after(120, self._read_messages)

    def _scan_temp(self) -> None:
        root = user_temp_dir()
        if root is None:
            messagebox.showwarning("PulsePC", "Nie znaleziono bezpiecznego katalogu TEMP użytkownika.")
            return
        self._submit("scan", scan_temp, root)

    def _confirm_clean(self) -> None:
        if not self.scan or not self.scan.files or "clean" in self.busy or "scan" in self.busy:
            return
        live_root = user_temp_dir()
        if live_root is None or os.path.normcase(os.path.abspath(live_root)) != os.path.normcase(os.path.abspath(self.scan.root)):
            messagebox.showerror("PulsePC", "Folder TEMP zmienił się. Uruchom nowe skanowanie.")
            return
        if not messagebox.askyesno(
            "Potwierdź usuwanie",
            f"Czy na pewno usunąć {len(self.scan.files)} plików starszych niż {MIN_FILE_AGE_DAYS} dni?\n\n"
            f"Maksymalnie {bytes_human(self.scan.bytes_total)} odzyskanego miejsca.\n"
            "Pliki mogą być potrzebne niektórym programom; nie można cofnąć tej operacji.\n\n"
            "Zamknij inne programy przed kontynuowaniem.",
            icon="warning",
        ):
            return
        self.clean_btn.configure(state="disabled")
        self._submit("clean", clean_temp, self.scan)

    def _open_startup_settings(self) -> None:
        try:
            os.startfile("ms-settings:startupapps")
        except OSError:
            messagebox.showinfo("PulsePC", "Otwórz Ustawienia → Aplikacje → Autostart.")

    def _save_report(self) -> None:
        if not self.data:
            messagebox.showinfo("PulsePC", "Najpierw odśwież pomiary.")
            return
        location = filedialog.asksaveasfilename(
            title="Zapisz raport diagnostyczny",
            initialfile="PulsePC-raport.json",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if not location:
            return
        try:
            report = report_payload(self.data, self.processes, self.startups, self.scan)
            with open(location, "w", encoding="utf-8") as file:
                json.dump(report, file, ensure_ascii=False, indent=2)
            self.status.configure(text=f"Zapisano raport: {location}", fg=GREEN)
        except OSError as exc:
            messagebox.showerror("PulsePC", f"Nie udało się zapisać raportu: {exc}")

    def _close(self) -> None:
        self.live = False
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()
