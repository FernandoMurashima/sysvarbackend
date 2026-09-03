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

## Execução de Desenvolvimento

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

## Windows Service

O serviço usa o mesmo núcleo do agente de desenvolvimento: configuração, cliente HTTP, fila SQLite, scanner e runner. Não depende de Django e não abre console.

Instale as dependências no venv do agente:

```powershell
cd local_agent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Crie `config.json` em `local_agent\config.json`. Caminhos relativos como `data/agent.db` e `logs/sysvar-agent.log` são resolvidos a partir da pasta do `config.json`, não do diretório corrente do processo.

Abra o PowerShell como Administrador e execute:

```powershell
.\.venv\Scripts\python.exe -m sysvar_agent.windows_service install
.\.venv\Scripts\python.exe -m sysvar_agent.windows_service start
.\.venv\Scripts\python.exe -m sysvar_agent.windows_service stop
.\.venv\Scripts\python.exe -m sysvar_agent.windows_service restart
.\.venv\Scripts\python.exe -m sysvar_agent.windows_service remove
```

O nome técnico é `SysvarLocalAgent` e o nome exibido é `Sysvar Local Agent`. A instalação configura inicialização automática. Após instalar, o serviço pode ser visto em `services.msc` como `Sysvar Local Agent`.

Se preferir um `config.json` fora da pasta do agente, defina `SYSVAR_AGENT_CONFIG` no ambiente do serviço apontando para o caminho absoluto do arquivo. Não coloque token em argumentos de linha de comando.

Se `config.json` não existir, estiver inválido ou sem token, o serviço registra a falha e encerra de forma controlada. Backend ou internet offline não derrubam o serviço; a fila SQLite permanece e o retry/backoff continua valendo.

Ao receber parada do Windows, o serviço sinaliza encerramento limpo, deixa o loop terminar, fecha a conexão SQLite e preserva `data/agent.db`, logs e configuração.

### Diretórios de Rede

Serviços Windows executados como LocalSystem normalmente não enxergam letras de unidade mapeadas do usuário, como `X:\XML`. Pastas locais como `C:\Sysvar\XML\Fornecedores` devem funcionar. Para rede, uma etapa futura deve usar caminhos UNC, como `\\servidor\compartilhamento\XML`, e conta de serviço com permissão apropriada.
