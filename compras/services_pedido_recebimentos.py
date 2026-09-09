from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Prefetch, Q, Sum

from compras.models import PedidoCompra, PedidoCompraEntrega, PedidoCompraItem
from fiscal.models import NotaFiscalEntrada, RecebimentoMercadoriaEstoque, RecebimentoMercadoriaPedido


QTD_ZERO = Decimal("0.000")


def _qtd(value):
    return Decimal(value or 0).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def _situacao(pedida, recebida):
    pedida = _qtd(pedida)
    recebida = _qtd(recebida)
    if recebida <= QTD_ZERO:
        return "PENDENTE"
    if recebida >= pedida:
        return "RECEBIDO"
    return "PARCIAL"


def _documento_key(*, chave_acesso=None, xml_fornecedor_id=None, nota_entrada_id=None, recebimento_id=None):
    chave = str(chave_acesso or "").strip()
    if chave:
        return ("chave_acesso", chave)
    if xml_fornecedor_id:
        return ("xml_fornecedor_id", xml_fornecedor_id)
    if nota_entrada_id:
        return ("nota_entrada_id", nota_entrada_id)
    return ("recebimento_id", recebimento_id)


def _dados_nota(nota):
    return {
        "nota_entrada_id": nota.pk,
        "status_fiscal": nota.status,
        "nota_cancelada": nota.status == NotaFiscalEntrada.Status.CANCELADA,
        "numero": nota.numero,
        "serie": nota.serie,
        "chave_acesso": nota.chave_acesso or "",
        "dh_emissao": nota.dh_emissao,
        "valor_total": nota.valor_total,
        "xml_fornecedor_id": nota.xml_fornecedor_id,
    }


def _dados_xml(xml):
    if not xml:
        return {}
    return {
        "xml_fornecedor_id": xml.pk,
        "numero": xml.numero,
        "serie": xml.serie,
        "chave_acesso": xml.chave_acesso or "",
        "dh_emissao": xml.dh_emissao,
        "valor_total": xml.valor_total,
        "status_operacional": xml.status_operacional,
        "tipo_tratamento": xml.tipo_tratamento,
    }


def _adicionar_documento(documentos, ordem, chave, dados):
    existente = documentos.get(chave)
    if existente is None:
        documentos[chave] = dados
        ordem.append(chave)
        return
    origens = set(str(existente.get("origem") or "").split("+")) | set(str(dados.get("origem") or "").split("+"))
    for campo, valor in dados.items():
        if campo == "origem":
            continue
        if campo in {"estoque_efetivado", "recebimento_cancelado", "nota_cancelada"}:
            existente[campo] = bool(existente.get(campo)) or bool(valor)
            continue
        if valor not in (None, "") or campo not in existente:
            existente[campo] = valor
    origens.discard("")
    existente["origem"] = "+".join(sorted(origens))


