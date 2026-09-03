# Sysvar Local Agent

Agente local para detectar XML de NF-e em pastas do cliente e registrar somente metadados no Sysvar via API HTTP. O XML original é lido apenas localmente e nunca é enviado ao backend.

## Requisitos de Desenvolvimento

- Python 3.10+
- Acesso ao backend Sysvar
- Token gerado em `/api/fiscal/agentes-locais/{id}/gerar-token/`

## Instalação

```powershell
cd local_agent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install .
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

### Desenvolvimento com Python

Instale as dependências no venv do agente:

```powershell
cd local_agent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install .
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

Durante `install` e `update`, o agente valida `local_agent\config.json` e grava o caminho absoluto no registro do próprio serviço. Em execução, o serviço usa esse caminho persistido, sem depender do diretório corrente nem do local onde o pacote `sysvar_agent` foi instalado. O arquivo não deve ser copiado para `.venv\Lib\site-packages`.

Durante `install` e `update`, o agente prepara automaticamente o host do pywin32 para que `pythonservice.exe` encontre as DLLs necessárias do Python e do pywin32 dentro do ambiente do agente. Ele também gera `pythonservice._pth` ao lado do host com os caminhos do venv necessários para `site-packages`, `win32`, `win32\lib` e `pythonwin`. Não copie `python312.dll` manualmente, não edite o Registry manualmente, não altere `System32` e não dependa do diretório corrente, do `PATH` ou do `PYTHONPATH` do usuário.

Se preferir um `config.json` fora da pasta do agente, defina `SYSVAR_AGENT_CONFIG` antes de executar `install` ou `update`; o caminho absoluto será validado e persistido no serviço. Não coloque token em argumentos de linha de comando.

### Distribuição Standalone

A distribuição de homologação é gerada com PyInstaller em modo `onedir`:

```powershell
cd local_agent
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\build.ps1
```

O executável final fica em `local_agent\dist\SysvarLocalAgent\SysvarLocalAgent.exe`. Ele deve ser chamado por caminho absoluto ou a partir da pasta `dist\SysvarLocalAgent`:

```powershell
.\dist\SysvarLocalAgent\SysvarLocalAgent.exe install
.\dist\SysvarLocalAgent\SysvarLocalAgent.exe start
.\dist\SysvarLocalAgent\SysvarLocalAgent.exe stop
.\dist\SysvarLocalAgent\SysvarLocalAgent.exe restart
.\dist\SysvarLocalAgent\SysvarLocalAgent.exe remove
```

No modo standalone, o serviço usa o próprio `SysvarLocalAgent.exe` como host. Ele não chama `python.exe`, `pip`, `.venv`, `pythonservice.exe` externo nem `pythonservice._pth`. `config.json`, token, `data\agent.db`, logs e XMLs continuam externos ao executável.

### Instalador Windows

O instalador é gerado com Inno Setup a partir da distribuição `onedir` já criada:

```powershell
cd local_agent
.\build.ps1
.\build-installer.ps1
```

O Setup instala os binários em `C:\Program Files\Sysvar\LocalAgent` e cria dados persistentes em `C:\ProgramData\Sysvar\LocalAgent`. Na primeira instalação, `config.example.json` é copiado para `C:\ProgramData\Sysvar\LocalAgent\config.json` somente se o arquivo ainda não existir. Em upgrade, reinstalação e desinstalação padrão, `config.json`, `data\agent.db` e `logs\` são preservados.

Durante a instalação, o Setup executa `SysvarLocalAgent.exe install` com `SYSVAR_AGENT_CONFIG` apontando para `C:\ProgramData\Sysvar\LocalAgent\config.json`, deixando o `ConfigPath` persistido no serviço. Se o arquivo ainda estiver com token placeholder, o serviço é registrado, mas não é iniciado automaticamente. Se a configuração já estiver válida, o serviço pode ser iniciado após instalação ou upgrade.

Se `config.json` não existir, estiver inválido ou sem token, o serviço registra a falha e encerra de forma controlada. Backend ou internet offline não derrubam o serviço; a fila SQLite permanece e o retry/backoff continua valendo.

Ao receber parada do Windows, o serviço sinaliza encerramento limpo, deixa o loop terminar, fecha a conexão SQLite e preserva `data/agent.db`, logs e configuração.

### Diretórios de Rede

Serviços Windows executados como LocalSystem normalmente não enxergam letras de unidade mapeadas do usuário, como `X:\XML`. Pastas locais como `C:\Sysvar\XML\Fornecedores` devem funcionar. Para rede, uma etapa futura deve usar caminhos UNC, como `\\servidor\compartilhamento\XML`, e conta de serviço com permissão apropriada.
