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
$scriptDirectory = Split-Path -Parent $PSCommandPath
$notifier = Join-Path $scriptDirectory 'run_notifier.py'
if (-not (Test-Path -LiteralPath $notifier -PathType Leaf)) { throw "Toolchain notifier was not found: $notifier" }

# Scheduled Task environments do not reliably inherit interactive PATH entries.
$env:PATH = "$(Split-Path -Parent $NodePath);$(Split-Path -Parent $PythonPath);$env:PATH"
$env:WUP_CONFIG = (Resolve-Path -LiteralPath $ConfigPath).Path
& $PythonPath $notifier --config $env:WUP_CONFIG
exit $LASTEXITCODE
