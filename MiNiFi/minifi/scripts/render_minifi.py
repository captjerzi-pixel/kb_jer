#!/usr/bin/env python3
import argparse
import copy
import datetime as dt
import json
import re
import shutil
import sys
import uuid
from pathlib import Path

import jsonschema
import yaml


class LiteralString(str):
    pass


def _literal_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


yaml.SafeDumper.add_representer(LiteralString, _literal_representer)

ROOT = Path(__file__).resolve().parents[2]
MINIFI_ROOT = ROOT / "minifi"
DEFAULTS_DIR = MINIFI_ROOT / "defaults"
SCHEMA_PATH = MINIFI_ROOT / "schemas" / "pipeline.schema.json"
TEMPLATES_DIR = MINIFI_ROOT / "templates"
DEFAULT_OUTPUT_ROOT = MINIFI_ROOT / "rendered"

TEMPLATE_FILES = [
    "bootstrap.conf.template",
    "minifi.properties.template",
    "minifi-env.properties.template",
    "flow-jdbc-to-s3-parquet.json.raw.template",
    "finalize_s3_manifest.py",
]


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        return yaml.safe_load(handle)


def resolve_schema_ref(config_path: Path, schema_ref: str) -> Path:
    candidates = [config_path.parent / schema_ref, ROOT / schema_ref]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise ValueError(f"Schema reference not found: {schema_ref}")


def load_table_schema(config_path: Path, table: dict) -> dict | None:
    schema_ref = table.get("schemaRef")
    if not schema_ref:
        return None
    schema_path = resolve_schema_ref(config_path, schema_ref)
    schema = load_yaml(schema_path)
    if not isinstance(schema, dict):
        raise ValueError(f"Schema reference {schema_ref} must contain a mapping")
    columns = schema.get("columns") or []
    if not isinstance(columns, list):
        raise ValueError(f"Schema reference {schema_ref} columns must be a list")
    seen = set()
    normalized_columns = []
    for column in columns:
        if isinstance(column, str):
            column = {"name": column}
        if not isinstance(column, dict) or not column.get("name"):
            raise ValueError(f"Schema reference {schema_ref} has an invalid column entry: {column!r}")
        name = str(column["name"])
        key = name.lower()
        if key in seen:
            raise ValueError(f"Schema reference {schema_ref} has duplicate column: {name}")
        seen.add(key)
        normalized_columns.append({
            "name": name,
            "type": str(column.get("type") or ""),
            "required": bool(column.get("required", True)),
        })
    return {
        "schemaRef": str(schema_path.relative_to(ROOT)) if schema_path.is_relative_to(ROOT) else str(schema_path),
        "allowExtraColumns": bool(schema.get("allowExtraColumns", True)),
        "strictColumnOrder": bool(schema.get("strictColumnOrder", False)),
        "columns": normalized_columns,
    }


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def slug(value: str, max_length: int = 45) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", value.lower())
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    if not normalized:
        normalized = "pipeline"
    return normalized[:max_length].strip("-")


def require_env(env_values: dict[str, str], env_name: str, purpose: str) -> str:
    value = env_values.get(env_name)
    if not value:
        raise ValueError(f"Missing required environment value {env_name} for {purpose}")
    return value


