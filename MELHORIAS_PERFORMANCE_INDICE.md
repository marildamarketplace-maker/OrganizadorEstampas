# Melhorias de performance do índice de estampas

## Objetivo

Reduzir o tempo de atualização do índice de um acervo com mais de 195 mil arquivos armazenados em HD externo, buscando concluir em menos de 60 segundos quando não houver alterações ou houver poucas mudanças.

A meta precisa ser validada no HD e no computador utilizados. A primeira indexação, a verificação completa de integridade e atualizações com muitos arquivos grandes não têm garantia de conclusão nesse prazo.

Primeira entrega implementada em 16/09/2026: instrumentação, checkpoints incrementais recuperáveis, atualização sem regravação quando nada mudou e reaproveitamento de metadados. As tarefas marcadas abaixo estão concluídas; as demais continuam pendentes.

## Diagnóstico anterior à implementação

- Em `meury_app/indexer.py`, `checkpoint_if_needed()` monta um snapshot do catálogo a cada 1.000 arquivos verificados. As funções de indexação passam esse snapshot para `_write_catalog()`, que serializa e grava todos os registros, inclusive em atualizações sem mudanças.
- Para 195 mil arquivos já cadastrados, isso pode resultar em aproximadamente 195 gravações completas durante uma única atualização, além da eventual gravação final.
- Usando como referência o catálogo de 262.549.357 bytes do benchmark existente, seriam aproximadamente 51 GB de escrita lógica nos checkpoints. O volume real depende do tamanho do catálogo. Essas gravações ocorrem no disco onde o catálogo está salvo, que precisa ser identificado; não necessariamente no HD das estampas.
- A enumeração usa `os.scandir()`, mas descarta o `DirEntry` e depois consulta `path.stat()` para cada arquivo.
- Arquivos novos e alterados passam por validação de conteúdo e cálculo de SHA-256, exigindo leitura dos originais.
- O catálogo JSONL é carregado integralmente e combinado com os dados operacionais. Quando existem registros modificados, o JSONL inteiro é gravado novamente.

O relatório `reports/index-benchmark-macos-195k.json` registra 16,4 segundos sem alterações e 17,6 segundos com poucas mudanças. Ele usa imagens JPEG de 32 × 32 pixels em disco local no macOS. É uma referência histórica, não uma validação do código atual nem do desempenho no HD externo.

## Plano priorizado

### P0 — Medir o fluxo real

- [x] Registrar duração por etapa: carregamento do catálogo, aplicação de estado operacional, resolução de caminhos, enumeração e metadados, validação, hashing, checkpoints, conciliação e gravação final.
- [x] Registrar contadores de arquivos novos, alterados, inalterados, ausentes, hashes calculados, checkpoints e bytes gravados no catálogo e no diário.
- [x] Identificar onde ficam catálogo, banco operacional e arquivos temporários.
- [ ] Medir no HD externo real, anotando conexão USB, quantidade de pastas, tamanho dos arquivos e uso de memória.
- [ ] Distinguir execução após conexão/inicialização do disco de execuções repetidas com cache do sistema operacional.

Critério de aceite: um relatório permite identificar quais etapas consomem os 60 segundos, incluindo a persistência necessária para concluir a atualização.

### P1 — Substituir checkpoints completos por persistência incremental

- [x] Remover a serialização de todo o catálogo a cada 1.000 arquivos.
- [x] Persistir apenas alterações em diário separado ou transações incrementais, com política de lote e intervalo de tempo.
- [x] Não regravar o catálogo quando não houver mudanças persistentes na atualização incremental.
- [x] Consolidar o catálogo ao finalizar, somente quando necessário na atualização incremental, usando substituição atômica.
- [x] Implementar recuperação do diário após interrupção, sem repetir alterações já aplicadas.
- [x] Manter o catálogo anterior válido até a conclusão da nova versão.
- [x] Não marcar arquivos como ausentes com base em uma varredura incompleta ou interrompida.

Critério de aceite: uma atualização sem alterações não regrava o JSONL; uma atualização com poucas mudanças não produz centenas de gravações completas. Interromper e retomar não perde metadados nem gera ausências falsas.

### P1 — Manter os dados de trabalho no disco interno

- [x] Conferir a configuração atual antes de alterar caminhos.
- [ ] Se estiverem no HD externo, permitir mover catálogo, banco operacional e diário para o SSD interno, quando disponível.
- [ ] Preservar caminhos relativos e o vínculo com as raízes configuradas, inclusive quando a letra da unidade mudar.
- [ ] Fazer migração com cópia, validação e possibilidade de retorno; preservar os dados anteriores até confirmar a integridade.

