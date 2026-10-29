"""Persistência, autenticação e permissões de usuários locais."""
from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3

_ITERATIONS = 600_000
PERMISSOES_ABAS = (
    ('mapear_estampas', 'Mapear estampas'),
    ('separar_estampas', 'Separar estampas'),
    ('verificar_existencia', 'Verificador de existência'),
    ('copiar_imagens', 'Copiar imagens'),
    ('compactar_psds', 'Compactação de PSDs'),
    ('separar_pedidos', 'Separar pedidos'),
)
PERMISSOES_DISPONIVEIS = (('admin', 'Administrador — acesso total (inclui Usuários)'),) + PERMISSOES_ABAS


def ensure_user_schema(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT NOT NULL COLLATE NOCASE UNIQUE
                CHECK(length(trim(usuario)) > 0),
            senha_hash TEXT NOT NULL CHECK(length(senha_hash) > 0),
            permissoes TEXT NOT NULL DEFAULT '[]'
                CHECK(CASE WHEN json_valid(permissoes)
                      THEN json_type(permissoes) = 'array' ELSE 0 END),
            criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # SQLite não possui tipo array. Valide também os elementos nas escritas SQL.
    for operation in ("INSERT", "UPDATE"):
        connection.execute(f"""
            CREATE TRIGGER IF NOT EXISTS usuarios_permissoes_{operation.lower()}
            BEFORE {operation} ON usuarios
            WHEN json_valid(NEW.permissoes)
            BEGIN
                SELECT RAISE(ABORT, 'Permissões devem ser strings não vazias')
                WHERE EXISTS (
                    SELECT 1 FROM json_each(NEW.permissoes)
                    WHERE type != 'text' OR length(trim(value)) = 0
                );
            END
        """)


def criar_usuario(db_path: Path, usuario: str, senha: str,
                  permissoes: list[str] | None = None, *,
                  administrador_id: int | None = None) -> int:
    """Cria usuário com salt aleatório; nomes são únicos sem distinguir ASCII case."""
    if not isinstance(usuario, str) or not usuario.strip():
        raise ValueError('Usuário deve ser preenchido')
    if not isinstance(senha, str) or not 8 <= len(senha) <= 1024:
        raise ValueError('Senha deve ter entre 8 e 1024 caracteres')
    if permissoes is None:
        permissoes = []
    if not isinstance(permissoes, list) or any(
        not isinstance(item, str) or not item.strip() for item in permissoes
    ):
        raise ValueError('Permissões devem ser uma lista de strings não vazias')
    encoded = _hash_senha(senha)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path, timeout=30)) as connection, connection:
        ensure_user_schema(connection)
        if administrador_id is not None:
            # Serializa a autorização e a inserção com outras escritas no banco.
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                'SELECT permissoes FROM usuarios WHERE id=?', (administrador_id,),
            ).fetchone()
            if row is None or 'admin' not in json.loads(row[0]):
                raise PermissionError('Somente administradores podem cadastrar usuários')
        cursor = connection.execute(
            'INSERT INTO usuarios (usuario, senha_hash, permissoes) VALUES (?, ?, ?)',
            (usuario.strip(), encoded, json.dumps(list(dict.fromkeys(permissoes)))),
        )
        return int(cursor.lastrowid)


def verificar_senha(senha: str, senha_hash: str) -> bool:
    """Compara senha sem armazenar ou registrar seu texto original."""
    if not isinstance(senha, str) or len(senha) > 1024:
        return False
    try:
        algorithm, iterations, salt, expected = senha_hash.split('$')
        if algorithm != 'pbkdf2_sha256' or int(iterations) != _ITERATIONS:
            return False
        salt_bytes = bytes.fromhex(salt)
        expected_bytes = bytes.fromhex(expected)
        if len(salt_bytes) != 16 or len(expected_bytes) != 32:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', senha.encode('utf-8'), salt_bytes, int(iterations))
        return hmac.compare_digest(actual, expected_bytes)
    except (ValueError, TypeError, AttributeError):
        return False


def tem_permissao(permissoes: list[str], permissao: str) -> bool:
    """A permissão admin concede acesso a qualquer operação."""
    return 'admin' in permissoes or permissao in permissoes


def autenticar_usuario(db_path: Path, usuario: str, senha: str) -> dict | None:
    """Retorna apenas identidade e permissões; credenciais nunca saem do store."""
    if not isinstance(usuario, str) or not usuario.strip():
        return None
    with closing(sqlite3.connect(db_path, timeout=30)) as connection, connection:
        ensure_user_schema(connection)
        row = connection.execute(
            'SELECT id, usuario, senha_hash, permissoes FROM usuarios WHERE usuario=?',
            (usuario.strip(),),
        ).fetchone()
    # Mesmo custo criptográfico para usuário inexistente, sem expor sua existência.
    encoded = row[2] if row else (
        f'pbkdf2_sha256${_ITERATIONS}$' + '00' * 16 + '$' + '00' * 32
    )
    valid = verificar_senha(senha, encoded)
    if row is None or not valid:
        return None
    permissions = json.loads(row[3])
    if not isinstance(permissions, list) or any(not isinstance(p, str) for p in permissions):
        return None
    return {'id': row[0], 'usuario': row[1], 'permissoes': permissions}


