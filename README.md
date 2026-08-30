# Organizador de Estampas — Meury Shop

## Fluxo atual do indexador e sincronizador

O fluxo operacional principal termina ao publicar a estampa com
`processing_status=PENDING` no Supabase:

```text
Diretório raiz → Atualizar índice (scan local) → SQLite
→ Sincronizar pendentes → preview derivado → Cloud → UPSERT Supabase
```

**Atualizar índice** faz somente o scan local incremental. **Sincronizar
pendentes** gera apenas previews ausentes, envia apenas versões ainda não
publicadas e executa UPSERT apenas dos registros pendentes ou alterados. Repetir
qualquer uma dessas ações é seguro e não duplica uploads ou registros.

As imagens originais são abertas somente para leitura. Previews ficam em
`~/.meury_organizador_estampas/previews`; nenhuma etapa do fluxo modifica, converte,
move, comprime, sobrescreve ou exclui automaticamente uma imagem original.

A pasta de dados pode ser alterada em **Organizar pedidos → Pasta de
dados/configurações**. O aplicativo copia a configuração, o SQLite, os índices e
os previews para uma pasta vazia e conclui a troca após ser reiniciado. Como
alternativa administrada, defina `MEURY_APP_DATA_PATH` no `.env`.

### Configuração obrigatória

Defina as variáveis no arquivo `.env` na raiz do projeto (ou no ambiente que
inicia o aplicativo). O `.env` é carregado automaticamente no Windows e macOS,
sem substituir variáveis já definidas pelo sistema. O diretório raiz também pode
ser escolhido na interface e fica salvo na configuração local.

```text
ORIGINAL_IMAGES_PATH=/Volumes/Estampas

CLOUD_BUCKET=nome-do-bucket
CLOUD_ACCESS_KEY_ID=...
CLOUD_SECRET_ACCESS_KEY=...
CLOUD_PUBLIC_BASE_URL=https://cdn.exemplo.com
CLOUD_ENDPOINT_URL=https://endpoint-s3-opcional
CLOUD_REGION=us-east-1

# Alternativa recomendada ao bloco CLOUD_* acima:
GOOGLE_CLOUD_STORAGE_BUCKET=nome-do-bucket
GOOGLE_CLOUD_CLIENT_EMAIL=indexador@projeto.iam.gserviceaccount.com
GOOGLE_CLOUD_PRIVATE_KEY=-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n
GOOGLE_CLOUD_PROJECT_ID=projeto
GOOGLE_CLOUD_PUBLIC_BASE_URL=https://storage.googleapis.com/nome-do-bucket

SUPABASE_URL=https://projeto.supabase.co
SUPABASE_KEY=chave-restrita-do-indexador
SUPABASE_TABLE=estampas
```

Quando qualquer variável `GOOGLE_CLOUD_*` de autenticação estiver presente, o
aplicativo seleciona GCS e valida o conjunto completo. `GOOGLE_CLOUD_PROJECT_ID`
pode ser inferido de um e-mail padrão de service account. A URL pública é opcional
e assume `https://storage.googleapis.com/BUCKET`; em bucket privado, configure uma
CDN pública ou faça o sistema consumidor gerar URLs assinadas.

No Windows, `ORIGINAL_IMAGES_PATH` pode ser, por exemplo, `D:\ESTAMPAS`. No
macOS, pode ser `/Volumes/Estampas`. O catálogo JSONL, o SQLite e o Supabase
guardam caminhos relativos como `6844/A/6844-A.tif`.

Antes da primeira sincronização, execute em ordem as migrações da pasta
`supabase/migrations`. Não distribua uma chave `service_role` em instaladores ou
computadores sem controle administrativo; prefira uma credencial restrita ao
UPSERT dos campos pertencentes ao indexador.

Cada arquivo possui identidade remota `codigo + variante + arquivo_id`. O
`arquivo_id` inclui o caminho relativo, permitindo nomes iguais em pastas
diferentes. Assim,
arquivos como `6236-a-1`, `6236-a-modelo` e `6236-a-mockup` são enviados sem
colisão. Seus previews usam chaves GCS distintas no formato
`estampas/6236/A/6236-a-1-<hash-curto>/preview.webp`.

