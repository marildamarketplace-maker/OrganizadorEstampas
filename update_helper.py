"""Standalone frozen entry point; never accepts a target path or shell command."""
import sys
import os
import json
from meury_app.environment import load_local_environment

load_local_environment()
from meury_app.update_install import install

if __name__ == '__main__':
    from pathlib import Path
    from meury_app.update_install import state_dir, write_state
    try:
        work = Path(sys.argv[1]).resolve()
        if work.parent != state_dir().resolve() or not work.name.startswith('job-'):
            raise ValueError('Diretório de atualização inválido.')
        context = work / 'context.json'
        if context.exists():
            data = Path(json.loads(context.read_text())['data_dir'])
            if not data.is_absolute():
                raise ValueError('Pasta de dados inválida.')
            os.environ['MEURY_APP_DATA_PATH'] = str(data)
        install(work)
    except Exception as error:
        work = Path(sys.argv[1]).resolve()
        if work.parent == state_dir().resolve() and work.name.startswith('job-'):
            write_state(work / 'error.json', {'phase': 'failed', 'error_type': type(error).__name__})
        raise SystemExit(1) from None
