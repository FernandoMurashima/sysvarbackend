from decimal import Decimal
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import UserFieldPermission, UserModulePermission
from cadastros.models import Empresa, Fornecedor, Funcionarios, Loja, Nat_Lancamento, PlanoContabil
from compras.models import PedidoCompra, PedidoCompraEntrega, PedidoCompraItem
from fiscal.models import Cfop, NotaFiscalSaida, NotaFiscalSaidaItem, RegraTributaria, Tributo
from financeiro.models import FormaPagamentoParcela
from produto.models import (
    Colecao,
    ConfigEan,
    Cor,
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

from .refazer_base_demo_moda import Command as BaseCommand
from fiscal.models.venda_pdv import money


class Command(BaseCommand):
    help = "Refaz a base realista de moda com duas empresas ativas e cadastros operacionais completos."

    EMPRESAS = [
        {
            "nome": "Maison Aurora Comercio de Moda Ltda",
            "fantasia": "Maison Aurora",
            "seed": 71001,
            "dominio": "maisonaurora.com.br",
            "prefixo": "aurora",
        },
        {
            "nome": "Bella Vista Fashion Comercio Ltda",
            "fantasia": "Bella Vista Fashion",
            "seed": 72001,
            "dominio": "bellavistafashion.com.br",
            "prefixo": "bella",
        },
    ]

    @transaction.atomic
    def handle(self, *args, **options):
        if not options["confirmar"]:
            raise CommandError("Use --confirmar para apagar os dados operacionais e refazer a base realista.")

        self._desligar_auditoria()
        try:
            empresas = self._preparar_empresas()
            self._limpar_operacional(empresas)
            for idx, empresa in enumerate(empresas, start=1):
                contexto = self._criar_empresa_demo(empresa, idx)
                self._usuarios(empresa, contexto["lojas"], idx)
            self.stdout.write(self.style.SUCCESS("Base realista de moda recriada com sucesso."))
        finally:
            self._religar_auditoria()

    def _preparar_empresas(self):
        empresas = []
        documentos = []
        for item in self.EMPRESAS:
            documento = self._cnpj(item["seed"])
            documentos.append(documento)
            empresa, _ = Empresa.objects.get_or_create(
                documento=documento,
                defaults={
                    "nome": item["nome"],
                    "nome_fantasia": item["fantasia"],
                    "ativo": True,
                },
            )
            empresa.nome = item["nome"]
            empresa.nome_fantasia = item["fantasia"]
            empresa.documento = documento
            empresa.ativo = True
            empresa.licenca_master = True
            empresa.regime_tributario = Empresa.REGIME_LUCRO_REAL
            empresa.ambiente_fiscal = Empresa.AMBIENTE_HOMOLOGACAO
            empresa.uf_fiscal = "SP"
            empresa.inscricao_estadual = f"110{item['seed']}112"
            empresa.serie_nfce = 1
            empresa.proximo_numero_nfce = 1
            empresa.serie_nfe = 1
            empresa.proximo_numero_nfe = 1
            empresa.save()
            empresas.append(empresa)
        Empresa.objects.exclude(documento__in=documentos).update(ativo=False)
        return Empresa.objects.filter(documento__in=documentos).order_by("documento")

    def _limpar_operacional(self, empresas):
        empresa_ids = list(empresas.values_list("id", flat=True))
        NotaFiscalSaidaItem.objects.filter(nota__empresa_id__in=empresa_ids).delete()
        NotaFiscalSaida.objects.filter(empresa_id__in=empresa_ids).delete()
        RegraTributaria.objects.filter(empresa_id__in=empresa_ids).delete()
        Tributo.objects.filter(empresa_id__in=empresa_ids).delete()
        Cfop.objects.filter(empresa_id__in=empresa_ids).delete()
        super()._limpar_operacional(empresas)
        Nat_Lancamento.objects.filter(empresa_id__in=empresa_ids).delete()
        PlanoContabil.objects.filter(empresa_id__in=empresa_ids).delete()
        User = get_user_model()
        UserModulePermission.objects.filter(user__empresa_id__in=empresa_ids).delete()
        UserFieldPermission.objects.filter(user__empresa_id__in=empresa_ids).delete()
        User.objects.filter(empresa_id__in=empresa_ids, is_superuser=False).delete()
        User.objects.filter(username__startswith="aurora.", is_superuser=False).delete()
        User.objects.filter(username__startswith="bella.", is_superuser=False).delete()

    def _criar_empresa_demo(self, empresa, idx):
        self._atualizar_empresa(empresa, idx)
        self._plano_naturezas(empresa)
        self._fiscal(empresa)
        lojas = self._lojas(empresa, idx)
        clientes = self._clientes(empresa, idx)
        fornecedores = self._fornecedores(empresa, idx)
        funcionarios = self._funcionarios(empresa, lojas, idx)
        financeiro = self._financeiro(empresa, lojas, idx)
        base = self._base_produtos(empresa, idx)
        produtos = self._produtos(base)
        self._estoque(lojas, produtos["vendaveis"])
        self._pedidos_compra_para_aprovar(empresa, lojas, fornecedores, produtos, base, financeiro)
        return {"lojas": lojas, "clientes": clientes, "fornecedores": fornecedores, "funcionarios": funcionarios, "financeiro": financeiro}

    def _atualizar_empresa(self, empresa, idx):
        dados = self.EMPRESAS[idx - 1]
        empresa.nome = dados["nome"]
        empresa.nome_fantasia = dados["fantasia"]
        empresa.documento = self._cnpj(dados["seed"])
        empresa.ativo = True
        empresa.licenca_master = True
        empresa.regime_tributario = Empresa.REGIME_LUCRO_REAL
        empresa.ambiente_fiscal = Empresa.AMBIENTE_HOMOLOGACAO
        empresa.uf_fiscal = "SP"
        empresa.save()

    def _lojas(self, empresa, idx):
        raiz = self.EMPRESAS[idx - 1]["fantasia"]
        dominio = self.EMPRESAS[idx - 1]["dominio"]
        dados = [
            (f"{raiz} Matriz", "MATRIZ", Loja.TIPO_MATRIZ, "Rua Haddock Lobo", "1540", "Jardins"),
            (f"{raiz} Shopping", "SHOPPING", Loja.TIPO_LOJA, "Avenida Roque Petroni Junior", "1089", "Morumbi"),
            (f"{raiz} Fabrica", "FABRICA", Loja.TIPO_FABRICA, "Rua do Curtume", "860", "Lapa"),
        ]
        lojas = []
        for pos, (nome, apelido, tipo, endereco, numero, bairro) in enumerate(dados, start=1):
            lojas.append(Loja.objects.create(
                empresa=empresa,
                nome_loja=nome,
                apelido_loja=apelido,
                cnpj=self._cnpj(idx * 9000 + pos),
                logradouro="Avenida" if pos == 2 else "Rua",
                endereco=endereco,
                numero=numero,
                complemento="",
                cep=f"01{idx}{pos}2-000",
                bairro=bairro,
                cidade="Sao Paulo",
                estado="SP",
                telefone1=f"(11)302{idx}-{pos}000",
                email=f"{apelido.lower()}@{dominio}",
                EstoqueNegativo="NAO",
                Rede="SIM",
                Matriz="SIM" if tipo == Loja.TIPO_MATRIZ else "NAO",
                tipo_unidade=tipo,
                regime_tributario=Empresa.REGIME_LUCRO_REAL,
                ambiente_fiscal=Empresa.AMBIENTE_HOMOLOGACAO,
                inscricao_estadual=f"110{idx}{pos}00112",
                ativo=True,
                DataAbertura=timezone.localdate().replace(month=1, day=10),
            ))
        return lojas

    def _fornecedores(self, empresa, idx):
        dados = [
            ("Textil Serena Ltda", "SERENA", "MATERIA_PRIMA"),
            ("Tecidos Santa Clara Ltda", "SANTA CLARA", "MATERIA_PRIMA"),
            ("Aviamentos Primor Ltda", "PRIMOR", "AVIAMENTO"),
            ("Confecções Dalia Ltda", "DALIA", "REVENDA"),
            ("Atelie Ponto Fino Ltda", "PONTO FINO", "FACCAO"),
            ("Costura Bella Forma Ltda", "BELLA FORMA", "FACCAO"),
            ("Embalagens Nova Caixa Ltda", "NOVA CAIXA", "OUTROS"),
        ]
        fornecedores = []
        for pos, (nome, apelido, categoria) in enumerate(dados, start=1):
            fornecedores.append(Fornecedor.objects.create(
                empresa=empresa,
                nome_fornecedor=nome,
                apelido=apelido,
                cnpj=self._cnpj(idx * 12000 + pos),
                logradouro="Rua",
                endereco=f"Rua Industrial {120 + pos}",
                numero=str(300 + pos),
                cep=f"06{idx}{pos}0-000",
                bairro="Centro Industrial",
                cidade="Sao Paulo",
                estado="SP",
                telefone1=f"(11)33{idx}{pos}-4000",
                email=f"comercial{pos}@fornecedor.com.br",
                categoria=categoria,
                ativo=True,
            ))
        return fornecedores

    def _funcionarios(self, empresa, lojas, idx):
        nomes_por_loja = [
            ("Marina Duarte", "Leandro Castro", "Sofia Almeida", "Rafael Monteiro"),
            ("Helena Rocha", "Marcelo Prado", "Bianca Ferreira", "Thiago Nunes"),
            ("Priscila Teixeira", "Eduardo Ramos", "Luiza Campos", "Gustavo Lima"),
        ]
        funcionarios = []
        seq = 1
        for loja, nomes in zip(lojas, nomes_por_loja):
            cargos = [
                (nomes[0], "Gerente", Decimal("0.00"), Decimal("6200.00")),
                (nomes[1], "Caixa", Decimal("0.00"), Decimal("3200.00")),
                (nomes[2], "Vendedor", Decimal("4.00"), Decimal("2400.00")),
                (nomes[3], "Vendedor", Decimal("4.00"), Decimal("2400.00")),
            ]
            for nome, categoria, comissao, salario in cargos:
                funcionarios.append(Funcionarios.objects.create(
                    empresa=empresa,
                    nomefuncionario=nome,
                    apelido=nome.split()[0].upper(),
                    cpf=self._cpf(idx * 3000 + seq),
                    inicio=timezone.localdate().replace(month=1, day=2),
                    categoria=categoria,
                    meta=Decimal("45000.00") if categoria == "Vendedor" else Decimal("0.00"),
                    comissao_percentual=comissao,
                    salario=salario,
                    idloja=loja,
                    ativo=True,
                ))
                seq += 1
        return funcionarios

    def _base_produtos(self, empresa, idx):
        ncm_vestuario = Ncm.objects.create(empresa=empresa, ncm="6204.62.00", descricao="Vestuário feminino", aliquota=Decimal("18.00"))
        ncm_tecido = Ncm.objects.create(empresa=empresa, ncm="5407.52.10", descricao="Tecidos de filamentos sinteticos", aliquota=Decimal("18.00"))
        ncm_aviamento = Ncm.objects.create(empresa=empresa, ncm="9606.21.00", descricao="Botoes e aviamentos", aliquota=Decimal("18.00"))
        unidade = Unidade.objects.create(empresa=empresa, Descricao="Unidade", Codigo="UN", permite_decimal=False)
        metro = Unidade.objects.create(empresa=empresa, Descricao="Metro", Codigo="M", permite_decimal=True)
        materiais = {
            "Jeans": Material.objects.create(empresa=empresa, Descricao="Jeans", Codigo="JEANS", Status="ATIVO"),
            "Viscose": Material.objects.create(empresa=empresa, Descricao="Viscose", Codigo="VISCOSE", Status="ATIVO"),
            "Crepe": Material.objects.create(empresa=empresa, Descricao="Crepe", Codigo="CREPE", Status="ATIVO"),
            "Renda": Material.objects.create(empresa=empresa, Descricao="Renda", Codigo="RENDA", Status="ATIVO"),
            "Malha": Material.objects.create(empresa=empresa, Descricao="Malha", Codigo="MALHA", Status="ATIVO"),
        }
        grade_num = Grade.objects.create(empresa=empresa, Descricao="Grade numerica 38 a 48", Status="ATIVO")
        grade_alpha = Grade.objects.create(empresa=empresa, Descricao="Grade moda PP a GG", Status="ATIVO")
        tamanhos_num = [Tamanho.objects.create(empresa=empresa, idgrade=grade_num, Tamanho=tam, Descricao=tam, Status="ATIVO") for tam in ["38", "40", "42", "44", "46", "48"]]
        tamanhos_alpha = [Tamanho.objects.create(empresa=empresa, idgrade=grade_alpha, Tamanho=tam, Descricao=tam, Status="ATIVO") for tam in ["PP", "P", "M", "G", "GG"]]
        cores = [
            Cor.objects.create(empresa=empresa, Codigo=cod, Descricao=nome, Cor=nome, Status="ATIVO")
            for cod, nome in [("BR", "Branca"), ("PR", "Preta"), ("VM", "Vermelho"), ("AZ", "Azul")]
        ]
        colecao = Colecao.objects.create(empresa=empresa, Codigo="26", Estacao="01", Descricao="Verao 2026", Status="AT")
        tabela = Tabelapreco.objects.create(empresa=empresa, NomeTabela="Tabela varejo", DataInicio=timezone.localdate(), Promocao=False)
        ConfigEan.objects.create(empresa=empresa, country_prefix="789", company_prefix=f"7{idx:03d}", next_itemref=1, ativo=True)
        grupos = {}
        for codigo, cod_ref, descricao, subs in [
            ("CALCA", "01", "Calca", ["Jeans", "Alfaiataria"]),
            ("SAIA", "02", "Saia", ["Midi", "Envelope"]),
            ("BLUSA", "03", "Blusa", ["Lisa", "Regata"]),
            ("VEST", "04", "Vestido", ["Renda", "Midi"]),
        ]:
            grupo = Grupo.objects.create(empresa=empresa, Codigo=codigo, CodigoRef=cod_ref, Descricao=descricao, Margem=Decimal("55.00"))
            grupos[descricao] = {"grupo": grupo, "subgrupos": {}}
            for sub in subs:
                grupos[descricao]["subgrupos"][sub] = Subgrupo.objects.create(empresa=empresa, Idgrupo=grupo, Descricao=sub, Margem=Decimal("55.00"))
        pack_num = Pack.objects.create(empresa=empresa, grade=grade_num, nome="Pack numerico 38-48", ativo=True)
        for tamanho in tamanhos_num:
            PackItem.objects.create(pack=pack_num, tamanho=tamanho, qtd=1)
        pack_alpha = Pack.objects.create(empresa=empresa, grade=grade_alpha, nome="Pack moda PP-GG", ativo=True)
        for tamanho in tamanhos_alpha:
            PackItem.objects.create(pack=pack_alpha, tamanho=tamanho, qtd=1)
        return {
            "ncm_vestuario": ncm_vestuario.ncm,
            "ncm_tecido": ncm_tecido.ncm,
            "ncm_aviamento": ncm_aviamento.ncm,
            "unidade": unidade,
            "metro": metro,
            "materiais": materiais,
            "grade_num": grade_num,
            "grade_alpha": grade_alpha,
            "tamanhos_num": tamanhos_num,
            "tamanhos_alpha": tamanhos_alpha,
            "cores": cores,
            "colecao": colecao,
            "tabela": tabela,
            "grupos": grupos,
            "pack_num": pack_num,
            "pack_alpha": pack_alpha,
        }

    def _produtos(self, base):
        vendaveis = []
        revenda_specs = [
            ("Calca", "Jeans", "Calca Jeans Reta Escura", Decimal("329.90"), "num", "Jeans"),
            ("Saia", "Midi", "Saia Midi Alfaiataria", Decimal("249.90"), "alpha", "Crepe"),
            ("Blusa", "Lisa", "Blusa Basica Lisa", Decimal("169.90"), "alpha", "Malha"),
            ("Vestido", "Renda", "Vestido de Renda Midi", Decimal("459.90"), "alpha", "Renda"),
        ]
        proprios_specs = [
            ("Calca", "Alfaiataria", "Calca Alfaiataria Propria", Decimal("389.90"), "num", "Crepe"),
            ("Saia", "Envelope", "Saia Envelope Propria", Decimal("279.90"), "alpha", "Viscose"),
            ("Blusa", "Regata", "Blusa Regata Propria", Decimal("189.90"), "alpha", "Viscose"),
            ("Vestido", "Midi", "Vestido Midi Proprio", Decimal("499.90"), "alpha", "Crepe"),
        ]
        for tipo, specs in [("1", revenda_specs), ("3", proprios_specs)]:
            for grupo_nome, sub_nome, descricao, preco, grade_tipo, material_nome in specs:
                vendaveis.append(self._produto_vendavel(base, tipo, grupo_nome, sub_nome, descricao, preco, grade_tipo, material_nome))
        materiais = []
        for descricao, referencia, unidade, ncm, custo in [
            ("Tecido Crepe Off White", "INS-TEC-001", base["metro"], base["ncm_tecido"], Decimal("24.90")),
            ("Tecido Viscose Estampada", "INS-TEC-002", base["metro"], base["ncm_tecido"], Decimal("19.90")),
            ("Renda Guipir Vermelha", "INS-REN-001", base["metro"], base["ncm_tecido"], Decimal("34.50")),
            ("Ziper Invisivel Azul", "AVI-ZIP-001", base["unidade"], base["ncm_aviamento"], Decimal("2.30")),
            ("Botao Perolado Branco", "AVI-BOT-001", base["unidade"], base["ncm_aviamento"], Decimal("0.55")),
            ("Linha de Costura Preta", "AVI-LIN-001", base["unidade"], base["ncm_aviamento"], Decimal("6.90")),
            ("Sacola Kraft Personalizada", "UC-SAC-001", base["unidade"], base["ncm_aviamento"], Decimal("1.20")),
        ]:
            tipo = "4" if referencia.startswith(("INS", "AVI")) else "2"
            materiais.append(Produto.objects.create(
                empresa=base["colecao"].empresa,
                tipo_produto=tipo,
                referencia=referencia,
                descricao=descricao,
                descricao_reduzida=descricao[:60],
                unidade=unidade,
                material=base["materiais"].get("Crepe"),
                ncm=ncm,
                custo_original=custo,
                custo_ultima_compra=custo,
                custo_medio=custo,
                ativo=True,
            ))
        return {"vendaveis": vendaveis, "materiais": materiais}

    def _produto_vendavel(self, base, tipo, grupo_nome, sub_nome, descricao, preco, grade_tipo, material_nome):
        grupo = base["grupos"][grupo_nome]["grupo"]
        subgrupo = base["grupos"][grupo_nome]["subgrupos"][sub_nome]
        grade = base["grade_num"] if grade_tipo == "num" else base["grade_alpha"]
        tamanhos = base["tamanhos_num"] if grade_tipo == "num" else base["tamanhos_alpha"]
        produto = Produto.objects.create(
            empresa=grupo.empresa,
            tipo_produto=tipo,
            descricao=descricao,
            descricao_reduzida=descricao[:60],
            unidade=base["unidade"],
            grupo=grupo,
            subgrupo=subgrupo,
            colecao=base["colecao"],
            material=base["materiais"][material_nome],
            grade=grade,
            ncm=base["ncm_vestuario"],
            origem_mercadoria=0,
            csosn_ou_cst_icms="000",
            aliquota_icms=Decimal("18.00"),
            cfop_venda_dentro="5102",
            cfop_venda_fora="6102",
            cst_pis="01",
            cst_cofins="01",
            aliq_pis=Decimal("1.65"),
            aliq_cofins=Decimal("7.60"),
            ativo=True,
        )
        TabelaprecoProduto.objects.create(produto=produto, tabela=base["tabela"], preco=preco, DataInicio=timezone.localdate(), ativo=True)
        custo = money(preco * Decimal("0.30"))
        for cor in base["cores"]:
            for tamanho in tamanhos:
                ProdutoDetalhe.objects.create(produto=produto, idcor=cor, idtamanho=tamanho, custo_original=custo, custo_ultima_compra=custo, custo_medio=custo)
        return produto

    def _pedidos_compra_para_aprovar(self, empresa, lojas, fornecedores, produtos, base, financeiro):
        forma = next((f for f in financeiro["formas"] if f.codigo == "CRE2"), financeiro["formas"][0])
        hoje = timezone.localdate()
        loja = next((l for l in lojas if l.tipo_unidade == Loja.TIPO_FABRICA), lojas[0])
        revenda_fornecedor = next((f for f in fornecedores if f.categoria == "REVENDA"), fornecedores[0])
        materia_fornecedor = next((f for f in fornecedores if f.categoria == "MATERIA_PRIMA"), fornecedores[0])
        aviamento_fornecedor = next((f for f in fornecedores if f.categoria == "AVIAMENTO"), fornecedores[0])
        self._pedido_revenda(empresa, lojas[0], revenda_fornecedor, produtos["vendaveis"][:4], base, forma, hoje)
        self._pedido_materiais(empresa, loja, materia_fornecedor, [p for p in produtos["materiais"] if p.referencia.startswith("INS")], forma, hoje, "Pedido de tecidos para producao.")
        self._pedido_materiais(empresa, loja, aviamento_fornecedor, [p for p in produtos["materiais"] if p.referencia.startswith("AVI")], forma, hoje, "Pedido de aviamentos para producao.")

    def _pedido_revenda(self, empresa, loja, fornecedor, produtos, base, forma, hoje):
        pedido = PedidoCompra.objects.create(
            empresa=empresa,
            tipo="1",
            loja=loja,
            fornecedor=fornecedor,
            emissao=hoje,
            previsao_entrega=hoje + timedelta(days=7),
            forma_pagamento=forma.codigo,
            status="AB",
            observacoes="Pedido realista de reposicao de produtos de revenda.",
        )
        for produto in produtos:
            pack = base["pack_num"] if produto.grade_id == base["grade_num"].pk else base["pack_alpha"]
            preco = TabelaprecoProduto.objects.filter(produto=produto, ativo=True).first().preco
            item = PedidoCompraItem.objects.create(
                pedido=pedido,
                produto=produto,
                cor=base["cores"][0],
                pack=pack,
                n_packs=1,
                preco_unit=money(Decimal(preco) * Decimal("0.30")),
                desconto_valor=Decimal("0.00"),
            )
            item.recalcular_totais()
            item.save(update_fields=["qtd", "total_item"])
            PedidoCompraEntrega.objects.create(item=item, qtd_prevista=item.qtd, data_prevista=pedido.previsao_entrega, status="PREV")
        pedido.recomputa_totais()
        pedido.save(update_fields=["total_itens", "total_pedido"])
        self._parcelas_pedido(pedido, forma)

    def _pedido_materiais(self, empresa, loja, fornecedor, produtos, forma, hoje, observacao):
        pedido = PedidoCompra.objects.create(
            empresa=empresa,
            tipo="2",
            loja=loja,
            fornecedor=fornecedor,
            emissao=hoje,
            previsao_entrega=hoje + timedelta(days=5),
            forma_pagamento=forma.codigo,
            status="AB",
            observacoes=observacao,
        )
        for produto in produtos:
            item = PedidoCompraItem.objects.create(
                pedido=pedido,
                produto=produto,
                descricao_livre=produto.descricao,
                qtd=Decimal("120.000") if produto.unidade.Codigo == "M" else Decimal("500.000"),
                preco_unit=money(produto.custo_original or 1),
                desconto_valor=Decimal("0.00"),
            )
            item.recalcular_totais()
            item.save(update_fields=["total_item"])
            PedidoCompraEntrega.objects.create(item=item, qtd_prevista=item.qtd, data_prevista=pedido.previsao_entrega, status="PREV")
        pedido.recomputa_totais()
        pedido.save(update_fields=["total_itens", "total_pedido"])
        self._parcelas_pedido(pedido, forma)

    def _usuarios(self, empresa, lojas, idx):
        User = get_user_model()
        prefixo = self.EMPRESAS[idx - 1]["prefixo"]
        admin = User.objects.create_user(
            username=f"{prefixo}.admin",
            email=f"admin@{self.EMPRESAS[idx - 1]['dominio']}",
            password="12345678",
            first_name="Administrador",
            last_name=self.EMPRESAS[idx - 1]["fantasia"],
            type="Admin",
            empresa=empresa,
            loja=None,
            is_staff=True,
        )
        admin.lojas.set(lojas)
        self._permissoes_usuario(admin, "EDIT", custos=True)
        seq = 1
        for loja in lojas:
            funcionarios = Funcionarios.objects.filter(empresa=empresa, idloja=loja).order_by("id")
            for func in funcionarios:
                tipo = {"Gerente": "Gerente", "Caixa": "Caixa", "Vendedor": "Vendedor"}.get(func.categoria, "Regular")
                username = f"{prefixo}.{loja.apelido_loja.lower()}.{seq}"
                user = User.objects.create_user(
                    username=username,
                    email=f"{username}@{self.EMPRESAS[idx - 1]['dominio']}",
                    password="12345678",
                    first_name=func.nomefuncionario.split()[0],
                    last_name=" ".join(func.nomefuncionario.split()[1:]),
                    type=tipo,
                    empresa=empresa,
                    loja=loja,
                )
                user.lojas.set([loja])
                acesso = "EDIT" if tipo in ("Gerente", "Caixa") else "VIEW"
                self._permissoes_usuario(user, acesso, custos=False)
                seq += 1

    def _permissoes_usuario(self, user, acesso, custos=False):
        for modulo, _ in UserModulePermission.Module.choices:
            UserModulePermission.objects.create(user=user, modulo=modulo, acesso=acesso)
        UserFieldPermission.objects.create(user=user, campo="produto.custo", pode_ver=custos)
        UserFieldPermission.objects.create(user=user, campo="funcionario.salario", pode_ver=custos)

    def _plano_naturezas(self, empresa):
        contas = [
            ("1.1.01.001", "Caixa Loja Matriz", PlanoContabil.CLASSE_ATIVO, PlanoContabil.NATUREZA_DEBITO),
            ("1.1.01.002", "Caixa Loja Filial", PlanoContabil.CLASSE_ATIVO, PlanoContabil.NATUREZA_DEBITO),
            ("1.1.01.003", "Caixa Fabrica", PlanoContabil.CLASSE_ATIVO, PlanoContabil.NATUREZA_DEBITO),
            ("1.1.01.004", "Caixa Master", PlanoContabil.CLASSE_ATIVO, PlanoContabil.NATUREZA_DEBITO),
            ("1.1.02.001", "Conta Corrente Matriz", PlanoContabil.CLASSE_ATIVO, PlanoContabil.NATUREZA_DEBITO),
            ("1.1.02.002", "Conta Corrente Filial", PlanoContabil.CLASSE_ATIVO, PlanoContabil.NATUREZA_DEBITO),
            ("1.1.02.003", "Conta Corrente Fabrica", PlanoContabil.CLASSE_ATIVO, PlanoContabil.NATUREZA_DEBITO),
            ("1.1.03", "Clientes", PlanoContabil.CLASSE_ATIVO, PlanoContabil.NATUREZA_DEBITO),
            ("2.1.01", "Fornecedores", PlanoContabil.CLASSE_PASSIVO, PlanoContabil.NATUREZA_CREDITO),
            ("3.1.01", "Capital Social", PlanoContabil.CLASSE_PATRIMONIO, PlanoContabil.NATUREZA_CREDITO),
            ("4.1.01", "Receita de Venda de Mercadorias", PlanoContabil.CLASSE_RECEITA, PlanoContabil.NATUREZA_CREDITO),
            ("4.1.02", "Receita de Venda de Produtos Proprios", PlanoContabil.CLASSE_RECEITA, PlanoContabil.NATUREZA_CREDITO),
            ("5.1.01", "CMV", PlanoContabil.CLASSE_CUSTO, PlanoContabil.NATUREZA_DEBITO),
            ("6.1.01", "Aluguel", PlanoContabil.CLASSE_DESPESA, PlanoContabil.NATUREZA_DEBITO),
            ("6.2.01", "Comissoes sobre vendas", PlanoContabil.CLASSE_DESPESA, PlanoContabil.NATUREZA_DEBITO),
            ("6.3.01", "Taxas e tarifas bancarias", PlanoContabil.CLASSE_DESPESA, PlanoContabil.NATUREZA_DEBITO),
        ]
        criadas = {}
        for codigo, descricao, classe, natureza in contas:
            criadas[codigo] = PlanoContabil.objects.create(
                empresa=empresa,
                codigo=codigo,
                descricao=descricao,
                classe=classe,
                natureza=natureza,
                nivel=codigo.count(".") + 1,
                analitica=True,
                ativa=True,
            )
        naturezas = [
            ("1.01", "Vendas", "Mercadorias", "Receita de venda de mercadorias", "RECEITA", "CREDITO", "RECEITA", "Vendas", criadas["4.1.01"]),
            ("1.02", "Vendas", "Produtos proprios", "Receita de venda de produtos proprios", "RECEITA", "CREDITO", "RECEITA", "Vendas", criadas["4.1.02"]),
            ("2.01", "Devolucoes", "Vendas", "Devolucao de venda de mercadorias", "DESPESA", "DEBITO", "DESPESA", "Deducoes", criadas["4.1.01"]),
            ("2.10", "Custos", "CMV", "CMV - Custo da mercadoria vendida", "DESPESA", "DEBITO", "DESPESA", "CMV", criadas["5.1.01"]),
            ("3.01", "Despesas", "Aluguel", "Aluguel de loja", "DESPESA", "DEBITO", "DESPESA", "Administrativas", criadas["6.1.01"]),
            ("3.02", "Despesas", "Comissoes", "Comissoes sobre vendas", "DESPESA", "DEBITO", "DESPESA", "Despesas com vendas", criadas["6.2.01"]),
            ("3.03", "Despesas", "Tarifas", "Taxas e tarifas bancarias", "DESPESA", "DEBITO", "DESPESA", "Financeiras", criadas["6.3.01"]),
        ]
        for codigo, categoria, sub, descricao, tipo, tipo_nat, operacao, gerencial, conta in naturezas:
            Nat_Lancamento.objects.create(
                empresa=empresa,
                codigo=codigo,
                categoria_principal=categoria,
                subcategoria=sub,
                descricao=descricao,
                tipo=tipo,
                status="ATIVO",
                tipo_natureza=tipo_nat,
                natureza_operacao=operacao,
                categoria_gerencial=gerencial,
                movimenta_financeiro=True,
                entra_dre=True,
                plano_contabil=conta,
                conta_contabil=conta.codigo,
                ativo=True,
            )

    def _fiscal(self, empresa):
        cfops = [
            ("5102", "Venda de mercadoria adquirida de terceiros", Cfop.TIPO_VENDA),
            ("5101", "Venda de producao do estabelecimento", Cfop.TIPO_VENDA),
            ("5152", "Transferencia de mercadoria", Cfop.TIPO_TRANSFERENCIA),
            ("1102", "Compra para comercializacao", Cfop.TIPO_COMPRA),
            ("1124", "Industrializacao efetuada por outra empresa", Cfop.TIPO_COMPRA),
            ("1556", "Compra de material de uso ou consumo", Cfop.TIPO_COMPRA),
        ]
        cfops_criados = []
        for codigo, descricao, tipo in cfops:
            cfops_criados.append(Cfop.objects.create(
                empresa=empresa,
                codigo=codigo,
                descricao=descricao,
                tipo_operacao=tipo,
                destino=Cfop.DESTINO_DENTRO,
                movimenta_estoque=True,
                gera_financeiro=tipo == Cfop.TIPO_COMPRA,
                ativo=True,
            ))
        tributos = [
            ("ICMS", "ICMS", Tributo.ESFERA_ESTADUAL, Decimal("18.0000")),
            ("PIS", "PIS", Tributo.ESFERA_FEDERAL, Decimal("1.6500")),
            ("COFINS", "COFINS", Tributo.ESFERA_FEDERAL, Decimal("7.6000")),
            ("CSLL", "CSLL", Tributo.ESFERA_FEDERAL, Decimal("1.0800")),
        ]
        for codigo, descricao, esfera, aliquota in tributos:
            tributo = Tributo.objects.create(empresa=empresa, codigo=codigo, descricao=descricao, esfera=esfera, ativo=True, atual=True)
            for cfop in cfops_criados:
                RegraTributaria.objects.create(
                    empresa=empresa,
                    nome=f"{codigo} {cfop.codigo}",
                    tributo=tributo,
                    cfop=cfop,
                    tipo_operacao="VENDA" if cfop.tipo_operacao == Cfop.TIPO_VENDA else "COMPRA",
                    regime_tributario=RegraTributaria.REGIME_LUCRO_REAL,
                    tipo_produto=RegraTributaria.TIPO_PRODUTO_TODOS,
                    uf_origem="SP",
                    uf_destino="SP",
                    cst_csosn="00" if codigo == "ICMS" else "01",
                    aliquota=aliquota,
                    permite_credito=cfop.tipo_operacao == Cfop.TIPO_COMPRA,
                    compoe_custo=cfop.tipo_operacao == Cfop.TIPO_COMPRA,
                    entra_dre=True,
                    ativo=True,
                )