def substitute_env_placeholders(value: str, env_values: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in env_values or not env_values[name]:
            raise ValueError(f"Missing required environment value {name}")
        return env_values[name]
    return re.sub(r"\$\{([A-Z][A-Z0-9_]*)\}", replace, value)


def validate_pipeline(config: dict) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8-sig"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(config), key=lambda err: list(err.path))
    if errors:
        details = []
        for err in errors:
            location = ".".join(str(part) for part in err.path) or "<root>"
            details.append(f"- {location}: {err.message}")
        raise ValueError("Pipeline config validation failed:\n" + "\n".join(details))

    table_keys = set()
    for table in config["tables"]:
        key = (table["schema"].lower(), table["name"].lower())
        if key in table_keys:
            raise ValueError(f"Duplicate table definition: {table['schema']}.{table['name']}")
        table_keys.add(key)


def build_database_url(source: dict, env_values: dict[str, str]) -> str:
    if source.get("databaseUrl"):
        return substitute_env_placeholders(source["databaseUrl"], env_values)

    host = require_env(env_values, source["hostEnv"], "source.hostEnv")
    database = require_env(env_values, source["databaseEnv"], "source.databaseEnv")
    source_type = source["type"]
    if source_type == "postgresql":
        return f"jdbc:postgresql://{host}/{database}"
    if source_type == "teradata":
        return f"jdbc:teradata://{host}/DATABASE={database}"
    if source_type == "oracle":
        return f"jdbc:oracle:thin:@//{host}/{database}"
    if source_type == "mssql":
        return f"jdbc:sqlserver://{host};databaseName={database};encrypt=true;trustServerCertificate=true"
    raise ValueError(f"Unsupported source type {source_type}")


def default_query(source_type: str, schema: str, table: str) -> str:
    if source_type == "postgresql":
        return f'SELECT * FROM {schema}."{table}"'
    return f"SELECT * FROM {schema}.{table}"


def parse_runtime_parameters(values: list[str] | None) -> dict[str, str]:
    parameters: dict[str, str] = {}
    for raw_value in values or []:
        if "=" not in raw_value:
            raise ValueError(f"Runtime parameter must use NAME=value format: {raw_value}")
        name, value = raw_value.split("=", 1)
        name = name.strip()
        if not re.match(r"^[A-Z][A-Z0-9_]*$", name):
            raise ValueError(f"Invalid runtime parameter name: {name}")
        parameters[name] = value.strip()
    return parameters


def strip_sql_semicolon(query: str) -> str:
    return re.sub(r";+\s*$", "", query.strip())


def sql_literal(value: str, value_type: str) -> str:
    if value_type == "number":
        if not re.match(r"^-?\d+(\.\d+)?$", value.strip()):
            raise ValueError(f"Incremental numeric parameter is not a number: {value}")
        return value.strip()
    if value_type == "date":
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", value.strip()):
            raise ValueError(f"Incremental date parameter must use YYYY-MM-DD: {value}")
    if value_type == "timestamp":
        if not re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$", value.strip()):
            raise ValueError(f"Incremental timestamp parameter must use ISO-like timestamp: {value}")
    return "'" + value.strip().replace("'", "''") + "'"


def quoted_identifier(source_type: str, identifier: str) -> str:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", identifier):
        raise ValueError(f"Invalid SQL identifier: {identifier}")
    if source_type == "mssql":
        return f"[{identifier}]"
    return '"' + identifier.replace('"', '""') + '"'


def incremental_column_ref(source_type: str, column: str, alias: str | None = None) -> str:
    quoted_column = quoted_identifier(source_type, column)
    return f"{alias}.{quoted_column}" if alias else quoted_column


def build_incremental_predicate(source_type: str, table: dict, runtime_parameters: dict[str, str], alias: str | None = None) -> str | None:
    incremental = table.get("incremental") or {}
    if not incremental.get("enabled"):
        return None
    column = incremental["column"]
    value_type = incremental.get("type", "timestamp")
    predicates = []
    column_ref = incremental_column_ref(source_type, column, alias)
    lower_param = incremental.get("lowerBoundParam")
    upper_param = incremental.get("upperBoundParam")
    if not lower_param and not upper_param:
        raise ValueError(f"Incremental table {table['schema']}.{table['name']} requires lowerBoundParam or upperBoundParam")
    if lower_param:
        if lower_param not in runtime_parameters or runtime_parameters[lower_param] == "":
            raise ValueError(f"Missing runtime parameter {lower_param} for incremental table {table['schema']}.{table['name']}")
        operator = ">=" if incremental.get("lowerInclusive", True) else ">"
        predicates.append(f"{column_ref} {operator} {sql_literal(runtime_parameters[lower_param], value_type)}")
    if upper_param:
        if upper_param not in runtime_parameters or runtime_parameters[upper_param] == "":
            raise ValueError(f"Missing runtime parameter {upper_param} for incremental table {table['schema']}.{table['name']}")
        operator = "<" if incremental.get("upperExclusive", True) else "<="
        predicates.append(f"{column_ref} {operator} {sql_literal(runtime_parameters[upper_param], value_type)}")
    return " AND ".join(predicates)


def build_table_query(source_type: str, table: dict, runtime_parameters: dict[str, str], query_limit: int | None = None) -> str:
    base_query = strip_sql_semicolon(table.get("query") or default_query(source_type, table["schema"], table["name"]))
    incremental = table.get("incremental") or {}
    if not incremental.get("enabled"):
        return apply_query_limit(source_type, base_query, query_limit)
    if table.get("query"):
        alias = incremental.get("queryAlias", "src")
        predicate = build_incremental_predicate(source_type, table, runtime_parameters, alias)
        query = f"SELECT * FROM ({base_query}) {alias} WHERE {predicate}"
    else:
        predicate = build_incremental_predicate(source_type, table, runtime_parameters)
        query = f"{base_query} WHERE {predicate}"
    return apply_query_limit(source_type, query, query_limit)


def apply_query_limit(source_type: str, query: str, query_limit: int | None) -> str:
    if not query_limit:
        return query
    if source_type == "mssql":
        if not re.match(r"^\s*SELECT\s+", query, re.IGNORECASE):
            raise ValueError("MSSQL queryLimit requires a SELECT query")
        return re.sub(r"^(\s*SELECT\s+)", rf"\1TOP ({query_limit}) ", query, count=1, flags=re.IGNORECASE)
    return f"{query} LIMIT {query_limit}"


def validate_incremental_columns(table: dict) -> None:
    incremental = table.get("incremental") or {}
    if not incremental.get("enabled") or not table.get("schemaDefinition"):
        return
    expected_column = incremental["column"].lower()
    schema_columns = {column["name"].lower() for column in table["schemaDefinition"].get("columns", [])}
    if expected_column not in schema_columns:
        raise ValueError(
            f"Incremental column {incremental['column']} for {table['schema']}.{table['name']} is not present in schemaRef"
        )


def render_string(value: str, variables: dict[str, str]) -> str:
    rendered = value
    for key, replacement in variables.items():
        rendered = rendered.replace("${" + key + "}", str(replacement))
    unresolved = sorted(set(re.findall(r"\$\{[^}]+\}", rendered)))
    if unresolved:
        raise ValueError(f"Unresolved placeholders: {', '.join(unresolved)}")
    return rendered



def flow_uuid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"kb-minifi/{name}"))


