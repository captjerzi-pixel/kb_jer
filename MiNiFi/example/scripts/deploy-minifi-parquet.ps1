param(
    [ValidateSet('mssql', 'postgresql')]
    [string] $Source = 'mssql',

    [string] $RunTimestamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
)

$ErrorActionPreference = 'Stop'

$namespace = 'ci'
$jobName = 'minifi-java-opb-task-inst-run'
$secretName = 'minifi-database'
$s3SecretName = 'minifi-s3'
$k8sPath = Join-Path $PSScriptRoot '..\k8s'
$checkpointPvc = Join-Path $k8sPath 'checkpoint-pvc.yaml'
$envPath = Join-Path $PSScriptRoot '..\..\..\.env'
$sourceConfigFile = if ($Source -eq 'mssql') { 'mssql-opb-task-inst-run.yaml' } else { 'postgresql-currency.yaml' }
$sourceConfigPath = Join-Path $k8sPath "source-configs\$sourceConfigFile"
$values = @{}

Get-Content $envPath | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]*)=(.*)$') {
        $values[$matches[1].Trim()] = $matches[2].Trim().Trim('"').Trim("'")
    }
}

$sourceSettings = @{
    mssql = @{
        Required = @('MSSQL_USERNAME', 'MSSQL_PASSWORD')
        Username = 'MSSQL_USERNAME'
        Password = 'MSSQL_PASSWORD'
    }
    postgresql = @{
        Required = @('PSQL_HOSTNAME', 'PSQL_DB', 'PSQL_USERNAME', 'PSQL_PASSWORD')
        Username = 'PSQL_USERNAME'
        Password = 'PSQL_PASSWORD'
    }
}

$required = @($sourceSettings[$Source].Required) + @('S3_HOST', 'S3_ACCESS_KEY', 'S3_ACCESS_SECRET', 'S3_BUCKET_NAME')
$missing = $required | Where-Object { -not $values.ContainsKey($_) -or -not $values[$_] }
if ($missing) {
    throw "Missing required .env keys for source '$Source': $($missing -join ', ')"
}

$secret = @{
    apiVersion = 'v1'
    kind = 'Secret'
    metadata = @{ name = $secretName; namespace = $namespace }
    type = 'Opaque'
    stringData = @{
        username = $values[$sourceSettings[$Source].Username]
        password = $values[$sourceSettings[$Source].Password]
    }
} | ConvertTo-Json -Depth 5

$s3Secret = @{
    apiVersion = 'v1'
    kind = 'Secret'
    metadata = @{ name = $s3SecretName; namespace = $namespace }
    type = 'Opaque'
    stringData = @{
        host = $values.S3_HOST
        accessKey = $values.S3_ACCESS_KEY
        accessSecret = $values.S3_ACCESS_SECRET
        bucketName = $values.S3_BUCKET_NAME
    }
} | ConvertTo-Json -Depth 5

$runtimeValues = @{}
foreach ($key in $values.Keys) {
    $runtimeValues[$key] = $values[$key]
}
$runtimeValues.RUN_TIMESTAMP = $RunTimestamp
$runtimeConfig = Get-Content -Raw $sourceConfigPath
foreach ($key in $runtimeValues.Keys) {
    $runtimeConfig = $runtimeConfig.Replace('${' + $key + '}', $runtimeValues[$key])
}
$unresolved = [regex]::Matches($runtimeConfig, '\$\{[^}]+\}') | ForEach-Object { $_.Value } | Select-Object -Unique
if ($unresolved) {
    throw "Unresolved placeholders in $sourceConfigPath: $($unresolved -join ', ')"
}

Write-Host "Deploying MiniFi source '$Source' with RUN_TIMESTAMP=$RunTimestamp"
$secret | kubectl apply -f - | Out-Host
$s3Secret | kubectl apply -f - | Out-Host
kubectl apply -f $checkpointPvc | Out-Host
kubectl apply -f (Join-Path $k8sPath 'configmap-templates.yaml') | Out-Host
$runtimeConfig | kubectl apply -f - | Out-Host
kubectl scale deployment/minifi-java-opb-task-inst-run -n $namespace --replicas=0 --timeout=5m 2>$null | Out-Host
kubectl delete job $jobName -n $namespace --ignore-not-found --wait=true | Out-Host
kubectl apply -f (Join-Path $k8sPath 'job.yaml') | Out-Host
$deadline = (Get-Date).AddHours(12)
while ((Get-Date) -lt $deadline) {
    $job = kubectl get job $jobName -n $namespace -o json | ConvertFrom-Json
    $complete = $job.status.conditions | Where-Object { $_.type -eq 'Complete' -and $_.status -eq 'True' }
    if ($complete) {
        break
    }
    $failed = $job.status.conditions | Where-Object { $_.type -eq 'Failed' -and $_.status -eq 'True' }
    if ($failed) {
        kubectl logs "job/$jobName" -n $namespace | Out-Host
        throw "Job $jobName failed"
    }
    Start-Sleep -Seconds 30
}
if ((Get-Date) -ge $deadline) {
    kubectl logs "job/$jobName" -n $namespace | Out-Host
    throw "Timed out waiting for job $jobName to complete"
}
kubectl logs "job/$jobName" -n $namespace | Out-Host
