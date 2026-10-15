"""Autorização Central com créditos reservados e ativação explícita."""
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import connection, models, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone

from cadastros.models import Cliente
from .models import (
    CashbackAutorizacao, CashbackConfig, CashbackConsumo, CashbackConsumoCredito,
    CashbackMovimento, CashbackReserva, CashbackReservaCredito, CashbackEstornoCredito,
)
from .services import (
    CashbackErro, CashbackNaoEncontrado, cliente_participa_cashback, money,
    serializar_cashback_reserva, _uuid_cashback,
)
from .cashback_creditos import CashbackHistoricoPendente, _creditos, _validar_historico, _valor as _valor_cashback

ZERO = Decimal("0.00")


def exigir_controle_creditos(*, exigir_ativacao=True):
    if exigir_ativacao and not getattr(settings, "CASHBACK_CENTRAL_FEFO_ATIVO", False):
        raise CashbackErro("Utilização de Cashback indisponível até a ativação explícita do controle Central.")
    necessarias = {
        ("financeiro", "0038_controle_creditos_cashback"),
        ("financeiro", "0039_autorizacao_cashback_online"),
    }
    tabelas = {m._meta.db_table for m in (
        CashbackConsumo, CashbackConsumoCredito, CashbackEstornoCredito, CashbackAutorizacao, CashbackReservaCredito,
    )}
    if (not necessarias.issubset(MigrationRecorder(connection).applied_migrations())
            or not tabelas.issubset(connection.introspection.table_names())):
        raise CashbackErro("Cashback indisponível: migrations 0038 e 0039 pendentes de implantação autorizada.")


def _validar_reservas_historicas(cliente, excluir=None):
    qs = CashbackReserva.objects.filter(cliente=cliente, status=CashbackReserva.STATUS_RESERVADA)
    if excluir:
        qs = qs.exclude(pk__in=excluir)
    if qs.filter(autorizacao_oficial__isnull=True).exists():
        raise CashbackHistoricoPendente("Reserva anterior sem autorização completa; cancelar ou reautorizar online com o contexto da venda.")


def creditos_disponiveis(cliente, empresa, excluir_reserva_ids=None, bloquear=False):
    _validar_historico(empresa, cliente)
    _validar_reservas_historicas(cliente, excluir_reserva_ids)
    qs = _creditos(empresa, cliente, timezone.localdate())
    if bloquear:
        qs = qs.select_for_update()
    creditos = list(qs)
    reservas = dict(CashbackReservaCredito.objects.filter(
        autorizacao__reserva__empresa=empresa, autorizacao__reserva__cliente=cliente,
        autorizacao__reserva__status=CashbackReserva.STATUS_RESERVADA,
        credito_id__in=[c.pk for c in creditos],
    ).exclude(autorizacao__reserva_id__in=excluir_reserva_ids or []).values("credito_id").annotate(
        total=models.Sum("valor"),
    ).values_list("credito_id", "total"))
    return [(c, max(ZERO, c.valor - c.consumido + c.restituido - c.cancelado - reservas.get(c.pk, ZERO))) for c in creditos]


def saldo_disponivel(cliente_id, empresa, excluir_reserva_ids=None):
    exigir_controle_creditos()
    cliente = Cliente.objects.filter(pk=cliente_id, empresa=empresa).first()
    if not cliente:
        raise CashbackNaoEncontrado("Cliente não encontrado para Cashback.")
    return money(sum((valor for _, valor in creditos_disponiveis(cliente, empresa, excluir_reserva_ids)), ZERO))


