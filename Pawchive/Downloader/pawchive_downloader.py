import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import requests
import re
import os
import json
from pathlib import Path
from urllib.parse import urlparse, urljoin

# ── Windows 10 filename rules ─────────────────────────────────────────────────
ILLEGAL_CHARS = r'<>:"/\\|?*'
ILLEGAL_NAMES = {
    "CON","PRN","AUX","NUL",
    "COM1","COM2","COM3","COM4","COM5","COM6","COM7","COM8","COM9",
    "LPT1","LPT2","LPT3","LPT4","LPT5","LPT6","LPT7","LPT8","LPT9",
}
MAX_FILENAME = 200   # leave room for path; Windows hard limit is 255 per component

AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".opus", ".wma"}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (compatible; PawchiveDL/1.0)"})

# ── Sanitisation ──────────────────────────────────────────────────────────────
def sanitize(name: str, ext: str) -> str:
    """Return a safe Windows filename (without extension) from a title."""
    # Strip / replace illegal characters
    clean = re.sub(f"[{re.escape(ILLEGAL_CHARS)}]", "_", name)
    # Collapse runs of whitespace / underscores
    clean = re.sub(r"[\s_]+", " ", clean).strip(" ._")
    # Reserved names
    if clean.upper() in ILLEGAL_NAMES:
        clean = "_" + clean
    # Truncate so that "name + ext" stays ≤ MAX_FILENAME
    max_stem = MAX_FILENAME - len(ext)
    if len(clean) > max_stem:
        clean = clean[:max_stem].rstrip(" ._")
    # Final fallback
    if not clean:
        clean = "untitled"
    return clean

# ── Pawchive API helpers ──────────────────────────────────────────────────────
def parse_profile_url(url: str):
    """Return (service, user_id) from a profile URL or raise ValueError."""
    url = url.strip().rstrip("/")
    # Accept: https://pawchive.st/patreon/user/12345  or  patreon/user/12345
    m = re.search(r"(patreon|fanbox)/user/(\d+)", url, re.I)
    if not m:
        raise ValueError(
            "URL must look like:\n"
            "  https://pawchive.st/patreon/user/12345\n"
            "  https://pawchive.st/fanbox/user/12345"
        )
    return m.group(1).lower(), m.group(2)

def fetch_posts(service: str, user_id: str, log):
    """Yield post dicts by walking the paginated API."""
    offset = 0
    while True:
        api = f"https://pawchive.st/api/v1/{service}/user/{user_id}?o={offset}"
        log(f"  Fetching post list (offset {offset}) …")
        r = SESSION.get(api, timeout=30)
        r.raise_for_status()
        posts = r.json()
        if not posts:
            break
        yield from posts
        if len(posts) < 50:          # Kemono returns 50 per page
            break
        offset += 50

def collect_audio_links(post: dict):
    """Return list of (url, original_filename) from a post's attachments + file."""
    links = []
    # Main file
    f = post.get("file") or {}
    if f.get("path"):
        ext = Path(f["path"]).suffix.lower()
        if ext in AUDIO_EXTS:
            name = f.get("name", "")
            url = f"https://file.pawchive.st/data{f['path']}"
            if name:
                from urllib.parse import quote
                url += f"?f={quote(name)}"
            links.append((url, name))
    # Attachments
    for a in post.get("attachments") or []:
        if not a.get("path"):
            continue
        ext = Path(a["path"]).suffix.lower()
        if ext in AUDIO_EXTS:
            name = a.get("name", "")
            url = f"https://file.pawchive.st/data{a['path']}"
            if name:
                from urllib.parse import quote
                url += f"?f={quote(name)}"
            links.append((url, name))
    return links

# ── Download worker ───────────────────────────────────────────────────────────
def download_all(profile_url: str, out_dir: str, log, set_progress, done_cb):
    try:
        service, user_id = parse_profile_url(profile_url)
        log(f"Service: {service}  |  User ID: {user_id}")
        log(f"Saving to: {out_dir}\n")

        os.makedirs(out_dir, exist_ok=True)
        all_posts = list(fetch_posts(service, user_id, log))
        log(f"Found {len(all_posts)} posts total.\n")

        # Collect everything first so we can show progress
        tasks = []  # (url, orig_ext, safe_stem)
        for post in all_posts:
            title = (post.get("title") or "untitled").strip()
            links = collect_audio_links(post)
            for idx, (url, orig_name) in enumerate(links):
                # Strip query string before reading extension
                ext = Path(url.split("?")[0]).suffix.lower() or ".mp3"
                stem = sanitize(title, ext)
                if len(links) > 1:
                    stem = sanitize(f"{title} ({idx+1})", ext)
                tasks.append((url, ext, stem))

        if not tasks:
            log("⚠  No audio files found on this profile.")
            done_cb()
            return

        log(f"{len(tasks)} audio file(s) to download.\n")
        downloaded = skipped = errors = 0

        for i, (url, ext, stem) in enumerate(tasks, 1):
            dest = Path(out_dir) / f"{stem}{ext}"
            # Avoid overwriting: append counter if needed
            counter = 1
            base_stem = stem
            while dest.exists():
                stem = f"{base_stem} [{counter}]"
                dest = Path(out_dir) / f"{stem}{ext}"
                counter += 1

            log(f"[{i}/{len(tasks)}] {stem}{ext}")
            set_progress(i / len(tasks) * 100)
            try:
                with SESSION.get(url, stream=True, timeout=60) as r:
                    if r.status_code == 404:
                        log(f"    Skipped (not archived yet)")
                        skipped += 1
                        continue
                    r.raise_for_status()
                    with open(dest, "wb") as fh:
                        for chunk in r.iter_content(65536):
                            fh.write(chunk)
                downloaded += 1
            except Exception as e:
                log(f"    ERROR: {e}")
                errors += 1

        log(f"\n✓ Done!  Downloaded: {downloaded}  Skipped: {skipped}  Errors: {errors}")
    except Exception as e:
        log(f"\n✗ Fatal error: {e}")
    finally:
        done_cb()

