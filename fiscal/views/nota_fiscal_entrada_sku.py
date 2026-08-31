from decimal import Decimal

from produto.models import Estoque, EstoqueMovimentacao, ProdutoDetalhe, ProdutoUsoConsumoEstoque

from .nota_fiscal_entrada import (
    NotaFiscalEntradaViewSet as NotaFiscalEntradaBaseViewSet,
    _money,
    _q3,
    _q4,
)


class NotaFiscalEntradaViewSet(NotaFiscalEntradaBaseViewSet):
    """Ajustes específicos de estoque por SKU para NF-e XML de revenda."""

    def _codigo_estoque_item_xml(self, item):
        produto = item.produto
        if not produto or produto.tipo_produto != "1":
            return self._codigo_estoque_produto(produto)

        gtin = str(item.gtin_ean or "").strip()
        if not gtin:
            raise ValueError(
                f"Item XML {item.numero_item} do produto de revenda "
                f"{produto.referencia or produto.pk} não possui GTIN/EAN para identificar o SKU."
            )

        try:
            sku = ProdutoDetalhe.objects.only("ean13").get(
                produto_id=produto.pk,
                ean13=gtin,
                ativo=True,
            )
        except ProdutoDetalhe.DoesNotExist as exc:
            raise ValueError(
                f"SKU/EAN {gtin} do item XML {item.numero_item} não pertence ao produto conciliado "
                f"{produto.referencia or produto.pk}."
            ) from exc
        except ProdutoDetalhe.MultipleObjectsReturned as exc:
            raise ValueError(
                f"SKU/EAN {gtin} do item XML {item.numero_item} está duplicado para o produto conciliado."
            ) from exc

        return sku.ean13

    def _codigo_estoque_cancelamento_xml(self, nota, item):
        documento_entrada = self._documento_estoque(nota, "ENTRADA")
        movimento_entrada = (
            EstoqueMovimentacao.objects.filter(
                documento=documento_entrada,
                tipo=EstoqueMovimentacao.TIPO_ENTRADA,
                observacao__contains=f";ITEM:{item.pk}",
            )
            .order_by("Idmovimento")
            .first()
        )
        if movimento_entrada:
            return movimento_entrada.CodigodeBarra
        return self._codigo_estoque_item_xml(item)

    def _validar_pronto_xml(self, nota):
        super()._validar_pronto_xml(nota)
        for item in nota.itens_xml.select_related("produto").order_by("numero_item"):
            if item.produto and item.produto.tipo_produto == "1":
                self._codigo_estoque_item_xml(item)

    def _movimentar_produto_xml_estoque(self, nota, item, qtd, documento):
        produto = item.produto
        codigo = self._codigo_estoque_item_xml(item)
        custo_movimento = _q4(
            item.valor_unitario_comercial
            or produto.custo_medio
            or produto.custo_ultima_compra
            or produto.custo_original
            or 0
        )
        estoque, _ = Estoque.objects.select_for_update().get_or_create(
            CodigodeBarra=codigo,
            Idloja=nota.loja,
            defaults={"referencia": produto.referencia or "", "Estoque": 0, "reserva": 0},
        )
        anterior = Decimal(estoque.Estoque or 0)
        posterior = anterior + qtd
        estoque.referencia = produto.referencia or estoque.referencia
        estoque.Estoque = posterior
        estoque.reserva = estoque.reserva or 0
        estoque.save(update_fields=["referencia", "Estoque", "reserva"])
        EstoqueMovimentacao.objects.create(
            Idloja=nota.loja,
            CodigodeBarra=codigo,
            referencia=produto.referencia or "",
            tipo=EstoqueMovimentacao.TIPO_ENTRADA,
            quantidade=_q3(qtd),
            custo_unitario=custo_movimento,
            custo_total=_money(qtd * custo_movimento),
            custo_medio_apos=_q4(
                produto.custo_medio
                or produto.custo_ultima_compra
                or produto.custo_original
                or custo_movimento
            ),
            saldo_anterior=anterior,
            saldo_posterior=posterior,
            origem=EstoqueMovimentacao.ORIGEM_NFE,
            documento=documento,
            observacao=f"Nota fiscal de entrada XML {nota.numero};ITEM:{item.pk}",
        )
        return 1

    def _alvos_estoque_cancelamento(self, nota):
        if not nota.xml_importado:
            return super()._alvos_estoque_cancelamento(nota)

        alvos = []
        for item in nota.itens_xml.select_related("produto"):
            produto = item.produto
            qtd = _q3(item.quantidade_interna_efetivada or 0)
            if not produto or qtd <= 0:
                continue
            if produto.tipo_produto == "2":
                saldo = (
                    ProdutoUsoConsumoEstoque.objects.filter(
                        empresa=nota.empresa,
                        loja=nota.loja,
                        produto=produto,
                    )
                    .values_list("saldo", flat=True)
                    .first()
                    or 0
                )
                alvos.append(
                    {
                        "uso_consumo": True,
                        "produto": produto.pk,
                        "codigo": None,
                        "quantidade": qtd,
                        "saldo": saldo,
                    }
                )
                continue

            codigo = self._codigo_estoque_cancelamento_xml(nota, item)
            saldo = (
                Estoque.objects.filter(CodigodeBarra=codigo, Idloja=nota.loja)
                .values_list("Estoque", flat=True)
                .first()
                or 0
            )
            alvos.append(
                {
                    "uso_consumo": False,
                    "produto": produto.pk,
                    "codigo": codigo,
                    "quantidade": qtd,
                    "saldo": saldo,
                }
            )
        return alvos

    def _estornar_produto_xml_estoque(self, nota, item, qtd, motivo, documento):
        produto = item.produto
        codigo = self._codigo_estoque_cancelamento_xml(nota, item)
        custo_movimento = _q4(
            item.valor_unitario_comercial
            or produto.custo_medio
            or produto.custo_ultima_compra
            or produto.custo_original
            or 0
        )
        estoque, _ = Estoque.objects.select_for_update().get_or_create(
            CodigodeBarra=codigo,
            Idloja=nota.loja,
            defaults={"referencia": produto.referencia or "", "Estoque": 0, "reserva": 0},
        )
        anterior = Decimal(estoque.Estoque or 0)
        posterior = anterior - qtd
        estoque.referencia = produto.referencia or estoque.referencia
        estoque.Estoque = posterior
        estoque.reserva = estoque.reserva or 0
        estoque.save(update_fields=["referencia", "Estoque", "reserva"])
        EstoqueMovimentacao.objects.create(
            Idloja=nota.loja,
            CodigodeBarra=codigo,
            referencia=produto.referencia or "",
            tipo=EstoqueMovimentacao.TIPO_SAIDA,
            quantidade=qtd,
            custo_unitario=custo_movimento,
            custo_total=_money(qtd * custo_movimento),
            custo_medio_apos=_q4(
                produto.custo_medio
                or produto.custo_ultima_compra
                or produto.custo_original
                or custo_movimento
            ),
            saldo_anterior=anterior,
            saldo_posterior=posterior,
            origem=EstoqueMovimentacao.ORIGEM_NFE,
            documento=documento,
            observacao=f"Cancelamento NF-e XML {nota.numero};ITEM:{item.pk};MOTIVO:{motivo}"[:255],
        )
        return 1
