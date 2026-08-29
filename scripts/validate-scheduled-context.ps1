[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
  [string]$NodePath,

  [string]$EmailHelper = $env:WUP_EMAIL_HELPER
)

$ErrorActionPreference = 'Stop'
if (-not $EmailHelper) {
  [pscustomobject]@{ Validated = $true; EmailAdapter = 'not configured' } | ConvertTo-Json -Compress
  exit 0
}
if (-not (Test-Path -LiteralPath $EmailHelper -PathType Leaf)) { throw 'Configured email helper is unavailable.' }
$env:PATH = "$(Split-Path -Parent $NodePath);$env:PATH"
$raw = & $NodePath $EmailHelper status
if ($LASTEXITCODE -ne 0) { throw 'Cloudflare Email status returned a nonzero exit code.' }
$status = $raw | Out-String | ConvertFrom-Json
if (-not ($status.configured -and $status.tokenPresent -and $status.accountIdPresent -and $status.recipientVerified)) {
  throw 'Cloudflare Email is not fully configured for the scheduled-user context.'
}