### Teste local do núcleo

```bash
python -m compileall -q app.py meury_app tests
python -W error::ResourceWarning -m unittest discover -s tests
```

O projeto ainda contém telas legadas de pedidos, IA e pesquisa local. Elas não são
chamadas por **Atualizar índice** nem por **Sincronizar pendentes**; o pipeline de
sincronização descrito acima termina no Supabase.

Os builds padrão são operacionais e não empacotam Torch/Transformers nem os
modelos de busca visual, pois a IA pertence ao sistema externo. O código legado
permanece disponível ao executar pelo ambiente de desenvolvimento com as
dependências opcionais de `requirements-visual.txt`.

## Inicialização e recursos opcionais

Use `executar_windows.bat` no Windows ou `executar_macos.command` no macOS. Os
inicializadores trabalham sempre na pasta do projeto, criam a `.venv` quando
necessário e instalam novamente as dependências básicas somente quando
`requirements.txt` mudar ou algum pacote estiver ausente.

A análise de imagens, a busca semântica e a busca de semelhantes usam a API OpenAI.
O aplicativo instala apenas bibliotecas leves e o índice FAISS local; não baixa
modelos visuais grandes. A chave da API é solicitada quando um desses recursos é usado.

Aplicativo para Windows e macOS que:

1. Lê uma planilha Excel.
2. Agrupa os itens pelo ID do cliente e ID do pedido.
3. Procura imagens em `ID_DA_ESTAMPA/ID_DA_ESTAMPA-VARIANTE`.
4. Cria as pastas `ID_DO_CLIENTE/DATA/ID_DO_PEDIDO/BASE` na saída.
5. Copia as imagens localizadas para a pasta do cliente e pedido.
6. Gera relatório em Excel e CSV.

## Copiar imagens por pasta

A aba **Copiar imagens** permite adicionar uma ou mais pastas de entrada,
selecionar os formatos JPG, JPEG, PNG e PDF e definir uma pasta de saída. A busca
percorre todas as subpastas.

Cada arquivo é copiado mantendo somente o nome da pasta imediatamente anterior:

```text
Entrada: design/clientes/7751/7751-A.png
Saída:   saida/7751/7751-A.png
```

Arquivos que já existirem no destino não são sobrescritos. Eles são ignorados e
listados no log da aba ao final do processamento.

## Pesquisar artes

A aba **Pesquisar Artes** consulta nome, caminho, descrição, palavras-chave, cores,
elementos, temas e categoria. A busca ignora acentos e aceita aproximações simples,
como `flor`, `floral` e `flores`, sem exigir correspondência literal completa.

Os 200 resultados mais relevantes são exibidos em uma grade. As miniaturas são
criadas sob demanda em `~/.meury_organizador_estampas/thumbnails` e reutilizadas
enquanto o arquivo original não mudar. Assim, a busca não mantém as imagens originais
em memória. Clique em uma arte para ampliar, consultar os metadados, abrir o arquivo
ou pasta, copiar caminho/nome e editar manualmente suas palavras-chave.

### Busca semântica

A busca semântica complementa o ranking textual. Ela envia somente a descrição e os
metadados da arte para o modelo `text-embedding-3-small`, em lotes, e grava os vetores
de 384 dimensões em um índice FAISS no computador. As imagens do catálogo não são
enviadas durante a criação desse índice.

Na aba **Pesquisar Artes**:

1. Clique em **Atualizar índice semântico** para gerar somente vetores ausentes ou
   cujos metadados mudaram.
2. Marque **Busca semântica** para combinar o ranking conceitual com a busca textual.
3. Use **Reconstruir índice semântico** somente quando quiser recalcular tudo.

Os arquivos ficam em `~/.meury_organizador_estampas/indice_semantico.faiss` e
`indice_semantico.jsonl`. O JSONL associa cada ID vetorial à origem e ao caminho
relativo da imagem. Movimentos e renomeações reutilizam o embedding quando o conteúdo
semântico continua igual. Imagens ainda sem descrição ou metadados de análise não são
incluídas até receberem conteúdo pesquisável.

### Encontrar imagens semelhantes por conteúdo

