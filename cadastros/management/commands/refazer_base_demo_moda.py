from decimal import Decimal
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Max, Q
from django.db.models.signals import post_save, pre_delete, pre_save
from django.utils import timezone

from auditoria import signals as audit_signals
from cadastros.models import Cliente, Empresa, Fornecedor, Funcionarios, Loja, Nat_Lancamento, PlanoContabil
from compras.models import PedidoCompra, PedidoCompraEntrega, PedidoCompraItem, PedidoCompraParcela
from financeiro.services import gerar_lancamento_contabil_movimentacao
from financeiro.models import (
    Caixa,
    CashbackConfig,
    CashbackMovimento,
    ContaBancaria,
    FormaPagamento,
    FormaPagamentoParcela,
    LancamentoContabil,
    MovimentacaoFinanceira,
    Pagar,
    PagarItem,
    PagarRateio,
    Receber,
    ReceberItem,
    ReceberRateio,
    AntecipacaoRecebivel,
    AntecipacaoRecebivelItem,
    ValeTroca,
    ValeTrocaMovimento,
)
from fiscal.models.venda_pdv import money
from fiscal.models.nota_fiscal_entrada import NotaFiscalEntrada, NotaFiscalEntradaItem
from fiscal.models.venda_pdv import (
    NFCe,
    NFeDevolucao,
    VendaDevolucao,
    VendaDevolucaoItem,
    VendaPdv,
    VendaPdvItem,
    VendaPdvPagamento,
)
from produto.models import (
    Codigos,
    Colecao,
    ConfigEan,
    Cor,
    Estoque,
    EstoqueMovimentacao,
    Grade,
    Grupo,
    InventarioEstoque,
    InventarioEstoqueItem,
    Material,
    Ncm,
    Pack,
    PackItem,
    Produto,
    ProdutoDetalhe,
    Promocao,
    Subgrupo,
    Tabelapreco,
    TabelaprecoProduto,
    Tamanho,
    Unidade,
)


