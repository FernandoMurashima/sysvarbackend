import uuid
from decimal import Decimal

from django.db import IntegrityError, models, transaction
from django.utils import timezone
from cadastros.models import Cliente, PlanoContabil

from .models import CashbackConfig, CashbackMovimento, CashbackReserva, LancamentoContabil, MovimentacaoFinanceira, SequenciaDocumento, ValeTroca, ValeTrocaMovimento, ValeTrocaReserva, saldo_cashback_cliente


ZERO = Decimal("0.00")
LIMITE_NUMERO_DOCUMENTO = 9999999
ERRO_CASHBACK_VALE_TROCA = (
    "Cashback não pode ser usado junto com Vale-Troca. "
    "Realize a venda com Cashback separadamente da troca."
)
TIPOS_VALE_TROCA = {"TROCA", "VALE_TROCA"}


class DocumentoSequenciaErro(Exception):
    pass


class DocumentoSequenciaParametroInvalido(DocumentoSequenciaErro):
    pass


class DocumentoSequenciaQuantidadeInvalida(DocumentoSequenciaErro):
    pass


class DocumentoSequenciaEsgotada(DocumentoSequenciaErro):
    pass


class ValeTrocaErro(DocumentoSequenciaErro):
    status_code = 409


class ValeTrocaNaoEncontrado(ValeTrocaErro):
    status_code = 404


class CashbackErro(DocumentoSequenciaErro):
    status_code = 409


class CashbackNaoEncontrado(CashbackErro):
    status_code = 404


LIMITE_NUMERO_VALE_TROCA = LIMITE_NUMERO_DOCUMENTO


def money(valor):
    return Decimal(valor or 0).quantize(Decimal("0.01"))


def pagamento_e_cashback(pagamento):
    forma = str(pagamento.get("forma") or pagamento.get("codigo") or "").upper()
    tipo = str(pagamento.get("tipo") or "").upper()
    return forma == "CASHBACK" or tipo == "CASHBACK"


def pagamento_e_vale_troca(pagamento):
    forma = str(pagamento.get("forma") or pagamento.get("codigo") or "").upper()
    tipo = str(pagamento.get("tipo") or "").upper()
    return (
        forma in TIPOS_VALE_TROCA
        or tipo in TIPOS_VALE_TROCA
        or bool(pagamento.get("vale_troca_documento") or pagamento.get("vale_troca_reserva_id"))
    )


def mensagem_cashback_vale_troca_exclusivos(pagamentos):
    pagamentos = list(pagamentos or [])
    if any(pagamento_e_cashback(pagamento) for pagamento in pagamentos) and any(pagamento_e_vale_troca(pagamento) for pagamento in pagamentos):
        return ERRO_CASHBACK_VALE_TROCA
    return ""


def validar_cashback_vale_troca_exclusivos(pagamentos):
    mensagem = mensagem_cashback_vale_troca_exclusivos(pagamentos)
    if mensagem:
        raise ValueError(mensagem)


def formatar_documento_vale_troca(numero):
    return f"VT{int(numero):07d}"


def escopo_empresa():
    return SequenciaDocumento.ESCOPO_EMPRESA


def escopo_loja(loja_id):
    try:
        valor = int(loja_id)
    except (TypeError, ValueError) as exc:
        raise DocumentoSequenciaParametroInvalido("Loja invalida para escopo de documento.") from exc
    if valor <= 0:
        raise DocumentoSequenciaParametroInvalido("Loja invalida para escopo de documento.")
    return f"LOJA:{valor}"


def escopo_ano(ano):
    try:
        valor = int(ano)
    except (TypeError, ValueError) as exc:
        raise DocumentoSequenciaParametroInvalido("Ano invalido para escopo de documento.") from exc
    if valor < 1:
        raise DocumentoSequenciaParametroInvalido("Ano invalido para escopo de documento.")
    return f"ANO:{valor}"


def reservar_numero_documento(empresa, tipo_documento, escopo):
    inicio, _fim = reservar_faixa_documento(empresa, tipo_documento, escopo, 1)
    return inicio


@transaction.atomic
def reservar_faixa_documento(empresa, tipo_documento, escopo, quantidade):
    if not empresa:
        raise DocumentoSequenciaParametroInvalido("Empresa obrigatoria para reservar sequencia de documento.")
    if not tipo_documento:
        raise DocumentoSequenciaParametroInvalido("Tipo de documento obrigatorio para reservar sequencia.")
    if not escopo:
        raise DocumentoSequenciaParametroInvalido("Escopo obrigatorio para reservar sequencia.")
    try:
        qtd = int(quantidade)
    except (TypeError, ValueError) as exc:
        raise DocumentoSequenciaQuantidadeInvalida("Quantidade invalida para reservar sequencia.") from exc
    if qtd <= 0:
        raise DocumentoSequenciaQuantidadeInvalida("Quantidade invalida para reservar sequencia.")

    sequencia = _obter_ou_criar_sequencia_documento(empresa, tipo_documento, escopo)
    numero = int(sequencia.proximo_numero or 1)
    fim = numero + qtd - 1
    if numero > LIMITE_NUMERO_DOCUMENTO or fim > LIMITE_NUMERO_DOCUMENTO:
        raise DocumentoSequenciaEsgotada("Faixa de numeracao de documento esgotada.")
    sequencia.proximo_numero = fim + 1
    sequencia.save(update_fields=["proximo_numero", "atualizado_em"])
    return numero, fim


