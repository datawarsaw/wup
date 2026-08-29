[CmdletBinding()]
param(
  [ValidateSet('Install', 'Status', 'Run', 'Remove', 'ValidateContext')]
  [string]$Action = 'Status',
  [string]$TaskName = 'WUP Toolchain Update Watch',
  [string]$PythonPath,
  [string]$NodePath,
  [string]$ConfigPath
)

$ErrorActionPreference = 'Stop'
$scriptDirectory = Split-Path -Parent $PSCommandPath
$runner = Join-Path $scriptDirectory 'run-scheduled-notifier.ps1'
$validator = Join-Path $scriptDirectory 'validate-scheduled-context.ps1'
$defaultConfig = Join-Path (Split-Path -Parent $scriptDirectory) 'wup.toml'

function Resolve-Executable([string]$providedPath, [string]$commandName) {
  if ($providedPath) {
    if (-not (Test-Path -LiteralPath $providedPath -PathType Leaf)) { throw "Executable was not found: $providedPath" }
    return (Resolve-Path -LiteralPath $providedPath).Path
  }
  return (Get-Command $commandName -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
}

function Quote-TaskArgument([string]$value) { return '"' + $value.Replace('"', '\"') + '"' }

function Get-TaskStatus([string]$name) {
  $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  if (-not $task) { return [pscustomobject]@{ TaskName = $name; Registered = $false } }
  $info = Get-ScheduledTaskInfo -TaskName $name
  return [pscustomobject]@{
    TaskName = $name; Registered = $true; State = [string]$task.State
    NextRunTime = $info.NextRunTime; LastRunTime = $info.LastRunTime; LastTaskResult = $info.LastTaskResult
    UserId = $task.Principal.UserId; LogonType = [string]$task.Principal.LogonType; RunLevel = [string]$task.Principal.RunLevel
    StartWhenAvailable = [bool]$task.Settings.StartWhenAvailable; WakeToRun = [bool]$task.Settings.WakeToRun
    MultipleInstances = [string]$task.Settings.MultipleInstances; ExecutionTimeLimit = [string]$task.Settings.ExecutionTimeLimit
    Execute = $task.Actions[0].Execute; Arguments = $task.Actions[0].Arguments
  }
}

if ($Action -eq 'Status') { Get-TaskStatus $TaskName | ConvertTo-Json -Depth 4; exit 0 }
if ($Action -eq 'Remove') {
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
  [pscustomobject]@{ TaskName = $TaskName; Registered = $false } | ConvertTo-Json -Compress
  exit 0
}

$resolvedPython = Resolve-Executable $PythonPath 'python.exe'
$resolvedNode = Resolve-Executable $NodePath 'node.exe'
$powerShell = (Get-Command powershell.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source

if ($Action -eq 'Install') {
  if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) { throw "Scheduled runner was not found: $runner" }
  $configCandidate = if ($ConfigPath) { $ConfigPath } else { $defaultConfig }
  $resolvedConfig = Resolve-Executable $configCandidate 'unused'
  $arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File $(Quote-TaskArgument $runner) -PythonPath $(Quote-TaskArgument $resolvedPython) -NodePath $(Quote-TaskArgument $resolvedNode) -ConfigPath $(Quote-TaskArgument $resolvedConfig)"
  $trigger = New-ScheduledTaskTrigger -Daily -At 8:00AM
  $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
  $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskName $TaskName -Action (New-ScheduledTaskAction -Execute $powerShell -Argument $arguments) -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
  Get-TaskStatus $TaskName | ConvertTo-Json -Depth 4
  exit 0
}

if ($Action -eq 'Run') {
  if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) { throw "Task is not registered: $TaskName" }
  Start-ScheduledTask -TaskName $TaskName
  Get-TaskStatus $TaskName | ConvertTo-Json -Depth 4
  exit 0
}

$validationName = "$TaskName Scheduler Validation"
try {
  $configCandidate = if ($ConfigPath) { $ConfigPath } else { $defaultConfig }
  $resolvedConfig = Resolve-Executable $configCandidate 'unused'
  $arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File $(Quote-TaskArgument $validator) -PythonPath $(Quote-TaskArgument $resolvedPython) -NodePath $(Quote-TaskArgument $resolvedNode) -ConfigPath $(Quote-TaskArgument $resolvedConfig)"
  $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
  $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskName $validationName -Action (New-ScheduledTaskAction -Execute $powerShell -Argument $arguments) -Settings $settings -Principal $principal -Force | Out-Null
  Start-ScheduledTask -TaskName $validationName
  $deadline = (Get-Date).AddSeconds(60)
  do { Start-Sleep -Seconds 2; $info = Get-ScheduledTaskInfo -TaskName $validationName; $task = Get-ScheduledTask -TaskName $validationName } while ($task.State -eq 'Running' -and (Get-Date) -lt $deadline)
  if ($task.State -eq 'Running') { throw 'Scheduled context validation did not complete within 60 seconds.' }
  if ($info.LastTaskResult -ne 0) { throw "Scheduled context validation failed with result $($info.LastTaskResult)." }
  [pscustomobject]@{ TaskName = $validationName; LastTaskResult = $info.LastTaskResult; Validated = $true } | ConvertTo-Json -Compress
} finally {
  if (Get-ScheduledTask -TaskName $validationName -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $validationName -Confirm:$false }
}
