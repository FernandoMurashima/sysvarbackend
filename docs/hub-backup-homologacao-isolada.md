# Backup e recuperacao do Hub — parecer integrado do Prompt 12

Data: 2026-10-15. **INTEGRACAO SIMULADA APROVADA. HOMOLOGACAO OPERACIONAL REAL PENDENTE.**
Nao liberar a operacao com base neste parecer. Nenhum MySQL real foi acessado,
restaurado ou alterado; nao houve migrations operacionais, agendamento, ativacao,
DROP/RESET/PURGE nem inicio/parada de servicos Windows reais.

## Escopo e fontes

Central inicial: main `fb464aadedca82195b60ba985ad7979671e9e4d9`; Hub inicial: main
`d284c8fb2d6567ec10ace31042f07a1251180e3e`. A modificacao preexistente em
`iniciar-central-dev.ps1` foi preservada, fora do commit. SHA-256 do arquivo:
`0A14CAE418E759DFFED18CDCDF40C77ACB7E64F179F24BE6536DB36969876864`.

Consultados AGENTS.md dos dois repositorios, skill sysvar-contexto, Estado Atual
- Sysvar Hub (2026-09-30), Estado Atual - Plataforma e Auditoria (2026-09-30),
Riscos e Cuidados - Sysvar Hub - Idempotencia e Retry (2026-09-18), trecho de
backup/restauracao do Planejamento - Fechamento do Sysvar Hub e contratos acoplados
vigentes dos Prompts 1–11. Pendencias antigas do planejamento nao foram tratadas
como homologacao nem como substitutas dos contratos atuais.

Reutilizados os geradores `Invoke-HubMySqlFullBackup`/`Invoke-HubMySqlIncrementalBackup`,
compactacao/ancoras/selecionador/travas, BackupClient/BackupSender, HubTokenAuthentication,
API real de recebimento, armazenamento AES-GCM, HubBackup/Upload/Parte, auditoria de
integridade, autorizacao administrativa, exportacao streaming, runtime.recovery,
bloqueio operacional e HubSyncProcessor. Nenhum mecanismo paralelo de backup.

## Ambiente e limites da evidencia

- Central Django real e banco SQLite compartilhado **somente em memoria**, criado
  pelo runner dedicado antes de qualquer conexao ao banco configurado. Migrations
  legadas MySQL foram desabilitadas apenas nesse processo; SyncDB dos models atuais.
- Servidor HTTPS WSGI real em loopback/porta efemera e certificado ficticio confiado
  explicitamente pelo cliente de teste; verificacao de certificado/hostname mantida.
  Recebimento e recuperacao habilitados apenas por override nesse processo de teste.
- Conta Hub e isolamento resolvidos pelo token ficticio autenticado na API real.
  Usuario/permissoes/escopo/auditoria administrativos reais no SQLite; somente o
  resolvedor de sessao administrativa e simulado, com token exclusivo ficticio.
- Geradores PowerShell 5.1 reais com mysqldump/mysqlbinlog, metadados MySQL, ACLs do
  gerador e consultas ao servico substituidos por mocks. Binlogs de teste nao sao
  binlogs MySQL recuperaveis; payload e somente comentarios/IDs ficticios.
- Compactacao, SHA-256, cadeia/manifestos originais, HTTP, retomada, AES-GCM,
  publicacao, verificacao independente e exportacao executados de fato sobre esses
  arquivos. Manifestos do gerador atravessam a API sem reserializacao.
- Importacao usa o restaurador real com cliente MySQL simulado. Preflight recebe
  metadados ficticios; CHECK TABLE/FKs nao sao executados no MySQL, e a conferencia
  logica pos-importacao e simulada. Nenhum SQL do pacote e interpretado/executado.
- Preservacao de IDs demonstrada nos bytes dos artefatos e no reenvio **ficticio**
  ao processador existente da Central; nao comprova restauracao de linhas MySQL.

## Etapas e falhas comprovadas

14 cenarios integrados novos, aprovados em uma execucao final (64,901 s):