def _obter_ou_criar_sequencia_documento(empresa, tipo_documento, escopo):
    try:
        return SequenciaDocumento.objects.select_for_update().get(
            empresa=empresa,
            tipo_documento=tipo_documento,
            escopo=escopo,
        )
    except SequenciaDocumento.DoesNotExist:
        try:
            with transaction.atomic():
                return SequenciaDocumento.objects.create(
                    empresa=empresa,
                    tipo_documento=tipo_documento,
                    escopo=escopo,
                    proximo_numero=1,
                )
        except IntegrityError:
            return SequenciaDocumento.objects.select_for_update().get(
                empresa=empresa,
                tipo_documento=tipo_documento,
                escopo=escopo,
            )


def reservar_documento_vale_troca(empresa):
    if not empresa:
        raise ValeTrocaErro("Empresa obrigatoria para gerar Vale-Troca.")
    try:
        numero = reservar_numero_documento(
            empresa,
            SequenciaDocumento.TIPO_VALE_TROCA,
            escopo_empresa(),
        )
    except DocumentoSequenciaEsgotada as exc:
        raise ValeTrocaErro("Faixa de numeracao de Vale-Troca esgotada.") from exc
    documento = formatar_documento_vale_troca(numero)
    return documento


def _uuid(valor, campo):
    try:
        return uuid.UUID(str(valor))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValeTrocaErro(f"{campo} invalido.") from exc


def _uuid_cashback(valor, campo):
    try:
        return uuid.UUID(str(valor))
    except (TypeError, ValueError, AttributeError) as exc:
        raise CashbackErro(f"{campo} invalido.") from exc


def _valor(valor):
    try:
        decimal = money(Decimal(str(valor)))
    except Exception as exc:
        raise ValeTrocaErro("Valor de Vale-Troca invalido.") from exc
    if decimal <= ZERO:
        raise ValeTrocaErro("Valor de Vale-Troca invalido.")
    return decimal


def _valor_cashback(valor):
    try:
        decimal = money(Decimal(str(valor)))
    except Exception as exc:
        raise CashbackErro("Valor de Cashback invalido.") from exc
    if decimal <= ZERO:
        raise CashbackErro("Valor de Cashback invalido.")
    return decimal


def _cliente_padrao_cashback(cliente):
    documento = "".join(ch for ch in str(getattr(cliente, "cpf", "") or getattr(cliente, "documento", "") or "") if ch.isdigit())
    nome = (getattr(cliente, "nome_cliente", "") or "").lower()
    return documento == "00000000000" or "consumidor final" in nome


def cliente_participa_cashback(cliente, config):
    return config.consumidor_final_participa or not _cliente_padrao_cashback(cliente)


def saldo_reservado_cashback_cliente(cliente_id, empresa, excluir_reserva_ids=None):
    qs = CashbackReserva.objects.filter(
        empresa=empresa,
        cliente_id=cliente_id,
        status=CashbackReserva.STATUS_RESERVADA,
    )
    if excluir_reserva_ids:
        qs = qs.exclude(pk__in=excluir_reserva_ids)
    return money(qs.aggregate(total=models.Sum("valor")).get("total") or ZERO)


def saldo_disponivel_cashback_cliente(cliente_id, empresa, excluir_reserva_ids=None):
    saldo = money(saldo_cashback_cliente(cliente_id, empresa=empresa))
    reservado = saldo_reservado_cashback_cliente(cliente_id, empresa, excluir_reserva_ids=excluir_reserva_ids)
    return money(max(ZERO, saldo - reservado))


def _cashback_devolucao_gerado_qs(venda, devolucao=None):
    qs = CashbackMovimento.objects.filter(
        venda_origem=venda,
        venda_uso__isnull=True,
        devolucao__isnull=False,
        tipo=CashbackMovimento.TIPO_DEBITO,
        status=CashbackMovimento.STATUS_ATIVO,
    )
    if devolucao is not None:
        qs = qs.filter(devolucao=devolucao)
    return qs


def _cashback_possui_consumo_apos_credito(credito):
    consumo = CashbackMovimento.objects.filter(
        empresa=credito.empresa,
        cliente=credito.cliente,
        venda_uso__isnull=False,
        tipo=CashbackMovimento.TIPO_DEBITO,
        status=CashbackMovimento.STATUS_ATIVO,
        Idcashbackmovimento__gt=credito.pk,
    ).exists()
    if consumo:
        return True
    return CashbackReserva.objects.filter(
        empresa=credito.empresa,
        cliente=credito.cliente,
        criado_em__gt=credito.criado_em,
    ).exclude(status=CashbackReserva.STATUS_CANCELADA).exists()


