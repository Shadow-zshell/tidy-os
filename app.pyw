"""TIDY//OS - desktop control panel for the tidy scripts.

Double-click the desktop shortcut (or run `pythonw app.pyw`). Every button just
runs one of the scripts in this folder and streams its output into the console.
Nothing here deletes files.
"""

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs"
# pythonw has no console; child scripts run under python.exe with the window hidden
PYTHON = str(Path(sys.executable).with_name("python.exe"))
NO_WINDOW = 0x08000000
DEMO = "--demo" in sys.argv  # fake output for screenshots - nothing actually runs

# Flipper Zero orange on black, with cyberpunk accents
BG = "#050505"
PANEL = "#0e0e0e"
ORANGE = "#ff8200"
ORANGE_HOT = "#ffb04d"
CYAN = "#00f0ff"
MAGENTA = "#ff2a6d"
DIM = "#6b4a2a"
FONT = "Consolas"

BANNER = r"""
 ████████╗██╗██████╗ ██╗   ██╗    ██╗██╗ ██████╗ ███████╗
 ╚══██╔══╝██║██╔══██╗╚██╗ ██╔╝   ██╔╝██╔╝██╔═══██╗██╔════╝
    ██║   ██║██║  ██║ ╚████╔╝   ██╔╝██╔╝ ██║   ██║███████╗
    ██║   ██║██║  ██║  ╚██╔╝   ██╔╝██╔╝  ██║   ██║╚════██║
    ██║   ██║██████╔╝   ██║   ██╔╝██╔╝   ╚██████╔╝███████║
    ╚═╝   ╚═╝╚═════╝    ╚═╝   ╚═╝ ╚═╝     ╚═════╝ ╚══════╝"""


class NeonButton(tk.Frame):
    """A flat button with a glowing border that inverts on hover."""

    def __init__(self, parent, text, sub, command, color=ORANGE):
        super().__init__(parent, bg=color, padx=2, pady=2, cursor="hand2")
        self.color, self.command, self.enabled = color, command, True
        self.inner = tk.Frame(self, bg=PANEL)
        self.inner.pack(fill="both", expand=True)
        self.title = tk.Label(self.inner, text=text, bg=PANEL, fg=color,
                              font=(FONT, 13, "bold"), anchor="w")
        self.title.pack(fill="x", padx=12, pady=(8, 0))
        self.sub = tk.Label(self.inner, text=sub, bg=PANEL, fg=DIM,
                            font=(FONT, 9), anchor="w")
        self.sub.pack(fill="x", padx=12, pady=(0, 8))
        for w in (self, self.inner, self.title, self.sub):
            w.bind("<Enter>", self._hover)
            w.bind("<Leave>", self._unhover)
            w.bind("<Button-1>", self._click)

    def _paint(self, bg, fg, sub_fg):
        self.inner.config(bg=bg)
        self.title.config(bg=bg, fg=fg)
        self.sub.config(bg=bg, fg=sub_fg)

    def _hover(self, _):
        if self.enabled:
            self._paint(self.color, BG, BG)

    def _unhover(self, _):
        self._paint(PANEL, self.color if self.enabled else DIM, DIM)

    def _click(self, _):
        if self.enabled:
            self.command()

    def set_enabled(self, on):
        self.enabled = on
        self.config(bg=self.color if on else DIM)
        self._unhover(None)


