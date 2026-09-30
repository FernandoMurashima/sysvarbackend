import uuid
from decimal import Decimal

from django.db import models, transaction
from django.utils import timezone
from cadastros.models import PlanoContabil

from .models import LancamentoContabil, MovimentacaoFinanceira, SequenciaDocumento, ValeTroca, ValeTrocaMovimento, ValeTrocaReserva


ZERO = Decimal("0.00")


class ValeTrocaErro(Exception):
    status_code = 409


class ValeTrocaNaoEncontrado(ValeTrocaErro):
    status_code = 404


LIMITE_NUMERO_VALE_TROCA = 9999999


def money(valor):
    return Decimal(valor or 0).quantize(Decimal("0.01"))


def formatar_documento_vale_troca(numero):
    return f"VT{int(numero):07d}"


@transaction.atomic
def reservar_documento_vale_troca(empresa):
    if not empresa:
        raise ValeTrocaErro("Empresa obrigatoria para gerar Vale-Troca.")
    sequencia, _ = SequenciaDocumento.objects.select_for_update().get_or_create(
        empresa=empresa,
        tipo_documento=SequenciaDocumento.TIPO_VALE_TROCA,
        defaults={"proximo_numero": 1},
    )
    numero = int(sequencia.proximo_numero or 1)
    if numero > LIMITE_NUMERO_VALE_TROCA:
        raise ValeTrocaErro("Faixa de numeracao de Vale-Troca esgotada.")
    documento = formatar_documento_vale_troca(numero)
    sequencia.proximo_numero = numero + 1
    sequencia.save(update_fields=["proximo_numero", "atualizado_em"])
    return documento


def _uuid(valor, campo):
    try:
        return uuid.UUID(str(valor))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValeTrocaErro(f"{campo} invalido.") from exc


def _valor(valor):
    try:
        decimal = money(Decimal(str(valor)))
    except Exception as exc:
        raise ValeTrocaErro("Valor de Vale-Troca invalido.") from exc
    if decimal <= ZERO:
        raise ValeTrocaErro("Valor de Vale-Troca invalido.")
    return decimal


def saldo_reservado_vale_troca(vale):
    return money(
        ValeTrocaReserva.objects.filter(
            vale=vale,
            status=ValeTrocaReserva.STATUS_RESERVADA,
        ).aggregate(total=models.Sum("valor")).get("total")
        or ZERO
    )


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
        if money(vale.saldo) < money(reserva.valor):
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
