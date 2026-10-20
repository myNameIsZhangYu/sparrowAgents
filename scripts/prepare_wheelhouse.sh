#!/usr/bin/env bash
set -euo pipefail

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

output="${1:-.wheelhouse}"
mkdir -p "$output"
rm -rf "$output"/*

UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}" uv export \
  --format requirements.txt \
  --frozen \
  --all-extras \
  --no-dev \
  --no-emit-project \
  --no-hashes \
  --output-file "$output/requirements.txt"

UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}" uv run \
  --python 3.11 \
  --no-project \
  python -m pip download \
  --dest "$output" \
  --only-binary=:all: \
  --requirement "$output/requirements.txt" \

UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}" uv build \
  --wheel \
  --out-dir "$output/project"

printf 'wheelhouse ready: %s\n' "$output"
