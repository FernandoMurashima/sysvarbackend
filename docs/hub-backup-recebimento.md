# Recebimento privado de backups MySQL do Hub

Implementacao preparada no Prompt 7. **Desativada por padrao. Nenhuma transmissao
real ou migration operacional foi executada.** Nao existe download, restauracao,
retencao automatica ou aprovacao automatica para recuperacao.

## Implantacao futura autorizada

1. Instalar `requirements.txt` (inclui `cryptography==50.0.2`).
2. Aplicar a migration `hub/0013_backup_recebimento_seguro.py` em janela autorizada.
   A 0012 permanece inalterada. O primeiro passo da 0013 verifica comprimentos
   existentes e interrompe se houver chave acima de 255; nao trunca nem reescreve
   dados. A chave gerada tem no maximo **249 caracteres** (IDs BigAutoField positivos
   de ate 19 digitos, quatro UUIDs hex e basename incremental).
3. Provisionar `HUB_BACKUP_ROOT` fora do projeto, media, static e todos os roots/alias
   publicos do Nginx. Informar esses roots adicionais em `HUB_BACKUP_PUBLIC_ROOTS`
   (lista de caminhos separados por virgulas). Nao criar alias/location de arquivos.
   O codigo nao provisiona automaticamente a raiz operacional.
4. No POSIX, raiz pertencente ao usuario do processo e modo 0700. No Windows, DACL
   exclusiva de SYSTEM, Administradores e identidade Windows do processo. Novas
   subpastas/arquivos recebem 0700/0600 ou DACL protegida equivalente. Links simbolicos,
   junctions/reparse points e hardlinks de escrita sao rejeitados. O filesystem deve
   suportar hardlinks locais (por exemplo, NTFS/ext4); staging e destino ficam juntos.
5. Gerar no gerenciador de segredos uma chave aleatoria de **32 bytes**, codificada
   em base64. Injetar `HUB_BACKUP_ENCRYPTION_KEYS` como JSON `{id: base64}` e selecionar
   `HUB_BACKUP_ACTIVE_KEY_ID`. Nao usar SECRET_KEY do Django, chave fixa, exemplo
   literal ou arquivo de chave dentro do storage. Nao versionar nem imprimir chaves.
   Sem chave valida o recebimento falha fechado (503); habilitado com configuracao
   invalida tambem gera `hub.E002` no system check.
6. Configurar HTTPS obrigatorio e garantir `request.is_secure()` verdadeiro pelo
   servidor/proxy confiavel. Nao confiar em X-Forwarded-Proto de clientes diretos.
   Nos locais da API de backup, desabilitar spool de plaintext pelo proxy:
   `proxy_request_buffering off`, `proxy_http_version 1.1` e
   `client_body_in_file_only off`. Nao usar middleware que registre/copie corpos
   ou Authorization. Ajustar `client_max_body_size`, `client_body_timeout` e
   timeouts do proxy/WSGI aos limites abaixo. A API usa stream octet-stream, sem
   multipart nem upload handlers que escrevam temporarios em claro.
7. Somente apos revisao de chaves, permissoes, migrations e proxy, definir
   `HUB_BACKUP_RECEBIMENTO_ATIVO=True`. Isso habilita a API da Central; nao configura
   nem ativa um transmissor no Hub. O lado Hub ainda precisa implementar o contrato.

Limites configuraveis (padroes):

| Configuracao | Padrao |
| --- | --- |
| HUB_BACKUP_PART_BYTES | 4 MiB; limite absoluto 16 MiB |
| HUB_BACKUP_MAX_FILE_BYTES | 512 GiB; limite absoluto 1 TiB |
| HUB_BACKUP_MAX_MANIFEST_BYTES | 1 MiB |
| HUB_BACKUP_MIN_FREE_BYTES | 512 MiB de margem alem da escrita |
| HUB_BACKUP_IDLE_SECONDS | 86400 (24 horas) |
| HUB_BACKUP_FINALIZE_SECONDS | 3600 (uma hora) |

