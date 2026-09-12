from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from produto.models import Estoque, ProdutoDetalhe, Tabelapreco, TabelaprecoProduto

CATALOGO_VERSAO = 1
CATALOGO_TABELA_PRECO_CODIGO_V1 = "PADRAO"
CATALOGO_TABELA_PRECO_NOME_V1 = "Tabela Padrão"
MOTIVO_SEM_PRECO = "SEM_PRECO"
MOTIVO_SEM_ESTOQUE = "SEM_ESTOQUE"
TIPOS_PRODUTO_CATALOGO = ("1", "3")
ZERO_3 = Decimal("0.000")
ZERO_4 = Decimal("0.0000")


def tabela_preco_catalogo_v1(empresa, hoje=None):
    hoje = hoje or timezone.localdate()
    return (
        Tabelapreco.objects.filter(
            empresa=empresa,
            NomeTabela__iexact=CATALOGO_TABELA_PRECO_NOME_V1,
            DataInicio__lte=hoje,
        )
        .filter(Q(DataFim__isnull=True) | Q(DataFim__gte=hoje))
        .order_by("-DataInicio", "-Idtabela")
        .first()
    )


def _precos_por_produto(tabela, hoje):
    if not tabela:
        return {}
    precos = (
        TabelaprecoProduto.objects.filter(tabela=tabela, ativo=True, DataInicio__lte=hoje)
        .filter(Q(DataFim__isnull=True) | Q(DataFim__gte=hoje))
        .order_by("produto_id", "-DataInicio", "-Idprodutopreco")
    )
    por_produto = {}
    for preco in precos:
        por_produto.setdefault(preco.produto_id, preco)
    return por_produto


def _estoques_por_ean(loja, eans):
    eans_validos = [ean for ean in eans if ean]
    if not eans_validos:
        return {}
    linhas = (
        Estoque.objects.filter(Idloja=loja, CodigodeBarra__in=eans_validos)
        .values("CodigodeBarra")
        .annotate(estoque_fisico=Sum("Estoque"), reserva=Sum("reserva"))
    )
    return {
        linha["CodigodeBarra"]: {
            "estoque_fisico": linha["estoque_fisico"] or ZERO_3,
            "reserva": linha["reserva"] or ZERO_3,
        }
        for linha in linhas
    }


def _fiscal(produto):
    return {
        "ncm": produto.ncm or "",
        "origem_mercadoria": produto.origem_mercadoria,
        "cfop_venda_dentro": produto.cfop_venda_dentro or "",
        "cfop_venda_fora": produto.cfop_venda_fora or "",
        "csosn_ou_cst_icms": produto.csosn_ou_cst_icms or "",
        "aliquota_icms": produto.aliquota_icms or Decimal("0.00"),
        "cst_pis": produto.cst_pis or "",
        "aliq_pis": produto.aliq_pis or Decimal("0.00"),
        "cst_cofins": produto.cst_cofins or "",
        "aliq_cofins": produto.aliq_cofins or Decimal("0.00"),
    }


def gerar_catalogo_hub(hub):
    hoje = timezone.localdate()
    loja = hub.loja
    empresa = loja.empresa
    tabela = tabela_preco_catalogo_v1(empresa, hoje)
    skus = list(
        ProdutoDetalhe.objects.select_related("produto", "produto__unidade", "idcor", "idtamanho")
        .filter(
            ativo=True,
            bloqueado_venda=False,
            produto__empresa=empresa,
            produto__ativo=True,
            produto__bloqueado_venda=False,
            produto__tipo_produto__in=TIPOS_PRODUTO_CATALOGO,
        )
        .order_by("produto_id", "IdprodutoDetalhe")
    )
    precos = _precos_por_produto(tabela, hoje)
    estoques = _estoques_por_ean(loja, [sku.ean13 for sku in skus])

    itens = []
    for sku in skus:
        produto = sku.produto
        preco_obj = precos.get(produto.pk)
        preco = preco_obj.preco if preco_obj else None
        preco_promocional = preco_obj.preco_promocional if preco_obj else None
        preco_venda = preco_promocional or preco
        estoque = estoques.get(sku.ean13, {"estoque_fisico": ZERO_3, "reserva": ZERO_3})
        estoque_fisico = estoque["estoque_fisico"] or ZERO_3
        reserva = estoque["reserva"] or ZERO_3
        estoque_disponivel = estoque_fisico - reserva
        motivos = []
        if not preco_venda or preco_venda <= ZERO_4:
            motivos.append(MOTIVO_SEM_PRECO)
            preco_venda = None
        if estoque_disponivel <= ZERO_3:
            motivos.append(MOTIVO_SEM_ESTOQUE)

        itens.append(
            {
                "produto_id": produto.pk,
                "sku_id": sku.pk,
                "tipo_produto": produto.tipo_produto,
                "referencia": produto.referencia or "",
                "descricao": produto.descricao,
                "descricao_reduzida": produto.descricao_reduzida or produto.descricao,
                "ean13": sku.ean13 or None,
                "codigo_item_ref": sku.codigo_item_ref or "",
                "cor": {"id": sku.idcor_id, "descricao": sku.idcor.Descricao},
                "tamanho": {"id": sku.idtamanho_id, "descricao": sku.idtamanho.Tamanho},
                "unidade": {
                    "id": produto.unidade_id,
                    "codigo": produto.unidade.Codigo or "",
                    "descricao": produto.unidade.Descricao,
                },
                "preco": preco,
                "preco_promocional": preco_promocional,
                "preco_venda": preco_venda,
                "estoque_fisico": estoque_fisico,
                "reserva": reserva,
                "estoque_disponivel": estoque_disponivel,
                "vendavel": not motivos,
                "motivos_bloqueio": motivos,
                "fiscal": _fiscal(produto),
            }
        )

    return {
        "catalogo_versao": CATALOGO_VERSAO,
        "gerado_em": timezone.now(),
        "hub": {"id": hub.pk, "hub_uuid": str(hub.hub_uuid)},
        "empresa": {"id": empresa.pk, "nome": empresa.nome},
        "loja": {"id": loja.pk, "nome": loja.nome_loja, "apelido": loja.apelido_loja},
        "tabela_preco": (
            {"codigo": CATALOGO_TABELA_PRECO_CODIGO_V1, "id": tabela.pk, "nome": tabela.NomeTabela} if tabela else None
        ),
        "total_itens": len(itens),
        "itens": itens,
    }
