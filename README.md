# Organizador de Estampas — Meury Shop

Aplicativo para Windows e macOS que:

1. Lê uma planilha Excel.
2. Agrupa os itens pelo ID do cliente e ID do pedido.
3. Procura imagens em `ID_DO_CLIENTE/ID_DA_ESTAMPA/ID_DA_ESTAMPA-VARIANTE`.
4. Cria as pastas `ID_DO_CLIENTE/DATA/ID_DO_PEDIDO/BASE` na saída.
5. Copia as imagens localizadas para a pasta do cliente e pedido.
6. Gera relatório em Excel e CSV.

## Estrutura esperada das estampas

É possível adicionar uma ou várias pastas de entrada. Cada pasta pode ter quantas
subpastas forem necessárias:

```text
Estampas/
├── MV/
│   ├── 6652/
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

Cada imagem deve estar na pasta do cliente e da estampa. O caminho deve ser:

```text
ID_DO_CLIENTE/ID_DA_ESTAMPA/ID_DA_ESTAMPA-VARIANTE.extensão
```

Regra da pasta de origem:

- Se o ID da estampa começar com `MV`, a busca usa a pasta do `ID do Cliente`.
- Se o ID da estampa não começar com `MV`, a busca usa a pasta compartilhada `MV`.

Exemplos: `CLIENTE1/MV5501/MV5501-A.jpg` e `MV/6652/6652-A.jpg`.

Exemplos:

```text
6652-A.jpg
6652-B.png
MV27164-W.jpeg
```

A comparação ignora letras maiúsculas e minúsculas, mas exige o nome completo correto.
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

## Instalação para testar com Python

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

1. Clique em `Selecionar Excel`.
2. Clique em `Adicionar entrada` para cada pasta onde existam estampas.
   Use `Remover selecionada` para retirar uma pasta da lista.
3. Escolha a pasta de saída.
4. Clique em `Atualizar índice`.
5. Após concluir, clique em `GERAR PASTAS DOS PEDIDOS`.
6. Confira o relatório criado na pasta de saída.

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

Atualize o índice quando adicionar, remover ou renomear estampas. Após instalar esta
versão, o índice antigo será desconsiderado e deverá ser atualizado uma vez.
