"""
WAV to MP3 Batch Converter
Requires: pip install pydub
Requires: ffmpeg installed and on PATH (see instructions in the app)
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import subprocess
import shutil
import os
import sys
from pathlib import Path

# ── Helpers ───────────────────────────────────────────────────────────────────

def find_ffmpeg() -> str | None:
    """Return path to ffmpeg executable, or None if not found."""
    return shutil.which("ffmpeg")

def find_wav_files(folder: str) -> list[Path]:
    results = []
    for root, _, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(".wav"):
                results.append(Path(root) / f)
    return results

def convert_file(ffmpeg: str, src: Path, dst: Path, bitrate: str) -> str | None:
    """
    Convert src (.wav) to dst (.mp3) using ffmpeg.
    Returns None on success, error string on failure.
    """
    cmd = [
        ffmpeg, "-y",           # overwrite without asking
        "-i", str(src),
        "-codec:a", "libmp3lame",
        "-b:a", bitrate,
        "-id3v2_version", "3",
        str(dst),
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
        )
        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="replace").strip()
            # Return last non-empty line as error summary
            lines = [l for l in err.splitlines() if l.strip()]
            return lines[-1] if lines else "Unknown ffmpeg error"
        return None
    except subprocess.TimeoutExpired:
        return "Timed out (file may be very large)"
    except Exception as e:
        return str(e)

# ── GUI ───────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    BG   = "#111117"
    CARD = "#1a1a24"
    BRD  = "#2a2a3a"
    FG   = "#e2e0f0"
    DIM  = "#6b6888"
    ACC  = "#f9a8d4"   # rose pink — audio/music feel
    GRN  = "#6ee7b7"
    RED  = "#f87171"
    FONT = ("Segoe UI", 10)

    BITRATES = ["320k", "256k", "192k", "128k", "96k"]

    def __init__(self):
        super().__init__()
        self.title("WAV → MP3 Converter")
        self.configure(bg=self.BG)
        self.minsize(680, 520)
        self._running = False
        self._build()
        self._center()
        self._check_ffmpeg()

    def _center(self):
        self.update_idletasks()
        w, h = 760, 600
        x = (self.winfo_screenwidth()  - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ── Layout ────────────────────────────────────────────────────────────────
    def _build(self):
        P = 16

        # Header
        hdr = tk.Frame(self, bg="#0d0d14", pady=P)
        hdr.pack(fill="x")
        tk.Label(hdr, text="WAV → MP3 Batch Converter",
                 font=("Segoe UI", 15, "bold"), bg="#0d0d14", fg=self.ACC).pack()
        tk.Label(hdr, text="Converts every .wav file in a folder to .mp3",
                 font=("Segoe UI", 9), bg="#0d0d14", fg=self.DIM).pack()

        body = tk.Frame(self, bg=self.BG, padx=P*2, pady=P)
        body.pack(fill="both", expand=True)

        # ffmpeg status banner
        self.ffmpeg_banner = tk.Label(
            body, text="", font=("Segoe UI", 9),
            bg="#2a1a1a", fg=self.RED, anchor="w", padx=10, pady=6)
        self.ffmpeg_banner.pack(fill="x", pady=(0, P))

        # Source folder
        self._section(body, "Source folder (contains your .wav files)")
        src_row = tk.Frame(body, bg=self.BG)
        src_row.pack(fill="x", pady=(0, P))
        self.src_var = tk.StringVar()
        self._entry(src_row, self.src_var).pack(side="left", fill="x", expand=True, padx=(0,8))
        self._btn(src_row, "Browse…", self._browse_src, accent=False).pack(side="left")

        # Output folder
        self._section(body, "Output folder  (leave blank to save next to originals)")
        dst_row = tk.Frame(body, bg=self.BG)
        dst_row.pack(fill="x", pady=(0, P))
        self.dst_var = tk.StringVar()
        self._entry(dst_row, self.dst_var).pack(side="left", fill="x", expand=True, padx=(0,8))
        self._btn(dst_row, "Browse…", self._browse_dst, accent=False).pack(side="left")

        # Options row
        opt_row = tk.Frame(body, bg=self.BG)
        opt_row.pack(fill="x", pady=(0, P))

        tk.Label(opt_row, text="MP3 quality:", font=("Segoe UI", 9),
                 bg=self.BG, fg=self.DIM).pack(side="left")

        self.bitrate_var = tk.StringVar(value="192k")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Acc.TCombobox",
                        fieldbackground=self.CARD, background=self.CARD,
                        foreground=self.FG, selectbackground=self.BRD,
                        arrowcolor=self.ACC)
        cb = ttk.Combobox(opt_row, textvariable=self.bitrate_var,
                          values=self.BITRATES, state="readonly",
                          style="Acc.TCombobox", width=8,
                          font=self.FONT)
        cb.pack(side="left", padx=(6, 20))

        self.delete_var = tk.BooleanVar(value=False)
        tk.Checkbutton(opt_row, text="Delete original .wav after conversion",
                       variable=self.delete_var,
                       bg=self.BG, fg=self.DIM, selectcolor=self.CARD,
                       activebackground=self.BG, activeforeground=self.FG,
                       font=("Segoe UI", 9)).pack(side="left")

        # Convert button + progress
        self.conv_btn = self._btn(body, "▶  Convert all WAV files",
                                  self._start, accent=True)
        self.conv_btn.pack(fill="x", pady=(0, 8))

        style.configure("Acc.Horizontal.TProgressbar",
                        troughcolor=self.BRD, background=self.ACC, thickness=8)
        self.progress = ttk.Progressbar(body, mode="determinate",
                                         style="Acc.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(0, P))

        # Log
        log_frame = tk.Frame(body, bg=self.BG)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, bg=self.CARD, fg=self.FG,
                           font=("Consolas", 9), wrap="word",
                           state="disabled", relief="flat",
                           insertbackground=self.FG, selectbackground=self.BRD)
        sb = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

    def _section(self, parent, text):
        tk.Label(parent, text=text, font=("Segoe UI", 9, "bold"),
                 bg=self.BG, fg=self.DIM).pack(anchor="w", pady=(0, 4))

    def _entry(self, parent, var):
        return tk.Entry(parent, textvariable=var,
                        bg=self.CARD, fg=self.FG, insertbackground=self.FG,
                        relief="flat", font=self.FONT,
                        highlightthickness=1, highlightbackground=self.BRD,
                        highlightcolor=self.ACC)

    def _btn(self, parent, text, cmd, accent=True):
        bg = self.ACC if accent else self.BRD
        fg = self.BG  if accent else self.FG
        return tk.Button(parent, text=text, command=cmd,
                         bg=bg, fg=fg, activebackground=self.ACC,
                         activeforeground=self.BG, relief="flat",
                         font=("Segoe UI", 9, "bold") if accent else self.FONT,
                         padx=14, pady=6, cursor="hand2", bd=0)

    # ── ffmpeg check ──────────────────────────────────────────────────────────
    def _check_ffmpeg(self):
        if find_ffmpeg():
            self.ffmpeg_banner.pack_forget()
        else:
            self.ffmpeg_banner.configure(
                text="⚠  ffmpeg not found.  "
                     "Download it from https://ffmpeg.org/download.html  →  "
                     "extract it  →  add the 'bin' folder to your PATH, then restart this app."
            )
            self.ffmpeg_banner.pack(fill="x", pady=(0, 16))
            self.conv_btn.configure(state="disabled")

    # ── Browse ────────────────────────────────────────────────────────────────
    def _browse_src(self):
        d = filedialog.askdirectory(title="Choose source folder")
        if d:
            self.src_var.set(d)

    def _browse_dst(self):
        d = filedialog.askdirectory(title="Choose output folder")
        if d:
            self.dst_var.set(d)

    # ── Logging ───────────────────────────────────────────────────────────────
    def _log(self, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ── Convert ───────────────────────────────────────────────────────────────
    def _start(self):
        if self._running:
            return
        src = self.src_var.get().strip()
        if not src or not os.path.isdir(src):
            messagebox.showwarning("No source", "Please choose a source folder.")
            return

        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            messagebox.showerror("ffmpeg missing",
                "ffmpeg was not found on your PATH.\n\n"
                "Download from https://ffmpeg.org/download.html\n"
                "Extract the zip, then add the 'bin' folder to your Windows PATH.")
            return

        dst_base = self.dst_var.get().strip() or None
        if dst_base and not os.path.isdir(dst_base):
            try:
                os.makedirs(dst_base, exist_ok=True)
            except Exception as e:
                messagebox.showerror("Bad output folder", str(e))
                return

        bitrate  = self.bitrate_var.get()
        delete   = self.delete_var.get()

        self._running = True
        self.conv_btn.configure(state="disabled", text="Converting…")
        self.progress["value"] = 0
        self.log.configure(state="normal"); self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

        def worker():
            wav_files = find_wav_files(src)
            if not wav_files:
                self.after(0, lambda: self._log("No .wav files found in that folder."))
                self.after(0, self._done)
                return

            total = len(wav_files)
            self.after(0, lambda: self._log(
                f"Found {total} .wav file(s).  Quality: {bitrate}\n{'─'*50}"))

            ok = skipped = errors = 0
            for i, wav_path in enumerate(wav_files, 1):
                # Determine output path
                if dst_base:
                    rel = wav_path.relative_to(src)
                    out = Path(dst_base) / rel.with_suffix(".mp3")
                    out.parent.mkdir(parents=True, exist_ok=True)
                else:
                    out = wav_path.with_suffix(".mp3")

                label = wav_path.name
                self.after(0, lambda l=label, n=i, t=total:
                           self._log(f"[{n}/{t}] {l}"))
                self.after(0, lambda v=i/total*100:
                           self.progress.configure(value=v))

                # Skip if output already exists
                if out.exists():
                    self.after(0, lambda: self._log("    → already exists, skipped"))
                    skipped += 1
                    continue

                err = convert_file(ffmpeg, wav_path, out, bitrate)
                if err:
                    self.after(0, lambda e=err: self._log(f"    ERROR: {e}"))
                    errors += 1
                    # Remove incomplete output if it was created
                    if out.exists():
                        try: out.unlink()
                        except: pass
                else:
                    size_mb = out.stat().st_size / 1_048_576
                    self.after(0, lambda s=size_mb: self._log(f"    → {s:.1f} MB"))
                    ok += 1
                    if delete:
                        try:
                            wav_path.unlink()
                        except Exception as e:
                            self.after(0, lambda e=e: self._log(
                                f"    (could not delete original: {e})"))

            summary = (f"\n{'─'*50}\n"
                       f"✓ Done!  Converted: {ok}  "
                       f"Skipped: {skipped}  Errors: {errors}")
            self.after(0, lambda: self._log(summary))
            self.after(0, self._done)

        threading.Thread(target=worker, daemon=True).start()

    def _done(self):
        self._running = False
        self.conv_btn.configure(state="normal", text="▶  Convert all WAV files")

if __name__ == "__main__":
    App().mainloop()
