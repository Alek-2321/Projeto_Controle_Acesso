import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import RPi.GPIO as GPIO
from mfrc522 import SimpleMFRC522

BASE_DIR = Path(__file__).resolve().parent
API_BASE_URL = os.getenv("RFID_API_URL", "http://192.168.10.10:8000").rstrip("/")
if API_BASE_URL.endswith("/api/rfid/ler"):
    API_BASE_URL = API_BASE_URL[: -len("/api/rfid/ler")]
API_KEY = os.getenv("RFID_API_KEY", "Sentinel")
LOCAL_DB = Path(os.getenv("RFID_LOCAL_DB", str(BASE_DIR / "access_control_rfid_local.db")))
CACHE_REFRESH_SECONDS = max(5, int(os.getenv("RFID_CACHE_REFRESH_SECONDS", "15")))
SYNC_SECONDS = max(2, int(os.getenv("RFID_SYNC_SECONDS", "5")))
BUZZER_PIN = int(os.getenv("BUZZER_PIN", "33"))
ENABLE_BUZZER = os.getenv("ENABLE_BUZZER", "1").lower() not in ("0", "false", "no", "off")

GPIO.setmode(GPIO.BOARD)
GPIO.setup(31, GPIO.OUT)
GPIO.setup(29, GPIO.OUT)
if ENABLE_BUZZER:
    GPIO.setup(BUZZER_PIN, GPIO.OUT)
GPIO.output(31, GPIO.LOW)
GPIO.output(29, GPIO.LOW)
if ENABLE_BUZZER:
    GPIO.output(BUZZER_PIN, GPIO.LOW)

reader = SimpleMFRC522()
lock = threading.Lock()


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def local_db():
    LOCAL_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(LOCAL_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_local_db():
    with local_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS cached_colaboradores (
            server_id INTEGER,
            nome TEXT NOT NULL,
            matricula TEXT NOT NULL UNIQUE,
            tag_rfid TEXT UNIQUE,
            cargo TEXT NOT NULL DEFAULT '',
            tem_acesso INTEGER NOT NULL DEFAULT 0,
            ativo INTEGER NOT NULL DEFAULT 1,
            atualizado_em TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS eventos_local (
            event_uuid TEXT PRIMARY KEY,
            codigo_tag TEXT NOT NULL,
            nome TEXT NOT NULL,
            matricula TEXT,
            tipo TEXT NOT NULL,
            resultado TEXT NOT NULL,
            mensagem TEXT NOT NULL,
            ocorrido_em TEXT NOT NULL,
            dispositivo TEXT NOT NULL DEFAULT 'Raspberry Pi',
            sincronizado INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_eventos_local_sync ON eventos_local(sincronizado, ocorrido_em);
        CREATE INDEX IF NOT EXISTS idx_eventos_local_matricula ON eventos_local(matricula, tipo, ocorrido_em);
        CREATE TABLE IF NOT EXISTS presencas_local (
            matricula TEXT PRIMARY KEY,
            nome TEXT NOT NULL,
            entrada_em TEXT NOT NULL
        );
        """)


def request_json(path, payload=None, timeout=4):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"X-RFID-API-Key": API_KEY, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request(f"{API_BASE_URL}{path}", data=body, headers=headers, method="GET" if body is None else "POST")
    with urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def refresh_cache():
    snapshot = request_json("/api/rfid/snapshot")
    people = snapshot.get("colaboradores", [])
    
    with local_db() as conn:
        conn.execute("DELETE FROM cached_colaboradores")
        for p in people:
            conn.execute("""INSERT INTO cached_colaboradores
                (server_id, nome, matricula, tag_rfid, cargo, tem_acesso, ativo, atualizado_em)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (p.get("id"), p.get("nome", ""), p.get("matricula", ""), p.get("tag_rfid"),
                 p.get("cargo", ""), int(p.get("tem_acesso", 0)), int(p.get("ativo", 1)), now_iso()))
    print(f"Cache offline atualizado: {len(people)} colaborador(es).")


def sync_pending():
    with local_db() as conn:
        rows = conn.execute("SELECT * FROM eventos_local WHERE sincronizado=0 ORDER BY ocorrido_em LIMIT 100").fetchall()
    if not rows:
        return
    events = [{
        "event_uuid": r["event_uuid"], "codigo_tag": r["codigo_tag"], "nome": r["nome"],
        "matricula": r["matricula"], "tipo": r["tipo"], "resultado": r["resultado"],
        "mensagem": r["mensagem"], "ocorrido_em": r["ocorrido_em"], "dispositivo": r["dispositivo"]
    } for r in rows]
    request_json("/api/rfid/sync", {"eventos": events}, timeout=8)
    with local_db() as conn:
        conn.executemany("UPDATE eventos_local SET sincronizado=1 WHERE event_uuid=?", [(r["event_uuid"],) for r in rows])
    print(f"Sincronizados {len(rows)} evento(s) pendente(s).")


def background_sync():
    last_cache = 0
    last_sync = 0
    while True:
        now = time.monotonic()
        if now - last_cache >= CACHE_REFRESH_SECONDS:
            try:
                refresh_cache()
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"Servidor indisponível; mantendo cache local: {exc}")
            last_cache = now
        if now - last_sync >= SYNC_SECONDS:
            try:
                sync_pending()
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"Sincronização pendente; os eventos continuam salvos localmente: {exc}")
            last_sync = now
        time.sleep(1)