def parse_base_flow_template(profile: dict, defaults: dict) -> dict:
    text = (TEMPLATES_DIR / "flow-jdbc-to-s3-parquet.json.raw.template").read_text(encoding="utf-8")
    numeric_values = {
        "MAX_TIMER_DRIVEN_THREAD_COUNT": profile["maxTimerDrivenThreadCount"],
        "EXECUTE_SQL_CONCURRENCY": profile["executeSqlConcurrency"],
        "UPDATE_ATTRIBUTE_CONCURRENCY": profile["updateAttributeConcurrency"],
        "S3_UPLOAD_CONCURRENCY": profile["s3UploadConcurrency"],
        "S3_RETRY_COUNT": defaults["s3"]["retryCount"],
    }
    for key, value in numeric_values.items():
        text = text.replace("${" + key + "}", str(value))
    return json.loads(text)


def build_processor_connection(source_id: str, source_name: str, destination_id: str, destination_name: str, relationship: str, name: str) -> dict:
    group_id = "986876a4-5cbd-388c-9671-5d25092768c7"
    return {
        "identifier": flow_uuid(f"connection/{name}"),
        "instanceIdentifier": name,
        "name": name,
        "source": {"id": source_id, "type": "PROCESSOR", "groupId": group_id, "name": source_name, "comments": ""},
        "destination": {"id": destination_id, "type": "PROCESSOR", "groupId": group_id, "name": destination_name, "comments": ""},
        "labelIndex": 1,
        "zIndex": 0,
        "selectedRelationships": [relationship],
        "availableRelationships": ["success", "failure"],
        "backPressureObjectThreshold": 10000,
        "backPressureDataSizeThreshold": "1 GB",
        "flowFileExpiration": "0 sec",
        "prioritizers": [],
        "componentType": "CONNECTION",
        "groupIdentifier": group_id,
    }