@transaction.atomic
def autorizar(hub, venda_uuid, operacao_uuid, cliente_id, valor, total_venda):
    exigir_controle_creditos()
    venda_uuid = _uuid_cashback(venda_uuid, "venda_uuid")
    operacao_uuid = _uuid_cashback(operacao_uuid, "operacao_uuid")
    valor = _valor_cashback(valor)
    if total_venda is None:
        raise CashbackErro("Autorização de Cashback exige total_venda no contrato online.")
    total = _valor_cashback(total_venda)
    try:
        cliente_id = int(cliente_id)
    except (TypeError, ValueError) as exc:
        raise CashbackErro("Cashback exige cliente identificado.") from exc
    empresa = hub.loja.empresa
    cliente = Cliente.objects.select_for_update().filter(pk=cliente_id, empresa=empresa).first()
    if not cliente:
        raise CashbackNaoEncontrado("Cliente não encontrado para Cashback.")
    # Serializa também operações da mesma venda com clientes divergentes.
    type(hub).objects.select_for_update().get(pk=hub.pk)
    existente = CashbackReserva.objects.select_for_update().filter(
        hub=hub, venda_uuid=venda_uuid, operacao_uuid=operacao_uuid,
    ).first()
    if existente:
        if existente.cliente_id != cliente.pk or existente.empresa_id != empresa.pk or money(existente.valor) != valor:
            raise CashbackErro("Operação de Cashback já reservada com dados diferentes.")
        if existente.status == CashbackReserva.STATUS_CANCELADA:
            raise CashbackErro("Reserva de Cashback cancelada para esta operação.")
        autorizacao = CashbackAutorizacao.objects.filter(reserva=existente).first()
        if autorizacao:
            if autorizacao.total_venda != total:
                raise CashbackErro("Total da venda diverge da autorização original de Cashback.")
            if existente.status == CashbackReserva.STATUS_RESERVADA:
                disponiveis = dict((c.pk, v) for c, v in creditos_disponiveis(cliente, empresa, [existente.pk]))
                origens = list(autorizacao.creditos.all())
                if sum((o.valor for o in origens), ZERO) != valor or any(o.valor > disponiveis.get(o.credito_id, ZERO) for o in origens):
                    raise CashbackErro("Créditos da reserva expirados ou indisponíveis; cancelar e reautorizar online.")
            return _resposta(existente, autorizacao)
        if existente.status != CashbackReserva.STATUS_RESERVADA:
            raise CashbackHistoricoPendente("Consumo anterior sem origem comprovada; conciliação explícita necessária.")
    from hub.models import HubVendaMapeamento
    if HubVendaMapeamento.objects.filter(hub=hub, venda_uuid=venda_uuid).exists():
        raise CashbackErro("Venda já sincronizada; não é possível criar nova autorização de Cashback.")
    config = CashbackConfig.regra_ativa(empresa)
    if not config or not cliente_participa_cashback(cliente, config):
        raise CashbackErro("Cliente ou regra de Cashback não elegível.")
    if valor < money(config.valor_minimo_uso):
        raise CashbackErro("O valor de cashback usado é menor que o mínimo configurado.")
    outras = CashbackReserva.objects.filter(hub=hub, venda_uuid=venda_uuid).exclude(status=CashbackReserva.STATUS_CANCELADA)
    if existente:
        outras = outras.exclude(pk=existente.pk)
    if outras.exclude(cliente=cliente).exists() or CashbackAutorizacao.objects.filter(reserva__in=outras).exclude(total_venda=total).exists():
        raise CashbackErro("Cliente ou total divergente para a mesma venda de Cashback.")
    usado = money(outras.aggregate(total=models.Sum("valor"))["total"] or ZERO)
    limite = money(min(total, total * Decimal(config.limite_uso_percentual or 0) / Decimal("100")))
    if usado + valor > limite:
        raise CashbackErro("O cashback usado ultrapassa o limite permitido para a venda.")
    parcelas = []
    restante = valor
    for credito, disponivel in creditos_disponiveis(cliente, empresa, [existente.pk] if existente else None, bloquear=True):
        parcela = min(restante, disponivel)
        if parcela > ZERO:
            parcelas.append((credito, parcela))
            restante -= parcela
        if restante == ZERO:
            break
    if restante > ZERO:
        raise CashbackErro("Saldo disponível de Cashback insuficiente.")
    reserva = existente or CashbackReserva.objects.create(
        empresa=empresa, hub=hub, venda_uuid=venda_uuid, operacao_uuid=operacao_uuid, cliente=cliente, valor=valor,
    )
    autorizacao = CashbackAutorizacao.objects.create(reserva=reserva, total_venda=total, limite_uso=limite)
    CashbackReservaCredito.objects.bulk_create([
        CashbackReservaCredito(autorizacao=autorizacao, credito=credito, valor=parcela) for credito, parcela in parcelas
    ])
    return _resposta(reserva, autorizacao)


def _resposta(reserva, autorizacao):
    return {**serializar_cashback_reserva(reserva), "autorizacao_versao": 2,
            "total_venda": str(autorizacao.total_venda), "limite_uso": str(autorizacao.limite_uso)}


