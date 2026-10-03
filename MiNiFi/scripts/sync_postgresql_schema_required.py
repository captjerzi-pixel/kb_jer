"""Mark schema columns optional when they are absent from PostgreSQL tables.

The script intentionally edits only schema files referenced by the selected
pipeline. It defaults to a dry run and to the first 60 tables.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

try:
    import psycopg
except ImportError as exc:  # pragma: no cover - exercised by the CLI
    try:
        import psycopg2 as psycopg
    except ImportError:
        raise SystemExit(
            "Missing PostgreSQL driver. Install one of: "
            "python -m pip install 'psycopg[binary]' or "
            "python -m pip install psycopg2-binary"
        ) from exc


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "example" / "pipelines" / "t24-load.yaml"
DEFAULT_ENV = ROOT / ".env"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def resolve_schema_ref(config_path: Path, schema_ref: str) -> Path:
    candidates = [config_path.parent / schema_ref, ROOT / schema_ref]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError(f"Schema file not found: {schema_ref}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mark absent PostgreSQL schema columns as required: false."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--db-schema", default="public", help="Physical PostgreSQL schema")
    parser.add_argument("--apply", action="store_true", help="Write changes; otherwise dry-run")
    return parser.parse_args()


def column_names(schema_path: Path) -> list[str]:
    document = yaml.safe_load(schema_path.read_text(encoding="utf-8")) or {}
    return [str(column["name"]) for column in document.get("columns", []) if "name" in column]


def mark_optional(schema_path: Path, absent: set[str], apply: bool) -> int:
    if not absent:
        return 0

    text = schema_path.read_text(encoding="utf-8")
    changed = 0
    output: list[str] = []
    # Supports the repository's inline column form while preserving formatting.
    pattern = re.compile(r"^(?P<prefix>\s*-\s*\{\s*name:\s*)(?P<name>[^,}]+)(?P<rest>.*)$")
    for line in text.splitlines(keepends=True):
        match = pattern.match(line)
        if not match or match.group("name").strip() not in absent:
            output.append(line)
            continue
        rest = match.group("rest")
        if re.search(r"\brequired\s*:", rest):
            output.append(line)
            continue
        newline = "" if not line.endswith(("\n", "\r")) else line[len(line.rstrip("\r\n")):]
        body = line.rstrip("\r\n")
        close = body.rfind("}")
        if close < 0:
            output.append(line)
            continue
        body = body[:close].rstrip() + ", required: false " + body[close:]
        output.append(body + newline)
        changed += 1

    if changed and apply:
        schema_path.write_text("".join(output), encoding="utf-8")
    return changed


def main() -> int:
    args = parse_args()
    if args.limit < 1:
        raise SystemExit("--limit must be at least 1")

    config_path = args.config.resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    tables = config.get("tables", [])[: args.limit]
    env = load_env(args.env.resolve())
    source = config.get("source", {})

    required_env = {
        "host": source.get("hostEnv", "PSQL_HOSTNAME"),
        "dbname": source.get("databaseEnv", "PSQL_DB"),
        "user": source.get("usernameEnv", "PSQL_USERNAME"),
        "password": source.get("passwordEnv", "PSQL_PASSWORD"),
    }
    missing = [key for key, env_name in required_env.items() if not env.get(env_name)]
    if missing:
        raise SystemExit("Missing database values in .env for: " + ", ".join(missing))

    connection_kwargs: dict[str, Any] = {
        "host": env[required_env["host"]],
        "dbname": env[required_env["dbname"]],
        "user": env[required_env["user"]],
        "password": env[required_env["password"]],
    }
    host = connection_kwargs["host"]
    if isinstance(host, str) and host.count(":") == 1:
        host_name, host_port = host.rsplit(":", 1)
        if host_port.isdigit():
            connection_kwargs["host"] = host_name
            connection_kwargs["port"] = host_port
    if env.get("PSQL_PORT"):
        connection_kwargs["port"] = env["PSQL_PORT"]
    if env.get("PSQL_SSLMODE"):
        connection_kwargs["sslmode"] = env["PSQL_SSLMODE"]

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Mode: {mode}; tables: {len(tables)}; physical schema: {args.db_schema}")
    total_changed = 0
    connection_kwargs["connect_timeout"] = 15
    connection = psycopg.connect(**connection_kwargs)
    connection.autocommit = True
    with connection:
        with connection.cursor() as cursor:
            for table in tables:
                table_name = str(table["name"])
                schema_ref = table.get("schemaRef")
                if not schema_ref:
                    print(f"SKIP {table_name}: no schemaRef")
                    continue
                schema_path = resolve_schema_ref(config_path, schema_ref)
                expected = column_names(schema_path)
                cursor.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    """,
                    (args.db_schema, table_name),
                )
                actual = {str(row[0]).lower() for row in cursor.fetchall()}
                absent = {name for name in expected if name.lower() not in actual}
                changed = mark_optional(schema_path, absent, args.apply)
                total_changed += changed
                status = "updated" if args.apply else "would update"
                if absent:
                    print(f"{table_name}: {status} {changed} columns: {', '.join(sorted(absent))}")
                else:
                    print(f"{table_name}: OK")

    print(f"Total schema columns {('updated' if args.apply else 'to update')}: {total_changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
