import csv
import hashlib
import io
import os
import secrets
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
APP_NAME = os.getenv("APP_NAME", "Sentinel Access")
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "access_control.db")))
if not DATABASE_PATH.is_absolute():
    DATABASE_PATH = BASE_DIR / DATABASE_PATH
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")
RFID_API_KEY = os.getenv("RFID_API_KEY", "Sentinel")

app = FastAPI(title=APP_NAME, description="Sistema de controle de acesso RFID para sala restrita.", version="2.0.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
sessions: set[str] = set()


def db():
    conn = sqlite3.connect(str(DATABASE_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def init_db():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
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
            event_uuid TEXT UNIQUE,
            colaborador_id INTEGER,
            nome_snapshot TEXT NOT NULL,
            matricula_snapshot TEXT,
            codigo_tag TEXT NOT NULL,
            tipo TEXT NOT NULL,
            resultado TEXT NOT NULL,
            mensagem TEXT NOT NULL,
            ocorrido_em TEXT NOT NULL,
            dispositivo TEXT NOT NULL DEFAULT 'API',
            sincronizado INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(colaborador_id) REFERENCES colaboradores(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS sessoes_sala (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            colaborador_id INTEGER,
            entrada_em TEXT NOT NULL,
            saida_em TEXT,
            FOREIGN KEY(colaborador_id) REFERENCES colaboradores(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_eventos_data ON eventos(ocorrido_em);
        CREATE INDEX IF NOT EXISTS idx_eventos_tipo ON eventos(tipo);
        CREATE INDEX IF NOT EXISTS idx_sessoes_abertas ON sessoes_sala(saida_em);
        """)
        
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(eventos)").fetchall()}
        if "event_uuid" not in cols:
            conn.execute("ALTER TABLE eventos ADD COLUMN event_uuid TEXT")
        if "dispositivo" not in cols:
            conn.execute("ALTER TABLE eventos ADD COLUMN dispositivo TEXT NOT NULL DEFAULT 'API'")
        if "sincronizado" not in cols:
            conn.execute("ALTER TABLE eventos ADD COLUMN sincronizado INTEGER NOT NULL DEFAULT 1")
        
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_eventos_uuid ON eventos(event_uuid) WHERE event_uuid IS NOT NULL")
        count = conn.execute("SELECT COUNT(*) FROM colaboradores").fetchone()[0]
        if count == 0:
            
            demos = [
                ("Henrique", "DEMO001", "703695879170", "Colaborador autorizado", 1, 1, now_iso()),
                ("Tomás", "DEMO002", "1045162096093", "Colaborador sem acesso", 0, 1, now_iso()),
            ]
            conn.executemany("""INSERT INTO colaboradores
                (nome, matricula, tag_rfid, cargo, tem_acesso, ativo, criado_em)
                VALUES (?, ?, ?, ?, ?, ?, ?)""", demos)


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
    if not RFID_API_KEY or x_rfid_api_key != RFID_API_KEY:
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


class EventoOffline(BaseModel):
    event_uuid: str = Field(min_length=1, max_length=100)
    codigo_tag: str = Field(min_length=1, max_length=100)
    nome: str = Field(default="Tag desconhecida", max_length=120)
    matricula: Optional[str] = Field(default=None, max_length=40)
    tipo: str = Field(min_length=2, max_length=40)
    resultado: str = Field(min_length=2, max_length=30)
    mensagem: str = Field(min_length=1, max_length=300)
    ocorrido_em: str = Field(min_length=10, max_length=50)
    dispositivo: str = Field(default="Raspberry Pi", max_length=100)


class SyncInput(BaseModel):
    eventos: list[EventoOffline] = Field(default_factory=list, max_length=500)


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(str(BASE_DIR / "static" / "index.html"))


@app.get("/health")
def health():
    return {"status": "ok", "app": APP_NAME, "timestamp": now_iso()}


@app.get("/config/envs")
def envs():
    return {"app_name": APP_NAME, "version": app.version, "environment": os.getenv("ENVIRONMENT", "desenvolvimento")}


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
        dentro = conn.execute("SELECT COUNT(*) FROM sessoes_sala WHERE saida_em IS NULL").fetchone()[0]
        hoje = date.today().isoformat()
        entradas = conn.execute("SELECT COUNT(*) FROM eventos WHERE tipo='ENTRADA' AND substr(ocorrido_em,1,10)=?", (hoje,)).fetchone()[0]
        alertas = conn.execute("SELECT COUNT(*) FROM eventos WHERE tipo IN ('ACESSO_NEGADO','TAG_DESCONHECIDA') AND substr(ocorrido_em,1,10)=?", (hoje,)).fetchone()[0]
    return {"colaboradores_ativos": pessoas, "dentro_agora": dentro, "entradas_hoje": entradas, "alertas_hoje": alertas}


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
            cur = conn.execute("""INSERT INTO colaboradores
                (nome, matricula, tag_rfid, cargo, tem_acesso, ativo, criado_em)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (data.nome.strip(), data.matricula.strip(), tag, data.cargo.strip(), int(data.tem_acesso), int(data.ativo), now_iso()))
            return dict(conn.execute("SELECT * FROM colaboradores WHERE id=?", (cur.lastrowid,)).fetchone())
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Matrícula ou tag RFID já cadastrada.") from exc


@app.put("/api/colaboradores/{colaborador_id}", dependencies=[Depends(current_admin)])
def editar_colaborador(colaborador_id: int, data: ColaboradorInput):
    tag = data.tag_rfid.strip() if data.tag_rfid else None
    try:
        with db() as conn:
            cur = conn.execute("""UPDATE colaboradores SET nome=?, matricula=?, tag_rfid=?, cargo=?, tem_acesso=?, ativo=? WHERE id=?""",
                (data.nome.strip(), data.matricula.strip(), tag, data.cargo.strip(), int(data.tem_acesso), int(data.ativo), colaborador_id))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Colaborador não encontrado.")
            return dict(conn.execute("SELECT * FROM colaboradores WHERE id=?", (colaborador_id,)).fetchone())
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Matrícula ou tag RFID já cadastrada.") from exc


@app.delete("/api/colaboradores/{colaborador_id}", dependencies=[Depends(current_admin)])
def excluir_colaborador(colaborador_id: int):
    with db() as conn:
        cur = conn.execute("DELETE FROM colaboradores WHERE id=?", (colaborador_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Colaborador não encontrado.")
    return {"ok": True}


def registrar_evento(conn, colaborador, codigo, tipo, resultado, mensagem, dispositivo="API", event_uuid=None, ocorrido_em=None):
    conn.execute("""INSERT INTO eventos
        (event_uuid, colaborador_id, nome_snapshot, matricula_snapshot, codigo_tag, tipo, resultado, mensagem, ocorrido_em, dispositivo, sincronizado)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
        (event_uuid, colaborador["id"] if colaborador else None,
         colaborador["nome"] if colaborador else "Tag desconhecida",
         colaborador["matricula"] if colaborador else None, codigo, tipo, resultado, mensagem,
         ocorrido_em or now_iso(), dispositivo))


def processar_leitura(conn, codigo, dispositivo="API"):
    row = conn.execute("SELECT * FROM colaboradores WHERE upper(tag_rfid)=upper(?)", (codigo,)).fetchone()
    pessoa = dict(row) if row else None
    if not pessoa:
        msg = "ALERTA: tentativa de acesso com tag não cadastrada."
        registrar_evento(conn, None, codigo, "TAG_DESCONHECIDA", "ALERTA", msg, dispositivo)
        return {"permitido": False, "tipo": "TAG_DESCONHECIDA", "mensagem": msg, "feedback": "alarme", "codigo_tag": codigo}
    if not pessoa["ativo"]:
        msg = f"Acesso negado: colaborador {pessoa['nome']} está inativo."
        registrar_evento(conn, pessoa, codigo, "ACESSO_NEGADO", "NEGADO", msg, dispositivo)
        return {"permitido": False, "tipo": "ACESSO_NEGADO", "mensagem": msg, "feedback": "negado", "colaborador": pessoa["nome"]}
    if not pessoa["tem_acesso"]:
        msg = f"Acesso negado: {pessoa['nome']} não possui permissão para esta sala."
        registrar_evento(conn, pessoa, codigo, "ACESSO_NEGADO", "NEGADO", msg, dispositivo)
        return {"permitido": False, "tipo": "ACESSO_NEGADO", "mensagem": msg, "feedback": "negado", "colaborador": pessoa["nome"]}
    aberta = conn.execute("SELECT id FROM sessoes_sala WHERE colaborador_id=? AND saida_em IS NULL ORDER BY id DESC LIMIT 1", (pessoa["id"],)).fetchone()
    if aberta:
        conn.execute("UPDATE sessoes_sala SET saida_em=? WHERE id=?", (now_iso(), aberta["id"]))
        msg = f"Saída registrada: até logo, {pessoa['nome']}."
        registrar_evento(conn, pessoa, codigo, "SAIDA", "AUTORIZADO", msg, dispositivo)
        return {"permitido": True, "tipo": "SAIDA", "mensagem": msg, "feedback": "saida", "colaborador": pessoa["nome"]}
    entrada_hoje = conn.execute("SELECT COUNT(*) FROM eventos WHERE colaborador_id=? AND tipo='ENTRADA' AND substr(ocorrido_em,1,10)=?", (pessoa["id"], date.today().isoformat())).fetchone()[0]
    conn.execute("INSERT INTO sessoes_sala (colaborador_id, entrada_em) VALUES (?, ?)", (pessoa["id"], now_iso()))
    msg = f"Bem-vindo de volta, {pessoa['nome']}." if entrada_hoje else f"Bem-vindo, {pessoa['nome']}."
    registrar_evento(conn, pessoa, codigo, "ENTRADA", "AUTORIZADO", msg, dispositivo)
    return {"permitido": True, "tipo": "ENTRADA", "mensagem": msg, "feedback": "entrada", "colaborador": pessoa["nome"]}


@app.post("/api/rfid/ler", dependencies=[Depends(require_rfid_key)])
def ler_rfid(data: LeituraRFID):
    with db() as conn:
        return processar_leitura(conn, data.codigo_tag.strip(), data.dispositivo)


@app.get("/api/rfid/snapshot", dependencies=[Depends(require_rfid_key)])
def snapshot_rfid():
    """Lista mínima para cache offline da Raspberry Pi."""
    with db() as conn:
        rows = conn.execute("""SELECT id, nome, matricula, tag_rfid, cargo, tem_acesso, ativo
            FROM colaboradores WHERE tag_rfid IS NOT NULL AND trim(tag_rfid)<>''""").fetchall()
    return {"gerado_em": now_iso(), "colaboradores": [dict(r) for r in rows]}


@app.post("/api/rfid/sync", dependencies=[Depends(require_rfid_key)])
def sincronizar_eventos(data: SyncInput):
    aceitos = 0
    duplicados = 0
    with db() as conn:
        for e in sorted(data.eventos, key=lambda item: item.ocorrido_em):
            exists = conn.execute("SELECT id FROM eventos WHERE event_uuid=?", (e.event_uuid,)).fetchone()
            if exists:
                duplicados += 1
                continue
            
            pessoa_row = conn.execute("SELECT * FROM colaboradores WHERE matricula=?", (e.matricula,)).fetchone() if e.matricula else None
            pessoa = dict(pessoa_row) if pessoa_row else None
            registrar_evento(conn, pessoa, e.codigo_tag, e.tipo, e.resultado, e.mensagem,
                             e.dispositivo, e.event_uuid, e.ocorrido_em)
            
            if pessoa and e.tipo == "ENTRADA" and e.resultado == "AUTORIZADO":
                aberta = conn.execute("SELECT id FROM sessoes_sala WHERE colaborador_id=? AND saida_em IS NULL ORDER BY id DESC LIMIT 1", (pessoa["id"],)).fetchone()
                if not aberta:
                    conn.execute("INSERT INTO sessoes_sala (colaborador_id, entrada_em) VALUES (?, ?)", (pessoa["id"], e.ocorrido_em))
            elif pessoa and e.tipo == "SAIDA" and e.resultado == "AUTORIZADO":
                aberta = conn.execute("SELECT id FROM sessoes_sala WHERE colaborador_id=? AND saida_em IS NULL ORDER BY id DESC LIMIT 1", (pessoa["id"],)).fetchone()
                if aberta:
                    conn.execute("UPDATE sessoes_sala SET saida_em=? WHERE id=?", (e.ocorrido_em, aberta["id"]))
            aceitos += 1
    return {"recebidos": len(data.eventos), "novos": aceitos, "duplicados": duplicados}


@app.get("/api/eventos", dependencies=[Depends(current_admin)])
def listar_eventos(limite: int = 100):
    limite = max(1, min(limite, 500))
    with db() as conn:
        rows = conn.execute("""SELECT id, nome_snapshot AS nome, matricula_snapshot AS matricula,
            codigo_tag, tipo, resultado, mensagem, ocorrido_em, dispositivo
            FROM eventos ORDER BY ocorrido_em DESC, id DESC LIMIT ?""", (limite,)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/monitoramento", dependencies=[Depends(current_admin)])
def monitoramento():
    with db() as conn:
        dentro = conn.execute("""SELECT c.id, c.nome, c.matricula, c.cargo, s.entrada_em
            FROM sessoes_sala s JOIN colaboradores c ON c.id=s.colaborador_id
            WHERE s.saida_em IS NULL ORDER BY s.entrada_em""").fetchall()
        eventos = conn.execute("""SELECT id, nome_snapshot AS nome, matricula_snapshot AS matricula,
            codigo_tag, tipo, resultado, mensagem, ocorrido_em, dispositivo
            FROM eventos ORDER BY ocorrido_em DESC, id DESC LIMIT 30""").fetchall()
    return {"dentro": [dict(r) for r in dentro], "eventos": [dict(r) for r in eventos]}


@app.get("/api/exportar.csv", dependencies=[Depends(current_admin)])
def exportar_csv():
    with db() as conn:
        rows = conn.execute("""SELECT id, event_uuid, nome_snapshot, matricula_snapshot, codigo_tag,
            tipo, resultado, mensagem, ocorrido_em, dispositivo FROM eventos ORDER BY ocorrido_em, id""").fetchall()
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["id", "event_uuid", "nome", "matricula", "codigo_tag", "tipo", "resultado", "mensagem", "ocorrido_em", "dispositivo"])
    for row in rows:
        writer.writerow(list(row))
    data_bytes = output.getvalue().encode("utf-8-sig")
    return StreamingResponse(io.BytesIO(data_bytes), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="logs_acesso.csv"'})


class Item(BaseModel):
    nome: str
    preco: float
    quantidade: int

@app.post("/items")
def criar_itens(itens: list[Item]):
    return [{"nome": i.nome, "preco_total": i.preco * i.quantidade, "mensagem": f"Item {i.nome} Cadastrado!"} for i in itens]