Critério de aceite: os originais continuam no HD externo e o catálogo permanece utilizável após reiniciar o aplicativo e reconectar a unidade.

### P1 — Reduzir consultas de metadados ao HD externo

- [x] Transportar os metadados de `DirEntry.stat()` da enumeração até a comparação dos registros, aproveitando o cache disponível na plataforma.
- [x] Evitar consultas redundantes de existência e resolução física de caminhos por arquivo durante a atualização do índice.
- [x] Preservar o tratamento de permissões, entradas inválidas, links e raízes sobrepostas. Falhas de acesso agora abortam a atualização antes de publicar ausências.
- [ ] Medir o ganho no Windows e no HD real antes de aumentar o paralelismo.

Critério de aceite: a varredura reutiliza os metadados disponíveis, preservando a detecção de arquivos novos, alterados e ausentes. Arquivos inalterados não têm seu conteúdo aberto.

### P2 — Separar atualização rápida de verificação de conteúdo

- [ ] Disponibilizar novos registros após a coleta de caminho, tamanho e data, com estado explícito de verificação pendente.
- [ ] Executar validação de conteúdo e SHA-256 em fila persistente, retomável e com concorrência limitada.
- [ ] Invalidar resultados derivados quando o arquivo mudar, sem reutilizar como atual uma prévia ou análise desatualizada.
- [ ] Confirmar movimentos automaticamente somente quando houver evidência suficiente, preservando a regra de hash inequívoco.
- [ ] Revalidar os metadados antes e depois da leitura para detectar alterações durante o processamento.
- [ ] Mostrar separadamente “índice atualizado” e “verificação de conteúdo concluída”.

Critério de aceite: o índice pode ficar disponível antes da leitura integral dos originais, sem apresentar verificações pendentes como concluídas. Medir e informar os tempos das duas etapas separadamente.

### P2 — Avaliar SQLite como catálogo principal

Executar esta etapa se as medições ainda apontarem custo relevante de carregamento ou gravação do JSONL após as correções prioritárias.

- [ ] Avaliar a integração com o banco operacional existente para evitar duas fontes conflitantes de estado.
- [ ] Consultar inicialmente apenas os campos necessários à varredura.
- [ ] Gravar somente registros modificados, em transações por lote.
- [ ] Criar índices de consulta adequados à identidade do arquivo e às buscas usadas pelo aplicativo.
- [ ] Planejar migração versionada, validação de contagens e metadados, backup e recuperação.
- [ ] Manter exportação JSONL quando necessária para compatibilidade.

Critério de aceite: atualizar poucos arquivos não exige reserializar o acervo inteiro; buscas, pedidos e metadados existentes continuam corretos.

### P3 — Evitar varredura completa em toda atualização

Considerar apenas se a enumeração do HD continuar ultrapassando a meta.

- [ ] Avaliar monitoramento de mudanças e, quando compatível, mecanismos do sistema de arquivos para recuperar eventos.
- [ ] Persistir eventos e processar somente arquivos ou pastas afetados.
- [ ] Detectar lacunas de monitoramento, desconexões e mudanças realizadas em outro computador.
- [ ] Executar reconciliação completa quando não for possível garantir que todas as mudanças foram observadas.
- [ ] Manter uma opção de conferência completa e definir sua periodicidade.

Não usar apenas a data de modificação da pasta para concluir que todos os arquivos dentro dela estão inalterados.

Critério de aceite: atualizações rápidas não deixam de detectar alterações feitas enquanto o HD esteve desconectado ou o aplicativo esteve fechado.

## Validação

Usar uma cópia controlada do acervo para os testes que modificam arquivos. Não alterar originais de produção para criar cenários de teste.

| Cenário | Resultado esperado |
| --- | --- |
| 195 mil arquivos, sem mudanças | Meta de menos de 60 s; nenhum hash de original e nenhuma regravação integral do catálogo |
| Poucas mudanças, por exemplo 10 inclusões, 10 alterações e 10 exclusões | Contagens corretas; meta de menos de 60 s para a etapa rápida, registrando tamanho dos arquivos e eventual tempo de verificação separado |
| Renomeações e movimentos | Metadados preservados quando a identidade for confirmada; ambiguidades encaminhadas para revisão |
| Muitos originais novos ou grandes | Progresso responsivo e fila retomável; tempo de verificação informado separadamente |
| Interrupção durante gravação ou varredura | Catálogo recuperável, sem perda de metadados ou ausências falsas |
| HD desconectado ou pasta inacessível | Erro explícito, sem concluir que todo o acervo foi removido |
| Reconexão com outra letra de unidade | Caminhos reconstruídos corretamente |
| Atualização após mudanças com aplicativo fechado | Reconciliação detecta as mudanças não observadas |

