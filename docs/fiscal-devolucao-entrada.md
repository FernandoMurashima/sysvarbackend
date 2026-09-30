# Documento fiscal de entrada da devolucao

Quando uma devolucao de venda e finalizada, a Central registra uma `NFeDevolucao`
modelo 55 como obrigacao fiscal de entrada. A devolucao comercial, a entrada de
estoque e o Vale-Troca nao dependem de autorizacao imediata da SEFAZ.

## Loja e origem

A NF-e de devolucao pertence a loja que recebeu fisicamente a mercadoria:
`NFeDevolucao.loja = VendaDevolucao.loja`. A venda original e a NFC-e original
permanecem vinculadas a loja da venda.

Quando existe NFC-e original, `NFeDevolucao.nfce_origem` guarda o vinculo interno
e a chave de acesso e referenciada no XML gerado. Se a NFC-e nao tiver chave
autorizada, nenhuma chave e inventada.

## Numeracao

A serie e o numero sao alocados pela configuracao da loja receptora:
`serie_nfe` e `proximo_numero_nfe`. A alocacao bloqueia a loja com
`select_for_update`, garantindo concorrencia segura por loja.

A unicidade fiscal e por loja, ambiente, modelo, serie e numero.

## Status

O ciclo fiscal usado por `NFeDevolucao` e:

- `DIGITADA`
- `PENDENTE_TRANSMISSAO`
- `EMITINDO`
- `AUTORIZADA`
- `REJEITADA`
- `ERRO_GERACAO`
- `CANCELADA`

Autorizacao nunca e simulada. Sem transmissor real de NF-e modelo 55 para esta
operacao, o documento fica `PENDENTE_TRANSMISSAO`.

## Itens e valores

O documento considera somente os itens efetivamente devolvidos. Quantidade, valor
unitario, desconto e total sao derivados dos itens da venda original e da
quantidade devolvida, preservando o desconto proporcional. Preco atual ou tabela
atual nao sao usados.

## Pendencias e reprocessamento

Se faltar configuracao fiscal essencial, o documento permanece registrado como
`ERRO_GERACAO`, com mensagem funcional em `retorno_mensagem`. Isso nao desfaz
estoque nem Vale-Troca.

O servico `processar_nfe_devolucao(nfe_id)` e idempotente, bloqueia o documento,
ignora documentos ja autorizados e pode ser reutilizado por endpoint, worker ou
rotina operacional futura. Emissao fiscal offline da devolucao fica para etapa
posterior.