| Cenario | Evidencia |
| --- | --- |
| FULL + dois incrementais | Geradores existentes, gzip e SHA reais, ancoras encadeadas; sem mudancas nao publica novo incremental |
| Envio e retomada | ACK perdido depois do commit de parte real; consulta offset e conclui sem duplicar partes |
| Recebimento e validacao | RECEBIDO fica PENDENTE ate auditoria independente; tags AES-GCM e cadeia verificadas |
| Selecao e autorizacao | Manifesto administrativo ordenado, autorizacao explicita, HTTPS e no-store |
| Exportacao e replay | Arquivos descriptografados por streaming, SHA igual ao original; FULL antes dos incrementais na mesma sessao simulada |
| Evento ja confirmado | IDs/payload preservados; HubSyncProcessor retorna DUPLICADO, sem reaplicar negocio |
| Internet indisponivel | Falha na conexao, backoff e retry; FULL ja recebido e arquivos locais preservados |
| FULL/incremental ausente | Cadeia de envio e pacote exportado incompletos rejeitados antes da importacao |
| Hash divergente | Central rejeita incremental adulterado; nao recebe sucessor nem altera FULL valido |
| Cadeia corrompida | Auditoria bloqueia descendentes e autorizacao; mantem arquivos e prefixo valido |
| Outra loja/empresa e usuario | Tokens/escopos divergentes rejeitados; usuario comum nao autoriza |
| Download interrompido | Arquivo curto rejeitado no preflight; cliente MySQL nao e iniciado |
| Destino nao vazio e importacao interrompida | Sem importacao sobre dados existentes; falha nao envia incremental nem libera Hub |
| Ponto anterior e memoria | Pacote/replay param na coordenada escolhida; streaming limitado com tamanhos variados |

Os testes tambem verificam marcador persistente e bootstrap do PDV recusado,
CONFLITO para evento confirmado com payload alterado, ausencia de temporario SQL
em claro, originais cifrados intactos e backups validos preservados.

## Defeitos de integracao encontrados e corrigidos

1. O transmissor usava UUID hex de 32 caracteres nas rotas de partes/progresso/finalizar.
   O converter `<uuid:...>` da Central exige grafia canonica com hifens. A API real
   retornou HTTP 404 no primeiro teste. Corrigida somente a grafia da rota; IDs de
   manifests/checkpoints continuam intactos. O simulador antigo foi alinhado ao
   converter real, com assercao mais estrita; nenhuma verificacao foi removida.
2. O inicio do FULL exportado inclui `reference=SNAPSHOT_START`; o inicio incremental
   nao tem esse campo descritivo. A igualdade de dicionarios rejeitava o pacote
   integro com CHAIN_GAP_OR_OVERLAP. O restaurador agora compara arquivo, posicao e
   ancora, mantendo todos os hashes e a verificacao integral do manifesto. Ancora
   divergente continua rejeitada, coberta por regressao.

Foram corrigidos tambem aspectos do proprio harness: carregar o modulo nativo
PowerShell 5.1 em vez do herdado de pwsh 7 e simular indisponibilidade em `connect`,
onde a conexao realmente ocorre. As assercoes funcionais nao foram enfraquecidas.

## Streaming e desempenho observado

Medicao de exportacao + restauracao simulada, com tracemalloc, nesta maquina.
Geracao e envio ficam fora desses tempos/picos. O observador do cliente simulado
conta bytes e descarta o conteudo, evitando acumular o SQL apenas para o teste.

| Entrada ficticia antes de base64/gzip | Pico de alocacoes Python | Maior escrita MySQL simulada | Tempo |
| --- | --- | --- | --- |
| 1 KiB | 492.442 bytes | 1.711 bytes | 0,422 s |
| 128 KiB | 791.697 bytes | 65.536 bytes | 0,469 s |
| 2 MiB | 2.603.898 bytes | 65.536 bytes | 0,500 s |

Pico Python menor que 16 MiB em todos os tamanhos; leitura de download/escrita de
importacao em blocos de ate 64 KiB. Nao mede RSS total, buffers nativos TLS/AES,
.NET/PowerShell ou MySQL, nem projeta tempo para arquivos de muitos GiB. Limites e
retencao operacionais nao foram alterados. Revalidacoes completas exigem I/O adicional.

## Validacoes e reproducao segura

- 14 cenarios integrados novos: aprovados.
- 59 testes direcionados Hub (transmissao e recuperacao): aprovados.
- 1 fixture Pester integrado novo: aprovado em Windows PowerShell 5.1 e reutilizado
  nos cenarios Central. Sem execucao de instaladores ou ferramentas MySQL reais.
- manage.py check em ambos os repositorios e git diff --check: aprovados.
- Os quatro erros antigos de mocks de runtime.tests_sync_worker sao separados deste
  bloco: os quatro casos foram reproduzidos em ensaio direcionado com SQLite em
  memoria, todos com TypeError: 'Mock' object is not iterable. Nao foram corrigidos
  nem mascarados. Worker e testes dele conferidos identicos ao HEAD inicial; nao
  sao falhas novas dos cenarios de backup.
- Nenhuma migration criada ou aplicada a banco operacional. Suite completa nao executada.

Central, usando os checkouts locais atualizados:

