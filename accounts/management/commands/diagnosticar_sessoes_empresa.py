from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import SessaoUsuario
from accounts.services.sessions import ConcurrentSessionService, timeout_cutoff
from cadastros.models import Empresa


class Command(BaseCommand):
    help = "Diagnostica sessões de uma empresa sem expor tokens brutos."

    def add_arguments(self, parser):
        parser.add_argument("--empresa-id", type=int, required=True)

    def handle(self, *args, **options):
        try:
            empresa = Empresa.objects.select_related("contrato").get(pk=options["empresa_id"])
        except Empresa.DoesNotExist as exc:
            raise CommandError("Empresa não encontrada.") from exc

        cutoff = timeout_cutoff()
        contador_antigo = empresa.sessoes_usuarios.filter(ativa=True, ultima_atividade_em__gte=cutoff).count()
        contador_valido = ConcurrentSessionService.count_active_sessions(empresa)
        contrato = getattr(empresa, "contrato", None)
        limite = int(getattr(contrato, "limite_sessoes_simultaneas", 0) or 0)

        self.stdout.write(f"empresa_id={empresa.pk}")
        self.stdout.write(f"empresa_nome={empresa.nome}")
        self.stdout.write(f"empresa_fantasia={empresa.nome_fantasia or ''}")
        self.stdout.write(f"limite={limite}")
        self.stdout.write(f"contador_antigo={contador_antigo}")
        self.stdout.write(f"sessoes_validas={contador_valido}")
        self.stdout.write(f"disponiveis={max(limite - contador_valido, 0)}")
        self.stdout.write(f"cutoff_inatividade={cutoff.isoformat()}")
        self.stdout.write("sessoes:")

        qs = (
            SessaoUsuario.objects
            .filter(empresa=empresa)
            .select_related("usuario", "session_token")
            .order_by("id")
        )
        for sessao in qs:
            valido, motivo = ConcurrentSessionService.session_validity(sessao)
            try:
                token = sessao.session_token
                token_existente = True
                token_revogado = token.revoked_at is not None
                token_revogado_em = token.revoked_at.isoformat() if token.revoked_at else ""
            except Exception:
                token_existente = False
                token_revogado = False
                token_revogado_em = ""
            self.stdout.write(
                " - "
                f"id={sessao.pk}; "
                f"usuario_id={sessao.usuario_id}; "
                f"username={sessao.usuario.username}; "
                f"device_id={sessao.dispositivo_id}; "
                f"ativa={sessao.ativa}; "
                f"iniciada_em={sessao.iniciada_em.isoformat()}; "
                f"ultima_atividade_em={sessao.ultima_atividade_em.isoformat()}; "
                f"encerrada_em={sessao.encerrada_em.isoformat() if sessao.encerrada_em else ''}; "
                f"motivo={sessao.motivo_encerramento or ''}; "
                f"token_key_hash_prefix={sessao.token_key_hash[:12]}; "
                f"token_existente={token_existente}; "
                f"token_revogado={token_revogado}; "
                f"token_revogado_em={token_revogado_em}; "
                f"valida={valido}; "
                f"motivo_validade={motivo}"
            )
        self.stdout.write(f"diagnosticado_em={timezone.now().isoformat()}")
