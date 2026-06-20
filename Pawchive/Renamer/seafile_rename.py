import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import re
from pathlib import Path

# ── Seafile / Windows-compatible filename rules ───────────────────────────────
# Source: Seafile vc-utils.c + Windows MSDN naming rules
# Characters that are illegal on Windows (and therefore in Seafile):
# \ : * ? " < > | /   plus control chars 0-31 (incl. tab, backspace)
ILLEGAL_CHARS  = r'\/:*?"<>|'
CONTROL_RE     = re.compile(r'[\x00-\x1f]')
RESERVED_NAMES = {
    "CON","PRN","AUX","NUL",
    "COM0","COM1","COM2","COM3","COM4","COM5","COM6","COM7","COM8","COM9",
    "LPT0","LPT1","LPT2","LPT3","LPT4","LPT5","LPT6","LPT7","LPT8","LPT9",
}
MAX_BYTES = 255   # Linux ext4 / NTFS filename byte limit

def _truncate_to_bytes(s: str, limit: int) -> str:
    """Shorten string so it fits within `limit` UTF-8 bytes."""
    encoded = s.encode("utf-8")
    if len(encoded) <= limit:
        return s
    # Slice bytes then decode safely (avoid cutting in the middle of a multibyte char)
    return encoded[:limit].decode("utf-8", errors="ignore").rstrip()

def sanitize_name(name: str) -> tuple[str, list[str]]:
    """
    Return (new_name, list_of_reasons) for a Seafile-safe filename.
    reasons is empty when nothing changed.
    """
    reasons = []
    result = name

    # 1. Illegal characters
    for ch in ILLEGAL_CHARS:
        if ch in result:
            result = result.replace(ch, " ")
            reasons.append(f"removed '{ch}'")

    # 2. Control characters (tab, backspace, 0x00–0x1f)
    if CONTROL_RE.search(result):
        result = CONTROL_RE.sub(" ", result)
        reasons.append("removed control chars")

    # 3. Collapse multiple spaces / underscores
    cleaned = re.sub(r" {2,}", " ", result).strip()
    if cleaned != result:
        reasons.append("collapsed spaces")
    result = cleaned

    # 4. Trailing dots and spaces (Windows rule)
    stripped = result.rstrip(". ")
    if stripped != result:
        reasons.append("removed trailing dots/spaces")
    result = stripped

    # 5. Leading dots (hidden files on Linux-based Seafile servers)
    if result.startswith(".") and len(result) > 1:
        result = result.lstrip(".")
        reasons.append("removed leading dot")

    # 6. Reserved Windows names (checked on stem only)
    p = Path(result)
    if p.stem.upper() in RESERVED_NAMES:
        result = "_" + result
        reasons.append(f"reserved name '{p.stem}'")

    # 7. Filename length (255-byte limit for Linux ext4 / most filesystems)
    encoded_len = len(result.encode("utf-8"))
    if encoded_len > MAX_BYTES:
        p2 = Path(result)
        ext = p2.suffix                          # e.g. ".wav"
        stem = p2.stem
        allowed = MAX_BYTES - len(ext.encode("utf-8"))
        stem = _truncate_to_bytes(stem, allowed).rstrip(" .")
        result = stem + ext
        reasons.append(f"truncated ({encoded_len} → {len(result.encode('utf-8'))} bytes)")

    # 8. Fallback for empty result
    if not result or result == ".":
        result = "_unnamed" + Path(name).suffix
        reasons.append("was empty after cleaning")

    return result, reasons

def needs_rename(name: str) -> bool:
    new, _ = sanitize_name(name)
    return new != name

def collect_files(folder: str):
    """Walk folder; return list of (abs_path, old_name, new_name, reasons, kind)."""
    results = []
    for root, dirs, files in os.walk(folder, topdown=True):
        for d in dirs:
            new, reasons = sanitize_name(d)
            if new != d:
                results.append((os.path.join(root, d), d, new, reasons, "folder"))
        for f in files:
            new, reasons = sanitize_name(f)
            if new != f:
                results.append((os.path.join(root, f), f, new, reasons, "file"))
    return results

def apply_renames(items):
    # Deepest paths first so children are renamed before their parents
    items_sorted = sorted(items, key=lambda x: x[0].count(os.sep), reverse=True)
    success = errors = 0
    for abs_path, old, new, reasons, kind in items_sorted:
        dest = os.path.join(os.path.dirname(abs_path), new)
        counter = 1
        base = dest
        base_p = Path(base)
        while os.path.exists(dest) and dest.lower() != abs_path.lower():
            stem = base_p.stem if kind == "file" else base_p.name
            suf  = base_p.suffix if kind == "file" else ""
            dest = os.path.join(os.path.dirname(abs_path), f"{stem} ({counter}){suf}")
            counter += 1
        try:
            os.rename(abs_path, dest)
            success += 1
        except Exception:
            errors += 1
    return success, errors