def build_flow_template(defaults: dict, source_type: str, profile: dict, config: dict, tables: list[dict], runtime_parameters: dict[str, str]) -> tuple[str, dict]:
    flow = parse_base_flow_template(profile, defaults)
    root = flow["rootGroup"]
    root["name"] = f"MiniFi {source_type.upper()} to Parquet"
    root["comments"] = "Extracts configured JDBC tables in parallel and writes Parquet files to S3."
    root["maxConcurrentTasks"] = int(profile["maxTimerDrivenThreadCount"])
    base_processors = root["processors"]
    execute_template, filename_template, put_s3_template = base_processors[:3]
    processors = []
    connections = []
    table_manifest = []

    for index, table in enumerate(tables):
        table_slug = slug(f"{table['schema']}-{table['name']}", 50)
        y = float(index * 250)
        output_name = table.get("outputName") or table["name"].lower()
        query = build_table_query(source_type, table, runtime_parameters, config.get("queryLimit"))
        execute = copy.deepcopy(execute_template)
        filename = copy.deepcopy(filename_template)
        put_s3 = copy.deepcopy(put_s3_template)
        sidecar_attributes = copy.deepcopy(filename_template)
        sidecar_content = copy.deepcopy(filename_template)
        put_sidecar = copy.deepcopy(put_s3_template)

        execute_id = flow_uuid(f"{config['pipelineName']}/{table_slug}/execute")
        filename_id = flow_uuid(f"{config['pipelineName']}/{table_slug}/filename")
        put_s3_id = flow_uuid(f"{config['pipelineName']}/{table_slug}/put-s3")
        sidecar_attributes_id = flow_uuid(f"{config['pipelineName']}/{table_slug}/sidecar-attributes")
        sidecar_content_id = flow_uuid(f"{config['pipelineName']}/{table_slug}/sidecar-content")
        put_sidecar_id = flow_uuid(f"{config['pipelineName']}/{table_slug}/put-sidecar")
        execute_name = f"execute-sql-record__{table_slug}"
        filename_name = f"parquet-filename__{table_slug}"
        put_s3_name = f"put-s3-object__{table_slug}"
        sidecar_attributes_name = f"sidecar-attributes__{table_slug}"
        sidecar_content_name = f"sidecar-content__{table_slug}"
        put_sidecar_name = f"put-sidecar__{table_slug}"

        execute.update({
            "identifier": execute_id,
            "instanceIdentifier": execute_name,
            "name": execute_name,
            "comments": f"Reads {table['schema']}.{table['name']} from {source_type}.",
            "position": {"x": 0.0, "y": y},
        })
        execute["properties"]["SQL select query"] = query
        execute["properties"]["Fetch Size"] = profile["fetchSize"]
        execute["properties"]["Max Rows Per Flow File"] = profile["maxRowsPerFlowFile"]
        execute["properties"]["Output Batch Size"] = profile["outputBatchSize"]
        execute["concurrentlySchedulableTaskCount"] = int(profile["executeSqlConcurrency"])

        filename.update({
            "identifier": filename_id,
            "instanceIdentifier": filename_name,
            "name": filename_name,
            "comments": f"Assigns deterministic Parquet object names for {table['schema']}.{table['name']}.",
            "position": {"x": 450.0, "y": y},
        })
        filename["properties"].update({
            "filename": f"{output_name}-part-${{fragment.index:padLeft(6,'0')}}.parquet",
            "table.schema": table["schema"],
            "table.name": table["name"],
            "table.slug": table_slug,
        })
        filename["concurrentlySchedulableTaskCount"] = int(profile["updateAttributeConcurrency"])

        put_s3.update({
            "identifier": put_s3_id,
            "instanceIdentifier": put_s3_name,
            "name": put_s3_name,
            "comments": f"Uploads Parquet files for {table['schema']}.{table['name']} to S3.",
            "position": {"x": 900.0, "y": y},
        })
        put_s3["properties"]["Object Key"] = f"${{S3_PREFIX}}/${{RUN_TIMESTAMP}}/{table['schema']}/{table['name']}/data/${{filename}}"
        put_s3["concurrentlySchedulableTaskCount"] = int(profile["s3UploadConcurrency"])
        put_s3["retryCount"] = int(defaults["s3"]["retryCount"])
        put_s3["autoTerminatedRelationships"] = [
            relationship
            for relationship in put_s3.get("autoTerminatedRelationships", [])
            if relationship != "success"
        ]

        sidecar_attributes.update({
            "identifier": sidecar_attributes_id,
            "instanceIdentifier": sidecar_attributes_name,
            "name": sidecar_attributes_name,
            "comments": f"Prepares immutable metadata attributes for {table['schema']}.{table['name']}.",
            "position": {"x": 1350.0, "y": y},
        })
        sidecar_attributes["properties"].update({
            "metadata.path": "data/${filename}",
            "metadata.status": "SUCCESS",
            "metadata.size_bytes": "${fileSize}",
            "metadata.uploaded_at_utc": "${now():format(\"yyyy-MM-dd'T'HH:mm:ss'Z'\", \"UTC\")}",
        })
        sidecar_attributes["concurrentlySchedulableTaskCount"] = 1

        sidecar_content.update({
            "identifier": sidecar_content_id,
            "instanceIdentifier": sidecar_content_name,
            "name": sidecar_content_name,
            "comments": f"Serializes immutable metadata for {table['schema']}.{table['name']}.",
            "position": {"x": 1800.0, "y": y},
            "type": "org.apache.nifi.processors.standard.ReplaceText",
            "bundle": {
                "group": "org.apache.nifi.minifi",
                "artifact": "minifi-standard-nar",
                "version": "2.12.0",
            },
        })
        sidecar_content["properties"] = {
            "Evaluation Mode": "Entire text",
            "Replacement Strategy": "Always Replace",
            "Replacement Value": '{"manifest_version":"1.0","schema_version":"1.0","source_system":"%s","dataset":"%s","batch_id":"${RUN_TIMESTAMP}","load_type":"%s","format":"parquet","path":"${metadata.path}","status":"${metadata.status}","size_bytes":${metadata.size_bytes},"uploaded_at_utc":"${metadata.uploaded_at_utc}"}' % (source_type, f"{table['schema']}.{table['name']}", "incremental" if (table.get("incremental") or {}).get("enabled") else "full"),
        }
        sidecar_content["concurrentlySchedulableTaskCount"] = 1

        put_sidecar.update({
            "identifier": put_sidecar_id,
            "instanceIdentifier": put_sidecar_name,
            "name": put_sidecar_name,
            "comments": f"Uploads immutable metadata for {table['schema']}.{table['name']}.",
            "position": {"x": 2250.0, "y": y},
        })
        put_sidecar["properties"]["Object Key"] = f"${{S3_PREFIX}}/${{RUN_TIMESTAMP}}/{table['schema']}/{table['name']}/metadata/${{filename}}.json"
        put_sidecar["concurrentlySchedulableTaskCount"] = 1
        put_sidecar["retryCount"] = int(defaults["s3"]["retryCount"])

        processors.extend([execute, filename, put_s3, sidecar_attributes, sidecar_content, put_sidecar])
        connections.append(build_processor_connection(execute_id, execute_name, filename_id, filename_name, "success", f"{table_slug}-sql-to-filename"))
        connections.append(build_processor_connection(filename_id, filename_name, put_s3_id, put_s3_name, "success", f"{table_slug}-filename-to-s3"))
        connections.append(build_processor_connection(put_s3_id, put_s3_name, sidecar_attributes_id, sidecar_attributes_name, "success", f"{table_slug}-s3-to-sidecar-attributes"))
        connections.append(build_processor_connection(sidecar_attributes_id, sidecar_attributes_name, sidecar_content_id, sidecar_content_name, "success", f"{table_slug}-sidecar-attributes-to-content"))
        connections.append(build_processor_connection(sidecar_content_id, sidecar_content_name, put_sidecar_id, put_sidecar_name, "success", f"{table_slug}-sidecar-content-to-s3"))
        table_entry = {
            "schema": table["schema"],
            "name": table["name"],
            "slug": table_slug,
            "sourceSystem": source_type,
            "dataset": f"{table['schema']}.{table['name']}",
            "loadType": "incremental" if (table.get("incremental") or {}).get("enabled") else "full",
            "query": query,
            "outputName": output_name,
            "putS3ProcessorId": put_s3_name,
            "s3Prefix": f"{config['output']['s3Prefix']}/${{RUN_TIMESTAMP}}/{table['schema']}/{table['name']}/data",
            "metadataPrefix": f"{config['output']['s3Prefix']}/${{RUN_TIMESTAMP}}/{table['schema']}/{table['name']}/metadata",
            "manifestKey": f"{config['output']['s3Prefix']}/${{RUN_TIMESTAMP}}/{table['schema']}/{table['name']}/manifest.json",
        }
        if (table.get("incremental") or {}).get("enabled"):
            table_entry["incremental"] = table["incremental"]
        if table.get("schemaDefinition"):
            table_entry["schemaDefinition"] = table["schemaDefinition"]
        table_manifest.append(table_entry)

    root["processors"] = processors
    root["connections"] = connections
    return json.dumps(flow, indent=2), {"tables": table_manifest}


