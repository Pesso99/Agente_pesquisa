# Runbook

## 1. Preparacao
1. Criar ambiente virtual: `python -m venv .venv`
2. Ativar ambiente: `.venv\Scripts\activate`
3. Instalar dependencias: `pip install -r requirements.txt`
4. Configurar `.env` a partir de `.env.example`

## 2. Bootstrap
Executar:
`python scripts/bootstrap_project.py`

Esperado:
- estrutura `data/` criada
- validacao de configuracoes essenciais

## 3. Ciclo manual ponta a ponta
Executar:
`python scripts/run_manual_cycle.py --job-id manual_001`

Saidas esperadas:
- `data/candidates/*.json`
- `data/observations/*.json`
- `data/campaigns/*.json`
- `data/reports/report_manual_001.{json,md,html}`
- `data/jobs/manual_001_summary.json`

## 4. Runtime multiagente V2
Ciclo autonomo:
`python scripts/run_cycle.py --autonomous --job-id auto_001`

Ciclo completo real (Instagram sem login via Playwright):
`python scripts/run_real_full.py --job-id real_001 --instagram-capture-mode playwright_dismiss --instagram-dismiss-attempts 3 --instagram-dismiss-timeout 2`

Primeira execucao de Playwright:
`python -m playwright install chromium`

Fila de revisao:
`python scripts/review_queue.py --status review`

Aprovar e enviar:
`python scripts/approve_send.py --job-id auto_001 --approved-by analista --send-now --to email@exemplo.com`

Replay de falhas:
`python scripts/replay_failed.py --job-id all`

## 5. Scripts auxiliares
- Normalizacao: `python scripts/normalize_campaigns.py`
- Dedupe: `python scripts/dedupe_campaigns.py`
- Revalidacao: `python scripts/validate_campaigns.py`
- Geracao de relatorio: `python scripts/generate_report.py --report-id manual_extra`
- Exportacao HTML: `python scripts/export_html_report.py --markdown data/reports/manual_extra.md --html data/reports/manual_extra_export.html`
- Retry de jobs: `python scripts/retry_failed_jobs.py`

## 6. OpenClaw
- Template local: `openclaw/openclaw.json`
- Instalacao guiada: `openclaw/install_user_config.ps1`
