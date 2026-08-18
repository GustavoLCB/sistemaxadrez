"""
Banco de dados do Portal do Torneio de Xadrez Escolar.
Usa SQLite puro (sem ORM) para ficar simples de rodar no PythonAnywhere,
com bloqueio de transação (BEGIN IMMEDIATE) nas escritas críticas para
evitar a condição de corrida que tínhamos na versão em Claude Artifacts.
"""
import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "torneio.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS category_rules (
    id TEXT PRIMARY KEY,
    category_id TEXT NOT NULL REFERENCES categories(id),
    min_age INTEGER NOT NULL,
    max_age INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS groups_t (
    id TEXT PRIMARY KEY,
    category_id TEXT NOT NULL REFERENCES categories(id),
    gender TEXT NOT NULL CHECK(gender IN ('M','F')),
    name TEXT NOT NULL,
    max_rounds INTEGER
);

CREATE TABLE IF NOT EXISTS athletes (
    id TEXT PRIMARY KEY,
    full_name TEXT NOT NULL,
    gender TEXT NOT NULL CHECK(gender IN ('M','F')),
    category_id TEXT REFERENCES categories(id),
    group_id TEXT REFERENCES groups_t(id),
    k_flag TEXT NOT NULL DEFAULT 'iniciante',
    rating INTEGER NOT NULL DEFAULT 1500,
    age INTEGER,
    school TEXT,
    cpf TEXT
);

CREATE TABLE IF NOT EXISTS rounds (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL REFERENCES groups_t(id),
    round_number INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS matches (
    id TEXT PRIMARY KEY,
    round_id TEXT NOT NULL REFERENCES rounds(id),
    group_id TEXT NOT NULL REFERENCES groups_t(id),
    white_id TEXT REFERENCES athletes(id),
    black_id TEXT REFERENCES athletes(id),
    is_bye INTEGER NOT NULL DEFAULT 0,
    bye_athlete_id TEXT REFERENCES athletes(id),
    result TEXT,
    delta_white INTEGER NOT NULL DEFAULT 0,
    delta_black INTEGER NOT NULL DEFAULT 0,
    applied INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tournament_archive (
    id TEXT PRIMARY KEY,
    tournament_name TEXT NOT NULL,
    date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS archive_entries (
    id TEXT PRIMARY KEY,
    archive_id TEXT NOT NULL REFERENCES tournament_archive(id),
    full_name TEXT NOT NULL,
    gender TEXT,
    school TEXT,
    category_name TEXT,
    group_name TEXT,
    k_flag TEXT,
    rating INTEGER,
    points REAL
);

CREATE INDEX IF NOT EXISTS idx_athletes_group ON athletes(group_id);
CREATE INDEX IF NOT EXISTS idx_matches_round ON matches(round_id);
CREATE INDEX IF NOT EXISTS idx_matches_group ON matches(group_id);
CREATE INDEX IF NOT EXISTS idx_rounds_group ON rounds(group_id);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name TEXT,
    role TEXT NOT NULL CHECK(role IN ('admin','fiscal'))
);

CREATE TABLE IF NOT EXISTS user_groups (
    user_id TEXT NOT NULL REFERENCES users(id),
    group_id TEXT NOT NULL REFERENCES groups_t(id),
    PRIMARY KEY(user_id, group_id)
);

CREATE INDEX IF NOT EXISTS idx_user_groups_user ON user_groups(user_id);
"""

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    conn.close()

def _migrate(conn):
    """Ajustes em bancos já existentes (ex: o que já está rodando no PythonAnywhere),
    sem apagar nenhum dado. Roda toda vez que o app inicia; é seguro rodar de novo."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(athletes)").fetchall()]
    if "cpf" not in cols:
        conn.execute("ALTER TABLE athletes ADD COLUMN cpf TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_athletes_cpf ON athletes(cpf) "
        "WHERE cpf IS NOT NULL AND cpf != ''"
    )
    group_cols = [r["name"] for r in conn.execute("PRAGMA table_info(groups_t)").fetchall()]
    if "max_rounds" not in group_cols:
        conn.execute("ALTER TABLE groups_t ADD COLUMN max_rounds INTEGER")

@contextmanager
def write_transaction():
    """
    Transação exclusiva para escritas críticas (resultado de partida, geração de rodada).
    BEGIN IMMEDIATE trava o banco para escrita assim que a transação abre, então se dois
    fiscais tentarem gravar ao mesmo tempo, o segundo espera o primeiro terminar em vez
    de um sobrescrever o outro (o problema que tínhamos no window.storage).
    """
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

@contextmanager
def read_conn():
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()