@transaction.atomic
def cancelar_cashback_gerado_venda_devolvida(devolucao):
    from fiscal.models import VendaDevolucao, VendaPdv

    devolucao = (
        VendaDevolucao.objects.select_for_update()
        .select_related("venda", "cliente", "empresa")
        .prefetch_related("itens", "venda__itens", "venda__devolucoes__itens")
        .get(pk=devolucao.pk)
    )
    venda = VendaPdv.objects.select_for_update().get(pk=devolucao.venda_id)
    Cliente.objects.select_for_update().filter(pk=devolucao.cliente_id).first()

    estorno_existente = money(
        _cashback_devolucao_gerado_qs(venda, devolucao)
        .select_for_update()
        .aggregate(total=models.Sum("valor")).get("total")
        or ZERO
    )
    if estorno_existente > ZERO:
        return estorno_existente

    credito = (
        CashbackMovimento.objects.select_for_update()
        .filter(
            empresa=devolucao.empresa,
            cliente=devolucao.cliente,
            venda_origem=venda,
            tipo=CashbackMovimento.TIPO_CREDITO,
            status=CashbackMovimento.STATUS_ATIVO,
        )
        .order_by("Idcashbackmovimento")
        .first()
    )
    if not credito or money(credito.valor) <= ZERO:
        return ZERO
    if credito.validade and credito.validade < timezone.localdate():
        return ZERO
    if _cashback_possui_consumo_apos_credito(credito):
        return ZERO

    base_itens = money(sum((money(item.total_item) for item in venda.itens.all()), ZERO))
    if base_itens <= ZERO:
        return ZERO

    devolvido_acumulado = ZERO
    for devolucao_venda in venda.devolucoes.all():
        if devolucao_venda.pk and devolucao_venda.pk > devolucao.pk:
            continue
        if devolucao_venda.status == VendaDevolucao.Status.CANCELADA:
            continue
        devolvido_acumulado += sum((money(item.total_item) for item in devolucao_venda.itens.all()), ZERO)
    devolvido_acumulado = money(devolvido_acumulado)

    estornado_anterior = money(
        _cashback_devolucao_gerado_qs(venda)
        .select_for_update()
        .exclude(devolucao=devolucao)
        .aggregate(total=models.Sum("valor")).get("total")
        or ZERO
    )

    if devolvido_acumulado >= base_itens:
        estorno_alvo = money(credito.valor)
    else:
        estorno_alvo = money(money(credito.valor) * devolvido_acumulado / base_itens)
    estorno_incremental = money(max(ZERO, min(money(credito.valor), estorno_alvo) - estornado_anterior))
    disponivel_credito = money(max(ZERO, money(credito.valor) - estornado_anterior))
    estorno_incremental = money(min(estorno_incremental, disponivel_credito))
    if estorno_incremental <= ZERO:
        return ZERO

    CashbackMovimento.objects.create(
        empresa=devolucao.empresa,
        cliente=devolucao.cliente,
        venda_origem=venda,
        devolucao=devolucao,
        tipo=CashbackMovimento.TIPO_DEBITO,
        valor=estorno_incremental,
        validade=credito.validade,
        observacao=f"Cancelamento do Cashback gerado na venda {venda.documento} pela devolução {devolucao.documento}",
        criado_por=devolucao.criado_por,
    )
    return estorno_incremental


def serializar_cashback_saldo_cliente(cliente, empresa):
    saldo = money(saldo_cashback_cliente(cliente.pk, empresa=empresa))
    reservado = saldo_reservado_cashback_cliente(cliente.pk, empresa)
    return {
        "cliente": {
            "id": cliente.pk,
            "nome": cliente.nome_cliente,
            "documento": cliente.documento or cliente.cpf or "",
        },
        "saldo_oficial": str(saldo),
        "valor_reservado": str(reservado),
        "saldo_disponivel": str(money(max(ZERO, saldo - reservado))),
    }


def consultar_cashback_online_cliente(hub, cliente_id):
    if not cliente_id:
        raise CashbackErro("Cashback exige cliente identificado.")
    cliente = Cliente.objects.filter(pk=cliente_id, empresa=hub.loja.empresa).first()
    if not cliente:
        raise CashbackNaoEncontrado("Cliente nao encontrado para Cashback.")
    return serializar_cashback_saldo_cliente(cliente, hub.loja.empresa)