def cadastrar_usuario(db_path: Path, administrador_id: int, usuario: str,
                      senha: str, permissoes: list[str]) -> int:
    """Cadastro administrativo: consulta a permissão atual no banco ao gravar."""
    if type(administrador_id) is not int:
        raise PermissionError('Sessão de administrador necessária')
    allowed = {key for key, _ in PERMISSOES_DISPONIVEIS}
    if not isinstance(permissoes, list) or any(
        not isinstance(item, str) or item not in allowed for item in permissoes
    ):
        raise ValueError('Permissão desconhecida')
    return criar_usuario(db_path, usuario, senha, permissoes,
                         administrador_id=administrador_id)


def pode_acessar_aba(identity: dict | None, permissao: str) -> bool:
    """Nega por padrão; somente admin acessa a gestão de usuários."""
    if not identity:
        return False
    permissions = identity.get('permissoes', [])
    if not isinstance(permissions, list):
        return False
    if permissao == 'usuarios':
        return 'admin' in permissions
    if permissao not in dict(PERMISSOES_ABAS):
        return False
    return tem_permissao(permissions, permissao)


def _hash_senha(senha: str) -> str:
    if not isinstance(senha, str) or not 8 <= len(senha) <= 1024:
        raise ValueError('Senha deve ter entre 8 e 1024 caracteres')
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', senha.encode('utf-8'), salt, _ITERATIONS)
    return f'pbkdf2_sha256${_ITERATIONS}${salt.hex()}${digest.hex()}'


@contextmanager
def _admin_connection(db_path: Path, administrador_id: int):
    if type(administrador_id) is not int:
        raise PermissionError('Sessão de administrador necessária')
    with closing(sqlite3.connect(db_path, timeout=30)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT permissoes FROM usuarios WHERE id=?',
                                 (administrador_id,)).fetchone()
        if row is None or 'admin' not in json.loads(row[0]):
            raise PermissionError('Somente administradores podem gerenciar usuários')
        yield connection


def listar_usuarios(db_path: Path, administrador_id: int) -> list[dict]:
    with _admin_connection(db_path, administrador_id) as connection:
        rows = connection.execute(
            'SELECT id, usuario, permissoes, criado_em FROM usuarios ORDER BY usuario COLLATE NOCASE'
        ).fetchall()
    return [dict(id=row[0], usuario=row[1], permissoes=json.loads(row[2]), criado_em=row[3])
            for row in rows]


def atualizar_usuario(db_path: Path, administrador_id: int, usuario_id: int,
                      usuario: str, permissoes: list[str], senha: str | None = None) -> None:
    if not isinstance(usuario, str) or not usuario.strip():
        raise ValueError('Usuário deve ser preenchido')
    allowed = {key for key, _ in PERMISSOES_DISPONIVEIS}
    if not isinstance(permissoes, list) or any(
        not isinstance(p, str) or p not in allowed for p in permissoes
    ):
        raise ValueError('Permissão desconhecida')
    encoded = _hash_senha(senha) if senha is not None else None
    with _admin_connection(db_path, administrador_id) as connection:
        if connection.execute('SELECT 1 FROM usuarios WHERE id=?', (usuario_id,)).fetchone() is None:
            raise ValueError('Usuário não encontrado')
        if usuario_id == administrador_id and 'admin' not in permissoes:
            raise ValueError('Você não pode remover sua própria permissão de administrador')
        connection.execute(
            'UPDATE usuarios SET usuario=?, permissoes=?, senha_hash=COALESCE(?, senha_hash) WHERE id=?',
            (usuario.strip(), json.dumps(list(dict.fromkeys(permissoes))), encoded, usuario_id),
        )


def excluir_usuario(db_path: Path, administrador_id: int, usuario_id: int) -> None:
    with _admin_connection(db_path, administrador_id) as connection:
        if usuario_id == administrador_id:
            raise ValueError('Você não pode excluir o usuário conectado')
        cursor = connection.execute('DELETE FROM usuarios WHERE id=?', (usuario_id,))
        if cursor.rowcount != 1:
            raise ValueError('Usuário não encontrado')


def exigir_permissao(db_path: Path, usuario_id: int, permissao: str) -> None:
    if type(usuario_id) is not int:
        raise PermissionError('Sessão autenticada necessária')
    with closing(sqlite3.connect(db_path, timeout=30)) as connection:
        row = connection.execute('SELECT permissoes FROM usuarios WHERE id=?', (usuario_id,)).fetchone()
    if row is None or not pode_acessar_aba({'permissoes': json.loads(row[0])}, permissao):
        raise PermissionError('Seu usuário não possui permissão para separar pedidos')
