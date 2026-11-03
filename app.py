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
    import sys
    instance_guard = None
    if getattr(sys, 'frozen', False):
        from meury_app.update_install import instance_lock, recover_interrupted
        recover_interrupted()
        import os
        if not os.environ.get('MV_IMAGE_UPDATE_HEALTH'):
            from meury_app.update_install import hold_data_lock
            hold_data_lock()
        instance_guard = instance_lock()
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
    from meury_app.update_ui import UpdateController
    app.update_controller = UpdateController(app)
    app.run()
    if instance_guard is not None:
        instance_guard.release()
    log("Aplicativo encerrado normalmente.")


if __name__ == "__main__":
    main()