def configmap_from_templates(defaults: dict, group_slug: str, flow_template: str, table_manifest: dict) -> dict:
    data = {}
    for filename in TEMPLATE_FILES:
        if filename == "flow-jdbc-to-s3-parquet.json.raw.template":
            data[filename] = LiteralString(flow_template)
        else:
            data[filename] = LiteralString((TEMPLATES_DIR / filename).read_text(encoding="utf-8"))
    data["table-manifest.json"] = LiteralString(json.dumps(table_manifest, indent=2))
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": f"{defaults['flowConfigMapName']}-{group_slug}",
            "namespace": defaults["namespace"],
            "labels": {
                "app.kubernetes.io/name": defaults["appName"],
                "app.kubernetes.io/component": "flow-templates",
            },
        },
        "data": data,
    }


def runtime_configmap(defaults: dict, source_defaults: dict, profile: dict, config: dict, tables: list[dict], env_values: dict[str, str], run_timestamp: str, group_slug: str) -> dict:
    source = config["source"]
    source_type = source["type"]
    driver_defaults = source_defaults["sources"][source_type]
    first_table = tables[0]
    tcp_proxy = source.get("tcpProxy") or {}
    data = {
        "HTTP_PROXY": defaults["proxy"]["httpProxy"],
        "HTTPS_PROXY": defaults["proxy"]["httpsProxy"],
        "http_proxy": defaults["proxy"]["httpProxy"],
        "https_proxy": defaults["proxy"]["httpsProxy"],
        "NO_PROXY": defaults["proxy"]["noProxy"],
        "no_proxy": defaults["proxy"]["noProxy"],
        "JAVA_TOOL_OPTIONS": defaults["proxy"]["javaToolOptions"],
        "DATABASE_URL": build_database_url(source, env_values),
        "DATABASE_DRIVER_CLASS": driver_defaults["driverClass"],
        "DATABASE_DRIVER_LOCATION": driver_defaults["driverLocation"],
        "DATABASE_VALIDATION_QUERY": source.get("validationQuery") or driver_defaults["validationQuery"],
        "SQL_SELECT_QUERY": first_table.get("query") or default_query(source_type, first_table["schema"], first_table["name"]),
        "FETCH_SIZE": profile["fetchSize"],
        "MAX_ROWS_PER_FLOW_FILE": profile["maxRowsPerFlowFile"],
        "OUTPUT_BATCH_SIZE": profile["outputBatchSize"],
        "MAX_TIMER_DRIVEN_THREAD_COUNT": profile["maxTimerDrivenThreadCount"],
        "EXECUTE_SQL_CONCURRENCY": profile["executeSqlConcurrency"],
        "UPDATE_ATTRIBUTE_CONCURRENCY": profile["updateAttributeConcurrency"],
        "PUT_PARQUET_CONCURRENCY": profile["s3UploadConcurrency"],
        "S3_UPLOAD_CONCURRENCY": profile["s3UploadConcurrency"],
        "S3_REGION_SELECTOR": defaults["s3"]["regionSelector"],
        "S3_REGION": defaults["s3"]["region"],
        "S3_PREFIX": config["output"]["s3Prefix"],
        "RUN_TIMESTAMP": run_timestamp,
        "TABLE_SCHEMA": first_table["schema"],
        "TABLE_NAME": first_table["name"],
        "TABLE_COUNT": str(len(tables)),
        # Test hook: keep disabled by default; enable only for finalizer failure scenarios.
        "FINALIZER_TEST_FAIL": "true" if config.get("finalizerTestFail", False) else "false",
        "S3_RETRY_COUNT": defaults["s3"]["retryCount"],
        "S3_COMMUNICATIONS_TIMEOUT": defaults["s3"]["communicationsTimeout"],
        "S3_MULTIPART_THRESHOLD": defaults["s3"]["multipartThreshold"],
        "S3_MULTIPART_PART_SIZE": defaults["s3"]["multipartPartSize"],
        "S3_USE_CHUNKED_ENCODING": defaults["s3"]["useChunkedEncoding"],
        "S3_USE_PATH_STYLE_ACCESS": defaults["s3"]["usePathStyleAccess"],
        "S3_TRUSTSTORE_PATH": defaults["s3"]["truststorePath"],
        "S3_TRUSTSTORE_PASSWORD": defaults["s3"]["truststorePassword"],
        "S3_TRUSTSTORE_TYPE": defaults["s3"]["truststoreType"],
        "RUN_SCHEDULE": defaults["minifi"]["runSchedule"],
        "OUTPUT_FILENAME_PREFIX": first_table.get("outputName") or first_table["name"].lower(),
        "PARQUET_COMPRESSION": defaults["parquet"]["compression"],
        "PARQUET_ROW_GROUP_SIZE": defaults["parquet"]["rowGroupSize"],
        "PARQUET_PAGE_SIZE": defaults["parquet"]["pageSize"],
        "HADOOP_CONFIGURATION_RESOURCES": defaults["hadoop"]["configurationResources"],
        "HADOOP_DEFAULT_FS": defaults["hadoop"]["defaultFs"],
        "MINIFI_RUN_DURATION": defaults["minifi"]["runDuration"],
        "NIFI_SENSITIVE_PROPS_KEY": defaults["minifi"]["sensitivePropsKey"],
        "MINIFI_ROOT_LOG_LEVEL": defaults["minifi"]["rootLogLevel"],
        "MINIFI_PROCESSOR_LOG_LEVEL": defaults["minifi"]["processorLogLevel"],
        "MINIFI_STATUS_PERIOD_SECONDS": defaults["minifi"].get("statusPeriodSeconds", "60"),
        "MINIFI_JOB_POLL_SECONDS": defaults["minifi"]["jobPollSeconds"],
        "MINIFI_JOB_IDLE_CHECKS": defaults["minifi"]["jobIdleChecks"],
        "MINIFI_STATUS_QUERIES": LiteralString("\n".join(defaults["minifi"]["statusQueries"])),
        "TCP_PROXY_ENABLED": str(bool(tcp_proxy.get("enabled"))).lower(),
        "TCP_PROXY_UPSTREAM_HOST": tcp_proxy.get("upstreamHost", ""),
        "TCP_PROXY_UPSTREAM_PORT": tcp_proxy.get("upstreamPort", ""),
        "TCP_PROXY_LISTEN_HOST": tcp_proxy.get("listenHost", "127.0.0.1"),
        "TCP_PROXY_LISTEN_PORT": tcp_proxy.get("listenPort", ""),
        "TCP_PROXY_CONNECT_TIMEOUT_SECONDS": tcp_proxy.get("upstreamConnectTimeoutSeconds", 15),
    }
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": f"{defaults['runtimeConfigMapName']}-{group_slug}",
            "namespace": defaults["namespace"],
            "labels": {
                "app.kubernetes.io/name": defaults["appName"],
                "app.kubernetes.io/component": group_slug,
                "minifi.kb.cz/source-type": f"jdbc-{source_type}",
            },
        },
        "data": {key: str(value) for key, value in data.items()},
    }


