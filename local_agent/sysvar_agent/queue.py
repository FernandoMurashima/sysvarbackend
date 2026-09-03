import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


BACKOFF_SECONDS = [60, 120, 300, 600, 1800]


def utcnow():
    return datetime.now(timezone.utc)


class AgentQueue:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self):
        self.conn.close()

    def _init_schema(self):
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fila_envio (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                configuracao_id INTEGER NOT NULL,
                caminho_local TEXT NOT NULL,
                chave_acesso TEXT NOT NULL UNIQUE,
                tamanho INTEGER NOT NULL,
                mtime REAL NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                retryable INTEGER NOT NULL DEFAULT 1,
                tentativas INTEGER NOT NULL DEFAULT 0,
                ultimo_erro TEXT NOT NULL DEFAULT '',
                proxima_tentativa TEXT,
                criado_em TEXT NOT NULL,
                atualizado_em TEXT NOT NULL
            )
            """
        )
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(fila_envio)").fetchall()}
        if "retryable" not in columns:
            self.conn.execute("ALTER TABLE fila_envio ADD COLUMN retryable INTEGER NOT NULL DEFAULT 1")
        self.conn.commit()

    def enqueue(self, configuracao_id, caminho_local, chave_acesso, tamanho, mtime, payload):
        existing = self.get_by_chave(chave_acesso)
        now = utcnow().isoformat()
        if existing and existing["status"] == "ENVIADO":
            return False
        if existing:
            if int(existing["tamanho"]) == int(tamanho) and float(existing["mtime"]) == float(mtime):
                return False
            self.conn.execute(
                """
                UPDATE fila_envio
                   SET configuracao_id=?, caminho_local=?, tamanho=?, mtime=?, payload_json=?, atualizado_em=?
                 WHERE chave_acesso=?
                """,
                (configuracao_id, caminho_local, tamanho, mtime, json.dumps(payload), now, chave_acesso),
            )
        else:
            self.conn.execute(
                """
                INSERT INTO fila_envio
                (configuracao_id, caminho_local, chave_acesso, tamanho, mtime, payload_json, status, retryable, criado_em, atualizado_em)
                VALUES (?, ?, ?, ?, ?, ?, 'PENDENTE', 1, ?, ?)
                """,
                (configuracao_id, caminho_local, chave_acesso, tamanho, mtime, json.dumps(payload), now, now),
            )
        self.conn.commit()
        return True

    def get_by_chave(self, chave_acesso):
        return self.conn.execute("SELECT * FROM fila_envio WHERE chave_acesso=?", (chave_acesso,)).fetchone()

    def is_sent(self, chave_acesso):
        row = self.get_by_chave(chave_acesso)
        return bool(row and row["status"] == "ENVIADO")

    def due_items(self):
        now = utcnow().isoformat()
        return self.conn.execute(
            """
            SELECT * FROM fila_envio
             WHERE status = 'PENDENTE'
                OR (status = 'ERRO' AND retryable = 1 AND proxima_tentativa IS NOT NULL AND proxima_tentativa <= ?)
             ORDER BY id
            """,
            (now,),
        ).fetchall()

    def mark_sent(self, item_id):
        self.conn.execute(
            "UPDATE fila_envio SET status='ENVIADO', retryable=0, ultimo_erro='', atualizado_em=? WHERE id=?",
            (utcnow().isoformat(), item_id),
        )
        self.conn.commit()

    def mark_error(self, item_id, message, retry=True):
        row = self.conn.execute("SELECT tentativas FROM fila_envio WHERE id=?", (item_id,)).fetchone()
        tentativas = int(row["tentativas"] if row else 0) + 1
        delay = BACKOFF_SECONDS[min(tentativas - 1, len(BACKOFF_SECONDS) - 1)] if retry else None
        if retry:
            proxima = (utcnow() + timedelta(seconds=delay)).isoformat()
        else:
            proxima = None
        self.conn.execute(
            "UPDATE fila_envio SET status='ERRO', retryable=?, tentativas=?, ultimo_erro=?, proxima_tentativa=?, atualizado_em=? WHERE id=?",
            (1 if retry else 0, tentativas, str(message)[:500], proxima, utcnow().isoformat(), item_id),
        )
        self.conn.commit()
        return proxima
