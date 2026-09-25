#!/usr/bin/env python3
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_NAME = "Xanax"
CONFIG_DIR = Path.home() / ".tf2vpkmanager"
CONFIG_FILE = CONFIG_DIR / "config.json"
GITHUB_URL = "https://github.com/effroi"

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
BG_DARK      = "#1a1a1d"
BG_PANEL     = "#212124"
BG_SIDEBAR   = "#17171a"
BG_CARD      = "#212124"
BG_HOVER     = "#2a2a2e"
ACCENT       = "#5b6472"
ACCENT_HOVER = "#6e7887"
ACCENT_DIM   = "#3a3f47"
TEXT_MAIN    = "#e4e4e6"
TEXT_SUB     = "#8a8a90"
GREEN_OK     = "#7fae86"
RED_OFF      = "#b06a6a"

FONT_TITLE = ("Segoe UI", 14, "bold")
FONT_SUB   = ("Segoe UI", 9)
FONT_BODY  = ("Segoe UI", 10)
FONT_MONO  = ("Consolas", 9)


# ---------------------------------------------------------------------------
# Steam / filesystem logic
# ---------------------------------------------------------------------------
class TF2Locator:
    """Locates the tf/custom folder for Team Fortress 2 (appid 440)."""

    STEAM_DEFAULTS = {
        "win32": [Path("C:/Program Files (x86)/Steam")],
        "linux": [Path.home() / ".steam/steam", Path.home() / ".local/share/Steam"],
        "darwin": [Path.home() / "Library/Application Support/Steam"],
    }

    @classmethod
    def guess_steam_root(cls):
        plat = "win32" if sys.platform.startswith("win") else (
            "darwin" if sys.platform == "darwin" else "linux"
        )
        for p in cls.STEAM_DEFAULTS.get(plat, []):
            if p.exists():
                return p
        return None

    @classmethod
    def find_library_folders(cls, steam_root: Path):
        """Parse libraryfolders.vdf to get every Steam library path."""
        vdf = steam_root / "steamapps" / "libraryfolders.vdf"
        libs = [steam_root]
        if vdf.exists():
            try:
                content = vdf.read_text(errors="ignore")
                for match in re.finditer(r'"path"\s+"([^"]+)"', content):
                    path = Path(match.group(1).replace("\\\\", "/"))
                    if path.exists():
                        libs.append(path)
            except Exception:
                pass
        return libs

    @classmethod
    def find_tf2_custom(cls):
        """Returns the tf/custom path if it can be found automatically."""
        steam_root = cls.guess_steam_root()
        if not steam_root:
            return None
        for lib in cls.find_library_folders(steam_root):
            candidate = lib / "steamapps" / "common" / "Team Fortress 2" / "tf" / "custom"
            if candidate.parent.exists():
                candidate.mkdir(parents=True, exist_ok=True)
                return candidate
        return None


# ---------------------------------------------------------------------------
# Mod type 
# ---------------------------------------------------------------------------
VPK_SIGNATURE = 0x55AA1234

MOD_CATEGORIES = [
    "HUD",
    "Player skin",
    "Weapon skin",
    "Texture / Material",
    "Effects / Particles",
    "Sound",
    "Script / Config",
    "Map",
    "Unknown",
]

#  keywords used when the VPK content can't be parsed
FILENAME_HINTS = [
    ("HUD", ("hud",)),
    ("Player skin", ("player", "playermodel", "cosmetic")),
    ("Weapon skin", ("weapon", "gun", "knife", "reskin")),
    ("Sound", ("sound", "audio", "voice")),
    ("Effects / Particles", ("particle", "fx", "effect")),
    ("Script / Config", ("script", "config", "cfg")),
    ("Map", ("map", "level")),
    ("Texture / Material", ("texture", "material", "skin")),
]


def _read_cstring(f) -> str:
    chars = bytearray()
    while True:
        b = f.read(1)
        if not b or b == b"\x00":
            break
        chars += b
    return chars.decode("utf-8", errors="replace")


