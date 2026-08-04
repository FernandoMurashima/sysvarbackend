from django.core.management.base import BaseCommand

from accounts.services.sessions import ConcurrentSessionService


class Command(BaseCommand):
    help = "Encerra sessões de acesso expiradas por inatividade."

    def handle(self, *args, **options):
        count = ConcurrentSessionService.close_expired()
        self.stdout.write(self.style.SUCCESS(f"sessoes_expiradas_encerradas={count}"))
