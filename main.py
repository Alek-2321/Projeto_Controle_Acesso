import os
import sqlite3
import hashlib
import secrets
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()

APP_NAME = os.getenv("APP_NAME", "Sentinel Access")
DATABASE_PATH = os.getenv("DATABASE_PATH", "access_control.db")
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "troque-esta-senha")
RFID_API_KEY = os.getenv("RFID_API_KEY", "troque-esta-chave")

app = FastAPI(
    title=APP_NAME,
    description="API de controle de acesso RFID para o MVP de sala restrita.",
    version="1.0.0",
)
app.mount("/static", StaticFiles(directory="static"), name="static")


sessions: set[str] = set()


def db():
    conn = sqlite3.connect(DATABASE_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS colaboradores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            matricula TEXT NOT NULL UNIQUE,
            tag_rfid TEXT UNIQUE,
            cargo TEXT NOT NULL DEFAULT '',
            tem_acesso INTEGER NOT NULL DEFAULT 0,
            ativo INTEGER NOT NULL DEFAULT 1,
            criado_em TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            colaborador_id INTEGER,
            nome_snapshot TEXT NOT NULL,
            matricula_snapshot TEXT,
            codigo_tag TEXT NOT NULL,
            tipo TEXT NOT NULL,
            resultado TEXT NOT NULL,
            mensagem TEXT NOT NULL,
            ocorrido_em TEXT NOT NULL,
            sincronizado INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(colaborador_id) REFERENCES colaboradores(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS sessoes_sala (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            colaborador_id INTEGER NOT NULL,
            entrada_em TEXT NOT NULL,
            saida_em TEXT,
            FOREIGN KEY(colaborador_id) REFERENCES colaboradores(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_eventos_data ON eventos(ocorrido_em);
        CREATE INDEX IF NOT EXISTS idx_eventos_tipo ON eventos(tipo);
        CREATE INDEX IF NOT EXISTS idx_sessoes_abertas ON sessoes_sala(saida_em);
        """)
      
        count = conn.execute("SELECT COUNT(*) FROM colaboradores").fetchone()[0]
        if count == 0:
            conn.execute("""
                INSERT INTO colaboradores
                (nome, matricula, tag_rfid, cargo, tem_acesso, ativo, criado_em)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("Alex Demonstração", "DEMO001", "A1B2C3D4", "Administrador de projeto", 1, 1, now_iso()))


@app.on_event("startup")
def startup():
    init_db()


def current_admin(authorization: Optional[str] = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Faça login para continuar.")
    token = authorization.removeprefix("Bearer ").strip()
    if token not in sessions:
        raise HTTPException(status_code=401, detail="Sessão inválida ou expirada.")
    return True


def require_rfid_key(x_rfid_api_key: Optional[str] = Header(default=None)):
 
    if RFID_API_KEY and x_rfid_api_key != RFID_API_KEY:
        raise HTTPException(status_code=401, detail="Chave do dispositivo RFID inválida.")
    return True


class LoginInput(BaseModel):
    usuario: str
    senha: str


class ColaboradorInput(BaseModel):
    nome: str = Field(min_length=2, max_length=120)
    matricula: str = Field(min_length=1, max_length=40)
    tag_rfid: Optional[str] = Field(default=None, max_length=100)
    cargo: str = Field(default="", max_length=100)
    tem_acesso: bool = False
    ativo: bool = True


class LeituraRFID(BaseModel):
    codigo_tag: str = Field(min_length=1, max_length=100)
    dispositivo: str = Field(default="Raspberry Pi", max_length=100)


@app.get("/", include_in_schema=False)
def home():
    return FileResponse("static/index.html")


@app.get("/health")
def health():
    return {"status": "ok", "app": APP_NAME, "timestamp": now_iso()}


@app.post("/api/login")
def login(data: LoginInput):
    if data.usuario != ADMIN_USER or hash_password(data.senha) != hash_password(ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Usuário ou senha incorretos.")
    token = secrets.token_urlsafe(32)
    sessions.add(token)
    return {"token": token, "usuario": data.usuario}


@app.post("/api/logout", dependencies=[Depends(current_admin)])
def logout(authorization: Optional[str] = Header(default=None)):
    token = authorization.removeprefix("Bearer ").strip()
    sessions.discard(token)
    return {"ok": True}


@app.get("/api/resumo", dependencies=[Depends(current_admin)])
def resumo():
    with db() as conn:
        pessoas = conn.execute("SELECT COUNT(*) FROM colaboradores WHERE ativo=1").fetchone()[0]
        dentro = conn.execute("""
            SELECT COUNT(*) FROM sessoes_sala WHERE saida_em IS NULL
        """).fetchone()[0]
        hoje = date.today().isoformat()
        entradas = conn.execute("""
            SELECT COUNT(*) FROM eventos
            WHERE tipo='ENTRADA' AND substr(ocorrido_em,1,10)=?
        """, (hoje,)).fetchone()[0]
        negados = conn.execute("""
            SELECT COUNT(*) FROM eventos
            WHERE tipo IN ('ACESSO_NEGADO','TAG_DESCONHECIDA')
              AND substr(ocorrido_em,1,10)=?
        """, (hoje,)).fetchone()[0]
    return {"colaboradores_ativos": pessoas, "dentro_agora": dentro, "entradas_hoje": entradas, "alertas_hoje": negados}


@app.get("/api/colaboradores", dependencies=[Depends(current_admin)])
def listar_colaboradores():
    with db() as conn:
        rows = conn.execute("SELECT * FROM colaboradores ORDER BY nome COLLATE NOCASE").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/colaboradores", dependencies=[Depends(current_admin)])
def criar_colaborador(data: ColaboradorInput):
    tag = data.tag_rfid.strip() if data.tag_rfid else None
    try:
        with db() as conn:
            cur = conn.execute("""
                INSERT INTO colaboradores
                (nome, matricula, tag_rfid, cargo, tem_acesso, ativo, criado_em)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (data.nome.strip(), data.matricula.strip(), tag, data.cargo.strip(),
                  int(data.tem_acesso), int(data.ativo), now_iso()))
            row = conn.execute("SELECT * FROM colaboradores WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Matrícula ou tag RFID já cadastrada.") from exc


@app.put("/api/colaboradores/{colaborador_id}", dependencies=[Depends(current_admin)])
def editar_colaborador(colaborador_id: int, data: ColaboradorInput):
    tag = data.tag_rfid.strip() if data.tag_rfid else None
    try:
        with db() as conn:
            cur = conn.execute("""
                UPDATE colaboradores
                SET nome=?, matricula=?, tag_rfid=?, cargo=?, tem_acesso=?, ativo=?
                WHERE id=?
            """, (data.nome.strip(), data.matricula.strip(), tag, data.cargo.strip(),
                  int(data.tem_acesso), int(data.ativo), colaborador_id))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Colaborador não encontrado.")
            row = conn.execute("SELECT * FROM colaboradores WHERE id=?", (colaborador_id,)).fetchone()
        return dict(row)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Matrícula ou tag RFID já cadastrada.") from exc


@app.delete("/api/colaboradores/{colaborador_id}", dependencies=[Depends(current_admin)])
def excluir_colaborador(colaborador_id: int):
    with db() as conn:
        cur = conn.execute("DELETE FROM colaboradores WHERE id=?", (colaborador_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Colaborador não encontrado.")
    return {"ok": True}


def registrar_evento(conn, colaborador, codigo, tipo, resultado, mensagem):
    nome = colaborador["nome"] if colaborador else "Tag desconhecida"
    matricula = colaborador["matricula"] if colaborador else None
    conn.execute("""
        INSERT INTO eventos
        (colaborador_id, nome_snapshot, matricula_snapshot, codigo_tag, tipo, resultado, mensagem, ocorrido_em)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (colaborador["id"] if colaborador else None, nome, matricula, codigo, tipo, resultado, mensagem, now_iso()))


@app.post("/api/rfid/ler", dependencies=[Depends(require_rfid_key)])
def ler_rfid(data: LeituraRFID):
    codigo = data.codigo_tag.strip()
    with db() as conn:
        row = conn.execute("SELECT * FROM colaboradores WHERE upper(tag_rfid)=upper(?)", (codigo,)).fetchone()
        colaborador = dict(row) if row else None

        if not colaborador:
            mensagem = "ALERTA: tentativa de acesso com tag não cadastrada."
            registrar_evento(conn, None, codigo, "TAG_DESCONHECIDA", "ALERTA", mensagem)
            return {"permitido": False, "tipo": "TAG_DESCONHECIDA", "mensagem": mensagem,
                    "feedback": "alarme", "codigo_tag": codigo}

        if not colaborador["ativo"]:
            mensagem = f"Acesso negado: colaborador {colaborador['nome']} está inativo."
            registrar_evento(conn, colaborador, codigo, "ACESSO_NEGADO", "NEGADO", mensagem)
            return {"permitido": False, "tipo": "ACESSO_NEGADO", "mensagem": mensagem,
                    "feedback": "negado", "colaborador": colaborador["nome"]}

        if not colaborador["tem_acesso"]:
            mensagem = f"Acesso negado: {colaborador['nome']} não possui permissão para esta sala."
            registrar_evento(conn, colaborador, codigo, "ACESSO_NEGADO", "NEGADO", mensagem)
            return {"permitido": False, "tipo": "ACESSO_NEGADO", "mensagem": mensagem,
                    "feedback": "negado", "colaborador": colaborador["nome"]}

        aberta = conn.execute("""
            SELECT id, entrada_em FROM sessoes_sala
            WHERE colaborador_id=? AND saida_em IS NULL ORDER BY id DESC LIMIT 1
        """, (colaborador["id"],)).fetchone()

        if aberta:
            conn.execute("UPDATE sessoes_sala SET saida_em=? WHERE id=?", (now_iso(), aberta["id"]))
            mensagem = f"Saída registrada: até logo, {colaborador['nome']}."
            registrar_evento(conn, colaborador, codigo, "SAIDA", "AUTORIZADO", mensagem)
            return {"permitido": True, "tipo": "SAIDA", "mensagem": mensagem,
                    "feedback": "saida", "colaborador": colaborador["nome"]}

        entrada_hoje = conn.execute("""
            SELECT COUNT(*) FROM eventos
            WHERE colaborador_id=? AND tipo='ENTRADA' AND substr(ocorrido_em,1,10)=?
        """, (colaborador["id"], date.today().isoformat())).fetchone()[0]
        conn.execute("INSERT INTO sessoes_sala (colaborador_id, entrada_em) VALUES (?, ?)",
                     (colaborador["id"], now_iso()))
        mensagem = (f"Bem-vindo de volta, {colaborador['nome']}." if entrada_hoje
                    else f"Bem-vindo, {colaborador['nome']}.")
        registrar_evento(conn, colaborador, codigo, "ENTRADA", "AUTORIZADO", mensagem)
        return {"permitido": True, "tipo": "ENTRADA", "mensagem": mensagem,
                "feedback": "entrada", "colaborador": colaborador["nome"]}


@app.get("/api/eventos", dependencies=[Depends(current_admin)])
def listar_eventos(limite: int = 100):
    limite = max(1, min(limite, 500))
    with db() as conn:
        rows = conn.execute("""
            SELECT id, nome_snapshot AS nome, matricula_snapshot AS matricula, codigo_tag,
                   tipo, resultado, mensagem, ocorrido_em
            FROM eventos ORDER BY id DESC LIMIT ?
        """, (limite,)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/monitoramento", dependencies=[Depends(current_admin)])
def monitoramento():
    with db() as conn:
        dentro = conn.execute("""
            SELECT c.id, c.nome, c.matricula, c.cargo, s.entrada_em
            FROM sessoes_sala s JOIN colaboradores c ON c.id=s.colaborador_id
            WHERE s.saida_em IS NULL ORDER BY s.entrada_em
        """).fetchall()
        eventos = conn.execute("""
            SELECT id, nome_snapshot AS nome, matricula_snapshot AS matricula, codigo_tag,
                   tipo, resultado, mensagem, ocorrido_em
            FROM eventos ORDER BY id DESC LIMIT 30
        """).fetchall()
    return {"dentro": [dict(r) for r in dentro], "eventos": [dict(r) for r in eventos]}


@app.get("/api/exportar.csv", dependencies=[Depends(current_admin)])
def exportar_csv():
    from fastapi.responses import StreamingResponse
    import csv
    import io
    with db() as conn:
        rows = conn.execute("""
            SELECT id, nome_snapshot, matricula_snapshot, codigo_tag, tipo, resultado, mensagem, ocorrido_em
            FROM eventos ORDER BY id
        """).fetchall()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["id", "nome", "matricula", "codigo_tag", "tipo", "resultado", "mensagem", "ocorrido_em"])
    for row in rows:
        writer.writerow(list(row))
    from io import BytesIO
    data_bytes = output.getvalue().encode("utf-8-sig")
    return StreamingResponse(BytesIO(data_bytes), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="logs_acesso.csv"'})
