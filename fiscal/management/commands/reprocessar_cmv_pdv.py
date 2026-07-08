from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import models, transaction
from django.utils import timezone

from cadastros.models import Nat_Lancamento, PlanoContabil
from financeiro.models import MovimentacaoFinanceira
from financeiro.services import gerar_lancamento_contabil_movimentacao
from fiscal.models import VendaDevolucao, VendaPdv
from fiscal.models.venda_pdv import money


def natureza_cmv(empresa):
    natureza = (
        Nat_Lancamento.objects
        .filter(empresa=empresa, ativo=True, natureza_operacao="DESPESA")
        .filter(models.Q(codigo="2100") | models.Q(descricao__icontains="CMV") | models.Q(descricao__icontains="mercadoria vendida"))
        .order_by("codigo")
        .first()
    )
    if natureza:
        return natureza

    plano = (
        PlanoContabil.objects
        .filter(empresa=empresa, ativa=True, classe=PlanoContabil.CLASSE_CUSTO)
        .filter(models.Q(codigo="5.1.01") | models.Q(descricao__icontains="CMV"))
        .order_by("codigo")
        .first()
    )
    return Nat_Lancamento.objects.create(
        empresa=empresa,
        codigo="2100",
        categoria_principal="CUSTOS DAS MERCADORIAS",
        subcategoria="CMV",
        descricao="CMV - Custo da mercadoria vendida",
        tipo="DESPESA",
        status="ATIVO",
        tipo_natureza="DEBITO",
        natureza_operacao="DESPESA",
        categoria_gerencial="CMV",
        movimenta_financeiro=False,
        entra_dre=True,
        plano_contabil=plano,
        conta_contabil=plano.codigo if plano else None,
        ativo=True,
    )


def custo_sku(sku):
    custo = Decimal(sku.custo_ultima_compra or sku.custo_original or 0)
    if custo > 0:
        return custo
    referencia = (
        sku.__class__.objects
        .filter(produto_id=sku.produto_id, custo_ultima_compra__gt=0)
        .order_by("-custo_ultima_compra")
        .values_list("custo_ultima_compra", flat=True)
        .first()
    )
    return Decimal(referencia or 0)


class Command(BaseCommand):
    help = "Reprocessa CMV ausente das vendas PDV e devoluções, sem alterar caixa/banco."

    def add_arguments(self, parser):
        parser.add_argument("--empresa", type=int, help="Reprocessa apenas uma empresa.")

    @transaction.atomic
    def handle(self, *args, **options):
        vendas = VendaPdv.objects.prefetch_related("itens__sku", "devolucoes__itens").filter(status=VendaPdv.Status.FINALIZADA)
        if options.get("empresa"):
            vendas = vendas.filter(empresa_id=options["empresa"])

        movimentos = 0
        itens_atualizados = 0
        for venda in vendas:
            for item in venda.itens.all():
                if Decimal(item.cmv_total or 0) > 0:
                    continue
                custo = Decimal(item.custo_unitario or 0) or custo_sku(item.sku)
                if custo <= 0:
                    continue
                item.custo_unitario = custo
                item.cmv_total = money(Decimal(item.quantidade or 0) * custo)
                item.save(update_fields=["custo_unitario", "cmv_total"])
                itens_atualizados += 1

            movimentos += self._gerar_cmv_venda(venda)
            for devolucao in venda.devolucoes.exclude(status=VendaDevolucao.Status.CANCELADA):
                for item in devolucao.itens.all():
                    if Decimal(item.cmv_total or 0) > 0:
                        continue
                    custo = Decimal(item.custo_unitario or item.venda_item.custo_unitario or 0)
                    if custo <= 0:
                        continue
                    item.custo_unitario = custo
                    item.cmv_total = money(Decimal(item.quantidade or 0) * custo)
                    item.save(update_fields=["custo_unitario", "cmv_total"])
                    itens_atualizados += 1
                movimentos += self._gerar_estorno_devolucao(devolucao)

        self.stdout.write(self.style.SUCCESS(
            f"CMV reprocessado. Itens atualizados: {itens_atualizados}. Movimentos gerados: {movimentos}."
        ))

    def _gerar_cmv_venda(self, venda):
        if MovimentacaoFinanceira.objects.filter(
            empresa=venda.empresa,
            origem=MovimentacaoFinanceira.ORIGEM_CMV,
            documento=venda.documento,
        ).exists():
            return 0
        total = money(sum((Decimal(item.cmv_total or 0) for item in venda.itens.all()), Decimal("0.00")))
        if total <= 0:
            return 0
        movimento = MovimentacaoFinanceira.objects.create(
            empresa=venda.empresa,
            idloja=venda.loja,
            data_movimento=getattr(venda, "data_venda", None) or timezone.localdate(),
            tipo=MovimentacaoFinanceira.TIPO_SAIDA,
            status=MovimentacaoFinanceira.STATUS_EFETIVA,
            origem=MovimentacaoFinanceira.ORIGEM_CMV,
            valor=total,
            historico=f"CMV venda PDV {venda.documento}",
            documento=venda.documento,
            Idnatureza=natureza_cmv(venda.empresa),
            FormaPagamento="CMV",
        )
        gerar_lancamento_contabil_movimentacao(movimento)
        return 1

    def _gerar_estorno_devolucao(self, devolucao):
        if MovimentacaoFinanceira.objects.filter(
            empresa=devolucao.empresa,
            origem=MovimentacaoFinanceira.ORIGEM_CMV,
            documento=devolucao.documento,
        ).exists():
            return 0
        total = money(sum((Decimal(item.cmv_total or 0) for item in devolucao.itens.all()), Decimal("0.00")))
        if total <= 0:
            return 0
        movimento = MovimentacaoFinanceira.objects.create(
            empresa=devolucao.empresa,
            idloja=devolucao.loja,
            data_movimento=timezone.localdate(),
            tipo=MovimentacaoFinanceira.TIPO_ENTRADA,
            status=MovimentacaoFinanceira.STATUS_EFETIVA,
            origem=MovimentacaoFinanceira.ORIGEM_CMV,
            valor=total,
            historico=f"Estorno CMV devolução {devolucao.documento}",
            documento=devolucao.documento,
            Idnatureza=natureza_cmv(devolucao.empresa),
            FormaPagamento="CMV",
        )
        gerar_lancamento_contabil_movimentacao(movimento)
        return 1
