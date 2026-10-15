"""Controle FEFO opt-in: requer a migration, sem ativar o fluxo de autorização.

Os serviços operacionais legados continuam em financeiro.services. Este módulo
não reconcilia movimentos antigos nem cria/recalcula estornos de devolução.
"""

import uuid
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, models, transaction
from django.db.models.functions import Coalesce
from django.utils import timezone

from cadastros.models import Cliente
from .models import (
    CashbackConsumo, CashbackConsumoCredito, CashbackEstornoCredito,
    CashbackMovimento, CashbackReserva,
)
from .services import CashbackErro


ZERO = Decimal('0.00')


class CashbackHistoricoPendente(CashbackErro):
    pass


def _valor(valor):
    try:
        decimal = Decimal(str(valor))
        if not decimal.is_finite() or decimal <= ZERO or decimal >= Decimal('10000000000000000'):
            raise ValueError
        if decimal != decimal.quantize(Decimal('0.01')):
            raise ValueError
        return decimal.quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CashbackErro('Valor de Cashback inválido.') from exc


def _cliente(empresa, cliente, *, bloquear=False):
    qs = Cliente.objects
    if bloquear:
        qs = qs.select_for_update()
    encontrado = qs.filter(pk=cliente.pk, empresa=empresa).first()
    if not encontrado:
        raise CashbackErro('Cliente não pertence à empresa do Cashback.')
    return encontrado


def _validar_historico(empresa, cliente):
    movimentos = CashbackMovimento.objects.filter(
        empresa=empresa, cliente=cliente, status=CashbackMovimento.STATUS_ATIVO,
    )
    if movimentos.filter(valor__lte=ZERO).exists():
        raise CashbackHistoricoPendente('Cashback possui movimentos não positivos; conciliação explícita necessária.')
    # Cancelamento de crédito gerado tem origem explícita na venda e devolução.
    # Débitos LOCAL sem esses vínculos continuam exigindo conciliação.
    cancelamentos = []
    for debito in movimentos.filter(tipo=CashbackMovimento.TIPO_DEBITO, venda_uso__isnull=True,
                                    venda_origem__isnull=False, devolucao__isnull=False):
        origens = movimentos.filter(tipo=CashbackMovimento.TIPO_CREDITO, venda_origem_id=debito.venda_origem_id)
        if origens.count() == 1:
            cancelamentos.append(debito.pk)
    debitos = movimentos.filter(tipo=CashbackMovimento.TIPO_DEBITO).exclude(pk__in=cancelamentos).annotate(
        valor_alocado=models.Sum('consumo_rastreavel__alocacoes__valor'),
    )
    if (movimentos.filter(tipo=CashbackMovimento.TIPO_EXPIRACAO).exists()
            or debitos.filter(models.Q(valor_alocado__isnull=True)
                              | ~models.Q(valor_alocado=models.F('valor'))).exists()):
        raise CashbackHistoricoPendente('Cashback possui débitos históricos sem origem comprovada; conciliação explícita necessária.')
    estornos = movimentos.filter(tipo=CashbackMovimento.TIPO_ESTORNO).annotate(
        valor_alocado=models.Sum('alocacoes_restituicao__valor'),
    )
    if estornos.filter(models.Q(valor_alocado__isnull=True) | ~models.Q(valor_alocado=models.F('valor'))).exists():
        raise CashbackHistoricoPendente('Cashback possui estornos sem origem comprovada; conciliação explícita necessária.')


def _creditos(empresa, cliente, data):
    consumido = CashbackConsumoCredito.objects.filter(
        credito_id=models.OuterRef('pk'),
        consumo__movimento__status=CashbackMovimento.STATUS_ATIVO,
    ).values('credito_id').annotate(total=models.Sum('valor')).values('total')
    restituido = CashbackEstornoCredito.objects.filter(
        consumo_credito__credito_id=models.OuterRef('pk'),
        consumo_credito__consumo__movimento__status=CashbackMovimento.STATUS_ATIVO,
        estorno__status=CashbackMovimento.STATUS_ATIVO,
    ).values('consumo_credito__credito_id').annotate(total=models.Sum('valor')).values('total')
    cancelado = CashbackMovimento.objects.filter(
        empresa=empresa, cliente=cliente, tipo=CashbackMovimento.TIPO_DEBITO,
        status=CashbackMovimento.STATUS_ATIVO, venda_uso__isnull=True,
        venda_origem_id=models.OuterRef('venda_origem_id'), devolucao__isnull=False,
    ).values('venda_origem_id').annotate(total=models.Sum('valor')).values('total')
    campo = models.DecimalField(max_digits=18, decimal_places=2)
    return CashbackMovimento.objects.filter(
        empresa=empresa, cliente=cliente, tipo=CashbackMovimento.TIPO_CREDITO,
        status=CashbackMovimento.STATUS_ATIVO,
    ).filter(models.Q(validade__isnull=True) | models.Q(validade__gte=data)).annotate(
        cancelado=Coalesce(models.Subquery(cancelado, output_field=campo), models.Value(ZERO), output_field=campo),
        consumido=Coalesce(models.Subquery(consumido, output_field=campo), models.Value(ZERO), output_field=campo),
        restituido=Coalesce(models.Subquery(restituido, output_field=campo), models.Value(ZERO), output_field=campo),
    ).order_by(models.F('validade').asc(nulls_last=True), 'criado_em', 'pk')


