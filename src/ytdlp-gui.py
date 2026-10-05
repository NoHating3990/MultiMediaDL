#!/usr/bin/env python3
"""MultiMedia Downloader: a small yt-dlp GUI (Audio / MP4 / DaVinci Resolve MOV). Runs on Linux and Windows."""
import json, os, queue, re, shlex, shutil, signal, subprocess, sys, threading, time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox

# ---- look & feel (all colours/fonts live here so they're easy to tweak) ----
ACCENT = "#E8A600"
ACCENT_HOVER, ACCENT_PRESSED, ACCENT_DIM = "#F5BC33", "#C48B00", "#4a3600"  # lighter / darker / disabled
BG, FIELD, BTN, BTN_HOVER, BORDER = "#1c1c1c", "#2b2b2b", "#333333", "#3d3d3d", "#4a4a4a"
FG, MUTED, DIS_FG = "#f2f2f2", "#9a9a9a", "#6b6b6b"
FONT_SIZE = 11
FONT_PREFS = ("Segoe UI", "Inter", "Noto Sans", "Open Sans", "Cantarell", "Adwaita Sans",
              "Ubuntu", "DejaVu Sans", "Liberation Sans", "Arial", "Helvetica")
MONO_PREFS = ("JetBrains Mono", "Noto Sans Mono", "DejaVu Sans Mono", "Liberation Mono",
              "Consolas", "Courier New")


def pick_font(root, prefs, default):
    have = {f.lower(): f for f in tkfont.families(root)}
    return next((have[n.lower()] for n in prefs if n.lower() in have), default)

# ---- platform differences live here ----
IS_WIN = sys.platform == "win32"
EXE = ".exe" if IS_WIN else ""
# keep spawned tools from flashing a console window on Windows
NOWIN = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)} if IS_WIN else {}
if IS_WIN:
    CFG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ytdlp-gui")
    DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ytdlp-gui")
else:
    CFG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "ytdlp-gui")
    DATA_DIR = os.path.expanduser("~/.local/share/ytdlp-gui")
CFG = os.path.join(CFG_DIR, "config.json")

# Resolve (free, Linux) can't decode H.264/AAC, so re-encode to intraframe codecs + PCM audio in MOV.
RESOLVE = {
    "DNxHR HQ (recommended)": "-c:v dnxhd -profile:v dnxhr_hq -pix_fmt yuv422p -c:a pcm_s16le",
    "DNxHR SQ (smaller files)":      "-c:v dnxhd -profile:v dnxhr_sq -pix_fmt yuv422p -c:a pcm_s16le",
    "ProRes 422 (10-bit)":           "-c:v prores_ks -profile:v 2 -pix_fmt yuv422p10le -c:a pcm_s16le",
}
# MP4 codec choices. None = keep the downloaded stream as-is (fast, no quality loss).
MP4_CODECS = {
    "Original (no re-encode)": None,
    "H.264 (most compatible)":  "-c:v libx264 -crf 20 -preset medium -pix_fmt yuv420p -c:a aac -b:a 192k",
    "H.265 / HEVC (smaller)":   "-c:v libx265 -crf 24 -preset medium -pix_fmt yuv420p -tag:v hvc1 -c:a aac -b:a 192k",
    "AV1 (smallest, slowest)":  "-c:v libsvtav1 -crf 32 -preset 8 -pix_fmt yuv420p -c:a aac -b:a 192k",
}

# Quality choices. Video: max height (None = best available). Audio: MP3 bitrate for ffmpeg.
VIDEO_Q = {"Best available": None, "2160p (4K)": 2160, "1440p": 1440, "1080p": 1080,
           "720p": 720, "480p": 480, "360p": 360}
AUDIO_Q = {"Best (VBR)": "0", "320 kbps": "320K", "192 kbps": "192K", "128 kbps": "128K"}  # MP3 only
AUDIO_FMT = {"MP3": "mp3", "FLAC (lossless)": "flac", "WAV (lossless)": "wav"}
# Browsers yt-dlp can read cookies from. Each lists every place a profile may live:
# native installs, Flatpak (~/.var/app), Snap, and the newer XDG location for Firefox.
def _h(*parts):
    return os.path.join(os.path.expanduser("~"), *parts)


if IS_WIN:
    _ap, _lo = os.environ.get("APPDATA", ""), os.environ.get("LOCALAPPDATA", "")
    BROWSERS = {
        "firefox":  ("Firefox",  [os.path.join(_ap, "Mozilla", "Firefox")]),
        "chrome":   ("Chrome",   [os.path.join(_lo, "Google", "Chrome", "User Data")]),
        "chromium": ("Chromium", [os.path.join(_lo, "Chromium", "User Data")]),
        "brave":    ("Brave",    [os.path.join(_lo, "BraveSoftware", "Brave-Browser", "User Data")]),
        "edge":     ("Edge",     [os.path.join(_lo, "Microsoft", "Edge", "User Data")]),
        "vivaldi":  ("Vivaldi",  [os.path.join(_lo, "Vivaldi", "User Data")]),
        "opera":    ("Opera",    [os.path.join(_ap, "Opera Software", "Opera Stable")]),
    }