Executar testes de regressão relacionados a índice JSONL, progresso, identidade de arquivos, diretórios de origem e armazenamento operacional. Adaptar `scripts/benchmark_index.py` para medir as etapas e verificar a ausência de regravação em atualizações sem mudanças.

Repetir as medições e registrar mediana e pior tempo observado, sem misturar resultados de cache frio e quente. Não usar somente a média para declarar cumprimento da meta.

## Ordem recomendada de entrega

1. Instrumentação e medição inicial.
2. Correção dos checkpoints e testes de recuperação.
3. Reaproveitamento de metadados e verificação do local de armazenamento do catálogo.
4. Novo benchmark no HD externo com 195 mil arquivos.
5. Separação da verificação pesada, se necessária para a experiência desejada.
6. Migração para SQLite e monitoramento de mudanças apenas conforme os gargalos restantes.

A primeira entrega deve atacar as gravações repetidas antes de mudanças maiores de arquitetura. O compromisso com menos de 60 segundos depende do resultado medido no equipamento real e do escopo de trabalho incluído nesse prazo.

## Funcionamento implementado

### Diário de candidatos e publicação

`meury_app/index_journal.py` mantém `indice_estampas.scan.jsonl`, separado do catálogo publicado. Ele registra apenas resultados de validação e SHA-256 dos candidatos novos ou alterados, com caminho, tamanho, mtime, ctime e identificação física. Não duplica os dicionários completos do catálogo.

O buffer é descarregado com `flush` e `fsync` a cada 1.000 candidatos ou quando decorrerem cinco segundos e o controle retornar ao loop. Ao sair normalmente ou por uma exceção tratável, os candidatos restantes também são persistidos. Não é um timer independente: uma leitura individual bloqueada pode atrasar esse intervalo. Em uma queda abrupta, o lote ainda em memória pode precisar ser refeito.

Na retomada, todas as origens são enumeradas novamente. Somente resultados com assinatura física e origens compatíveis são reutilizados; a cauda incompleta do diário é descartada. Metadados e movimentos são reconciliados novamente com o catálogo e o estado operacional atuais. O diário não publica ausências nem substitui a lista de arquivos por um scan incompleto.

A gravação final usa arquivo temporário no mesmo diretório e substituição atômica. `indice_estampas.commit.json` identifica a publicação e o lote operacional pendente. Caso o processo termine entre a publicação do JSONL e a transação SQLite, o próximo carregamento conclui essa transação antes de aplicar o estado operacional, evitando que dados antigos sobrescrevam os registros novos. A recuperação é idempotente.

Um lock de processo (`indice_estampas.lock`) evita duas atualizações simultâneas do mesmo catálogo. O sistema operacional libera o lock quando o processo encerra, inclusive abruptamente. O arquivo de lock pode permanecer no diretório e não significa, isoladamente, que existe uma execução ativa.

Na atualização incremental sem mudanças, catálogo e diário de candidatos não são regravados. O resumo operacional e o diagnóstico ainda são atualizados. A reconstrução completa explícita (`build_index`) continua publicando um catálogo completo ao final.

### Acesso aos originais

Arquivos inalterados usam os metadados de `DirEntry`, sem `Path.stat()` adicional por arquivo e sem abertura de conteúdo. O carregamento para scan monta os caminhos textualmente; o carregamento usado pelos consumidores de cópia mantém a resolução física anterior.

Somente candidatos novos ou alterados recebem consultas físicas adicionais para recuperação e verificação de estabilidade antes/depois da leitura. No Windows, isso também obtém a identificação do arquivo que não vem no `DirEntry.stat()`. Um arquivo que muda durante a leitura interrompe a atualização para nova tentativa.

Falhas de enumeração ou de leitura de metadados abortam o scan. A disponibilidade das origens é reconferida antes da conciliação e sua identidade é conferida antes da publicação. Isso não transforma o filesystem em um snapshot: alterações concorrentes depois que um arquivo foi visitado podem exigir outra atualização. Permanece a regra de considerar inalterado um arquivo com mesmo caminho, tamanho e mtime; esta entrega não é uma auditoria integral de conteúdo.

