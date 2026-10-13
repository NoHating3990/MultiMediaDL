#!/usr/bin/env bash
# Builds a self-contained MultiMedia Downloader AppImage.
# Put this script and ytdlp-gui.py in /home/hate/Downloads/YTDownloader/, then run:  bash build-appimage.sh
# yt-dlp is NOT bundled: the app downloads it on first launch and keeps it fresh.
# Python: conda-forge's build (via micromamba). Neither it nor python-build-standalone ships a Tk with
# Xft (anti-aliased fonts), so we compile Tcl/Tk with --enable-xft inside an Ubuntu 22.04 container
# (podman, preinstalled on Bazzite) and swap it in. If that step can't run, the build still works,
# the text is just blocky.
# Needs: internet, curl, tar (with xz). Downloads are cached in ./cache so re-runs are fast.
set -euo pipefail

BUILD="${BUILD_DIR:-/home/hate/Downloads/YTDownloader}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE="$BUILD/cache"
APPDIR="$BUILD/AppDir"
OUT="$BUILD/MultiMedia-Downloader-x86_64.AppImage"
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

# micromamba: tiny static binary used to install Python + Tk from conda-forge
get_micromamba() {
  [ -x "$CACHE/micromamba" ] && { echo "  cached:      micromamba"; return 0; }
  if ! dl "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-linux-64" "$CACHE/micromamba"; then
    echo "  GitHub copy failed, trying micro.mamba.pm"
    mkdir -p "$BUILD/mm_tmp"
    curl -fL https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xj -C "$BUILD/mm_tmp" bin/micromamba || return 1
    install -m755 "$BUILD/mm_tmp/bin/micromamba" "$CACHE/micromamba"
    rm -rf "$BUILD/mm_tmp"
  fi
  chmod +x "$CACHE/micromamba"
}
get_micromamba || echo "  WARNING: couldn't get micromamba; will use the fallback Python (blocky fonts)"

# Compile Tcl/Tk with Xft support (needs podman + internet once; result is cached in $CACHE/tk-xft)
build_tk_xft() {
  local OUTD="$CACHE/tk-xft"
  if [ -f "$OUTD/lib/libtk8.6.so" ]; then echo "  cached:      Xft-enabled Tk"; return 0; fi
  command -v podman >/dev/null 2>&1 || { echo "  podman not found"; return 1; }
  rm -rf "$OUTD"; mkdir -p "$OUTD"
  cat > "$OUTD/inner.sh" <<'INNER'
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq build-essential curl ca-certificates libx11-dev libxft-dev libfontconfig1-dev libfreetype-dev libxrender-dev libxext-dev
cd /tmp
V=8.6.16
curl -fL --retry 3 -o tcl.tgz "https://downloads.sourceforge.net/project/tcl/Tcl/$V/tcl$V-src.tar.gz"
curl -fL --retry 3 -o tk.tgz  "https://downloads.sourceforge.net/project/tcl/Tcl/$V/tk$V-src.tar.gz"
tar xzf tcl.tgz; tar xzf tk.tgz
(cd tcl$V/unix && ./configure --prefix=/out --enable-threads --enable-64bit >/dev/null && make -j"$(nproc)" >/dev/null && make install >/dev/null)
(cd tk$V/unix && ./configure --prefix=/out --with-tcl=/tmp/tcl$V/unix --enable-threads --enable-64bit --enable-xft >/dev/null && make -j"$(nproc)" >/dev/null && make install >/dev/null)
echo "  Tcl/Tk $V built with Xft"
INNER
  echo "  compiling Tcl/Tk with Xft in a container (first time takes a few minutes)"
  podman run --rm -v "$OUTD:/out:Z" docker.io/library/ubuntu:22.04 bash /out/inner.sh || return 1
  [ -f "$OUTD/lib/libtk8.6.so" ] && [ -f "$OUTD/lib/libtcl8.6.so" ]
}

# Swap our Xft-enabled Tcl/Tk into the conda Python (same 8.6 ABI, same library names)
install_tk_xft() {
  local L="$APPDIR/usr/python/lib" S="$CACHE/tk-xft/lib" f
  for f in libtcl8.6.so libtk8.6.so; do rm -f "$L/$f"; cp "$S/$f" "$L/$f"; done  # rm first: conda files are hardlinks into its cache
  for f in tcl8.6 tk8.6 tcl8; do
    rm -rf "$L/$f"
    [ -d "$S/$f" ] && cp -r "$S/$f" "$L/$f"
  done
  return 0
}

