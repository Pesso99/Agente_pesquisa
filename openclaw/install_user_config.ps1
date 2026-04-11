$ErrorActionPreference = "Stop"

$projectConfig = Join-Path $PSScriptRoot "openclaw.json"
$userOpenClawDir = Join-Path $env:USERPROFILE ".openclaw"
$userConfig = Join-Path $userOpenClawDir "openclaw.json"

if (-not (Test-Path $projectConfig)) {
  throw "Arquivo nao encontrado: $projectConfig"
}

New-Item -ItemType Directory -Path $userOpenClawDir -Force | Out-Null

if (Test-Path $userConfig) {
  $backup = Join-Path $userOpenClawDir ("openclaw.backup." + (Get-Date -Format "yyyyMMdd_HHmmss") + ".json")
  Copy-Item -Path $userConfig -Destination $backup -Force
  Write-Host "Backup criado em: $backup"
}

Copy-Item -Path $projectConfig -Destination $userConfig -Force
Write-Host "Configuracao instalada em: $userConfig"
