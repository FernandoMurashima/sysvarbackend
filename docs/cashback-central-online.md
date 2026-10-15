# Cashback Central: contrato de autorização e implantação

Implementação Central preparada para o Prompt 5. A integração do Hub e a homologação de ponta a ponta ainda não foram executadas. O planejamento P9 de 13/10/2026 descreve LOCAL/CENTRAL anteriores; a decisão aprovada no Prompt 4B final substitui essa permissão de novo uso LOCAL.

## Ativação explícita

`CASHBACK_CENTRAL_FEFO_ATIVO` é `False` por padrão. Não modificar o ambiente operacional nesta entrega. Com a opção desativada, novos usos/reservas são recusados, sem fallback. Geração de Cashback por venda sincronizada, leitura histórica, cancelamento de reserva e retry de eventos já processados continuam disponíveis.

A implantação exige autorização separada para aplicar as migrations **0038** e **0039**, atualização do Hub ao contrato abaixo e verificação com `python manage.py verificar_cashback_online`. Esse comando só lê o esquema e aponta clientes com movimentos/reservas pendentes; não ativa, migra ou reconcilia dados. Só depois habilitar explicitamente `CASHBACK_CENTRAL_FEFO_ATIVO=True` no ambiente e reiniciar os processos. Mesmo se a opção for habilitada antes das migrations, novas operações são bloqueadas com erro funcional.

0039 cria somente tabelas aditivas para o contexto autorizado e a origem dos créditos reservados. Não altera registros antigos. A aplicação das migrations não ativa o fluxo.

## O que o Prompt 5 precisa enviar

Continuar usando a autenticação de Hub e os endpoints existentes. Nunca utilizar o saldo do snapshot para autorizar pagamento.

1. Consultar `GET /api/hub/cashback/saldo/?cliente_id=<id Central>`. `saldo_oficial` considera créditos válidos e consumos rastreados; `saldo_disponivel` desconta reservas válidas. Histórico sem origem comprovada retorna erro funcional, sem inventar saldo.
2. Enviar `POST /api/hub/cashback/reservar-venda/` com:

```json
{
  "venda_uuid": "UUID estável da venda local",
  "operacao_uuid": "UUID estável da operação de pagamento",
  "cliente_id": 123,
  "valor": "30.00",
  "total_venda": "100.00"
}
```

`total_venda` é obrigatório para nova autorização, inclusive com limite configurado em 100%. É o total comercial final dos itens e descontos, não o saldo pendente ou valor recebido. Também é aceito o alias anterior `cliente_retaguarda_id`. Valores monetários devem ser finitos, positivos, com no máximo duas casas decimais.

A resposta mantém `reserva` com os campos anteriores (`id`, `venda_uuid`, `operacao_uuid`, `cliente_id`, `valor`, `status`) e acrescenta `autorizacao_versao: 2`, `total_venda` e `limite_uso`. Só registrar o pagamento no Hub depois de resposta real confirmada. Timeout deve repetir os mesmos UUIDs, cliente, valor e total. Não gerar nova operação para contornar conflito.

3. Em `VENDA_FINALIZADA`, cada pagamento Cashback deve conter:

```json
{
  "codigo": "CBK",
  "tipo": "CASHBACK",
  "valor": "30.00",
  "operacao_uuid": "mesmo UUID da autorização",
  "cashback_reserva_id": 456
}
```

`reserva_id` continua aceito como alias. O envelope e o payload da venda mantêm os contratos atuais: `venda_uuid`, `cliente_retaguarda_id`, itens, descontos, total e demais pagamentos. O total recalculado pela Central precisa corresponder ao total autorizado. Uma reserva financia um único pagamento; o somatório das operações da venda respeita o limite percentual. Cashback não gera troco. Não existe autorização de uso por `origem: LOCAL`, data antiga ou saldo armazenado.

4. Em cancelamento/troca de cliente/alteração de total, cancelar a operação anterior online e criar outra com novo UUID. Enviar `POST /api/hub/cashback/cancelar-reserva/` com `reserva_id`, `venda_uuid` e `operacao_uuid` corretos. Todos os identificadores enviados são validados; cancelamento somente por ID permanece compatível e restrito ao Hub autenticado. Reserva consumida não pode ser cancelada. A consulta por venda mantém a resposta histórica; para confirmar autorização de versão 2 após retry, repetir a chamada de autorização com os mesmos dados.

## FEFO, validade e transações

A autorização bloqueia cliente/Hub, seleciona créditos por vencimento (sem validade por último) e registra suas parcelas. O consumo utiliza exatamente essas origens, criando um débito e suas alocações por reserva na mesma transação da venda. Retry não duplica reserva, débito ou alocação. Qualquer divergência/falha reverte pagamentos, venda, débito e status de reserva; o evento pode permanecer em ERRO para retry.

Crédito vencido não pode sustentar reserva ainda não consumida. Nesse caso a sincronização é recusada e exige cancelamento/nova autorização; o valor vencido reservado não bloqueia créditos posteriores. Um consumo já realizado nunca é descontado novamente de créditos novos. Estornos existentes mantêm seus valores e cálculos; vínculos são acrescentados apenas aos consumos comprovados da venda e não renovam o vencimento original.

## Histórico e pendências

Eventos LOCAL já PROCESSADOS/mapeados na Central mantêm os registros e retries idempotentes. Reapresentação com outra identidade exige payload idêntico ao histórico processado; pagamentos divergentes não são confirmados apenas porque o UUID da venda já possui mapeamento. Evento desconhecido ou apenas recebido com ERRO não comprova autorização e é recusado se não possuir reserva válida. Nenhum corte baseado em data informada pelo Hub é utilizado.

Débitos e restituições antigos sem alocação comprovada ficam preservados e impedem novos usos daquele cliente até conciliação explícita. Não há backfill ou alocação presumida. Reservas antigas ainda não consumidas podem ser canceladas pelo contrato anterior ou reautorizadas online com o total atual e créditos disponíveis; isso não reclassifica débitos históricos. A geração de novas vendas offline continua ocorrendo na sincronização, mesmo quando o uso estiver bloqueado.

Dependências operacionais: aplicação autorizada das migrations, Prompt 5 no Hub, conciliação individual quando necessária e homologação integrada. Nenhuma delas é executada por este documento ou por `verificar_cashback_online`.
