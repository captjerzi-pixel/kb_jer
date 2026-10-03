# Ingestion

This repository now focuses on a reusable Apache MiNiFi Java ingestion base image and generated Kubernetes pipelines.

## Layout

- `Dockerfile`, `Jenkinsfile`, and `application.properties` build and publish the reusable `minifi-java-base` image.
- `minifi/defaults` contains repository-owned defaults that users should not normally edit.
- `minifi/schemas` contains validation schemas for user pipeline configuration.
- `minifi/templates` contains the shared MiNiFi/Kubernetes templates rendered into ConfigMaps and Jobs.
- `example/pipelines` contains user-facing pipeline definitions such as MSSQL and PostgreSQL examples.
- `minifi/scripts` contains validation, rendering, and deploy helpers.
- `example` keeps a runnable MiNiFi example and Docker/Jenkins sample files.

The former Meltano sample was removed. The root `.env` remains local and ignored; scripts use it to create Kubernetes Secrets without writing credentials to disk.

## Common Commands

```powershell
.\minifi\scripts\validate-config.ps1 -Config .\example\pipelines\postgresql-currency.yaml
.\minifi\scripts\render-k8s.ps1 -Config .\example\pipelines\postgresql-currency.yaml
.\minifi\scripts\deploy-job.ps1 -Config .\example\pipelines\postgresql-currency.yaml
```

The three commands have different responsibilities:

- `validate-config.ps1` performs local validation of the pipeline YAML against the pipeline schema. It does not need Kubernetes, `kubectl`, or the environment values, and it does not connect to PostgreSQL or S3.
- `render-k8s.ps1` performs local rendering. It combines the pipeline, schema, shared templates, defaults, and values from `env` to create Kubernetes manifests under `minifi/rendered/`. It does not call `kubectl` and does not deploy anything.
- `deploy-job.ps1` renders the manifests, then uses `kubectl` to create/update Secrets, apply the PVC and ConfigMaps, delete and recreate the Kubernetes Job, and wait for the Job to finish. The `env` file is required because the script creates the database and S3 Secrets at deployment time.

The first two commands are local-only; the third command uses `kubectl` internally and therefore requires access to the target Kubernetes cluster.

For MSSQL, use `example/pipelines/mssql-opb-task-inst-run.yaml`.

## Embedded TCP Proxy

For sources where Java HTTP proxy settings do not help raw JDBC traffic, enable the image-local TCP proxy in the pipeline config:

```yaml
source:
  tcpProxy:
    enabled: true
    listenHost: 127.0.0.1
    listenPort: 1433
    upstreamHost: vssql38.ds.kb.cz
    upstreamPort: 1433
    upstreamConnectTimeoutSeconds: 15
```

Set the JDBC URL to the local listener, for example `jdbc:sqlserver://127.0.0.1:1433;...`. The base image includes `/opt/minifi/bin/tcp_proxy.py`; rebuild and publish `minifi-java-base` after Dockerfile changes before deploying proxy-enabled jobs.

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

## Deploy Samples to Kubernetes

These steps run a generated MiNiFi Kubernetes `Job` from one of the sample pipeline configs in `example/pipelines/`.

### 1. Prepare Local Tools

- Use PowerShell from the repository root.
- Ensure `python` is available with `PyYAML` and `jsonschema` installed.
- Ensure `kubectl` points to the target cluster and can access the target namespace.
- Ensure the reusable image from `minifi/defaults/minifi-defaults.yaml` is published and pullable by the cluster.

```powershell
kubectl config current-context
kubectl get ns
python -c "import yaml, jsonschema; print('python dependencies OK')"
```

### 2. Prepare `.env`

The deploy script reads secrets from the ignored root `.env` file and creates Kubernetes `Secret` objects at deploy time. Do not commit `.env` or print its values in logs.

Required S3 keys for every sample:

```text
S3_HOST=
S3_ACCESS_KEY=
S3_ACCESS_SECRET=
S3_BUCKET_NAME=
```

Required database keys depend on the selected pipeline config. For example, `example/pipelines/postgresql-currency.yaml` expects:

```text
PSQL_HOSTNAME=
PSQL_DB=
PSQL_USERNAME=
PSQL_PASSWORD=
```

Check the exact required source credentials without exposing values:

```powershell
python .\minifi\scripts\render_minifi.py source-info --config .\example\pipelines\postgresql-currency.yaml
```

### 3. Validate the Sample

Validate before rendering or deploying. This checks the pipeline config against `minifi/schemas/pipeline.schema.json`.

```powershell
.\minifi\scripts\validate-config.ps1 -Config .\example\pipelines\postgresql-currency.yaml
```

Expected result:

```text
OK: example\pipelines\postgresql-currency.yaml
```

### 4. Render Kubernetes Manifests

Render the config into `minifi/rendered/<pipeline>/<group>/`.

```powershell
.\minifi\scripts\render-k8s.ps1 -Config .\example\pipelines\postgresql-currency.yaml
```