class App:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.lines = queue.Queue()
        self.use_ai = tk.BooleanVar(value=bool(os.environ.get("ANTHROPIC_API_KEY")))

        root.title("TIDY//OS")
        root.configure(bg=BG)
        root.geometry("1080x860")
        root.minsize(900, 820)
        icon = HERE / "icon.ico"
        if icon.exists():
            root.iconbitmap(str(icon))

        tk.Label(root, text=BANNER, bg=BG, fg=ORANGE, font=(FONT, 8), justify="left").pack(anchor="w", padx=18)
        tk.Label(root, text=">> personal system janitor  //  nothing gets deleted, only sorted",
                 bg=BG, fg=CYAN, font=(FONT, 10)).pack(anchor="w", padx=22, pady=(0, 10))

        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=18)

        left = tk.Frame(body, bg=BG, width=330)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        self.buttons = []

        # tab bar: FILES | GMAIL
        tabs = tk.Frame(left, bg=BG)
        tabs.pack(fill="x", pady=(0, 4))
        self.pages, self.tab_labels = {}, {}
        for name in ("FILES", "GMAIL"):
            lbl = tk.Label(tabs, text=f" {name} ", bg=BG, fg=DIM, font=(FONT, 11, "bold"),
                           cursor="hand2", padx=10, pady=4)
            lbl.pack(side="left", padx=(0, 6))
            lbl.bind("<Button-1>", lambda _e, n=name: self.show_tab(n))
            self.tab_labels[name] = lbl
            self.pages[name] = tk.Frame(left, bg=BG)

        def section(page, label):
            tk.Label(page, text=f"[ {label} ]", bg=BG, fg=MAGENTA,
                     font=(FONT, 10, "bold"), anchor="w").pack(fill="x", pady=(8, 4))

        def button(page, text, sub, cmd, color=ORANGE, runs_script=True):
            b = NeonButton(page, text, sub, cmd, color)
            b.pack(fill="x", pady=3)
            if runs_script:
                self.buttons.append(b)
            return b

        files = self.pages["FILES"]
        section(files, "SCAN")
        button(files, "◉ SCAN SYSTEM BLOAT", "find VMs, caches, junk apps (read-only)",
               lambda: self.run(["bloat.py"]), CYAN)
        button(files, "◉ PREVIEW SORT", "show where files would go (moves nothing)",
               lambda: self.run(self.tidy_args()), CYAN)
        section(files, "ACTION")
        button(files, "▶ SORT MY FILES", "Desktop / Downloads / Documents", self.sort_now)
        button(files, "↺ UNDO LAST SORT", "put everything back", self.undo)
        section(files, "REVIEW")
        button(files, "⚠ OPEN POTENTIAL JUNK", "folders of files flagged for you to review",
               self.open_junk, MAGENTA, runs_script=False)
        button(files, "☰ OPEN JUNK LIST", "system items flagged by the bloat scan",
               lambda: self.open_path(LOGS / "potential-junk.csv"), MAGENTA, runs_script=False)
        tk.Checkbutton(files, text="AI topic sorting (needs API key)", variable=self.use_ai,
                       bg=BG, fg=ORANGE, selectcolor=BG, activebackground=BG,
                       activeforeground=ORANGE_HOT, font=(FONT, 9)).pack(anchor="w", pady=(10, 0))

        mail = self.pages["GMAIL"]
        section(mail, "SCAN")
        button(mail, "◉ SCAN INBOX", "top senders, never-opened (read-only)",
               lambda: self.run(["gmail_tidy.py", "scan"]), CYAN)
        button(mail, "◉ PREVIEW LABELS", "what would get labeled (no changes)",
               lambda: self.run(["gmail_tidy.py", "sort"]), CYAN)
        section(mail, "ACTION")
        button(mail, "▶ LABEL MY EMAIL", "adds TIDY/ labels - never deletes", self.gmail_sort)
        button(mail, "↺ UNDO EMAIL LABELS", "remove labels from the last run", self.gmail_undo)
        section(mail, "REVIEW")
        button(mail, "☰ OPEN SENDER LIST", "never-opened senders + unsubscribe links",
               lambda: self.open_path(LOGS / "gmail-senders.csv"), MAGENTA, runs_script=False)
        button(mail, "✉ OPEN GMAIL", "see the TIDY labels in your sidebar",
               lambda: self.open_url("https://mail.google.com/mail/u/0/#label/TIDY"), MAGENTA,
               runs_script=False)

        self.show_tab("FILES")

        # console
        right = tk.Frame(body, bg=ORANGE, padx=2, pady=2)
        right.pack(side="left", fill="both", expand=True, padx=(16, 0))
        head = tk.Frame(right, bg=ORANGE)
        head.pack(fill="x")
        tk.Label(head, text=" TERMINAL", bg=ORANGE, fg=BG, font=(FONT, 10, "bold")).pack(side="left")
        self.state = tk.Label(head, text="IDLE ", bg=ORANGE, fg=BG, font=(FONT, 10, "bold"))
        self.state.pack(side="right")
        self.console = tk.Text(right, bg=BG, fg=ORANGE, insertbackground=ORANGE, font=(FONT, 10),
                               relief="flat", wrap="none", padx=10, pady=8,
                               selectbackground=ORANGE, selectforeground=BG)
        self.console.pack(fill="both", expand=True)
        self.console.tag_config("cmd", foreground=CYAN)
        self.console.tag_config("junk", foreground=MAGENTA)
        self.console.tag_config("head", foreground=ORANGE_HOT)

        # status bar
        self.status = tk.Label(root, text="", bg=BG, fg=DIM, font=(FONT, 9), anchor="w")
        self.status.pack(fill="x", padx=22, pady=8)
        self.update_status()

        self.write("TIDY//OS online. Pick an action on the left.\n", "cmd")
        self.write("Tip: start with SCAN SYSTEM BLOAT or PREVIEW SORT - neither changes anything.\n\n")
        self.root.after(50, self.pump)

    # ---------------------------------------------------------- helpers

    def write(self, text, tag=None):
        if tag is None:
            if "POTENTIAL JUNK" in text or "Potential Junk" in text:
                tag = "junk"
            elif text.startswith("==") or text.startswith("C:\\"):
                tag = "head"
        self.console.insert("end", text, tag)
        self.console.see("end")

    def update_status(self):
        if DEMO:
            from demo import DEMO_DISK
            used, total = DEMO_DISK
        else:
            du = shutil.disk_usage("C:/")
            used, total = du.used, du.total
        pct = used / total
        bar = "█" * int(pct * 30) + "░" * (30 - int(pct * 30))
        demo_tag = "   [DEMO MODE]" if DEMO else ""
        self.status.config(text=f"C:  {bar}  {(total - used) / 1e9:.0f} GB free of {total / 1e9:.0f} GB{demo_tag}")

    def tidy_args(self, *extra):
        args = ["tidy.py", *extra]
        if not self.use_ai.get():
            args.append("--no-ai")
        return args

    def set_busy(self, busy):
        for b in self.buttons:
            b.set_enabled(not busy)
        self.state.config(text="RUNNING... " if busy else "IDLE ")

    def run(self, args):
        if self.proc:
            return
        self.set_busy(True)
        self.write(f"\n$ {' '.join(args)}\n", "cmd")
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")

        def demo_worker():
            from demo import demo_output
            for line in demo_output(args).splitlines(keepends=True):
                self.lines.put(line)
                time.sleep(0.06)
            self.lines.put(("done", 0))

        def worker():
            try:
                self.proc = subprocess.Popen(
                    [PYTHON, str(HERE / args[0]), *args[1:]], cwd=HERE, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
                for line in self.proc.stdout:
                    self.lines.put(line)
                code = self.proc.wait()
                self.lines.put(("done", code))
            except OSError as e:
                self.lines.put(f"Couldn't start: {e}\n")
                self.lines.put(("done", 1))

        threading.Thread(target=demo_worker if DEMO else worker, daemon=True).start()

    def pump(self):
        try:
            while True:
                item = self.lines.get_nowait()
                if isinstance(item, tuple):
                    self.proc = None
                    self.set_busy(False)
                    self.update_status()
                    self.write("[done]\n" if item[1] == 0 else f"[exited with code {item[1]}]\n", "cmd")
                else:
                    self.write(item)
        except queue.Empty:
            pass
        self.root.after(50, self.pump)

    def open_url(self, url):
        if DEMO:
            self.write(f"(demo) would open {url}\n", "cmd")
        else:
            os.startfile(url)

    def open_path(self, path):
        if DEMO:
            self.write(f"(demo) would open {path.name}\n", "cmd")
        elif path.exists():
            os.startfile(path)
        else:
            self.write(f"Not found yet: {path}  (run a scan or sort first)\n", "junk")

    def show_tab(self, name):
        for n, page in self.pages.items():
            page.pack_forget()
            self.tab_labels[n].config(bg=BG, fg=DIM)
        self.pages[name].pack(fill="both", expand=True)
        self.tab_labels[name].config(bg=ORANGE, fg=BG)

    # ---------------------------------------------------------- actions

    def gmail_sort(self):
        if messagebox.askyesno("TIDY//OS", "Add TIDY/ labels to your email?\n\nLabels are just tags - "
                               "nothing is deleted or moved out of your inbox, and UNDO removes them."):
            self.run(["gmail_tidy.py", "sort", "--apply"])

    def gmail_undo(self):
        if messagebox.askyesno("TIDY//OS", "Remove the labels added by the last email sort?"):
            self.run(["gmail_tidy.py", "undo"])

    def sort_now(self):
        if messagebox.askyesno("TIDY//OS", "Move loose files on Desktop, Downloads and Documents "
                               "into folders?\n\nNothing is deleted, and UNDO LAST SORT puts it all back."):
            self.run(self.tidy_args("--apply"))

    def undo(self):
        if messagebox.askyesno("TIDY//OS", "Put the files from the last sort back where they were?"):
            self.run(["undo.py"])

    def open_junk(self):
        if DEMO:
            self.write("(demo) would open the Potential Junk folders\n", "cmd")
            return
        found = False
        for folder in (Path.home() / "Downloads", Path.home() / "OneDrive/Desktop",
                       Path.home() / "OneDrive/Documents"):
            junk = folder / "Potential Junk"
            if junk.exists():
                os.startfile(junk)
                found = True
        if not found:
            self.write("No Potential Junk folders yet - run SORT MY FILES first.\n", "junk")


if __name__ == "__main__":
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)  # crisp text on high-DPI screens
    except Exception:
        pass
    App(root)
    root.mainloop()