def parse_vpk_internal_paths(vpk_path: Path, max_entries: int = 6000):
    
    paths = []
    try:
        with open(vpk_path, "rb") as f:
            sig = struct.unpack("<I", f.read(4))[0]
            if sig != VPK_SIGNATURE:
                return None
            version = struct.unpack("<I", f.read(4))[0]
            tree_size = struct.unpack("<I", f.read(4))[0]
            if version == 2:
                f.read(16)  # embed_chunk_len, chunk_hash_len, self_hash_len, sig_len
            elif version != 1:
                return None

            tree_end = f.tell() + tree_size
            while f.tell() < tree_end:
                ext = _read_cstring(f)
                if ext == "":
                    break
                while True:
                    path = _read_cstring(f)
                    if path == "":
                        break
                    while True:
                        name = _read_cstring(f)
                        if name == "":
                            break
                        f.read(4)                                    # CRC
                        preload = struct.unpack("<H", f.read(2))[0]   # preload bytes
                        f.read(2)                                    # archive index
                        f.read(4)                                    # entry offset
                        f.read(4)                                    # entry length
                        f.read(2)                                    # terminator 0xFFFF
                        if preload:
                            f.read(preload)
                        clean_path = "" if path.strip() in ("", " ") else path
                        full = f"{clean_path}/{name}.{ext}" if clean_path else f"{name}.{ext}"
                        paths.append(full.lower())
                        if len(paths) >= max_entries:
                            return paths
    except Exception:
        return None
    return paths


def classify_by_paths(paths):
    """Applies keyword rules to the VPK's internal file paths."""
    joined = "\n".join(paths)

    def any_in(*needles):
        return any(n in joined for n in needles)

    if any_in("resource/ui/", "hudlayout", "hudanimations", "scripts/hudanimations"):
        return "HUD"
    if any_in("particles/", "scripts/particles"):
        return "Effects / Particles"
    if any_in("models/player/", "materials/models/player/"):
        return "Player skin"
    if any_in("models/weapons/", "materials/models/weapons/"):
        return "Weapon skin"
    if any_in("sound/"):
        return "Sound"
    if any_in("maps/"):
        return "Map"
    if any_in("materials/", "materialsrc/"):
        return "Texture / Material"
    if any_in("scripts/", "cfg/"):
        return "Script / Config"
    return "Unknown"


def classify_by_filename(name: str):
    lower = name.lower()
    for category, keywords in FILENAME_HINTS:
        if any(k in lower for k in keywords):
            return category
    return "Unknown"


def detect_mod_category(vpk_path: Path) -> str:
    paths = parse_vpk_internal_paths(vpk_path)
    if paths:
        return classify_by_paths(paths)
    return classify_by_filename(vpk_path.name)


