#!/bin/bash
# Convert a clip to 48 kHz 16-bit mono WAV, loudness-normalize, install into
# the Dino media folder.
#
# Usage:
#   import_media.sh [-f] file [file ...]
#   import_media.sh ./new_file.mp3
#   import_media.sh ~/walk.wav ~/roar.mp3
#
# Options:
#   -f    overwrite an existing dest wav
#   -d DIR  media directory (default /opt/dino-media-player/media)
set -euo pipefail

MEDIA_DIR=/opt/dino-media-player/media
FORCE=0
LUFS=-16
TRUE_PEAK=-1.5

usage() {
  echo "Usage: $0 [-f] [-d MEDIA_DIR] file [file ...]" >&2
  exit 2
}

while getopts ":fd:" opt; do
  case "$opt" in
    f) FORCE=1 ;;
    d) MEDIA_DIR="$OPTARG" ;;
    *) usage ;;
  esac
done
shift $((OPTIND - 1))
[[ $# -ge 1 ]] || usage

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required. Install with: sudo apt install -y ffmpeg" >&2
  exit 1
fi

if [[ ! -d "$MEDIA_DIR" ]]; then
  echo "Media directory not found: $MEDIA_DIR" >&2
  exit 1
fi

install_as_dino() {
  local src=$1 dest=$2
  if command -v sudo >/dev/null 2>&1 && id dino >/dev/null 2>&1; then
    sudo install -o dino -g dino -m 0644 "$src" "$dest"
  else
    install -m 0644 "$src" "$dest"
  fi
}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

failed=0
for src in "$@"; do
  if [[ ! -f "$src" ]]; then
    echo "Skip (not a file): $src" >&2
    failed=1
    continue
  fi

  base=$(basename -- "$src")
  stem=${base%.*}
  stem=$(echo "$stem" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9._-]+/_/g; s/_+/_/g; s/^_|_$//g')
  [[ -n "$stem" ]] || stem=clip
  dest="$MEDIA_DIR/${stem}.wav"

  if [[ -e "$dest" && "$FORCE" -ne 1 ]]; then
    echo "Exists (use -f to overwrite): $dest" >&2
    failed=1
    continue
  fi

  tmp="$work/${stem}.wav"
  echo "Importing $src -> $dest"
  if ! ffmpeg -hide_banner -loglevel error -y -i "$src" \
      -ac 1 -ar 48000 \
      -af "loudnorm=I=${LUFS}:TP=${TRUE_PEAK}:LRA=11" \
      -sample_fmt s16 \
      "$tmp"; then
    echo "ffmpeg failed: $src" >&2
    failed=1
    continue
  fi

  install_as_dino "$tmp" "$dest"
  echo "Installed $dest"
done

exit "$failed"
