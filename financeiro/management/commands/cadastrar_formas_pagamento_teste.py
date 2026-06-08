from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from financeiro.models import FormaPagamento, FormaPagamentoParcela


class Command(BaseCommand):
    help = "Cadastra formas de pagamento basicas para compras e financeiro."

    @transaction.atomic
    def handle(self, *args, **options):
        formas = [
            ("AV", "A vista", [0]),
            ("PIX", "PIX", [0]),
            ("DEBITO", "Cartao de debito", [0]),
            ("CREDITO", "Cartao de credito", [30]),
            ("BOLETO", "Boleto bancario", [30]),
            ("7", "7 dias", [7]),
            ("15", "15 dias", [15]),
            ("30", "30 dias", [30]),
            ("30/60", "30/60 dias", [30, 60]),
            ("30/60/90", "30/60/90 dias", [30, 60, 90]),
        ]

        criadas = 0
        atualizadas = 0
        parcelas_total = 0

        for codigo, descricao, dias_parcelas in formas:
            forma, created = FormaPagamento.objects.update_or_create(
                codigo=codigo,
                defaults={
                    "descricao": descricao,
                    "num_parcelas": len(dias_parcelas),
                    "ativo": True,
                },
            )
            criadas += 1 if created else 0
            atualizadas += 0 if created else 1

            percentual = Decimal("1") / Decimal(len(dias_parcelas))
            for ordem, dias in enumerate(dias_parcelas, start=1):
                FormaPagamentoParcela.objects.update_or_create(
                    forma=forma,
                    ordem=ordem,
                    defaults={
                        "dias": dias,
                        "percentual": percentual,
                        "valor_fixo": None,
                    },
                )
                parcelas_total += 1

            FormaPagamentoParcela.objects.filter(
                forma=forma,
                ordem__gt=len(dias_parcelas),
            ).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Formas de pagamento: {criadas} criada(s), {atualizadas} atualizada(s). "
                f"Parcelas configuradas: {parcelas_total}."
            )
        )
