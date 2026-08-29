[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
  [string]$PythonPath,

  [Parameter(Mandatory = $true)]
  [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
  [string]$NodePath
)

$ErrorActionPreference = 'Stop'
$scriptDirectory = Split-Path -Parent $PSCommandPath
$notifier = Join-Path $scriptDirectory 'run_notifier.py'
if (-not (Test-Path -LiteralPath $notifier -PathType Leaf)) { throw "Toolchain notifier was not found: $notifier" }

# Scheduled Task environments do not reliably inherit interactive PATH entries.
$env:PATH = "$(Split-Path -Parent $NodePath);$(Split-Path -Parent $PythonPath);$env:PATH"
& $PythonPath $notifier
exit $LASTEXITCODE
