from decimal import Decimal

from django.core.management.base import BaseCommand

from financeiro.models import CashbackConfig
from fiscal.models import VendaPdv
from fiscal.models.venda_pdv import money


def cliente_padrao(cliente):
    cpf = "".join(ch for ch in str(getattr(cliente, "cpf", "") or "") if ch.isdigit())
    nome = (getattr(cliente, "nome_cliente", "") or "").lower()
    return cpf == "00000000000" or "consumidor final" in nome


class Command(BaseCommand):
    help = "Mostra por que vendas PDV geraram ou não geraram cashback."

    def add_arguments(self, parser):
        parser.add_argument("--limite", type=int, default=20, help="Quantidade de vendas recentes para analisar.")

    def handle(self, *args, **options):
        limite = options["limite"]
        config = CashbackConfig.regra_ativa()
        if config:
            self.stdout.write(self.style.SUCCESS(
                f"Regra ativa: {config.nome} | {config.percentual}% | validade {config.validade_dias} dias"
            ))
        else:
            self.stdout.write(self.style.WARNING("Não existe regra de cashback ativa."))

        vendas = (
            VendaPdv.objects
            .select_related("cliente")
            .prefetch_related("cashback_creditos", "cashback_usos", "pagamentos")
            .filter(status=VendaPdv.Status.FINALIZADA)
            .order_by("-data_venda")[:limite]
        )

        if not vendas:
            self.stdout.write(self.style.WARNING("Nenhuma venda PDV finalizada encontrada."))
            return

        for venda in vendas:
            creditos = list(venda.cashback_creditos.all())
            debitos = list(venda.cashback_usos.all())
            cliente = venda.cliente
            motivos = []

            if not config:
                motivos.append("sem regra ativa")
            elif not config.ativo:
                motivos.append("regra inativa")
            elif Decimal(config.percentual or 0) <= 0:
                motivos.append("percentual zerado")
            elif cliente_padrao(cliente) and not config.consumidor_final_participa:
                motivos.append("cliente consumidor final não participa")
            else:
                cashback_usado = money(sum((p.valor for p in venda.pagamentos.all() if p.forma == "CASHBACK"), Decimal("0.00")))
                base = money(venda.total - cashback_usado)
                if base < money(config.valor_minimo_geracao):
                    motivos.append("valor abaixo do mínimo para gerar")

            status = "OK" if creditos else "SEM CRÉDITO"
            self.stdout.write(
                f"{venda.documento} | {cliente.nome_cliente} | total {money(venda.total)} | "
                f"{status} | créditos {len(creditos)} | usos {len(debitos)}"
            )
            if motivos:
                self.stdout.write(f"  Motivo provável: {', '.join(motivos)}")
