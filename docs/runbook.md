# Runbook

## 1. Preparacao
1. Criar ambiente virtual:
   `python -m venv .venv`
2. Ativar ambiente:
   `.venv\Scripts\activate`
3. Instalar dependencias:
   `pip install -r requirements.txt`
4. Configurar `.env` a partir de `.env.example`.

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

## 4. Envio de e-mail
Opcao integrada no ciclo:
`python scripts/run_manual_cycle.py --job-id manual_002 --send-email --to email@exemplo.com`

Opcao direta:
`python scripts/send_email.py --html data/reports/report_manual_002.html --subject "Monitor diario" --to email@exemplo.com`

## 5. Scripts auxiliares
- Normalizacao: `python scripts/normalize_campaigns.py`
- Dedupe: `python scripts/dedupe_campaigns.py`
- Revalidacao: `python scripts/validate_campaigns.py`
- Geracao de relatorio: `python scripts/generate_report.py --report-id manual_extra`
- Exportacao HTML: `python scripts/export_html_report.py --markdown data/reports/manual_extra.md --html data/reports/manual_extra_export.html`
- Retry de jobs: `python scripts/retry_failed_jobs.py`

## 6. Automacao minima
- Cron exemplo: `automations/daily_cycle.cron.example`
- Execucao PowerShell: `automations/daily_cycle.ps1`

## 7. OpenClaw
- Template local: `openclaw/openclaw.json`
- Instalacao guiada: `openclaw/install_user_config.ps1`