def saldo_cashback_alocado(cliente, empresa, data=None):
    """Saldo dos créditos rastreáveis; reservas continuam no contrato atual.

    Histórico sem alocação não é interpretado como saldo livre. A consulta não
    expira nem modifica movimentos e não substitui o saldo operacional legado.
    """
    cliente = _cliente(empresa, cliente)
    _validar_historico(empresa, cliente)
    return sum((max(ZERO, c.valor - c.consumido + c.restituido - c.cancelado)
                for c in _creditos(empresa, cliente, data or timezone.localdate())), ZERO)


@transaction.atomic
def consumir_cashback_fefo(*, empresa, cliente, operacao_uuid, valor, venda=None, criado_por=None):
    """Registra um débito novo e suas origens, atomicamente e sem backfill."""
    valor = _valor(valor)
    try:
        operacao_uuid = uuid.UUID(str(operacao_uuid))
    except (ValueError, TypeError, AttributeError) as exc:
        raise CashbackErro('Operação de Cashback inválida.') from exc
    cliente = _cliente(empresa, cliente, bloquear=True)
    if venda is not None:
        from fiscal.models import VendaPdv
        if not VendaPdv.objects.filter(pk=venda.pk, empresa=empresa, cliente=cliente).exists():
            raise CashbackErro('Venda não pertence ao cliente e empresa do Cashback.')
    existente = CashbackConsumo.objects.select_related('movimento').filter(
        empresa=empresa, operacao_uuid=operacao_uuid,
    ).first()
    if existente:
        movimento = existente.movimento
        if (movimento.cliente_id != cliente.pk or movimento.valor != valor
                or movimento.venda_uso_id != (venda.pk if venda else None)
                or movimento.status != CashbackMovimento.STATUS_ATIVO):
            raise CashbackErro('Operação de Cashback já utilizada com dados diferentes ou cancelada.')
        return existente
    _validar_historico(empresa, cliente)
    if CashbackReserva.objects.filter(empresa=empresa, cliente=cliente, status=CashbackReserva.STATUS_RESERVADA).exists():
        raise CashbackHistoricoPendente('Reservas ativas exigem integração com a alocação no fluxo de autorização.')
    creditos = list(_creditos(empresa, cliente, timezone.localdate()).select_for_update())
    restante = valor
    parcelas = []
    for credito in creditos:
        parcela = min(restante, max(ZERO, credito.valor - credito.consumido + credito.restituido - credito.cancelado))
        if parcela > ZERO:
            parcelas.append((credito, parcela))
            restante -= parcela
        if restante == ZERO:
            break
    if restante > ZERO:
        raise CashbackErro('Saldo rastreável de Cashback insuficiente.')
    try:
        with transaction.atomic():
            movimento = CashbackMovimento.objects.create(
                empresa=empresa, cliente=cliente, tipo=CashbackMovimento.TIPO_DEBITO,
                valor=valor, venda_uso=venda, criado_por=criado_por,
            )
            consumo = CashbackConsumo.objects.create(empresa=empresa, operacao_uuid=operacao_uuid, movimento=movimento)
            CashbackConsumoCredito.objects.bulk_create([
                CashbackConsumoCredito(consumo=consumo, credito=credito, valor=parcela)
                for credito, parcela in parcelas
            ])
    except IntegrityError as exc:
        raise CashbackErro('Conflito na operação de Cashback; repita a mesma operação.') from exc
    return consumo


