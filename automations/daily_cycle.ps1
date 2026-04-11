$ErrorActionPreference = "Stop"
$root = "C:\Users\ZigPay\Documents\Agente Pesquisa"
Set-Location $root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
  throw "Ambiente virtual nao encontrado em .venv"
}

$jobId = "daily_$(Get-Date -Format yyyyMMdd_HHmmss)"
& ".venv\Scripts\python.exe" "scripts/run_manual_cycle.py" --job-id $jobId
