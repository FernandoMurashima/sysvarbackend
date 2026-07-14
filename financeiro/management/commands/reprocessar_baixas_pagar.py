from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from financeiro.models import Caixa, MovimentacaoFinanceira, PagarItem
from financeiro.services import gerar_lancamento_contabil_movimentacao


class Command(BaseCommand):
    help = "Recria movimentações financeiras ausentes para parcelas do contas a pagar já baixadas."

    def add_arguments(self, parser):
        parser.add_argument("--empresa", type=int, help="Reprocessa apenas uma empresa.")
        parser.add_argument("--dry-run", action="store_true", help="Apenas lista o que seria processado.")

    @transaction.atomic
    def handle(self, *args, **options):
        qs = (
            PagarItem.objects
            .select_related("Idpagar", "Idpagar__empresa", "Idpagar__idloja", "Idpagar__idfornecedor", "Idnatureza")
            .filter(status=PagarItem.STATUS_BAIXADO, valor_baixa__gt=0)
        )
        if options.get("empresa"):
            qs = qs.filter(Idpagar__empresa_id=options["empresa"])

        criados = 0
        ignorados = 0
        sem_caixa = 0
        dry_run = options["dry_run"]

        for item in qs.order_by("Idpagar_id", "parcela_n"):
            if MovimentacaoFinanceira.objects.filter(
                pagar_item=item,
                status=MovimentacaoFinanceira.STATUS_EFETIVA,
            ).exists():
                ignorados += 1
                continue

            titulo = item.Idpagar
            caixa = (
                Caixa.objects
                .select_for_update()
                .filter(
                    empresa=titulo.empresa,
                    idloja=titulo.idloja,
                    tipo_caixa=Caixa.TIPO_LOJA,
                    ativo=True,
                )
                .order_by("Idcaixa")
                .first()
            )
            if not caixa:
                sem_caixa += 1
                self.stdout.write(self.style.WARNING(
                    f"Sem caixa ativo para {titulo.Titulo}-{item.parcela_n} ({titulo.idloja})."
                ))
                continue

            if dry_run:
                criados += 1
                continue

            valor = Decimal(item.valor_baixa or 0)
            caixa.saldo_atual = Decimal(caixa.saldo_atual or 0) - valor
            caixa.save(update_fields=["saldo_atual"])

            documento = self._documento_parcela(item)
            fornecedor = getattr(titulo.idfornecedor, "nome_fornecedor", "") or getattr(titulo.idfornecedor, "apelido", "")
            movimento = MovimentacaoFinanceira.objects.create(
                empresa=titulo.empresa,
                idloja=titulo.idloja,
                data_movimento=item.data_baixa,
                tipo=MovimentacaoFinanceira.TIPO_SAIDA,
                status=MovimentacaoFinanceira.STATUS_EFETIVA,
                origem=MovimentacaoFinanceira.ORIGEM_PAGAR,
                valor=valor,
                historico=f"Baixa contas a pagar {documento}" + (f" - {fornecedor}" if fornecedor else ""),
                documento=documento,
                Idnatureza=item.Idnatureza or titulo.Idnatureza,
                FormaPagamento=item.FormaPagamento or titulo.FormaPagamento,
                caixa=caixa,
                pagar_item=item,
            )
            gerar_lancamento_contabil_movimentacao(movimento)
            criados += 1

        msg = "simulados" if dry_run else "criados"
        self.stdout.write(self.style.SUCCESS(
            f"Baixas a pagar {msg}: {criados}. Ignoradas: {ignorados}. Sem caixa: {sem_caixa}."
        ))

    def _documento_parcela(self, item):
        titulo = str(item.Idpagar.Titulo or item.Idpagar_id)
        sufixo = f"-{item.parcela_n}"
        return titulo if titulo.endswith(sufixo) else f"{titulo}{sufixo}"