def montar_resumo_recebimentos_pedido(pedido: PedidoCompra):
    itens = list(
        PedidoCompraItem.objects
        .select_related("produto", "cor", "pack")
        .prefetch_related(Prefetch("entregas", queryset=PedidoCompraEntrega.objects.order_by("id")))
        .filter(pedido=pedido)
        .order_by("id")
    )

    itens_resumo = []
    quantidade_pedida_total = QTD_ZERO
    quantidade_recebida_total = QTD_ZERO
    for item in itens:
        pedida = _qtd(item.qtd)
        recebida = _qtd(sum((Decimal(entrega.qtd_recebida or 0) for entrega in item.entregas.all()), Decimal("0")))
        saldo = max(pedida - recebida, QTD_ZERO)
        quantidade_pedida_total += pedida
        quantidade_recebida_total += recebida
        produto = getattr(item, "produto", None)
        cor = getattr(item, "cor", None)
        pack = getattr(item, "pack", None)
        itens_resumo.append({
            "pedido_item_id": item.pk,
            "produto_id": item.produto_id,
            "produto": getattr(produto, "descricao", "") or getattr(item, "descricao_livre", "") or "",
            "referencia": getattr(produto, "referencia", "") or "",
            "cor": getattr(cor, "Descricao", "") or "",
            "pack": getattr(pack, "nome", "") or "",
            "quantidade_pedida": pedida,
            "quantidade_recebida": recebida,
            "saldo": saldo,
            "situacao": _situacao(pedida, recebida),
        })

    documentos = {}
    ordem = []
    recebimentos = (
        RecebimentoMercadoriaPedido.objects
        .select_related(
            "recebimento",
            "recebimento__xml_fornecedor",
            "recebimento__efetivacao_estoque",
            "recebimento__xml_fornecedor__nota_fiscal_entrada",
        )
        .filter(pedido=pedido, recebimento__xml_fornecedor__isnull=False)
        .annotate(quantidade_fisica_pedido=Sum("recebimento__conferencia_itens__quantidade_recebida", filter=Q(recebimento__conferencia_itens__pedido=pedido)))
        .order_by("recebimento__criado_em", "recebimento_id")
    )
    for vinculo in recebimentos:
        recebimento = vinculo.recebimento
        xml = recebimento.xml_fornecedor
        nota = getattr(xml, "nota_fiscal_entrada", None)
        efetivacao = getattr(recebimento, "efetivacao_estoque", None)
        quantidade_fisica = _qtd(vinculo.quantidade_fisica_pedido)
        if quantidade_fisica == QTD_ZERO and efetivacao:
            quantidade_fisica = _qtd(efetivacao.quantidade_total)
        dados = {
            "origem": "RECEBIMENTO_FISICO",
            "recebimento_id": recebimento.pk,
            "quantidade_fisica": quantidade_fisica,
            "status_operacional": recebimento.status,
            "tipo_tratamento": getattr(xml, "tipo_tratamento", ""),
            "estoque_efetivado": efetivacao is not None and recebimento.status != RecebimentoMercadoriaEstoque.Status.CANCELADO,
            "nota_entrada_id": getattr(nota, "pk", None),
            "status_fiscal": getattr(nota, "status", None),
            "recebimento_cancelado": recebimento.status == RecebimentoMercadoriaEstoque.Status.CANCELADO,
        }
        dados.update(_dados_xml(xml))
        if nota:
            for campo, valor in _dados_nota(nota).items():
                dados[campo] = valor
        chave = _documento_key(chave_acesso=dados.get("chave_acesso"), xml_fornecedor_id=xml.pk, nota_entrada_id=getattr(nota, "pk", None), recebimento_id=recebimento.pk)
        _adicionar_documento(documentos, ordem, chave, dados)

    notas = (
        NotaFiscalEntrada.objects
        .select_related("xml_fornecedor")
        .filter(pedido_compra=pedido)
        .order_by("dt_emissao", "id")
    )
    for nota in notas:
        dados = {
            "origem": "NOTA_FISCAL",
            "recebimento_id": None,
            "quantidade_fisica": None,
            "status_operacional": None,
            "tipo_tratamento": getattr(getattr(nota, "xml_fornecedor", None), "tipo_tratamento", None),
            "estoque_efetivado": False,
            "recebimento_cancelado": False,
        }
        dados.update(_dados_xml(getattr(nota, "xml_fornecedor", None)))
        dados.update(_dados_nota(nota))
        chave = _documento_key(chave_acesso=dados.get("chave_acesso"), xml_fornecedor_id=nota.xml_fornecedor_id, nota_entrada_id=nota.pk)
        _adicionar_documento(documentos, ordem, chave, dados)

    saldo_total = max(quantidade_pedida_total - quantidade_recebida_total, QTD_ZERO)
    return {
        "pedido_id": pedido.pk,
        "resumo": {
            "quantidade_pedida_total": quantidade_pedida_total,
            "quantidade_recebida_total": quantidade_recebida_total,
            "saldo_total": saldo_total,
            "situacao": _situacao(quantidade_pedida_total, quantidade_recebida_total),
        },
        "itens": itens_resumo,
        "documentos": [documentos[chave] for chave in ordem],
    }
