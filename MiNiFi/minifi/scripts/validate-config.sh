#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
renderer="$script_dir/render_minifi.py"
python_cmd=$(command -v python3 || command -v python || true)
config=

usage() {
    printf 'Usage: %s --config PATH\n' "$(basename "$0")" >&2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            config=$2
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown argument: %s\n' "$1" >&2
            usage
            exit 2
            ;;
    esac
done

[[ -n "$config" ]] || { usage; exit 2; }
[[ -n "$python_cmd" ]] || { printf 'python3 or python is required\n' >&2; exit 1; }
exec "$python_cmd" "$renderer" validate --config "$config"