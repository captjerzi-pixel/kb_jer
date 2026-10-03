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
runtime_parameters=()

usage() {
    printf 'Usage: %s --config PATH [--output-root PATH] [--env PATH] [--run-timestamp VALUE] [--parameter NAME=value]...\n' "$(basename "$0")" >&2
}

add_parameters() {
    local value item
    value=$1
    IFS=',' read -r -a items <<< "$value"
    for item in "${items[@]}"; do
        [[ "$item" =~ ^[A-Z][A-Z0-9_]*=.+$ ]] || {
            printf 'Runtime parameter must use NAME=value format: %s\n' "$item" >&2
            exit 2
        }
        runtime_parameters+=("$item")
    done
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config|-config|--output-root|-output-root|--env|-env|--run-timestamp|-run-timestamp|--parameter|-parameter|--param|-param)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            case "$1" in
                --config|-config) config=$2 ;;
                --output-root|-output-root) output_root=$2 ;;
                --env|-env) env_path=$2 ;;
                --run-timestamp|-run-timestamp) run_timestamp=$2 ;;
                --parameter|-parameter|--param|-param) add_parameters "$2" ;;
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
command -v kubectl >/dev/null || { printf 'kubectl is required\n' >&2; exit 1; }

render_args=(render --config "$config" --output-root "$output_root" --env "$env_path" --run-timestamp "$run_timestamp")
for parameter in "${runtime_parameters[@]}"; do
    render_args+=(--param "$parameter")
done
render_output=$("$python_cmd" "$renderer" "${render_args[@]}")
mapfile -t rendered_dirs <<< "$render_output"
[[ "${#rendered_dirs[@]}" -gt 0 ]] || { printf 'Renderer did not return any manifest directories\n' >&2; exit 1; }

source_info_json=$("$python_cmd" "$renderer" source-info --config "$config")
mapfile -t source_info < <(printf '%s\n' "$source_info_json" | "$python_cmd" -c 'import json, sys; print(json.load(sys.stdin)["namespace"])')
[[ "${#source_info[@]}" -eq 1 ]] || { printf 'MiniFi source metadata failed\n' >&2; exit 1; }
namespace=${source_info[0]}

for dir in "${rendered_dirs[@]}"; do
    job_name=$(awk '/^[[:space:]]+name:[[:space:]]*/ { print $2; exit }' "$dir/job.yaml")
    [[ -n "$job_name" ]] || { printf 'Could not find Job name in %s/job.yaml\n' "$dir" >&2; exit 1; }
    kubectl delete job "$job_name" -n "$namespace" --ignore-not-found --wait=true
    kubectl apply -f "$dir/job.yaml"

    deadline=$((SECONDS + 43200))
    while (( SECONDS < deadline )); do
        succeeded=$(kubectl get job "$job_name" -n "$namespace" -o jsonpath='{.status.succeeded}' 2>/dev/null || true)
        failed=$(kubectl get job "$job_name" -n "$namespace" -o jsonpath='{.status.failed}' 2>/dev/null || true)
        active=$(kubectl get job "$job_name" -n "$namespace" -o jsonpath='{.status.active}' 2>/dev/null || true)
        if [[ "$succeeded" =~ ^[1-9][0-9]*$ ]]; then
            printf 'MiniFi Job %s completed successfully\n' "$job_name"
            kubectl logs "job/$job_name" -n "$namespace" --tail=200
            break
        fi
        if [[ "$failed" =~ ^[1-9][0-9]*$ ]]; then
            printf 'MiniFi Job %s failed; recent logs:\n' "$job_name" >&2
            kubectl logs "job/$job_name" -n "$namespace" --tail=300
            exit 1
        fi
        printf 'Waiting for MiniFi Job %s (active=%s succeeded=%s failed=%s)\n' "$job_name" "$active" "$succeeded" "$failed"
        sleep 30
    done
    if (( SECONDS >= deadline )); then
        kubectl logs "job/$job_name" -n "$namespace" --tail=300 || true
        printf 'Timed out waiting for MiniFi Job %s\n' "$job_name" >&2
        exit 1
    fi
done