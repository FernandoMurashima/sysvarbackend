from django.core.management.base import BaseCommand

from fiscal.services.recebimento_custos import reparar_custos_recebimentos_mercadoria


class Command(BaseCommand):
    help = "Repara custos de SKUs e movimentações gerados por recebimentos de mercadoria já efetivados."

    def add_arguments(self, parser):
        parser.add_argument("--empresa-id", type=int, default=None, help="Limita a reparação a uma empresa.")

    def handle(self, *args, **options):
        stats = reparar_custos_recebimentos_mercadoria(empresa_id=options.get("empresa_id"))
        for chave, valor in stats.items():
            self.stdout.write(f"{chave}: {valor}")
