# AGENTS.md

## Objetivo do projeto
Este projeto monitora promocoes, campanhas e acoes comerciais de instituicoes financeiras no Brasil.

## Resultado esperado
O sistema deve pesquisar campanhas, capturar evidencias visuais, extrair dados estruturados, validar qualidade e gerar um relatorio diario por e-mail.

## Regras obrigatorias
- Nunca afirmar a existencia de uma campanha sem evidencia suficiente.
- Sempre preferir fontes oficiais.
- Sempre salvar screenshot para campanhas relevantes.
- Sempre separar campanhas confirmadas, em revisao e descartadas.
- Nunca sobrescrever dados brutos de captura.
- Nunca omitir incertezas.
- Se nao souber, use null, unclear ou review.

## Estrutura de dados
- candidates = hipoteses iniciais
- observations = evidencias coletadas
- campaigns = campanhas estruturadas e validadas
- reports = saidas editoriais

## Regras de nomeacao
- institution_id em minusculo, sem espacos
- datas em ISO-8601
- arquivos com timestamp e prefixo de tipo
- score entre 0 e 1

## Regras de qualidade
- Sem print, nao entra como validada.
- Sem URL rastreavel, nao entra como validada.
- Fonte nao oficial exige mais evidencia.
- Post antigo sem data clara deve ir para revisao.
- Nao inventar regulamento, beneficio ou datas.

## Principios de implementacao
- Preferir clareza a complexidade.
- Preferir scripts previsiveis a logica implicita demais dentro dos agentes.
- Usar schemas JSON.
- Validar arquivos antes de avancar para a proxima etapa.
- Deixar logs uteis.

## O que o Codex deve fazer
- Criar e manter a estrutura do projeto.
- Implementar scripts Python simples, bem comentados e testaveis.
- Melhorar o sistema incrementalmente sem quebrar contratos JSON.
- Sugerir refactors seguros quando necessario.

## O que o Codex nunca deve fazer
- Remover dados brutos sem instrucao explicita.
- Mudar o significado dos status.
- Alterar a estrutura dos schemas sem atualizar os consumidores.
- Assumir credenciais no codigo.
