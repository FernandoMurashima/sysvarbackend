from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from cadastros.models import Cliente, Empresa
from cadastros.services import ClientePadraoService
from cadastros.validators import check_cnpj, check_cpf, only_digits


class Command(BaseCommand):
    help = "Diagnostica clientes sem empresa, documentos, duplicidades e cliente padrão."

    def add_arguments(self, parser):
        parser.add_argument("--empresa-id", type=int)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        apply = bool(options["apply"])
        columns = {c.name for c in connection.introspection.get_table_description(connection.cursor(), Cliente._meta.db_table)}
        fields = ["id", "empresa_id", "cpf"]
        for field in ("documento", "tipo_pessoa", "cliente_padrao"):
            if field in columns:
                fields.append(field)
        qs = Cliente.objects.order_by("empresa_id", "id").values(*fields)
        if options.get("empresa_id"):
            qs = qs.filter(empresa_id=options["empresa_id"])
        clientes = list(qs)
        empresas = Empresa.objects.all()
        if options.get("empresa_id"):
            empresas = empresas.filter(pk=options["empresa_id"])

        sem_empresa = [c["id"] for c in clientes if not c.get("empresa_id")]
        duplicados = defaultdict(list)
        invalidos = []
        cpf_14 = []
        comum_doc_000 = []
        padrao_por_empresa = defaultdict(list)
        doc000_por_empresa = defaultdict(list)

        for c in clientes:
            doc = only_digits(c.get("documento") or c.get("cpf"))
            empresa_id = c.get("empresa_id")
            tipo = c.get("tipo_pessoa") or Cliente.TIPO_PESSOA_FISICA
            cliente_padrao = bool(c.get("cliente_padrao")) if "cliente_padrao" in c else doc == Cliente.DOCUMENTO_CONSUMIDOR_FINAL
            if empresa_id and doc:
                duplicados[(empresa_id, doc)].append(c["id"])
            if doc and tipo == Cliente.TIPO_PESSOA_FISICA and len(doc) == 14:
                cpf_14.append(c["id"])
            if doc and doc != Cliente.DOCUMENTO_CONSUMIDOR_FINAL:
                if tipo == Cliente.TIPO_PESSOA_JURIDICA:
                    ok = check_cnpj(doc)
                else:
                    ok = check_cpf(doc)
                if not ok:
                    invalidos.append(c["id"])
            if doc == Cliente.DOCUMENTO_CONSUMIDOR_FINAL:
                doc000_por_empresa[empresa_id].append(c["id"])
                if not cliente_padrao:
                    comum_doc_000.append(c["id"])
            if cliente_padrao:
                padrao_por_empresa[empresa_id].append(c["id"])

        duplicados = {str(k): v for k, v in duplicados.items() if len(v) > 1}
        empresas_sem_padrao = []
        empresas_com_padrao_duplicado = {}
        conflitos_padrao = []
        for empresa in empresas:
            marcados = padrao_por_empresa.get(empresa.pk, [])
            docs = doc000_por_empresa.get(empresa.pk, [])
            if not marcados and not docs:
                empresas_sem_padrao.append(empresa.pk)
            if len(marcados) > 1:
                empresas_com_padrao_duplicado[empresa.pk] = marcados
            if marcados and docs and set(marcados) != set(docs):
                conflitos_padrao.append({"empresa": empresa.pk, "marcados": marcados, "documento_000": docs})

        self.stdout.write(f"clientes_sem_empresa={len(sem_empresa)} ids={sem_empresa}")
        self.stdout.write(f"documentos_duplicados={len(duplicados)} {dict(duplicados)}")
        self.stdout.write(f"documentos_invalidos={len(invalidos)} ids={invalidos}")
        self.stdout.write(f"cpf_com_14_digitos={len(cpf_14)} ids={cpf_14}")
        self.stdout.write(f"cliente_comum_documento_000={len(comum_doc_000)} ids={comum_doc_000}")
        self.stdout.write(f"empresas_sem_cliente_padrao={len(empresas_sem_padrao)} ids={empresas_sem_padrao}")
        self.stdout.write(f"empresas_com_padrao_duplicado={len(empresas_com_padrao_duplicado)} {empresas_com_padrao_duplicado}")
        self.stdout.write(f"conflitos_padrao={len(conflitos_padrao)} {conflitos_padrao}")

        ambiguidades = sem_empresa or duplicados or invalidos or cpf_14 or comum_doc_000 or empresas_com_padrao_duplicado or conflitos_padrao
        if apply:
            if ambiguidades:
                raise CommandError("Correção interrompida: há ambiguidade ou inconsistência não determinística.")
            with transaction.atomic():
                for empresa in empresas.filter(pk__in=empresas_sem_padrao):
                    ClientePadraoService.obter_ou_criar(empresa, aplicar=True)
            self.stdout.write(self.style.SUCCESS(f"clientes_padrao_criados={len(empresas_sem_padrao)}"))
