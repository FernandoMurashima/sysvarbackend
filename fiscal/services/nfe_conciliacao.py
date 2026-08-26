from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from auditoria.models import AuditAction, AuditCategory
from auditoria.services import AuditService
from fiscal.models import NotaFiscalEntrada, NotaFiscalEntradaItemXml
from produto.models import Produto, ProdutoDetalhe, ProdutoFornecedor


def normalizar_unidade(value):
    return str(value or "").strip().upper()


def conversao_info(item):
    vinculo = item.produto_fornecedor
    produto = item.produto
    unidade_interna = getattr(getattr(produto, "unidade", None), "Codigo", "") or ""
    info = {
        "unidade_fornecedor": getattr(vinculo, "unidade_fornecedor", "") or "",
        "fator_conversao": str(getattr(vinculo, "fator_conversao", "")) if vinculo else "",
        "unidade_interna": unidade_interna,
        "quantidade_interna_calculada": None,
        "conversao_pendente": False,
    }
    if not vinculo:
        info["conversao_pendente"] = bool(produto)
        return info
    unidade_xml = normalizar_unidade(item.unidade_comercial)
    unidade_vinculo = normalizar_unidade(vinculo.unidade_fornecedor)
    if unidade_vinculo and unidade_xml == unidade_vinculo:
        info["quantidade_interna_calculada"] = str(vinculo.converter_quantidade_fornecedor(item.quantidade_comercial))
    else:
        info["conversao_pendente"] = True
    return info


def resumo_conciliacao(nota):
    return nota.resumo_conciliacao_xml()


def conciliar_automaticamente(nota, user=None, request=None):
    stats = {"processados": 0, "conciliados": 0, "pendentes": 0}
    for item in nota.itens_xml.select_for_update().filter(produto__isnull=True).order_by("numero_item"):
        stats["processados"] += 1
        produto = produto_fornecedor = origem = None
        vinculo = _vinculo_por_codigo(nota, item)
        if vinculo:
            produto_fornecedor = vinculo
            produto = vinculo.produto
            origem = NotaFiscalEntradaItemXml.OrigemConciliacao.VINCULO
        elif item.gtin_ean:
            produto, produto_fornecedor, origem = _produto_por_gtin(nota, item)
        if not produto and nota.pedido_compra_id:
            produto, produto_fornecedor, origem = _produto_por_pedido(nota, item)

        if produto:
            item.produto = produto
            item.produto_fornecedor = produto_fornecedor
            if nota.pedido_compra_id:
                item.pedido_item = _pedido_item_por_produto(nota, produto)
            item.origem_conciliacao = origem
            item.conciliado_por = user if getattr(user, "is_authenticated", False) else None
            item.conciliado_em = timezone.now()
            item.save(update_fields=["produto", "produto_fornecedor", "pedido_item", "origem_conciliacao", "conciliado_por", "conciliado_em"])
            stats["conciliados"] += 1
        else:
            stats["pendentes"] += 1
    if request and stats["processados"]:
        AuditService.success(
            AuditAction.OBJECT_UPDATED,
            category=AuditCategory.FISCAL,
            request=request,
            user=user,
            instance=nota,
            metadata={"legacy_action": "conciliacao_automatica_xml", **stats, **resumo_conciliacao(nota)},
        )
    return stats


