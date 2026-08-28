from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from cadastros.models import Cliente, Empresa, Fornecedor, Funcionarios, Loja, Nat_Lancamento
from financeiro.models import (
    Caixa,
    CashbackConfig,
    ContaBancaria,
    FormaPagamento,
    FormaPagamentoParcela,
)
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
    Pack,
    PackItem,
    Produto,
    ProdutoDetalhe,
    Subgrupo,
    Tabelapreco,
    TabelaprecoProduto,
    Tamanho,
    Unidade,
)


class Command(BaseCommand):
    help = "Popula dados de teste para a empresa 2 sem mexer na empresa 1."

    @transaction.atomic
    def handle(self, *args, **options):
        empresa = Empresa.objects.filter(pk=2).first()
        if not empresa:
            self.stderr.write(self.style.ERROR("Empresa 2 nao encontrada."))
            return

        loja = self._loja(empresa)
        self._clientes(empresa)
        self._fornecedores(empresa)
        self._funcionarios(empresa, loja)
        self._financeiro(empresa, loja)
        base = self._base_produtos(empresa)
        produtos = self._produtos(empresa, base)
        self._estoque(loja, produtos)

        self.stdout.write(self.style.SUCCESS("Dados de teste da empresa 2 populados com sucesso."))

    def _loja(self, empresa):
        loja = Loja.objects.filter(empresa=empresa).order_by("id").first()
        if loja:
            return loja
        return Loja.objects.create(
            empresa=empresa,
            nome_loja="Loja 01 Empresa 2",
            apelido_loja="E2-01",
            cnpj="20000000000101",
            logradouro="Rua",
            endereco="Rua Empresa Dois",
            numero="100",
            cep="01001000",
            bairro="Centro",
            cidade="Sao Paulo",
            estado="SP",
            telefone1="11999990001",
            email="loja01@empresa2.test",
            EstoqueNegativo="NAO",
            Rede="NAO",
            Matriz="SIM",
            DataAbertura=date(2026, 1, 1),
            ContaContabil="1.1.02",
            ativo=True,
        )

    def _clientes(self, empresa):
        dados = [
            ("00000000000", "Consumidor Final", "CONSUMIDOR", "consumidor.e2@cliente.test"),
            ("20000000001", "Cliente E2 01", "CLI E2 01", "cli01.e2@cliente.test"),
            ("20000000002", "Cliente E2 02", "CLI E2 02", "cli02.e2@cliente.test"),
            ("20000000003", "Cliente E2 03", "CLI E2 03", "cli03.e2@cliente.test"),
            ("20000000004", "Cliente E2 04", "CLI E2 04", "cli04.e2@cliente.test"),
            ("20000000005", "Cliente E2 05", "CLI E2 05", "cli05.e2@cliente.test"),
        ]
        for cpf, nome, apelido, email in dados:
            Cliente.objects.update_or_create(
                empresa=empresa,
                cpf=cpf,
                defaults={
                    "nome_cliente": nome,
                    "apelido": apelido,
                    "categoria": "Varejo",
                    "cidade": "Sao Paulo",
                    "estado": "SP",
                    "telefone1": "11999990000",
                    "email": email,
                    "ativo": True,
                },
            )

    def _fornecedores(self, empresa):
        dados = [
            ("20000000000111", "Fornecedor E2 01", "FOR E2 01", "Revenda"),
            ("20000000000122", "Fornecedor E2 02", "FOR E2 02", "Revenda"),
            ("20000000000133", "Fornecedor E2 03", "FOR E2 03", "Uso/Consumo"),
        ]
        for cnpj, nome, apelido, categoria in dados:
            Fornecedor.objects.update_or_create(
                cnpj=cnpj,
                defaults={
                    "empresa": empresa,
                    "nome_fornecedor": nome,
                    "apelido": apelido,
                    "categoria": categoria,
                    "cidade": "Sao Paulo",
                    "estado": "SP",
                    "telefone1": "1133330000",
                    "email": f"{apelido.lower().replace(' ', '')}@fornecedor.test",
                    "ativo": True,
                },
            )

    def _funcionarios(self, empresa, loja):
        dados = [
            ("20000000101", "Gerente E2 01", "GER E2", "Gerente", Decimal("0.00")),
            ("20000000102", "Vendedor E2 01", "VEN E2 01", "Vendedor", Decimal("5.00")),
            ("20000000103", "Vendedor E2 02", "VEN E2 02", "Vendedor", Decimal("5.00")),
            ("20000000104", "Caixa E2 01", "CX E2", "Caixa", Decimal("0.00")),
        ]
        for cpf, nome, apelido, categoria, comissao in dados:
            Funcionarios.objects.update_or_create(
                empresa=empresa,
                cpf=cpf,
                defaults={
                    "nomefuncionario": nome,
                    "apelido": apelido,
                    "categoria": categoria,
                    "inicio": date(2026, 1, 1),
                    "meta": Decimal("10000.00"),
                    "comissao_percentual": comissao,
                    "idloja": loja,
                    "ativo": True,
                },
            )

    def _financeiro(self, empresa, loja):
        Caixa.objects.update_or_create(
            empresa=empresa,
            idloja=loja,
            codigo="E2CX01",
            defaults={
                "tipo_caixa": Caixa.TIPO_LOJA,
                "descricao": "Caixa Loja Empresa 2",
                "saldo_inicial": Decimal("500.00"),
                "saldo_atual": Decimal("500.00"),
                "ativo": True,
            },
        )
        Caixa.objects.update_or_create(
            empresa=empresa,
            idloja=None,
            codigo="E2MASTER",
            defaults={
                "tipo_caixa": Caixa.TIPO_MASTER,
                "descricao": "Caixa Master Empresa 2",
                "saldo_inicial": Decimal("0.00"),
                "saldo_atual": Decimal("0.00"),
                "ativo": True,
            },
        )
        ContaBancaria.objects.update_or_create(
            empresa=empresa,
            idloja=loja,
            banco="Banco Teste E2",
            agencia="0002",
            conta="20001-0",
            defaults={
                "descricao": "Conta Principal Empresa 2",
                "tipo_conta": "CORRENTE",
                "pix_chave": "pix@empresa2.test",
                "saldo_inicial": Decimal("1000.00"),
                "saldo_atual": Decimal("1000.00"),
                "ativo": True,
            },
        )
        formas = [
            ("E2AV", "A vista Empresa 2", [0]),
            ("E230", "30 dias Empresa 2", [30]),
            ("E23060", "30/60 dias Empresa 2", [30, 60]),
        ]
        for codigo, descricao, dias in formas:
            forma, _ = FormaPagamento.objects.update_or_create(
                codigo=codigo,
                defaults={
                    "empresa": empresa,
                    "descricao": descricao,
                    "num_parcelas": len(dias),
                    "ativo": True,
                },
            )
            FormaPagamentoParcela.objects.filter(forma=forma).delete()
            percentual = Decimal("100.000000") / Decimal(len(dias))
            for idx, prazo in enumerate(dias, start=1):
                FormaPagamentoParcela.objects.create(
                    forma=forma,
                    ordem=idx,
                    dias=prazo,
                    percentual=percentual,
                )
        CashbackConfig.objects.update_or_create(
            empresa=empresa,
            nome="Cashback padrao Empresa 2",
            defaults={
                "ativo": True,
                "percentual": Decimal("3.0000"),
                "validade_dias": 180,
                "valor_minimo_geracao": Decimal("0.00"),
                "valor_minimo_uso": Decimal("0.00"),
                "limite_uso_percentual": Decimal("100.0000"),
                "consumidor_final_participa": False,
            },
        )

    def _base_produtos(self, empresa):
        ncm, _ = Ncm.objects.update_or_create(
            empresa=empresa,
            ncm="6204.62.00",
            defaults={"descricao": "Vestuário feminino de teste", "aliquota": Decimal("18.00")},
        )
        unidade, _ = Unidade.objects.update_or_create(empresa=empresa, Descricao="Unidade", defaults={"Codigo": "UN"})
        material, _ = Material.objects.update_or_create(
            empresa=empresa,
            Descricao="Algodao",
            defaults={"Codigo": "ALG", "Status": "ATIVO"},
        )
        grade, _ = Grade.objects.update_or_create(empresa=empresa, Descricao="P P M G", defaults={"Status": "ATIVO"})
        tamanhos = []
        for ordem, tamanho in enumerate(["P", "M", "G"], start=1):
            item, _ = Tamanho.objects.update_or_create(
                empresa=empresa,
                idgrade=grade,
                Tamanho=tamanho,
                defaults={"Descricao": tamanho, "Status": "ATIVO"},
            )
            tamanhos.append(item)
        cores = []
        for codigo, nome in [("PR", "Preto"), ("AZ", "Azul")]:
            cor, _ = Cor.objects.update_or_create(
                empresa=empresa,
                Codigo=codigo,
                defaults={"Descricao": nome, "Cor": nome, "Status": "ATIVO"},
            )
            cores.append(cor)
        colecao, _ = Colecao.objects.update_or_create(
            Codigo="27",
            Estacao="01",
            defaults={
                "empresa": empresa,
                "Descricao": "Verao 2027 Empresa 2",
                "Status": "AT",
            },
        )
        grupos = {}
        for codigo, cod_ref, descricao, subgrupos in [
            ("E2-CALCA", "01", "Calca E2", ["Lisa", "Jeans"]),
            ("E2-BLUSA", "02", "Blusa E2", ["Lisa", "Estampada"]),
            ("E2-VEST", "03", "Vestido E2", ["Liso", "Estampado"]),
        ]:
            grupo, _ = Grupo.objects.update_or_create(
                empresa=empresa,
                Codigo=codigo,
                defaults={"CodigoRef": cod_ref, "Descricao": descricao, "Margem": Decimal("50.00")},
            )
            grupos[descricao] = {"grupo": grupo, "subgrupos": []}
            for nome_sub in subgrupos:
                sub, _ = Subgrupo.objects.update_or_create(
                    empresa=empresa,
                    Idgrupo=grupo,
                    Descricao=nome_sub,
                    defaults={"Margem": Decimal("50.00")},
                )
                grupos[descricao]["subgrupos"].append(sub)
        tabela, _ = Tabelapreco.objects.update_or_create(
            empresa=empresa,
            NomeTabela="Tabela Padrao Empresa 2",
            defaults={"DataInicio": timezone.localdate(), "Promocao": False},
        )
        ConfigEan.objects.get_or_create(
            empresa=empresa,
            country_prefix="789",
            company_prefix="2002",
            defaults={"next_itemref": 1, "ativo": True},
        )
        return {
            "ncm": ncm.ncm,
            "unidade": unidade,
            "material": material,
            "grade": grade,
            "tamanhos": tamanhos,
            "cores": cores,
            "colecao": colecao,
            "grupos": grupos,
            "tabela": tabela,
        }

    def _produtos(self, empresa, base):
        dados = [
            ("Calca E2", 0, "Calca Jeans Empresa 2", Decimal("159.90")),
            ("Calca E2", 1, "Calca Lisa Empresa 2", Decimal("139.90")),
            ("Blusa E2", 0, "Blusa Basica Empresa 2", Decimal("79.90")),
            ("Blusa E2", 1, "Blusa Estampada Empresa 2", Decimal("99.90")),
            ("Vestido E2", 0, "Vestido Liso Empresa 2", Decimal("189.90")),
            ("Vestido E2", 1, "Vestido Estampado Empresa 2", Decimal("229.90")),
        ]
        produtos = []
        for grupo_nome, sub_idx, descricao, preco in dados:
            grupo = base["grupos"][grupo_nome]["grupo"]
            subgrupo = base["grupos"][grupo_nome]["subgrupos"][sub_idx]
            produto, _ = Produto.objects.get_or_create(
                empresa=empresa,
                descricao=descricao,
                defaults={
                    "tipo_produto": "1",
                    "descricao_reduzida": descricao[:60],
                    "unidade": base["unidade"],
                    "grupo": grupo,
                    "subgrupo": subgrupo,
                    "colecao": base["colecao"],
                    "material": base["material"],
                    "grade": base["grade"],
                    "ncm": base["ncm"],
                    "origem_mercadoria": 0,
                    "csosn_ou_cst_icms": "102",
                    "aliquota_icms": Decimal("18.00"),
                    "cfop_venda_dentro": "5102",
                    "cfop_venda_fora": "6102",
                    "ativo": True,
                },
            )
            produtos.append(produto)
            TabelaprecoProduto.objects.update_or_create(
                produto=produto,
                tabela=base["tabela"],
                defaults={"preco": preco, "DataInicio": timezone.localdate(), "ativo": True},
            )
            for cor in base["cores"]:
                for tamanho in base["tamanhos"]:
                    ProdutoDetalhe.objects.get_or_create(
                        produto=produto,
                        idcor=cor,
                        idtamanho=tamanho,
                    )
        uso = [
            ("Sacola Empresa 2", "UC-E2-001"),
            ("Etiqueta Empresa 2", "UC-E2-002"),
            ("Cabide Empresa 2", "UC-E2-003"),
        ]
        for descricao, referencia in uso:
            Produto.objects.update_or_create(
                empresa=empresa,
                descricao=descricao,
                defaults={
                    "tipo_produto": "2",
                    "referencia": referencia,
                    "descricao_reduzida": descricao,
                    "unidade": base["unidade"],
                    "grupo": None,
                    "subgrupo": None,
                    "colecao": None,
                    "material": base["material"],
                    "grade": None,
                    "ncm": base["ncm"],
                    "ativo": True,
                },
            )
        pack, _ = Pack.objects.update_or_create(
            empresa=empresa,
            grade=base["grade"],
            nome="Pack P/M/G Empresa 2",
            defaults={"ativo": True},
        )
        for tamanho in base["tamanhos"]:
            PackItem.objects.update_or_create(pack=pack, tamanho=tamanho, defaults={"qtd": 1})
        return produtos

    def _estoque(self, loja, produtos):
        for produto in produtos:
            for sku in produto.skus.all():
                estoque, _ = Estoque.objects.get_or_create(
                    CodigodeBarra=sku.ean13,
                    Idloja=loja,
                    defaults={"referencia": produto.referencia or "", "Estoque": 8, "reserva": 0},
                )
                if estoque.Estoque in (None, 0):
                    estoque.Estoque = 8
                    estoque.referencia = produto.referencia or estoque.referencia
                    estoque.reserva = estoque.reserva or 0
                    estoque.save(update_fields=["Estoque", "referencia", "reserva"])
                if not EstoqueMovimentacao.objects.filter(
                    Idloja=loja,
                    CodigodeBarra=sku.ean13,
                    documento="SEED-E2",
                ).exists():
                    EstoqueMovimentacao.objects.create(
                        Idloja=loja,
                        CodigodeBarra=sku.ean13,
                        referencia=produto.referencia or "",
                        tipo=EstoqueMovimentacao.TIPO_ENTRADA,
                        quantidade=8,
                        saldo_anterior=0,
                        saldo_posterior=8,
                        origem=EstoqueMovimentacao.ORIGEM_AJUSTE_MANUAL,
                        documento="SEED-E2",
                        observacao="Carga inicial de teste empresa 2",
                    )
