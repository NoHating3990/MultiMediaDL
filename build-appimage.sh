#!/usr/bin/env bash
# Builds a self-contained YTDownloader AppImage.
# Put this script and ytdlp-gui.py in /home/hate/Downloads/YTDownloader/, then run:  bash build-appimage.sh
# yt-dlp is NOT bundled: the app downloads it on first launch and keeps it fresh.
# Needs: internet, curl, tar (with xz). Downloads are cached in ./cache so re-runs are fast.
set -euo pipefail

BUILD="${BUILD_DIR:-/home/hate/Downloads/YTDownloader}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE="$BUILD/cache"
APPDIR="$BUILD/AppDir"
OUT="$BUILD/YTDownloader-x86_64.AppImage"
mkdir -p "$BUILD" "$CACHE"

SRC=""
for d in "$HERE" "$BUILD"; do
  if [ -f "$d/ytdlp-gui.py" ]; then SRC="$d/ytdlp-gui.py"; break; fi
done
[ -n "$SRC" ] || { echo "ERROR: ytdlp-gui.py not found next to this script or in $BUILD"; exit 1; }

dl() { # dl URL DEST
  if [ -s "$2" ]; then echo "  cached:      $(basename "$2")"; return 0; fi
  echo "  downloading: $(basename "$2")"
  curl -fL --retry 3 -o "$2.part" "$1"
  mv "$2.part" "$2"
}

echo "[1/6] Downloading dependencies into $CACHE"

# Portable Python 3.12 that includes Tk (python-build-standalone)
if ! ls "$CACHE"/cpython-*.tar.gz >/dev/null 2>&1; then
  PY_URL=$(curl -fsSL https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest \
    | grep -oE '"browser_download_url": *"[^"]*cpython-3\.12\.[0-9]+[^"]*x86_64-unknown-linux-gnu-install_only\.tar\.gz"' \
    | head -1 | sed -E 's/.*"(https[^"]*)"$/\1/')
  [ -n "$PY_URL" ] || { echo "ERROR: couldn't find a Python build URL (GitHub API rate limit? try again later)"; exit 1; }
  dl "$PY_URL" "$CACHE/$(basename "$PY_URL" | sed 's/%2B/+/')"
fi
PY_TAR="$(ls "$CACHE"/cpython-*.tar.gz | head -1)"

dl "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz" "$CACHE/ffmpeg-static.tar.xz"
# yt-dlp needs a JS runtime for YouTube; deno is a single binary
dl "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip" "$CACHE/deno.zip"
dl "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage" "$CACHE/appimagetool"
chmod +x "$CACHE/appimagetool"

echo "[2/6] Assembling AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/ytdlp-gui"

tar -xzf "$PY_TAR" -C "$APPDIR/usr"            # -> usr/python
rm -rf "$APPDIR"/usr/python/lib/python3*/test
PY="$APPDIR/usr/python/bin/python3"

# Modern UI theme (pure Python, ~1 MB). If this fails the app still works with the plain theme.
"$PY" -m pip install --quiet --no-warn-script-location sv-ttk || echo "  WARNING: couldn't install sv-ttk; UI will use the plain theme"

TMP="$BUILD/tmp_extract"; rm -rf "$TMP"; mkdir -p "$TMP"
tar -xJf "$CACHE/ffmpeg-static.tar.xz" -C "$TMP" --strip-components=1 --wildcards '*/ffmpeg' '*/ffprobe'
install -m755 "$TMP/ffmpeg" "$TMP/ffprobe" "$APPDIR/usr/bin/"
rm -rf "$TMP"; mkdir -p "$TMP"
"$PY" -m zipfile -e "$CACHE/deno.zip" "$TMP"
install -m755 "$TMP/deno" "$APPDIR/usr/bin/deno"
rm -rf "$TMP"

install -m644 "$SRC" "$APPDIR/usr/share/ytdlp-gui/ytdlp-gui.py"

echo "[3/6] Writing launcher, desktop file, icon"
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
export APPDIR="$HERE"
export PATH="$HERE/usr/bin:$PATH"
for d in "$HERE"/usr/python/lib/tcl[89].*; do [ -d "$d" ] && export TCL_LIBRARY="$d" && break; done
for d in "$HERE"/usr/python/lib/tk[89].*;  do [ -d "$d" ] && export TK_LIBRARY="$d"  && break; done
for c in /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/certs/ca-certificates.crt; do
  [ -f "$c" ] && export SSL_CERT_FILE="$c" && break
done
exec "$HERE/usr/python/bin/python3" "$HERE/usr/share/ytdlp-gui/ytdlp-gui.py" "$@"
EOF
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/ytdlp-gui.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=YT Downloader
Comment=Download video/audio with yt-dlp
Exec=AppRun
Icon=ytdlp-gui
Categories=AudioVideo;Network;
Terminal=false
EOF

cat > "$APPDIR/ytdlp-gui.svg" <<'EOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <rect width="128" height="128" rx="26" fill="#d62828"/>
  <path d="M64 24v50M40 52l24 26 24-26" fill="none" stroke="#fff" stroke-width="12" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M30 98h68" stroke="#fff" stroke-width="12" stroke-linecap="round"/>
</svg>
EOF
ln -s ytdlp-gui.svg "$APPDIR/.DirIcon"

echo "[4/6] Sanity checks"
export APPDIR PATH="$APPDIR/usr/bin:$PATH"
for d in "$APPDIR"/usr/python/lib/tcl[89].*; do [ -d "$d" ] && export TCL_LIBRARY="$d" && break; done
for d in "$APPDIR"/usr/python/lib/tk[89].*;  do [ -d "$d" ] && export TK_LIBRARY="$d"  && break; done
"$PY" -c "import tkinter; print('  tkinter OK')"
"$PY" -c "import sv_ttk; print('  sv-ttk OK')" 2>/dev/null || echo "  WARNING: sv-ttk missing (plain theme)" || echo "  WARNING: tkinter failed to import"
"$APPDIR/usr/bin/ffmpeg" -hide_banner -encoders 2>/dev/null | grep -qE 'dnxhd' && echo "  ffmpeg: dnxhd OK" || echo "  WARNING: ffmpeg lacks dnxhd"
"$APPDIR/usr/bin/ffmpeg" -hide_banner -encoders 2>/dev/null | grep -q 'libmp3lame' && echo "  ffmpeg: mp3 OK" || echo "  WARNING: ffmpeg lacks libmp3lame"

for enc in libx264 libx265 libsvtav1 prores_ks; do
  "$APPDIR/usr/bin/ffmpeg" -hide_banner -encoders 2>/dev/null | grep -q "$enc" && echo "  ffmpeg: $enc OK" || echo "  WARNING: ffmpeg lacks $enc"
done

echo "[5/6] Packing AppImage"
rm -f "$OUT"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$CACHE/appimagetool" "$APPDIR" "$OUT"
chmod +x "$OUT"

echo "[6/6] Done: $OUT  ($(du -h "$OUT" | cut -f1))"
echo "Add it to your start menu with:  \"$OUT\" --install"