@transaction.atomic
def restaurar_cashback_utilizado_devolucao(devolucao):
    from fiscal.models import VendaDevolucao, VendaPdv

    devolucao = (
        VendaDevolucao.objects.select_for_update()
        .select_related("venda", "cliente", "empresa")
        .prefetch_related("itens", "venda__itens", "venda__devolucoes__itens")
        .get(pk=devolucao.pk)
    )
    venda = VendaPdv.objects.select_for_update().get(pk=devolucao.venda_id)
    Cliente.objects.select_for_update().filter(pk=devolucao.cliente_id).first()

    estorno_existente = money(
        CashbackMovimento.objects.select_for_update()
        .filter(devolucao=devolucao, tipo=CashbackMovimento.TIPO_ESTORNO, status=CashbackMovimento.STATUS_ATIVO)
        .aggregate(total=models.Sum("valor")).get("total")
        or ZERO
    )
    if estorno_existente > ZERO:
        credito = money(max(ZERO, money(devolucao.subtotal) - estorno_existente))
        if money(devolucao.credito_cliente) != credito:
            devolucao.credito_cliente = credito
            devolucao.save(update_fields=["credito_cliente", "atualizado_em"])
        return estorno_existente

    cashback_usado = money(
        CashbackMovimento.objects.select_for_update()
        .filter(venda_uso=venda, tipo=CashbackMovimento.TIPO_DEBITO, status=CashbackMovimento.STATUS_ATIVO)
        .aggregate(total=models.Sum("valor")).get("total")
        or ZERO
    )
    if cashback_usado <= ZERO:
        devolucao.credito_cliente = money(devolucao.subtotal)
        devolucao.save(update_fields=["credito_cliente", "atualizado_em"])
        return ZERO

    base_itens = money(sum((money(item.total_item) for item in venda.itens.all()), ZERO))
    if base_itens <= ZERO:
        devolucao.credito_cliente = money(devolucao.subtotal)
        devolucao.save(update_fields=["credito_cliente", "atualizado_em"])
        return ZERO

    devolvido_acumulado = ZERO
    for devolucao_venda in venda.devolucoes.all():
        if devolucao_venda.pk and devolucao_venda.pk > devolucao.pk:
            continue
        if devolucao_venda.status == VendaDevolucao.Status.CANCELADA:
            continue
        devolvido_acumulado += sum((money(item.total_item) for item in devolucao_venda.itens.all()), ZERO)
    devolvido_acumulado = money(devolvido_acumulado)

    estornado_anterior = money(
        CashbackMovimento.objects.select_for_update()
        .filter(
            devolucao__venda=venda,
            tipo=CashbackMovimento.TIPO_ESTORNO,
            status=CashbackMovimento.STATUS_ATIVO,
        )
        .exclude(devolucao=devolucao)
        .aggregate(total=models.Sum("valor")).get("total")
        or ZERO
    )

    if devolvido_acumulado >= base_itens:
        estorno_alvo = cashback_usado
    else:
        estorno_alvo = money(cashback_usado * devolvido_acumulado / base_itens)
    estorno_incremental = money(max(ZERO, min(cashback_usado, estorno_alvo) - estornado_anterior))

    if estorno_incremental > ZERO:
        CashbackMovimento.objects.create(
            empresa=devolucao.empresa,
            cliente=devolucao.cliente,
            devolucao=devolucao,
            tipo=CashbackMovimento.TIPO_ESTORNO,
            valor=estorno_incremental,
            observacao=f"Restituição do Cashback utilizado na devolução {devolucao.documento}",
            criado_por=devolucao.criado_por,
        )

    devolucao.credito_cliente = money(max(ZERO, money(devolucao.subtotal) - estorno_incremental))
    devolucao.save(update_fields=["credito_cliente", "atualizado_em"])
    return estorno_incremental


def serializar_cashback_reserva(reserva):
    return {
        "id": reserva.pk,
        "venda_uuid": str(reserva.venda_uuid),
        "operacao_uuid": str(reserva.operacao_uuid),
        "cliente_id": reserva.cliente_id,
        "valor": str(money(reserva.valor)),
        "status": reserva.status,
    }


@transaction.atomic
def reservar_cashback_venda(hub, venda_uuid, operacao_uuid, cliente_id, valor):
    venda_uuid = _uuid_cashback(venda_uuid, "venda_uuid")
    operacao_uuid = _uuid_cashback(operacao_uuid, "operacao_uuid")
    valor = _valor_cashback(valor)
    empresa = hub.loja.empresa
    try:
        cliente_id = int(cliente_id)
    except (TypeError, ValueError) as exc:
        raise CashbackErro("Cashback exige cliente identificado.") from exc
    existente = (
        CashbackReserva.objects.select_for_update()
        .filter(hub=hub, venda_uuid=venda_uuid, operacao_uuid=operacao_uuid)
        .first()
    )
    if existente:
        if existente.status == CashbackReserva.STATUS_CANCELADA:
            raise CashbackErro("Reserva de Cashback cancelada para esta operacao.")
        if existente.cliente_id != cliente_id or money(existente.valor) != valor:
            raise CashbackErro("Operacao de Cashback ja reservada com dados diferentes.")
        return serializar_cashback_reserva(existente)

    cliente = Cliente.objects.select_for_update().filter(pk=cliente_id, empresa=empresa).first()
    if not cliente:
        raise CashbackNaoEncontrado("Cliente nao encontrado para Cashback.")
    config = CashbackConfig.regra_ativa(empresa)
    if not config:
        raise CashbackErro("Não existe regra de cashback ativa.")
    if not cliente_participa_cashback(cliente, config):
        raise CashbackErro("Cashback não pode ser usado para consumidor final.")
    if valor < money(config.valor_minimo_uso):
        raise CashbackErro("O valor de cashback usado é menor que o mínimo configurado.")
    disponivel = saldo_disponivel_cashback_cliente(cliente.pk, empresa)
    if valor > disponivel:
        raise CashbackErro("Saldo disponivel de Cashback insuficiente.")
    reserva = CashbackReserva.objects.create(
        empresa=empresa,
        hub=hub,
        venda_uuid=venda_uuid,
        operacao_uuid=operacao_uuid,
        cliente=cliente,
        valor=valor,
    )
    return serializar_cashback_reserva(reserva)


def consultar_reservas_cashback_venda(hub, venda_uuid):
    venda_uuid = _uuid_cashback(venda_uuid, "venda_uuid")
    return [
        serializar_cashback_reserva(reserva)
        for reserva in CashbackReserva.objects.filter(hub=hub, venda_uuid=venda_uuid).order_by("Idcashbackreserva")
    ]


