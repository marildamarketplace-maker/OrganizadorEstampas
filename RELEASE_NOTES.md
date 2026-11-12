# MV Image — atualizações Windows

- Uma única sessão por pasta compartilhada, desde antes do login até o encerramento; tentativas adicionais mostram o nome do usuário autenticado.
- Login sem migração, banco em DELETE/FULL, gravações curtas e preparação exclusiva com backup.
- Reservas por pedido e por saída, relatórios únicos e histórico protegido como defesa adicional.
- Metadados antigos não bloqueiam o uso: a exclusividade depende da trava ativa, não de arquivos de identificação.
- Instalações exigem encerrar o uso do aplicativo; novas sessões não iniciam durante manutenção.

- Correção da abertura do login com banco em caminho de rede Windows (UNC), incluindo testes de regressão e preservação do acesso somente de leitura.

- Cores e negrito nos logs de Separar pedidos, preservando a cópia como texto limpo.

- Atualizações assinadas no Windows, consultadas somente por Ajuda > Buscar atualização.
- Consulta, download e instalação dependem de ações explícitas; nenhuma busca ao abrir o aplicativo.
- Instalação após confirmação, com preservação dos dados e recuperação de falhas.

- Publicação automática em rascunho ao integrar na main, com metadados Ed25519 e sem certificado Windows pago.
