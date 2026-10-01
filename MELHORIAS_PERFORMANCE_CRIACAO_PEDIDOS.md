# Melhorias de desempenho na criação de pedidos

## Contexto e conclusão da avaliação

Sintoma informado no primeiro de cinco PDFs, `20003945.pdf`:

```text
Aguardando extração do Codex...
Extração salva: ...\.controle\extracoes\v1\...json
Criando pedido e copiando estampas...
```

A extração já terminou segundo esse log. A mensagem seguinte antecede a execução completa de `criar_pedido.py`, inclusive o carregamento do catálogo. Ela não comprova que a cópia começou.

**Não foi possível confirmar um travamento do pedido específico.** O código contém operações potencialmente demoradas sem progresso visível e espera pelo subprocesso sem timeout. Isso explica a aparência de travamento, mas não exclui bloqueio de disco ou outra falha.

Na inspeção de 16/09/2026:

- Os processos Python 4124 e 12152 apresentaram aumento de CPU acumulada entre duas amostras: aproximadamente 44,12 → 46,06 s e 35,19 → 37,09 s, respectivamente.
- Cada um utilizava aproximadamente 1,2 GB de memória residente. Isso demonstra atividade desses processos, mas não comprova avanço deste pedido nem identifica sua etapa.
- A consulta de linhas de comando via `Win32_Process` retornou acesso negado. Não foi possível associar esses processos ao pedido.
- A pasta `pedidos` do caminho informado não estava presente no workspace inspecionado, impedindo conferir a extração, o log e os arquivos de saída correspondentes.
- Nenhum processo foi encerrado e nenhum pedido foi reexecutado durante a avaliação.

As recomendações abaixo são trabalho a realizar; não foram implementadas nesta análise.

## Gargalos identificados no código

### 1. Saída retida até o término do subprocesso

Em `meury_app/batch_order_processor.py`, `run_creator()` usa `subprocess.run()` com captura de stdout e stderr. O log da criação só é escrito após o retorno. Não há timeout configurado nessa chamada.

Em `criar_pedido.py`, o JSON final é impresso somente depois de todo o processamento. Os callbacks de progresso disponíveis não são encaminhados nessa execução.

Consequência: carregar catálogo, resolver caminhos, buscar estampas, copiar arquivos e gerar relatórios aparecem como uma única espera silenciosa.

### 2. Catálogo inteiro carregado novamente a cada pedido

Cada chamada de `run_creator()` inicia um novo Python. `criar_pedido.py` chama `load_index(sources)`, que carrega o JSONL, aplica resultados de análise, combina estado operacional, resolve caminhos e constrói o índice de busca.

Em um lote de cinco pedidos, esse custo se repete até cinco vezes, mesmo que cada pedido use poucas estampas. O carregamento inclui campos que a separação dos arquivos não precisa utilizar.

### 3. Resolução física de caminhos de todo o acervo

`_load_catalog()` chama `resolve_record_path()` para cada registro. Em `meury_app/config.py`, `resolve_relative_image_path()` executa `resolve(strict=False)` tanto na raiz quanto no caminho resultante.

Com 195 mil registros, são aproximadamente 390 mil chamadas de resolução nesse trecho por carregamento. Isso não equivale necessariamente a 390 mil leituras físicas, por causa de caches e diferenças de plataforma, mas pode gerar consultas caras ao sistema de arquivos e ao HD externo.

### 4. Reconstrução automática e silenciosa do índice

Em `criar_pedido.py`, se `load_index()` retornar vazio, o programa chama `build_index()` automaticamente. Um índice ausente, incompatível ou sem registros utilizáveis pode transformar a criação de um pedido em uma indexação completa.

Esse caminho também passa pelos checkpoints completos e pelas leituras de conteúdo descritos em `MELHORIAS_PERFORMANCE_INDICE.md`.

### 5. Busca alternativa percorre todo o índice

Em `meury_app/processor.py`, `exclusive_image_matches()` percorre todas as chaves quando a busca principal não encontra correspondências.

Para K linhas sem correspondência direta e N chaves no catálogo, o custo pode crescer proporcionalmente a K × N. Repetições da mesma estampa repetem esse trabalho.

### 6. Cópia sem progresso por bytes e retomada frágil

`process_excel()` usa `shutil.copy2()` sequencialmente. Um original grande ou uma leitura lenta pode ficar muito tempo sem retorno visível. A mensagem de progresso por linha só ocorre depois do processamento da linha.

