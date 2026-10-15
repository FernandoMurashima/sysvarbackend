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