# ---------------------------------------------------------------------------
# VPK file management
# ---------------------------------------------------------------------------
class VPKManager:
    """Handles operations on .vpk files inside the custom folder."""

    DISABLED_SUFFIX = ".disabled"

    def __init__(self, custom_dir: Path):
        self.custom_dir = Path(custom_dir)
        self._category_cache = {}  # (path_str, mtime, size) -> category

    def _get_category(self, entry: Path):
        try:
            stat = entry.stat()
            key = (str(entry), stat.st_mtime, stat.st_size)
        except OSError:
            key = (str(entry), 0, 0)
        cached = self._category_cache.get(key)
        if cached:
            return cached
        category = detect_mod_category(entry)
        self._category_cache[key] = category
        return category

    def list_vpks(self):
        """Returns a list of dicts: {name, path, enabled, size, category}."""
        items = []
        if not self.custom_dir.exists():
            return items
        for entry in sorted(self.custom_dir.iterdir()):
            if entry.is_file() and (
                entry.suffix == ".vpk"
                or entry.name.endswith(".vpk" + self.DISABLED_SUFFIX)
            ):
                enabled = entry.suffix == ".vpk"
                display_name = entry.name[: -len(self.DISABLED_SUFFIX)] if not enabled else entry.name
                items.append({
                    "name": display_name,
                    "path": entry,
                    "enabled": enabled,
                    "size": entry.stat().st_size,
                    "category": self._get_category(entry),
                })
        return items

    def add_vpk(self, source_path: str):
        src = Path(source_path)
        if not src.exists() or src.suffix.lower() != ".vpk":
            raise ValueError(f"Invalid file: {src}")
        dest = self.custom_dir / src.name
        if dest.exists():
            raise FileExistsError(f"{src.name} already exists in the custom folder.")
        self.custom_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return dest

    def set_enabled(self, entry_path: Path, enabled: bool):
        entry_path = Path(entry_path)
        if enabled:
            if entry_path.name.endswith(self.DISABLED_SUFFIX):
                new_path = entry_path.with_name(entry_path.name[: -len(self.DISABLED_SUFFIX)])
                entry_path.rename(new_path)
                return new_path
            return entry_path
        else:
            if not entry_path.name.endswith(self.DISABLED_SUFFIX):
                new_path = entry_path.with_name(entry_path.name + self.DISABLED_SUFFIX)
                entry_path.rename(new_path)
                return new_path
            return entry_path

    def remove(self, entry_path: Path):
        Path(entry_path).unlink(missing_ok=True)


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def open_in_explorer(path: Path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        messagebox.showerror(APP_NAME, f"Couldn't open the folder:\n{exc}")


class AccentButton(tk.Button):
    """Flat button with a simple color change on hover."""

    def __init__(self, master, text, command=None, kind="accent", **kwargs):
        colors = {
            "accent": (ACCENT, ACCENT_HOVER, "#ffffff"),
            "ghost":  (BG_CARD, BG_HOVER, TEXT_MAIN),
            "danger": ("#33282a", "#3d2e30", RED_OFF),
        }
        bg, hover, fg = colors.get(kind, colors["accent"])
        super().__init__(
            master, text=text, command=command,
            bg=bg, fg=fg, activebackground=hover, activeforeground=fg,
            relief="flat", bd=0, font=FONT_BODY, padx=14, pady=7,
            cursor="hand2", **kwargs,
        )
        self._bg, self._hover = bg, hover
        self.bind("<Enter>", lambda e: self.config(bg=self._hover))
        self.bind("<Leave>", lambda e: self.config(bg=self._bg))


class SidebarButton(tk.Frame):
    """Sidebar navigation entry, with an active-state indicator."""

    def __init__(self, master, text, command):
        super().__init__(master, bg=BG_SIDEBAR)
        self.command = command
        self.active = False

        self.indicator = tk.Frame(self, bg=BG_SIDEBAR, width=3)
        self.indicator.pack(side="left", fill="y")

        self.label = tk.Label(
            self, text=f"  {text}", bg=BG_SIDEBAR, fg=TEXT_SUB,
            font=FONT_BODY, anchor="w", padx=10, pady=10,
        )
        self.label.pack(side="left", fill="both", expand=True)

        for widget in (self, self.label):
            widget.bind("<Button-1>", lambda e: self.command())
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

    def _on_enter(self, _):
        if not self.active:
            self.label.config(fg=TEXT_MAIN, bg=BG_HOVER)
            self.config(bg=BG_HOVER)

    def _on_leave(self, _):
        if not self.active:
            self.label.config(fg=TEXT_SUB, bg=BG_SIDEBAR)
            self.config(bg=BG_SIDEBAR)

    def set_active(self, active: bool):
        self.active = active
        bg = BG_CARD if active else BG_SIDEBAR
        fg = TEXT_MAIN if active else TEXT_SUB
        self.label.config(bg=bg, fg=fg)
        self.config(bg=bg)
        self.indicator.config(bg=ACCENT if active else BG_SIDEBAR)


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------
class TF2VPKApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("880x560")
        self.minsize(760, 480)
        self.configure(bg=BG_DARK)

        self.config_data = self._load_config()
        custom_dir = self.config_data.get("tf2_custom_dir") or ""
        self.manager = VPKManager(Path(custom_dir)) if custom_dir else None

        self._setup_style()
        self._build_layout()
        self._show_page("vpks")

        if not custom_dir:
            self.after(300, self._try_autodetect)
        else:
            self.refresh_list()

    # -- config ---------------------------------------------------------
    def _load_config(self):
        if CONFIG_FILE.exists():
            try:
                return json.loads(CONFIG_FILE.read_text())
            except Exception:
                return {}
        return {}

    def _save_config(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(self.config_data, indent=2))

    # -- style ------------------------------------------------------------
    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure("Treeview",
                         background=BG_CARD, fieldbackground=BG_CARD,
                         foreground=TEXT_MAIN, rowheight=30, borderwidth=0,
                         font=FONT_BODY)
        style.map("Treeview", background=[("selected", ACCENT_DIM)],
                   foreground=[("selected", "#ffffff")])
        style.configure("Treeview.Heading",
                         background=BG_PANEL, foreground=TEXT_SUB,
                         relief="flat", font=("Segoe UI", 9, "bold"))
        style.map("Treeview.Heading", background=[("active", BG_PANEL)])

        style.configure("TScrollbar", background=BG_PANEL, troughcolor=BG_DARK,
                         bordercolor=BG_DARK, arrowcolor=TEXT_SUB)

    # -- layout -----------------------------------------------------------
    def _build_layout(self):
        # Sidebar
        sidebar = tk.Frame(self, bg=BG_SIDEBAR, width=190)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        self.logo = tk.Label(sidebar, text="XANAX", bg=BG_SIDEBAR, fg=TEXT_MAIN,
                              font=("Segoe UI", 16, "bold"), pady=24)
        self.logo.pack(fill="x")

        self.nav_buttons = {}
        nav_items = [
            ("vpks", "VPKs"),
            ("settings", "Settings"),
        ]
        for key, label in nav_items:
            btn = SidebarButton(sidebar, label, lambda k=key: self._show_page(k))
            btn.pack(fill="x", pady=1)
            self.nav_buttons[key] = btn

        footer = tk.Label(sidebar, text="Not affiliated with Valve",
                           bg=BG_SIDEBAR, fg=TEXT_SUB, font=("Segoe UI", 8))
        footer.pack(side="bottom", pady=(0, 4))

        credit_footer = tk.Label(sidebar, text="by effroi", bg=BG_SIDEBAR,
                                  fg=TEXT_SUB, font=("Segoe UI", 8, "underline"),
                                  cursor="hand2")
        credit_footer.pack(side="bottom", pady=(0, 8))
        credit_footer.bind("<Button-1>", lambda e: webbrowser.open(GITHUB_URL))
        credit_footer.bind("<Enter>", lambda e: credit_footer.config(fg=TEXT_MAIN))
        credit_footer.bind("<Leave>", lambda e: credit_footer.config(fg=TEXT_SUB))

        # Main area
        self.main_area = tk.Frame(self, bg=BG_DARK)
        self.main_area.pack(side="left", fill="both", expand=True)

        self.pages = {}
        self._build_vpks_page()
        self._build_settings_page()

    def _show_page(self, key):
        for k, btn in self.nav_buttons.items():
            btn.set_active(k == key)
        for k, frame in self.pages.items():
            frame.pack_forget()
        self.pages[key].pack(fill="both", expand=True)

    # -- page: VPKs ---------------------------------------------------
    def _build_vpks_page(self):
        page = tk.Frame(self.main_area, bg=BG_DARK)
        self.pages["vpks"] = page

        header = tk.Frame(page, bg=BG_DARK)
        header.pack(fill="x", padx=24, pady=(22, 10))
        tk.Label(header, text="VPKs", bg=BG_DARK, fg=TEXT_MAIN,
                 font=FONT_TITLE).pack(side="left")
        self.path_label = tk.Label(header, text="", bg=BG_DARK, fg=TEXT_SUB,
                                    font=FONT_SUB)
        self.path_label.pack(side="left", padx=14)

        # Action bar
        actions = tk.Frame(page, bg=BG_DARK)
        actions.pack(fill="x", padx=24, pady=(0, 12))
        AccentButton(actions, "Add VPK", self.on_add_vpk).pack(side="left")
        AccentButton(actions, "Enable/Disable", self.on_toggle_selected,
                     kind="ghost").pack(side="left", padx=8)
        AccentButton(actions, "Remove", self.on_remove_selected,
                     kind="danger").pack(side="left")
        AccentButton(actions, "Open folder", self.on_open_folder,
                     kind="ghost").pack(side="left", padx=8)
        AccentButton(actions, "Refresh", self.refresh_list,
                     kind="ghost").pack(side="right")

        tk.Label(actions, text="Type:", bg=BG_DARK, fg=TEXT_SUB,
                 font=FONT_SUB).pack(side="right", padx=(0, 6))
        self.category_filter_var = tk.StringVar(value="All")
        self.category_combo = ttk.Combobox(
            actions, textvariable=self.category_filter_var, state="readonly",
            width=18, values=["All"] + MOD_CATEGORIES,
            font=FONT_SUB,
        )
        self.category_combo.pack(side="right")
        self.category_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_list())

        # Treeview
        table_wrap = tk.Frame(page, bg=BG_CARD)
        table_wrap.pack(fill="both", expand=True, padx=24, pady=(0, 20))

        columns = ("status", "name", "category", "size")
        self.tree = ttk.Treeview(table_wrap, columns=columns, show="headings",
                                  selectmode="extended")
        self.tree.heading("status", text="Status")
        self.tree.heading("name", text="File name")
        self.tree.heading("category", text="Type")
        self.tree.heading("size", text="Size")
        self.tree.column("status", width=100, anchor="center")
        self.tree.column("name", width=350, anchor="w")
        self.tree.column("category", width=160, anchor="w")
        self.tree.column("size", width=90, anchor="center")

        scroll = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.tree.tag_configure("enabled", foreground=GREEN_OK)
        self.tree.tag_configure("disabled", foreground=RED_OFF)
        self.tree.bind("<Double-1>", lambda e: self.on_toggle_selected())

        self.status_bar = tk.Label(page, text="", bg=BG_DARK, fg=TEXT_SUB,
                                    font=FONT_SUB, anchor="w")
        self.status_bar.pack(fill="x", padx=26, pady=(0, 10))

    # -- page: Settings -----------------------------------------------
    def _build_settings_page(self):
        page = tk.Frame(self.main_area, bg=BG_DARK)
        self.pages["settings"] = page

        tk.Label(page, text="Settings", bg=BG_DARK, fg=TEXT_MAIN,
                 font=FONT_TITLE).pack(anchor="w", padx=24, pady=(22, 16))

        card = tk.Frame(page, bg=BG_CARD, padx=18, pady=18)
        card.pack(fill="x", padx=24)

        tk.Label(card, text="Team Fortress 2 tf/custom folder",
                 bg=BG_CARD, fg=TEXT_MAIN, font=FONT_BODY).pack(anchor="w")

        row = tk.Frame(card, bg=BG_CARD)
        row.pack(fill="x", pady=(8, 4))
        self.path_var = tk.StringVar(value=str(self.manager.custom_dir) if self.manager else "")
        entry = tk.Entry(row, textvariable=self.path_var, bg=BG_DARK, fg=TEXT_MAIN,
                          insertbackground=TEXT_MAIN, relief="flat", font=FONT_MONO)
        entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        AccentButton(row, "Browse", self.on_browse_folder, kind="ghost").pack(side="left")
        AccentButton(row, "Auto-detect", self.on_autodetect_click).pack(side="left", padx=(8, 0))

        tk.Label(card, text="Example: .../Steam/steamapps/common/Team Fortress 2/tf/custom",
                 bg=BG_CARD, fg=TEXT_SUB, font=FONT_SUB).pack(anchor="w", pady=(4, 0))

        AccentButton(card, "Save", self.on_save_path).pack(anchor="w", pady=(14, 0))

    # -- callbacks --------------------------------------------------------
    def _try_autodetect(self):
        found = TF2Locator.find_tf2_custom()
        if found:
            self.manager = VPKManager(found)
            self.config_data["tf2_custom_dir"] = str(found)
            self._save_config()
            self.path_var.set(str(found))
            self.refresh_list()
        else:
            self._show_page("settings")
            messagebox.showinfo(
                APP_NAME,
                "Couldn't auto-detect Team Fortress 2.\n"
                "Please select the tf/custom folder manually in Settings."
            )

    def on_autodetect_click(self):
        found = TF2Locator.find_tf2_custom()
        if found:
            self.path_var.set(str(found))
        else:
            messagebox.showwarning(APP_NAME, "No TF2 installation found.")

    def on_browse_folder(self):
        chosen = filedialog.askdirectory(title="Select the tf/custom folder")
        if chosen:
            self.path_var.set(chosen)

    def on_save_path(self):
        path = Path(self.path_var.get().strip())
        if not path.exists():
            create = messagebox.askyesno(
                APP_NAME, f"This folder doesn't exist:\n{path}\n\nCreate it?"
            )
            if create:
                path.mkdir(parents=True, exist_ok=True)
            else:
                return
        self.manager = VPKManager(path)
        self.config_data["tf2_custom_dir"] = str(path)
        self._save_config()
        messagebox.showinfo(APP_NAME, "Folder saved.")
        self._show_page("vpks")
        self.refresh_list()

    def refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        if not self.manager:
            self.path_label.config(text="(no folder configured)")
            self.status_bar.config(text="Set your TF2 folder in Settings.")
            return

        self.path_label.config(text=str(self.manager.custom_dir))
        all_items = self.manager.list_vpks()

        chosen_filter = self.category_filter_var.get()
        items = all_items if chosen_filter == "All" else [
            i for i in all_items if i["category"] == chosen_filter
        ]

        for item in items:
            status = "Enabled" if item["enabled"] else "Disabled"
            tag = "enabled" if item["enabled"] else "disabled"
            self.tree.insert(
                "", "end", iid=str(item["path"]),
                values=(status, item["name"], item["category"],
                        human_size(item["size"])),
                tags=(tag,),
            )
        n_on = sum(1 for i in items if i["enabled"])
        self.status_bar.config(
            text=(f"{len(items)}/{len(all_items)} VPK(s) shown - "
                  f"{n_on} enabled, {len(items) - n_on} disabled.")
        )

    def _require_manager(self):
        if not self.manager:
            messagebox.showwarning(APP_NAME, "Set your TF2 folder first (Settings tab).")
            return False
        return True

    def on_add_vpk(self):
        if not self._require_manager():
            return
        paths = filedialog.askopenfilenames(
            title="Select one or more .vpk files",
            filetypes=[("VPK files", "*.vpk")],
        )
        errors = []
        for p in paths:
            try:
                self.manager.add_vpk(p)
            except Exception as exc:
                errors.append(f"{Path(p).name}: {exc}")
        self.refresh_list()
        if errors:
            messagebox.showerror(APP_NAME, "Errors:\n" + "\n".join(errors))
        elif paths:
            messagebox.showinfo(APP_NAME, f"{len(paths)} VPK(s) added.")

    def on_toggle_selected(self):
        if not self._require_manager():
            return
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo(APP_NAME, "Select at least one VPK.")
            return
        for iid in selection:
            entry_path = Path(iid)
            currently_enabled = entry_path.suffix == ".vpk"
            self.manager.set_enabled(entry_path, not currently_enabled)
        self.refresh_list()

    def on_remove_selected(self):
        if not self._require_manager():
            return
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo(APP_NAME, "Select at least one VPK.")
            return
        names = [self.tree.item(iid, "values")[1] for iid in selection]
        if not messagebox.askyesno(
            APP_NAME, "Permanently delete:\n" + "\n".join(names) + "?"
        ):
            return
        for iid in selection:
            self.manager.remove(Path(iid))
        self.refresh_list()

    def on_open_folder(self):
        if not self._require_manager():
            return
        self.manager.custom_dir.mkdir(parents=True, exist_ok=True)
        open_in_explorer(self.manager.custom_dir)


if __name__ == "__main__":
    app = TF2VPKApp()
    app.mainloop()