else:
    BROWSERS = {
        "firefox":  ("Firefox",  [_h(".mozilla/firefox"), _h(".config/mozilla/firefox"),
                                  _h(".var/app/org.mozilla.firefox/.mozilla/firefox"),
                                  _h("snap/firefox/common/.mozilla/firefox")]),
        "chrome":   ("Chrome",   [_h(".config/google-chrome"), _h(".var/app/com.google.Chrome/config/google-chrome")]),
        "chromium": ("Chromium", [_h(".config/chromium"), _h(".var/app/org.chromium.Chromium/config/chromium"),
                                  _h("snap/chromium/common/chromium")]),
        "brave":    ("Brave",    [_h(".config/BraveSoftware/Brave-Browser"),
                                  _h(".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser")]),
        "edge":     ("Edge",     [_h(".config/microsoft-edge"), _h(".var/app/com.microsoft.Edge/config/microsoft-edge")]),
        "vivaldi":  ("Vivaldi",  [_h(".config/vivaldi"), _h(".var/app/com.vivaldi.Vivaldi/config/vivaldi")]),
        "opera":    ("Opera",    [_h(".config/opera"), _h(".var/app/com.opera.Opera/config/opera")]),
    }


def browser_dir(key):
    for d in BROWSERS[key][1]:
        if os.path.isdir(d):
            return d
    return None


def cookie_args(key, file):
    if key == "file" and file and os.path.isfile(file):
        return ["--cookies", file]
    if key in BROWSERS:
        d = browser_dir(key)
        if d and d != BROWSERS[key][1][0]:  # non-default location (Flatpak/Snap/XDG): point yt-dlp at it
            return ["--cookies-from-browser", f"{key}:{d}"]
        return ["--cookies-from-browser", key]
    return []


DEFAULTS = {"dest": os.path.expanduser("~/Videos"), "type": "mp4",
            "resolve": list(RESOLVE)[0], "playlist": False,
            "vq": "1080p", "aq": "Best (VBR)", "af": "MP3", "mc": list(MP4_CODECS)[0],
            "ck": "none", "cfile": "", "art": True}


def load_cfg():
    try:
        with open(CFG) as f:
            return {**DEFAULTS, **json.load(f)}
    except Exception:
        return dict(DEFAULTS)


def save_cfg(c):
    os.makedirs(CFG_DIR, exist_ok=True)
    with open(CFG, "w") as f:
        json.dump(c, f, indent=2)


APPDIR = os.environ.get("APPDIR", "")  # set by the AppImage launcher (Linux only)
USER_BIN = os.path.join(DATA_DIR, "yt-dlp" + EXE)  # updatable copy (an AppImage/exe can't modify itself)
YTDLP_URL = ("https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" if IS_WIN
             else "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux")
# Windows: ffmpeg + deno are downloaded on first run (the Linux AppImage bundles them instead).
WIN_TOOLS = {
    "ffmpeg": ("https://github.com/yt-dlp/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip",
               ("ffmpeg.exe", "ffprobe.exe")),
    "deno": ("https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip",
             ("deno.exe",)),
}
if IS_WIN:  # so yt-dlp can find the deno/ffmpeg we download
    os.environ["PATH"] = DATA_DIR + os.pathsep + os.environ.get("PATH", "")


def find_ytdlp():
    bundled = os.path.join(APPDIR, "usr/bin/yt-dlp") if APPDIR else ""
    for p in (USER_BIN, bundled):
        if p and os.access(p, os.X_OK):
            return p
    extra = [] if IS_WIN else [os.path.expanduser("~/.local/bin"), "/home/linuxbrew/.linuxbrew/bin", "/usr/local/bin"]
    return shutil.which("yt-dlp", path=os.pathsep.join([os.environ.get("PATH", "")] + extra))


def download(url, dest, say, label):
    """Download url -> dest (atomically), reporting percent through say()."""
    import urllib.request
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    last = [-1]

    def hook(blocks, size, total):
        if total > 0:
            pct = min(100, int(blocks * size * 100 / total))
            if pct != last[0]:
                last[0] = pct
                say(f"Downloading {label}… {pct}%")

    urllib.request.urlretrieve(url, dest + ".part", hook)
    os.replace(dest + ".part", dest)


def get_ytdlp(say):
    download(YTDLP_URL, USER_BIN, say, "yt-dlp")
    os.chmod(USER_BIN, 0o755)
    return subprocess.run([USER_BIN, "--version"], capture_output=True, text=True, **NOWIN).stdout.strip()


