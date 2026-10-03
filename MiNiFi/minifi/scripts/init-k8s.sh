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
mapfile -t source_info < <(printf '%s\n' "$source_info_json" | "$python_cmd" -c 'import json, sys; value=json.load(sys.stdin); print(value["namespace"]); print(value["databaseSecretName"]); print(value["s3SecretName"]); print(value["usernameEnv"]); print(value["passwordEnv"])')
[[ "${#source_info[@]}" -eq 5 ]] || { printf 'MiniFi source metadata failed\n' >&2; exit 1; }
namespace=${source_info[0]}
database_secret_base=${source_info[1]}
s3_secret_name=${source_info[2]}
username_env=${source_info[3]}
password_env=${source_info[4]}

env_value() {
    "$python_cmd" - "$env_path" "$1" <<'PY'
import sys
from pathlib import Path

path, wanted = sys.argv[1:]
for raw_line in Path(path).read_text(encoding="utf-8-sig").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key.strip() == wanted:
        print(value.strip().strip('"').strip("'"))
        break
PY
}

require_env_value() {
    local value
    value=$(env_value "$1")
    [[ -n "$value" ]] || { printf 'Missing required .env key: %s\n' "$1" >&2; exit 1; }
    printf '%s' "$value"
}

s3_host=$(require_env_value S3_HOST)
s3_access_key=$(require_env_value S3_ACCESS_KEY)
s3_access_secret=$(require_env_value S3_ACCESS_SECRET)
s3_bucket_name=$(require_env_value S3_BUCKET_NAME)
database_username=$(require_env_value "$username_env")
database_password=$(require_env_value "$password_env")

apply_s3_secret() {
    "$python_cmd" - "$s3_secret_name" "$namespace" "$s3_host" "$s3_access_key" "$s3_access_secret" "$s3_bucket_name" <<'PY' | kubectl apply -f -
import json
import sys

name, namespace, host, access_key, access_secret, bucket_name = sys.argv[1:]
print(json.dumps({
    "apiVersion": "v1",
    "kind": "Secret",
    "metadata": {"name": name, "namespace": namespace},
    "type": "Opaque",
    "stringData": {"host": host, "accessKey": access_key, "accessSecret": access_secret, "bucketName": bucket_name},
}))
PY
}

apply_database_secret() {
    local name=$1
    "$python_cmd" - "$name" "$namespace" "$database_username" "$database_password" <<'PY' | kubectl apply -f -
import json
import sys

name, namespace, username, password = sys.argv[1:]
print(json.dumps({
    "apiVersion": "v1",
    "kind": "Secret",
    "metadata": {"name": name, "namespace": namespace},
    "type": "Opaque",
    "stringData": {"username": username, "password": password},
}))
PY
}

apply_s3_secret

for dir in "${rendered_dirs[@]}"; do
    printf 'Applying rendered MiniFi manifests from %s\n' "$dir"
    group_slug=$(basename "$dir")
    database_secret_name="$database_secret_base-$group_slug"
    apply_database_secret "$database_secret_name"
    kubectl apply -f "$dir/configmap-templates.yaml"
    kubectl apply -f "$dir/configmap-runtime.yaml"
done