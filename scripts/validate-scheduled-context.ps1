[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
  [string]$PythonPath,

  [Parameter(Mandatory = $true)]
  [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
  [string]$NodePath,

  [Parameter(Mandatory = $true)]
  [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
  [string]$ConfigPath
)

$ErrorActionPreference = 'Stop'
$env:PATH = "$(Split-Path -Parent $NodePath);$env:PATH"
$env:WUP_CONFIG = (Resolve-Path -LiteralPath $ConfigPath).Path
$notifier = Join-Path (Split-Path -Parent $PSCommandPath) 'run_notifier.py'
& $PythonPath $notifier --config $env:WUP_CONFIG --dry-run
if ($LASTEXITCODE -ne 0) { throw 'Configured WUP dry run failed in the scheduled-user context.' }
[pscustomobject]@{ Validated = $true; ConfigPath = $env:WUP_CONFIG } | ConvertTo-Json -Compress
