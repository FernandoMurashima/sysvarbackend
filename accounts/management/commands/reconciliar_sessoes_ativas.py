from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import SessaoUsuario, SessionToken
from accounts.services.sessions import ConcurrentSessionService


class Command(BaseCommand):
    help = "Reconcilia sessões marcadas como ativas que não ocupam licença pelo critério central."

    def add_arguments(self, parser):
        parser.add_argument("--empresa-id", type=int)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        if options["dry_run"] == options["apply"]:
            self.stderr.write("Informe exatamente uma opção: --dry-run ou --apply.")
            return

        qs = SessaoUsuario.objects.filter(ativa=True).select_related("empresa", "usuario", "session_token").order_by("id")
        if options.get("empresa_id"):
            qs = qs.filter(empresa_id=options["empresa_id"])

        analisadas = validas = invalidas = corrigidas = 0
        linhas_invalidas = []
        now = timezone.now()

        with transaction.atomic():
            if options["apply"]:
                qs = qs.select_for_update()
            for sessao in qs:
                analisadas += 1
                valido, motivo = ConcurrentSessionService.session_validity(sessao)
                if valido:
                    validas += 1
                    continue
                invalidas += 1
                linhas_invalidas.append((sessao, motivo))
                if options["apply"]:
                    sessao.ativa = False
                    sessao.encerrada_em = sessao.encerrada_em or now
                    sessao.motivo_encerramento = sessao.motivo_encerramento or motivo or "INVALIDATED"
                    sessao.save(update_fields=["ativa", "encerrada_em", "motivo_encerramento"])
                    SessionToken.objects.filter(session=sessao, revoked_at__isnull=True).update(revoked_at=now)
                    corrigidas += 1

            if options["dry_run"]:
                transaction.set_rollback(True)

        self.stdout.write(f"sessoes_analisadas={analisadas}")
        self.stdout.write(f"sessoes_validas={validas}")
        self.stdout.write(f"sessoes_invalidas={invalidas}")
        self.stdout.write(f"sessoes_corrigidas={corrigidas}")
        for sessao, motivo in linhas_invalidas:
            self.stdout.write(
                " - "
                f"id={sessao.pk}; empresa_id={sessao.empresa_id}; usuario_id={sessao.usuario_id}; "
                f"username={sessao.usuario.username}; device_id={sessao.dispositivo_id}; motivo={motivo}"
            )