@transaction.atomic
def consumir(hub, venda_uuid, venda, pagamentos):
    exigir_controle_creditos()
    venda_uuid = _uuid_cashback(venda_uuid, "venda_uuid")
    cliente = Cliente.objects.select_for_update().get(pk=venda.cliente_id, empresa=venda.empresa)
    ids = set()
    reservados = []
    from .services import _reserva_id_cashback_pagamento, pagamento_e_cashback
    usados = [p for p in pagamentos if pagamento_e_cashback(p)]
    valor_usado = money(sum((_valor_cashback(p.get("valor")) for p in usados), ZERO))
    config = CashbackConfig.regra_ativa(venda.empresa)
    if not config or not cliente_participa_cashback(cliente, config):
        raise CashbackErro("Cliente ou regra de Cashback não elegível.")
    limite = money(min(venda.total, venda.total * Decimal(config.limite_uso_percentual or 0) / Decimal("100")))
    if valor_usado < money(config.valor_minimo_uso) or valor_usado > limite:
        raise CashbackErro("Cashback fora do mínimo ou limite percentual autorizado para a venda.")
    outros = money(sum((money(p.get("valor")) for p in pagamentos if not pagamento_e_cashback(p)), ZERO))
    if valor_usado > money(venda.total - outros):
        raise CashbackErro("Cashback não pode gerar troco.")
    for pagamento in pagamentos:
        if not pagamento_e_cashback(pagamento):
            continue
        reserva_id = _reserva_id_cashback_pagamento(pagamento)
        if not reserva_id:
            raise CashbackErro("Todo novo pagamento Cashback exige reserva oficial online; evento LOCAL desconhecido não autorizado.")
        if reserva_id in ids:
            raise CashbackErro("Uma reserva de Cashback não pode financiar dois pagamentos.")
        ids.add(reserva_id)
        reservados.append((pagamento, reserva_id))
    for pagamento, reserva_id in reservados:
        reserva = CashbackReserva.objects.select_for_update().filter(pk=reserva_id).first()
        if not reserva or (reserva.hub_id, reserva.empresa_id, reserva.cliente_id, reserva.venda_uuid, reserva.operacao_uuid) != (
            hub.pk, venda.empresa_id, cliente.pk, venda_uuid, _uuid_cashback(pagamento.get("operacao_uuid"), "operacao_uuid"),
        ):
            raise CashbackErro("Reserva de Cashback pertence a outra operação, venda, cliente, empresa ou Hub.")
        if money(reserva.valor) != money(pagamento.get("valor")):
            raise CashbackErro("Valor da reserva de Cashback diverge do pagamento.")
        autorizacao = CashbackAutorizacao.objects.filter(reserva=reserva).first()
        if not autorizacao or autorizacao.total_venda != money(venda.total) or valor_usado > autorizacao.limite_uso:
            raise CashbackErro("Reserva sem autorização completa ou total da venda divergente.")
        operacao = uuid.uuid5(uuid.NAMESPACE_URL, f"sysvar:cashback:reserva:{hub.pk}:{reserva.pk}:{reserva.operacao_uuid}")
        anterior = CashbackConsumo.objects.select_related("movimento").filter(empresa=venda.empresa, operacao_uuid=operacao).first()
        if reserva.status == CashbackReserva.STATUS_CONSUMIDA:
            if not anterior or anterior.movimento.venda_uso_id != venda.pk or anterior.movimento.valor != reserva.valor:
                raise CashbackErro("Reserva já consumida em outro pagamento ou sem consumo rastreável.")
            continue
        if reserva.status != CashbackReserva.STATUS_RESERVADA or anterior:
            raise CashbackErro("Reserva de Cashback não está ativa ou já possui consumo.")
        disponiveis = dict((c.pk, valor) for c, valor in creditos_disponiveis(cliente, venda.empresa, [reserva.pk], bloquear=True))
        origens = list(autorizacao.creditos.select_related("credito").order_by("pk"))
        if (sum((o.valor for o in origens), ZERO) != reserva.valor
                or any(o.credito.empresa_id != venda.empresa_id or o.credito.cliente_id != cliente.pk
                       or o.valor > disponiveis.get(o.credito_id, ZERO) for o in origens)):
            raise CashbackErro("Créditos da reserva expirados ou indisponíveis; nova autorização online necessária.")
        movimento = CashbackMovimento.objects.create(
            empresa=venda.empresa, cliente=cliente, tipo=CashbackMovimento.TIPO_DEBITO,
            valor=reserva.valor, venda_uso=venda, criado_por=venda.criado_por,
            observacao=f"Uso autorizado na venda PDV {venda.documento}",
        )
        consumo = CashbackConsumo.objects.create(empresa=venda.empresa, operacao_uuid=operacao, movimento=movimento)
        CashbackConsumoCredito.objects.bulk_create([
            CashbackConsumoCredito(consumo=consumo, credito=o.credito, valor=o.valor) for o in origens
        ])
        reserva.status = CashbackReserva.STATUS_CONSUMIDA
        reserva.save(update_fields=["status", "atualizado_em"])
