param(
    [Parameter(Mandatory = $true)]
    [string] $Config,

    [string] $OutputRoot,

    [string] $EnvPath,

    [string] $RunTimestamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $PSCommandPath
if (-not $OutputRoot) { $OutputRoot = Join-Path (Split-Path -Parent $scriptDir) 'rendered' }
if (-not $EnvPath) { $EnvPath = Join-Path (Split-Path -Parent (Split-Path -Parent $scriptDir)) '.env' }
python (Join-Path $scriptDir 'render_minifi.py') render --config $Config --output-root $OutputRoot --env $EnvPath --run-timestamp $RunTimestamp
