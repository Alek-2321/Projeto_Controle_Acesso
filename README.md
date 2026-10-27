# Sentinel Access — FastAPI + RFID

MVP acadêmico de controle de acesso à sala restrita usando FastAPI, SQLite e interface web responsiva.

## Recursos
- Login administrativo simples.
- Cadastro, edição e exclusão de colaboradores.
- Tag RFID única, matrícula, cargo, permissão e status ativo/inativo.
- Endpoint de leitura RFID que autoriza, nega ou gera alerta para tag desconhecida.
- Alternância entre entrada e saída com base na presença registrada.
- Dashboard e monitoramento atualizados automaticamente a cada 5 segundos.
- Logs de eventos no SQLite e exportação CSV para análise com Pandas.
- Endpoint de saúde `/health` e documentação automática em `/docs`.

## Requisitos
Python 3.10 ou superior. Recomendado Python 3.12 ou 3.13 para evitar incompatibilidades de bibliotecas em versões muito recentes.

## Executar no Windows (PowerShell)
```powershell
# Na pasta do projeto:
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edite `.env` e troque `ADMIN_PASSWORD` e `RFID_API_KEY` por valores próprios. Depois:
```powershell
uvicorn main:app --reload
```
Abra http://127.0.0.1:8000

Credenciais iniciais de demonstração, caso você mantenha o `.env.example` sem alterações:
- Usuário: `admin`
- Senha: `troque-esta-senha`

## Executar com uv
```powershell
uv python install 3.13
uv venv --python 3.13
uv sync
uv run uvicorn main:app --reload
```
O `uv sync` usa as dependências declaradas em `pyproject.toml`. Se houver um `uv.lock` antigo no projeto, remova-o ou gere-o novamente depois de atualizar o `pyproject.toml`.

## API principal
- `POST /api/login` — recebe `{"usuario":"admin","senha":"..."}` e retorna token.
- `GET /api/colaboradores` — lista colaboradores (Bearer token).
- `POST /api/colaboradores` — cadastra colaborador (Bearer token).
- `PUT /api/colaboradores/{id}` — edita colaborador (Bearer token).
- `DELETE /api/colaboradores/{id}` — exclui colaborador (Bearer token).
- `POST /api/rfid/ler` — leitura da tag pelo dispositivo RFID; exige cabeçalho `X-RFID-API-Key`.
- `GET /api/monitoramento` — eventos recentes e colaboradores dentro (Bearer token).
- `GET /api/eventos?limite=100` — histórico (Bearer token).
- `GET /api/exportar.csv` — baixa os logs CSV (Bearer token).
- `GET /health` — healthcheck público.

### Simular uma leitura RFID
Com a API ligada, rode em outro terminal:
```powershell
$headers = @{ "X-RFID-API-Key" = "troque-esta-chave"; "Content-Type" = "application/json" }
$body = @{ codigo_tag = "A1B2C3D4"; dispositivo = "Raspberry Pi" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/rfid/ler" -Method Post -Headers $headers -Body $body
```
A tag `A1B2C3D4` vem cadastrada como demonstração. Cadastre as tags reais pela interface.

## Resposta de leitura
- Tag autorizada: `permitido: true`, tipo `ENTRADA` ou `SAIDA`.
- Colaborador inativo ou sem permissão: `permitido: false`, tipo `ACESSO_NEGADO`.
- Tag desconhecida: `permitido: false`, tipo `TAG_DESCONHECIDA` e evento de alerta severo.

## Integração com Raspberry Pi
A Raspberry Pi deve ler o UID com o leitor RFID e enviar o código para `POST /api/rfid/ler`. Acione o relé/trava somente se `permitido` for `true`; use LED/buzzer conforme `feedback`. Em caso de perda de conexão, este MVP ainda não implementa cache local nem fila de sincronização offline — isso deve ser desenvolvido na camada embarcada para cumprir o requisito de operação desconectada.

## Segurança e limitações do MVP
- A autenticação e a chave RFID são adequadas apenas para demonstração acadêmica; usar HTTPS, senhas com hash robusto, expiração de sessão e armazenamento seguro de segredos em produção.
- Tokens ficam em memória e são invalidados quando a aplicação reinicia.
- O SQLite é local; para várias instâncias, usar banco compartilhado.
- A alternância entrada/saída é baseada em sessões abertas; para recuperação de eventos perdidos, criar rotina administrativa de reconciliação.
- O sistema não controla diretamente GPIO/relé: isso deve ser feito pelo programa embarcado na Raspberry Pi após resposta da API.
