# Troubleshooting

## OpenClaw nao encontra workspace
Checklist:
1. Confirmar caminho em `openclaw/openclaw.json`.
2. Instalar o arquivo em `%USERPROFILE%\.openclaw\openclaw.json`.
3. Confirmar permissao de leitura da pasta do projeto.

## Screenshots nao sao gerados
Checklist:
1. Conferir pasta `data/artifacts/screenshots/`.
2. Rodar `python scripts/run_manual_cycle.py --job-id debug_shot`.
3. Verificar permissao de escrita no disco.

## E-mail nao envia
Checklist:
1. Conferir `EMAIL_USER`.
2. Conferir `EMAIL_PASSWORD`.
3. Conferir `SMTP_HOST` e `SMTP_PORT`.
4. Validar app password no provedor de e-mail.

## Dedupe agrupando demais
Acao:
- aumentar `--threshold` em `scripts/dedupe_campaigns.py` (ex.: 0.92)

## Muitas campanhas em review
Acao:
- melhorar discover/capture para evidencias mais claras
- enriquecer beneficio e prazo na extracao
