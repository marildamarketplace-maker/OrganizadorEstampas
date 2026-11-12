# Um usuário por vez na pasta compartilhada

Os caminhos de banco, catálogo, usuários, configurações, pedidos e imagens permanecem os mesmos. Uma única sessão pode usar a mesma pasta de dados por vez, incluindo login, consultas, índice, separação e execução pelo terminal.

## Funcionamento

- Antes de consultar ou preparar o banco, a sessão adquire acesso exclusivo em `.update-data.lock`. A trava permanece durante todo o uso; não é liberada quando uma tarefa individual termina.
- Após login bem-sucedido, a sessão registra somente o nome cadastrado do usuário, PID e identificador da execução. Não registra senha, hash de senha ou tokens.
- Uma segunda abertura mostra **“Sistema em uso por Maria. Aguarde o usuário fechar o aplicativo para entrar.”** O nome corresponde ao usuário autenticado no MV Image, não ao computador ou usuário do Windows.
- Enquanto ninguém se autenticou na primeira janela, a segunda informa **“Sistema em uso: há uma sessão iniciando ou aguardando login.”** Durante instalação/manutenção, pode aparecer um aviso genérico de indisponibilidade.
- Cancelar o login ou fechar o aplicativo libera a sessão. Em encerramento abrupto, a trava depende do sistema operacional/servidor de arquivos; o código não remove travas com base em idade, PID remoto ou metadados.
- Os arquivos em `.sessions` identificam a sessão ativa, mas sua existência não prova uso. O código consulta a trava de cada identificação e ignora metadados de sessões encerradas.
- Instâncias antigas sem esse protocolo não devem operar durante a implantação. A trava global é compatível com o bloqueio de uso do instalador e das versões anteriores que o mantinham, mas versões antigas não publicam o nome do usuário.

As reservas por pedido, proteção da saída e do histórico permanecem como defesa adicional. Elas não habilitam múltiplas sessões no uso normal do programa.

## Primeira implantação

1. Conclua ou cancele tarefas e feche o MV Image e os iniciadores de terminal em todos os PCs.
2. Distribua manualmente a versão corrigida para todos os PCs antes de retomar o uso.
3. Abra um único PC. Se necessário, ele faz backup SQLite consistente, checkpoint do WAL, conversão para `DELETE/FULL` e preparação do schema sob a trava exclusiva. O backup fica ao lado do banco, com sufixo `.backup-<data>-<UUID>`.
4. Faça login. Nos demais PCs, confirme que o aviso mostra o nome desse usuário e impede abrir o login ou executar tarefas.

Não apague manualmente `-wal`, `-shm` ou arquivos `.lock`. Versões antigas podem reativar WAL. Um banco com schema mais recente continua protegido contra acesso por versões incompatíveis.

## Atualização manual

Ajuda > **Buscar atualização** consulta a distribuição. **Baixar atualização** baixa e valida o pacote assinado. **Atualizar e reiniciar** solicita instalação. Abrir o aplicativo, fechar a janela de atualização ou escolher Mais tarde não dispara download/instalação.

O instalador revalida acesso exclusivo à mesma pasta. Durante instalação, migração ou restauração, novas sessões não podem iniciar. A recuperação de instalação manual interrompida não consulta versões novas.

## Validação obrigatória no SMB real

Testes locais com processos independentes não comprovam que o servidor SMB respeita as mesmas garantias. Antes de liberar:

1. Use uma cópia dos dados, com a mesma configuração de compartilhamento, em dois PCs Windows com a versão corrigida.
2. Abra A e deixe na tela de login. B deve ser bloqueado com aviso de autenticação pendente. Autentique Maria em A; B deve passar a informar Maria, sem abrir login ou acessar o banco.
3. Inicie tarefas em A e tente abrir B pela interface e pelo terminal. Ambos devem ser bloqueados. Feche A normalmente e confirme que B entra; após outro usuário autenticar em B, A deve mostrar o nome desse usuário.
4. Cancele o login e confirme liberação. Encerre um processo de teste abruptamente e verifique quando o servidor libera a sessão. Metadados antigos não devem bloquear ou atribuir o uso ao usuário anterior.
5. Desconecte a rede durante leitura, cópia, publicação e movimentação. Confirme erros explícitos, ausência de sobrescrita e consistência com `PRAGMA integrity_check` após encerrar todos os processos. Não prossiga com duas sessões se o servidor tiver perdido a exclusividade; restabeleça a rede e reabra o aplicativo.
6. Confirme a atualização exclusivamente manual, a exclusividade durante instalação e a recuperação de uma falha de instalação.

Este procedimento ainda precisa ser executado nos PCs Windows e no compartilhamento da operação. Exclusividade reduz a concorrência, mas não elimina falhas de armazenamento, sincronização ou perda de travas do SMB. Backups e sua restauração continuam necessários.