# ── GUI ───────────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Pawchive Audio Downloader")
        self.resizable(True, True)
        self.minsize(620, 480)
        self._build_ui()
        self._center()

    def _center(self):
        self.update_idletasks()
        w, h = 680, 560
        x = (self.winfo_screenwidth()  - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _build_ui(self):
        PAD = 12
        BG  = "#1e1e2e"
        FG  = "#cdd6f4"
        ACC = "#89b4fa"
        INP = "#313244"
        BTN = "#585b70"

        self.configure(bg=BG)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TLabel",      background=BG, foreground=FG,  font=("Segoe UI", 10))
        style.configure("TEntry",      fieldbackground=INP, foreground=FG, insertcolor=FG)
        style.configure("TButton",     background=BTN, foreground=FG,  font=("Segoe UI", 10))
        style.map      ("TButton",     background=[("active", ACC)], foreground=[("active", BG)])
        style.configure("Accent.TButton", background=ACC, foreground=BG, font=("Segoe UI", 10, "bold"))
        style.map      ("Accent.TButton", background=[("active", "#74c7ec")], foreground=[("active", BG)])
        style.configure("TProgressbar", troughcolor=INP, background=ACC, thickness=8)

        # Header
        hdr = tk.Frame(self, bg="#181825", pady=PAD)
        hdr.pack(fill="x")
        tk.Label(hdr, text="🐾  Pawchive Audio Downloader",
                 font=("Segoe UI", 14, "bold"), bg="#181825", fg=ACC).pack()
        tk.Label(hdr, text="Downloads every audio file from a creator profile",
                 font=("Segoe UI", 9), bg="#181825", fg="#6c7086").pack()

        body = tk.Frame(self, bg=BG, padx=PAD*2, pady=PAD)
        body.pack(fill="both", expand=True)

        # Profile URL
        ttk.Label(body, text="Creator Profile URL").pack(anchor="w", pady=(PAD, 2))
        url_frame = tk.Frame(body, bg=BG)
        url_frame.pack(fill="x")
        self.url_var = tk.StringVar()
        ttk.Entry(url_frame, textvariable=self.url_var, font=("Segoe UI", 10)).pack(
            side="left", fill="x", expand=True)

        # Example hint
        hint = "e.g. https://pawchive.st/patreon/user/12345  or  https://pawchive.st/fanbox/user/67890"
        tk.Label(body, text=hint, bg=BG, fg="#6c7086", font=("Segoe UI", 8)).pack(anchor="w")

        # Output folder
        ttk.Label(body, text="Save to folder").pack(anchor="w", pady=(PAD, 2))
        dir_frame = tk.Frame(body, bg=BG)
        dir_frame.pack(fill="x")
        self.dir_var = tk.StringVar(value=str(Path.home() / "Downloads" / "PawchiveAudio"))
        ttk.Entry(dir_frame, textvariable=self.dir_var, font=("Segoe UI", 10)).pack(
            side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(dir_frame, text="Browse…", command=self._browse).pack(side="left")

        # Download button
        self.dl_btn = ttk.Button(body, text="▶  Start Download",
                                 style="Accent.TButton", command=self._start)
        self.dl_btn.pack(fill="x", pady=(PAD*2, PAD))

        # Progress
        self.progress = ttk.Progressbar(body, mode="determinate", style="TProgressbar")
        self.progress.pack(fill="x", pady=(0, PAD))

        # Log
        log_frame = tk.Frame(body, bg=BG)
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(
            log_frame, bg=INP, fg=FG, font=("Consolas", 9),
            wrap="word", state="disabled", relief="flat",
            insertbackground=FG, selectbackground=ACC
        )
        sb = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

        self._log("Ready. Paste a creator URL above and press Start Download.")

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.dir_var.get())
        if d:
            self.dir_var.set(d)

    def _log(self, msg: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_progress(self, val: float):
        self.progress["value"] = val

    def _start(self):
        url = self.url_var.get().strip()
        out = self.dir_var.get().strip()
        if not url:
            messagebox.showwarning("Missing URL", "Please enter a creator profile URL.")
            return
        if not out:
            messagebox.showwarning("Missing Folder", "Please choose a save folder.")
            return

        self.dl_btn.configure(state="disabled", text="Downloading…")
        self.progress["value"] = 0
        self._log("\n" + "─"*50)

        def done():
            self.after(0, lambda: self.dl_btn.configure(state="normal", text="▶  Start Download"))

        t = threading.Thread(
            target=download_all,
            args=(url, out,
                  lambda m: self.after(0, lambda m=m: self._log(m)),
                  lambda v: self.after(0, lambda v=v: self._set_progress(v)),
                  done),
            daemon=True,
        )
        t.start()


if __name__ == "__main__":
    App().mainloop()
