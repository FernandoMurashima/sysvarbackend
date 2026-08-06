from django.core.management.base import BaseCommand, CommandError

from cadastros.models import Cliente, Empresa


class Command(BaseCommand):
    help = "Diagnostica clientes sem empresa. A correção exige --apply e --empresa-id."

    def add_arguments(self, parser):
        parser.add_argument("--empresa-id", type=int)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        qs = Cliente.objects.filter(empresa__isnull=True).order_by("id")
        ids = list(qs.values_list("id", flat=True))
        self.stdout.write(f"clientes_sem_empresa={len(ids)} ids={ids}")
        if not options["apply"]:
            return
        if not options.get("empresa_id"):
            raise CommandError("Informe --empresa-id para aplicar correção.")
        if not Empresa.objects.filter(pk=options["empresa_id"]).exists():
            raise CommandError("Empresa informada não existe.")
        qs.update(empresa_id=options["empresa_id"])
        self.stdout.write(self.style.SUCCESS(f"clientes_atualizados={len(ids)} empresa_id={options['empresa_id']}"))