Para uma imagem externa, o aplicativo envia somente essa imagem ao GPT para obter
descrição, cores, elementos, temas e categoria. Depois pesquisa esses dados no mesmo
índice semântico do catálogo. Para uma arte já catalogada, reutiliza seus metadados.
Os vetores e seu mapeamento persistem em:

```text
~/.meury_organizador_estampas/indice_semantico.faiss
~/.meury_organizador_estampas/indice_semantico.jsonl
```

Para pesquisar, clique em **Encontrar imagens semelhantes** e escolha um JPG, JPEG ou
PNG, ou clique com o botão direito sobre uma miniatura e selecione **Encontrar
semelhantes**. Os resultados aparecem na mesma grade, em ordem decrescente, com o
percentual de similaridade. A própria arte selecionada é omitida dos resultados.

Essa busca compara o significado e os elementos identificados, não os pixels. Por
isso, encontra artes com tema e composição parecidos, mas não é um detector exato de
arquivos duplicados.

### Escala, cache e diagnóstico

O aplicativo foi preparado para catálogos da ordem de 195 mil imagens:

- a inicialização valida apenas o cabeçalho do JSONL; o catálogo completo é carregado
  sob demanda e sempre fora da thread da interface;
- a atualização incremental percorre o HD em fluxo e reutiliza os registros antigos,
  evitando manter dois catálogos completos independentes em RAM;
- IA e embeddings trabalham em lotes, nunca com o acervo inteiro de imagens aberto;
- imagens enviadas para análise são reduzidas antes da chamada à API;
- miniaturas são persistentes e invalidadas automaticamente quando o arquivo muda;
- JSONL e índices FAISS são substituídos atomicamente, reduzindo o risco de índice
  corrompido após uma interrupção.

A área **Estatísticas do catálogo** é atualizada em segundo plano e informa total de
imagens, presença/ausência de palavras-chave, embeddings semânticos, pendências e
erros. O contador de embeddings lê apenas o mapeamento JSONL e não carrega o FAISS ou
o modelo na memória.

Os índices FAISS atuais usam busca exata (`IndexFlatIP`), adequada para máxima
qualidade e pesquisa rápida nessa escala. Como referência, 195 mil vetores semânticos
de 384 dimensões ocupam cerca de 286 MiB, além dos metadados. Eles são carregados
somente quando a funcionalidade é usada. Se o acervo crescer muito além disso ou a RAM disponível for limitada, a troca
para HNSW/IVF pode ser feita sem mudar o catálogo JSONL.

## Estrutura esperada das estampas

Selecione um diretório raiz. Ele pode ter quantas subpastas forem necessárias:

```text
Estampas/
├── MV/
│   ├── 6652 NATAL CORRIDAS/
│   │   ├── 6652-A.jpg
│   │   └── 6652-B.jpg
│   └── 7001/
│       └── 7001-X.jpg
└── CLIENTE1/
    ├── 6652/
    │   └── 6652-A.jpg
    └── 7001/
        └── 7001-X.jpg
```

Cada imagem deve estar na pasta da estampa. A pasta pode conter uma descrição
depois do código; por exemplo, `6162 NATAL CORRIDAS` é identificada como estampa
`6162`. O caminho deve terminar em:

```text
ID_DA_ESTAMPA [DESCRIÇÃO]/ID_DA_ESTAMPA-VARIANTE.extensão
```

Podem existir quantas pastas intermediárias forem necessárias antes da pasta da
estampa. Elas não fazem parte da identificação da imagem.

Exemplos:

```text
6652-A.jpg
6652-B.png
MV27164-W.pdf
```

A comparação ignora letras maiúsculas e minúsculas, mas exige o nome completo correto.
Para a variante A, também é aceito um arquivo sem o sufixo `-A`, como `6652.png`.
Somente arquivos nos formatos `.JPG`, `.JPEG`, `.PNG` e `.PDF` são incluídos no índice.
Os IDs de pedido, cliente, estampa e variante são convertidos automaticamente para
maiúsculas. As pastas e os nomes dos arquivos copiados também saem em maiúsculas.

## Colunas da planilha

Planilha para preencher os pedidos:

