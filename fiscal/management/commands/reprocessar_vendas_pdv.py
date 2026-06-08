from django.core.management.base import BaseCommand
from django.db import transaction

from financeiro.models import CashbackConfig
from financeiro.models import Receber
from fiscal.models import VendaPdv
from fiscal.views.venda_pdv import VendaPdvViewSet


class Command(BaseCommand):
    help = "Reprocessa vendas PDV finalizadas que ficaram sem contas a receber ou cashback."

    def add_arguments(self, parser):
        parser.add_argument(
            "--documento",
            default="",
            help="Reprocessa apenas uma venda específica pelo documento.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        documento = (options.get("documento") or "").strip()
        config = CashbackConfig.regra_ativa()
        if not config:
            self.stdout.write(self.style.WARNING(
                "Não existe regra de cashback ativa. O financeiro será reprocessado, mas cashback não será gerado."
            ))
        qs = VendaPdv.objects.filter(status=VendaPdv.Status.FINALIZADA).prefetch_related("pagamentos")
        if documento:
            qs = qs.filter(documento=documento)

        view = VendaPdvViewSet()
        financeiro = 0
        cashback = 0

        for venda in qs.order_by("id"):
            if not Receber.objects.filter(pedido_venda=venda.pk).exists():
                view._registrar_financeiro(venda)
                financeiro += 1
            antes_creditos = venda.cashback_creditos.count()
            antes_usos = venda.cashback_usos.count()
            pagamentos = [
                {"forma": pagamento.forma, "valor": pagamento.valor}
                for pagamento in venda.pagamentos.all()
            ]
            view._registrar_cashback(venda, pagamentos)
            depois_creditos = venda.cashback_creditos.count()
            depois_usos = venda.cashback_usos.count()
            if depois_creditos > antes_creditos or depois_usos > antes_usos:
                cashback += 1

        self.stdout.write(self.style.SUCCESS(
            f"Reprocessamento concluído. Financeiro criado: {financeiro}. Cashback criado: {cashback}."
        ))