Cada arquivo aceita no maximo um milhao de partes. As partes tem o tamanho fixo
negociado; somente a ultima pode ser menor. Configurar limites e concorrencia de
conexoes no proxy e capacidade do filesystem; nao ha quota/limpeza automatica.
Nao reduzir o tamanho permitido das partes enquanto houver tentativas abertas.
Finalizar faz leitura completa para conferir integridade e pode levar tempo.
Nao manter transacao SQL aberta durante essa leitura; ajustar timeout do WSGI/proxy.

## Contrato HTTP (somente Authorization: Hub <token>)

Base: `/api/hub/backups/`. Nao aceita JWT administrativo, sessao de navegador,
empresa/loja de payload como autoridade, caminho livre ou nome de arquivo livre.
Respostas somente JSON e `Cache-Control: no-store`.

- `POST iniciar/`: JSON com apenas `manifesto`, uma **string com os bytes UTF-8
  originais do manifest.json**. Preservar whitespace, quebras de linha, BOM e ordem
  originais: o hash do manifesto e calculado pela Central, nao sobre JSON
  reserializado. Sao aceitos FULL/INCREMENTAL versao 1 dos Prompts 3/4; manifesto
  concluido `status=VALIDO` na origem nao significa validacao na Central.
- `PUT <backup_uuid>/partes/<indice>/`: corpo binario `application/octet-stream`,
  Content-Length exato e headers `X-Upload-Id`, `X-Upload-Offset`, `X-Chunk-SHA256`.
  Indices comecam em zero; offset = indice * tamanho_parte. SHA-256 hexadecimal
  minusculo do corpo dessa parte. Nao enviar JSON, base64 ou multipart nesta rota.
- `GET <backup_uuid>/progresso/`: retorna upload_uuid, tentativa, offset confirmado,
  proxima_parte, tamanho_parte, tamanho_total, expira_em, codigo de erro e estados.
- `POST <backup_uuid>/finalizar/`: JSON com apenas `upload_uuid`. Confere tags de
  todos os registros, tamanho total, SHA-256, manifesto e predecessor. Nao executa
  SQL nem descompacta o dump. Retorna RECEBIDO com validacao ainda PENDENTE; a
  validacao independente da recuperabilidade pertence a etapa posterior.

O Hub e resolvido por HubTokenAuthentication; IDs do manifesto somente corroboram
loja/empresa do cadastro oficial. UUID da instalacao deve corresponder ao Hub atual.
IDs FULL/predecessor sao UUIDs de backups, resolvidos dentro desse escopo. O hash
predecessor/full e do manifesto original, distinto do SHA do gzip. Sequencia,
coordenadas, identidade MySQL, ancoras e fontes de rotacao sao validadas. O predecessor
precisa estar RECEBIDO e sua ancora autenticada deve coincidir com o inicio novo.
Nenhum arquivo e servido publicamente ou armazenado como BLOB no MySQL.

## Retomada e falhas

Repetir iniciar com o mesmo manifesto recupera a mesma tentativa e offset confirmado.
Repetir uma parte confirmada com os mesmos bytes e idempotente; conteudo divergente,
salto de indice ou upload_uuid antigo resulta em conflito. Uma trava de arquivo por
backup impede processos/requisicoes concorrentes; constraints SQL protegem indices
e tentativas. Progresso e registrado somente depois da escrita criptografada e fsync.
Uma cauda temporaria sem checkpoint e truncada antes do retry, preservando as partes
confirmadas. A trava e liberada tambem no encerramento do processo.

Timeout de inatividade retorna 408. Repetir iniciar renova a janela da mesma tentativa,
sem perder partes. Falha de conexao, falta de espaco ou falha temporaria de disco nao
avanca o offset. SHA total incorreto/corrupcao torna a tentativa ERRO; iniciar de novo
cria nova tentativa com novo upload_uuid/sal e offset zero, preservando a tentativa e
staging antigos. Isso reinicia somente o envio desse arquivo, sem trocar a cadeia,
identificador do backup ou manifesto. Nao existe limpeza automatica de staging.

