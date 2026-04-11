# Security

## Credenciais
- Nunca versionar `.env`.
- Nunca armazenar senha em codigo.
- Usar apenas variaveis de ambiente para SMTP.
- Evitar compartilhar logs que contenham dados sensiveis.

## Dados coletados
- Guardar apenas o necessario para auditoria.
- Evitar persistir dados pessoais sem necessidade.
- Revisar artefatos antes de compartilhar externamente.

## Execucao
- Rodar scripts apenas dentro do workspace do projeto.
- Nao elevar privilegios sem necessidade.
- Preferir fontes oficiais para reduzir risco de desinformacao.

## Git
- Nao commitar `.env`, relatórios operacionais temporarios e artefatos brutos.
- Revisar `git diff` antes de qualquer commit.
