param(
    [Parameter(Mandatory = $true)]
    [string] $Config,

    [string] $OutputRoot,

    [string] $EnvPath,

    [string] $RunTimestamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ'),

    [Alias('Parameter')]
    [string[]] $RuntimeParameter = @(),

    [string[]] $Object = @()
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $PSCommandPath
if (-not $OutputRoot) { $OutputRoot = Join-Path (Split-Path -Parent $scriptDir) 'rendered' }
if (-not $EnvPath) { $EnvPath = Join-Path (Split-Path -Parent (Split-Path -Parent $scriptDir)) '.env' }
$renderer = Join-Path $scriptDir 'render_minifi.py'
function Invoke-Kubectl {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]] $ArgsList)
    & kubectl @ArgsList
    if ($LASTEXITCODE -ne 0) {
        throw "kubectl failed: kubectl $($ArgsList -join ' ')"
    }
}


$renderArgs = @(
    'render',
    '--config', $Config,
    '--output-root', $OutputRoot,
    '--env', $EnvPath,
    '--run-timestamp', $RunTimestamp
)
foreach ($item in $RuntimeParameter) {
    if ($item -notmatch '^[A-Z][A-Z0-9_]*=.+$') {
        throw "Runtime parameter must use NAME=value format: $item"
    }
    $renderArgs += @('--param', $item)
}
foreach ($objectName in $Object) {
    if ([string]::IsNullOrWhiteSpace($objectName)) {
        throw 'Object name must not be empty'
    }
    $renderArgs += @('--object', $objectName)
}
$renderOutput = python $renderer @renderArgs
if ($LASTEXITCODE -ne 0) {
    throw 'MiniFi render failed'
}
$renderedDirs = $renderOutput | Where-Object { $_ -and (Test-Path $_) }
if (-not $renderedDirs) {
    throw 'Renderer did not return any manifest directories'
}

$sourceInfo = python $renderer source-info --config $Config | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw 'MiniFi source metadata failed'
}

$values = @{}
Get-Content $EnvPath | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]*)=(.*)$') {
        $values[$matches[1].Trim()] = $matches[2].Trim().Trim('"').Trim("'")
    }
}

$required = @($sourceInfo.usernameEnv, $sourceInfo.passwordEnv, 'S3_HOST', 'S3_ACCESS_KEY', 'S3_ACCESS_SECRET', 'S3_BUCKET_NAME')
$missing = $required | Where-Object { -not $values.ContainsKey($_) -or -not $values[$_] }
if ($missing) {
    throw "Missing required .env keys: $($missing -join ', ')"
}

$s3Secret = @{
    apiVersion = 'v1'
    kind = 'Secret'
    metadata = @{ name = $sourceInfo.s3SecretName; namespace = $sourceInfo.namespace }
    type = 'Opaque'
    stringData = @{
        host = $values.S3_HOST
        accessKey = $values.S3_ACCESS_KEY
        accessSecret = $values.S3_ACCESS_SECRET
        bucketName = $values.S3_BUCKET_NAME
    }
} | ConvertTo-Json -Depth 5

$s3Secret | kubectl apply --server-side -f - | Out-Host
if ($LASTEXITCODE -ne 0) { throw 'kubectl failed applying S3 secret' }

foreach ($dir in $renderedDirs) {
    Write-Host "Applying rendered MiniFi manifests from $dir"
    $groupSlug = Split-Path -Leaf $dir
    $databaseSecretName = "$($sourceInfo.databaseSecretName)-$groupSlug"
    $databaseSecret = @{
        apiVersion = 'v1'
        kind = 'Secret'
        metadata = @{ name = $databaseSecretName; namespace = $sourceInfo.namespace }
        type = 'Opaque'
        stringData = @{
            username = $values[$sourceInfo.usernameEnv]
            password = $values[$sourceInfo.passwordEnv]
        }
    } | ConvertTo-Json -Depth 5
    $databaseSecret | kubectl apply --server-side -f - | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "kubectl failed applying database secret $databaseSecretName" }
    Invoke-Kubectl apply --server-side -f (Join-Path $dir 'configmap-templates.yaml') | Out-Host
    Invoke-Kubectl apply --server-side -f (Join-Path $dir 'configmap-runtime.yaml') | Out-Host
    $job = Get-Content (Join-Path $dir 'job.yaml') | Select-String -Pattern '^  name: ' | Select-Object -First 1
    if ($job) {
        $jobName = ($job.Line -replace '^\s*name:\s*', '').Trim()
        Invoke-Kubectl delete job $jobName -n $sourceInfo.namespace --ignore-not-found --wait=true | Out-Host
    }
    Invoke-Kubectl apply --server-side -f (Join-Path $dir 'job.yaml') | Out-Host

    $deadline = (Get-Date).AddHours(12)
    $pollIntervalSeconds = 5
    $lastProgress = 'progress=not available yet'
    while ((Get-Date) -lt $deadline) {
        $succeeded = kubectl get job $jobName -n $sourceInfo.namespace -o jsonpath='{.status.succeeded}' 2>$null
        $failed = kubectl get job $jobName -n $sourceInfo.namespace -o jsonpath='{.status.failed}' 2>$null
        $active = kubectl get job $jobName -n $sourceInfo.namespace -o jsonpath='{.status.active}' 2>$null
        $podPhase = kubectl get pods -n $sourceInfo.namespace -l "job-name=$jobName" -o jsonpath='{.items[0].status.phase}' 2>$null
        $containerReady = kubectl get pods -n $sourceInfo.namespace -l "job-name=$jobName" -o jsonpath='{.items[0].status.containerStatuses[0].ready}' 2>$null
        $canReadLogs = $podPhase -in @('Running', 'Succeeded', 'Failed') -and ($containerReady -eq 'true' -or $podPhase -in @('Succeeded', 'Failed'))
        if ($canReadLogs) {
            $monitorLine = kubectl logs job/$jobName -n $sourceInfo.namespace --tail=200 2>$null |
                Select-String -Pattern 'Job monitor:' |
                Select-Object -Last 1
            if ($monitorLine) {
                $lastProgress = $monitorLine.Line.Trim()
            }
        }
        if ($succeeded -and [int]$succeeded -ge 1) {
            Write-Host "MiniFi Job $jobName completed successfully"
            kubectl logs job/$jobName -n $sourceInfo.namespace --tail=200 | Out-Host
            break
        }
        if ($failed -and [int]$failed -ge 1) {
            Write-Host "MiniFi Job $jobName failed; recent logs:" -ForegroundColor Red
            kubectl logs job/$jobName -n $sourceInfo.namespace --tail=300 | Out-Host
            throw "MiniFi Job $jobName failed"
        }
        Write-Host "Waiting for MiniFi Job $jobName (pod=$podPhase active=$active succeeded=$succeeded failed=$failed; $lastProgress)"
        Start-Sleep -Seconds $pollIntervalSeconds
    }
    if ((Get-Date) -ge $deadline) {
        kubectl logs job/$jobName -n $sourceInfo.namespace --tail=300 | Out-Host
        throw "Timed out waiting for MiniFi Job $jobName"
    }
}

