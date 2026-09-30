# Vale-Troca Online no PDV

O Vale-Troca online e oficial tem a Central como autoridade de saldo. O Hub consulta e reserva pela API autenticada de Hub; o frontend nunca chama endpoints web/JWT da Central diretamente.

O uso exige venda com cliente identificado e bloqueia Consumidor Final. A Central valida tenant, cliente, status aberto, validade e saldo disponivel. O saldo disponivel e calculado como saldo contabil do Vale menos reservas em status `RESERVADA`.

## Documento comercial

O documento comercial do Vale-Troca segue exatamente `VT` + 7 digitos numericos, por exemplo `VT0000001`. A sequencia e por empresa/tenant, nao por loja. A loja de origem continua registrada no Vale, mas nao faz parte do numero comercial.

O ultimo numero permitido e `VT9999999`. Ao esgotar a faixa, a Central deve bloquear novas emissoes com erro funcional claro, sem reiniciar nem reutilizar a sequencia.

Identificador tecnico nao e documento comercial. UUIDs, ids de idempotencia, ids de sincronizacao, chaves de integracao e rastreadores internos podem existir, mas nao devem ser apresentados ao operador ou cliente como numero comercial. Como revisao estrutural futura, aplicar a mesma diretriz a vendas/cupons, devolucoes, contas a receber, contas a pagar e demais documentos tecnicos `HUB-*`.

Reservas sao criadas em lote por venda para evitar uso simultaneo em lojas diferentes. A chave idempotente e `hub + venda_uuid + operacao_uuid`; retries reaproveitam a mesma reserva. O consumo oficial acontece ao processar `VENDA_FINALIZADA`, criando `ValeTrocaMovimento` `USO`, reduzindo o saldo e marcando o Vale como `ABERTO` quando parcial ou `USADO` quando total.

Fiscalmente, o Hub envia Vale-Troca como `tPag 05` Credito Loja. Financeiro e caixa tratam o instrumento como credito da cliente: ele nao gera numerario e nao cria novo contas a receber para a parcela paga com o proprio credito.

A contingencia offline de Vale oficial da Central nao faz parte desta etapa. O espelho local `ValeTrocaHub`/`ValeTrocaMovimentoHub` permanece preservado para vales locais ainda nao sincronizados.