@transaction.atomic
def cancelar_reserva_cashback(hub, reserva_id=None, venda_uuid=None, operacao_uuid=None):
    qs = CashbackReserva.objects.select_for_update().filter(hub=hub)
    if reserva_id:
        qs = qs.filter(pk=reserva_id)
    else:
        qs = qs.filter(venda_uuid=_uuid_cashback(venda_uuid, "venda_uuid"), operacao_uuid=_uuid_cashback(operacao_uuid, "operacao_uuid"))
    reserva = qs.first()
    if not reserva:
        raise CashbackNaoEncontrado("Reserva de Cashback nao encontrada.")
    if venda_uuid is not None and reserva.venda_uuid != _uuid_cashback(venda_uuid, "venda_uuid"):
        raise CashbackErro("Reserva de Cashback pertence a outra venda.")
    if operacao_uuid is not None and reserva.operacao_uuid != _uuid_cashback(operacao_uuid, "operacao_uuid"):
        raise CashbackErro("Reserva de Cashback pertence a outra operacao.")
    if reserva.status == CashbackReserva.STATUS_CONSUMIDA:
        raise CashbackErro("Reserva consumida nao pode ser cancelada.")
    if reserva.status != CashbackReserva.STATUS_CANCELADA:
        reserva.status = CashbackReserva.STATUS_CANCELADA
        reserva.save(update_fields=["status", "atualizado_em"])
    return serializar_cashback_reserva(reserva)


@transaction.atomic
def cancelar_reservas_cashback_venda(hub, venda_uuid):
    venda_uuid = _uuid_cashback(venda_uuid, "venda_uuid")
    reservas = list(CashbackReserva.objects.select_for_update().filter(hub=hub, venda_uuid=venda_uuid))
    for reserva in reservas:
        if reserva.status == CashbackReserva.STATUS_RESERVADA:
            reserva.status = CashbackReserva.STATUS_CANCELADA
            reserva.save(update_fields=["status", "atualizado_em"])
    return [serializar_cashback_reserva(reserva) for reserva in reservas]


def _reserva_id_cashback_pagamento(pagamento):
    reserva_id = pagamento.get("reserva_id") or pagamento.get("cashback_reserva_id")
    if reserva_id in (None, ""):
        return None
    try:
        reserva_id = int(reserva_id)
    except (TypeError, ValueError) as exc:
        raise CashbackErro("Reserva de Cashback invalida.") from exc
    if reserva_id <= 0:
        raise CashbackErro("Reserva de Cashback invalida.")
    return reserva_id


@transaction.atomic
def consumir_reservas_cashback_venda(hub, venda_uuid, venda, pagamentos):
    venda_uuid = _uuid_cashback(venda_uuid, "venda_uuid")
    reserva_ids = set()
    pagamentos_reservados = []
    for pagamento in pagamentos:
        if str(pagamento.get("tipo") or "").upper() != "CASHBACK":
            continue
        reserva_id = _reserva_id_cashback_pagamento(pagamento)
        if not reserva_id:
            continue
        if reserva_id in reserva_ids:
            raise CashbackErro("Uma reserva de Cashback nao pode financiar dois pagamentos.")
        reserva_ids.add(reserva_id)
        pagamentos_reservados.append((pagamento, reserva_id))
    for pagamento, reserva_id in pagamentos_reservados:
        operacao_uuid = _uuid_cashback(pagamento.get("operacao_uuid"), "operacao_uuid")
        reserva = (
            CashbackReserva.objects.select_for_update()
            .filter(pk=reserva_id)
            .first()
        )
        if not reserva:
            raise CashbackErro("Reserva de Cashback obrigatoria nao encontrada.")
        if reserva.hub_id != hub.pk or reserva.venda_uuid != venda_uuid or reserva.operacao_uuid != operacao_uuid:
            raise CashbackErro("Reserva de Cashback pertence a outra operacao.")
        if reserva.empresa_id != venda.empresa_id or reserva.cliente_id != venda.cliente_id:
            raise CashbackErro("Reserva de Cashback nao pertence a esta venda.")
        if money(reserva.valor) != money(pagamento.get("valor")):
            raise CashbackErro("Valor da reserva de Cashback diverge do pagamento.")
        if reserva.status == CashbackReserva.STATUS_CONSUMIDA:
            continue
        if reserva.status != CashbackReserva.STATUS_RESERVADA:
            raise CashbackErro("Reserva de Cashback nao esta ativa.")
        reserva.status = CashbackReserva.STATUS_CONSUMIDA
        reserva.save(update_fields=["status", "atualizado_em"])


def saldo_reservado_vale_troca(vale, excluir_reserva_ids=None):
    return money(
        ValeTrocaReserva.objects.filter(
            vale=vale,
            status=ValeTrocaReserva.STATUS_RESERVADA,
        ).exclude(pk__in=excluir_reserva_ids or []).aggregate(total=models.Sum("valor")).get("total")
        or ZERO
    )


def saldo_disponivel_vale_troca(vale, excluir_reserva_ids=None):
    return money(max(ZERO, money(vale.saldo) - saldo_reservado_vale_troca(vale, excluir_reserva_ids)))


