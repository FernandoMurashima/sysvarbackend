from django.core.management.base import BaseCommand
from django.db import connection

from cadastros.models import Cliente, Empresa
from cadastros.services import ClientePadraoService


class Command(BaseCommand):
    help = "Diagnostica e, opcionalmente, cria cliente padrão em situações determinísticas."

    def add_arguments(self, parser):
        parser.add_argument("--empresa-id", type=int)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        empresas = Empresa.objects.order_by("id")
        if options.get("empresa_id"):
            empresas = empresas.filter(pk=options["empresa_id"])
        columns = {c.name for c in connection.introspection.get_table_description(connection.cursor(), Cliente._meta.db_table)}
        total_criados = 0
        for empresa in empresas:
            qs = Cliente.objects.filter(empresa=empresa)
            if "cliente_padrao" in columns:
                marcados = list(qs.filter(cliente_padrao=True).values_list("id", flat=True))
            else:
                marcados = []
            doc_field = "documento" if "documento" in columns else "cpf"
            doc000 = list(qs.filter(**{doc_field: Cliente.DOCUMENTO_CONSUMIDOR_FINAL}).values_list("id", flat=True))
            self.stdout.write(
                f"empresa={empresa.pk} sem_padrao={not marcados and not doc000} "
                f"padroes={marcados} documento_000={doc000}"
            )
            if options["apply"] and not marcados and not doc000:
                _, created = ClientePadraoService.obter_ou_criar(empresa, aplicar=True)
                total_criados += 1 if created else 0
        if options["apply"]:
            self.stdout.write(self.style.SUCCESS(f"clientes_padrao_criados={total_criados}"))
