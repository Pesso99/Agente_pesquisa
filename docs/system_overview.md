# System Overview

## Objetivo
Monitorar campanhas e promocoes de instituicoes financeiras no Brasil com rastreabilidade completa.

## Arquitetura v1
O sistema segue arquitetura orientada a arquivos e handoffs JSON.

Fluxo principal:
maestro -> discover -> capture -> extract -> validate -> report -> sender

## Camadas
1. Operacao: scripts + automacao + OpenClaw
2. Desenvolvimento: codigo Python + schemas + skills
3. Persistencia: JSON + artefatos + relatorios

## Contratos
- Candidates: hipoteses iniciais
- Observations: evidencias capturadas
- Campaigns: dados estruturados e validados
- Reports: saida editorial consolidada
- Handoffs: envelopes de troca entre agentes

## Persistencia local
- `data/candidates/`
- `data/observations/`
- `data/campaigns/`
- `data/reports/`
- `data/jobs/`
- `data/logs/`
- `data/artifacts/screenshots/`
- `data/artifacts/raw_html/`
- `data/artifacts/raw_text/`

## Decisoes de v1
- Simplicidade primeiro: pipeline previsivel antes de scraping complexo
- Validacao por schema em todos os modelos principais
- Evidencia visual obrigatoria para status de alta confianca