def get_zip_tool(name, say):
    """Windows: download a zip and pull just the wanted .exe files out of it."""
    import zipfile
    url, files = WIN_TOOLS[name]
    tmp = os.path.join(DATA_DIR, name + ".zip")
    download(url, tmp, say, name)
    say(f"Unpacking {name}…")
    with zipfile.ZipFile(tmp) as z:
        for member in z.namelist():
            base = os.path.basename(member)
            if base in files:
                target = os.path.join(DATA_DIR, base)
                with z.open(member) as src, open(target + ".part", "wb") as out:
                    shutil.copyfileobj(src, out)
                os.replace(target + ".part", target)
    os.remove(tmp)
    missing = [f for f in files if not os.path.exists(os.path.join(DATA_DIR, f))]
    if missing:
        raise RuntimeError(f"{', '.join(missing)} not found in the {name} download")


def install_entry():
    """AppImage --install: add a KDE/GNOME app-menu entry pointing at this AppImage."""
    img = os.environ.get("APPIMAGE")
    if not img:
        print("Run this from the AppImage: ./MultiMedia-Downloader-x86_64.AppImage --install")
        return
    home = os.path.expanduser("~/.local/share")
    os.makedirs(home + "/applications", exist_ok=True)
    os.makedirs(home + "/icons", exist_ok=True)
    shutil.copy(os.path.join(APPDIR, "ytdlp-gui.svg"), home + "/icons/ytdlp-gui.svg")
    with open(home + "/applications/ytdlp-gui.desktop", "w") as f:
        f.write(f"[Desktop Entry]\nType=Application\nName=MultiMedia Downloader\n"
                f"Comment=Download video/audio with yt-dlp\nExec=\"{img}\"\n"
                f"Icon=ytdlp-gui\nCategories=AudioVideo;Network;\nTerminal=false\n")
    print("Menu entry installed. Search for 'MultiMedia Downloader' in your start menu.")


def explain(log, cookies_on):
    """Turn yt-dlp's raw output into one plain-English line. First match wins."""
    errs = [l for l in log if l.startswith("ERROR")]
    text = " ".join(errs or log[-15:]).lower()

    def has(*words):
        return any(w in text for w in words)

    if cookies_on and has("cookies database", "cookie database", "could not copy", "decrypt", "keyring"):
        return "Couldn't read the browser's cookies. Close the browser, try another one, or use a cookies.txt file."
    if has("private video"):
        return "This video is private."
    if has("not a bot", "sign in to confirm", "login required", "log in", "sign in", "members-only", "join this channel"):
        if cookies_on:
            return "The site still wants a login. Make sure you're signed in in that browser, or try another browser/cookies.txt."
        return "The site wants a login. Pick your browser (or a cookies.txt file) under Cookies and try again."
    if has("live event will begin", "premieres in", "this live event"):
        return "This is an upcoming livestream or premiere. It isn't available yet."
    if has("drm"):
        return "This video is DRM-protected and can't be downloaded."
    if has("video unavailable", "not available", "has been removed", "no longer available", "does not exist"):
        if has("country", "geo"):
            return "This video isn't available in your country."
        if has("requested format is not available"):
            return "That quality/format isn't available for this video. Try 'Best available'."
        return "This video is unavailable (removed, private, or deleted)."
    if has("http error 429", "too many requests"):
        return "The site is rate-limiting you. Wait a few minutes and try again."
    if has("http error 403", "forbidden", "signature", "n challenge", "nsig", "javascript runtime"):
        return "The site blocked the download. Click 'Update yt-dlp' and try again."
    if has("unsupported url"):
        return "That link isn't supported. Check the URL."
    if has("name resolution", "unable to download webpage", "timed out", "network is unreachable",
           "connection reset", "urlopen error", "getaddrinfo"):
        return "Network problem. Check your internet connection and try again."
    if has("no space left"):
        return "The disk is full. Free some space or pick another folder."
    if has("permission denied", "read-only file system"):
        return "Can't write to that folder. Pick a different one with Browse."
    if has("ffmpeg", "ffprobe") and has("not found", "not installed"):
        return "ffmpeg wasn't found, so the file couldn't be converted."
    if has("conversion failed", "unknown encoder", "error initializing", "invalid argument", "postprocessing"):
        return "Converting the file failed. Try a different codec (see Show log)."
    if errs:
        msg = re.sub(r"^ERROR:\s*(?:\[[^\]]*\]\s*[\w\-]+:\s*)?", "", errs[-1])
        return f"Failed: {msg[:100]} (see Show log)"
    return "Failed. Click 'Show log' for details."