Rendered files per group:

- `checkpoint-pvc.yaml`
- `configmap-templates.yaml`
- `configmap-runtime.yaml`
- `job.yaml`
- `deployment.yaml`

### 5. Deploy the Job

Deploy creates or updates the S3 secret, creates a per-job database secret, applies the PVC and ConfigMaps, deletes any existing job with the same name, applies the new job, and waits for completion.

```powershell
.\minifi\scripts\deploy-job.ps1 -Config .\example\pipelines\postgresql-currency.yaml
```

Other sample configs:

```powershell
.\minifi\scripts\deploy-job.ps1 -Config .\example\pipelines\mssql-opb-task-inst-run.yaml
.\minifi\scripts\deploy-job.ps1 -Config .\example\pipelines\postgresql-t24-currency.yaml
```

For incremental samples, pass runtime parameters with `-Parameter`:

```powershell
.\minifi\scripts\deploy-job.ps1 `
  -Config .\example\pipelines\postgresql-currency-incremental.yaml `
  -Parameter EXTRACT_FROM_DATE=2026-09-20,EXTRACT_TO_DATE=2026-09-21
```

### 6. Monitor the Run

Find namespace and job names from source metadata and rendered manifests:

```powershell
python .\minifi\scripts\render_minifi.py source-info --config .\example\pipelines\postgresql-currency.yaml
Get-ChildItem .\minifi\rendered\postgresql-currency\*\job.yaml | ForEach-Object { Select-String -Path $_ -Pattern '^  name: ' | Select-Object -First 1 }
```

Watch job and pod status:

```powershell
kubectl get job minifi-java-postgresql-currency-g001 -n ci -w
kubectl get pods -n ci -l job-name=minifi-java-postgresql-currency-g001 -w
```

Tail logs while the job runs:

```powershell
kubectl logs job/minifi-java-postgresql-currency-g001 -n ci -f
```

Inspect recent logs after completion or failure:

```powershell
kubectl logs job/minifi-java-postgresql-currency-g001 -n ci --tail=300
```

Useful success signals in logs:

- `Schema validation completed successfully`
- `Job monitor: tables=... s3_sent=... s3_bytes=... queued_count=0 active_threads=0`
- `MiNiFi job is idle and output is stable; stopping.`
- `S3 manifest finalized: runs/<RUN_TIMESTAMP>/<schema>/<table>/manifest.json`

Troubleshooting commands:

```powershell
kubectl describe job minifi-java-postgresql-currency-g001 -n ci
kubectl describe pod -n ci -l job-name=minifi-java-postgresql-currency-g001
kubectl get events -n ci --sort-by=.lastTimestamp
```

### 7. Rerun or Clean Up

`deploy-job.ps1` deletes and recreates the job with the same generated name. Completion state is stored in the per-object S3 `manifest.json`; use a new timestamp for a new extraction run.

```powershell
.\minifi\scripts\deploy-job.ps1 `
  -Config .\example\pipelines\postgresql-currency.yaml `
  -RunTimestamp 20260924T112142Z
```

To remove a sample job after inspection:

```powershell
kubectl delete job minifi-java-postgresql-currency-g001 -n ci --ignore-not-found
```

There is no local `_SUCCESS` checkpoint or checkpoint PVC. Rerunning with the same timestamp uses the S3 manifest as the completion authority.

## Onboard a New Source

Use this checklist when adding another table group or database source.

1. Confirm the source type is supported in `minifi/defaults/source-defaults.yaml`: `mssql`, `postgresql`, `oracle`, or `teradata`.
2. Confirm the base image includes the matching JDBC driver at the configured `driverLocation`; update `Dockerfile` and publish a new image if needed.
3. Create a pipeline YAML under `example/pipelines/` with `pipelineName`, `source`, `tables`, `output`, and `runtime`.
4. Add only environment variable names to the pipeline config; put actual values in `.env`.
5. Add table schemas under `example/schemas/` and reference them with `schemaRef` when schema validation is required.
6. Use `query` for custom SQL, otherwise the renderer builds a default table select for the source type.
7. Add `incremental` bounds when the extraction must be date, timestamp, or number windowed.
8. Set `runtime.profile` to `small`, `medium`, or `large`, and tune `runtime.tablesPerPod` for grouping tables into jobs.
9. Validate, render, deploy, and monitor using the commands in this README.

Minimal pipeline skeleton:

```yaml
pipelineName: postgresql-my-source
source:
  type: postgresql
  hostEnv: PSQL_HOSTNAME
  databaseEnv: PSQL_DB
  usernameEnv: PSQL_USERNAME
  passwordEnv: PSQL_PASSWORD
tables:
  - schema: public
    name: MY_TABLE
    schemaRef: ../schemas/postgresql-my-source.schema.yaml
    outputName: my_table
output:
  s3Prefix: runs/my-source
runtime:
  tablesPerPod: 10
  profile: medium
```
