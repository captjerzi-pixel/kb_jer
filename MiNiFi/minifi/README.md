# Root MiNiFi Pipeline Configuration

This folder contains the configurable, schema-validated MiNiFi pipeline model used to render Kubernetes manifests.

## Folders

- `defaults/` contains repo-owned defaults that are not part of user pipeline configuration.
- `schemas/` contains JSON Schema validation for user pipeline YAML files.
- `templates/` contains shared MiNiFi and Kubernetes templates.
- Concrete sample pipeline configs live outside this framework folder in `../example/pipelines/`.
- `scripts/` contains validation, render, and deploy helpers.
- `rendered/` is generated locally and ignored by Git.

## Resource Profiles

Profiles are defined in `defaults/resource-profiles.yaml`:

- `small`: 1 CPU, 2 Gi memory.
- `medium`: 2 CPU, 4 Gi memory.
- `large`: 4 CPU, 8 Gi memory.

Users select `runtime.profile` and optional `runtime.tablesPerPod`; CPU, memory, threads, batch size, and upload concurrency are resolved from defaults.

## Pipeline Config

A user pipeline config defines source type, source credentials env var names, tables, output prefix, runtime profile, and optionally how many table branches are grouped into one MiniFi pod with `runtime.tablesPerPod`. The renderer creates one Kubernetes Job per table group. Inside the pod, the entrypoint removes already-checkpointed table branches before MiniFi starts, then MiniFi runs the remaining table branches in parallel. Concrete sample configs live outside this folder under `../example/pipelines`:

- `../example/pipelines/mssql-opb-task-inst-run.yaml`
- `../example/pipelines/postgresql-currency.yaml`
- `../example/pipelines/postgresql-t24-currency.yaml`

## Commands

```powershell
.\minifi\scripts\validate-config.ps1 -Config .\example\pipelines\postgresql-currency.yaml
.\minifi\scripts\render-k8s.ps1 -Config .\example\pipelines\postgresql-currency.yaml
.\minifi\scripts\deploy-job.ps1 -Config .\example\pipelines\postgresql-currency.yaml
```

On Linux or macOS, use the equivalent Bash helpers:

```bash
./minifi/scripts/validate-config.sh --config ./example/pipelines/postgresql-currency.yaml
./minifi/scripts/render-k8s.sh --config ./example/pipelines/postgresql-currency.yaml
./minifi/scripts/init-k8s.sh --config ./example/pipelines/postgresql-currency.yaml
./minifi/scripts/deploy-job.sh --config ./example/pipelines/postgresql-currency.yaml
```

The Bash scripts require Python with `PyYAML` and `jsonschema` available. In this two-step workflow, `init-k8s.sh` reads database and S3 credentials from the ignored root `.env` file and creates the Secrets, checkpoint PVC, and ConfigMaps. Run it before `deploy-job.sh`, which creates and monitors the Jobs. The PowerShell deployment script remains unchanged.


## Informatica XML Conversion

Use the root helper to convert Informatica `SOURCE` entries into MiniFi pipeline config:

```powershell
python .\scripts\informatica_to_minifi_config.py `
  --workflow .\example\wfs\T24.xml `
  --table CURRENCY `
  --pipeline-name postgresql-t24-currency `
  --source-type postgresql `
  --s3-prefix runs/t24 `
  --profile medium `
  --tables-per-pod 10 `
  --output .\example\pipelines\postgresql-t24-currency.yaml
```

The sample `T24.xml` only contains `SOURCE` entries, so the generated config validates table ownership/name but does not infer column schemas from this file.
## Schema Validation

Pipeline tables can define `schemaRef` pointing to a YAML schema file. Rendered Jobs load those refs into `table-manifest.json` and perform JDBC metadata validation before starting MiniFi data movement. Failures are logged as `SCHEMA_VALIDATION_FAILED` and stop the Job before Parquet/S3 writes.

The Informatica converter generates schema files automatically when the XML contains field metadata such as `SOURCEFIELD`, `TARGETFIELD`, or `TRANSFORMFIELD` under source/target definitions.


## Embedded TCP Proxy

`source.tcpProxy.enabled: true` starts the bundled Python TCP proxy inside the MiniFi container before schema validation and processor startup. This is intended as a fallback for JDBC sources, such as MSSQL, where `JAVA_TOOL_OPTIONS` HTTP proxy settings do not apply to the database socket.

The pipeline `databaseUrl` should point at `listenHost:listenPort`; the proxy forwards traffic to `upstreamHost:upstreamPort`. The Docker image must include `/opt/minifi/bin/tcp_proxy.py`, so rebuild the `minifi-java-base` image before running this option in Kubernetes.

## Incremental Extraction

Tables can define an optional bounded incremental window. The renderer injects the predicate into the generated SQL and the existing checkpoint behavior still applies per `RUN_TIMESTAMP`: completed tables are skipped on rerun, failed tables restart from scratch.

```yaml
tables:
  - schema: public
    name: CURRENCY
    query: SELECT * FROM public."CURRENCY"
    schemaRef: ../schemas/postgresql-currency.schema.yaml
    incremental:
      enabled: true
      column: BANKING_DATE_DL
      type: date
      lowerBoundParam: EXTRACT_FROM_DATE
      upperBoundParam: EXTRACT_TO_DATE
      lowerInclusive: true
      upperExclusive: true
```

Render or deploy with runtime parameters:

```powershell
.\minifi\scripts\deploy-job.ps1 `
  -Config .\example\pipelines\postgresql-currency-incremental.yaml `
  -Parameter EXTRACT_FROM_DATE=2026-09-20,EXTRACT_TO_DATE=2026-09-21
```

If a custom `query` is configured, MiniFi wraps it as a subquery and applies the incremental predicate outside it. Supported incremental types are `date`, `timestamp`, and `number`.
