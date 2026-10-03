#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
renderer="$script_dir/render_minifi.py"
python_cmd=$(command -v python3 || command -v python || true)
minifi_root=$(cd "$script_dir/.." && pwd)
repo_root=$(cd "$minifi_root/.." && pwd)
config=
output_root="$minifi_root/rendered"
env_path="$repo_root/.env"
run_timestamp=$(date -u '+%Y%m%dT%H%M%SZ')

usage() {
    printf 'Usage: %s --config PATH [--output-root PATH] [--env PATH] [--run-timestamp VALUE]\n' "$(basename "$0")" >&2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config|--output-root|--env|--run-timestamp)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            case "$1" in
                --config) config=$2 ;;
                --output-root) output_root=$2 ;;
                --env) env_path=$2 ;;
                --run-timestamp) run_timestamp=$2 ;;
            esac
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
exec "$python_cmd" "$renderer" render \
    --config "$config" \
    --output-root "$output_root" \
    --env "$env_path" \
    --run-timestamp "$run_timestamp"