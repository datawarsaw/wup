[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
  [string]$NodePath,

  [string]$EmailHelper = 'C:\AI\workstation-ops-mcp\dist\cloudflare-email-cli.js'
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $EmailHelper -PathType Leaf)) { throw 'The Workstation Ops Cloudflare Email helper is unavailable.' }
$env:PATH = "$(Split-Path -Parent $NodePath);$env:PATH"
$raw = & $NodePath $EmailHelper status
if ($LASTEXITCODE -ne 0) { throw 'Cloudflare Email status returned a nonzero exit code.' }
$status = $raw | Out-String | ConvertFrom-Json
if (-not ($status.configured -and $status.tokenPresent -and $status.accountIdPresent -and $status.recipientVerified)) {
  throw 'Cloudflare Email is not fully configured for the scheduled-user context.'
}