A publicacao e um hardlink atomico exclusivo no mesmo filesystem, sem overwrite,
apos verificacao; depois e removido somente o staging dessa tentativa. Se o processo
parar entre publicacao e commit SQL, finalizar novamente autentica o arquivo ja
publicado e conclui o registro sem reescrever o arquivo. Arquivo inesperado/corrompido
no destino exige suporte e e preservado. Corrupcao detectada num arquivo ja recebido
sinaliza CADEIA_INTERROMPIDA; uma falha temporaria nao apaga nem reclassifica arquivo
integro. Nenhum erro de disco remove backups anteriores.

Status funcionais: 400 manifesto/headers invalidos; 401 credencial; 403 HTTPS ausente;
404 backup fora do escopo; 409 conflito/incompleto; 408 timeout; 413 limites; 415 tipo
de corpo; 422 checksum; 503 configuracao/servico; 507 espaco. Logs contem codigos e
UUID da tentativa, sem corpo, SQL, chave, token ou mensagem nativa de erro.

## Criptografia em repouso

Container `SYSVAR-HUB-BACKUP` versao 1, algoritmo `AES256_GCM_CHUNKS_V1`.
Os bytes fisicos na chave `.sql.gz` sao **container criptografado**, nao um gzip
aberto. O header identifica versao, key_id, sal, upload/backup, hashes e tamanhos;
nao contem chave, conteudo de vendas ou corpo do manifesto.

HKDF-SHA256 deriva uma chave de 256 bits por tentativa, com sal aleatorio de 32 bytes
mais contexto/UUID. Cada registro usa AES-GCM com nonce aleatorio de 96 bits e tag
completa. AAD vincula hash do header e indice (-1 para manifesto, 0..N-1 para partes).
Manifesto e gzip nunca sao gravados em claro. Registros sao verificados por inteiro
antes de usar plaintext. Processa um registro por vez (ate 16 MiB de plaintext),
com seus buffers criptograficos e metadados de ate 1 MiB; memoria nao cresce com o
tamanho total do backup. Tamanho, ordem e SHA global detectam truncamento/reordenacao.

Manter chaves historicas no gerenciador de segredos durante toda a vida dos backups.
Rotacao muda somente o key_id ativo para tentativas novas; tentativas/arquivos antigos
continuam exigindo sua chave original. Remover essa chave impede verificacao e futura
recuperacao; nao existe fallback nem geracao automatica de chave. Nao realizar testes
reais de recuperacao nesta etapa.

