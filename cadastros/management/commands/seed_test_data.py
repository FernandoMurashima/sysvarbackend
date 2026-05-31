from decimal import Decimal
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from cadastros.models import Cliente, Fornecedor, Funcionarios, Loja, Nat_Lancamento
from financeiro.models import (
    Caixa,
    ContaBancaria,
    FormaPagamento,
    FormaPagamentoParcela,
    MovimentacaoFinanceira,
    Pagar,
    PagarItem,
    Receber,
    ReceberItem,
)
from produto.models import (
    Colecao,
    ConfigEan,
    Cor,
    Estoque,
    EstoqueMovimentacao,
    Grade,
    InventarioEstoque,
    InventarioEstoqueItem,
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
    help = "Popula a base com cadastros basicos para testes locais."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default="admin123",
            help="Senha do usuario admin de teste. Padrao: admin123",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        today = timezone.localdate()

        lojas = self._seed_lojas()
        self._seed_clientes()
        fornecedores = self._seed_fornecedores()
        self._seed_funcionarios(lojas)
        self._seed_naturezas()
        self._seed_formas_pagamento()
        self._seed_caixa_bancos_movimentacoes(lojas, today)
        self._seed_titulos_financeiros(lojas, fornecedores, today)
        base = self._seed_produto_base(today)
        produtos = self._seed_produtos(base, today)
        self._seed_estoque(lojas, produtos)
        self._seed_estoque_movimentos_inventario(lojas)
        self._seed_usuario(lojas[0], options["password"])

        self.stdout.write(self.style.SUCCESS("Base de teste populada com sucesso."))
        self.stdout.write("Usuario: admin | Senha: {}".format(options["password"]))

    def _seed_lojas(self):
        dados = [
            {
                "cnpj": "11222333000181",
                "nome_loja": "Sysvar Moda Centro",
                "apelido_loja": "CENTRO",
                "cidade": "Sao Paulo",
                "estado": "SP",
                "telefone1": "11988887777",
                "email": "centro@sysvar.test",
                "Matriz": "SIM",
                "Rede": "SIM",
            },
            {
                "cnpj": "12345678000195",
                "nome_loja": "Sysvar Moda Shopping",
                "apelido_loja": "SHOPPING",
                "cidade": "Sao Paulo",
                "estado": "SP",
                "telefone1": "11977776666",
                "email": "shopping@sysvar.test",
                "Matriz": "NAO",
                "Rede": "SIM",
            },
        ]
        lojas = []
        for item in dados:
            loja, _ = Loja.objects.update_or_create(
                cnpj=item["cnpj"],
                defaults={
                    **item,
                    "logradouro": "Rua",
                    "endereco": "Avenida Paulista",
                    "numero": "1000",
                    "bairro": "Bela Vista",
                    "cep": "01310100",
                    "EstoqueNegativo": "NAO",
                    "DataAbertura": date(2024, 1, 10),
                    "ContaContabil": "1.1.01",
                    "ativo": True,
                },
            )
            lojas.append(loja)
        return lojas

    def _seed_clientes(self):
        dados = [
            ("00000000000", "Consumidor Final", "CONSUMIDOR", "Sem cadastro"),
            ("52998224725", "Mariana Alves", "MARI", "Varejo"),
            ("11144477735", "Rafael Costa", "RAFA", "Varejo"),
            ("93541134780", "Bianca Lima", "BIANCA", "VIP"),
        ]
        for cpf, nome, apelido, categoria in dados:
            Cliente.objects.update_or_create(
                cpf=cpf,
                defaults={
                    "nome_cliente": nome,
                    "apelido": apelido,
                    "categoria": categoria,
                    "cidade": "Sao Paulo",
                    "estado": "SP",
                    "telefone1": "11999990000",
                    "email": f"{apelido.lower()}@cliente.test",
                    "mala_direta": categoria == "VIP",
                    "ativo": True,
                },
            )

    def _seed_fornecedores(self):
        dados = [
            ("19131243000197", "Textil Aurora Ltda", "AURORA", "Revenda"),
            ("04252011000110", "Malharia Horizonte Ltda", "HORIZONTE", "Revenda"),
            ("40432123000188", "Insumos Office Ltda", "OFFICE", "Uso/Consumo"),
        ]
        fornecedores = []
        for cnpj, nome, apelido, categoria in dados:
            fornecedor, _ = Fornecedor.objects.update_or_create(
                cnpj=cnpj,
                defaults={
                    "nome_fornecedor": nome,
                    "apelido": apelido,
                    "categoria": categoria,
                    "cidade": "Sao Paulo",
                    "estado": "SP",
                    "telefone1": "1133334444",
                    "email": f"{apelido.lower()}@fornecedor.test",
                    "ativo": True,
                },
            )
            fornecedores.append(fornecedor)
        return fornecedores

    def _seed_funcionarios(self, lojas):
        dados = [
            ("39053344705", "Ana Souza", "ANA", "Gerente", lojas[0], Decimal("25000.00"), Decimal("0.00")),
            ("98765432100", "Carlos Pereira", "CARLOS", "Vendedor", lojas[0], Decimal("18000.00"), Decimal("5.00")),
            ("12345678909", "Julia Martins", "JULIA", "Vendedor", lojas[1], Decimal("18000.00"), Decimal("5.00")),
        ]
        for cpf, nome, apelido, categoria, loja, meta, comissao in dados:
            Funcionarios.objects.update_or_create(
                cpf=cpf,
                defaults={
                    "nomefuncionario": nome,
                    "apelido": apelido,
                    "categoria": categoria,
                    "inicio": date(2025, 1, 2),
                    "meta": meta,
                    "comissao_percentual": comissao,
                    "idloja": loja,
                    "ativo": True,
                },
            )

    def _seed_naturezas(self):
        dados = [
            ("1.01", "Vendas", "Mercadorias", "Receita de venda de mercadorias", "RECEITA", "ATIVO", "CREDITO"),
            ("2.01", "Compras", "Mercadorias", "Compra de mercadorias para revenda", "DESPESA", "ATIVO", "DEBITO"),
            ("2.02", "Administrativo", "Uso e consumo", "Compra de materiais de uso e consumo", "DESPESA", "ATIVO", "DEBITO"),
        ]
        for codigo, categoria, subcategoria, descricao, tipo, status, tipo_natureza in dados:
            Nat_Lancamento.objects.update_or_create(
                codigo=codigo,
                defaults={
                    "categoria_principal": categoria,
                    "subcategoria": subcategoria,
                    "descricao": descricao,
                    "tipo": tipo,
                    "status": status,
                    "tipo_natureza": tipo_natureza,
                },
            )

    def _seed_formas_pagamento(self):
        formas = [
            ("AV", "A vista", [0]),
            ("30", "30 dias", [30]),
            ("30/60", "30/60 dias", [30, 60]),
            ("CC", "Cartao de credito", [30]),
        ]
        for codigo, descricao, parcelas in formas:
            forma, _ = FormaPagamento.objects.update_or_create(
                codigo=codigo,
                defaults={
                    "descricao": descricao,
                    "num_parcelas": len(parcelas),
                    "ativo": True,
                },
            )
            percentual = Decimal("1") / Decimal(len(parcelas))
            for ordem, dias in enumerate(parcelas, start=1):
                FormaPagamentoParcela.objects.update_or_create(
                    forma=forma,
                    ordem=ordem,
                    defaults={"dias": dias, "percentual": percentual},
                )

    def _seed_titulos_financeiros(self, lojas, fornecedores, today):
        cliente = Cliente.objects.filter(cpf="52998224725").first()
        natureza_compra = Nat_Lancamento.objects.filter(codigo="2.01").first()
        natureza_venda = Nat_Lancamento.objects.filter(codigo="1.01").first()
        if not lojas or not fornecedores or not cliente or not natureza_compra or not natureza_venda:
            return

        pagar, _ = Pagar.objects.update_or_create(
            Titulo="Compra Textil Aurora",
            Documento="NF-1001",
            defaults={
                "idloja": lojas[0],
                "idfornecedor": fornecedores[0],
                "Data_emissao": today,
                "Valor_total": Decimal("850.00"),
                "Previsao": False,
                "FormaPagamento": "30/60",
                "Idnatureza": natureza_compra,
                "conta_contabil": "2.1.01",
            },
        )
        for parcela_n, dias, valor in [(1, 30, Decimal("425.00")), (2, 60, Decimal("425.00"))]:
            PagarItem.objects.update_or_create(
                Idpagar=pagar,
                parcela_n=parcela_n,
                defaults={
                    "status": PagarItem.STATUS_EFETIVO,
                    "Data_vencimento": today + timedelta(days=dias),
                    "valor_parcela": valor,
                    "FormaPagamento": "30/60",
                    "Previsao": False,
                    "Idnatureza": natureza_compra,
                },
            )

        receber, _ = Receber.objects.update_or_create(
            Titulo="Venda Balcao Mariana",
            Documento="PV-2001",
            defaults={
                "idloja": lojas[0],
                "idcliente": cliente,
                "Data_emissao": today,
                "Valor_total": Decimal("379.80"),
                "Previsao": False,
                "FormaPagamento": "CC",
                "Idnatureza": natureza_venda,
                "conta_contabil": "1.1.02",
            },
        )
        ReceberItem.objects.update_or_create(
            Idreceber=receber,
            parcela_n=1,
            defaults={
                "status": ReceberItem.STATUS_EFETIVO,
                "Data_vencimento": today + timedelta(days=30),
                "valor_parcela": Decimal("379.80"),
                "FormaPagamento": "CC",
                "Previsao": False,
                "Idnatureza": natureza_venda,
            },
        )

    def _seed_caixa_bancos_movimentacoes(self, lojas, today):
        natureza_venda = Nat_Lancamento.objects.filter(codigo="1.01").first()
        natureza_compra = Nat_Lancamento.objects.filter(codigo="2.01").first()
        if not lojas:
            return

        caixas_por_loja = {}
        for index, loja in enumerate(lojas, start=1):
            saldo_inicial = Decimal("500.00") if loja == lojas[0] else Decimal("0.00")
            saldo_atual = Decimal("879.80") if loja == lojas[0] else Decimal("0.00")
            caixa = Caixa.objects.filter(idloja=loja, tipo_caixa=Caixa.TIPO_LOJA).order_by("Idcaixa").first()
            if caixa:
                caixa.codigo = f"CX{index:02d}"
                caixa.descricao = f"Caixa Principal {loja.nome_loja}"
                caixa.ativo = True
                caixa.save(update_fields=["codigo", "descricao", "ativo"])
            else:
                caixa = Caixa.objects.create(
                    idloja=loja,
                    tipo_caixa=Caixa.TIPO_LOJA,
                    codigo=f"CX{index:02d}",
                    descricao=f"Caixa Principal {loja.nome_loja}",
                    saldo_inicial=saldo_inicial,
                    saldo_atual=saldo_atual,
                    ativo=True,
                    data_abertura=today,
                )
            caixas_por_loja[loja.id] = caixa

        caixa = caixas_por_loja[lojas[0].id]
        Caixa.objects.update_or_create(
            idloja=None,
            codigo="MASTER",
            defaults={
                "tipo_caixa": Caixa.TIPO_MASTER,
                "descricao": "Caixa Master do Grupo",
                "saldo_inicial": Decimal("0.00"),
                "saldo_atual": Decimal("879.80"),
                "ativo": True,
                "data_abertura": today,
            },
        )

        conta, _ = ContaBancaria.objects.update_or_create(
            idloja=lojas[0],
            banco="Banco Teste",
            agencia="0001",
            conta="12345-6",
            defaults={
                "descricao": "Conta Corrente Principal",
                "tipo_conta": "CORRENTE",
                "pix_chave": "financeiro@sysvar.test",
                "saldo_inicial": Decimal("2500.00"),
                "saldo_atual": Decimal("2075.00"),
                "ativo": True,
            },
        )

        if natureza_venda:
            MovimentacaoFinanceira.objects.update_or_create(
                idloja=lojas[0],
                documento="PV-2001",
                historico="Recebimento venda balcão Mariana",
                defaults={
                    "data_movimento": today,
                    "tipo": MovimentacaoFinanceira.TIPO_ENTRADA,
                    "status": MovimentacaoFinanceira.STATUS_EFETIVA,
                    "origem": MovimentacaoFinanceira.ORIGEM_MANUAL,
                    "valor": Decimal("379.80"),
                    "Idnatureza": natureza_venda,
                    "FormaPagamento": "CC",
                    "caixa": caixa,
                    "conta_bancaria": None,
                },
            )

        if natureza_compra:
            MovimentacaoFinanceira.objects.update_or_create(
                idloja=lojas[0],
                documento="NF-1001",
                historico="Pagamento parcial fornecedor Aurora",
                defaults={
                    "data_movimento": today,
                    "tipo": MovimentacaoFinanceira.TIPO_SAIDA,
                    "status": MovimentacaoFinanceira.STATUS_EFETIVA,
                    "origem": MovimentacaoFinanceira.ORIGEM_MANUAL,
                    "valor": Decimal("425.00"),
                    "Idnatureza": natureza_compra,
                    "FormaPagamento": "30/60",
                    "caixa": None,
                    "conta_bancaria": conta,
                },
            )

    def _seed_produto_base(self, today):
        config_ean, created = ConfigEan.objects.get_or_create(
            country_prefix="789",
            company_prefix="1234",
            defaults={"next_itemref": 1, "ativo": True},
        )
        if not created and not config_ean.ativo:
            config_ean.ativo = True
            config_ean.save(update_fields=["ativo"])

        ncm_vestuario, _ = Ncm.objects.update_or_create(
            ncm="6204.42.00",
            defaults={"descricao": "Vestidos femininos de algodao", "aliquota": Decimal("18.00")},
        )
        ncm_calca, _ = Ncm.objects.update_or_create(
            ncm="6203.42.00",
            defaults={"descricao": "Calcas de algodao", "aliquota": Decimal("18.00")},
        )

        unidade, _ = Unidade.objects.update_or_create(
            Codigo="UN",
            defaults={"Descricao": "Unidade"},
        )
        grade, _ = Grade.objects.update_or_create(
            Descricao="Grade PP ao GG",
            defaults={"Status": "ATIVO"},
        )
        tamanhos = []
        for tamanho in ["PP", "P", "M", "G", "GG"]:
            obj, _ = Tamanho.objects.update_or_create(
                idgrade=grade,
                Tamanho=tamanho,
                defaults={"Descricao": f"Tamanho {tamanho}", "Status": "ATIVO"},
            )
            tamanhos.append(obj)

        cores = []
        for codigo, descricao, cor in [
            ("PR", "Preto", "Preto"),
            ("AZ", "Azul", "Azul"),
            ("BR", "Branco", "Branco"),
        ]:
            obj, _ = Cor.objects.update_or_create(
                Codigo=codigo,
                defaults={"Descricao": descricao, "Cor": cor, "Status": "ATIVO"},
            )
            cores.append(obj)

        algodao, _ = Material.objects.update_or_create(
            Codigo="ALG",
            defaults={"Descricao": "Algodao", "Status": "ATIVO"},
        )
        jeans, _ = Material.objects.update_or_create(
            Codigo="JNS",
            defaults={"Descricao": "Jeans", "Status": "ATIVO"},
        )
        colecao, _ = Colecao.objects.update_or_create(
            Codigo="26",
            Estacao="01",
            defaults={"Descricao": "Verao 2026", "Status": "AT", "Contador": 0},
        )
        grupo, _ = Grupo.objects.update_or_create(
            Codigo="01",
            defaults={"CodigoRef": "01", "Descricao": "Feminino", "Margem": Decimal("120.00")},
        )
        subgrupo_vestido, _ = Subgrupo.objects.update_or_create(
            Idgrupo=grupo,
            Descricao="Vestidos",
            defaults={"Margem": Decimal("120.00")},
        )
        subgrupo_calca, _ = Subgrupo.objects.update_or_create(
            Idgrupo=grupo,
            Descricao="Calcas",
            defaults={"Margem": Decimal("110.00")},
        )
        tabela, _ = Tabelapreco.objects.update_or_create(
            NomeTabela="Tabela Padrao",
            defaults={"DataInicio": today, "Promocao": False},
        )
        pack, _ = Pack.objects.update_or_create(
            grade=grade,
            nome="Pack Basico 1-2-2-1",
            defaults={"ativo": True},
        )
        for tamanho, qtd in zip(tamanhos, [1, 2, 2, 1, 1]):
            PackItem.objects.update_or_create(
                pack=pack,
                tamanho=tamanho,
                defaults={"qtd": qtd},
            )

        return {
            "unidade": unidade,
            "grade": grade,
            "tamanhos": tamanhos,
            "cores": cores,
            "algodao": algodao,
            "jeans": jeans,
            "colecao": colecao,
            "grupo": grupo,
            "subgrupo_vestido": subgrupo_vestido,
            "subgrupo_calca": subgrupo_calca,
            "tabela": tabela,
            "ncm_vestuario": ncm_vestuario,
            "ncm_calca": ncm_calca,
        }

    def _seed_produtos(self, base, today):
        dados = [
            {
                "descricao": "Vestido Midi Floral",
                "descricao_reduzida": "Vestido Floral",
                "subgrupo": base["subgrupo_vestido"],
                "material": base["algodao"],
                "ncm": base["ncm_vestuario"].ncm,
                "preco": Decimal("189.90"),
                "cores": base["cores"],
            },
            {
                "descricao": "Calca Jeans Reta",
                "descricao_reduzida": "Jeans Reta",
                "subgrupo": base["subgrupo_calca"],
                "material": base["jeans"],
                "ncm": base["ncm_calca"].ncm,
                "preco": Decimal("159.90"),
                "cores": base["cores"][:2],
            },
        ]
        produtos = []
        for item in dados:
            produto, _ = Produto.objects.get_or_create(
                descricao=item["descricao"],
                defaults={
                    "tipo_produto": "1",
                    "descricao_reduzida": item["descricao_reduzida"],
                    "unidade": base["unidade"],
                    "grupo": base["grupo"],
                    "subgrupo": item["subgrupo"],
                    "colecao": base["colecao"],
                    "material": item["material"],
                    "grade": base["grade"],
                    "ncm": item["ncm"],
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
                defaults={"preco": item["preco"], "DataInicio": today, "ativo": True},
            )

            for cor in item["cores"]:
                for tamanho in base["tamanhos"]:
                    ProdutoDetalhe.objects.get_or_create(
                        produto=produto,
                        idcor=cor,
                        idtamanho=tamanho,
                    )

        uso, _ = Produto.objects.get_or_create(
            descricao="Sacola Kraft Media",
            defaults={
                "tipo_produto": "2",
                "descricao_reduzida": "Sacola Kraft",
                "unidade": base["unidade"],
                "ncm": "4819.40.00",
                "origem_mercadoria": 0,
                "ativo": True,
            },
        )
        produtos.append(uso)
        return produtos

    def _seed_estoque(self, lojas, produtos):
        for produto in produtos:
            for sku in produto.skus.all():
                for idx, loja in enumerate(lojas):
                    Estoque.objects.update_or_create(
                        CodigodeBarra=sku.ean13,
                        Idloja=loja,
                        defaults={
                            "referencia": produto.referencia or "",
                            "Estoque": 12 if idx == 0 else 6,
                            "reserva": 0,
                        },
                    )

    def _seed_estoque_movimentos_inventario(self, lojas):
        loja = lojas[0] if lojas else None
        estoque = Estoque.objects.filter(Idloja=loja).first() if loja else None
        if not loja or not estoque:
            return

        EstoqueMovimentacao.objects.update_or_create(
            Idloja=loja,
            CodigodeBarra=estoque.CodigodeBarra,
            documento="INI-001",
            defaults={
                "referencia": estoque.referencia,
                "tipo": EstoqueMovimentacao.TIPO_ENTRADA,
                "quantidade": estoque.Estoque or 0,
                "saldo_anterior": 0,
                "saldo_posterior": estoque.Estoque or 0,
                "observacao": "Carga inicial de teste",
            },
        )

        inventario, _ = InventarioEstoque.objects.update_or_create(
            Idloja=loja,
            descricao="Inventário de Teste",
            defaults={
                "status": InventarioEstoque.STATUS_ABERTO,
                "data_abertura": timezone.localdate(),
                "observacao": "Inventário inicial para homologação",
            },
        )
        InventarioEstoqueItem.objects.update_or_create(
            inventario=inventario,
            CodigodeBarra=estoque.CodigodeBarra,
            defaults={
                "referencia": estoque.referencia,
                "saldo_sistema": estoque.Estoque or 0,
                "saldo_contado": estoque.Estoque or 0,
            },
        )

    def _seed_usuario(self, loja, password):
        User = get_user_model()
        user, created = User.objects.update_or_create(
            username="admin",
            defaults={
                "email": "admin@sysvar.test",
                "first_name": "Admin",
                "last_name": "Teste",
                "type": "Admin",
                "loja": loja,
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
            },
        )
        user.set_password(password)
        user.save(update_fields=["password"])