### Diagnóstico

`meury_app/index_diagnostics.py` coleta métricas por execução, sem compartilhar contadores entre threads. Os resultados de `build_index` e `update_index_incremental` incluem o campo `performance`. O relatório também é salvo em `indice_estampas.performance.json`, junto ao catálogo, inclusive quando uma execução falha após obter o lock.

O relatório contém fases, contadores, resumo e caminhos operacionais. Os bytes de catálogo/diário são bytes lógicos da aplicação; não incluem amplificação de escrita do filesystem, tráfego SMB, bytes internos do SQLite nem todas as leituras feitas pelo Pillow. `hash_bytes_read` contabiliza os blocos efetivamente lidos pelo cálculo de hash. Tempos como `load_total` e `scan_total` incluem suas subetapas e não devem ser somados a elas. O tempo total cobre o trabalho e a persistência operacional, excluindo a gravação auxiliar do próprio diagnóstico.

## Armazenamento encontrado e recomendação

Na configuração consultada, sem migrar dados:

- Pasta operacional: `\\MYCLOUDEX2ULTRA\criacao\OrganizadorDeImagens`.
- Catálogo: `indice_estampas.jsonl` nessa pasta de rede.
- Banco operacional: `estado_indexador.sqlite3` nessa mesma pasta.
- Diários, lock, diagnóstico e temporários da publicação: junto ao catálogo, portanto também na rede nessa configuração.
- Seletor de localização: `C:\Users\Users\.meury_organizador_estampas\data_location.json`.
- Temporários gerais do sistema: `C:\Users\Users\AppData\Local\Temp`.

Recomendação: manter catálogo, SQLite e diários em um SSD interno, preservando os originais no HD externo. O aplicativo já oferece “Pasta de dados/configurações → Alterar pasta”; essa alteração deve ser uma operação separada, com processamento parado, backup, validação e reinício. Não basta apontar `MEURY_APP_DATA_PATH` para uma pasta vazia e esperar que os dados sejam migrados: essa variável seleciona a pasta e tem precedência sobre o seletor. Nenhum dado real foi migrado nesta entrega.

## Evidências e reprodução

- `reports/index-before-2000.json`: linha de base anterior à alteração, com 2.000 cabeçalhos PDF sintéticos locais. Atualização sem mudanças: 1,205 s, duas gravações integrais e 5.261.130 bytes de catálogo escritos.
- `reports/index-after-2000.json`: três atualizações sem mudanças, entre 0,342 e 0,361 s, zero gravações do catálogo e zero bytes de diário; três atualizações com 10 inclusões, 10 alterações, 10 movimentos e 10 exclusões, entre 0,548 e 0,561 s, uma gravação final por execução.
- As amostras de 2.000 arquivos têm organização de pastas diferente, cache não controlado e não constituem um ensaio controlado para afirmar um fator exato de aceleração. A eliminação das gravações repetidas foi verificada por contadores e por teste automatizado acima do antigo limite de 1.000 arquivos.
- Testes de recuperação cobrem diário truncado, primeira criação interrompida, invalidação de assinatura, falha de publicação atômica, falha de sincronização SQLite, diretório/arquivo inacessível, alteração durante hashing e concorrência.
- Um teste encerra abruptamente um subprocesso com `os._exit` após o primeiro lote durável. Na retomada, 1.000 candidatos são recuperados e só os 25 restantes são recalculados. Outra atualização confirma que não houve reaplicação de mudanças.

Benchmark isolado, criando somente arquivos temporários:

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_index.py --count 195000 --repeats 3 --output reports/index-after-windows-195k.json
```

O script registra ambiente, localização temporária, fases, memória de pico quando disponível, mediana e pior tempo. O cenário de poucas alterações contém inclusões, alterações, movimentos e exclusões. `--temp-parent` permite escolher onde criar a fixture descartável; não aponta para um acervo existente. Não limpa caches do sistema e não comprova a meta no HD real.

## Pendências desta entrega

- Medir no HD externo real e na localização operacional escolhida, em cache frio e quente, sem modificar originais para fabricar cenários.
- Confirmar tipo de disco, conexão USB, volume/pastas e efeito de manter o SQLite no NAS versus SSD interno.
- Confirmar a meta de 60 segundos no equipamento e acervo reais.
- Validação e SHA-256 continuam síncronos. Separá-los em fila, adotar SQLite como catálogo principal e monitorar mudanças continuam fora desta entrega.
