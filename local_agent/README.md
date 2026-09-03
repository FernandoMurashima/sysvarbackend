# Sysvar Local Agent

Agente local para detectar XML de NF-e em pastas do cliente e registrar somente metadados no Sysvar via API HTTP. O XML original é lido apenas localmente e nunca é enviado ao backend.

## Requisitos

- Python 3.10+
- Acesso ao backend Sysvar
- Token gerado em `/api/fiscal/agentes-locais/{id}/gerar-token/`

## Instalação

```powershell
cd local_agent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Configuração

Crie `config.json` a partir de `config.example.json`:

```json
{
  "api_base_url": "http://127.0.0.1:8000",
  "token": "COLOQUE_O_TOKEN_AQUI",
  "poll_interval_seconds": 15,
  "heartbeat_interval_seconds": 60,
  "request_timeout_seconds": 15
}
```

Também é possível informar o token por variável de ambiente. Quando existir, ela prevalece sobre `config.json`:

```powershell
$env:SYSVAR_AGENT_TOKEN="TOKEN_GERADO"
```

## Execução

Executar um ciclo e encerrar:

```powershell
python -m sysvar_agent --once
```

Executar continuamente:

```powershell
python -m sysvar_agent
```

## Arquivos locais

- `data/agent.db`: fila SQLite de XMLs pendentes, enviados e com erro.
- `logs/sysvar-agent.log`: log local do agente.
- `config.json`: configuração local com token.

Esses arquivos não devem ser versionados.

## Funcionamento

O agente autentica com `Authorization: Agent <TOKEN>`, busca configurações em `/api/fiscal/agente-local/configuracoes/`, envia heartbeat periódico e varre apenas as pastas retornadas pelo backend. Ele procura arquivos `*.xml` no diretório informado, sem subdiretórios, sem mover, apagar, renomear ou editar arquivos.

Quando encontra uma NF-e, extrai metadados com `xml.etree.ElementTree` e envia para `/api/fiscal/agente-local/xml-detectado/`. Respostas `created=true` e `created=false` significam sucesso e marcam a chave como enviada na fila local.

Falhas transitórias de internet/backend preservam a fila e usam backoff simples. Erros 400 são registrados como erro sem retry infinito.