def serializar_vale_troca_online(vale):
    reservado = saldo_reservado_vale_troca(vale)
    disponivel = money(max(ZERO, money(vale.saldo) - reservado))
    devolucao = vale.devolucao
    return {
        "id": vale.pk,
        "documento": vale.documento,
        "cliente": {
            "id": vale.cliente_id,
            "nome": vale.cliente.nome_cliente,
            "documento": vale.cliente.documento or vale.cliente.cpf or "",
        },
        "valor_original": str(money(vale.valor_original)),
        "saldo_contabil": str(money(vale.saldo)),
        "saldo_reservado": str(reservado),
        "saldo_disponivel": str(disponivel),
        "status": vale.status,
        "validade": vale.validade.isoformat() if vale.validade else None,
        "loja_origem": {"id": vale.loja_id, "nome": vale.loja.nome_loja},
        "devolucao_origem": {
            "id": devolucao.pk,
            "documento": devolucao.documento,
        } if getattr(vale, "devolucao_id", None) else None,
    }


def consultar_vale_troca_online(hub, documento):
    documento = str(documento or "").strip().upper()
    if not documento:
        raise ValeTrocaErro("Informe o numero do Vale-Troca.")
    vale = (
        ValeTroca.objects.select_related("cliente", "loja", "devolucao")
        .filter(empresa=hub.loja.empresa)
        .filter(models.Q(documento=documento) | models.Q(documento_legado=documento))
        .first()
    )
    if not vale:
        raise ValeTrocaNaoEncontrado("Vale-Troca nao encontrado.")
    return serializar_vale_troca_online(vale)


def listar_vales_troca_online_cliente(hub, cliente_id):
    if not cliente_id:
        raise ValeTrocaErro("Vale-Troca exige cliente identificado.")
    hoje = timezone.localdate()
    vales = (
        ValeTroca.objects.select_related("cliente", "loja", "devolucao")
        .filter(
            empresa=hub.loja.empresa,
            cliente_id=cliente_id,
            status=ValeTroca.STATUS_ABERTO,
            saldo__gt=ZERO,
        )
        .filter(models.Q(validade__isnull=True) | models.Q(validade__gte=hoje))
        .order_by("criado_em", "Idvaletroca")
    )
    disponiveis = []
    for vale in vales:
        payload = serializar_vale_troca_online(vale)
        if money(payload["saldo_disponivel"]) > ZERO:
            disponiveis.append(payload)
    return disponiveis


def validar_vale_troca_online_para_cliente(vale, cliente_id):
    if not cliente_id:
        raise ValeTrocaErro("Venda com Vale-Troca exige cliente identificado.")
    if vale.cliente_id != int(cliente_id):
        raise ValeTrocaErro("Vale-Troca pertence a outro cliente.")
    if vale.status != ValeTroca.STATUS_ABERTO:
        raise ValeTrocaErro("Vale-Troca nao esta aberto.")
    if vale.validade and vale.validade < timezone.localdate():
        raise ValeTrocaErro("Vale-Troca vencido.")


def serializar_vale_troca_reserva(reserva):
    return {
        "id": reserva.pk,
        "venda_uuid": str(reserva.venda_uuid),
        "operacao_uuid": str(reserva.operacao_uuid),
        "documento": reserva.vale.documento,
        "vale_id": reserva.vale_id,
        "valor": str(money(reserva.valor)),
        "status": reserva.status,
    }


@transaction.atomic
def reservar_vales_troca_venda(hub, venda_uuid, cliente_id, pagamentos):
    venda_uuid = _uuid(venda_uuid, "venda_uuid")
    if not isinstance(pagamentos, list) or not pagamentos:
        raise ValeTrocaErro("Informe os pagamentos de Vale-Troca.")
    reservas = []
    documentos = set()
    for item in pagamentos:
        operacao_uuid = _uuid(item.get("operacao_uuid"), "operacao_uuid")
        documento = str(item.get("documento") or "").strip()
        valor = _valor(item.get("valor"))
        if not documento:
            raise ValeTrocaErro("Informe o numero do Vale-Troca.")
        if documento in documentos:
            raise ValeTrocaErro("O mesmo Vale-Troca nao pode ser usado duas vezes na venda.")
        documentos.add(documento)
        existente = (
            ValeTrocaReserva.objects.select_for_update()
            .select_related("vale")
            .filter(hub=hub, venda_uuid=venda_uuid, operacao_uuid=operacao_uuid)
            .first()
        )
        if existente:
            if existente.status == ValeTrocaReserva.STATUS_CANCELADA:
                raise ValeTrocaErro("Reserva de Vale-Troca cancelada para esta operacao.")
            if existente.vale.documento != documento or money(existente.valor) != valor:
                raise ValeTrocaErro("Operacao de Vale-Troca ja reservada com dados diferentes.")
            reservas.append(existente)
            continue
        vale = ValeTroca.objects.select_for_update().filter(empresa=hub.loja.empresa, documento=documento).first()
        if not vale:
            raise ValeTrocaNaoEncontrado("Vale-Troca nao encontrado.")
        validar_vale_troca_online_para_cliente(vale, cliente_id)
        disponivel = money(max(ZERO, money(vale.saldo) - saldo_reservado_vale_troca(vale)))
        if valor > disponivel:
            raise ValeTrocaErro("Saldo disponivel do Vale-Troca insuficiente.")
        reservas.append(ValeTrocaReserva.objects.create(
            empresa=hub.loja.empresa,
            hub=hub,
            venda_uuid=venda_uuid,
            operacao_uuid=operacao_uuid,
            vale=vale,
            valor=valor,
        ))
    return [serializar_vale_troca_reserva(reserva) for reserva in reservas]


