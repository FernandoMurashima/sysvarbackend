from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from cadastros.models import Loja
from financeiro.models import Caixa


class Command(BaseCommand):
    help = "Cria ou corrige um caixa por loja ativa e o caixa master central."

    @transaction.atomic
    def handle(self, *args, **options):
        lojas = list(Loja.objects.filter(ativo=True).order_by("id"))
        if not lojas:
            self.stdout.write(self.style.WARNING("Nenhuma loja ativa encontrada."))
            return

        criados = 0
        atualizados = 0
        hoje = timezone.localdate()

        for index, loja in enumerate(lojas, start=1):
            codigo = f"CX{index:02d}"
            defaults = {
                "codigo": codigo,
                "descricao": f"{codigo} - Caixa Principal {loja.nome_loja}",
                "saldo_inicial": Decimal("0.00"),
                "ativo": True,
                "data_abertura": hoje,
            }

            caixa = (
                Caixa.objects
                .filter(idloja=loja, tipo_caixa=Caixa.TIPO_LOJA)
                .order_by("Idcaixa")
                .first()
            )
            if caixa:
                for field, value in defaults.items():
                    setattr(caixa, field, value)
                caixa.save(update_fields=[*defaults.keys()])
                atualizados += 1
            else:
                Caixa.objects.create(
                    idloja=loja,
                    tipo_caixa=Caixa.TIPO_LOJA,
                    saldo_atual=Decimal("0.00"),
                    **defaults,
                )
                criados += 1

        total_lojas = (
            Caixa.objects
            .filter(tipo_caixa=Caixa.TIPO_LOJA, ativo=True)
            .aggregate(total=Sum("saldo_atual"))
            .get("total")
            or Decimal("0.00")
        )
        master, master_created = Caixa.objects.update_or_create(
            idloja=None,
            tipo_caixa=Caixa.TIPO_MASTER,
            codigo="MASTER",
            defaults={
                "descricao": "MASTER - Caixa Central do Grupo",
                "saldo_inicial": Decimal("0.00"),
                "saldo_atual": total_lojas,
                "ativo": True,
                "data_abertura": hoje,
            },
        )

        self.stdout.write(self.style.SUCCESS(
            f"Caixas por loja: {criados} criados, {atualizados} atualizados."
        ))
        self.stdout.write(self.style.SUCCESS(
            f"Caixa master {'criado' if master_created else 'atualizado'}: {master.codigo}."
        ))