O destino recebe diretamente o nome final. Em uma interrupção, pode restar um arquivo parcial; uma nova execução considera `destination.exists()` suficiente para ignorá-lo como já existente.

### 7. Conversões intermediárias dispensáveis

O fluxo JSON passa por linhas CSV e cria um XLSX temporário, que é reaberto para processar o pedido. Isso adiciona serialização, escrita e leitura. Provavelmente é secundário diante do carregamento do catálogo e do HD, mas deve ser medido.

## Melhorias priorizadas

### P0 — Tornar a espera observável e diagnosticável

- [ ] Emitir fases explícitas: carregando catálogo, preparando busca, localizando arquivos, copiando e gerando relatório.
- [ ] Medir duração por fase e registrar PID, pedido, início, fim e último avanço real.
- [ ] Encaminhar progresso em tempo real usando leitura contínua do subprocesso ou worker persistente.
- [ ] Preservar o protocolo do JSON final: usar stderr para progresso ou mensagens estruturadas com tipos distintos, sem quebrar `extract_json()`.
- [ ] Gravar logs durante a execução, com flush, em vez de somente ao terminar.
- [ ] Mostrar arquivo atual, quantidade concluída, bytes copiados, tempo decorrido e taxa de transferência quando disponíveis.
- [ ] Implementar alerta configurável de ausência de avanço; distinguir um heartbeat do processo de progresso efetivo de trabalho.
- [ ] Prever cancelamento seguro. Não encerrar automaticamente uma cópia válida apenas por um timeout total curto.

Aceite: é possível identificar a etapa em andamento antes da primeira cópia. Uma operação sem avanço gera diagnóstico com o último arquivo ou fase conhecida, sem declarar sucesso.

### P1 — Preparar o catálogo uma vez por lote

- [ ] Reutilizar um único índice entre pedidos, em processo persistente ou chamando a função de processamento diretamente pelo executor do lote.
- [ ] Criar uma visão leve para pedidos: chave de busca, origem, caminho relativo e estado necessário à disponibilidade do arquivo.
- [ ] Evitar carregar dados de análise, previews e sincronização que não afetam a seleção das estampas.
- [ ] Definir uma versão consistente do catálogo para o lote e uma política de atualização quando o catálogo mudar.
- [ ] Impedir execuções duplicadas do mesmo pedido ou destino; não presumir que os processos observados nesta análise sejam duplicações.

Aceite: cinco pedidos reutilizam uma preparação do catálogo. Medir separadamente o tempo do primeiro pedido e o dos seguintes, assim como o pico de memória.

### P1 — Resolver apenas os caminhos selecionados

- [ ] Normalizar as raízes uma vez e manter caminhos relativos na estrutura de busca.
- [ ] Fazer resolução física e validação de acesso apenas para os arquivos selecionados para o pedido.
- [ ] Preservar proteções contra caminhos fora da raiz, caminhos absolutos indevidos, `..`, links e junctions que escapem da origem permitida.
- [ ] Validar o arquivo real no momento da cópia, pois o HD pode ser desconectado após a busca.

Aceite: separar dez estampas não exige resolver fisicamente os caminhos de todos os 195 mil arquivos; testes de segurança de caminhos continuam passando.

### P1 — Não reconstruir o acervo implicitamente ao criar pedido

- [ ] Distinguir catálogo ausente, incompatível, inválido e válido sem correspondências.
- [ ] Quando não houver catálogo utilizável, informar claramente a necessidade de atualização.
- [ ] Manter reconstrução como ação explícita, com progresso próprio; respeitar a opção `--atualizar-indice` quando solicitada.
- [ ] Não interpretar uma estampa não encontrada como motivo para reindexar automaticamente todo o HD.

Aceite: a criação normal não chama `build_index()` silenciosamente.

### P1 — Cópia segura com progresso e retomada

- [ ] Copiar para arquivo temporário exclusivo no diretório de destino e promover ao nome final somente após conclusão e verificações definidas.
- [ ] Mostrar avanço por bytes em cópias grandes, limitando a frequência de mensagens para não prejudicar a velocidade.
- [ ] Conferir tamanho esperado e detectar mudança da origem durante a cópia; aplicar hash quando a política de integridade exigir, medindo o custo adicional.
- [ ] Registrar conclusão em manifesto de retomada. Não considerar mera existência como prova de cópia íntegra; tamanho igual, isoladamente, também não comprova identidade.
- [ ] Tratar conflitos com arquivos preexistentes sem sobrescrevê-los silenciosamente.
- [ ] Preservar os metadados atualmente mantidos por `copy2()` quando necessários.
- [ ] Registrar erros por arquivo e definir claramente quando continuar ou interromper o pedido.
- [ ] Só registrar sucesso e mover o PDF para concluídos após confirmar o resultado completo.

