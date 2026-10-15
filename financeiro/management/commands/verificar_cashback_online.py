from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from cadastros.models import Cliente
from financeiro.cashback_online import exigir_controle_creditos, _validar_reservas_historicas
from financeiro.cashback_creditos import _validar_historico
from financeiro.services import CashbackErro


class Command(BaseCommand):
    help = "Verifica esquema e pendências de Cashback; não ativa fluxo nem modifica dados."

    def handle(self, *args, **options):
        try:
            exigir_controle_creditos(exigir_ativacao=False)
        except CashbackErro as exc:
            raise CommandError(str(exc)) from exc
        pendentes = 0
        for cliente in Cliente.objects.filter(cashback_movimentos__isnull=False).distinct().iterator():
            try:
                _validar_historico(cliente.empresa, cliente)
                _validar_reservas_historicas(cliente)
            except CashbackErro as exc:
                pendentes += 1
                self.stdout.write(f"Cliente {cliente.pk}: {exc}")
        self.stdout.write(f"Esquema disponível. Clientes com pendências: {pendentes}.")
        self.stdout.write(f"CASHBACK_CENTRAL_FEFO_ATIVO={getattr(settings, 'CASHBACK_CENTRAL_FEFO_ATIVO', False)}")
