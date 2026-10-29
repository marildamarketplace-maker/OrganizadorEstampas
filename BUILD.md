# Build do aplicativo

Execute `python build_app.py` na raiz do projeto. O script detecta o sistema operacional, prepara o ambiente `.venv` e gera o aplicativo nativo:

- Windows: `dist/MV Image/MV Image.exe`, com ícone `.ico`.
- macOS: `dist/MV Image.app`, com ícone `.icns`.

Os atalhos `build_windows.bat` e `bash build_macos.sh` chamam o mesmo fluxo e também detectam o sistema atual. Não é necessário ativar o ambiente virtual manualmente. Erros interrompem o build e retornam um código de falha.

O build deve ser executado no sistema de destino: Windows gera o executável Windows; macOS gera o aplicativo macOS. Linux não é suportado. Caso a pasta `.venv` tenha sido criada em outro sistema, renomeie-a antes de executar para permitir a criação de um ambiente nativo.