@transaction.atomic
def conciliar_manual(item, produto_id, user=None, request=None):
    item = NotaFiscalEntradaItemXml.objects.select_for_update().select_related("nota__fornecedor").get(pk=item.pk)
    nota = item.nota
    if nota.status != NotaFiscalEntrada.Status.ABERTA:
        raise ValidationError({"nota": "Somente notas abertas podem ser conciliadas."})
    produto = Produto.objects.select_related("unidade").filter(pk=produto_id, empresa_id=nota.empresa_id).first()
    if not produto:
        raise ValidationError({"produto": "Produto não encontrado para a empresa da NF."})

    vinculo = None
    codigo = ProdutoFornecedor.normalizar_codigo(item.codigo_produto_fornecedor)
    if codigo:
        vinculo = ProdutoFornecedor.objects.select_for_update().filter(
            empresa_id=nota.empresa_id,
            fornecedor_id=nota.fornecedor_id,
            codigo_vigente=codigo,
        ).first()
        if vinculo and vinculo.produto_id != produto.pk:
            raise ValidationError({"produto": "Código externo já vinculado a outro Produto Sysvar."})
        if not vinculo:
            vinculo = ProdutoFornecedor.objects.create(
                empresa_id=nota.empresa_id,
                fornecedor_id=nota.fornecedor_id,
                produto=produto,
                codigo_produto_fornecedor=codigo,
                descricao_fornecedor=item.descricao_produto,
                gtin_ean=item.gtin_ean,
                unidade_fornecedor=item.unidade_comercial,
                fator_conversao=Decimal("1"),
                criado_por=user if getattr(user, "is_authenticated", False) else None,
            )
            if request:
                AuditService.success(
                    AuditAction.OBJECT_CREATED,
                    category=AuditCategory.PRODUCT,
                    request=request,
                    user=user,
                    instance=vinculo,
                    metadata={
                        "legacy_action": "produto_fornecedor_criado_conciliacao_nfe",
                        "nota": nota.pk,
                        "item_xml": item.pk,
                        "codigo_externo": codigo,
                    },
                )

    before = {"produto": item.produto_id, "produto_fornecedor": item.produto_fornecedor_id}
    item.produto = produto
    item.produto_fornecedor = vinculo
    item.pedido_item = _pedido_item_por_produto(nota, produto)
    item.origem_conciliacao = NotaFiscalEntradaItemXml.OrigemConciliacao.MANUAL
    item.conciliado_por = user if getattr(user, "is_authenticated", False) else None
    item.conciliado_em = timezone.now()
    item.save(update_fields=["produto", "produto_fornecedor", "pedido_item", "origem_conciliacao", "conciliado_por", "conciliado_em"])
    if request:
        AuditService.success(
            AuditAction.OBJECT_UPDATED,
            category=AuditCategory.FISCAL,
            request=request,
            user=user,
            instance=item,
            before=before,
            after={"produto": item.produto_id, "produto_fornecedor": item.produto_fornecedor_id},
            metadata={
                "legacy_action": "conciliacao_manual_xml",
                "nota": nota.pk,
                "item_xml": item.pk,
                "fornecedor": nota.fornecedor_id,
                "codigo_externo": codigo,
            },
        )
    return item


def candidatos_item(item, search=""):
    nota = item.nota
    qs = Produto.objects.select_related("unidade").filter(empresa_id=nota.empresa_id, ativo=True)
    search = str(search or item.descricao_produto or "").strip()
    if search:
        qs = qs.filter(Q(descricao__icontains=search) | Q(referencia__icontains=search))
    return qs.order_by("descricao")[:20]


def _vinculo_por_codigo(nota, item):
    codigo = ProdutoFornecedor.normalizar_codigo(item.codigo_produto_fornecedor)
    if not codigo:
        return None
    vinculos = list(
        ProdutoFornecedor.objects.select_related("produto", "produto__unidade")
        .filter(empresa_id=nota.empresa_id, fornecedor_id=nota.fornecedor_id, codigo_vigente=codigo, ativo=True)[:2]
    )
    if len(vinculos) != 1:
        return None
    vinculo = vinculos[0]
    unidade_xml = normalizar_unidade(item.unidade_comercial)
    unidade_vinculo = normalizar_unidade(vinculo.unidade_fornecedor)
    if unidade_vinculo and unidade_xml and unidade_xml != unidade_vinculo:
        return None
    return vinculo if vinculo.produto.empresa_id == nota.empresa_id else None


def _produto_por_gtin(nota, item):
    produtos = {}
    vinculos = ProdutoFornecedor.objects.select_related("produto").filter(
        empresa_id=nota.empresa_id,
        gtin_ean=item.gtin_ean,
        ativo=True,
    )
    for vinculo in vinculos:
        if vinculo.fornecedor_id == nota.fornecedor_id:
            produtos[vinculo.produto_id] = (vinculo.produto, vinculo)
        else:
            produtos.setdefault(vinculo.produto_id, (vinculo.produto, None))
    skus = ProdutoDetalhe.objects.select_related("produto", "produto__unidade").filter(produto__empresa_id=nota.empresa_id, ean13=item.gtin_ean)
    for sku in skus:
        produtos.setdefault(sku.produto_id, (sku.produto, None))
    if len(produtos) == 1:
        produto, vinculo = next(iter(produtos.values()))
        return produto, vinculo, NotaFiscalEntradaItemXml.OrigemConciliacao.GTIN
    return None, None, ""


def _produto_por_pedido(nota, item):
    if not nota.pedido_compra_id:
        return None, None, ""
    total = item.valor_produto - item.valor_desconto
    qs = nota.pedido_compra.itens.select_related("produto", "produto__unidade").filter(produto__isnull=False)
    candidatos = qs.filter(qtd=item.quantidade_comercial, preco_unit=item.valor_unitario_comercial)
    if not candidatos.exists():
        candidatos = qs.filter(total_item=total)
    produtos = {row.produto_id: row.produto for row in candidatos}
    if len(produtos) == 1:
        return next(iter(produtos.values())), None, NotaFiscalEntradaItemXml.OrigemConciliacao.PEDIDO
    return None, None, ""


def _pedido_item_por_produto(nota, produto):
    if not nota.pedido_compra_id or not produto:
        return None
    itens = list(nota.pedido_compra.itens.filter(produto=produto)[:2])
    return itens[0] if len(itens) == 1 else None
