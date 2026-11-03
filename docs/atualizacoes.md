# Distribuição e atualização automática

O mecanismo está desativado até configurar `assets/update-trust.json`. Nenhuma chave
privada, token, configuração de cliente ou banco pode ser incluído no aplicativo.
A primeira instalação com suporte a atualizações precisa ser distribuída manualmente.

## Configuração do distribuidor

1. Criar um repositório público vazio para binários (por exemplo,
   `marildamarketplace-maker/mv-image-releases`). O código pode continuar privado.
2. Gerar uma chave Ed25519 em uma máquina de publicação confiável. Guardar a chave
   privada em cofre; exportar a pública em formato Raw, codificada em base64.
3. No ambiente protegido `release` do GitHub, definir as variáveis:
   `DISTRIBUTION_REPOSITORY` (`owner/repo`), `UPDATE_PUBLIC_KEY` (Raw/base64),
   `UPDATE_MANIFEST_URL` (`https://github.com/owner/repo/releases/latest/download/update.json`).
4. Definir os segredos: `DISTRIBUTION_TOKEN` com escrita somente no repositório de
   distribuição, `UPDATE_PRIVATE_KEY_BASE64` (PEM/base64). Não cadastrar segredos como variáveis
   públicas. O fluxo gratuito não usa certificado Authenticode do Windows.
5. Para builds locais de distribuição, preencher também `assets/update-trust.json`
   com a mesma URL e chave pública. A chave privada permanece fora do repositório.

## Lançamento

- Enviar mudanças por branch/PR para `main`, atualizando `RELEASE_NOTES.md`.
- Ao integrar na `main`, `Build signed release candidates` roda automaticamente.
  Também pode ser disparado manualmente na `main`.
- O número da versão embutida é `MAJOR.MINOR.GITHUB_RUN_NUMBER` (por exemplo,
  `1.0.1` na primeira execução). Os dois primeiros números vêm de
  `meury_app.__version__`; o patch é atribuído somente no CI. Reexecuções da mesma
  execução mantêm a versão. Uma execução manual nova recebe um novo número.
- O workflow testa, empacota Windows x64, assina os metadados com Ed25519 e publica
  um **rascunho**. Uma reexecução atualiza somente rascunhos, nunca versões públicas.
- Os executáveis não têm assinatura Authenticode: o Windows pode mostrar avisos
  de editor desconhecido ou SmartScreen. A assinatura Ed25519 e os hashes continuam
  obrigatórios antes de instalar atualizações; nenhuma proteção do Windows é desativada.
- O build local é `bash build_macos.sh` ou `build_windows.bat`; estes geram o app,
  o helper e o instalador inicial, mas não executam assinatura de produção.
- Validar o ciclo N → N+1 e a recuperação nas máquinas reais antes de publicar
  manualmente o rascunho como release estável. O arquivo `update.json` só se torna
  acessível no endpoint `latest` quando a release completa for publicada.
- O SHA-256 é calculado sobre o ZIP final antes de assinar os metadados. Eles
  contêm somente o pacote Windows x64. Nenhum pacote ou certificado Apple é exigido.
- Distribuir `Install MV Image.exe` junto à pasta `MV Image` extraída do ZIP Windows.
  A instalação inicial cria o atalho no menu Iniciar.

O fluxo de atualização publicado atende **somente Windows x64**. A verificação de
atualizações no macOS fica inativa e a consulta manual informa essa limitação.
O código de instalação macOS é preservado para uma ativação futura. O build local
macOS continua sendo exigido pelo `AGENTS.md` para validar alterações no projeto;
ele não faz parte da publicação de atualizações.

## Comportamento e recuperação

O app consulta ao iniciar e pelo menu Ajuda; falhas de rede não bloqueiam seu uso.
Downloads só aceitam HTTPS e hosts do GitHub. O helper revalida a assinatura e o
hash, aguarda as instâncias e bloqueia a pasta de dados durante a migração.
Nenhuma tarefa é encerrada à força. Outra instância aberta pode causar timeout;
feche todas as instâncias que usam os mesmos dados e tente novamente.

Um `.env` legado junto ao executável é preservado, com permissão 0600, em
`~/.meury_organizador_estampas/.env`; a inicialização passa a consultar esse local
antes do caminho legado, sem aceitar arquivos do diretório corrente. Conflitos
entre os dois arquivos bloqueiam a instalação até serem resolvidos.

Banco e configurações são copiados antes de trocar o app. Índices e arquivos dos
clientes não são substituídos. A versão anterior é mantida até a interface de
login estar pronta e o banco validado. Após confirmar saúde, o helper libera a
pasta de dados e a nova versão permite login; depois disso não há restauração
automática do banco.

O estado fica em `~/.mv-image-updates/job-*/journal.json`. Se o helper falhar após
substituir os executáveis, a próxima inicialização solicita recuperação pelo helper
preservado. Se a pasta de destino estiver ausente após interrupção entre renomes,
execute `MV Image Updater[.exe]` nessa pasta passando a própria pasta `job-*` como
argumento. Não apague `.previous` ou `data-backup` antes de concluir a recuperação.
Em timeout com o novo processo ainda vivo, o helper não mata o processo nem restaura
dados: encerre as instâncias e repita a recuperação. Diários registram somente
fases da operação; não contêm credenciais.

## Validação pendente de produção

A configuração do repositório público e os testes
N → N+1 em Windows são requisitos de ativação, não substituídos
por testes unitários. Versões antigas sem protocolo de bloqueio não devem executar
simultaneamente sobre a mesma pasta durante a primeira migração.


## Validação proporcional à alteração

- **PR:** `Validar aplicativo Windows` executa a suíte completa (incluindo 7-Zip
  real uma única vez), índice com 5 mil arquivos e uma repetição, previews com seis
  imagens (JPEG/PNG/TIFF) e build Windows. Dependências usam cache do pip.
- **main:** somente `Build signed release candidates` roda automaticamente,
  testando e construindo uma única vez antes de criar o rascunho.
- **Carga completa:** em Actions → Validar aplicativo Windows → Run workflow,
  marcar `full_benchmark`. Isso usa 195 mil arquivos e três repetições.
  Executar ao investigar desempenho ou antes de validar mudanças importantes no
  indexador/armazenamento. Nenhuma carga de 195 mil arquivos roda por padrão.
- Atualizações no mesmo PR cancelam a validação anterior desse PR. A publicação
  na main mantém sua própria fila e não é cancelada durante um envio de release.
