# MiNiFi Java example

This directory keeps a runnable example for the reusable `minifi-java-base` image. Production-style configuration now lives at the repository root under `minifi/`.

## Recommended Root Workflow

```powershell
.\minifi\scripts\validate-config.ps1 -Config .\example\pipelines\mssql-opb-task-inst-run.yaml
.\minifi\scripts\render-k8s.ps1 -Config .\example\pipelines\mssql-opb-task-inst-run.yaml
.\minifi\scripts\deploy-job.ps1 -Config .\example\pipelines\mssql-opb-task-inst-run.yaml

.\minifi\scripts\validate-config.ps1 -Config .\example\pipelines\postgresql-currency.yaml
.\minifi\scripts\render-k8s.ps1 -Config .\example\pipelines\postgresql-currency.yaml
.\minifi\scripts\deploy-job.ps1 -Config .\example\pipelines\postgresql-currency.yaml
```

## Example Contents

- `Dockerfile` builds the reusable `minifi-java-base` image with JDBC drivers, Kafka NAR, Parquet NAR, and AWS/S3 NARs.
- `Jenkinsfile` mirrors the root Jenkins image build parameters.
- `k8s` contains the original hand-written example manifests.
- `scripts` contains the original example deploy helpers.

The root `minifi/` folder contains the reusable rendering framework, validation schemas, resource profiles, defaults, and rendered Kubernetes manifests. Concrete sample pipeline YAML files live in `example/pipelines`.