# Fallback Python (python-build-standalone): works, but its Tk has no Xft
build_python_pbs() {
  if ! ls "$CACHE"/cpython-*.tar.gz >/dev/null 2>&1; then
    PY_URL=$(curl -fsSL https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest \
      | grep -oE '"browser_download_url": *"[^"]*cpython-3\.12\.[0-9]+[^"]*x86_64-unknown-linux-gnu-install_only\.tar\.gz"' \
      | head -1 | sed -E 's/.*"(https[^"]*)"$/\1/')
    [ -n "$PY_URL" ] || { echo "ERROR: couldn't find a Python build URL (GitHub API rate limit? try again later)"; exit 1; }
    dl "$PY_URL" "$CACHE/$(basename "$PY_URL" | sed 's/%2B/+/')"
  fi
  tar -xzf "$(ls "$CACHE"/cpython-*.tar.gz | head -1)" -C "$APPDIR/usr"   # -> usr/python
}

build_python_conda() {
  [ -x "$CACHE/micromamba" ] || return 1
  MAMBA_ROOT_PREFIX="$BUILD/mamba-root" "$CACHE/micromamba" create -y --no-rc -p "$APPDIR/usr/python" \
    -c conda-forge --override-channels "python=3.12" tk || return 1
  [ -x "$APPDIR/usr/python/bin/python3" ]
}

dl "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz" "$CACHE/ffmpeg-static.tar.xz"
# yt-dlp needs a JS runtime for YouTube; deno is a single binary
dl "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip" "$CACHE/deno.zip"
dl "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage" "$CACHE/appimagetool"
chmod +x "$CACHE/appimagetool"

echo "[2/6] Assembling AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/ytdlp-gui"

echo "  installing Python + Tk (first time downloads ~150-250 MB)"
PYSRC=conda
if ! build_python_conda; then
  echo "  WARNING: conda-forge Python failed; falling back to python-build-standalone (fonts will be blocky)"
  rm -rf "$APPDIR/usr/python"
  build_python_pbs
  PYSRC=pbs
fi
if [ "$PYSRC" = conda ]; then
  if build_tk_xft; then
    install_tk_xft && echo "  installed Xft-enabled Tk"
  else
    echo "  WARNING: couldn't build the Xft Tk (needs podman + internet); text will be blocky"
  fi
fi
rm -rf "$APPDIR"/usr/python/lib/python3*/test "$APPDIR"/usr/python/include "$APPDIR"/usr/python/conda-meta \
       "$APPDIR"/usr/python/share/man 2>/dev/null || true
find "$APPDIR/usr/python/lib" -name '*.a' -delete 2>/dev/null || true
PY="$APPDIR/usr/python/bin/python3"


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
[ -f /etc/fonts/fonts.conf ] && export FONTCONFIG_FILE=/etc/fonts/fonts.conf FONTCONFIG_PATH=/etc/fonts
exec "$HERE/usr/python/bin/python3" "$HERE/usr/share/ytdlp-gui/ytdlp-gui.py" "$@"
EOF
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/ytdlp-gui.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=MultiMedia Downloader
Comment=Download video/audio with yt-dlp
Exec=AppRun
Icon=ytdlp-gui
Categories=AudioVideo;Network;
Terminal=false
EOF

cat > "$APPDIR/ytdlp-gui.svg" <<'EOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <rect width="128" height="128" rx="26" fill="#E8A600"/>
  <path d="M64 24v50M40 52l24 26 24-26" fill="none" stroke="#000" stroke-width="12" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M30 98h68" stroke="#000" stroke-width="12" stroke-linecap="round"/>
</svg>
EOF
ln -s ytdlp-gui.svg "$APPDIR/.DirIcon"

echo "[4/6] Sanity checks"
export APPDIR PATH="$APPDIR/usr/bin:$PATH"
for d in "$APPDIR"/usr/python/lib/tcl[89].*; do [ -d "$d" ] && export TCL_LIBRARY="$d" && break; done
for d in "$APPDIR"/usr/python/lib/tk[89].*;  do [ -d "$d" ] && export TK_LIBRARY="$d"  && break; done
[ -f /etc/fonts/fonts.conf ] && export FONTCONFIG_FILE=/etc/fonts/fonts.conf FONTCONFIG_PATH=/etc/fonts
"$PY" -c "import tkinter; print('  tkinter OK')" || echo "  WARNING: tkinter failed to import"
if [ -n "${DISPLAY:-}" ]; then
  FS=$("$PY" -c "import tkinter; print(tkinter.Tk().tk.eval('::tk::pkgconfig get fontsystem'))" 2>/dev/null || echo "none")
  if [ "$FS" = "xft" ]; then echo "  Tk font system: xft (smooth fonts) OK"
  else echo "  WARNING: Tk font system is '$FS', not xft: text will look blocky"; fi
fi
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
