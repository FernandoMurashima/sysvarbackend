import json

from django.core.exceptions import ValidationError, ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from hub.backup_integrity import auditar_backups_hub


class Command(BaseCommand):
    help = "Diagnostica integridade e planeja retencao por Hub. Sempre DRY-RUN; nunca apaga arquivos."

    def add_arguments(self, parser):
        for name in ("empresa", "loja", "hub"):
            parser.add_argument("--" + name, required=True, type=int)
        parser.add_argument("--dry-run", action="store_true", help="Explicito; modo padrao e unico do comando.")

    def handle(self, *args, **options):
        try:
            report = auditar_backups_hub(empresa_id=options["empresa"], loja_id=options["loja"], hub_id=options["hub"])
        except (ValidationError, ImproperlyConfigured):
            raise CommandError("Escopo ou configuracao de auditoria invalido; verificar cadastro/fuso privado.") from None
        except Exception:
            raise CommandError("Auditoria indisponivel; nenhum arquivo foi removido. Acionar suporte.") from None
        self.stdout.write(json.dumps(report, ensure_ascii=True, indent=2))