def load_yaml_template(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as handle:
        return yaml.safe_load(handle)


def set_first_container_env_secret(workload: dict, database_secret_name: str, s3_secret_name: str) -> None:
    container = workload["spec"]["template"]["spec"]["containers"][0]
    for env in container.get("env", []):
        if env["name"] in {"DATABASE_USER", "DATABASE_PASSWORD"}:
            env["valueFrom"]["secretKeyRef"]["name"] = database_secret_name
        if env["name"].startswith("S3_"):
            env["valueFrom"]["secretKeyRef"]["name"] = s3_secret_name



def patch_workload(workload: dict, defaults: dict, profile: dict, config: dict, group_slug: str, kind: str) -> dict:
    result = copy.deepcopy(workload)
    workload_name = f"{defaults['jobNamePrefix']}-{group_slug}"
    result["metadata"]["name"] = workload_name
    result["metadata"]["namespace"] = defaults["namespace"]
    result["metadata"].setdefault("labels", {})["app.kubernetes.io/name"] = defaults["appName"]
    result["metadata"]["labels"]["app.kubernetes.io/component"] = group_slug

    pod_spec = result["spec"]["template"]["spec"]
    result["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/name"] = defaults["appName"]
    result["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/component"] = group_slug
    pod_spec["imagePullSecrets"] = [{"name": defaults["imagePullSecret"]}]
    container = pod_spec["containers"][0]
    container["image"] = defaults["image"]
    container["resources"] = {
        "requests": {"cpu": profile["cpu"], "memory": profile["memory"]},
        "limits": {"cpu": profile["cpu"], "memory": profile["memory"]},
    }
    for env_from in container.get("envFrom", []):
        if "configMapRef" in env_from:
            env_from["configMapRef"]["name"] = f"{defaults['runtimeConfigMapName']}-{group_slug}"
    set_first_container_env_secret(result, f"{defaults['databaseSecretName']}-{group_slug}", defaults["s3SecretName"])
    for volume in pod_spec.get("volumes", []):
        if volume.get("name") == "minifi-flow-templates":
            volume["configMap"]["name"] = f"{defaults['flowConfigMapName']}-{group_slug}"
    return result


def literalize_multiline(value):
    if isinstance(value, dict):
        return {key: literalize_multiline(item) for key, item in value.items()}
    if isinstance(value, list):
        return [literalize_multiline(item) for item in value]
    if isinstance(value, str) and "\n" in value:
        return LiteralString(value)
    return value


def write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(literalize_multiline(data), handle, sort_keys=False, default_flow_style=False, allow_unicode=True)


def chunk_tables(tables: list[dict], size: int) -> list[list[dict]]:
    return [tables[index:index + size] for index in range(0, len(tables), size)]


def render(config_path: Path, output_root: Path, env_path: Path, run_timestamp: str, runtime_parameter_values: list[str] | None = None, object_filters: list[str] | None = None) -> list[Path]:
    config = load_yaml(config_path)
    validate_pipeline(config)
    defaults = load_yaml(DEFAULTS_DIR / "minifi-defaults.yaml")
    profiles = load_yaml(DEFAULTS_DIR / "resource-profiles.yaml")["profiles"]
    source_defaults = load_yaml(DEFAULTS_DIR / "source-defaults.yaml")
    profile_name = config["runtime"]["profile"]
    profile = profiles[profile_name]
    tables_per_pod = int(config["runtime"].get("tablesPerPod", 1))
    env_values = load_env(env_path)
    runtime_parameters = {**env_values, **parse_runtime_parameters(runtime_parameter_values)}

    job_template = load_yaml_template(TEMPLATES_DIR / "job.yaml.tpl")
    deployment_template = load_yaml_template(TEMPLATES_DIR / "deployment.yaml.tpl")
    rendered_dirs = []
    pipeline_dir = output_root / config["pipelineName"]
    if pipeline_dir.exists():
        shutil.rmtree(pipeline_dir)
    selected_tables = config["tables"]
    if object_filters:
        requested = {object_name.casefold() for object_name in object_filters}
        selected_tables = [
            table for table in selected_tables
            if str(table.get("name", "")).casefold() in requested
        ]
        if not selected_tables:
            available = ", ".join(str(table.get("name", "")) for table in config["tables"])
            raise ValueError(f"No objects matched --object: {', '.join(object_filters)}. Available objects: {available}")
    enriched_tables = []
    for table in selected_tables:
        enriched = copy.deepcopy(table)
        schema_definition = load_table_schema(config_path, enriched)
        if schema_definition:
            enriched["schemaDefinition"] = schema_definition
        validate_incremental_columns(enriched)
        enriched_tables.append(enriched)

    for group_index, table_group in enumerate(chunk_tables(enriched_tables, tables_per_pod), start=1):
        group_name = f"{config['pipelineName']}-g{group_index:03d}"
        group_slug = slug(group_name)
        group_dir = pipeline_dir / group_slug
        group_dir.mkdir(parents=True, exist_ok=True)
        flow_template, table_manifest = build_flow_template(defaults, config["source"]["type"], profile, config, table_group, runtime_parameters)
        write_yaml(group_dir / "configmap-templates.yaml", configmap_from_templates(defaults, group_slug, flow_template, table_manifest))
        write_yaml(group_dir / "configmap-runtime.yaml", runtime_configmap(defaults, source_defaults, profile, config, table_group, env_values, run_timestamp, group_slug))
        write_yaml(group_dir / "job.yaml", patch_workload(job_template, defaults, profile, config, group_slug, "Job"))
        write_yaml(group_dir / "deployment.yaml", patch_workload(deployment_template, defaults, profile, config, group_slug, "Deployment"))
        rendered_dirs.append(group_dir)
    return rendered_dirs


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and render MiniFi JDBC-to-Parquet-to-S3 pipelines")
    parser.add_argument("command", choices=["validate", "render", "source-info"])
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--run-timestamp", default=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--param", action="append", default=[], help="Runtime parameter in NAME=value format; can be repeated")
    parser.add_argument("--object", action="append", default=[], help="Render only the named table or view; can be repeated")
    args = parser.parse_args()

    try:
        config = load_yaml(args.config)
        validate_pipeline(config)
        if args.command == "validate":
            print(f"OK: {args.config}")
            return 0
        if args.command == "source-info":
            defaults = load_yaml(DEFAULTS_DIR / "minifi-defaults.yaml")
            source = config["source"]
            print(json.dumps({
                "namespace": defaults["namespace"],
                "databaseSecretName": defaults["databaseSecretName"],
                "s3SecretName": defaults["s3SecretName"],
                "usernameEnv": source["usernameEnv"],
                "passwordEnv": source["passwordEnv"],
            }))
            return 0
        dirs = render(args.config, args.output_root, args.env, args.run_timestamp, args.param, args.object)
        for directory in dirs:
            print(directory)
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
