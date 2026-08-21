from decimal import Decimal

from django.db.models import Sum

from produto.models import ProdutoUsoConsumoEstoque

from .models import CotacaoItem, PedidoCompraEntrega


COTACAO_COMPRA_EM_ANDAMENTO = {
    "EM_ELABORACAO",
    "ABERTA",
    "PROPOSTAS_RECEBIDAS",
    "EM_ANALISE",
    "AGUARDANDO_APROVACAO",
    "APROVADA",
    "PEDIDO_GERADO",
}


def estoque_disponivel_requisicao_item(item):
    if not item.produto_id:
        return Decimal("0")
    return ProdutoUsoConsumoEstoque.objects.filter(
        empresa_id=item.requisicao.empresa_id,
        loja_id=item.requisicao.loja_id,
        produto_id=item.produto_id,
    ).aggregate(total=Sum("saldo"))["total"] or Decimal("0")


def cotacoes_pedidos_relacionados(item):
    cot_itens = CotacaoItem.objects.select_related("cotacao").filter(
        requisicao_item_origem=item,
        cotacao__empresa_id=item.requisicao.empresa_id,
        cotacao__loja_id=item.requisicao.loja_id,
        cotacao__status__in=COTACAO_COMPRA_EM_ANDAMENTO,
    )
    cotacoes = []
    pedidos = []
    for cot_item in cot_itens:
        cotacao = cot_item.cotacao
        cotacoes.append({
            "id": cotacao.id,
            "numero": cotacao.numero,
            "status": cotacao.status,
        })
        pedido = getattr(cotacao, "pedido_compra_gerado", None)
        if pedido:
            pedidos.append({
                "id": pedido.id,
                "status": pedido.status,
                "numero": pedido.id,
            })
    return cotacoes, pedidos


def qtd_pendente_pedido_para_item(item):
    total = Decimal("0")
    cot_itens = CotacaoItem.objects.filter(
        requisicao_item_origem=item,
        cotacao__pedido_compra_gerado__isnull=False,
        cotacao__status__in=["PEDIDO_GERADO", "APROVADA"],
    )
    for cot_item in cot_itens:
        pedido = getattr(cot_item.cotacao, "pedido_compra_gerado", None)
        if not pedido:
            continue
        for ped_item in pedido.itens.filter(produto_id=item.produto_id):
            recebido = ped_item.entregas.aggregate(total=Sum("qtd_recebida"))["total"] or Decimal("0")
            pendente = max(Decimal(ped_item.qtd or 0) - recebido, Decimal("0"))
            total += pendente
    return total


def indicador_requisicao_item(item):
    if item.tipo != "MATERIAL" or item.origem != "PRODUTO" or not item.produto_id or Decimal(item.qtd_pendente or 0) <= 0:
        return {
            "cor": None,
            "codigo": "NAO_APLICAVEL",
            "label": "",
            "estoque_atual": None,
            "qtd_pendente_compra": None,
            "cotacoes": [],
            "pedidos": [],
        }
    estoque = estoque_disponivel_requisicao_item(item)
    pendente = Decimal(item.qtd_pendente or 0)
    cotacoes, pedidos = cotacoes_pedidos_relacionados(item)
    pendente_compra = qtd_pendente_pedido_para_item(item)
    if estoque >= pendente:
        cor, codigo, label = "VERDE", "DISPONIVEL", "Disponível para atendimento"
    elif cotacoes or pendente_compra > 0:
        cor, codigo, label = "AMARELO", "EM_PROCESSO_COMPRA", "Em processo de compra"
    else:
        cor, codigo, label = "VERMELHO", "PRECISA_COMPRAR", "Sem estoque / precisa comprar"
    return {
        "cor": cor,
        "codigo": codigo,
        "label": label,
        "estoque_atual": estoque,
        "qtd_pendente_compra": pendente_compra,
        "cotacoes": cotacoes,
        "pedidos": pedidos,
    }
