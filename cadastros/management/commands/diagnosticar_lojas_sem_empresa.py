from django.core.management.base import BaseCommand

from cadastros.models import Loja


class Command(BaseCommand):
    help = "Lista estabelecimentos sem empresa vinculada para saneamento antes de tornar a FK obrigatória."

    def handle(self, *args, **options):
        qs = Loja.objects.filter(empresa__isnull=True).order_by("id")
        total = qs.count()
        self.stdout.write(f"lojas_sem_empresa={total}")
        for loja in qs.iterator():
            self.stdout.write(f"id={loja.id}; nome={loja.nome_loja}; cnpj={loja.cnpj}")
