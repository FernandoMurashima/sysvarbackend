from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from produto.models import (
    Colecao,
    Grade,
    Grupo,
    Material,
    Ncm,
    Pack,
    PackItem,
    Produto,
    Subgrupo,
    Tamanho,
    Unidade,
)


class Command(BaseCommand):
    help = "Cadastra produtos de uso/consumo e um pack padrao para testes."

    @transaction.atomic
    def handle(self, *args, **options):
        base = self._base()
        produtos = self._produtos()
        criados = 0
        atualizados = 0

        for item in produtos:
            produto, created = Produto.objects.update_or_create(
                referencia=item["referencia"],
                defaults={
                    "tipo_produto": "2",
                    "descricao": item["descricao"],
                    "descricao_reduzida": item["reduzida"],
                    "unidade": base["unidade"],
                    "grupo": base["grupo"],
                    "subgrupo": base["subgrupos"][item["subgrupo"]],
                    "colecao": base["colecao"],
                    "material": base["material"],
                    "grade": None,
                    "ncm": base["ncm"].ncm,
                    "origem_mercadoria": 0,
                    "csosn_ou_cst_icms": "102",
                    "aliquota_icms": Decimal("0.00"),
                    "cfop_venda_dentro": "5102",
                    "cfop_venda_fora": "6102",
                    "cst_pis": "49",
                    "aliq_pis": Decimal("0.00"),
                    "cst_cofins": "49",
                    "aliq_cofins": Decimal("0.00"),
                    "ativo": True,
                    "bloqueado_venda": True,
                    "observacoes": "Produto de uso e consumo para base de testes.",
                },
            )
            criados += 1 if created else 0
            atualizados += 0 if created else 1

        pack = self._pack()

        self.stdout.write(
            self.style.SUCCESS(
                f"Uso/consumo: {criados} criado(s), {atualizados} atualizado(s)."
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Pack: {pack.nome} com {pack.itens.count()} tamanho(s)."
            )
        )

    def _base(self):
        ncm = Ncm.objects.filter(ncm__isnull=False).exclude(ncm="").order_by("id").first()
        if not ncm:
            ncm, _ = Ncm.objects.update_or_create(
                ncm="6204.62.00",
                defaults={
                    "descricao": "NCM padrao para base de testes",
                    "aliquota": Decimal("0.00"),
                },
            )

        unidade, _ = Unidade.objects.update_or_create(
            Codigo="UN",
            defaults={"Descricao": "Unidade"},
        )
        material, _ = Material.objects.update_or_create(
            Codigo="USO",
            defaults={"Descricao": "Uso e consumo", "Status": "ATIVO"},
        )
        colecao, _ = Colecao.objects.update_or_create(
            Codigo="26",
            Estacao="01",
            defaults={"Descricao": "Verao 2026", "Status": "AT", "Contador": 0},
        )
        grupo, _ = Grupo.objects.update_or_create(
            Codigo="USOCONS",
            defaults={
                "CodigoRef": "90",
                "Descricao": "Uso e Consumo",
                "Margem": Decimal("0.00"),
            },
        )

        subgrupos = {}
        for descricao in ["Embalagens", "Etiquetas", "Acessorios"]:
            subgrupo, _ = Subgrupo.objects.update_or_create(
                Idgrupo=grupo,
                Descricao=descricao,
                defaults={"Margem": Decimal("0.00")},
            )
            subgrupos[descricao] = subgrupo

        return {
            "ncm": ncm,
            "unidade": unidade,
            "material": material,
            "colecao": colecao,
            "grupo": grupo,
            "subgrupos": subgrupos,
        }

    def _produtos(self):
        return [
            {
                "referencia": "UC-001",
                "descricao": "Sacola de Papel Personalizada",
                "reduzida": "SACOLA PAPEL",
                "subgrupo": "Embalagens",
            },
            {
                "referencia": "UC-002",
                "descricao": "Etiqueta Adesiva de Preco",
                "reduzida": "ETIQUETA PRECO",
                "subgrupo": "Etiquetas",
            },
            {
                "referencia": "UC-003",
                "descricao": "Cabide Plastico Adulto",
                "reduzida": "CABIDE ADULTO",
                "subgrupo": "Acessorios",
            },
        ]

    def _pack(self):
        grade, _ = Grade.objects.update_or_create(
            Descricao="Grade PP ao G",
            defaults={"Status": "ATIVO"},
        )
        pack, _ = Pack.objects.update_or_create(
            grade=grade,
            nome="Pack Padrao PP/P/M/G",
            defaults={"ativo": True},
        )

        for nome, quantidade in [("PP", 1), ("P", 2), ("M", 2), ("G", 1)]:
            tamanho, _ = Tamanho.objects.update_or_create(
                idgrade=grade,
                Tamanho=nome,
                defaults={"Descricao": f"Tamanho {nome}", "Status": "ATIVO"},
            )
            PackItem.objects.update_or_create(
                pack=pack,
                tamanho=tamanho,
                defaults={"qtd": quantidade},
            )

        return pack
