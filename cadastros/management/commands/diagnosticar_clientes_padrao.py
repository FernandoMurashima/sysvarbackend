from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from cadastros.models import Cliente, Empresa
from cadastros.services import ClientePadraoService
from cadastros.validators import only_digits


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
        ambiguidades = []
        empresas_para_criar = []
        for empresa in empresas:
            qs = Cliente.objects.filter(empresa=empresa)
            if "cliente_padrao" in columns:
                marcados_qs = qs.filter(cliente_padrao=True)
                marcados = list(marcados_qs.values_list("id", flat=True))
            else:
                marcados_qs = qs.none()
                marcados = []
            doc_field = "documento" if "documento" in columns else "cpf"
            doc000 = list(qs.filter(**{doc_field: Cliente.DOCUMENTO_CONSUMIDOR_FINAL}).values_list("id", flat=True))
            marcados_incompativeis = []
            if "cliente_padrao" in columns:
                for cliente in marcados_qs.values("id", doc_field):
                    if only_digits(cliente.get(doc_field)) != Cliente.DOCUMENTO_CONSUMIDOR_FINAL:
                        marcados_incompativeis.append(cliente["id"])
            comuns_doc000 = []
            if "cliente_padrao" in columns:
                comuns_doc000 = list(qs.filter(**{doc_field: Cliente.DOCUMENTO_CONSUMIDOR_FINAL, "cliente_padrao": False}).values_list("id", flat=True))
            conflito_marcacao_documento = bool(marcados and doc000 and set(marcados) != set(doc000))
            self.stdout.write(
                f"empresa={empresa.pk} sem_padrao={not marcados and not doc000} "
                f"padroes={marcados} documento_000={doc000}"
            )
            problemas = {
                "padroes_duplicados": marcados if len(marcados) > 1 else [],
                "documento_000_duplicado": doc000 if len(doc000) > 1 else [],
                "padrao_documento_incompativel": marcados_incompativeis,
                "documento_000_sem_marcacao": comuns_doc000,
                "conflito_marcacao_documento": {"marcados": marcados, "documento_000": doc000} if conflito_marcacao_documento else None,
            }
            problemas = {k: v for k, v in problemas.items() if v}
            if problemas:
                ambiguidades.append({"empresa": empresa.pk, **problemas})
                self.stdout.write(self.style.WARNING(f"empresa={empresa.pk} ambiguidades={problemas}"))
            if not marcados and not doc000:
                empresas_para_criar.append(empresa)
        if options["apply"]:
            if ambiguidades:
                raise CommandError("Correção interrompida: use diagnosticar_clientes para validar ambiguidades antes de aplicar.")
            for empresa in empresas_para_criar:
                _, created = ClientePadraoService.obter_ou_criar(empresa, aplicar=True)
                total_criados += 1 if created else 0
            self.stdout.write(self.style.SUCCESS(f"clientes_padrao_criados={total_criados}"))
