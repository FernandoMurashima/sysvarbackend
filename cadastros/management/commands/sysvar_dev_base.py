from django.core.management.base import BaseCommand, CommandError

from sysvar_devtools.dev_base import SysvarDevBaseService


class Command(BaseCommand):
    help = "Gerencia a Base de Desenvolvimento oficial do Sysvar."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--reset", action="store_true", help="Limpa a massa da base de desenvolvimento.")
        group.add_argument("--create", action="store_true", help="Cria a base de desenvolvimento sem limpeza anterior.")
        group.add_argument("--rebuild", action="store_true", help="Executa reset, create e validate.")
        group.add_argument("--validate", action="store_true", help="Valida a base de desenvolvimento.")

    def handle(self, *args, **options):
        service = SysvarDevBaseService()
        try:
            if options["reset"]:
                report = service.reset()
            elif options["create"]:
                report = service.create()
            elif options["rebuild"]:
                report = service.rebuild()
            elif options["validate"]:
                report = service.validate()
            else:
                raise CommandError("Informe uma operação.")
        except Exception as exc:
            raise CommandError(f"Falha na Base de Desenvolvimento: {exc}") from exc
        self._print_report(report)
        if not report.valid:
            raise CommandError("BASE DE DESENVOLVIMENTO: INVÁLIDA")

    def _print_report(self, report):
        self.stdout.write("Resumo da Base de Desenvolvimento")
        for key in sorted(report.created):
            self.stdout.write(f"- {key}: {report.created[key]}")
        if "usuários da base" in report.created:
            self.stdout.write("")
            self.stdout.write("USUÁRIO DE DESENVOLVIMENTO")
            self.stdout.write("admin.delegado")
            self.stdout.write("")
            self.stdout.write("SENHA")
            self.stdout.write("Sysvar@123")
            self.stdout.write("")
            self.stdout.write("USUÁRIOS DA BASE")
            self.stdout.write(str(report.created.get("usuários da base", 0)))
            self.stdout.write("")
            self.stdout.write("SUPERUSUÁRIOS RECRIADOS")
            self.stdout.write(str(report.created.get("superusuários recriados", 0)))
            self.stdout.write("")
            self.stdout.write("SUPERUSUÁRIO RECRIADO")
            self.stdout.write("takeshi")
            self.stdout.write("")
            self.stdout.write("USUÁRIOS RESIDUAIS")
            self.stdout.write(str(report.created.get("usuários residuais", 0)))
        if report.valid:
            self.stdout.write(self.style.SUCCESS("BASE DE DESENVOLVIMENTO: VÁLIDA"))
        else:
            self.stdout.write(self.style.ERROR("BASE DE DESENVOLVIMENTO: INVÁLIDA"))
            for problem in report.problems:
                self.stdout.write(f"- {problem}")