def consultar_reservas_vale_troca_venda(hub, venda_uuid):
    venda_uuid = _uuid(venda_uuid, "venda_uuid")
    return [
        serializar_vale_troca_reserva(reserva)
        for reserva in ValeTrocaReserva.objects.select_related("vale").filter(hub=hub, venda_uuid=venda_uuid).order_by("Idvaletrocareserva")
    ]


@transaction.atomic
def cancelar_reserva_vale_troca(hub, reserva_id=None, venda_uuid=None, operacao_uuid=None):
    qs = ValeTrocaReserva.objects.select_for_update().select_related("vale").filter(hub=hub)
    if reserva_id:
        qs = qs.filter(pk=reserva_id)
    else:
        qs = qs.filter(venda_uuid=_uuid(venda_uuid, "venda_uuid"), operacao_uuid=_uuid(operacao_uuid, "operacao_uuid"))
    reserva = qs.first()
    if not reserva:
        raise ValeTrocaNaoEncontrado("Reserva de Vale-Troca nao encontrada.")
    if reserva.status == ValeTrocaReserva.STATUS_CONSUMIDA:
        raise ValeTrocaErro("Reserva consumida nao pode ser cancelada.")
    if reserva.status != ValeTrocaReserva.STATUS_CANCELADA:
        reserva.status = ValeTrocaReserva.STATUS_CANCELADA
        reserva.save(update_fields=["status", "atualizado_em"])
    return serializar_vale_troca_reserva(reserva)


@transaction.atomic
def cancelar_reservas_vale_troca_venda(hub, venda_uuid):
    venda_uuid = _uuid(venda_uuid, "venda_uuid")
    reservas = list(ValeTrocaReserva.objects.select_for_update().select_related("vale").filter(hub=hub, venda_uuid=venda_uuid))
    for reserva in reservas:
        if reserva.status == ValeTrocaReserva.STATUS_RESERVADA:
            reserva.status = ValeTrocaReserva.STATUS_CANCELADA
            reserva.save(update_fields=["status", "atualizado_em"])
    return [serializar_vale_troca_reserva(reserva) for reserva in reservas]


@transaction.atomic
def consumir_reservas_vale_troca_venda(hub, venda_uuid, venda, pagamentos):
    venda_uuid = _uuid(venda_uuid, "venda_uuid")
    for pagamento in pagamentos:
        if str(pagamento.get("tipo") or "").upper() not in ("TROCA", "VALE_TROCA"):
            continue
        operacao_uuid = _uuid(pagamento.get("operacao_uuid"), "operacao_uuid")
        reserva = (
            ValeTrocaReserva.objects.select_for_update()
            .select_related("vale")
            .filter(hub=hub, venda_uuid=venda_uuid, operacao_uuid=operacao_uuid)
            .first()
        )
        if not reserva:
            raise ValeTrocaErro("Reserva de Vale-Troca obrigatoria nao encontrada.")
        if reserva.status == ValeTrocaReserva.STATUS_CONSUMIDA:
            continue
        if reserva.status != ValeTrocaReserva.STATUS_RESERVADA:
            raise ValeTrocaErro("Reserva de Vale-Troca nao esta ativa.")
        vale = ValeTroca.objects.select_for_update().get(pk=reserva.vale_id)
        if saldo_disponivel_vale_troca(vale, excluir_reserva_ids=[reserva.pk]) < money(reserva.valor):
            raise ValeTrocaErro("Saldo contabil do Vale-Troca insuficiente.")
        vale.saldo = money(vale.saldo - reserva.valor)
        vale.status = ValeTroca.STATUS_ABERTO if vale.saldo > ZERO else ValeTroca.STATUS_USADO
        vale.save(update_fields=["saldo", "status", "atualizado_em"])
        if not ValeTrocaMovimento.objects.filter(vale=vale, venda_uso=venda, tipo=ValeTrocaMovimento.TIPO_USO).exists():
            ValeTrocaMovimento.objects.create(
                vale=vale,
                venda_uso=venda,
                tipo=ValeTrocaMovimento.TIPO_USO,
                valor=reserva.valor,
                saldo_apos=vale.saldo,
                observacao=f"Uso na venda Hub {venda_uuid}",
            )
        reserva.status = ValeTrocaReserva.STATUS_CONSUMIDA
        reserva.save(update_fields=["status", "atualizado_em"])


def _conta_por_codigo(empresa, codigo):
    if not empresa or not codigo:
        return None
    return PlanoContabil.objects.filter(empresa=empresa, codigo=codigo, ativa=True).first()


def _conta_analitica_por_classe(empresa, classe, termos=()):
    if not empresa:
        return None
    qs = PlanoContabil.objects.filter(empresa=empresa, classe=classe, ativa=True, analitica=True)
    for termo in termos:
        conta = qs.filter(descricao__icontains=termo).order_by('codigo').first()
        if conta:
            return conta
    return qs.order_by('codigo').first()


