from datetime import datetime

from meury_app.environment import load_local_environment

load_local_environment()

from meury_app.ui import App


def log(message):
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)

if __name__ == "__main__":
    log("Iniciando o Organizador de Estampas...")
    log("Preparando a janela do aplicativo...")
    app = App()
    log("Janela pronta. O aplicativo está em execução.")
    app.run()
    log("Aplicativo encerrado normalmente.")
