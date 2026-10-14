#!/usr/bin/env bash
set -euo pipefail

# Run a downloaded file, not `curl | bash`: the wizard needs stdin.
ref="${TAXMAP_REF:-main}"
source_dir="${TAXMAP_SOURCE_DIR:-}"
work="$(mktemp -d)"
trap 'rm -f "$work/setup.py" "$work/uv-install.sh" "$work/revision.json"; rmdir "$work"' EXIT
trap 'exit 130' INT

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
command -v curl >/dev/null || fail "curl is required. Install it through your operating system and retry."
python=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null &&
        "$candidate" -c 'import sys, venv, ensurepip; assert sys.version_info >= (3, 10)' 2>/dev/null; then
        python="$("$candidate" -c 'import sys; print(sys.executable)')"
        break
    fi
done

if [[ -z "$python" ]]; then
    printf 'Python 3.10+ with venv/pip was not found.\n'
    printf 'Download uv from astral.sh and managed Python 3.13 into your user account?\n'
    printf 'No administrator access or shell-profile changes. Continue? [y/N] '
    read -r answer
    [[ "$answer" == y || "$answer" == Y ]] || fail "Python bootstrap cancelled. Install Python 3.10+ with venv/pip and retry."
    uv_dir="$HOME/.local/share/taxmap/bootstrap/bin"
    curl --fail --silent --show-error --location https://astral.sh/uv/0.13.0/install.sh -o "$work/uv-install.sh"
    UV_UNMANAGED_INSTALL="$uv_dir" sh "$work/uv-install.sh"
    export UV_PYTHON_INSTALL_DIR="$HOME/.local/share/taxmap/bootstrap/python"
    "$uv_dir/uv" python install 3.13 --no-bin
    python="$("$uv_dir/uv" python find --managed-python 3.13)"
fi

if [[ -n "$source_dir" ]]; then
    source_dir="$(cd "$source_dir" && pwd)"
    [[ -f "$source_dir/scripts/setup_project.py" ]] || fail "TAXMAP_SOURCE_DIR must point to a taxmap checkout."
    "$python" "$source_dir/scripts/setup_project.py" --source "$source_dir"
else
    [[ "$ref" =~ ^[A-Za-z0-9._/-]+$ && "$ref" != *..* ]] || fail "Invalid TAXMAP_REF."
    curl --fail --silent --show-error --location \
        "https://api.github.com/repos/Cocoa-County/tax-map/commits/$ref" -o "$work/revision.json"
    revision="$("$python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha"])' "$work/revision.json")"
    [[ "$revision" =~ ^[0-9a-f]{40}$ ]] || fail "GitHub did not return a valid repository revision."
    curl --fail --silent --show-error --location \
        "https://raw.githubusercontent.com/Cocoa-County/tax-map/$revision/scripts/setup_project.py" -o "$work/setup.py"
    "$python" "$work/setup.py" --revision "$revision"
fi
