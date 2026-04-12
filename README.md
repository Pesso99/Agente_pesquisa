# Agente Pesquisa v1

Monitor de promocoes e campanhas de instituicoes financeiras no Brasil usando OpenClaw + Codex.

## Visao geral
O pipeline v1 executa o fluxo:

1. discover: gera candidates
2. capture: coleta evidencias e artefatos
3. extract: gera campaigns estruturadas
4. validate: aplica score e status
5. report: gera relatorio Markdown/HTML/JSON
6. sender: envia relatorio por e-mail (opcional)

## Estrutura principal
- `config/`: instituicoes e regras
- `schemas/`: contratos JSON
- `skills/`: instrucoes por agente
- `app/`: nucleo Python
- `scripts/`: execucao operacional
- `data/`: persistencia local
- `docs/`: operacao e seguranca
- `openclaw/`: configuracao exemplo para OpenClaw

## Setup rapido
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Variaveis de ambiente
Copie `.env.example` para `.env` e configure:
- `EMAIL_USER`
- `EMAIL_PASSWORD`
- `SMTP_HOST`
- `SMTP_PORT`

## Como rodar ciclo manual
```bash
python scripts/bootstrap_project.py
python scripts/run_manual_cycle.py --job-id manual_001
```

Para enviar e-mail no mesmo ciclo:
```bash
python scripts/run_manual_cycle.py --job-id manual_002 --send-email --to voce@empresa.com
```

Para modo real mais rapido/controlado:
```bash
python scripts/run_manual_cycle.py --job-id real_001 --max-total 8 --max-per-institution 2 --capture-timeout 12
```

## Runtime V2 multiagente autônomo
Executar ciclo autônomo:
```bash
python scripts/run_cycle.py --autonomous --job-id auto_001 --max-total 8 --capture-timeout 12
```

Listar fila de revisão:
```bash
python scripts/review_queue.py --status review
```

Aprovar envio humano e disparar e-mail:
```bash
python scripts/approve_send.py --job-id auto_001 --approved-by seu_nome --send-now --to voce@empresa.com
```

Reexecutar jobs com falha:
```bash
python scripts/replay_failed.py --job-id all
```

Execucao completa real em um comando:
```bash
python scripts/run_real_full.py --job-id real_001 --max-total 8 --max-per-institution 2 --capture-timeout 12
```

Execucao completa real com aprovacao e envio no final:
```bash
python scripts/run_real_full.py --job-id real_002 --approve-and-send --approved-by seu_nome --to voce@empresa.com
```

Observação:
- O sender está protegido por aprovação humana (`approval_status=approved`).
- O estado operacional do runtime fica em `data/state/runtime.db`.

## Como validar localmente
```bash
python -m pytest
python scripts/run_manual_cycle.py --job-id smoke_test
```

## Cron e automacao
- Template OpenClaw: `openclaw/openclaw.json`
- Exemplo de cron local: `automations/daily_cycle.cron.example`
- Script PowerShell de automacao: `automations/daily_cycle.ps1`

## Status da v1
Base funcional pronta para evolucao incremental (mais fontes, scraping real, retries e observabilidade).