class Command(BaseCommand):
    help = "Refaz a base demonstrativa de moda preservando empresas, usuarios, plano contabil e naturezas."

    def add_arguments(self, parser):
        parser.add_argument("--confirmar", action="store_true", help="Confirma a limpeza e recriacao da base demo.")
        parser.add_argument("--empresa", type=int, help="Opcional: refaz somente uma empresa.")

    @transaction.atomic
    def handle(self, *args, **options):
        if not options["confirmar"]:
            raise CommandError("Use --confirmar para apagar os dados operacionais e refazer a base demo.")

        self._desligar_auditoria()
        try:
            empresas = Empresa.objects.filter(ativo=True).order_by("id")
            if options.get("empresa"):
                empresas = empresas.filter(pk=options["empresa"])
            if not empresas.exists():
                empresa = Empresa.objects.create(
                    nome="CISVAR Demonstração Ltda",
                    nome_fantasia="CISVAR Moda",
                    documento=self._cnpj(90),
                    ativo=True,
                )
                empresas = Empresa.objects.filter(pk=empresa.pk)

            self._limpar_operacional(empresas)

            for idx, empresa in enumerate(empresas, start=1):
                contexto = self._criar_empresa_demo(empresa, idx)
                self._ajustar_usuarios(empresa, contexto["lojas"])

            self.stdout.write(self.style.SUCCESS("Base demonstrativa de moda recriada com sucesso."))
            self.stdout.write("Preservados: empresas, usuarios, plano contabil e naturezas financeiras.")
        finally:
            self._religar_auditoria()

    def _desligar_auditoria(self):
        pre_save.disconnect(audit_signals.audit_presave_snapshot)
        post_save.disconnect(audit_signals.audit_postsave)
        pre_delete.disconnect(audit_signals.audit_predelete)

    def _religar_auditoria(self):
        pre_save.connect(audit_signals.audit_presave_snapshot)
        post_save.connect(audit_signals.audit_postsave)
        pre_delete.connect(audit_signals.audit_predelete)

    def _limpar_operacional(self, empresas):
        empresa_ids = list(empresas.values_list("id", flat=True))
        lojas = Loja.objects.filter(empresa_id__in=empresa_ids)

        User = get_user_model()
        User.objects.filter(empresa_id__in=empresa_ids).update(loja=None)
        for usuario in User.objects.filter(empresa_id__in=empresa_ids):
            usuario.lojas.clear()

        NFeDevolucao.objects.filter(devolucao__empresa_id__in=empresa_ids).delete()
        NFCe.objects.filter(venda__empresa_id__in=empresa_ids).delete()
        VendaDevolucaoItem.objects.filter(devolucao__empresa_id__in=empresa_ids).delete()
        ValeTrocaMovimento.objects.filter(vale__empresa_id__in=empresa_ids).delete()
        ValeTroca.objects.filter(empresa_id__in=empresa_ids).delete()
        CashbackMovimento.objects.filter(empresa_id__in=empresa_ids).delete()
        VendaDevolucao.objects.filter(empresa_id__in=empresa_ids).delete()
        VendaPdvPagamento.objects.filter(venda__empresa_id__in=empresa_ids).delete()
        VendaPdvItem.objects.filter(venda__empresa_id__in=empresa_ids).delete()
        VendaPdv.objects.filter(empresa_id__in=empresa_ids).delete()

        LancamentoContabil.objects.filter(empresa_id__in=empresa_ids).delete()
        AntecipacaoRecebivelItem.objects.filter(antecipacao__empresa_id__in=empresa_ids).delete()
        AntecipacaoRecebivel.objects.filter(empresa_id__in=empresa_ids).delete()
        MovimentacaoFinanceira.objects.filter(empresa_id__in=empresa_ids).delete()
        ReceberRateio.objects.filter(Idreceberitem__Idreceber__empresa_id__in=empresa_ids).delete()
        PagarRateio.objects.filter(Idpagaritem__Idpagar__empresa_id__in=empresa_ids).delete()
        ReceberItem.objects.filter(Idreceber__empresa_id__in=empresa_ids).delete()
        Receber.objects.filter(empresa_id__in=empresa_ids).delete()
        PagarItem.objects.filter(Idpagar__empresa_id__in=empresa_ids).delete()
        Pagar.objects.filter(empresa_id__in=empresa_ids).delete()

        NotaFiscalEntradaItem.objects.filter(nota__pedido_compra__empresa_id__in=empresa_ids).delete()
        NotaFiscalEntrada.objects.filter(pedido_compra__empresa_id__in=empresa_ids).delete()
        PedidoCompraEntrega.objects.filter(item__pedido__empresa_id__in=empresa_ids).delete()
        PedidoCompraParcela.objects.filter(pedido__empresa_id__in=empresa_ids).delete()
        PedidoCompraItem.objects.filter(pedido__empresa_id__in=empresa_ids).delete()
        PedidoCompra.objects.filter(empresa_id__in=empresa_ids).delete()

        InventarioEstoqueItem.objects.filter(inventario__Idloja__in=lojas).delete()
        InventarioEstoque.objects.filter(Idloja__in=lojas).delete()
        EstoqueMovimentacao.objects.filter(Idloja__in=lojas).delete()
        Estoque.objects.filter(Idloja__in=lojas).delete()

        Promocao.objects.filter(empresa_id__in=empresa_ids).delete()
        TabelaprecoProduto.objects.filter(produto__empresa_id__in=empresa_ids).delete()
        ProdutoDetalhe.objects.filter(produto__empresa_id__in=empresa_ids).delete()
        PackItem.objects.filter(pack__empresa_id__in=empresa_ids).delete()
        Pack.objects.filter(empresa_id__in=empresa_ids).delete()
        Produto.objects.filter(empresa_id__in=empresa_ids).delete()
        Codigos.objects.filter(empresa_id__in=empresa_ids).delete()
        Tabelapreco.objects.filter(empresa_id__in=empresa_ids).delete()
        Subgrupo.objects.filter(empresa_id__in=empresa_ids).delete()
        Grupo.objects.filter(empresa_id__in=empresa_ids).delete()
        Colecao.objects.filter(empresa_id__in=empresa_ids).delete()
        Material.objects.filter(empresa_id__in=empresa_ids).delete()
        Cor.objects.filter(empresa_id__in=empresa_ids).delete()
        Tamanho.objects.filter(empresa_id__in=empresa_ids).delete()
        Grade.objects.filter(empresa_id__in=empresa_ids).delete()
        Unidade.objects.filter(empresa_id__in=empresa_ids).delete()
        Ncm.objects.filter(empresa_id__in=empresa_ids).delete()
        ConfigEan.objects.filter(empresa_id__in=empresa_ids).delete()

        FormaPagamentoParcela.objects.filter(forma__empresa_id__in=empresa_ids).delete()
        FormaPagamento.objects.filter(empresa_id__in=empresa_ids).delete()
        CashbackConfig.objects.filter(empresa_id__in=empresa_ids).delete()
        ContaBancaria.objects.filter(empresa_id__in=empresa_ids).delete()
        Caixa.objects.filter(empresa_id__in=empresa_ids).delete()

        Funcionarios.objects.filter(empresa_id__in=empresa_ids).delete()
        Fornecedor.objects.filter(empresa_id__in=empresa_ids).delete()
        Cliente.objects.filter(empresa_id__in=empresa_ids).delete()
        Loja.objects.filter(empresa_id__in=empresa_ids).delete()

    def _criar_empresa_demo(self, empresa, idx):
        self._atualizar_empresa(empresa, idx)
        lojas = self._lojas(empresa, idx)
        clientes = self._clientes(empresa, idx)
        fornecedores = self._fornecedores(empresa, idx)
        funcionarios = self._funcionarios(empresa, lojas, idx)
        financeiro = self._financeiro(empresa, lojas, idx)
        base = self._base_produtos(empresa, idx)
        produtos = self._produtos(base)
        self._estoque(lojas, produtos)
        self._vendas_demo(empresa, lojas, clientes, funcionarios, financeiro)
        self._pedidos_compra_para_aprovar(empresa, lojas, fornecedores, produtos, base, financeiro)
        return {"lojas": lojas, "clientes": clientes, "fornecedores": fornecedores, "funcionarios": funcionarios, "financeiro": financeiro}

    def _atualizar_empresa(self, empresa, idx):
        if not empresa.nome_fantasia:
            empresa.nome_fantasia = "Maison Aurora" if idx == 1 else f"Rede Moda {idx}"
        if not empresa.documento:
            empresa.documento = self._cnpj(100 + idx)
        empresa.ativo = True
        empresa.save(update_fields=["nome_fantasia", "documento", "ativo"])

    def _lojas(self, empresa, idx):
        nomes = [
            ("Maison Aurora Matriz", "MATRIZ", "Rua Oscar Freire", "1120", "Jardins"),
            ("Maison Aurora Shopping", "SHOPPING", "Avenida Roque Petroni Junior", "1089", "Morumbi"),
            ("Maison Aurora Vila Madalena", "VILA", "Rua Harmonia", "455", "Vila Madalena"),
        ]
        lojas = []
        for pos, (nome, apelido, endereco, numero, bairro) in enumerate(nomes, start=1):
            loja = Loja.objects.create(
                empresa=empresa,
                nome_loja=nome if idx == 1 else f"{nome} E{idx}",
                apelido_loja=apelido,
                cnpj=self._cnpj(idx * 1000 + pos),
                logradouro="Rua" if pos != 2 else "Avenida",
                endereco=endereco,
                numero=numero,
                complemento="Loja A" if pos == 2 else "",
                cep=f"01{idx}{pos}0-000",
                bairro=bairro,
                cidade="Sao Paulo",
                estado="SP",
                telefone1=f"1130{idx}{pos}9000",
                email=f"loja{pos}@{self._dominio(empresa)}",
                EstoqueNegativo="NAO",
                Rede="SIM",
                Matriz="SIM" if pos == 1 else "NAO",
                DataAbertura=timezone.localdate().replace(month=1, day=10),
                ContaContabil="1.1.01",
                ativo=True,
            )
            lojas.append(loja)
        return lojas

    def _clientes(self, empresa, idx):
        nomes = [
            ("Mariana Lopes Nogueira", "MARIANA", "VIP"),
            ("Camila Ribeiro Azevedo", "CAMILA", "Varejo"),
            ("Patricia Moreira Santos", "PATRICIA", "Varejo"),
            ("Juliana Carvalho Lima", "JULIANA", "VIP"),
            ("Renata Fernandes Prado", "RENATA", "Varejo"),
            ("Leticia Almeida Rocha", "LETICIA", "Varejo"),
        ]
        clientes = [
            Cliente.objects.create(
                empresa=empresa,
                nome_cliente="Consumidor Final",
                apelido="CONSUMIDOR",
                cpf="00000000000",
                cidade="Sao Paulo",
                estado="SP",
                telefone1="11999990000",
                email=f"consumidor@{self._dominio(empresa)}",
                categoria="Padrao",
                ativo=True,
            )
        ]
        for pos, (nome, apelido, categoria) in enumerate(nomes, start=1):
            clientes.append(
                Cliente.objects.create(
                    empresa=empresa,
                    nome_cliente=nome,
                    apelido=apelido,
                    cpf=self._cpf(idx * 100 + pos),
                    logradouro="Rua",
                    endereco=f"Rua das Flores {pos}",
                    numero=str(100 + pos),
                    cep=f"04{idx}{pos}0-000",
                    bairro="Pinheiros",
                    cidade="Sao Paulo",
                    estado="SP",
                    telefone1=f"1198{idx}{pos}7000",
                    email=f"{apelido.lower()}@cliente.com.br",
                    categoria=categoria,
                    mala_direta=True,
                    ativo=True,
                )
            )
        return clientes

    def _fornecedores(self, empresa, idx):
        dados = [
            ("Textil Aurora Ltda", "AURORA", "Revenda"),
            ("Confecções Bella Forma Ltda", "BELLA", "Revenda"),
            ("Malharia Ponto Fino Ltda", "PONTO FINO", "Revenda"),
            ("Estamparia Jardim das Cores Ltda", "JARDIM CORES", "Revenda"),
            ("Embalagens Prime Comercio Ltda", "PRIME EMB", "Uso/Consumo"),
            ("Papelaria Central Office Ltda", "CENTRAL OFFICE", "Uso/Consumo"),
        ]
        fornecedores = []
        for pos, (nome, apelido, categoria) in enumerate(dados, start=1):
            fornecedores.append(
                Fornecedor.objects.create(
                    empresa=empresa,
                    nome_fornecedor=nome,
                    apelido=apelido,
                    cnpj=self._cnpj(idx * 2000 + pos),
                    logradouro="Rua",
                    endereco=f"Rua Industrial {pos}",
                    numero=str(300 + pos),
                    cep=f"06{idx}{pos}0-000",
                    bairro="Centro Industrial",
                    cidade="Sao Paulo",
                    estado="SP",
                    telefone1=f"1133{idx}{pos}4000",
                    email=f"comercial{pos}@fornecedor.com.br",
                    categoria=categoria,
                    ativo=True,
                )
            )
        return fornecedores

    def _funcionarios(self, empresa, lojas, idx):
        base_nomes = [
            ("Ana Beatriz Costa", "ANA", "Gerente", Decimal("0.00")),
            ("Bruno Henrique Martins", "BRUNO", "Caixa", Decimal("0.00")),
            ("Carolina Menezes", "CAROL", "Vendedor", Decimal("4.00")),
            ("Diego Pacheco", "DIEGO", "Vendedor", Decimal("4.00")),
            ("Fernanda Duarte", "FERNANDA", "Vendedor", Decimal("4.00")),
        ]
        funcionarios = []
        seq = 1
        for loja in lojas:
            for nome, apelido, categoria, comissao in base_nomes:
                funcionarios.append(
                    Funcionarios.objects.create(
                        empresa=empresa,
                        nomefuncionario=nome if loja == lojas[0] else f"{nome} {loja.apelido_loja}",
                        apelido=apelido,
                        cpf=self._cpf(idx * 1000 + seq),
                        inicio=timezone.localdate().replace(month=1, day=2),
                        categoria=categoria,
                        meta=Decimal("45000.00") if categoria == "Vendedor" else Decimal("0.00"),
                        comissao_percentual=comissao,
                        idloja=loja,
                        ativo=True,
                    )
                )
                seq += 1
        return funcionarios

    def _financeiro(self, empresa, lojas, idx):
        caixas = []
        for pos, loja in enumerate(lojas, start=1):
            caixas.append(
                Caixa.objects.create(
                    empresa=empresa,
                    idloja=loja,
                    codigo=f"L{pos:02d}CX01",
                    tipo_caixa=Caixa.TIPO_LOJA,
                    descricao=f"Caixa principal {loja.apelido_loja}",
                    saldo_inicial=Decimal("300.00"),
                    saldo_atual=Decimal("300.00"),
                    ativo=True,
                )
            )
        caixa_master = Caixa.objects.create(
            empresa=empresa,
            idloja=None,
            codigo="MASTER",
            tipo_caixa=Caixa.TIPO_MASTER,
            descricao="Caixa master da empresa",
            saldo_inicial=Decimal("0.00"),
            saldo_atual=Decimal("0.00"),
            ativo=True,
        )
        contas = []
        contas_por_loja = {}
        for pos, loja in enumerate(lojas, start=1):
            conta = ContaBancaria.objects.create(
                empresa=empresa,
                idloja=loja,
                descricao=f"Conta Itau {loja.apelido_loja}",
                banco="Banco Itau",
                agencia=f"{3000 + idx}",
                conta=f"{idx}{pos:02d}{12000 + pos}-5",
                tipo_conta="CORRENTE",
                pix_chave=f"{loja.apelido_loja.lower()}@{self._dominio(empresa)}",
                saldo_inicial=Decimal("5000.00"),
                saldo_atual=Decimal("5000.00"),
                ativo=True,
            )
            contas.append(conta)
            contas_por_loja[loja.id] = conta
        conta_padrao = contas[0]
        formas = [
            ("DIN", "Dinheiro", 1, [0], False, "", None, Decimal("0.0000")),
            ("PIX", "Pix", 1, [0], True, "Itau Empresas", conta_padrao, Decimal("0.0000")),
            ("DEB", "Cartao de debito", 1, [1], True, "Rede", conta_padrao, Decimal("1.2500")),
            ("CRE", "Cartao de credito", 1, [30], True, "Rede", conta_padrao, Decimal("2.4900")),
            ("CRE2", "Cartao de credito 2x", 2, [30, 60], True, "Rede", conta_padrao, Decimal("2.9900")),
            ("TRC", "Vale troca", 1, [0], False, "", None, Decimal("0.0000")),
        ]
        formas_criadas = []
        for codigo, descricao, parcelas, dias, recebivel, adquirente, conta_liq, taxa in formas:
            forma = FormaPagamento.objects.create(
                empresa=empresa,
                codigo=codigo,
                descricao=descricao,
                num_parcelas=parcelas,
                ativo=True,
                gera_recebivel_bancario=recebivel,
                adquirente=adquirente,
                conta_liquidacao=conta_liq,
                prazo_credito_dias=max(dias),
                taxa_percentual=taxa,
            )
            percentual = Decimal("100.000000") / Decimal(parcelas)
            for ordem, prazo in enumerate(dias, start=1):
                FormaPagamentoParcela.objects.create(forma=forma, ordem=ordem, dias=prazo, percentual=percentual)
            formas_criadas.append(forma)
        CashbackConfig.objects.create(
            empresa=empresa,
            nome="Cashback padrao",
            ativo=True,
            percentual=Decimal("3.0000"),
            validade_dias=180,
            valor_minimo_geracao=Decimal("0.00"),
            valor_minimo_uso=Decimal("0.00"),
            limite_uso_percentual=Decimal("100.0000"),
            consumidor_final_participa=False,
        )
        return {
            "caixas": caixas,
            "caixa_master": caixa_master,
            "contas": contas,
            "contas_por_loja": contas_por_loja,
            "formas": formas_criadas,
        }

    def _base_produtos(self, empresa, idx):
        ncm = Ncm.objects.create(empresa=empresa, ncm="6204.62.00", descricao="Vestuário feminino", aliquota=Decimal("18.00"))
        unidade = Unidade.objects.create(empresa=empresa, Descricao="Unidade", Codigo="UN")
        material = Material.objects.create(empresa=empresa, Descricao="Algodao", Codigo="ALG", Status="ATIVO")
        grade_num = Grade.objects.create(empresa=empresa, Descricao="Grade jeans 38 a 48", Status="ATIVO")
        grade_alpha = Grade.objects.create(empresa=empresa, Descricao="Grade moda PP a GG", Status="ATIVO")
        tamanhos_num = [
            Tamanho.objects.create(empresa=empresa, idgrade=grade_num, Tamanho=tam, Descricao=tam, Status="ATIVO")
            for tam in ["38", "40", "42", "44", "46", "48"]
        ]
        tamanhos_alpha = [
            Tamanho.objects.create(empresa=empresa, idgrade=grade_alpha, Tamanho=tam, Descricao=tam, Status="ATIVO")
            for tam in ["PP", "P", "M", "G", "GG"]
        ]
        cores = [
            Cor.objects.create(empresa=empresa, Codigo=cod, Descricao=nome, Cor=nome, Status="ATIVO")
            for cod, nome in [("BR", "Branca"), ("PR", "Preta")]
        ]
        colecao = Colecao.objects.create(empresa=empresa, Codigo="26", Estacao="01", Descricao="Verao 2026", Status="AT")
        tabela = Tabelapreco.objects.create(empresa=empresa, NomeTabela="Tabela varejo", DataInicio=timezone.localdate(), Promocao=False)
        ConfigEan.objects.create(empresa=empresa, country_prefix="789", company_prefix=f"{idx:04d}", next_itemref=1, ativo=True)

        grupos = {}
        estrutura = [
            ("CALCA", "01", "Calca", ["Jeans", "Alfaiataria", "Pantalona"]),
            ("SAIA", "02", "Saia", ["Midi", "Longa", "Curta"]),
            ("BLUSA", "03", "Blusa", ["Lisa", "Estampada", "Regata"]),
            ("VEST", "04", "Vestido", ["Liso", "Estampado", "Renda"]),
        ]
        for codigo, cod_ref, descricao, subs in estrutura:
            grupo = Grupo.objects.create(empresa=empresa, Codigo=codigo, CodigoRef=cod_ref, Descricao=descricao, Margem=Decimal("55.00"))
            grupos[descricao] = {"grupo": grupo, "subgrupos": {}}
            for sub in subs:
                grupos[descricao]["subgrupos"][sub] = Subgrupo.objects.create(
                    empresa=empresa,
                    Idgrupo=grupo,
                    Descricao=sub,
                    Margem=Decimal("55.00"),
                )

        return {
            "ncm": ncm.ncm,
            "unidade": unidade,
            "material": material,
            "grade_num": grade_num,
            "grade_alpha": grade_alpha,
            "tamanhos_num": tamanhos_num,
            "tamanhos_alpha": tamanhos_alpha,
            "cores": cores,
            "colecao": colecao,
            "tabela": tabela,
            "grupos": grupos,
        }

    def _produtos(self, base):
        revenda = [
            ("Calca", "Jeans", "Calca Jeans Reta Escura", Decimal("299.90"), "num"),
            ("Blusa", "Lisa", "Blusa Basica Lisa", Decimal("159.90"), "alpha"),
            ("Vestido", "Estampado", "Vestido Midi Floral", Decimal("399.90"), "alpha"),
        ]
        produtos = []
        for grupo_nome, sub_nome, descricao, preco, grade_tipo in revenda:
            grupo = base["grupos"][grupo_nome]["grupo"]
            subgrupo = base["grupos"][grupo_nome]["subgrupos"][sub_nome]
            grade = base["grade_num"] if grade_tipo == "num" else base["grade_alpha"]
            tamanhos = base["tamanhos_num"] if grade_tipo == "num" else base["tamanhos_alpha"]
            produto = Produto.objects.create(
                empresa=grupo.empresa,
                tipo_produto="1",
                descricao=descricao,
                descricao_reduzida=descricao[:60],
                unidade=base["unidade"],
                grupo=grupo,
                subgrupo=subgrupo,
                colecao=base["colecao"],
                material=base["material"],
                grade=grade,
                ncm=base["ncm"],
                origem_mercadoria=0,
                csosn_ou_cst_icms="102",
                aliquota_icms=Decimal("18.00"),
                cfop_venda_dentro="5102",
                cfop_venda_fora="6102",
                cst_pis="49",
                cst_cofins="49",
                ativo=True,
            )
            produtos.append(produto)
            TabelaprecoProduto.objects.create(
                produto=produto,
                tabela=base["tabela"],
                preco=preco,
                DataInicio=timezone.localdate(),
                ativo=True,
            )
            custo = money(preco * Decimal("0.30"))
            for cor in base["cores"]:
                for tamanho in tamanhos:
                    ProdutoDetalhe.objects.create(
                        produto=produto,
                        idcor=cor,
                        idtamanho=tamanho,
                        custo_original=custo,
                        custo_ultima_compra=custo,
                    )

        uso = [
            ("Sacola Papel Kraft Media", "UC-001"),
            ("Etiqueta Adesiva de Preco", "UC-002"),
            ("Cabide Plastico Adulto", "UC-003"),
            ("Bobina Termica PDV", "UC-004"),
        ]
        for descricao, referencia in uso:
            Produto.objects.create(
                empresa=base["colecao"].empresa,
                tipo_produto="2",
                referencia=referencia,
                descricao=descricao,
                descricao_reduzida=descricao,
                unidade=base["unidade"],
                material=base["material"],
                ncm=base["ncm"],
                ativo=True,
            )

        pack_num = Pack.objects.create(empresa=base["colecao"].empresa, grade=base["grade_num"], nome="Pack jeans 38-48", ativo=True)
        for tamanho in base["tamanhos_num"]:
            PackItem.objects.create(pack=pack_num, tamanho=tamanho, qtd=1)
        pack_alpha = Pack.objects.create(empresa=base["colecao"].empresa, grade=base["grade_alpha"], nome="Pack moda PP-GG", ativo=True)
        for tamanho in base["tamanhos_alpha"]:
            PackItem.objects.create(pack=pack_alpha, tamanho=tamanho, qtd=1)
        return produtos

    def _estoque(self, lojas, produtos):
        for loja_idx, loja in enumerate(lojas, start=1):
            for produto in produtos:
                for sku in produto.skus.all():
                    saldo = 6 + loja_idx
                    Estoque.objects.create(
                        CodigodeBarra=sku.ean13,
                        referencia=produto.referencia or "",
                        Idloja=loja,
                        Estoque=saldo,
                        reserva=0,
                    )
                    EstoqueMovimentacao.objects.create(
                        Idloja=loja,
                        CodigodeBarra=sku.ean13,
                        referencia=produto.referencia or "",
                        tipo=EstoqueMovimentacao.TIPO_ENTRADA,
                        quantidade=saldo,
                        saldo_anterior=0,
                        saldo_posterior=saldo,
                        documento="CARGA-INICIAL",
                        observacao="Carga inicial da base demonstrativa",
                    )

    def _vendas_demo(self, empresa, lojas, clientes, funcionarios, financeiro):
        cliente_padrao = next((cliente for cliente in clientes if cliente.cpf == "00000000000"), clientes[0])
        clientes_identificados = [cliente for cliente in clientes if cliente.cpf != "00000000000"] or [cliente_padrao]
        formas = {forma.codigo.upper(): forma for forma in financeiro["formas"]}
        natureza = self._natureza_venda(empresa)
        data_base = timezone.localdate()
        alvo_loja = Decimal("16000.00")

        for loja_idx, loja in enumerate(lojas, start=1):
            caixa = next(caixa for caixa in financeiro["caixas"] if caixa.idloja_id == loja.id)
            vendedores = [
                func for func in funcionarios
                if func.idloja_id == loja.id and (func.categoria or "").lower() == "vendedor"
            ]
            if not vendedores:
                continue
            estoque_rows = (
                Estoque.objects
                .select_related("Idloja")
                .filter(Idloja=loja, Estoque__gt=0)
                .order_by("CodigodeBarra")
            )
            itens_disponiveis = []
            for estoque in estoque_rows:
                sku = ProdutoDetalhe.objects.select_related("produto").filter(ean13=estoque.CodigodeBarra).first()
                if not sku:
                    continue
                preco_row = (
                    TabelaprecoProduto.objects
                    .filter(produto=sku.produto, ativo=True)
                    .order_by("-DataInicio")
                    .first()
                )
                if not preco_row:
                    continue
                itens_disponiveis.append({
                    "estoque": estoque,
                    "sku": sku,
                    "produto": sku.produto,
                    "preco": money(preco_row.preco),
                    "saldo": int(estoque.Estoque or 0),
                })

            total_loja = Decimal("0.00")
            venda_seq = 0
            item_idx = 0
            vendas_planejadas = 29
            for venda_seq in range(1, vendas_planejadas + 1):
                restante_loja = money(alvo_loja - total_loja)
                if restante_loja <= 0:
                    break
                vendas_restantes = vendas_planejadas - venda_seq + 1
                ticket_alvo = money(restante_loja / Decimal(vendas_restantes))
                venda_atual = []
                total_atual = Decimal("0.00")
                tentativas = 0
                while total_atual < ticket_alvo and tentativas < len(itens_disponiveis) * 2:
                    item = itens_disponiveis[item_idx % len(itens_disponiveis)]
                    item_idx += 1
                    tentativas += 1
                    if item["saldo"] <= 0:
                        continue
                    item["saldo"] -= 1
                    venda_atual.append({**item, "quantidade": 1, "total": item["preco"]})
                    total_atual = money(total_atual + item["preco"])
                    if len(venda_atual) >= 5:
                        break
                if not venda_atual:
                    break
                self._criar_venda_demo(
                    empresa, loja, caixa, clientes_identificados, cliente_padrao, vendedores,
                    formas, natureza, venda_atual, data_base, loja_idx, venda_seq
                )
                total_loja = money(total_loja + total_atual)

    def _criar_venda_demo(self, empresa, loja, caixa, clientes, cliente_padrao, vendedores, formas, natureza, linhas, data_base, loja_idx, venda_seq):
        documento = self._proximo_documento_pdv()
        vendedor = vendedores[(venda_seq - 1) % len(vendedores)]
        cliente = clientes[(venda_seq - 1) % len(clientes)] if venda_seq % 4 else cliente_padrao
        data_venda = timezone.make_aware(datetime.combine(data_base, time(hour=9 + ((venda_seq - 1) % 9), minute=(venda_seq * 7) % 60)))
        total = money(sum((linha["total"] for linha in linhas), Decimal("0.00")))
        venda = VendaPdv.objects.create(
            empresa=empresa,
            loja=loja,
            caixa=caixa,
            cliente=cliente,
            vendedor=vendedor,
            documento=documento,
            status=VendaPdv.Status.FINALIZADA,
            forma_pagamento="MULTIPLO",
            data_venda=data_venda,
            subtotal=total,
            desconto_itens=Decimal("0.00"),
            desconto_geral=Decimal("0.00"),
            total=total,
            valor_recebido=total,
            troco=Decimal("0.00"),
        )
        for linha in linhas:
            estoque = linha["estoque"]
            sku = linha["sku"]
            produto = linha["produto"]
            quantidade = int(linha["quantidade"])
            anterior = int(estoque.Estoque or 0)
            posterior = anterior - quantidade
            estoque.Estoque = posterior
            estoque.save(update_fields=["Estoque"])
            EstoqueMovimentacao.objects.create(
                Idloja=loja,
                CodigodeBarra=sku.ean13,
                referencia=produto.referencia or "",
                tipo=EstoqueMovimentacao.TIPO_SAIDA,
                quantidade=quantidade,
                saldo_anterior=anterior,
                saldo_posterior=posterior,
                documento=documento,
                observacao=f"Venda demonstrativa PDV {documento}",
            )
            VendaPdvItem.objects.create(
                venda=venda,
                produto=produto,
                sku=sku,
                ean=sku.ean13,
                referencia=produto.referencia or "",
                descricao=produto.descricao,
                cor=sku.idcor.Descricao,
                tamanho=sku.idtamanho.Tamanho,
                quantidade=quantidade,
                preco_unitario=linha["preco"],
                desconto=Decimal("0.00"),
                custo_unitario=money(sku.custo_ultima_compra or sku.custo_original or 0),
                cmv_total=money(Decimal(quantidade) * Decimal(sku.custo_ultima_compra or sku.custo_original or 0)),
            )

        pagamentos = self._pagamentos_demo(total, formas, venda_seq)
        for pagamento in pagamentos:
            VendaPdvPagamento.objects.create(venda=venda, **pagamento)
        venda.forma_pagamento = pagamentos[0]["forma"] if len(pagamentos) == 1 else "MULTIPLO"
        venda.save(update_fields=["forma_pagamento", "atualizado_em"])
        self._financeiro_venda(venda, natureza, formas)
        self._cmv_venda(venda)
        self._comissao_venda(venda)
        self._cashback_venda(venda)
        self._nfce_demo(venda)

    def _pagamentos_demo(self, total, formas, venda_seq):
        if venda_seq % 2 == 0:
            return [{"forma": "PIX", "descricao": formas["PIX"].descricao, "valor": total, "autorizacao": f"PIX{venda_seq:04d}"}]
        return [{"forma": "DIN", "descricao": formas["DIN"].descricao, "valor": total, "autorizacao": ""}]

    def _financeiro_venda(self, venda, natureza, formas):
        receber = Receber.objects.create(
            empresa=venda.empresa,
            idloja=venda.loja,
            idcliente=venda.cliente,
            Titulo=str(venda.documento),
            Documento=venda.documento,
            Data_emissao=timezone.localdate(),
            Valor_total=money(venda.total),
            Previsao=False,
            FormaPagamento=venda.forma_pagamento,
            Idnatureza=natureza,
            pedido_venda=venda.pk,
        )
        parcela = 1
        for pagamento in venda.pagamentos.all():
            forma = formas.get(pagamento.forma.upper())
            valor = money(pagamento.valor)
            if forma and forma.gera_recebivel_bancario and forma.conta_liquidacao_id:
                item = ReceberItem.objects.create(
                    Idreceber=receber,
                    parcela_n=parcela,
                    status=ReceberItem.STATUS_EFETIVO,
                    Data_vencimento=timezone.localdate() + timedelta(days=int(forma.prazo_credito_dias or 0)),
                    valor_parcela=valor,
                    FormaPagamento=pagamento.forma,
                    Previsao=True,
                    Idnatureza=natureza,
                )
                self._movimento_bancario_previsto(venda, natureza, pagamento, forma, valor, item)
            else:
                item = ReceberItem.objects.create(
                    Idreceber=receber,
                    parcela_n=parcela,
                    status=ReceberItem.STATUS_BAIXADO,
                    Data_vencimento=timezone.localdate(),
                    valor_parcela=valor,
                    FormaPagamento=pagamento.forma,
                    Previsao=False,
                    Idnatureza=natureza,
                    data_baixa=timezone.localdate(),
                    valor_baixa=valor,
                )
                self._movimento_caixa_venda(venda, natureza, pagamento, valor, item)
            parcela += 1

    def _movimento_caixa_venda(self, venda, natureza, pagamento, valor, item):
        venda.caixa.saldo_atual = money(venda.caixa.saldo_atual) + valor
        venda.caixa.save(update_fields=["saldo_atual"])
        MovimentacaoFinanceira.objects.create(
            empresa=venda.empresa,
            idloja=venda.loja,
            data_movimento=timezone.localdate(),
            tipo=MovimentacaoFinanceira.TIPO_ENTRADA,
            status=MovimentacaoFinanceira.STATUS_EFETIVA,
            origem=MovimentacaoFinanceira.ORIGEM_RECEBER,
            valor=valor,
            historico=f"Venda PDV {venda.documento} - {pagamento.descricao or pagamento.forma}",
            documento=venda.documento,
            Idnatureza=natureza,
            FormaPagamento=pagamento.forma,
            caixa=venda.caixa,
            receber_item=item,
        )
        master = Caixa.objects.filter(empresa=venda.empresa, tipo_caixa=Caixa.TIPO_MASTER, ativo=True).first()
        if master:
            master.saldo_atual = money(master.saldo_atual) + valor
            master.save(update_fields=["saldo_atual"])
            MovimentacaoFinanceira.objects.create(
                empresa=venda.empresa,
                idloja=venda.loja,
                data_movimento=timezone.localdate(),
                tipo=MovimentacaoFinanceira.TIPO_ENTRADA,
                status=MovimentacaoFinanceira.STATUS_EFETIVA,
                origem=MovimentacaoFinanceira.ORIGEM_MANUAL,
                valor=valor,
                historico=f"Consolidacao master PDV {venda.documento}",
                documento=venda.documento,
                Idnatureza=natureza,
                FormaPagamento=pagamento.forma,
                caixa=master,
            )

    def _movimento_bancario_previsto(self, venda, natureza, pagamento, forma, valor_bruto, item):
        taxa = money((valor_bruto * Decimal(forma.taxa_percentual or 0) / Decimal("100")) + Decimal(forma.taxa_fixa or 0))
        valor_liquido = money(max(Decimal("0.00"), valor_bruto - taxa))
        data_prevista = timezone.localdate() + timedelta(days=int(forma.prazo_credito_dias or 0))
        conta_liquidacao = (
            ContaBancaria.objects
            .filter(empresa=venda.empresa, idloja=venda.loja, ativo=True)
            .order_by("Idconta")
            .first()
        ) or forma.conta_liquidacao
        status_movimento = MovimentacaoFinanceira.STATUS_PREVISTA
        origem_movimento = MovimentacaoFinanceira.ORIGEM_CARTAO
        if int(forma.prazo_credito_dias or 0) <= 0:
            status_movimento = MovimentacaoFinanceira.STATUS_EFETIVA
            origem_movimento = MovimentacaoFinanceira.ORIGEM_RECEBER
            conta_liquidacao.saldo_atual = money(Decimal(conta_liquidacao.saldo_atual or 0) + valor_liquido)
            conta_liquidacao.save(update_fields=["saldo_atual"])
        MovimentacaoFinanceira.objects.create(
            empresa=venda.empresa,
            idloja=venda.loja,
            data_movimento=data_prevista,
            tipo=MovimentacaoFinanceira.TIPO_ENTRADA,
            status=status_movimento,
            origem=origem_movimento,
            valor=valor_liquido,
            historico=f"Recebivel {forma.descricao} PDV {venda.documento} - {forma.adquirente or ''} | bruto {valor_bruto} taxa {taxa}"[:255],
            documento=venda.documento,
            Idnatureza=natureza,
            FormaPagamento=pagamento.forma,
            conta_bancaria=conta_liquidacao,
            receber_item=item,
        )

    def _cashback_venda(self, venda):
        config = CashbackConfig.regra_ativa(venda.empresa)
        if not config:
            return
        cpf = "".join(ch for ch in str(venda.cliente.cpf or "") if ch.isdigit())
        if cpf == "00000000000" and not config.consumidor_final_participa:
            return
        valor = money(money(venda.total) * Decimal(config.percentual or 0) / Decimal("100"))
        if valor <= 0:
            return
        CashbackMovimento.objects.create(
            empresa=venda.empresa,
            cliente=venda.cliente,
            venda_origem=venda,
            tipo=CashbackMovimento.TIPO_CREDITO,
            valor=valor,
            validade=timezone.localdate() + timedelta(days=int(config.validade_dias or 0)),
            observacao=f"Credito gerado pela venda demonstrativa {venda.documento}",
        )

    def _nfce_demo(self, venda):
        numero = int(venda.documento)
        nfce = NFCe.objects.create(venda=venda, numero=numero, status=NFCe.Status.AUTORIZADA)
        nfce.chave_acesso = self._chave_nfce(nfce)
        nfce.protocolo = f"135{timezone.now().strftime('%y%m%d%H%M%S')}{numero:04d}"[:30]
        nfce.qr_code_url = f"https://homologacao.nfce.sysvar.local/consulta?p={nfce.chave_acesso}"
        nfce.xml = f"<NFCe ambiente=\"homologacao\" chave=\"{nfce.chave_acesso}\" venda=\"{venda.documento}\" />"
        nfce.retorno_codigo = "100"
        nfce.retorno_mensagem = "Autorizado o uso da NFC-e em ambiente de homologacao."
        nfce.autorizada_em = timezone.now()
        nfce.save()

    def _pedidos_compra_para_aprovar(self, empresa, lojas, fornecedores, produtos, base, financeiro):
        forma = next((f for f in financeiro["formas"] if f.codigo == "CRE2"), financeiro["formas"][0])
        fornecedores_revenda = [f for f in fornecedores if f.categoria == "Revenda"] or fornecedores
        fornecedores_uso = [f for f in fornecedores if f.categoria == "Uso/Consumo"] or fornecedores
        hoje = timezone.localdate()
        for idx, loja in enumerate(lojas, start=1):
            pedido = PedidoCompra.objects.create(
                empresa=empresa,
                tipo="1",
                loja=loja,
                fornecedor=fornecedores_revenda[(idx - 1) % len(fornecedores_revenda)],
                emissao=hoje,
                previsao_entrega=hoje + timedelta(days=7),
                forma_pagamento=forma.codigo,
                status="AB",
                observacoes="Pedido demonstrativo de reposicao para aprovacao.",
            )
            for produto in produtos:
                pack = Pack.objects.filter(empresa=empresa, grade=produto.grade, ativo=True).first()
                if not pack:
                    continue
                preco = TabelaprecoProduto.objects.filter(produto=produto, ativo=True).first().preco
                item = PedidoCompraItem.objects.create(
                    pedido=pedido,
                    produto=produto,
                    cor=base["cores"][(idx - 1) % len(base["cores"])],
                    pack=pack,
                    n_packs=2,
                    preco_unit=money(Decimal(preco) * Decimal("0.30")),
                    desconto_valor=Decimal("0.00"),
                )
                item.recalcular_totais()
                item.save(update_fields=["qtd", "total_item"])
                PedidoCompraEntrega.objects.create(
                    item=item,
                    qtd_prevista=item.qtd,
                    data_prevista=pedido.previsao_entrega,
                    status="PREV",
                )
            pedido.recomputa_totais()
            pedido.save(update_fields=["total_itens", "total_pedido"])
            self._parcelas_pedido(pedido, forma)

        uso_produtos = list(Produto.objects.filter(empresa=empresa, tipo_produto="2").order_by("referencia"))
        pedido_uso = PedidoCompra.objects.create(
            empresa=empresa,
            tipo="2",
            loja=lojas[0],
            fornecedor=fornecedores_uso[0],
            emissao=hoje,
            previsao_entrega=hoje + timedelta(days=5),
            forma_pagamento=forma.codigo,
            status="AB",
            observacoes="Pedido demonstrativo de materiais de uso e consumo para aprovacao.",
        )
        for produto in uso_produtos:
            item = PedidoCompraItem.objects.create(
                pedido=pedido_uso,
                produto=produto,
                descricao_livre=produto.descricao,
                qtd=50,
                preco_unit=Decimal("1.90") if "Etiqueta" in produto.descricao else Decimal("3.50"),
                desconto_valor=Decimal("0.00"),
            )
            item.recalcular_totais()
            item.save(update_fields=["total_item"])
            PedidoCompraEntrega.objects.create(item=item, qtd_prevista=item.qtd, data_prevista=pedido_uso.previsao_entrega, status="PREV")
        pedido_uso.recomputa_totais()
        pedido_uso.save(update_fields=["total_itens", "total_pedido"])
        self._parcelas_pedido(pedido_uso, forma)

    def _parcelas_pedido(self, pedido, forma):
        parcelas = list(FormaPagamentoParcela.objects.filter(forma=forma).order_by("ordem"))
        total = money(pedido.total_pedido)
        restante = total
        for idx, parcela in enumerate(parcelas, start=1):
            if idx < len(parcelas):
                valor = money(total * Decimal(parcela.percentual or 0) / Decimal("100"))
                restante = money(restante - valor)
            else:
                valor = restante
            PedidoCompraParcela.objects.create(
                pedido=pedido,
                parcela_n=idx,
                vencimento=pedido.emissao + timedelta(days=int(parcela.dias or 0)),
                valor=valor,
                percentual=parcela.percentual,
                origem="FORMA",
                status="PLAN",
            )

    def _natureza_venda(self, empresa):
        return (
            Nat_Lancamento.objects
            .filter(empresa=empresa, natureza_operacao="RECEITA", ativo=True)
            .order_by("codigo")
            .first()
        )

    def _natureza_cmv(self, empresa):
        natureza = (
            Nat_Lancamento.objects
            .filter(empresa=empresa, ativo=True, natureza_operacao="DESPESA")
            .filter(Q(codigo="2100") | Q(descricao__icontains="CMV") | Q(descricao__icontains="mercadoria vendida"))
            .order_by("codigo")
            .first()
        )
        if natureza:
            return natureza

        plano = (
            PlanoContabil.objects
            .filter(empresa=empresa, ativa=True, classe=PlanoContabil.CLASSE_CUSTO)
            .filter(Q(codigo="5.1.01") | Q(descricao__icontains="CMV"))
            .order_by("codigo")
            .first()
        )
        return Nat_Lancamento.objects.create(
            empresa=empresa,
            codigo="2100",
            categoria_principal="CUSTOS DAS MERCADORIAS",
            subcategoria="CMV",
            descricao="CMV - Custo da mercadoria vendida",
            tipo="DESPESA",
            status="ATIVO",
            tipo_natureza="DEBITO",
            natureza_operacao="DESPESA",
            categoria_gerencial="CMV",
            movimenta_financeiro=False,
            entra_dre=True,
            plano_contabil=plano,
            conta_contabil=plano.codigo if plano else None,
            ativo=True,
        )

    def _natureza_comissao(self, empresa):
        natureza = (
            Nat_Lancamento.objects
            .filter(empresa=empresa, ativo=True, natureza_operacao="DESPESA")
            .filter(Q(codigo="3103") | Q(descricao__icontains="Comiss"))
            .order_by("codigo")
            .first()
        )
        if natureza:
            return natureza

        plano = (
            PlanoContabil.objects
            .filter(empresa=empresa, ativa=True, classe=PlanoContabil.CLASSE_DESPESA)
            .filter(Q(codigo="6.3.02") | Q(descricao__icontains="Comiss"))
            .order_by("codigo")
            .first()
        )
        return Nat_Lancamento.objects.create(
            empresa=empresa,
            codigo="3103",
            categoria_principal="DESPESAS OPERACIONAIS",
            subcategoria="Vendas",
            descricao="Comissões",
            tipo="DESPESA",
            status="ATIVO",
            tipo_natureza="DEBITO",
            natureza_operacao="DESPESA",
            categoria_gerencial="Despesas com vendas",
            movimenta_financeiro=False,
            entra_dre=True,
            plano_contabil=plano,
            conta_contabil=plano.codigo if plano else None,
            ativo=True,
        )

    def _cmv_venda(self, venda):
        total_cmv = money(sum((Decimal(item.cmv_total or 0) for item in venda.itens.all()), Decimal("0.00")))
        if total_cmv <= 0:
            return
        movimento = MovimentacaoFinanceira.objects.create(
            empresa=venda.empresa,
            idloja=venda.loja,
            data_movimento=timezone.localdate(),
            tipo=MovimentacaoFinanceira.TIPO_SAIDA,
            status=MovimentacaoFinanceira.STATUS_EFETIVA,
            origem=MovimentacaoFinanceira.ORIGEM_CMV,
            valor=total_cmv,
            historico=f"CMV venda PDV {venda.documento}",
            documento=venda.documento,
            Idnatureza=self._natureza_cmv(venda.empresa),
            FormaPagamento="CMV",
        )
        gerar_lancamento_contabil_movimentacao(movimento)

    def _comissao_venda(self, venda):
        percentual = Decimal(getattr(venda.vendedor, "comissao_percentual", 0) or 0)
        if percentual <= 0:
            return
        valor = money(Decimal(venda.total or 0) * percentual / Decimal("100"))
        if valor <= 0:
            return
        movimento = MovimentacaoFinanceira.objects.create(
            empresa=venda.empresa,
            idloja=venda.loja,
            data_movimento=timezone.localdate(),
            tipo=MovimentacaoFinanceira.TIPO_SAIDA,
            status=MovimentacaoFinanceira.STATUS_EFETIVA,
            origem=MovimentacaoFinanceira.ORIGEM_COMISSAO,
            valor=valor,
            historico=f"Comissão venda PDV {venda.documento} - {venda.vendedor.nomefuncionario}",
            documento=venda.documento,
            Idnatureza=self._natureza_comissao(venda.empresa),
            FormaPagamento="COMISSAO",
        )
        gerar_lancamento_contabil_movimentacao(movimento)

    def _proximo_documento_pdv(self):
        numero = (NFCe.objects.aggregate(max_numero=Max("numero")).get("max_numero") or 0) + 1
        while VendaPdv.objects.filter(documento=str(numero)).exists() or NFCe.objects.filter(numero=numero).exists():
            numero += 1
        return str(numero)

    def _chave_nfce(self, nfce):
        cnpj = "".join(ch for ch in str(nfce.venda.loja.cnpj or "") if ch.isdigit()).zfill(14)[-14:]
        aamm = timezone.localdate().strftime("%y%m")
        serie = str(nfce.serie).zfill(3)
        numero = str(nfce.numero).zfill(9)
        codigo = str(90000000 + int(nfce.numero or 0))[-8:]
        base = f"35{aamm}{cnpj}{nfce.modelo}{serie}{numero}1{codigo}"
        return f"{base}{self._digito_chave(base)}"

    def _digito_chave(self, base43):
        pesos = [2, 3, 4, 5, 6, 7, 8, 9]
        total = sum(int(digit) * pesos[idx % len(pesos)] for idx, digit in enumerate(reversed(base43)))
        resto = total % 11
        dv = 11 - resto
        return "0" if dv >= 10 else str(dv)

    def _ajustar_usuarios(self, empresa, lojas):
        User = get_user_model()
        usuarios = User.objects.filter(empresa=empresa)
        loja_principal = lojas[0] if lojas else None
        for usuario in usuarios:
            if not usuario.is_superuser:
                usuario.loja = loja_principal
                usuario.save(update_fields=["loja"])
                usuario.lojas.set(lojas)

    def _dominio(self, empresa):
        nome = (empresa.nome_fantasia or empresa.nome or "sysvar").lower()
        limpo = "".join(ch for ch in nome if ch.isalnum())
        return f"{limpo[:18] or 'sysvar'}.com.br"

    def _cpf(self, seed):
        base = f"{seed:09d}"[-9:]
        d1 = self._cpf_dv(base)
        d2 = self._cpf_dv(base + str(d1))
        return f"{base}{d1}{d2}"

    def _cpf_dv(self, nums):
        soma = sum(int(n) * w for n, w in zip(nums, range(len(nums) + 1, 1, -1)))
        resto = (soma * 10) % 11
        return 0 if resto == 10 else resto

    def _cnpj(self, seed):
        base = f"{seed:08d}0001"[-12:]
        d1 = self._cnpj_dv(base, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
        d2 = self._cnpj_dv(base + str(d1), [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
        return f"{base}{d1}{d2}"

    def _cnpj_dv(self, nums, pesos):
        resto = sum(int(n) * p for n, p in zip(nums, pesos)) % 11
        return 0 if resto < 2 else 11 - resto
