"""
Windows Firewall App Blocker
=============================
A GUI tool that scans a folder (and subfolders) for executable files and
creates Windows Defender Firewall rules (both inbound AND outbound) to
block them from accessing the network.

Requirements:
    - Windows 10/11
    - Python 3.8+ (uses only the standard library: tkinter, subprocess, etc.)
    - Must be run as Administrator (the script will try to elevate itself)

Run it with:
    python firewall_blocker.py

This script must be run on Windows. It will not work on macOS/Linux since
it relies on the `netsh advfirewall` command, which is Windows-only.
"""

import ctypes
import hashlib
import json
import os
import subprocess
import sys
import threading
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

# Extensions treated as "executable" / scannable.
# Note: .dll and .jar are included per request, but they do NOT run as their
# own process (.dll is loaded by a host process, .jar is run by java.exe),
# so a firewall "program=" rule pointed at them will typically have no
# effect. The app still lets you create rules for them, but flags this in
# the log so you aren't surprised if it doesn't actually stop traffic.
EXECUTABLE_EXTENSIONS = {
    ".exe", ".com", ".bat", ".cmd", ".msi", ".ps1",
    ".vbs", ".scr", ".pif", ".jar", ".dll",
}

NO_STANDALONE_PROCESS_EXTENSIONS = {".dll", ".jar"}

RULE_PREFIX = "PyFWBlock"  # used to namespace/find rules this tool creates
STORE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "blocked_apps_store.json"
)

# --------------------------------------------------------------------------
# Admin elevation
# --------------------------------------------------------------------------

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    """Re-launch this script with a UAC elevation prompt, then exit."""
    script = os.path.abspath(sys.argv[0])
    params = " ".join(f'"{a}"' for a in sys.argv[1:])
    try:
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, f'"{script}" {params}', None, 1
        )
    except Exception as e:
        messagebox.showerror(
            "Elevation failed",
            f"Could not restart as Administrator automatically.\n\n"
            f"Please right-click the script / your terminal and choose "
            f"'Run as administrator'.\n\nDetails: {e}",
        )
    sys.exit(0)


# --------------------------------------------------------------------------
# Persistent store (so the Manage tab still knows what we blocked & when,
# across restarts)
# --------------------------------------------------------------------------

