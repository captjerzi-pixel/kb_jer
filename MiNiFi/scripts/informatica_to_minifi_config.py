#!/usr/bin/env python3
"""Convert Informatica workflow/source XML exports into MiniFi pipeline YAML and schema refs."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

ATTR_PATTERN = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
OBJECT_PATTERN = re.compile(r'<(SOURCE|TARGET)\s+([^>]*)>', re.IGNORECASE)
OBJECT_END_PATTERN = re.compile(r'</(SOURCE|TARGET)\s*>', re.IGNORECASE)
FIELD_PATTERN = re.compile(r'<(SOURCEFIELD|TARGETFIELD|TRANSFORMFIELD)\s+([^>]*)>', re.IGNORECASE)

TYPE_MAP = {
    'string': 'string',
    'nstring': 'string',
    'varchar': 'string',
    'nvarchar': 'string',
    'char': 'string',
    'nchar': 'string',
    'text': 'string',
    'integer': 'integer',
    'int': 'integer',
    'bigint': 'integer',
    'smallint': 'integer',
    'decimal': 'decimal',
    'numeric': 'decimal',
    'number': 'decimal',
    'double': 'float',
    'float': 'float',
    'real': 'float',
    'date/time': 'timestamp',
    'datetime': 'timestamp',
    'timestamp': 'timestamp',
    'date': 'date',
    'boolean': 'boolean',
    'bool': 'boolean',
}


def parse_attrs(raw: str) -> dict[str, str]:
    return {key: value for key, value in ATTR_PATTERN.findall(raw)}


def parse_sources(path: Path) -> list[dict]:
    sources: list[dict] = []
    targets_by_name: dict[str, dict] = {}
    current: dict | None = None
    current_kind = ''
    for line in path.read_text(encoding='utf-8-sig', errors='replace').splitlines():
        object_match = OBJECT_PATTERN.search(line)
        if object_match:
            current_kind = object_match.group(1).upper()
            current = parse_attrs(object_match.group(2))
            current['fields'] = []
            if current_kind == 'SOURCE':
                sources.append(current)
            elif current_kind == 'TARGET' and current.get('NAME'):
                targets_by_name[current['NAME'].upper()] = current
            if '/>' in line:
                current = None
                current_kind = ''
            continue
        field_match = FIELD_PATTERN.search(line)
        if field_match and current is not None:
            current['fields'].append(parse_attrs(field_match.group(2)))
        if OBJECT_END_PATTERN.search(line):
            current = None
            current_kind = ''
    for source in sources:
        if not source.get('fields') and source.get('NAME', '').upper() in targets_by_name:
            source['fields'] = targets_by_name[source['NAME'].upper()].get('fields') or []
    return sources


def safe_output_name(value: str) -> str:
    normalized = re.sub(r'[^A-Za-z0-9_-]+', '_', value.lower()).strip('_-')
    return normalized or 'table'


def quoted_table(source_type: str, schema: str, table: str) -> str:
    if source_type == 'postgresql':
        return f'{schema}."{table}"'
    return f'{schema}.{table}'


def normalize_type(value: str | None) -> str:
    if not value:
        return ''
    normalized = value.strip().lower()
    return TYPE_MAP.get(normalized, normalized)


def field_to_column(field: dict[str, str]) -> dict:
    column = {'name': field['NAME']}
    data_type = normalize_type(field.get('DATATYPE') or field.get('TYPE'))
    if data_type:
        column['type'] = data_type
    if field.get('PRECISION'):
        column['precision'] = field['PRECISION']
    if field.get('SCALE'):
        column['scale'] = field['SCALE']
    if field.get('NULLABLE'):
        column['required'] = field['NULLABLE'].upper() in {'NOTNULL', 'NO', 'FALSE', '0'}
    return column


def write_schema(schema_dir: Path, pipeline_name: str, schema_name: str, table_name: str, fields: list[dict[str, str]]) -> Path | None:
    columns = [field_to_column(field) for field in fields if field.get('NAME')]
    if not columns:
        return None
    schema_dir.mkdir(parents=True, exist_ok=True)
    path = schema_dir / f'{pipeline_name}-{safe_output_name(schema_name)}-{safe_output_name(table_name)}.schema.yaml'
    document = {
        'schemaVersion': 1,
        'description': f'Generated from Informatica mapping/source metadata for {schema_name}.{table_name}.',
        'allowExtraColumns': True,
        'strictColumnOrder': False,
        'columns': columns,
    }
    path.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding='utf-8')
    return path


def build_pipeline(args: argparse.Namespace, selected_sources: list[dict]) -> tuple[dict, int]:
    tables = []
    schema_count = 0
    seen_tables = set()
    schema_dir = args.schema_output_dir or (args.output.parent.parent / 'schemas')
    for source in selected_sources:
        table_name = source.get('NAME')
        schema_name = source.get('OWNERNAME') or args.schema
        if not table_name:
            continue
        table_key = (schema_name.lower(), table_name.lower())
        if table_key in seen_tables:
            continue
        seen_tables.add(table_key)
        table = {
            'schema': schema_name,
            'name': table_name,
            'query': f'SELECT * FROM {quoted_table(args.source_type, schema_name, table_name)}',
            'outputName': safe_output_name(table_name),
        }
        schema_path = write_schema(schema_dir, args.pipeline_name, schema_name, table_name, source.get('fields') or [])
        if schema_path:
            table['schemaRef'] = Path(__import__('os').path.relpath(schema_path.resolve(), args.output.parent.resolve())).as_posix()
            schema_count += 1
        tables.append(table)

    return {
        'pipelineName': args.pipeline_name,
        'source': {
            'type': args.source_type,
            'hostEnv': args.host_env,
            'databaseEnv': args.database_env,
            'usernameEnv': args.username_env,
            'passwordEnv': args.password_env,
        },
        'tables': tables,
        'output': {
            's3Prefix': args.s3_prefix,
        },
        'runtime': {
            'profile': args.profile,
            'tablesPerPod': args.tables_per_pod,
        },
    }, schema_count


def main() -> int:
    parser = argparse.ArgumentParser(description='Convert Informatica SOURCE entries to MiniFi pipeline YAML and schema refs')
    parser.add_argument('--workflow', required=True, type=Path, help='Informatica XML export path')
    parser.add_argument('--output', required=True, type=Path, help='Output MiniFi pipeline YAML')
    parser.add_argument('--schema-output-dir', type=Path, help='Directory for generated table schema files. Defaults to <output parent>/../schemas.')
    parser.add_argument('--table', action='append', help='Table name to include; repeatable. Defaults to all sources.')
    parser.add_argument('--schema', default='public', help='Default schema when OWNERNAME is missing')
    parser.add_argument('--pipeline-name', default='postgresql-t24', help='MiniFi pipeline name')
    parser.add_argument('--source-type', default='postgresql', choices=['postgresql', 'mssql', 'oracle', 'teradata'])
    parser.add_argument('--host-env', default='PSQL_HOSTNAME')
    parser.add_argument('--database-env', default='PSQL_DB')
    parser.add_argument('--username-env', default='PSQL_USERNAME')
    parser.add_argument('--password-env', default='PSQL_PASSWORD')
    parser.add_argument('--s3-prefix', default='runs/t24')
    parser.add_argument('--profile', default='medium', choices=['small', 'medium', 'large'])
    parser.add_argument('--tables-per-pod', default=10, type=int)
    args = parser.parse_args()

    sources = parse_sources(args.workflow)
    if args.table:
        requested = {name.upper() for name in args.table}
        sources = [source for source in sources if source.get('NAME', '').upper() in requested]
        missing = requested - {source.get('NAME', '').upper() for source in sources}
        if missing:
            print(f"Missing SOURCE entries in {args.workflow}: {', '.join(sorted(missing))}", file=sys.stderr)
            return 1
    if not sources:
        print(f'No SOURCE entries found in {args.workflow}', file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    pipeline, schema_count = build_pipeline(args, sources)
    args.output.write_text(yaml.safe_dump(pipeline, sort_keys=False, allow_unicode=True), encoding='utf-8')
    print(f"Wrote {args.output} with {len(pipeline['tables'])} table(s) and {schema_count} schema ref(s)")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