Referencias: [AESGCM/HKDF da cryptography](https://cryptography.io/en/stable/hazmat/primitives/aead/),
[uploads e temporarios Django](https://docs.djangoproject.com/en/4.2/topics/http/file-uploads/).

## Validacao executada

Testes direcionados usam SQLite em memoria, criptografia real e arquivos ficticios
em diretorios temporarios com ACL privada. O bootstrap ignora migrations legadas
exclusivas de MySQL; 0012/0013 sao aplicadas e revertidas no banco isolado. Testes do
verificador MySQL confirmam ausencia do W003 sem conectar ao MySQL operacional.
Homologacao de infraestrutura, volume/latencia e recuperacao real permanece futura.


## Prompt 9 — integridade independente e retencao DRY-RUN

Implementacao preparada, sem execucao contra backups operacionais, sem exclusao e
sem agendamento. A API de recebimento continua desativada por padrao. Nenhuma
migration nova e necessaria para esta etapa; a estrutura dos Prompts 6/7 e reutilizada.

Comando futuro do suporte, sempre somente diagnostico (IDs do escopo autorizado):

```powershell
.\venv\Scripts\python.exe manage.py auditar_backups_hub --empresa 1 --loja 1 --hub 1 --dry-run
```

`--empresa`, `--loja` e `--hub` sao obrigatorios e devem corresponder ao cadastro
oficial. Nao existe enumeracao global, parametro de caminho, exclusao, `--apply` ou
registro de validacao pelo comando. `--dry-run` e explicito opcional: o comando
permanece DRY-RUN quando omitido. Requer as migrations anteriores ja implantadas,
storage privado e chaves historicas disponiveis; nao exige ativar recebimento.

A auditoria reutiliza o verificador do container: header, AES-GCM/HKDF, manifesto
UTF-8 original, hashes das partes, SHA-256 global, tamanho, ordem e fim do arquivo.
Confere metadados imutaveis, empresa/loja/Hub/instalacao/MySQL, FULL, predecessor,
sequencia, coordenadas e ancoras autenticadas. Processa uma parte de ate 16 MiB por
vez, com buffers criptograficos associados; manifesto ate 1 MiB. Metadados do plano
crescem com a quantidade de backups, e somente a ancora de cada ponto e retida para
conferir sucessores. Nao descompacta/executa SQL nem realiza restauracao.

Usa a trava de upload existente sem criar diretorios ou novos arquivos de trava.
Recebimento em andamento, trava ausente/ocupada, chave historica ausente, erro de
storage ou alteracao concorrente impedem aprovar integridade/retencao. Chave ausente
produz INCONCLUSIVO, sem declarar corrupcao comprovada. Um trecho anterior integro
pode continuar valido quando um incremental posterior estiver corrompido, mas os
sucessores ficam bloqueados e a cadeia inteira e preservada pelo plano.
Instalacoes antigas sao conferidas contra sua identidade imutavel registrada na
Central, mesmo apos novo pareamento; nunca sao misturadas com a instalacao nova.

O resultado JSON distingue `integridade` (resultado desta leitura) de
`estado_validacao` (estado persistido). Assim, um recebido PENDENTE pode ter prova
fisica VALIDO no DRY-RUN, mas continua PENDENTE no banco e e preservado. Tambem
inclui UUID, chave logica do arquivo, geracao, sequencia, motivo, bloqueio da cadeia
e acao MANTER/ELEGIVEL_REMOCAO. ELEGIVEL_REMOCAO e exclusivamente uma simulacao:
nenhum arquivo, tentativa ou registro e removido.

O servico interno `auditar_backups_hub(..., registrar=True)` prepara o registro
explicito de VALIDO/INVALIDO/CADEIA_INTERROMPIDA e `verificado_em`, somente apos
verificar os arquivos e todos os ancestrais do ponto. Nao e acionado pela API,
comando ou agendador. Seu uso operacional exige autorizacao especifica futura.
Antes do commit dos estados, trava o Hub e confere novamente cadastro, metadados,
tentativas e identidade dos arquivos. Divergencia aborta a transacao inteira.
Nesta implementacao esse caminho foi exercitado somente em banco isolado de testes.

Politica temporal:

- `HUB_BACKUP_RETENTION_TIME_ZONE` usa por padrao `TIME_ZONE`, atualmente
  `America/Sao_Paulo`. Valor IANA invalido falha fechado (`hub.E003`).
- A janela usa dias civis do instante da auditoria nesse fuso, comparando a data
  de geracao do manifesto: dia atual e anterior, com todos os pontos desses dias.
- FULLs e incrementais anteriores necessarios a esses pontos sao preservados por
  dependencia, inclusive quando o FULL tem mais de uma semana.
- Datas futuras tambem sao preservadas: relogio divergente nao autoriza remocao.
  Uma cadeia que contem ponto protegido e mantida inteira, inclusive se o relogio
  retroceder num incremental posterior; nao se planeja remocao de seu sufixo.
- A ultima cadeia integralmente verificada e registrada VALIDO de **cada
  instalacao/MySQL** e mantida, mesmo antiga ou apos renovacao semanal/pareamento.
- Pendentes (de recebimento ou validacao), incompletos e qualquer cadeia com duvida
  de validade ficam integralmente preservados. O plano nao considera somente
  estados VALIDO antigos: exige nova conferencia fisica nesta execucao.
- Somente cadeias antigas independentes, fora da janela, inteiramente recebidas e
  validas, e com alternativa integra preservada podem aparecer como elegiveis.

**VALIDO significa integridade criptografica e continuidade verificadas. Nao
significa restauracao homologada.** `restauracao_homologada` permanece falso no
relatorio; nenhuma politica de exclusao efetiva foi implementada. Homologacao de
restauracao, carga e infraestrutura continua pendente para as etapas autorizadas.


## Prompt 10 — selecao e pacote privado de recuperacao

Preparado e **desativado por padrao**, sem downloads operacionais, restauracao ou
exclusao. Nao executa SQL e nao modifica o Hub. Integridade revalidada nao significa
restauracao homologada. A listagem/seleção e o download sao exclusivamente
administrativos: nao usam HubTokenAuthentication nem dependem de notebook/Hub ativo.

### Configuracao futura autorizada

- Manter implantadas as migrations anteriores do controle/recebimento (nenhuma
  migration nova nesta etapa); nao foram aplicadas operacionalmente aqui.
- Provisionar o storage privado e todas as chaves historicas segundo as secoes
  anteriores. A recuperacao nao exige ativar recebimento ou transmissao.
- Usar SECRET_KEY forte da Central para assinar autorizacoes temporarias. Nao ha
  chave fixa adicional; o segredo de assinatura nao e enviado ao suporte.
- Autorizar os usuarios de suporte pelo controle existente: administrador do Hub
  (superusuario ou papeis Admin/Diretor), permissao Django `hub.view_hubbackup`
  para consultar, e tambem `hub.change_hubbackup` para autorizar/baixar. As
  permissoes padrao do model ja existem; nao ha novo papel ou concessao automatica.
  Escopo de empresa/loja e perfis existentes continuam valendo. Superusuario
  vinculado a empresa tambem respeita a restricao existente do administrador Hub.
- Os pontos e todos os ancestrais precisam estar RECEBIDO **e** registrados VALIDO
  pela validacao independente do Prompt 9. Este fluxo nao promove PENDENTE por
  simples recebimento, nem altera estados silenciosamente.
- Configurar HTTPS valido e proxy confiavel com `request.is_secure()` verdadeiro.
  Nao registrar Authorization, X-Backup-Recovery-Token, corpos ou respostas dessas
  rotas. No proxy, desabilitar cache/buffering de resposta e transformacoes dos
  arquivos gzip (`proxy_cache off`, `proxy_buffering off`, gzip/gunzip desativados
  nessas rotas); nao criar alias publico de backup. Preservar Content-Length.
- Ajustar timeout de proxy/WSGI e `HUB_BACKUP_FINALIZE_SECONDS` ao volume: autorizacao
  e cada download fazem revalidacao fisica de toda a ancestralidade selecionada.
  Isso pode exigir I/O significativo; homologacao de carga/infraestrutura e futura.
- Somente depois da revisao autorizada, configurar `HUB_BACKUP_RECOVERY_ATIVO=True`.
  Sem isso as rotas retornam 503. `HUB_BACKUP_RECOVERY_AUTH_SECONDS` padrao 900
  segundos, configuravel de 60 ate 86400, e a janela para admitir downloads.
  `manage.py check` falha com hub.E004 se a configuracao habilitada estiver invalida.

### API administrativa

Base `/api/hub/backups/recuperacao/<empresa_id>/<loja_id>/`.
Autenticacao **Authorization: Token <sessao administrativa vigente>**, a mesma da
Central; tokens Hub nao sao aceitos. Escopo e permissoes sao conferidos no servidor
em cada requisicao, inclusive depois de emitir autorizacao.

1. `GET pontos/`: lista data/hora ISO com fuso, tipo, sequencia, instalacao, MySQL,
   recebimento e validacao. Filtros opcionais instalacao_uuid/servidor_mysql_uuid;
   paginacao `offset` (padrao 0) e `limit` (padrao 100, maximo 200). `selecionavel`
   avalia os metadados, com `revalidacao_obrigatoria=true`; nao atesta leitura fisica.
2. `POST autorizar/`: JSON somente com `ponto_uuid`, `instalacao_uuid`,
   `servidor_mysql_uuid`, `autorizar: true` e `motivo` nao vazio (ate 500 caracteres).
   Revalida fisicamente o FULL e exatamente os predecessores ate o ponto escolhido.
   Corrupcao de um incremental posterior nao impede selecionar um ponto anterior.
   Retorna manifesto do pacote, seu SHA-256, `autorizacao_token` assinado e validade.
   Auditoria duravel e obrigatoria antes de liberar; registra operador/escopo/ponto e
   hash do motivo, sem texto livre, token, chaves, manifesto SQL ou conteudo do backup.
3. `GET arquivos/<backup_uuid>/`: requer tambem header **X-Backup-Recovery-Token**
   com a autorizacao emitida. Nao colocar esse valor em URL/query string. Somente
   arquivos do pacote autorizado podem ser baixados. A autorizacao e vinculada ao
   usuario, empresa, loja, Hub, instalacao, MySQL, ponto e SHA do manifesto completo;
   nao funciona sozinha, para outro usuario ou em outro escopo.

O pacote e um manifesto JSON mais seus arquivos individuais, evitando ZIP gigante
ou spool em disco. O manifesto informa FULL, incrementais e ordem de aplicacao
(0=FULL), nomes seguros, UUIDs, datas, formatos, compressao, tamanhos, SHA-256 dos
gzip e dos manifestos originais, predecessores e coordenadas/ancoras inicial/final.
`download_url` e caminho relativo privado, sem token. O SHA do pacote usa JSON ASCII
com chaves ordenadas e separadores `,` e `:` (sem espacos); o token assinado vincula
o hash, identificador e data do pacote.

Downloads retornam gzip SQL original descriptografado por streaming, Content-Length,
X-Backup-SHA256 e Content-Disposition attachment. Dados nao sao executados nem
escritos em temporarios em claro na Central. Os containers AES-GCM originais sao
preservados. Partes criptografadas tem ate 16 MiB, com buffers associados; a resposta
entrega blocos de ate 64 KiB. AES-GCM e SHA sao verificados novamente no fluxo; a
ultima parte so e liberada depois do SHA global e fim do arquivo. Alteracoes do
arquivo durante a transmissao interrompem o fluxo. A trava e liberada mesmo se a
conexao fechar sem consumir a resposta.

As respostas, inclusive erros, usam no-store/no-cache, Vary por autenticacao e
header de autorizacao, e nosniff; downloads sinalizam X-Accel-Buffering=no. Interrupcao
nao altera originais nem marca pacote restaurado. Nao ha Range/retomada parcial
(416): repetir o arquivo completo com a mesma autorizacao enquanto valida, ou obter
nova autorizacao explicita. A expiracao e conferida antes/depois da revalidacao para
admitir o download; um fluxo ja admitido pode terminar alem da janela do token,
sujeito aos timeouts de verificacao/transporte. Auditoria distingue inicio, fim do
fluxo no servidor e interrupcao; nao representa confirmacao do cliente. O suporte
deve conferir tamanho e SHA de **todos** os arquivos contra o manifesto antes de
considerar o pacote completo. A homologacao real de restauracao continua pendente.

Nenhuma rota publica, servico, agendamento, exclusao ou habilitacao operacional
foi criada/executada. A implementacao foi testada apenas com banco isolado e
containers/arquivos ficticios, incluindo permissoes, corrupcao e interrupcoes.
