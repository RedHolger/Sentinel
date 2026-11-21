#!/bin/sh
# Explicit dependency acquisition and portfolio build step. Demo/benchmark/eval remain offline.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
release_dir="$root/.release"
command -v uv >/dev/null || { echo "uv is required. Install via https://docs.astral.sh/uv/" >&2; exit 1; }
mkdir -p "$release_dir/wheels"

uv build --wheel --out-dir "$release_dir/wheels" "$root/adi-common"
uv build --wheel --out-dir "$release_dir/wheels" "$root/adi-runtime"
uv build --wheel --out-dir "$release_dir/wheels" "$root/adi-app"

if [ ! -x "$release_dir/venv/bin/python" ]; then
    uv venv --python 3.11 "$release_dir/venv"
fi

# Install all wheels through normal dependency resolution (no --no-deps workaround)
uv pip install --python "$release_dir/venv/bin/python" \
    "$release_dir/wheels"/adi_common-*.whl \
    "$release_dir/wheels"/adi_runtime-*.whl \
    "$release_dir/wheels"/adi_app-*.whl

printf 'Installed. Run %s/venv/bin/adi-portfolio demo\n' "$release_dir"