```powershell
.\venv\Scripts\python.exe -B -m hub.run_backup_integration_tests --hub-root C:\SysvarHub\Backend
```

O runner exige o checkout Hub, instala SQLite em memoria antes de django.setup e
carrega somente os testes integrados por padrao. **Nao usar manage.py test com a
configuracao MySQL DEV para este ensaio.** No Hub, os testes complementares sao
`python -B -m unittest integracao.tests_backup_transmission runtime.tests_recovery`
e o fixture `deploy/windows/tests/backup-integrated-fixture.Tests.ps1` via Pester 3
no Windows PowerShell 5.1. Variavel SYSVAR_BACKUP_TEST_FIXTURE_ROOT e somente para
pasta TEMP exclusiva de dados ficticios; nao apontar para instalacao/backup real.

## PENDENTE DE HOMOLOGACAO — infraestrutura real

Exige autorizacao propria para laboratorio descartavel, nunca MySQL80 DEV:

- MySQL 8.0.x real, ferramentas da mesma versao do FULL, contas e grants minimos,
  instancia exclusiva SysvarHubMySQL/3307 vazia, UUID fisico novo e volume privado.
- Dump consistente com transacoes concorrentes, bloqueio breve de source-data=2,
  binlogs nativos CRC32/rotacao/limites de transacao, contextos temporarios entre
  segmentos, charset/BLOBs e aplicacao de SQL_LOG_BIN somente na sessao.
- Importacao SQL real, CHECK TABLE/FKs, migrations e compatibilidade de schema,
  consistencia comercial/fiscal e conciliacao de vendas/eventos no banco restaurado.
- Build congelado/instalador Inno Setup e servicos Windows reais, identidade do
  servico, ACLs, ambiente SYSVARHUB_PROGRAMDATA e bloqueio ao reinicio/reboot.
- Locks/concorrencia MySQL (SQLite nao reproduz select_for_update), carga/CPU/disco,
  backup de muitos GiB, espaco/queda de energia e disponibilidade do PDV sob carga.
- HTTPS/proxy/Nginx real, ausencia de spool/cache/log de conteudo, certificados,
  timeouts, banda da loja e configuracao privada das chaves historicas.

Nenhuma dessas etapas foi executada implicitamente pelo teste simulado.

## Condicoes para ativacao futura e desastre assistido

1. Revisar os builds/commits corrigidos e autorizar separadamente o laboratorio real.
   Depois da homologacao real, preparar migrations 0012/0013 na Central em janela
   autorizada, storage privado e todas as chaves historicas em gerenciador seguro.
2. Validar conta de backup somente leitura, ferramentas/versionamento, exclusividade,
   binlogs ROW, retencao local, espaco e continuidade. FULL inicial manual validado;
   agendamento/transmissao continuam desativados ate outra autorizacao explicita.
3. Em desastre, isolar/revogar o notebook antigo e usar administrador autorizado da
   Central para escolher ponto VALIDADO e revalidado. Exportar manifesto + arquivos;
   conferir hash oficial por canal confiavel e cada arquivo. Recebimento nao basta.
4. Preparar substituto com o modo explicito do instalador do Prompt 11, nunca o fluxo
   normal que preenche o banco. MySQL exclusivo vazio/3307, credencial temporaria
   sysvarhub_restore protegida, servico Hub parado/desativado e isolamento de rede.
5. Seguir `deploy/windows/RECUPERACAO.md` no Hub: DRY-RUN, autorizacao vinculada ao
   ponto/hash e importacao assistida. Falha preserva dados para analise; nao limpar
   destino, pular segmento, trocar UUID ou retomar no meio de SQL. Novo ensaio exige
   destino vazio preparado pelo suporte e confirmacao explicita.
6. Manter RESTAURADO_BLOQUEADO. Conferir vendas/caixas/filas/eventos com a Central,
   preservando IDs, chaves e payloads originais. Providenciar reautorizacao da mesma
   loja/Hub e revisao/revogacao de credenciais antigas, sem ativacao automatica.
   Retirar bloqueio e iniciar operacao somente com aprovacao explicita do suporte.

Certificados, configuracoes externas, XML/anexos/arquivos fora do MySQL e dados
posteriores ao ultimo backup/incremental nao estao garantidos. Perda de chaves AES
pode tornar o arquivo irrecuperavel. A conciliacao pode retornar conflito legitimo;
nao criar novo evento ou identidade para contorna-lo. Restauracao de banco isolado
nao autoriza dois Hubs concorrentes da mesma loja. Retencao permanece somente
planejamento DRY-RUN; nao foi implementada nem executada exclusao de backups.
