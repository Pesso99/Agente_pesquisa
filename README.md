# Agente Pesquisa v2

Monitor de promocoes e campanhas de instituicoes financeiras no Brasil com agentes LLM reais.

## Visao geral
O pipeline executa o fluxo com IA integrada:

1. discover: gera candidates (crawl de fontes oficiais)
2. capture: coleta evidencias visuais e textuais (Playwright/Chrome)
3. **extract**: LLM analisa texto capturado e extrai campanhas estruturadas
4. **validate**: dois LLMs independentes (primary + critic) avaliam cada campanha
5. **report**: LLM redige resumo executivo editorial
6. sender: envia relatorio por e-mail (opcional, com aprovacao humana)

Cada etapa marcada com **negrito** usa chamadas reais a modelos OpenAI (gpt-5.4-mini / gpt-5.4-nano). Se a API estiver indisponivel, o sistema faz fallback para logica deterministica.

## Estrutura principal
- `config/`: instituicoes, regras, modelos por agente
- `schemas/`: contratos JSON
- `skills/`: system prompts consumidos pelos agentes LLM
- `app/`: nucleo Python (llm_client, orchestrator, scoring, quality_gate, reporter)
- `scripts/`: execucao operacional
- `data/`: persistencia local
- `docs/`: operacao e seguranca

## Setup rapido
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Variaveis de ambiente
Copie `.env.example` para `.env` e configure:
- `OPENAI_API_KEY` (obrigatorio para agentes LLM)
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

### Execucao via Telegram Bot

Configure no `.env`:

```bash
TELEGRAM_BOT_TOKEN=<seu_token_do_botfather>
TELEGRAM_ALLOWED_CHAT_IDS=<id_chat_1>,<id_chat_2>
```

Iniciar bot:

```bash
python scripts/run_telegram_bot.py
```

Comandos no Telegram:
- `/start` - ajuda inicial
- `/run` - executa ciclo autonomo e envia o relatorio HTML
- `/status` - status atual da execucao
- `/last` - reenvia o ultimo HTML gerado

Observacao: o Telegram nao renderiza CSS/HTML completo no corpo da mensagem. Por isso, o bot envia o arquivo `.html` como documento para preservar a formatacao completa.

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

## Base historica e feedback loop

O sistema acumula todas as campanhas na base historica (SQLite) ao final de cada ciclo.
Um revisor humano pode confirmar ou negar campanhas, e os padroes aprendidos sao usados para melhorar ciclos futuros.

Revisar campanhas pendentes de feedback:
```bash
python scripts/confirm_campaign.py --status review --batch
```

Revisar campanha especifica:
```bash
python scripts/confirm_campaign.py --campaign-id camp_20260412_001
```

Recalcular padroes aprendidos a partir do feedback existente:
```bash
python scripts/confirm_campaign.py --relearn
```

Ver estatisticas de feedback e padroes:
```bash
python scripts/confirm_campaign.py --stats
```

Os padroes aprendidos ajustam automaticamente:
- **Descoberta**: confianca inicial dos candidatos (boost/penalidade por instituicao e tipo de fonte)
- **Extracao**: prior de probabilidade com base em tipo de campanha e historico
- **Validacao**: ajuste de score final com base em padroes historicos
- **Relatorio**: secao de insights historicos com taxa de acerto e padroes aprendidos

Execucao completa real em um comando:
```bash
python scripts/run_real_full.py --job-id real_001 --max-total 8 --max-per-institution 2 --capture-timeout 12
```

Execucao completa real com aprovacao e envio no final:
```bash
python scripts/run_real_full.py --job-id real_002 --approve-and-send --approved-by seu_nome --to voce@empresa.com
```

Modo Instagram sem login (Playwright + dismiss modal):
```bash
python scripts/run_real_full.py --job-id real_ig_001 --instagram-capture-mode playwright_dismiss --instagram-dismiss-attempts 3 --instagram-dismiss-timeout 2
```

Se for a primeira vez no ambiente, instale o browser do Playwright:
```bash
python -m playwright install chromium
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

## Arquitetura LLM

Os agentes usam modelos configurados em `config/agent_models.json`:
- **extract** (gpt-5.4-mini): analisa texto de paginas e extrai campos estruturados de campanhas
- **validate** (gpt-5.4-mini): avalia veracidade e completude da campanha
- **validate_critic** (gpt-5.4-mini): contraponto rigoroso ao validador primario
- **quality_gate** (gpt-5.4-nano): classifica tipo de pagina (campanha, institucional, login, erro)
- **report** (gpt-5.4-mini): redige resumo executivo editorial

Os system prompts ficam em `skills/{agent_name}/SKILL.md` e sao carregados automaticamente pelo `app/llm_client.py`.

Se `OPENAI_API_KEY` nao estiver configurada, todas as etapas usam fallback deterministico (regex/keywords/formulas).

## Status da v2
Pipeline com IA integrada, web search no discover, base historica com feedback loop e aprendizado de padroes. Proximos passos: analise visual de screenshots com modelo multimodal, dashboard web para revisao.