def load_store() -> dict:
    if os.path.exists(STORE_FILE):
        try:
            with open(STORE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_store(store: dict):
    try:
        with open(STORE_FILE, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
    except Exception:
        pass


# --------------------------------------------------------------------------
# Firewall operations (netsh advfirewall)
# --------------------------------------------------------------------------

def rule_base_name(path: str) -> str:
    """Stable, unique-ish rule name derived from the full file path."""
    h = hashlib.md5(path.lower().encode("utf-8")).hexdigest()[:10]
    base = os.path.basename(path)
    # Strip characters netsh dislikes in names, keep it readable.
    safe_base = "".join(c for c in base if c.isalnum() or c in "._-")[:40]
    return f"{RULE_PREFIX}_{safe_base}_{h}"


def run_netsh(args):
    """Run a netsh command, return CompletedProcess. Never raises."""
    try:
        return subprocess.run(
            ["netsh"] + args,
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
    except Exception as e:
        class _Fake:
            returncode = -1
            stdout = ""
            stderr = str(e)
        return _Fake()


def add_block_rules(path: str):
    """Create an inbound AND outbound block rule for the given file path."""
    base = rule_base_name(path)
    outcomes = []
    for direction in ("in", "out"):
        name = f"{base}_{direction}"
        proc = run_netsh([
            "advfirewall", "firewall", "add", "rule",
            f"name={name}",
            f"dir={direction}",
            "action=block",
            f"program={path}",
            "enable=yes",
            "profile=any",
        ])
        ok = proc.returncode == 0
        outcomes.append((direction, ok, (proc.stderr or proc.stdout).strip()))
    return outcomes


def delete_rules_for(path: str):
    base = rule_base_name(path)
    outcomes = []
    for direction in ("in", "out"):
        name = f"{base}_{direction}"
        proc = run_netsh([
            "advfirewall", "firewall", "delete", "rule", f"name={name}",
        ])
        ok = proc.returncode == 0
        outcomes.append((direction, ok, (proc.stderr or proc.stdout).strip()))
    return outcomes


def list_live_blocked_rules():
    """
    Query Windows Firewall for every rule this tool created (by name
    prefix) and return {path: {"in": bool, "out": bool}}.
    Cross-referenced against the persistent store to recover the original
    path (the prefix-based rule name doesn't contain the full path).
    """
    store = load_store()
    result = {}

    proc = run_netsh(["advfirewall", "firewall", "show", "rule", "name=all"])
    output = proc.stdout or ""

    live_rule_names = set()
    for line in output.splitlines():
        line = line.strip()
        if line.lower().startswith("rule name:"):
            name = line.split(":", 1)[1].strip()
            if name.startswith(RULE_PREFIX):
                live_rule_names.add(name)

    for path, meta in store.items():
        base = rule_base_name(path)
        in_present = f"{base}_in" in live_rule_names
        out_present = f"{base}_out" in live_rule_names
        if in_present or out_present:
            result[path] = {
                "in": in_present,
                "out": out_present,
                "date_blocked": meta.get("date_blocked", "unknown"),
            }
    return result


# --------------------------------------------------------------------------
# Folder scanning
# --------------------------------------------------------------------------

def scan_folder(folder: str, stop_flag=None):
    """Walk folder recursively, yield matching file paths."""
    found = []
    for root, _dirs, files in os.walk(folder):
        if stop_flag and stop_flag.is_set():
            break
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext in EXECUTABLE_EXTENSIONS:
                full = os.path.join(root, fname)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                found.append((full, ext, size))
    return found


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------

class FirewallBlockerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Windows Firewall App Blocker")
        self.root.geometry("900x620")

        self.checked_items = {}  # iid -> bool
        self.scan_results = {}   # iid -> (path, ext, size)

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self.scan_tab = ttk.Frame(notebook)
        self.manage_tab = ttk.Frame(notebook)
        notebook.add(self.scan_tab, text="Scan && Block")
        notebook.add(self.manage_tab, text="Manage Blocked Apps")

        self._build_scan_tab()
        self._build_manage_tab()

        if not is_admin():
            self.log(
                "WARNING: Not running as Administrator. Firewall rule "
                "changes will fail. Restart as Administrator to proceed."
            )

        self.refresh_blocked_list()

    # ---------------- Scan & Block tab ----------------

    def _build_scan_tab(self):
        frame = self.scan_tab

        top = ttk.Frame(frame)
        top.pack(fill="x", padx=6, pady=6)

        ttk.Label(top, text="Folder to scan:").pack(side="left")
        self.folder_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.folder_var, width=70)
        entry.pack(side="left", padx=6)
        ttk.Button(top, text="Browse...", command=self.browse_folder).pack(side="left")
        self.scan_btn = ttk.Button(top, text="Scan", command=self.start_scan)
        self.scan_btn.pack(side="left", padx=6)

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(btn_row, text="Select All", command=lambda: self.set_all_checks(True)).pack(side="left")
        ttk.Button(btn_row, text="Deselect All", command=lambda: self.set_all_checks(False)).pack(side="left", padx=6)
        self.block_btn = ttk.Button(
            btn_row, text="Block Selected (Inbound + Outbound)",
            command=self.start_block_selected,
        )
        self.block_btn.pack(side="right")

        columns = ("checked", "path", "ext", "size")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        self.tree.heading("checked", text="✓")
        self.tree.heading("path", text="File path")
        self.tree.heading("ext", text="Type")
        self.tree.heading("size", text="Size")
        self.tree.column("checked", width=30, anchor="center", stretch=False)
        self.tree.column("path", width=560)
        self.tree.column("ext", width=70, anchor="center", stretch=False)
        self.tree.column("size", width=90, anchor="e", stretch=False)
        self.tree.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.tree.bind("<Button-1>", self.on_tree_click)

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(frame, textvariable=self.status_var).pack(fill="x", padx=6)

        log_frame = ttk.LabelFrame(frame, text="Log")
        log_frame.pack(fill="both", expand=False, padx=6, pady=6)
        self.log_text = tk.Text(log_frame, height=8, wrap="word", state="disabled")
        self.log_text.pack(fill="both", expand=True)

    def browse_folder(self):
        path = filedialog.askdirectory()
        if path:
            self.folder_var.set(path)

    def log(self, msg: str):
        self.log_text.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {msg}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def start_scan(self):
        folder = self.folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("Invalid folder", "Please choose a valid folder to scan.")
            return
        self.scan_btn.configure(state="disabled")
        self.status_var.set("Scanning...")
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.checked_items.clear()
        self.scan_results.clear()

        def worker():
            results = scan_folder(folder)
            self.root.after(0, lambda: self.populate_results(results))

        threading.Thread(target=worker, daemon=True).start()

    def populate_results(self, results):
        for path, ext, size in results:
            iid = self.tree.insert("", "end", values=("", path, ext, human_size(size)))
            self.checked_items[iid] = False
            self.scan_results[iid] = (path, ext, size)
        self.status_var.set(f"Found {len(results)} executable file(s).")
        self.scan_btn.configure(state="normal")
        self.log(f"Scan complete: {len(results)} file(s) found.")

    def on_tree_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        col = self.tree.identify_column(event.x)
        row = self.tree.identify_row(event.y)
        if region == "cell" and col == "#1" and row:
            self.toggle_check(row)

    def toggle_check(self, iid):
        new_val = not self.checked_items.get(iid, False)
        self.checked_items[iid] = new_val
        vals = list(self.tree.item(iid, "values"))
        vals[0] = "✔" if new_val else ""
        self.tree.item(iid, values=vals)

    def set_all_checks(self, value: bool):
        for iid in self.tree.get_children():
            self.checked_items[iid] = value
            vals = list(self.tree.item(iid, "values"))
            vals[0] = "✔" if value else ""
            self.tree.item(iid, values=vals)

    def start_block_selected(self):
        if not is_admin():
            messagebox.showerror(
                "Administrator required",
                "This program must be run as Administrator to modify "
                "Windows Firewall rules.",
            )
            return

        selected_paths = [
            self.scan_results[iid][0]
            for iid, checked in self.checked_items.items()
            if checked
        ]
        if not selected_paths:
            messagebox.showinfo("Nothing selected", "Check at least one file to block.")
            return

        if not messagebox.askyesno(
            "Confirm",
            f"Create inbound + outbound BLOCK rules for {len(selected_paths)} "
            f"file(s)? This will prevent them from making or receiving "
            f"any network connections.",
        ):
            return

        self.block_btn.configure(state="disabled")
        self.status_var.set("Applying firewall rules...")

        def worker():
            store = load_store()
            ok_count = 0
            for path in selected_paths:
                ext = os.path.splitext(path)[1].lower()
                outcomes = add_block_rules(path)
                success = all(ok for _, ok, _ in outcomes)
                for direction, ok, err in outcomes:
                    status = "OK" if ok else f"FAILED ({err})"
                    self.root.after(0, lambda d=direction, p=path, s=status: self.log(
                        f"[{d.upper()}] {p} -> {s}"
                    ))
                if success:
                    ok_count += 1
                    store[path] = {"date_blocked": datetime.now().isoformat(timespec="seconds")}
                if ext in NO_STANDALONE_PROCESS_EXTENSIONS:
                    self.root.after(0, lambda p=path, e=ext: self.log(
                        f"NOTE: {p} is a .{e.lstrip('.')} file. It does not run as its "
                        f"own process, so this rule may not actually stop its traffic "
                        f"-- consider blocking the host executable that loads/runs it "
                        f"instead (e.g. java.exe for .jar, or the app that loads the .dll)."
                    ))
            save_store(store)
            self.root.after(0, lambda: self.finish_block(ok_count, len(selected_paths)))

        threading.Thread(target=worker, daemon=True).start()

    def finish_block(self, ok_count, total):
        self.status_var.set(f"Blocked {ok_count}/{total} file(s).")
        self.block_btn.configure(state="normal")
        self.refresh_blocked_list()
        messagebox.showinfo("Done", f"Successfully blocked {ok_count} of {total} file(s).\nSee the log for details.")

    # ---------------- Manage Blocked Apps tab ----------------

    def _build_manage_tab(self):
        frame = self.manage_tab

        top = ttk.Frame(frame)
        top.pack(fill="x", padx=6, pady=6)
        ttk.Button(top, text="Refresh", command=self.refresh_blocked_list).pack(side="left")
        ttk.Button(top, text="Unblock Selected", command=self.unblock_selected).pack(side="left", padx=6)
        ttk.Button(top, text="Unblock All", command=self.unblock_all).pack(side="left")

        columns = ("path", "in", "out", "date")
        self.manage_tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        self.manage_tree.heading("path", text="File path")
        self.manage_tree.heading("in", text="Inbound")
        self.manage_tree.heading("out", text="Outbound")
        self.manage_tree.heading("date", text="Blocked on")
        self.manage_tree.column("path", width=520)
        self.manage_tree.column("in", width=80, anchor="center", stretch=False)
        self.manage_tree.column("out", width=80, anchor="center", stretch=False)
        self.manage_tree.column("date", width=160, anchor="center", stretch=False)
        self.manage_tree.pack(fill="both", expand=True, padx=6, pady=6)

        self.manage_status_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.manage_status_var).pack(fill="x", padx=6, pady=(0, 6))

    def refresh_blocked_list(self):
        for i in self.manage_tree.get_children():
            self.manage_tree.delete(i)
        live = list_live_blocked_rules()
        for path, info in live.items():
            self.manage_tree.insert(
                "", "end",
                values=(
                    path,
                    "Blocked" if info["in"] else "-",
                    "Blocked" if info["out"] else "-",
                    info.get("date_blocked", "unknown")[:19].replace("T", " "),
                ),
            )
        self.manage_status_var.set(f"{len(live)} app(s) currently blocked by this tool.")

    def unblock_selected(self):
        sel = self.manage_tree.selection()
        if not sel:
            messagebox.showinfo("Nothing selected", "Select one or more rows to unblock.")
            return
        paths = [self.manage_tree.item(iid, "values")[0] for iid in sel]
        self._unblock_paths(paths)

    def unblock_all(self):
        all_iids = self.manage_tree.get_children()
        if not all_iids:
            return
        if not messagebox.askyesno("Confirm", "Remove ALL firewall block rules created by this tool?"):
            return
        paths = [self.manage_tree.item(iid, "values")[0] for iid in all_iids]
        self._unblock_paths(paths)

    def _unblock_paths(self, paths):
        if not is_admin():
            messagebox.showerror("Administrator required", "Run this program as Administrator to modify firewall rules.")
            return

        def worker():
            store = load_store()
            for path in paths:
                outcomes = delete_rules_for(path)
                for direction, ok, err in outcomes:
                    status = "OK" if ok else f"FAILED ({err})"
                    self.root.after(0, lambda d=direction, p=path, s=status: self.log(
                        f"[UNBLOCK {d.upper()}] {p} -> {s}"
                    ))
                store.pop(path, None)
            save_store(store)
            self.root.after(0, self.refresh_blocked_list)

        threading.Thread(target=worker, daemon=True).start()


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main():
    if os.name != "nt":
        print("This tool only works on Windows (it relies on netsh advfirewall).")
        sys.exit(1)

    if not is_admin():
        # Try to relaunch elevated automatically.
        relaunch_as_admin()
        return

    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    app = FirewallBlockerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
