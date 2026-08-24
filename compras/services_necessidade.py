from decimal import Decimal

from django.db.models import Sum

from produto.models import ProdutoUsoConsumoEstoque

from .models import CotacaoItem, PedidoCompraEntrega


STATUS_ITEM_PROCESSO_COMPRA = {"AGUARDANDO_COTACAO", "EM_COTACAO", "PEDIDO_GERADO", "AGUARDANDO_RECEBIMENTO"}
STATUS_REQUISICAO_PROCESSO_COMPRA = {"AGUARDANDO_COTACAO", "EM_PROCESSO_COMPRA"}


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
    loja_estoque = loja_estoque_requisicao_item(item)
    return ProdutoUsoConsumoEstoque.objects.filter(
        empresa_id=item.requisicao.empresa_id,
        loja_id=loja_estoque.id,
        produto_id=item.produto_id,
    ).aggregate(total=Sum("saldo"))["total"] or Decimal("0")


def loja_estoque_requisicao_item(item):
    requisicao = item.requisicao
    if getattr(requisicao, "tipo_requisicao", "USO_CONSUMO") != "USO_CONSUMO":
        return requisicao.loja
    setor = getattr(requisicao, "setor_responsavel", None)
    if not setor or not setor.loja_id:
        from rest_framework.exceptions import ValidationError
        raise ValidationError({"detail": "Não foi possível identificar o estoque do Almoxarifado responsável por esta requisição."})
    if setor.loja.empresa_id != requisicao.empresa_id:
        from rest_framework.exceptions import ValidationError
        raise ValidationError({"detail": "Loja física do Almoxarifado pertence a outra empresa."})
    return setor.loja


def cotacoes_pedidos_relacionados(item):
    cot_itens = CotacaoItem.objects.select_related("cotacao").filter(
        requisicao_item_origem=item,
        cotacao__empresa_id=item.requisicao.empresa_id,
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
    try:
        estoque = estoque_disponivel_requisicao_item(item)
    except Exception:
        estoque = Decimal("0")
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


def sincronizar_requisicao_disponivel_para_atendimento(requisicao):
    itens_atualizados = 0
    possui_item_atendivel = False
    itens = requisicao.itens.select_related("produto", "requisicao", "requisicao__setor_responsavel", "requisicao__setor_responsavel__loja")
    for item in itens:
        if item.status in {"APROVADO", "ATENDIDO_PARCIALMENTE"} and Decimal(item.qtd_pendente or 0) > 0:
            indicador = indicador_requisicao_item(item)
            if indicador.get("codigo") == "DISPONIVEL":
                possui_item_atendivel = True
            continue
        if item.status not in STATUS_ITEM_PROCESSO_COMPRA or Decimal(item.qtd_pendente or 0) <= 0:
            continue
        indicador = indicador_requisicao_item(item)
        if indicador.get("codigo") != "DISPONIVEL":
            continue
        item.status = "APROVADO"
        item.save(update_fields=["status", "atualizado_em"])
        itens_atualizados += 1
        possui_item_atendivel = True

    requisicao_atualizada = False
    if possui_item_atendivel and requisicao.status in STATUS_REQUISICAO_PROCESSO_COMPRA:
        requisicao.status = "EM_ATENDIMENTO"
        requisicao.save(update_fields=["status", "atualizado_em"])
        requisicao_atualizada = True

    return {"itens": itens_atualizados, "requisicao": requisicao_atualizada}