Aceite: cancelar e retomar não faz um arquivo parcial ser aceito como concluído. Desconexões e falta de espaço produzem erro identificável.

### P2 — Indexar a busca alternativa

- [ ] Preparar uma estrutura auxiliar de busca por nome, reutilizada durante todo o lote.
- [ ] Preservar as regras atuais de nome exato e prefixos delimitados por espaço ou ponto.
- [ ] Memorizar resultados para estampas e variantes repetidas na mesma versão do catálogo.
- [ ] Preservar a distinção entre múltiplos arquivos exclusivos válidos e duplicidades que impedem cópia.

Aceite: a busca alternativa não percorre o acervo inteiro a cada linha, e retorna os mesmos arquivos do comportamento atual nos casos de regressão.

### P2 — Remover o XLSX temporário do processamento JSON

- [ ] Extrair um núcleo que processe registros normalizados diretamente.
- [ ] Fazer JSON, CSV e Excel alimentarem esse núcleo, mantendo compatibilidade.
- [ ] Gerar apenas os relatórios finais necessários.

Aceite: entradas equivalentes produzem as mesmas pastas, arquivos, contagens e relatórios, sem criar XLSX intermediário para pedidos JSON.

### P2 — Ajustar a transferência ao HD externo

- [ ] Medir bytes totais e taxa efetiva de transferência, identificando se origem e destino compartilham o mesmo HD.
- [ ] Começar com cópia sequencial e testar concorrência pequena apenas se houver ganho demonstrado. Muitas leituras paralelas podem piorar o acesso em HD mecânico.
- [ ] Avaliar catálogo e logs no SSD interno, quando disponível.
- [ ] Considerar destino no SSD interno somente se compatível com a organização desejada pelo usuário.

Aceite: a configuração escolhida melhora o tempo real sem comprometer integridade, memória ou responsividade.

## Plano de validação

Usar cópias de teste dos pedidos e destinos separados. Não reexecutar pedidos de produção apenas para medir, evitando cópias e conclusões duplicadas.

| Cenário | Verificação |
| --- | --- |
| Primeiro pedido com catálogo de 195 mil arquivos | Tempo de carregamento e resolução separado do tempo de cópia |
| Lote de cinco pedidos | Apenas uma preparação do catálogo; resultado correto em cada pedido |
| Estampas ausentes e exclusivas | Busca rápida, resultados equivalentes e nenhum scan completo automático |
| Originais grandes | Progresso em bytes e distinção entre transferência lenta e ausência de avanço |
| Catálogo ausente ou inválido | Erro claro ou atualização explicitamente solicitada |
| Interrupção durante cópia | Arquivo parcial não é publicado nem aceito como concluído |
| Destino preexistente ou concorrente | Sem sobrescrita silenciosa ou sucesso indevido |
| HD desconectado ou destino sem espaço | Erro rastreável; PDF não é marcado como concluído |
| Caminho fora da raiz ou junction externa | Proteções de caminho preservadas |
| JSON, CSV e Excel equivalentes | Mesmas regras de negócio e relatórios |

Registrar tempo total, preparação, busca, cópia, relatórios, bytes transferidos e pico de memória. Comparar execuções com cache frio e quente separadamente. Testar recuperação e correção funcional além de medir velocidade.

## Expectativa de desempenho

A meta de menos de um minuto para atualizar o índice não é automaticamente uma meta para copiar um pedido. O tempo de transferência depende do volume de originais e da velocidade efetiva dos discos.

Como referência aritmética, 10 GB a 100 MB/s exigem aproximadamente 100 segundos somente de transferência, antes de outras etapas. Esses números são ilustrativos, não uma medição do equipamento.

Prioridade de entrega: **progresso e medição → catálogo reutilizado e caminhos sob demanda → remoção da reconstrução implícita → cópia segura → busca alternativa e simplificação das conversões**. A cópia segura deve estar pronta antes de introduzir encerramento automático de processos.

Documento relacionado: [Melhorias de performance do índice](MELHORIAS_PERFORMANCE_INDICE.md).
