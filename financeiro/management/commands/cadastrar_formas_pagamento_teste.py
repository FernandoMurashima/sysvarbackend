from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from financeiro.models import FormaPagamento, PrazoPagamento, PrazoPagamentoParcela


class Command(BaseCommand):
    help = "Cadastra formas de pagamento basicas para compras e financeiro."

    @transaction.atomic
    def handle(self, *args, **options):
        formas = [
            ("AV", "A vista", [0]),
            ("PIX", "PIX", [0]),
            ("DEBITO", "Cartao de debito", [0]),
            ("CREDITO", "Cartao de credito", [30]),
            ("TROCA", "Vale-troca", [0]),
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
            prazo, _ = PrazoPagamento.objects.update_or_create(
                codigo=codigo,
                defaults={
                    "descricao": descricao,
                    "num_parcelas": len(dias_parcelas),
                    "intervalo_dias": dias_parcelas[0] if len(dias_parcelas) == 1 else 30,
                    "ativo": True,
                },
            )
            forma, created = FormaPagamento.objects.update_or_create(
                codigo=codigo,
                defaults={
                    "descricao": descricao,
                    "prazo_pagamento": prazo,
                    "ativo": True,
                },
            )
            criadas += 1 if created else 0
            atualizadas += 0 if created else 1

            percentual = Decimal("1") / Decimal(len(dias_parcelas))
            for ordem, dias in enumerate(dias_parcelas, start=1):
                PrazoPagamentoParcela.objects.update_or_create(
                    prazo=prazo,
                    ordem=ordem,
                    defaults={
                        "dias": dias,
                        "percentual": percentual,
                    },
                )
                parcelas_total += 1

            PrazoPagamentoParcela.objects.filter(
                prazo=prazo,
                ordem__gt=len(dias_parcelas),
            ).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Formas de pagamento: {criadas} criada(s), {atualizadas} atualizada(s). "
                f"Parcelas configuradas: {parcelas_total}."
            )
        )
