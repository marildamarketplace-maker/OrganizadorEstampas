from datetime import datetime
import gc
import tkinter as tk

from meury_app.environment import load_local_environment

load_local_environment()

from meury_app.ui import App
from meury_app.config import OPERATIONAL_DB_FILE
from meury_app.login import solicitar_login
from meury_app.app_icon import apply_app_icon


def log(message):
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)

def main():
    log("Iniciando o MV Image...")
    root = tk.Tk()
    apply_app_icon(root)
    identity = solicitar_login(OPERATIONAL_DB_FILE, root=root)
    # Collect login callback cycles before starting any application workers.
    gc.collect()
    if identity is None:
        return
    log("Preparando a janela do aplicativo...")
    app = App(usuario_atual=identity, root=root)
    log("Janela pronta. O aplicativo está em execução.")
    app.run()
    log("Aplicativo encerrado normalmente.")


if __name__ == "__main__":
    main()
