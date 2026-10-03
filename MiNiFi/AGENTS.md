# AGENTS.md

Scope: entire repository unless a more specific `AGENTS.md` overrides it.

## Repository Purpose

This repository builds a reusable Apache MiNiFi Java base image and renders schema-validated JDBC-to-Parquet-to-S3 Kubernetes workloads from YAML pipeline configs.

## Safety Rules for Agents

- Do not print `.env` values, database passwords, S3 credentials, or generated Kubernetes Secret contents.
- Keep user-facing pipeline configs under `example/pipelines/` and table schema files under `example/schemas/` unless the user asks otherwise.
- Treat `minifi/defaults/`, `minifi/templates/`, and `minifi/scripts/` as shared framework code; change them only when onboarding requires reusable behavior, not for one-off source details.
- Do not commit rendered manifests from `minifi/rendered/`; they are generated local output.
- Prefer validating with `.\minifi\scripts\validate-config.ps1` or `python minifi/scripts/render_minifi.py validate` before rendering or deploying.
- When using Kubernetes, first confirm the current context and namespace. Default namespace is configured in `minifi/defaults/minifi-defaults.yaml`.

## Onboarding a New Source

When asked to onboard a source, collect or infer these requirements:

- **Source type**: one of `mssql`, `postgresql`, `oracle`, or `teradata` from `minifi/defaults/source-defaults.yaml`.
- **Connectivity**: either `source.databaseUrl` or both `source.hostEnv` and `source.databaseEnv`; use env var names in YAML, never literal secret values.
- **Credentials**: `source.usernameEnv` and `source.passwordEnv` must point to keys present in the root `.env` file.
- **JDBC driver**: the base image must contain the configured driver from `source-defaults.yaml`; update `Dockerfile` and image tag/defaults if a driver changes.
- **Network path**: confirm cluster egress to the database and S3. For raw JDBC traffic that cannot use Java HTTP proxy settings, configure `source.tcpProxy` and point the JDBC URL at the local listener.
- **Tables**: each table needs `schema` and `name`; add `query` for custom SQL and `outputName` when the S3 path should not use the raw table name.
- **Schema validation**: add a `schemaRef` to a YAML schema in `example/schemas/` when column validation is required. The job fails before movement if JDBC metadata does not match.
- **Incremental bounds**: for windowed extraction, configure `incremental.enabled`, `column`, `type`, and at least one of `lowerBoundParam` or `upperBoundParam`; deploy with matching `-Parameter NAME=value` values.
- **Output**: set `output.s3Prefix`; S3 host, access key, secret, and bucket name come from `.env` keys `S3_HOST`, `S3_ACCESS_KEY`, `S3_ACCESS_SECRET`, and `S3_BUCKET_NAME`.
- **Runtime sizing**: choose `runtime.profile` from `small`, `medium`, or `large`, and set `runtime.tablesPerPod` between `1` and `50`.

## Standard Workflow

Use this sequence for every new or changed source:

```powershell
.\minifi\scripts\validate-config.ps1 -Config .\example\pipelines\<pipeline>.yaml
.\minifi\scripts\render-k8s.ps1 -Config .\example\pipelines\<pipeline>.yaml
.\minifi\scripts\deploy-job.ps1 -Config .\example\pipelines\<pipeline>.yaml
```

For incremental jobs:

```powershell
.\minifi\scripts\deploy-job.ps1 `
  -Config .\example\pipelines\<pipeline>.yaml `
  -Parameter EXTRACT_FROM_DATE=YYYY-MM-DD,EXTRACT_TO_DATE=YYYY-MM-DD
```

Monitor with:

```powershell
kubectl get job <job-name> -n <namespace> -w
kubectl get pods -n <namespace> -l job-name=<job-name> -w
kubectl logs job/<job-name> -n <namespace> -f
```

Success signals include `Schema validation completed successfully`, `s3_sent` greater than zero when rows were exported, `MiNiFi job is idle and output is stable; stopping.`, and `S3 manifest finalized`.
