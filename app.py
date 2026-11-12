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
        instance_guard = instance_lock()
    import os
    from meury_app.update_install import hold_data_lock, release_data_lock
    from meury_app.shared_sessions import set_session_user
    import atexit
    atexit.register(release_data_lock)
    if not os.environ.get('MV_IMAGE_UPDATE_HEALTH'):
        try:
            hold_data_lock()
        except Exception as exc:
            from tkinter import messagebox
            messagebox.showerror('Dados compartilhados indisponíveis', str(exc))
            return
    root = tk.Tk()
    apply_app_icon(root)
    try:
        identity = solicitar_login(OPERATIONAL_DB_FILE, root=root)
        if identity is not None:
            set_session_user(OPERATIONAL_DB_FILE.parent, identity)
    except Exception as exc:
        from tkinter import messagebox
        messagebox.showerror('Banco indisponível', str(exc), parent=root)
        root.destroy()
        release_data_lock()
        return
    # Collect login callback cycles before starting any application workers.
    gc.collect()
    if identity is None:
        release_data_lock()
        return
    log("Preparando a janela do aplicativo...")
    app = App(usuario_atual=identity, root=root)
    log("Janela pronta. O aplicativo está em execução.")
    from meury_app.update_ui import UpdateController
    app.update_controller = UpdateController(app)
    app.run()
    if instance_guard is not None:
        instance_guard.release()
    release_data_lock()
    log("Aplicativo encerrado normalmente.")


if __name__ == "__main__":
    main()