- [Abrir planilha modelo no Google Sheets](https://docs.google.com/spreadsheets/d/1qx2X7ndQ31F-HuCkwdf1XcYb1RxI__BqnTZDrkDIhWU/edit?usp=sharing)
- [Baixar planilha modelo em Excel](./planilha_modelo.xlsx)

Obrigatórias:

- `ID do Pedido`
- `Data do Pedido`
- `ID do Cliente`
- `BASE`
- `ID da Estampa`
- `Variante`

Cada item deve ficar em uma linha. Um pedido com seis itens ocupa seis linhas.
A base é convertida para maiúsculas e define a pasta de destino. Exemplo:

```text
CLIENTE1/19-07-2026/PEDIDO2/BASE1/6652-A.JPG
```

A data usa o formato `DD-MM-AAAA` no nome da pasta, pois barras não são permitidas
em nomes de pastas.

## Entrada por texto CSV

Como alternativa ao Excel, selecione `Texto CSV` no aplicativo e cole os pedidos no
campo de texto. São aceitos separadores por ponto e vírgula, vírgula ou tabulação.
O cabeçalho é opcional, mas as colunas devem estar nesta ordem:

```text
ID do Pedido;Data do Pedido;ID do Cliente;BASE;ID da Estampa;Variante
PEDIDO1;18/07/2026;CLIENTE1;BASE1;6652;A
PEDIDO1;18/07/2026;CLIENTE1;BASE1;7001;X
```

## Criação pelo Codex, sem abrir a interface

Depois de extrair o PDF, o Codex pode entregar o objeto JSON diretamente ao comando
`criar_pedido.py`. O comando usa as pastas de estampas e de saída que foram salvas
anteriormente no aplicativo, carrega o índice existente (ou o cria se necessário),
cria a pasta do pedido e copia os arquivos encontrados.

```bash
python criar_pedido.py pedido.json
```

Também é possível informar os caminhos sem usar a configuração da interface:

```bash
python criar_pedido.py pedido.json --origem "/pasta/estampas" --saida "/pasta/pedidos"
```

No JSON, código e nome são combinados com hífens nas pastas. Por exemplo:
`1710-MV-PRINTS-LTDA/05-08-2026/85951917582/1416-TRICOLINE`.
Por isso, `clienteCodigo`, `clienteNome`, `tecidoCodigo` e `tecidoNome` são
obrigatórios.

## Processamento de vários PDFs pelo Codex

1. Coloque os pedidos em `pedidos_pdf/entrada`.
2. Execute `./pedidos_pdf/processar_pedidos_lote.sh`.
3. Consulte a pasta mais recente em `pedidos_pdf/relatorios`.

Cada PDF novo recebe uma execução isolada do prompt. O controle usa o conteúdo do
arquivo, e não apenas o nome: um PDF concluído continua sendo reconhecido se for
renomeado. Os relatórios do lote separam sucessos, falhas e arquivos já processados.
O arquivo `historico_processados.csv` lista todos os pedidos concluídos até o momento.
Qualquer pendência é classificada como falha. Falhas ficam no histórico e são tentadas
novamente na execução seguinte.

## Instalação para testar com Python

## Análise de uma imagem com IA

A análise usa a API OpenAI e nunca inicia um lote sem confirmação. Defina a variável
`OPENAI_API_KEY` para usar o utilitário de linha de comando; na interface, a chave é
solicitada quando necessário e mantida apenas durante a execução do aplicativo.

Teste uma única imagem:

```bash
python analisar_imagem.py "/caminho/para/12345.jpg"
```

O resultado é exibido como JSON com descrição, palavras-chave, cores, elementos,
temas e categoria, sempre sem iniciar a análise das demais imagens.

Na interface, o botão **Gerar Palavras-chave com IA** permite escolher todas as
imagens pendentes ou um lote de teste com 1, 10, 50 ou 100 imagens. Cada
resultado é salvo imediatamente em `resultados_analise_ia.jsonl`; fechar o aplicativo
ou reiniciar o computador não descarta as imagens já concluídas. Ao iniciar novamente,
somente as pendentes são oferecidas. O arquivo `analise_ia.log` registra sucessos,
erros e resumos. Pausar ou cancelar aguarda a imagem atual terminar e ser salva.

### Windows

1. [Baixe e instale o Python 3.12 ou superior](https://www.python.org/downloads/windows/).
2. Marque `Add Python to PATH` durante a instalação.
3. Abra a pasta do projeto no Explorador.
4. Clique na barra de endereço, digite `cmd` e pressione Enter.
5. Execute:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

### macOS

1. [Baixe e instale o Python 3.12 ou superior](https://www.python.org/downloads/macos/).
2. Abra o Terminal dentro da pasta do projeto.
3. Execute:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

## Gerar aplicativo para Windows

Também é possível gerar e baixar o aplicativo automaticamente pela página
[GitHub Actions — Gerar aplicativo Windows](https://github.com/marildamarketplace-maker/OrganizadorEstampas/actions/workflows/build-windows.yml).

Abra a execução mais recente e baixe o artefato `OrganizadorEstampas-Windows`.

A geração do `.exe` deve ser feita em um computador Windows.

```bat
.venv\Scripts\activate
pyinstaller --noconfirm --clean --windowed --name "OrganizadorEstampasMeury" app.py
```

O programa ficará em:

```text
dist\OrganizadorEstampasMeury\OrganizadorEstampasMeury.exe
```

Para distribuir, compacte e envie a pasta inteira `dist\OrganizadorEstampasMeury`.

## Gerar aplicativo para macOS

A geração do `.app` deve ser feita em um Mac.

```bash
source .venv/bin/activate
pyinstaller --noconfirm --clean --windowed --name "OrganizadorEstampasMeury" app.py
```

O aplicativo ficará em:

```text
dist/OrganizadorEstampasMeury.app
```

Na primeira abertura, o macOS pode bloquear o app por não estar assinado. Acesse:

```text
Ajustes do Sistema > Privacidade e Segurança > Abrir Mesmo Assim
```

## Como usar

1. Escolha `Planilha Excel` e clique em `Selecionar Excel`, ou escolha `Texto CSV`
   e cole os pedidos.
2. Clique em `Selecionar raiz` e escolha o diretório raiz das estampas.
3. Escolha a pasta de saída.
4. Clique em `ATUALIZAR ÍNDICE`. A mesma ação faz scans incrementais posteriores e
   identifica arquivos novos, alterados, ausentes, movidos ou renomeados.
5. Clique em `Sincronizar pendentes` para gerar previews, publicar na Cloud e fazer
   UPSERT no Supabase.
6. Se usar o módulo legado de pedidos, clique em `GERAR PASTAS DOS PEDIDOS`.
7. Confira o relatório criado na pasta de saída.

Ao concluir, o log também lista cada imagem não encontrada, incluindo a linha da
planilha, o pedido, o cliente e o caminho pesquisado.

## Regras de segurança

- Arquivos originais nunca são apagados ou movidos.
- O aplicativo apenas copia as imagens.
- Se houver dois arquivos com o mesmo nome, nenhum deles é copiado e o relatório marca `DUPLICADO`.
- Se uma imagem não existir, o relatório marca `NÃO ENCONTRADO`.
- Se o arquivo já existir na pasta do pedido, ele é marcado como `JÁ EXISTE` e não é
  copiado novamente. O aplicativo não cria cópias com `_2`, `_3` etc.

## Cache do índice

O índice fica salvo em uma pasta oculta do usuário:

- Windows: `C:\Users\SEU_USUARIO\.meury_organizador_estampas`
- macOS: `/Users/SEU_USUARIO/.meury_organizador_estampas`

O índice percorre todas as pastas de entrada adicionadas. Se a mesma estampa existir
em mais de uma origem, ela será marcada como `DUPLICADO`.

Ao terminar, o aplicativo informa quantas imagens foram encontradas e quantas
duplicidades existem. Quando houver duplicidades, todos os caminhos conflitantes
serão gravados em `duplicidades_indice.txt`, junto ao cache do índice, para ajuste
manual. As duplicidades não interrompem a criação do índice.

Atualize o índice quando adicionar, remover ou renomear estampas. Após instalar esta
versão, o índice antigo será desconsiderado e deverá ser atualizado uma vez.