@transaction.atomic
def vincular_estorno_cashback(*, empresa, estorno, parcelas):
    """Anexa origens explícitas a um estorno existente, sem gerar restituição.

    parcelas: pares (CashbackConsumoCredito.pk, valor). A validade continua sendo
    a do crédito original; a vinculação não torna utilizável um crédito expirado.
    O chamador deve integrar a vinculação à transação que gera o estorno.
    """
    cliente = _cliente(empresa, estorno.cliente, bloquear=True)
    movimento = CashbackMovimento.objects.select_for_update().filter(
        pk=estorno.pk, empresa=empresa, cliente=cliente,
        tipo=CashbackMovimento.TIPO_ESTORNO, status=CashbackMovimento.STATUS_ATIVO,
    ).first()
    if not movimento:
        raise CashbackErro('Estorno de Cashback inválido para esta empresa e cliente.')
    valores = {}
    for alocacao_id, valor in parcelas:
        if type(alocacao_id) is not int or alocacao_id <= 0 or alocacao_id in valores:
            raise CashbackErro('Origem do estorno de Cashback inválida ou repetida.')
        valores[alocacao_id] = _valor(valor)
    if not valores or sum(valores.values(), ZERO) != movimento.valor:
        raise CashbackErro('Alocação do estorno deve corresponder integralmente ao movimento.')
    existentes = dict(CashbackEstornoCredito.objects.filter(estorno=movimento).values_list('consumo_credito_id', 'valor'))
    if existentes:
        if existentes != valores:
            raise CashbackErro('Estorno de Cashback já vinculado com origens ou valores diferentes.')
        return list(CashbackEstornoCredito.objects.filter(estorno=movimento).order_by('consumo_credito_id'))
    origens = list(CashbackConsumoCredito.objects.select_for_update().select_related(
        'credito', 'consumo__movimento',
    ).filter(pk__in=valores).order_by('pk'))
    if len(origens) != len(valores):
        raise CashbackErro('Origem do estorno de Cashback não encontrada.')
    registros = []
    for origem in origens:
        if (origem.consumo.empresa_id != empresa.pk or origem.credito.empresa_id != empresa.pk
                or origem.credito.cliente_id != cliente.pk or origem.credito.tipo != CashbackMovimento.TIPO_CREDITO
                or origem.consumo.movimento.empresa_id != empresa.pk
                or origem.consumo.movimento.tipo != CashbackMovimento.TIPO_DEBITO
                or origem.consumo.movimento.cliente_id != cliente.pk
                or origem.consumo.movimento.status != CashbackMovimento.STATUS_ATIVO):
            raise CashbackErro('Origem do estorno não pertence ao cliente e empresa do Cashback.')
        devolvido = origem.estornos.filter(estorno__status=CashbackMovimento.STATUS_ATIVO).aggregate(
            total=models.Sum('valor'),
        )['total'] or ZERO
        if valores[origem.pk] > origem.valor - devolvido:
            raise CashbackErro('Estorno excede o consumo rastreado no crédito de origem.')
        registros.append(CashbackEstornoCredito(estorno=movimento, consumo_credito=origem, valor=valores[origem.pk]))
    CashbackEstornoCredito.objects.bulk_create(registros)
    return list(CashbackEstornoCredito.objects.filter(estorno=movimento).order_by('consumo_credito_id'))


@transaction.atomic
def vincular_restituicao_venda(estorno, venda):
    """Rastreia a restituição existente apenas quando seus consumos são comprovados."""
    origens = list(CashbackConsumoCredito.objects.select_related('consumo__movimento').filter(
        consumo__movimento__venda_uso=venda, consumo__movimento__status=CashbackMovimento.STATUS_ATIVO,
    ).order_by('pk'))
    debitos = CashbackMovimento.objects.filter(venda_uso=venda, tipo=CashbackMovimento.TIPO_DEBITO,
                                               status=CashbackMovimento.STATUS_ATIVO)
    if not origens or sum((o.valor for o in origens), ZERO) != sum(debitos.values_list('valor', flat=True), ZERO):
        return  # Histórico permanece íntegro e indisponível para novos usos até conciliação.
    if estorno.alocacoes_restituicao.exists():
        return
    restante = _valor(estorno.valor)
    parcelas = []
    # O vínculo é com consumos comprovados desta venda; nunca com créditos de outra venda.
    for origem in origens:
        devolvido = origem.estornos.filter(estorno__status=CashbackMovimento.STATUS_ATIVO).aggregate(total=models.Sum('valor'))['total'] or ZERO
        parcela = min(restante, origem.valor - devolvido)
        if parcela > ZERO:
            parcelas.append((origem.pk, parcela))
            restante -= parcela
        if restante == ZERO:
            break
    if restante > ZERO:
        raise CashbackErro('Restituição excede os consumos comprovados da venda.')
    vincular_estorno_cashback(empresa=estorno.empresa, estorno=estorno, parcelas=parcelas)