def _conta_operacional(movimentacao):
    empresa = movimentacao.empresa
    if movimentacao.origem == MovimentacaoFinanceira.ORIGEM_CMV:
        return _conta_analitica_por_classe(empresa, PlanoContabil.CLASSE_ATIVO, ('Estoque', 'Mercadoria'))
    if movimentacao.origem == MovimentacaoFinanceira.ORIGEM_RECEBER and not movimentacao.conta_bancaria_id and not movimentacao.caixa_id:
        return _conta_analitica_por_classe(empresa, PlanoContabil.CLASSE_ATIVO, ('Cliente', 'Receber', 'Duplicata'))

    destino = movimentacao.conta_bancaria or movimentacao.caixa
    codigo = getattr(destino, 'conta_contabil', None)
    conta = _conta_por_codigo(empresa, codigo)
    if conta:
        return conta

    if movimentacao.conta_bancaria_id:
        return _conta_analitica_por_classe(empresa, PlanoContabil.CLASSE_ATIVO, ('Banco', 'Conta', 'Dispon'))
    return _conta_analitica_por_classe(empresa, PlanoContabil.CLASSE_ATIVO, ('Caixa', 'Dispon'))


def _conta_natureza(movimentacao):
    natureza = movimentacao.Idnatureza
    if not natureza:
        return None
    if natureza.plano_contabil_id:
        return natureza.plano_contabil
    conta = _conta_por_codigo(movimentacao.empresa, natureza.conta_contabil)
    if conta:
        return conta

    operacao = (natureza.natureza_operacao or '').upper()
    if operacao == 'RECEITA':
        return _conta_analitica_por_classe(movimentacao.empresa, PlanoContabil.CLASSE_RECEITA, (natureza.categoria_principal, 'Receita'))
    if operacao == 'DESPESA':
        texto = ' '.join([
            natureza.categoria_principal or '',
            natureza.subcategoria or '',
            natureza.descricao or '',
            natureza.categoria_gerencial or '',
        ]).lower()
        if any(palavra in texto for palavra in ('cmv', 'custo', 'mercadoria vendida')):
            conta_custo = _conta_analitica_por_classe(
                movimentacao.empresa,
                PlanoContabil.CLASSE_CUSTO,
                (natureza.categoria_principal, natureza.subcategoria, natureza.descricao, 'CMV', 'Custo')
            )
            if conta_custo:
                return conta_custo
        return _conta_analitica_por_classe(movimentacao.empresa, PlanoContabil.CLASSE_DESPESA, (natureza.categoria_principal, 'Despesa'))
    return None


def gerar_lancamento_contabil_movimentacao(movimentacao):
    if not movimentacao or not movimentacao.pk:
        return None
    if movimentacao.status != MovimentacaoFinanceira.STATUS_EFETIVA:
        return None
    if not movimentacao.empresa_id:
        return None

    try:
        existente = movimentacao.lancamento_contabil
    except Exception:
        existente = None
    if existente:
        return existente

    conta_operacional = _conta_operacional(movimentacao)
    conta_natureza = _conta_natureza(movimentacao)
    observacoes = []

    if not conta_operacional:
        observacoes.append('Conta operacional de caixa/banco não localizada.')
    if not conta_natureza:
        observacoes.append('Conta contábil da natureza não localizada.')

    conta_debito = None
    conta_credito = None
    if movimentacao.tipo == MovimentacaoFinanceira.TIPO_ENTRADA:
        conta_debito = conta_operacional
        conta_credito = conta_natureza
    elif movimentacao.tipo == MovimentacaoFinanceira.TIPO_SAIDA:
        conta_debito = conta_natureza
        conta_credito = conta_operacional
    else:
        observacoes.append('Transferência ainda exige lançamento contábil pareado.')

    status = LancamentoContabil.STATUS_GERADO
    if observacoes or not conta_debito or not conta_credito:
        status = LancamentoContabil.STATUS_PENDENTE

    return LancamentoContabil.objects.create(
        empresa=movimentacao.empresa,
        idloja=movimentacao.idloja,
        movimentacao=movimentacao,
        data_lancamento=movimentacao.data_movimento,
        documento=movimentacao.documento,
        historico=movimentacao.historico[:255],
        origem=movimentacao.origem,
        natureza=movimentacao.Idnatureza,
        conta_debito=conta_debito,
        conta_credito=conta_credito,
        valor=Decimal(movimentacao.valor or 0),
        status=status,
        observacao=' '.join(observacoes)[:255],
    )


def estornar_lancamento_contabil_movimentacao(movimentacao, motivo=''):
    if not movimentacao or not movimentacao.pk:
        return None
    try:
        lancamento = movimentacao.lancamento_contabil
    except Exception:
        lancamento = None
    if not lancamento or lancamento.status == LancamentoContabil.STATUS_ESTORNADO:
        return lancamento
    lancamento.status = LancamentoContabil.STATUS_ESTORNADO
    if motivo:
        lancamento.observacao = (motivo or '')[:255]
    lancamento.save(update_fields=['status', 'observacao'])
    return lancamento
