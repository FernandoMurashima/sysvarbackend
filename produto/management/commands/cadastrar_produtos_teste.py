from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from cadastros.models import Loja
from produto.models import (
    Colecao,
    ConfigEan,
    Cor,
    Estoque,
    EstoqueMovimentacao,
    Grade,
    Grupo,
    Material,
    Ncm,
    Produto,
    ProdutoDetalhe,
    Subgrupo,
    Tabelapreco,
    TabelaprecoProduto,
    Tamanho,
    Unidade,
)


class Command(BaseCommand):
    help = "Cadastra 5 produtos de cada grupo principal com SKUs, preços e estoque."

    @transaction.atomic
    def handle(self, *args, **options):
        base = self._base()
        produtos = self._produtos()
        criados = 0
        atualizados = 0
        skus_criados = 0
        estoques_criados = 0

        for item in produtos:
            grupo = base["grupos"][item["grupo"]]
            subgrupo = base["subgrupos"][(item["grupo"], item["subgrupo"])]
            produto, created = Produto.objects.update_or_create(
                descricao=item["descricao"],
                defaults={
                    "tipo_produto": "1",
                    "descricao_reduzida": item["reduzida"],
                    "unidade": base["unidade"],
                    "grupo": grupo,
                    "subgrupo": subgrupo,
                    "colecao": base["colecao"],
                    "material": base["material"],
                    "grade": base["grade"],
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
                    "bloqueado_venda": False,
                },
            )
            criados += 1 if created else 0
            atualizados += 0 if created else 1

            TabelaprecoProduto.objects.update_or_create(
                produto=produto,
                tabela=base["tabela"],
                defaults={
                    "preco": item["preco"],
                    "preco_promocional": None,
                    "DataInicio": timezone.localdate(),
                    "ativo": True,
                },
            )

            for cor in item["cores"]:
                for tamanho in base["tamanhos"]:
                    _, sku_created = ProdutoDetalhe.objects.get_or_create(
                        produto=produto,
                        idcor=base["cores"][cor],
                        idtamanho=tamanho,
                        defaults={"ativo": True, "bloqueado_venda": False},
                    )
                    sku = ProdutoDetalhe.objects.get(produto=produto, idcor=base["cores"][cor], idtamanho=tamanho)
                    if not sku.ativo or sku.bloqueado_venda:
                        sku.ativo = True
                        sku.bloqueado_venda = False
                        sku.save(update_fields=["ativo", "bloqueado_venda"])
                    skus_criados += 1 if sku_created else 0
                    for loja in base["lojas"]:
                        estoque, estoque_created = Estoque.objects.update_or_create(
                            CodigodeBarra=sku.ean13,
                            Idloja=loja,
                            defaults={
                                "referencia": produto.referencia or "",
                                "Estoque": 8,
                                "reserva": 0,
                            },
                        )
                        estoques_criados += 1 if estoque_created else 0
                        if estoque_created:
                            EstoqueMovimentacao.objects.create(
                                Idloja=loja,
                                CodigodeBarra=sku.ean13,
                                referencia=produto.referencia or "",
                                tipo=EstoqueMovimentacao.TIPO_ENTRADA,
                                quantidade=8,
                                saldo_anterior=0,
                                saldo_posterior=8,
                                documento="BASE-TESTE",
                                observacao="Carga inicial de produtos de teste",
                            )

        self.stdout.write(self.style.SUCCESS(
            f"Produtos: {criados} criados, {atualizados} atualizados."
        ))
        self.stdout.write(self.style.SUCCESS(
            f"SKUs novos: {skus_criados}. Estoques novos: {estoques_criados}."
        ))

    def _base(self):
        lojas = list(Loja.objects.filter(ativo=True).order_by("id"))
        if not lojas:
            raise RuntimeError("Cadastre as lojas antes dos produtos.")

        ncm, _ = Ncm.objects.update_or_create(
            ncm="6204.62.00",
            defaults={"descricao": "Vestuário feminino de algodão", "aliquota": Decimal("0.00")},
        )
        unidade, _ = Unidade.objects.update_or_create(
            Codigo="UN",
            defaults={"Descricao": "Unidade"},
        )
        material, _ = Material.objects.update_or_create(
            Codigo="ALG",
            defaults={"Descricao": "Algodão", "Status": "ATIVO"},
        )
        colecao, _ = Colecao.objects.update_or_create(
            Codigo="26",
            Estacao="01",
            defaults={"Descricao": "Verão 2026", "Status": "AT", "Contador": 0},
        )
        tabela, _ = Tabelapreco.objects.update_or_create(
            NomeTabela="Tabela Padrão",
            defaults={"DataInicio": timezone.localdate(), "Promocao": False},
        )
        grade, _ = Grade.objects.update_or_create(
            Descricao="Grade PP ao G",
            defaults={"Status": "ATIVO"},
        )
        tamanhos = []
        for ordem, nome in enumerate(["PP", "P", "M"], start=1):
            tamanho, _ = Tamanho.objects.update_or_create(
                idgrade=grade,
                Tamanho=nome,
                defaults={"Descricao": f"Tamanho {nome}", "Status": "ATIVO"},
            )
            tamanhos.append(tamanho)

        cores = {}
        for codigo, descricao, nome in [
            ("PT", "Preto", "Preto"),
            ("AZ", "Azul", "Azul"),
            ("BR", "Branco", "Branco"),
            ("VM", "Vermelho", "Vermelho"),
            ("VD", "Verde", "Verde"),
            ("RS", "Rosa", "Rosa"),
        ]:
            cor, _ = Cor.objects.update_or_create(
                Codigo=codigo,
                defaults={"Descricao": descricao, "Cor": nome, "Status": "ATIVO"},
            )
            cores[descricao] = cor

        ConfigEan.objects.update_or_create(
            country_prefix="789",
            company_prefix="1234",
            defaults={"ativo": True},
        )

        grupos = {}
        subgrupos = {}
        for codigo, cod_ref, descricao, subs in [
            ("CALCA", "01", "Calça", ["Lisa", "Estampada", "Jeans"]),
            ("BLUSA", "02", "Blusa", ["Estampada", "Lisa"]),
            ("VEST", "03", "Vestido", ["Lisos", "Estampados", "Renda"]),
        ]:
            grupo = Grupo.objects.filter(Descricao__iexact=descricao).order_by("Idgrupo").first()
            if grupo:
                grupo.Codigo = grupo.Codigo or codigo
                grupo.CodigoRef = grupo.CodigoRef or cod_ref
                grupo.Margem = grupo.Margem or Decimal("50.00")
                grupo.save(update_fields=["Codigo", "CodigoRef", "Margem"])
            else:
                grupo = Grupo.objects.create(
                    Codigo=codigo,
                    CodigoRef=cod_ref,
                    Descricao=descricao,
                    Margem=Decimal("50.00"),
                )
            grupos[descricao] = grupo
            for sub in subs:
                subgrupo, _ = Subgrupo.objects.update_or_create(
                    Idgrupo=grupo,
                    Descricao=sub,
                    defaults={"Margem": Decimal("50.00")},
                )
                subgrupos[(descricao, sub)] = subgrupo

        return {
            "lojas": lojas,
            "ncm": ncm,
            "unidade": unidade,
            "material": material,
            "colecao": colecao,
            "tabela": tabela,
            "grade": grade,
            "tamanhos": tamanhos,
            "cores": cores,
            "grupos": grupos,
            "subgrupos": subgrupos,
        }

    def _produtos(self):
        return [
            {"grupo": "Calça", "subgrupo": "Jeans", "descricao": "Calça Jeans Reta", "reduzida": "CALCA JEANS RETA", "preco": Decimal("159.90"), "cores": ["Azul", "Preto"]},
            {"grupo": "Calça", "subgrupo": "Lisa", "descricao": "Calça Alfaiataria Lisa", "reduzida": "CALCA ALFAIATARIA", "preco": Decimal("179.90"), "cores": ["Preto", "Branco"]},
            {"grupo": "Calça", "subgrupo": "Estampada", "descricao": "Calça Pantalona Estampada", "reduzida": "CALCA PANTALONA", "preco": Decimal("189.90"), "cores": ["Verde", "Rosa"]},
            {"grupo": "Calça", "subgrupo": "Jeans", "descricao": "Calça Jeans Skinny", "reduzida": "CALCA SKINNY", "preco": Decimal("149.90"), "cores": ["Azul", "Preto"]},
            {"grupo": "Calça", "subgrupo": "Lisa", "descricao": "Calça Jogger Lisa", "reduzida": "CALCA JOGGER", "preco": Decimal("129.90"), "cores": ["Preto", "Verde"]},
            {"grupo": "Blusa", "subgrupo": "Lisa", "descricao": "Blusa Básica Lisa", "reduzida": "BLUSA BASICA", "preco": Decimal("79.90"), "cores": ["Branco", "Preto"]},
            {"grupo": "Blusa", "subgrupo": "Estampada", "descricao": "Blusa Floral Estampada", "reduzida": "BLUSA FLORAL", "preco": Decimal("99.90"), "cores": ["Rosa", "Verde"]},
            {"grupo": "Blusa", "subgrupo": "Lisa", "descricao": "Blusa Manga Longa Lisa", "reduzida": "BLUSA ML LISA", "preco": Decimal("109.90"), "cores": ["Preto", "Azul"]},
            {"grupo": "Blusa", "subgrupo": "Estampada", "descricao": "Blusa Poá Estampada", "reduzida": "BLUSA POA", "preco": Decimal("89.90"), "cores": ["Branco", "Vermelho"]},
            {"grupo": "Blusa", "subgrupo": "Lisa", "descricao": "Blusa Regata Lisa", "reduzida": "BLUSA REGATA", "preco": Decimal("69.90"), "cores": ["Branco", "Rosa"]},
            {"grupo": "Vestido", "subgrupo": "Lisos", "descricao": "Vestido Midi Liso", "reduzida": "VESTIDO MIDI", "preco": Decimal("189.90"), "cores": ["Preto", "Verde"]},
            {"grupo": "Vestido", "subgrupo": "Estampados", "descricao": "Vestido Floral Estampado", "reduzida": "VESTIDO FLORAL", "preco": Decimal("219.90"), "cores": ["Rosa", "Azul"]},
            {"grupo": "Vestido", "subgrupo": "Renda", "descricao": "Vestido Renda Curto", "reduzida": "VESTIDO RENDA", "preco": Decimal("249.90"), "cores": ["Branco", "Preto"]},
            {"grupo": "Vestido", "subgrupo": "Lisos", "descricao": "Vestido Tubinho Liso", "reduzida": "VESTIDO TUBINHO", "preco": Decimal("199.90"), "cores": ["Preto", "Vermelho"]},
            {"grupo": "Vestido", "subgrupo": "Estampados", "descricao": "Vestido Longo Estampado", "reduzida": "VESTIDO LONGO", "preco": Decimal("229.90"), "cores": ["Verde", "Rosa"]},
        ]
