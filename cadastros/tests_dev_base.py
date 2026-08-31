from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from accounts.models import SessaoUsuario
from cadastros.models import CentroCusto, Cliente, Empresa, EmpresaContrato, Fornecedor, Funcionarios, Loja, Nat_Lancamento, PlanoContabil
from compras.models import Cotacao, CotacaoFornecedor, PedidoCompra, Requisicao, RequisicaoSetor
from financeiro.models import Caixa, ContaBancaria, FormaPagamento, PrazoPagamento, ValeTroca, ValeTrocaMovimento
from fiscal.models.nota_fiscal_entrada import NotaFiscalEntrada
from fiscal.models.nota_fiscal_saida import NotaFiscalSaida
from fiscal.models.venda_pdv import VendaDevolucao, VendaPdv, VendaPdvItem
from produto.models import Estoque, EstoqueMovimentacao, FichaTecnica, Grade, Pack, Produto, ProdutoDetalhe, Unidade
from sysvar_devtools.dev_base import DEV_COMPANY_DOCUMENT, DEV_PASSWORD, DEV_USERS, PRESERVED_SUPERUSER, SysvarDevBaseService


class SysvarDevBaseTests(TransactionTestCase):
    def setUp(self):
        self.User = get_user_model()
        self.takeshi = self.User.objects.create_superuser(
            username=PRESERVED_SUPERUSER,
            email="takeshi@sysvar.test",
            password="SenhaOriginal@123",
            first_name="Takeshi",
            last_name="Sysvar",
        )

    def test_reset_bloqueia_ambiente_producao(self):
        service = SysvarDevBaseService()
        with override_settings(DEBUG=False, DATABASES={"default": {"ENGINE": "django.db.backends.mysql", "NAME": "sysvar_prod"}}):
            with self.assertRaises(CommandError):
                service.assert_not_production(destructive=True)

    def test_create_validate_and_expected_counts(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        call_command("sysvar_dev_base", "--create", verbosity=0)
        report = SysvarDevBaseService().validate()
        self.assertTrue(report.valid, report.problems)

        empresa = Empresa.objects.get(documento=DEV_COMPANY_DOCUMENT)
        self.assertEqual(Loja.objects.filter(empresa=empresa, tipo_unidade=Loja.TIPO_LOJA).count(), 3)
        self.assertEqual(Loja.objects.filter(empresa=empresa, tipo_unidade=Loja.TIPO_FABRICA).count(), 1)
        self.assertEqual(Produto.objects.filter(empresa=empresa, tipo_produto="1").count(), 100)
        self.assertEqual(Produto.objects.filter(empresa=empresa, tipo_produto="3").count(), 100)
        self.assertEqual(FichaTecnica.objects.filter(empresa=empresa).count(), 100)
        self.assertEqual(Fornecedor.objects.filter(empresa=empresa).count(), 40)
        self.assertEqual(Cliente.objects.filter(empresa=empresa).count(), 11)
        self.assertEqual(FormaPagamento.objects.filter(empresa=empresa).count(), 5)
        self.assertEqual(PrazoPagamento.objects.filter(empresa=empresa).count(), 12)
        self.assertEqual(Grade.objects.filter(empresa=empresa).count(), 3)
        self.assertEqual(Pack.objects.filter(empresa=empresa).count(), 3)

    def test_rebuild_recria_takeshi_remove_residuos_e_nao_gera_protectederror(self):
        old = self._criar_massa_antiga()
        call_command("sysvar_dev_base", "--rebuild", verbosity=0)
        self.assertTrue(SysvarDevBaseService().validate().valid)

        takeshi = self.User.objects.get(username=PRESERVED_SUPERUSER)
        self.assertEqual(takeshi.email, "takeshi@sysvar.test")
        self.assertTrue(takeshi.is_superuser)
        self.assertTrue(takeshi.is_staff)
        self.assertTrue(takeshi.is_active)
        self.assertIsNone(takeshi.empresa_id)
        self.assertTrue(takeshi.check_password(DEV_PASSWORD))
        self.assertFalse(self.User.objects.filter(username__in=["dbg", "fernando", "usuario.antigo"]).exists())
        self.assertFalse(Empresa.objects.filter(pk=old["empresa_id"]).exists())
        self.assertFalse(EmpresaContrato.objects.filter(empresa_id=old["empresa_id"]).exists())
        self.assertFalse(SessaoUsuario.objects.filter(usuario_id=old["user_id"]).exists())
        self.assertFalse(Funcionarios.objects.filter(pk=old["funcionario_id"]).exists())
        for model in [PedidoCompra, Requisicao, Cotacao, NotaFiscalEntrada, NotaFiscalSaida, VendaPdv, VendaDevolucao, ValeTroca]:
            self.assertFalse(model.objects.exists(), model.__name__)

    def test_rebuild_idempotente_e_sem_movimentos_operacionais(self):
        call_command("sysvar_dev_base", "--rebuild", verbosity=0)
        first = SysvarDevBaseService().validate().created
        call_command("sysvar_dev_base", "--rebuild", verbosity=0)
        second = SysvarDevBaseService().validate().created
        self.assertEqual(first, second)

        empresa = Empresa.objects.get(documento=DEV_COMPANY_DOCUMENT)
        for model in [PedidoCompra, Requisicao, Cotacao, VendaPdv, VendaDevolucao, NotaFiscalEntrada, NotaFiscalSaida]:
            self.assertFalse(model.objects.filter(empresa=empresa).exists(), model.__name__)
        self.assertFalse(EstoqueMovimentacao.objects.exists())

    def test_integridade_ean_estoque_banco_centros(self):
        call_command("sysvar_dev_base", "--rebuild", verbosity=0)
        empresa = Empresa.objects.get(documento=DEV_COMPANY_DOCUMENT)
        eans = list(ProdutoDetalhe.objects.filter(produto__empresa=empresa).values_list("ean13", flat=True))
        self.assertEqual(len(eans), len(set(eans)))

        skus = ProdutoDetalhe.objects.filter(produto__empresa=empresa, produto__tipo_produto__in=["1", "3"]).count()
        for loja in Loja.objects.filter(empresa=empresa):
            esperado = Decimal("100.000") if loja.tipo_unidade == Loja.TIPO_FABRICA else Decimal("50.000")
            self.assertEqual(Estoque.objects.filter(Idloja=loja, Estoque=esperado).count(), skus)

        fabrica = Loja.objects.get(empresa=empresa, tipo_unidade=Loja.TIPO_FABRICA)
        self.assertEqual(ContaBancaria.objects.get(empresa=empresa, idloja=fabrica).saldo_atual, Decimal("5000000.00"))
        self.assertEqual(ContaBancaria.objects.filter(empresa=empresa).exclude(idloja=fabrica, saldo_atual=Decimal("5000000.00")).filter(saldo_atual=0).count(), 3)
        self.assertGreaterEqual(CentroCusto.objects.filter(empresa=empresa).count(), 8)
        self.assertFalse(RequisicaoSetor.objects.filter(empresa=empresa, centro_custo__isnull=True).exists())

    def test_usuarios_finais_nomes_senha_perfis_e_sem_residual(self):
        call_command("sysvar_dev_base", "--rebuild", verbosity=0)
        empresa = Empresa.objects.get(documento=DEV_COMPANY_DOCUMENT)
        self.assertEqual(self.User.objects.count(), 14)
        self.assertEqual(list(self.User.objects.filter(is_superuser=True).values_list("username", flat=True)), [PRESERVED_SUPERUSER])
        self.assertEqual(self.User.objects.filter(empresa=empresa).count(), 13)
        self.assertEqual(set(self.User.objects.values_list("username", flat=True)), {PRESERVED_SUPERUSER, *DEV_USERS})
        for username, (first_name, last_name, perfil_nome, _loja_slug, _type_name) in DEV_USERS.items():
            user = self.User.objects.select_related("perfil_principal").get(username=username)
            self.assertEqual(user.first_name, first_name)
            self.assertEqual(user.last_name, last_name)
            self.assertEqual(user.email, f"{username}@sysvar.test")
            self.assertTrue(user.check_password(DEV_PASSWORD))
            self.assertEqual(user.perfil_principal.nome, perfil_nome)
        admin = self.User.objects.get(username="admin.delegado")
        self.assertTrue(admin.perfil_principal.permissoes_modulos.filter(modulo__chave="fiscal", acesso="EDIT").exists())

    def test_validate_falha_com_usuario_empresa_ou_movimento_residual(self):
        call_command("sysvar_dev_base", "--rebuild", verbosity=0)
        estranho = self.User.objects.create_user("estranho", password="x")
        report = SysvarDevBaseService().validate()
        self.assertFalse(report.valid)
        self.assertTrue(any("Usuários residuais" in p for p in report.problems))
        estranho.delete()

        Empresa.objects.create(nome="Empresa Residual", documento="22333444000110", ativo=True)
        report = SysvarDevBaseService().validate()
        self.assertFalse(report.valid)
        self.assertTrue(any("empresa antiga" in p for p in report.problems))

        call_command("sysvar_dev_base", "--rebuild", verbosity=0)
        empresa = Empresa.objects.get(documento=DEV_COMPANY_DOCUMENT)
        PedidoCompra.objects.create(empresa=empresa, loja=Loja.objects.filter(empresa=empresa).first(), fornecedor=Fornecedor.objects.filter(empresa=empresa).first(), emissao=timezone.localdate(), status="AB")
        report = SysvarDevBaseService().validate()
        self.assertFalse(report.valid)
        self.assertTrue(any("Movimentos operacionais" in p for p in report.problems))

    def _criar_massa_antiga(self):
        empresa = Empresa.objects.create(nome="Empresa Antiga Dev", documento="33444555000101", ativo=True)
        loja = Loja.objects.create(empresa=empresa, nome_loja="Loja Antiga", apelido_loja="ANTIGA", cnpj="44555666000196", estado="RJ", cidade="Rio de Janeiro")
        unidade = Unidade.objects.create(empresa=empresa, Codigo="PC", Descricao="Peça")
        fornecedor = Fornecedor.objects.create(empresa=empresa, nome_fornecedor="Fornecedor Antigo Ltda", apelido="Forn Antigo", documento="55666777000171", cnpj="55666777000171", categoria="REVENDA")
        plano_pai = PlanoContabil.objects.create(empresa=empresa, codigo="1", descricao="Ativo antigo", classe=PlanoContabil.CLASSE_ATIVO, natureza=PlanoContabil.NATUREZA_DEBITO, analitica=False)
        plano_filho = PlanoContabil.objects.create(empresa=empresa, codigo="1.1", descricao="Caixa antigo", classe=PlanoContabil.CLASSE_ATIVO, natureza=PlanoContabil.NATUREZA_DEBITO, conta_pai=plano_pai, nivel=2)
        natureza = Nat_Lancamento.objects.create(empresa=empresa, codigo="OLD", categoria_principal="Antiga", subcategoria="Antiga", descricao="Natureza antiga", tipo="DESPESA", status="ATIVO", tipo_natureza="DEBITO", natureza_operacao="DESPESA", plano_contabil=plano_filho, conta_contabil=plano_filho.codigo, ativo=True)
        fornecedor.natureza_padrao = natureza
        fornecedor.conta_contabil_padrao = plano_filho
        fornecedor.save(update_fields=["natureza_padrao", "conta_contabil_padrao"])
        cliente = Cliente.objects.create(empresa=empresa, nome_cliente="Cliente Antigo", documento="00000910123", cpf="00000910123")
        user = self.User.objects.create_user("usuario.antigo", password="old", empresa=empresa, loja=loja)
        self.User.objects.create_superuser("dbg", password="old")
        self.User.objects.create_user("fernando", password="old")
        EmpresaContrato.objects.filter(empresa=empresa).update(usuario_master=user)
        funcionario = Funcionarios.objects.create(empresa=empresa, nomefuncionario="Usuário Antigo", apelido="Antigo", cpf="00000700119", idloja=loja, usuario=user)
        SessaoUsuario.objects.create(empresa=empresa, usuario=user, loja=loja, token_key_hash="hash-antigo", dispositivo_id="dev-antigo", ultima_atividade_em=timezone.now(), ativa=True)
        setor = RequisicaoSetor.objects.create(empresa=empresa, loja=loja, nome="Setor Antigo")
        requisicao = Requisicao.objects.create(numero=1, empresa=empresa, loja=loja, setor=setor, requisitante=user, criado_por=user, justificativa="Antiga")
        cotacao = Cotacao.objects.create(empresa=empresa, loja=loja, responsavel=user, tipo_compra="USO_CONSUMO")
        CotacaoFornecedor.objects.create(cotacao=cotacao, fornecedor=fornecedor)
        PedidoCompra.objects.create(empresa=empresa, loja=loja, fornecedor=fornecedor, emissao=timezone.localdate(), status="AB")
        caixa = Caixa.objects.create(empresa=empresa, idloja=loja, codigo="OLD", descricao="Caixa antigo")
        venda = VendaPdv.objects.create(empresa=empresa, loja=loja, caixa=caixa, cliente=cliente, vendedor=funcionario, documento="OLD-1", forma_pagamento="PIX", criado_por=user)
        devolucao = VendaDevolucao.objects.create(empresa=empresa, venda=venda, loja=loja, cliente=cliente, documento="DEV-OLD", criado_por=user)
        vale = ValeTroca.objects.create(empresa=empresa, cliente=cliente, loja=loja, devolucao=devolucao, documento="VT-OLD", valor_original=1, saldo=1, criado_por=user)
        ValeTrocaMovimento.objects.create(vale=vale, tipo="CREDITO", valor=1, saldo_apos=1, criado_por=user)
        NotaFiscalEntrada.objects.create(empresa=empresa, loja=loja, fornecedor=fornecedor, modelo="55", serie="1", numero="OLD", dt_emissao=timezone.localdate(), dt_entrada=timezone.localdate(), criado_por=user)
        NotaFiscalSaida.objects.create(empresa=empresa, loja_origem=loja, modelo="55", serie="1", numero="OLD", dt_emissao=timezone.localdate(), dt_saida=timezone.localdate(), criado_por=user)
        return {"empresa_id": empresa.pk, "user_id": user.pk, "funcionario_id": funcionario.pk, "requisicao_id": requisicao.pk}