# Status text per yt-dlp stage. busy=True -> no percentage exists, so the bar just animates.
STAGES = (
    ("[youtube", "Fetching video info…", False), ("[info]", "Fetching video info…", False),
    ("[generic]", "Fetching video info…", False),
    ("[Merger]", "Merging video and audio…", True),
    ("[VideoConvertor]", "Converting video… this can take a while", True),
    ("[ExtractAudio]", "Converting audio…", True),
    ("[ThumbnailsConvertor]", "Preparing cover art…", True),
    ("[EmbedThumbnail]", "Adding cover art…", True),
    ("[Metadata]", "Adding tags…", True),
)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MultiMedia Downloader")
        self.resizable(False, False)
        if IS_WIN:  # icon.ico is bundled into the exe by PyInstaller
            try:
                base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
                self.iconbitmap(os.path.join(base, "icon.ico"))
            except Exception:
                pass
        self.cfg = load_cfg()
        self.proc = None
        self.locked = False
        self.cancelled = False
        self.indet = False
        self.log = []
        self.logwin = self.logtext = None
        self.q = queue.Queue()

        self.url = tk.StringVar()
        t = "audio" if self.cfg["type"] == "mp3" else self.cfg["type"]  # "mp3" was the old name
        self.type = tk.StringVar(value=t if t in ("audio", "mp4", "resolve") else "mp4")
        self.res = self.cfg["resolve"] if self.cfg["resolve"] in RESOLVE else DEFAULTS["resolve"]
        self.mc = self.cfg["mc"] if self.cfg["mc"] in MP4_CODECS else DEFAULTS["mc"]
        self.af = self.cfg["af"] if self.cfg["af"] in AUDIO_FMT else DEFAULTS["af"]
        self.codec = tk.StringVar()
        self.ck = self.cfg["ck"] if self.cfg["ck"] in BROWSERS or self.cfg["ck"] in ("none", "file") else "none"
        self.cfile = self.cfg["cfile"]
        self.cookie = tk.StringVar()
        self.dest = tk.StringVar(value=self.cfg["dest"])
        self.playlist = tk.BooleanVar(value=self.cfg["playlist"])
        self.art = tk.BooleanVar(value=self.cfg["art"])
        self.vq = self.cfg["vq"] if self.cfg["vq"] in VIDEO_Q else DEFAULTS["vq"]
        self.aq = self.cfg["aq"] if self.cfg["aq"] in AUDIO_Q else DEFAULTS["aq"]
        self.qual = tk.StringVar()
        self.status = tk.StringVar(value="Ready")
        self.apply_theme()
        title_font = tkfont.nametofont("TkDefaultFont").copy()
        title_font.configure(size=FONT_SIZE + 9, weight="bold")

        p = ttk.Frame(self, padding=(22, 18))
        p.pack(fill="both", expand=True)

        # header
        head = ttk.Frame(p)
        head.pack(fill="x")
        ttk.Label(head, text="MultiMedia Downloader", font=title_font, style="Title.TLabel").pack(side="left")

        # link + paste
        row = ttk.Frame(p)
        row.pack(fill="x", pady=(14, 0))
        e = ttk.Entry(row, textvariable=self.url, width=48)
        e.pack(side="left", fill="x", expand=True)
        e.focus()
        ttk.Button(row, text="Paste", command=self.paste).pack(side="left", padx=(8, 0))

        # output type
        fmt = ttk.Frame(p)
        fmt.pack(fill="x", pady=(14, 0))
        for text, val in (("Audio", "audio"), ("MP4 video", "mp4"), ("DaVinci Resolve", "resolve")):
            ttk.Radiobutton(fmt, text=text, value=val, variable=self.type, command=self.on_type,
                            style="Toolbutton").pack(side="left", fill="x", expand=True, padx=(0, 6))

        # quality + codec side by side
        opts = ttk.Frame(p)
        opts.pack(fill="x", pady=(14, 0))
        opts.columnconfigure((0, 1), weight=1, uniform="o")
        ttk.Label(opts, text="Quality", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(opts, text="Codec / format", style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.qcombo = ttk.Combobox(opts, textvariable=self.qual, state="readonly", width=24)
        self.qcombo.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        self.qcombo.bind("<<ComboboxSelected>>", self.on_quality)
        self.combo = ttk.Combobox(opts, textvariable=self.codec, state="readonly", width=24)
        self.combo.grid(row=1, column=1, sticky="ew", pady=(3, 0), padx=(12, 0))
        self.combo.bind("<<ComboboxSelected>>", self.on_codec)

        # save location
        sv = ttk.Frame(p)
        sv.pack(fill="x", pady=(14, 0))
        ttk.Label(sv, text="Save to", style="Muted.TLabel").pack(anchor="w")
        r2 = ttk.Frame(sv)
        r2.pack(fill="x", pady=(3, 0))
        ttk.Entry(r2, textvariable=self.dest, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(r2, text="Browse…", command=self.browse).pack(side="left", padx=(8, 0))

        # toggles
        tg = ttk.Frame(p)
        tg.pack(fill="x", pady=(12, 0))
        ttk.Checkbutton(tg, text="Whole playlist", variable=self.playlist,
                        command=self.on_change).pack(side="left")
        self.art_chk = ttk.Checkbutton(tg, text="Embed cover art & tags", variable=self.art,
                                       command=self.on_change)
        self.art_chk.pack(side="left", padx=(18, 0))

        # advanced (cookies)
        self.adv_open = False
        self.adv_btn = ttk.Button(p, text="Advanced ▸", command=self.toggle_adv, style="Link.TButton")
        self.adv_btn.pack(anchor="w", pady=(10, 0))
        self.adv = ttk.Frame(p)
        ttk.Label(self.adv, text="Browser cookies (for videos that need a login)", style="Muted.TLabel").pack(anchor="w")
        self.ccombo = ttk.Combobox(self.adv, textvariable=self.cookie, state="readonly", width=34)
        self.ccombo.pack(anchor="w", pady=(3, 0))
        self.ccombo.bind("<<ComboboxSelected>>", self.on_cookie)
        if self.ck != "none":
            self.toggle_adv()  # make an active cookie setting visible

        # actions + progress
        act = ttk.Frame(p)
        act.pack(fill="x", pady=(16, 0))
        self.go = ttk.Button(act, text="Download", command=self.start, style="Accent.TButton")
        self.go.pack(side="left", fill="x", expand=True, ipady=4)
        self.stop = ttk.Button(act, text="Cancel", command=self.cancel, state="disabled")
        self.stop.pack(side="left", padx=(8, 0), ipady=4)

        self.bar = ttk.Progressbar(p, maximum=100)
        self.bar.pack(fill="x", pady=(14, 0))
        ttk.Label(p, textvariable=self.status, wraplength=540, justify="left",
                  anchor="w").pack(fill="x", pady=(6, 0))

        # footer
        foot = ttk.Frame(p)
        foot.pack(fill="x", pady=(10, 0))
        ttk.Button(foot, text="Open folder", command=self.open_folder).pack(side="left")
        ttk.Button(foot, text="Show log", command=self.show_log).pack(side="left", padx=6)
        ttk.Button(foot, text="Update yt-dlp", command=self.update_ytdlp).pack(side="right")

        self.bind("<Return>", lambda _: self.start())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_quality()
        self.refresh_codec()
        self.refresh_cookies()
        self.on_change()
        self.after(100, self.poll)
        self.after(300, self.ensure_ytdlp)

    def toggle_adv(self):
        self.adv_open = not self.adv_open
        if self.adv_open:
            self.adv.pack(after=self.adv_btn, anchor="w", pady=(6, 0))
        else:
            self.adv.pack_forget()
        self.adv_btn.configure(text="Advanced ▾" if self.adv_open else "Advanced ▸")

    def apply_theme(self):
        """Dark flat theme with an orange accent, built on ttk's 'clam' (no extra packages)."""
        st = ttk.Style(self)
        st.theme_use("clam")
        self.configure(bg=BG)

        fam = pick_font(self, FONT_PREFS, "TkDefaultFont")
        mono = pick_font(self, MONO_PREFS, "TkFixedFont")
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont"):
            tkfont.nametofont(name).configure(family=fam, size=FONT_SIZE)
        tkfont.nametofont("TkTooltipFont").configure(family=fam, size=FONT_SIZE - 1)
        tkfont.nametofont("TkFixedFont").configure(family=mono, size=FONT_SIZE - 1)

        flat = dict(lightcolor=BTN, darkcolor=BTN, bordercolor=BORDER, focuscolor=BTN)
        st.configure(".", background=BG, foreground=FG, fieldbackground=FIELD, troughcolor=FIELD,
                     bordercolor=BORDER, lightcolor=BG, darkcolor=BG, focuscolor=BG,
                     selectbackground=ACCENT, selectforeground="#000000", insertcolor=FG)
        st.map(".", foreground=[("disabled", DIS_FG)])

        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("Muted.TLabel", foreground=MUTED)
        st.configure("Title.TLabel", foreground=ACCENT)

        st.configure("TButton", background=BTN, foreground=FG, padding=(14, 7), relief="flat", **flat)
        st.map("TButton", background=[("disabled", "#262626"), ("pressed", BORDER), ("active", BTN_HOVER)])

        st.configure("Accent.TButton", background=ACCENT, foreground="#000000", padding=(14, 9),
                     lightcolor=ACCENT, darkcolor=ACCENT, bordercolor=ACCENT, focuscolor=ACCENT)
        st.map("Accent.TButton", background=[("disabled", ACCENT_DIM), ("pressed", ACCENT_PRESSED), ("active", ACCENT_HOVER)],
               foreground=[("disabled", DIS_FG)], bordercolor=[("disabled", ACCENT_DIM)])

        st.configure("Link.TButton", background=BG, foreground=MUTED, padding=(0, 4), relief="flat",
                     lightcolor=BG, darkcolor=BG, bordercolor=BG, focuscolor=BG)
        st.map("Link.TButton", background=[("active", BG), ("pressed", BG)], foreground=[("active", ACCENT)])

        # Output-type selector: radiobuttons drawn as a row of buttons, selected = accent colour
        st.configure("Toolbutton", background=BTN, foreground=FG, padding=(10, 8), relief="flat", **flat)
        st.map("Toolbutton", background=[("selected", ACCENT), ("active", BTN_HOVER)],
               foreground=[("selected", "#000000")], relief=[("selected", "flat"), ("pressed", "flat")])

        for w in ("TEntry", "TCombobox"):
            st.configure(w, fieldbackground=FIELD, foreground=FG, padding=7, arrowcolor=FG,
                         background=BTN, lightcolor=FIELD, darkcolor=FIELD, bordercolor=BORDER)
            st.map(w, bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
                   darkcolor=[("focus", ACCENT)], fieldbackground=[("readonly", FIELD), ("disabled", BG)],
                   foreground=[("disabled", DIS_FG)], arrowcolor=[("disabled", DIS_FG)],
                   selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FG)])
        # the dropdown list of a combobox is a plain Tk listbox
        self.option_add("*TCombobox*Listbox.background", FIELD)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "#000000")
        self.option_add("*TCombobox*Listbox.borderWidth", 0)

        st.configure("TCheckbutton", background=BG, foreground=FG, indicatorbackground=FIELD,
                     indicatorforeground="#000000", upperbordercolor=BORDER, lowerbordercolor=BORDER,
                     focuscolor=BG, padding=3)
        st.map("TCheckbutton", background=[("active", BG)],
               indicatorbackground=[("selected", ACCENT), ("disabled", "#262626")],
               indicatorforeground=[("disabled", DIS_FG)], foreground=[("disabled", DIS_FG)])

        st.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=FIELD, thickness=8,
                     lightcolor=ACCENT, darkcolor=ACCENT, bordercolor=FIELD)
        st.configure("TScrollbar", background=BTN, troughcolor=FIELD, arrowcolor=FG, bordercolor=FIELD,
                     lightcolor=BTN, darkcolor=BTN)
        st.map("TScrollbar", background=[("active", BTN_HOVER)])

    def bar_mode(self, indeterminate):
        """Determinate while downloading; a moving bar while ffmpeg works (no percentage exists then)."""
        if indeterminate == self.indet:
            return
        self.indet = indeterminate
        if indeterminate:
            self.bar.configure(mode="indeterminate")
            self.bar.start(15)
        else:
            self.bar.stop()
            self.bar.configure(mode="determinate")
            self.bar["value"] = 0

    # ---- option dropdowns -------------------------------------------------
    def refresh_quality(self):
        t = self.type.get()
        if t == "audio":
            if AUDIO_FMT[self.af] == "mp3":
                values, cur = list(AUDIO_Q), self.aq
            else:
                values, cur = ["Lossless"], "Lossless"
        else:
            values, cur = list(VIDEO_Q), self.vq
        self.qcombo.configure(values=values)
        self.qual.set(cur)

    def refresh_codec(self):
        t = self.type.get()
        if t == "audio":
            self.combo.configure(values=list(AUDIO_FMT))
            self.codec.set(self.af)
        elif t == "mp4":
            self.combo.configure(values=list(MP4_CODECS))
            self.codec.set(self.mc)
        else:
            self.combo.configure(values=list(RESOLVE))
            self.codec.set(self.res)

    def on_type(self):
        self.refresh_quality()
        self.refresh_codec()
        self.on_change()

    def on_quality(self, _=None):
        if self.type.get() == "audio":
            if self.qual.get() in AUDIO_Q:
                self.aq = self.qual.get()
        else:
            self.vq = self.qual.get()
        self.on_change()

    def on_codec(self, _=None):
        t = self.type.get()
        if t == "audio":
            self.af = self.codec.get()
            self.refresh_quality()  # bitrate only applies to MP3
        elif t == "mp4":
            self.mc = self.codec.get()
        else:
            self.res = self.codec.get()
        self.on_change()

    def on_change(self):
        lossless = self.type.get() == "audio" and AUDIO_FMT[self.af] != "mp3"
        self.qcombo.configure(state="disabled" if lossless else "readonly")
        art_ok = self.type.get() == "audio" and AUDIO_FMT[self.af] in ("mp3", "flac")
        self.art_chk.state(["!disabled"] if art_ok else ["disabled"])
        self.cfg.update(art=self.art.get(), type=self.type.get(), resolve=self.res, mc=self.mc, af=self.af,
                        dest=self.dest.get(), playlist=self.playlist.get(),
                        vq=self.vq, aq=self.aq, ck=self.ck, cfile=self.cfile)
        save_cfg(self.cfg)

    # ---- cookies ----------------------------------------------------------
    def cookie_options(self):
        opts = [("None", "none")]
        for k, (name, _) in BROWSERS.items():
            opts.append((name + (" (detected)" if browser_dir(k) else ""), k))
        opts.append((f"cookies.txt: {os.path.basename(self.cfile)}" if self.cfile else "cookies.txt file…", "file"))
        return opts

    def refresh_cookies(self):
        opts = self.cookie_options()
        self.ccombo.configure(values=[l for l, _ in opts])
        self.cookie.set(next((l for l, k in opts if k == self.ck), "None"))

    def on_cookie(self, _=None):
        key = dict(self.cookie_options()).get(self.cookie.get(), "none")
        if key == "file":
            f = filedialog.askopenfilename(title="Select a cookies.txt file",
                                           initialdir=os.path.dirname(self.cfile) if self.cfile else os.path.expanduser("~"),
                                           filetypes=[("cookies.txt", "*.txt"), ("All files", "*")])
            if f:
                self.cfile, self.ck = f, "file"
        else:
            self.ck = key
        self.refresh_cookies()
        self.on_change()

    # ---- small helpers ----------------------------------------------------
    def browse(self):
        d = filedialog.askdirectory(initialdir=self.dest.get(), title="Choose download folder")
        if d:
            self.dest.set(d)
            self.on_change()

    def paste(self):
        try:
            text = self.clipboard_get().strip()
        except tk.TclError:
            text = ""
        if not text:
            self.status.set("Clipboard is empty (or doesn't contain text).")
            return
        self.url.set(text.splitlines()[0].strip())
        self.status.set("Pasted from clipboard.")

    def open_folder(self):
        try:
            os.makedirs(self.dest.get(), exist_ok=True)
            if IS_WIN:
                os.startfile(self.dest.get())
            else:
                subprocess.Popen(["xdg-open", self.dest.get()], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        except Exception:
            self.status.set("Couldn't open the folder. It's at: " + self.dest.get())

    def add_log(self, line):
        self.log = (self.log + [line])[-3000:]
        if self.logwin and self.logwin.winfo_exists():
            self.logtext.configure(state="normal")
            self.logtext.insert("end", line + "\n")
            self.logtext.see("end")
            self.logtext.configure(state="disabled")

    def show_log(self):
        if self.logwin and self.logwin.winfo_exists():
            self.logwin.lift()
            return
        w = tk.Toplevel(self)
        w.title("Log")
        w.geometry("680x340")
        box = ttk.Frame(w)
        box.pack(fill="both", expand=True)
        t = tk.Text(box, wrap="word", font="TkFixedFont", borderwidth=0, padx=8, pady=6,
                    bg=FIELD, fg=FG, insertbackground=FG, selectbackground=ACCENT, selectforeground="#000000")
        sb = ttk.Scrollbar(box, command=t.yview)
        t.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        t.pack(fill="both", expand=True)
        t.insert("end", "\n".join(self.log) + ("\n" if self.log else ""))
        t.see("end")
        t.configure(state="disabled")
        self.logwin, self.logtext = w, t

    # ---- downloading ------------------------------------------------------
    def build_cmd(self, exe):
        t = self.type.get()
        cmd = [exe, "--newline", "--no-colors", "-P", self.dest.get(),
               "-o", "%(title)s.%(ext)s",
               "--yes-playlist" if self.playlist.get() else "--no-playlist"]
        ff_dir = os.path.join(APPDIR, "usr/bin") if APPDIR else (DATA_DIR if IS_WIN else "")
        if ff_dir and os.path.exists(os.path.join(ff_dir, "ffmpeg" + EXE)):
            cmd += ["--ffmpeg-location", ff_dir]
        cmd += cookie_args(self.ck, self.cfile)
        if t == "audio":
            fmt = AUDIO_FMT[self.af]
            cmd += ["-x", "--audio-format", fmt]
            if fmt == "mp3":
                cmd += ["--audio-quality", AUDIO_Q[self.aq]]
            if self.art.get() and fmt in ("mp3", "flac"):
                cmd += ["--embed-thumbnail", "--embed-metadata", "--convert-thumbnails", "jpg"]
            return cmd + [self.url.get().strip()]
        cmd += ["-f", "bv*+ba/b"]
        h = VIDEO_Q[self.vq]
        if h:  # prefer the best stream at or below this height (falls back to closest if none)
            cmd += ["-S", f"res:{h}"]
        if t == "mp4":
            args = MP4_CODECS[self.mc]
            if args:  # merge to mkv first so yt-dlp's recode step always runs
                cmd += ["--merge-output-format", "mkv", "--recode-video", "mp4",
                        "--postprocessor-args", "VideoConvertor:" + args]
            else:
                cmd += ["--merge-output-format", "mp4"]
        else:
            cmd += ["--recode-video", "mov",
                    "--postprocessor-args", "VideoConvertor:" + RESOLVE[self.res]]
        return cmd + [self.url.get().strip()]

    def start(self):
        if self.proc or self.locked or not self.url.get().strip():
            return
        exe = find_ytdlp()
        if not exe:
            messagebox.showerror("yt-dlp not found",
                                 "Couldn't download yt-dlp. Check your internet connection, then click 'Update yt-dlp'.")
            return
        if self.ck == "file" and not os.path.isfile(self.cfile):
            messagebox.showerror("cookies.txt missing", "The selected cookies.txt file no longer exists. Pick it again under Cookies.")
            return
        os.makedirs(self.dest.get(), exist_ok=True)
        cmd = self.build_cmd(exe)
        self.log = []
        self.cancelled = False
        if self.logwin and self.logwin.winfo_exists():
            self.logtext.configure(state="normal")
            self.logtext.delete("1.0", "end")
            self.logtext.configure(state="disabled")
        self.add_log("$ " + " ".join(shlex.quote(c) for c in cmd))
        self.bar_mode(False)
        self.bar["value"] = 0
        self.status.set("Starting…")
        self.go.state(["disabled"])
        self.stop.state(["!disabled"])
        threading.Thread(target=self.run, args=(cmd,), daemon=True).start()

    def run(self, cmd):
        try:
            kw = dict(NOWIN) if IS_WIN else {"start_new_session": True}  # new group so Cancel can stop ffmpeg too
            env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1,
                                         encoding="utf-8", errors="replace", env=env, **kw)
            for line in self.proc.stdout:
                self.q.put(("line", line.strip()))
            self.q.put(("done", self.proc.wait()))
        except Exception as ex:
            self.q.put(("line", f"ERROR: could not start yt-dlp: {ex}"))
            self.q.put(("done", 1))

    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "line" and val:
                    m = re.match(r"\[download\]\s+(\d+(?:\.\d+)?)%", val)
                    if m:
                        self.bar_mode(False)
                        self.bar["value"] = float(m.group(1))
                        sp = re.search(r"\bat\s+(\S+)", val)
                        eta = re.search(r"ETA\s+(\S+)", val)
                        parts = [f"Downloading {float(m.group(1)):.0f}%"]
                        if sp and sp.group(1) != "Unknown":
                            parts.append(sp.group(1))
                        if eta and eta.group(1) != "Unknown":
                            parts.append("ETA " + eta.group(1))
                        self.status.set("  ·  ".join(parts))
                    else:
                        self.add_log(val)  # progress ticks stay out of the log
                        for prefix, text, busy in STAGES:
                            if val.startswith(prefix):
                                self.bar_mode(busy)
                                self.status.set(text)
                                break
                        else:
                            self.status.set(val[:90])
                elif kind == "status":
                    self.status.set(val)
                elif kind == "unlock":
                    self.locked = False
                    if not self.proc:
                        self.go.state(["!disabled"])
                elif kind == "done":
                    self.proc = None
                    if not self.locked:
                        self.go.state(["!disabled"])
                    self.stop.state(["disabled"])
                    self.bar_mode(False)
                    if val == 0:
                        self.bar["value"] = 100
                        self.status.set("Done ✔  → " + self.dest.get())
                    elif self.cancelled:
                        self.status.set("Cancelled.")
                    else:
                        self.status.set(explain(self.log, self.ck != "none"))
        except queue.Empty:
            pass
        self.after(100, self.poll)

    # ---- tool downloads / updates -----------------------------------------
    def missing_tools(self):
        missing = [] if os.path.exists(USER_BIN) else ["yt-dlp"]
        if IS_WIN:
            for name, (_, files) in WIN_TOOLS.items():
                if not all(os.path.exists(os.path.join(DATA_DIR, f)) for f in files):
                    missing.append(name)
        return missing

    def update_ytdlp(self):
        if self.proc or self.locked:
            return
        names = self.missing_tools()  # the button also repairs a failed first-run download
        if "yt-dlp" not in names:
            names.insert(0, "yt-dlp")
        self.fetch_tools(names, "Updating yt-dlp…", lock=False)

    def ensure_ytdlp(self):
        """First launch: download what's missing. Later launches: refresh yt-dlp quietly if over a week old."""
        missing = self.missing_tools()
        if missing:
            what = "tools (one time, may take a few minutes)" if IS_WIN else "yt-dlp"
            self.fetch_tools(missing, f"First run: downloading {what}…", lock=True)
        elif time.time() - os.path.getmtime(USER_BIN) > 7 * 86400:
            self.fetch_tools(["yt-dlp"], "Refreshing yt-dlp…", lock=False)

    def fetch_tools(self, names, msg, lock):
        self.status.set(msg)
        if lock:
            self.locked = True
            self.go.state(["disabled"])

        def work():
            def say(t):
                self.q.put(("status", t))
            try:
                v = ""
                for n in names:
                    if n == "yt-dlp":
                        v = get_ytdlp(say)
                    else:
                        get_zip_tool(n, say)
                say(f"yt-dlp {v} ready" if v else "Ready")
            except Exception as ex:
                say(f"Download failed: {ex}")
            self.q.put(("unlock", None))

        threading.Thread(target=work, daemon=True).start()

    def cancel(self):
        if self.proc:
            self.cancelled = True
            try:
                if IS_WIN:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                                   capture_output=True, **NOWIN)
                else:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except Exception:
                self.proc.terminate()

    def close(self):
        self.cancel()
        self.on_change()
        self.destroy()


if __name__ == "__main__":
    if IS_WIN:  # crisp text on scaled displays
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    if "--install" in sys.argv:
        install_entry()
    else:
        App().mainloop()