# ── GUI ───────────────────────────────────────────────────────────────────────
class App(tk.Tk):
    BG   = "#111117"
    CARD = "#1a1a24"
    BRD  = "#2a2a3a"
    FG   = "#e2e0f0"
    DIM  = "#6b6888"
    ACC  = "#a78bfa"
    GRN  = "#6ee7b7"
    WARN = "#fbbf24"
    FONT = ("Segoe UI", 10)

    def __init__(self):
        super().__init__()
        self.title("Seafile Filename Sanitizer")
        self.configure(bg=self.BG)
        self.minsize(820, 540)
        self._items = []
        self._build()
        self._center()

    def _center(self):
        self.update_idletasks()
        w, h = 920, 620
        x = (self.winfo_screenwidth()  - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _build(self):
        P = 16

        hdr = tk.Frame(self, bg="#0d0d14", pady=P)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Seafile Filename Sanitizer",
                 font=("Segoe UI", 15, "bold"), bg="#0d0d14", fg=self.ACC).pack()
        tk.Label(hdr,
                 text="Scans a folder and renames files so they can be uploaded to Seafile",
                 font=("Segoe UI", 9), bg="#0d0d14", fg=self.DIM).pack()

        body = tk.Frame(self, bg=self.BG, padx=P*2, pady=P)
        body.pack(fill="both", expand=True)

        # Folder row
        tk.Label(body, text="Folder to scan", font=("Segoe UI", 9, "bold"),
                 bg=self.BG, fg=self.DIM).pack(anchor="w", pady=(0, 4))
        row = tk.Frame(body, bg=self.BG)
        row.pack(fill="x", pady=(0, P))
        self.folder_var = tk.StringVar()
        tk.Entry(row, textvariable=self.folder_var,
                 bg=self.CARD, fg=self.FG, insertbackground=self.FG,
                 relief="flat", font=self.FONT,
                 highlightthickness=1, highlightbackground=self.BRD,
                 highlightcolor=self.ACC).pack(
                     side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        self._btn(row, "Browse…", self._browse, accent=False).pack(side="left")
        self._btn(row, "Scan",    self._scan,   accent=True).pack(side="left", padx=(8, 0))

        # Info strip
        self.info_var = tk.StringVar(value="No folder scanned yet.")
        tk.Label(body, textvariable=self.info_var, font=("Segoe UI", 9),
                 bg=self.BG, fg=self.DIM, anchor="w").pack(fill="x", pady=(0, 8))

        # Table
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Sea.Treeview",
                        background=self.CARD, fieldbackground=self.CARD,
                        foreground=self.FG, rowheight=26, font=("Segoe UI", 9))
        style.configure("Sea.Treeview.Heading",
                        background=self.BRD, foreground=self.DIM,
                        font=("Segoe UI", 9, "bold"), relief="flat")
        style.map("Sea.Treeview",
                  background=[("selected", "#2d2a4a")],
                  foreground=[("selected", self.FG)])

        cols = ("type", "original_name", "new_name", "reason")
        frame = tk.Frame(body, bg=self.BRD)
        frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(frame, columns=cols, show="headings",
                                  style="Sea.Treeview", selectmode="none")
        self.tree.heading("type",          text="")
        self.tree.heading("original_name", text="Original name")
        self.tree.heading("new_name",      text="New name")
        self.tree.heading("reason",        text="What was changed")
        self.tree.column("type",          width=36,  anchor="center", stretch=False)
        self.tree.column("original_name", width=280, anchor="w")
        self.tree.column("new_name",      width=280, anchor="w")
        self.tree.column("reason",        width=280, anchor="w")

        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        # Footer
        foot = tk.Frame(self, bg="#0d0d14", padx=P*2, pady=P)
        foot.pack(fill="x", side="bottom")
        self.rename_btn = self._btn(foot, "✦  Rename all files now",
                                    self._rename, accent=True)
        self.rename_btn.pack(side="right")
        self.rename_btn.configure(state="disabled")
        tk.Label(foot,
                 text="Files are renamed in-place — make a backup first if unsure.",
                 font=("Segoe UI", 8), bg="#0d0d14", fg=self.DIM).pack(side="left", anchor="w")

    def _btn(self, parent, text, cmd, accent=True):
        bg = self.ACC if accent else self.BRD
        fg = self.BG  if accent else self.FG
        return tk.Button(parent, text=text, command=cmd,
                         bg=bg, fg=fg, activebackground=self.ACC,
                         activeforeground=self.BG, relief="flat",
                         font=("Segoe UI", 9, "bold") if accent else self.FONT,
                         padx=14, pady=6, cursor="hand2", bd=0)

    def _browse(self):
        d = filedialog.askdirectory(title="Choose folder to scan")
        if d:
            self.folder_var.set(d)

    def _scan(self):
        folder = self.folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("No folder", "Please choose a valid folder first.")
            return

        self.tree.delete(*self.tree.get_children())
        self.info_var.set("Scanning…")
        self.update()

        self._items = collect_files(folder)

        if not self._items:
            self.info_var.set("✓  All filenames are already Seafile-compatible — nothing to rename.")
            self.rename_btn.configure(state="disabled")
            return

        for abs_path, old, new, reasons, kind in self._items:
            icon = "📁" if kind == "folder" else "📄"
            reason_str = ", ".join(reasons)
            self.tree.insert("", "end", values=(icon, old, new, reason_str))

        n = len(self._items)
        self.info_var.set(f"Found {n} item{'s' if n != 1 else ''} that need renaming. "
                         f"Check the 'What was changed' column to see why.")
        self.rename_btn.configure(state="normal")

    def _rename(self):
        if not self._items:
            return
        n = len(self._items)
        if not messagebox.askyesno(
            "Confirm rename",
            f"This will rename {n} item{'s' if n != 1 else ''} in-place.\n\n"
            "Make a backup if needed. Continue?"
        ):
            return

        ok, err = apply_renames(self._items)
        self.tree.delete(*self.tree.get_children())
        self._items = []
        self.rename_btn.configure(state="disabled")

        if err:
            self.info_var.set(f"Done — {ok} renamed, {err} failed (check permissions).")
            messagebox.showwarning("Partial success",
                f"{ok} renamed.\n{err} could not be renamed — check no files are open.")
        else:
            self.info_var.set(f"✓  Done — {ok} file{'s' if ok != 1 else ''} renamed successfully.")
            messagebox.showinfo("All done", f"{ok} file(s) renamed.\nYour folder is ready for Seafile.")

if __name__ == "__main__":
    App().mainloop()
