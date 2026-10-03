# AGENTS.md

Scope: files in `scripts/`.

## Informatica to MiniFi Config Converter

Use `informatica_to_minifi_config.py` when converting Informatica workflow/source XML exports into example MiniFi pipeline YAML.

The converter parses `SOURCE` entries and, when present, field metadata from `SOURCEFIELD`, `TARGETFIELD`, or `TRANSFORMFIELD` entries scoped under source/target objects. If field metadata exists for a table, it writes a schema file and adds `schemaRef` to the generated pipeline table config. By default schemas are written to `example/schemas` when the output is under `example/pipelines`.

Example:

```powershell
python scripts/informatica_to_minifi_config.py `
  --workflow example/wfs/T24.xml `
  --table CURRENCY `
  --pipeline-name postgresql-t24-currency `
  --source-type postgresql `
  --s3-prefix runs/t24 `
  --profile medium `
  --tables-per-pod 10 `
  --output example/pipelines/postgresql-t24-currency.yaml
```

Use `--schema-output-dir example/schemas` when you want to force where generated schema refs are written.

Then validate/render/deploy with:

```powershell
python minifi/scripts/render_minifi.py validate --config example/pipelines/postgresql-t24-currency.yaml
python minifi/scripts/render_minifi.py render --config example/pipelines/postgresql-t24-currency.yaml --env .env
minifi/scripts/deploy-job.ps1 -Config example/pipelines/postgresql-t24-currency.yaml
```

Do not print `.env` values or generated Kubernetes Secret contents.
