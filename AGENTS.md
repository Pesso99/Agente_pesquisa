# AGENTS.md

## Objetivo do projeto
Monitorar promoções, campanhas e ações comerciais de instituições financeiras no Brasil.

## Regras gerais
- Nunca afirmar que existe uma campanha sem evidência.
- Sempre preferir fonte oficial.
- Sempre salvar screenshot quando uma campanha parecer relevante.
- Separar claramente "confirmada", "em validação" e "descartada".
- Nunca sobrescrever dados brutos de captura.
- Usar JSON estruturado para candidates, observations e campaigns.
- Relatórios devem ser objetivos e legíveis por humanos.

## Estrutura de dados
- candidates = hipóteses de campanha
- observations = evidências coletadas
- campaigns = campanha consolidada e validada

## Padrões
- institution_id em minúsculo e sem espaços
- datas em ISO-8601
- nome dos arquivos com timestamp
- score de confiança entre 0 e 1

## Regras de qualidade
- Sem print, não entra como confirmada.
- Sem URL, não entra como confirmada.
- Post antigo sem data clara deve ir para revisão.
- Campanhas não oficiais precisam de mais de uma evidência.

## Saída esperada
- JSON limpo
- Resumo executivo diário
- Evidências salvas em /data/artifacts/screenshots
