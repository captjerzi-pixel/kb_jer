param(
    [Parameter(Mandatory = $true)]
    [string] $Config
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
python (Join-Path $scriptDir 'render_minifi.py') validate --config $Config