def beep_pattern(count=1, duration=0.08, pause=0.08):
    if not ENABLE_BUZZER:
        return
    for _ in range(count):
        GPIO.output(BUZZER_PIN, GPIO.HIGH)
        time.sleep(duration)
        GPIO.output(BUZZER_PIN, GPIO.LOW)
        time.sleep(pause)


def signal_result(feedback):
    if feedback in ("entrada", "saida"):
        GPIO.output(31, GPIO.HIGH)
        beep_pattern(1, 0.12, 0.05)
        time.sleep(0.8)
        GPIO.output(31, GPIO.LOW)
    elif feedback == "negado":
        GPIO.output(29, GPIO.HIGH)
        beep_pattern(2, 0.10, 0.10)
        time.sleep(0.8)
        GPIO.output(29, GPIO.LOW)
    else:  
        GPIO.output(29, GPIO.HIGH)
        beep_pattern(3, 0.12, 0.07)
        time.sleep(1.2)
        GPIO.output(29, GPIO.LOW)


def save_local_event(codigo, nome, matricula, tipo, resultado, mensagem, conn=None):
    event_uuid = str(uuid.uuid4())
    ocorrido_em = now_iso()
    sql = """INSERT INTO eventos_local
        (event_uuid, codigo_tag, nome, matricula, tipo, resultado, mensagem, ocorrido_em, dispositivo, sincronizado)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Raspberry Pi', 0)"""
    params = (event_uuid, codigo, nome, matricula, tipo, resultado, mensagem, ocorrido_em)
    if conn is not None:
        conn.execute(sql, params)
    else:
        with local_db() as own_conn:
            own_conn.execute(sql, params)
    print(mensagem)
    return {"event_uuid": event_uuid, "tipo": tipo, "resultado": resultado, "ocorrido_em": ocorrido_em}


def process_card(codigo):
    codigo = str(codigo).strip()
    with local_db() as conn:
        row = conn.execute("SELECT * FROM cached_colaboradores WHERE upper(tag_rfid)=upper(?)", (codigo,)).fetchone()
        person = dict(row) if row else None
        if not person:
            result = save_local_event(codigo, "Tag desconhecida", None, "TAG_DESCONHECIDA", "ALERTA",
                                      "ALERTA: tentativa de acesso com tag não cadastrada.", conn=conn)
            result["feedback"] = "alarme"
            return result
        nome, matricula = person["nome"], person["matricula"]
        if not person["ativo"]:
            result = save_local_event(codigo, nome, matricula, "ACESSO_NEGADO", "NEGADO",
                                      f"Acesso negado: colaborador {nome} está inativo.", conn=conn)
            result["feedback"] = "negado"
            return result
        if not person["tem_acesso"]:
            result = save_local_event(codigo, nome, matricula, "ACESSO_NEGADO", "NEGADO",
                                      f"Acesso negado: {nome} não possui permissão para esta sala.", conn=conn)
            result["feedback"] = "negado"
            return result
        inside = conn.execute("SELECT entrada_em FROM presencas_local WHERE matricula=?", (matricula,)).fetchone()
        if inside:
            conn.execute("DELETE FROM presencas_local WHERE matricula=?", (matricula,))
            result = save_local_event(codigo, nome, matricula, "SAIDA", "AUTORIZADO", f"Saída registrada: até logo, {nome}.", conn=conn)
            result["feedback"] = "saida"
            return result
        today = datetime.now().astimezone().date().isoformat()
        previous_entries = conn.execute("SELECT COUNT(*) FROM eventos_local WHERE matricula=? AND tipo='ENTRADA' AND substr(ocorrido_em,1,10)=?", (matricula, today)).fetchone()[0]
        entered = now_iso()
        conn.execute("INSERT INTO presencas_local (matricula, nome, entrada_em) VALUES (?, ?, ?)", (matricula, nome, entered))
        msg = f"Bem-vindo de volta, {nome}." if previous_entries else f"Bem-vindo, {nome}."
        result = save_local_event(codigo, nome, matricula, "ENTRADA", "AUTORIZADO", msg, conn=conn)
        result["feedback"] = "entrada"
        return result


def main():
    init_local_db()
    try:
        refresh_cache()
    except Exception as exc:
        print(f"Não foi possível carregar cache inicial: {exc}")
        print("O leitor vai usar o cache local existente; sem cache, todas as tags serão tratadas como desconhecidas.")
    threading.Thread(target=background_sync, daemon=True).start()
    print("Sentinel Access — leitor RFID iniciado.")
    print(f"Servidor: {API_BASE_URL}")
    print(f"Banco local: {LOCAL_DB}")
    print("Modo offline habilitado: as permissões ficam em cache e os eventos são sincronizados quando a conexão voltar.")
    print("Aproxime o cartão do leitor...")
    try:
        while True:
            codigo, _texto = reader.read()
            print(f"Tag lida: {codigo}")
            try:
                result = process_card(codigo)
                signal_result(result.get("feedback", "negado"))
            except sqlite3.Error as exc:
                print(f"Erro no banco local: {exc}")
                signal_result("alarme")
            print("Aproxime o cartão do leitor...")
            time.sleep(0.8)
    finally:
        GPIO.output(31, GPIO.LOW)
        GPIO.output(29, GPIO.LOW)
        if ENABLE_BUZZER:
            GPIO.output(BUZZER_PIN, GPIO.LOW)
        GPIO.cleanup()


if __name__ == "__main__":
    main()
