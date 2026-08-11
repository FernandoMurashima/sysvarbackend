from io import StringIO
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import PerfilAcesso, PerfilModuloPermissao, SessaoUsuario, SessionToken, UserModulePermission
from accounts.services.sessions import token_hash
from auditoria.models import AuditAction, AuditLog
from cadastros.models import Cargo, Cliente, Empresa, EmpresaContrato, Fornecedor, FornecedorContato, FornecedorEndereco, FuncionarioHistorico, Funcionarios, Loja, ModuloSistema, Nat_Lancamento, PlanoContabil
from cadastros.services import ClientePadraoService
from fiscal.models import VendaDevolucao, VendaPdv, VendaPdvItem, VendaPdvPagamento
from financeiro.models import CashbackMovimento, PrazoPagamento
from produto.models import ConfigEan, Cor, Grade, Produto, ProdutoDetalhe, Tamanho, Unidade


User = get_user_model()


class OperacionalBaseTest(TestCase):
    def setUp(self):
        self.operacional = ModuloSistema.objects.update_or_create(
            chave="operacional",
            defaults={"nome": "Operacional", "categoria": "BASICO", "basico": True, "ativo": True, "ordem": 1},
        )[0]
        self.cadastros = ModuloSistema.objects.update_or_create(
            chave="cadastros",
            defaults={"nome": "Cadastros", "categoria": "BASICO", "basico": True, "ativo": True, "ordem": 2},
        )[0]
        self.empresa = Empresa.objects.create(nome="Empresa Operacional", nome_fantasia="Operacional", documento="11222333000181", plano_completo=True)
        self.outra = Empresa.objects.create(nome="Outra Empresa", documento="22333444000102", plano_completo=True)
        self.loja = Loja.objects.create(empresa=self.empresa, nome_loja="Loja 1", apelido_loja="L1", cnpj="11222333000181")
        self.loja2 = Loja.objects.create(empresa=self.empresa, nome_loja="Loja 2", apelido_loja="L2", cnpj="11222333000262")
        self.superuser = User.objects.create_superuser(username="root", password="senha12345", email="root@example.com")
        self.master = User.objects.create_user(username="master", password="senha12345", empresa=self.empresa, loja=self.loja)
        self.view_profile = PerfilAcesso.objects.create(empresa=self.empresa, nome="View")
        PerfilModuloPermissao.objects.create(perfil=self.view_profile, modulo=self.operacional, acesso=UserModulePermission.Access.VIEW)
        PerfilModuloPermissao.objects.create(perfil=self.view_profile, modulo=self.cadastros, acesso=UserModulePermission.Access.VIEW)
        self.edit_profile = PerfilAcesso.objects.create(empresa=self.empresa, nome="Edit")
        PerfilModuloPermissao.objects.create(perfil=self.edit_profile, modulo=self.operacional, acesso=UserModulePermission.Access.EDIT)
        PerfilModuloPermissao.objects.create(perfil=self.edit_profile, modulo=self.cadastros, acesso=UserModulePermission.Access.EDIT)
        self.user_view = User.objects.create_user(username="view", password="senha12345", empresa=self.empresa, loja=self.loja, perfil_principal=self.view_profile)
        self.user_view.lojas.set([self.loja])
        self.user_edit = User.objects.create_user(username="edit", password="senha12345", empresa=self.empresa, loja=self.loja, perfil_principal=self.edit_profile)
        self.user_edit.lojas.set([self.loja, self.loja2])
        self.user_none = User.objects.create_user(username="none", password="senha12345", empresa=self.empresa, loja=self.loja)
        EmpresaContrato.objects.update_or_create(empresa=self.empresa, defaults={"status": EmpresaContrato.STATUS_ATIVO, "usuario_master": self.master, "plano_completo": True, "limite_sessoes_simultaneas": 3})
        EmpresaContrato.objects.update_or_create(empresa=self.outra, defaults={"status": EmpresaContrato.STATUS_ATIVO, "plano_completo": True, "limite_sessoes_simultaneas": 3})
        self.client = APIClient()

    def active_session(self, user=None):
        raw = "raw-token-value"
        sessao = SessaoUsuario.objects.create(
            empresa=self.empresa,
            usuario=user or self.user_edit,
            loja=self.loja,
            token_key_hash=token_hash(raw),
            dispositivo_id="dev",
            ultima_atividade_em=timezone.now(),
        )
        SessionToken.objects.create(key_hash=token_hash(raw), session=sessao)
        return raw, sessao


class EmpresaSuspensaoTests(OperacionalBaseTest):
    def test_superusuario_suspende_encerra_sessoes_revoga_tokens_e_audita(self):
        raw, sessao = self.active_session()
        self.client.force_authenticate(self.superuser)
        res = self.client.post(f"/api/cadastros/empresas/{self.empresa.pk}/suspender/", {
            "motivo": EmpresaContrato.MOTIVO_INADIMPLENCIA,
            "observacao": "parcela em aberto",
            "confirmacao": self.empresa.nome,
        }, format="json")
        self.assertEqual(res.status_code, 200)
        contrato = self.empresa.contrato
        contrato.refresh_from_db()
        sessao.refresh_from_db()
        self.assertEqual(contrato.status, EmpresaContrato.STATUS_SUSPENSO)
        self.assertFalse(sessao.ativa)
        self.assertIsNotNone(sessao.session_token.revoked_at)
        log = AuditLog.objects.get(action=AuditAction.CONTRACT_SUSPENDED)
        self.assertEqual(log.metadata["sessoes_encerradas"], 1)
        self.assertEqual(log.status_code, 200)
        res_login = self.client.post("/api/accounts/auth/token/", {"username": "edit", "password": "senha12345", "device_id": "novo"}, format="json")
        self.assertEqual(res_login.status_code, 401)
        self.assertEqual(res_login.data["code"], "CONTRACT_SUSPENDED")

    def test_master_e_usuario_comum_nao_suspendem(self):
        for user in (self.master, self.user_edit):
            self.client.force_authenticate(user)
            res = self.client.post(f"/api/cadastros/empresas/{self.empresa.pk}/suspender/", {"motivo": EmpresaContrato.MOTIVO_INADIMPLENCIA, "confirmacao": self.empresa.nome}, format="json")
            self.assertEqual(res.status_code, 403)

    def test_confirmacao_invalida_bloqueia(self):
        self.client.force_authenticate(self.superuser)
        res = self.client.post(f"/api/cadastros/empresas/{self.empresa.pk}/suspender/", {"motivo": EmpresaContrato.MOTIVO_INADIMPLENCIA, "confirmacao": "errado"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.empresa.contrato.refresh_from_db()
        self.assertEqual(self.empresa.contrato.status, EmpresaContrato.STATUS_ATIVO)

    def test_falha_auditoria_obrigatoria_gera_rollback(self):
        raw, sessao = self.active_session()
        self.client.force_authenticate(self.superuser)
        with patch("cadastros.views.AuditService.required_success", side_effect=Exception("falha")):
            with self.assertRaises(Exception):
                self.client.post(f"/api/cadastros/empresas/{self.empresa.pk}/suspender/", {"motivo": EmpresaContrato.MOTIVO_INADIMPLENCIA, "confirmacao": self.empresa.nome}, format="json")
        self.empresa.contrato.refresh_from_db()
        sessao.refresh_from_db()
        self.assertEqual(self.empresa.contrato.status, EmpresaContrato.STATUS_ATIVO)
        self.assertTrue(sessao.ativa)

    def test_reativacao_funciona_sem_reativar_sessoes_antigas(self):
        raw, sessao = self.active_session()
        self.client.force_authenticate(self.superuser)
        self.client.post(f"/api/cadastros/empresas/{self.empresa.pk}/suspender/", {"motivo": EmpresaContrato.MOTIVO_INADIMPLENCIA, "confirmacao": self.empresa.nome}, format="json")
        res = self.client.post(f"/api/cadastros/empresas/{self.empresa.pk}/reativar/")
        self.assertEqual(res.status_code, 200)
        self.empresa.contrato.refresh_from_db()
        sessao.refresh_from_db()
        self.assertEqual(self.empresa.contrato.status, EmpresaContrato.STATUS_ATIVO)
        self.assertFalse(sessao.ativa)
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.CONTRACT_REACTIVATED).exists())


class LojaOperacionalTests(OperacionalBaseTest):
    def test_permissoes_view_edit_none_e_escopo_loja(self):
        self.client.force_authenticate(self.user_view)
        self.assertEqual(self.client.get("/api/cadastros/lojas/").status_code, 200)
        self.assertEqual(self.client.patch(f"/api/cadastros/lojas/{self.loja.pk}/", {"apelido_loja": "X"}).status_code, 403)
        self.client.force_authenticate(self.user_edit)
        self.assertEqual(self.client.patch(f"/api/cadastros/lojas/{self.loja.pk}/", {"apelido_loja": "X"}).status_code, 200)
        self.client.force_authenticate(self.user_none)
        self.assertEqual(self.client.get("/api/cadastros/lojas/").status_code, 403)

    def test_validacoes_de_loja(self):
        self.client.force_authenticate(self.superuser)
        res = self.client.post("/api/cadastros/lojas/", {"nome_loja": "Sem Empresa", "apelido_loja": "SE", "cnpj": "11222333000343"}, format="json")
        self.assertEqual(res.status_code, 400)
        res = self.client.post("/api/cadastros/lojas/", {"empresa": self.empresa.pk, "nome_loja": "Duplicada", "apelido_loja": "D", "cnpj": "11222333000181"}, format="json")
        self.assertEqual(res.status_code, 400)
        res = self.client.post("/api/cadastros/lojas/", {"empresa": self.empresa.pk, "nome_loja": "Matriz", "apelido_loja": "M", "cnpj": "11222333000343", "tipo_unidade": Loja.TIPO_MATRIZ}, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(Loja.objects.get(pk=res.data["id"]).Matriz, "SIM")

    def test_ciclo_de_vida_usuarios_indicadores_e_auditoria(self):
        self.client.force_authenticate(self.user_edit)
        self.assertEqual(self.client.get("/api/cadastros/lojas/indicadores/").status_code, 200)
        self.assertEqual(self.client.get(f"/api/cadastros/lojas/{self.loja.pk}/usuarios/").status_code, 200)
        res = self.client.post(f"/api/cadastros/lojas/{self.loja.pk}/inativar/")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "STORE_DEACTIVATION_BLOCKED")
        self.loja2.usuarios.clear()
        self.loja2.usuarios_permitidos.clear()
        self.client.force_authenticate(self.superuser)
        res = self.client.post(f"/api/cadastros/lojas/{self.loja2.pk}/encerrar/", {"data": "2026-08-05", "motivo": "fechamento"}, format="json")
        self.assertEqual(res.status_code, 200)
        res = self.client.post(f"/api/cadastros/lojas/{self.loja2.pk}/reabrir/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.STORE_CLOSED).exists())
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.STORE_REOPENED).exists())


class FuncionariosFase1Tests(OperacionalBaseTest):
    def setUp(self):
        super().setUp()
        self.cargo_vendedor = Cargo.objects.create(
            empresa=self.empresa,
            codigo="VENDEDOR",
            descricao="Vendedor",
            ativo=True,
            participa_vendas=True,
            permite_comissao=True,
            autoridade_operacional_loja=True,
        )
        self.cargo_supervisor = Cargo.objects.create(
            empresa=self.empresa,
            codigo="SUP",
            descricao="Supervisor",
            ativo=True,
            permite_multiplas_lojas=True,
            autoridade_operacional_loja=True,
            gerencial=True,
        )
        self.cargo_outra = Cargo.objects.create(empresa=self.outra, codigo="OUT", descricao="Outro")
        self.loja_outra = Loja.objects.create(empresa=self.outra, nome_loja="Outra Loja", apelido_loja="OL", cnpj="22333444000102")

    def payload(self, **kwargs):
        data = {
            "nomefuncionario": "Ana Vendedora",
            "cpf": "52998224725",
            "cargo": self.cargo_vendedor.pk,
            "idloja": self.loja.pk,
            "inicio": timezone.localdate().isoformat(),
            "participa_vendas": True,
            "comissionado": True,
            "comissao_percentual": "5.00",
        }
        data.update(kwargs)
        return data

    def test_criacao_valida_empresa_matricula_cpf_e_auditoria(self):
        self.client.force_authenticate(self.user_edit)
        res = self.client.post("/api/cadastros/funcionarios/", self.payload(), format="json")
        self.assertEqual(res.status_code, 201)
        funcionario = Funcionarios.objects.get(pk=res.data["id"])
        self.assertEqual(funcionario.empresa_id, self.empresa.pk)
        self.assertEqual(funcionario.matricula, "000001")
        self.assertEqual(funcionario.cpf, "52998224725")
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.EMPLOYEE_CREATED, object_id=str(funcionario.pk)).exists())

    def test_validacoes_cpf_matricula_cargo_loja_e_user(self):
        self.client.force_authenticate(self.user_edit)
        self.assertEqual(self.client.post("/api/cadastros/funcionarios/", self.payload(cpf=""), format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/cadastros/funcionarios/", self.payload(cpf="11111111111"), format="json").status_code, 400)
        ok = self.client.post("/api/cadastros/funcionarios/", self.payload(matricula="ABC001"), format="json")
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(self.client.post("/api/cadastros/funcionarios/", self.payload(cpf="15350946056", matricula="ABC001"), format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/cadastros/funcionarios/", self.payload(cpf="15350946056", cargo=self.cargo_outra.pk), format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/cadastros/funcionarios/", self.payload(cpf="15350946056", idloja=self.loja_outra.pk), format="json").status_code, 400)
        outro_user = User.objects.create_user(username="outra-user", password="senha12345", empresa=self.outra, loja=self.loja_outra)
        self.assertEqual(self.client.post("/api/cadastros/funcionarios/", self.payload(cpf="15350946056", usuario=outro_user.pk), format="json").status_code, 400)

    def test_multi_loja_ciclo_historico_filtros_e_exclusao_protegida(self):
        self.client.force_authenticate(self.user_edit)
        res = self.client.post("/api/cadastros/funcionarios/", self.payload(cargo=self.cargo_supervisor.pk, cpf="15350946056", lojas_supervisionadas=[self.loja.pk, self.loja2.pk], todas_lojas_da_empresa=True, comissionado=False), format="json")
        self.assertEqual(res.status_code, 201)
        funcionario = Funcionarios.objects.get(pk=res.data["id"])
        self.assertEqual(funcionario.lojas_supervisionadas.count(), 2)
        filtro = self.client.get("/api/cadastros/funcionarios/", {"situacao": "ATIVO", "page_size": 1})
        self.assertEqual(filtro.status_code, 200)
        self.assertIn("count", filtro.data)
        afastar = self.client.post(f"/api/cadastros/funcionarios/{funcionario.pk}/afastar/", {"motivo": "licenca"}, format="json")
        retornar = self.client.post(f"/api/cadastros/funcionarios/{funcionario.pk}/retornar/", format="json")
        desligar = self.client.post(f"/api/cadastros/funcionarios/{funcionario.pk}/desligar/", format="json")
        historico = self.client.get(f"/api/cadastros/funcionarios/{funcionario.pk}/historico/")
        self.assertEqual(afastar.status_code, 200)
        self.assertEqual(retornar.status_code, 200)
        self.assertEqual(desligar.status_code, 200)
        self.assertEqual(historico.status_code, 200)
        self.assertGreaterEqual(FuncionarioHistorico.objects.filter(funcionario=funcionario).count(), 3)
        self.assertEqual(self.client.delete(f"/api/cadastros/funcionarios/{funcionario.pk}/").status_code, 400)


class ClienteMultiempresaTests(OperacionalBaseTest):
    def test_modelo_normaliza_documento_e_cpf_por_tipo_de_pessoa(self):
        cliente = Cliente.objects.create(
            empresa=self.empresa,
            nome_cliente="Maria Cliente",
            tipo_pessoa=Cliente.TIPO_PESSOA_FISICA,
            documento="529.982.247-25",
        )

        self.assertEqual(cliente.documento, "52998224725")
        self.assertEqual(cliente.cpf, "52998224725")

        empresa_cliente = Cliente.objects.create(
            empresa=self.outra,
            nome_cliente="Empresa Cliente",
            tipo_pessoa=Cliente.TIPO_PESSOA_JURIDICA,
            documento="11.222.333/0001-81",
        )

        self.assertEqual(empresa_cliente.documento, "11222333000181")
        self.assertEqual(empresa_cliente.cpf, "11222333000181")

    def test_api_bloqueia_documento_duplicado_na_mesma_empresa(self):
        Cliente.objects.create(
            empresa=self.empresa,
            nome_cliente="Cliente Existente",
            tipo_pessoa=Cliente.TIPO_PESSOA_FISICA,
            documento="52998224725",
        )
        self.client.force_authenticate(self.user_edit)

        res = self.client.post(
            "/api/cadastros/clientes/",
            {
                "nome_cliente": "Duplicado",
                "tipo_pessoa": Cliente.TIPO_PESSOA_FISICA,
                "documento": "52998224725",
            },
            format="json",
        )

        self.assertEqual(res.status_code, 400)
        self.assertIn("documento", res.data)

    def test_cliente_padrao_nao_pode_ser_inativado_ou_excluido(self):
        padrao = Cliente.objects.create(
            empresa=self.empresa,
            nome_cliente="Consumidor Final",
            tipo_pessoa=Cliente.TIPO_PESSOA_FISICA,
            documento=Cliente.DOCUMENTO_CONSUMIDOR_FINAL,
            cliente_padrao=True,
        )
        self.client.force_authenticate(self.user_edit)

        res_inativar = self.client.post(f"/api/cadastros/clientes/{padrao.pk}/inativar/")
        res_delete = self.client.delete(f"/api/cadastros/clientes/{padrao.pk}/")

        self.assertEqual(res_inativar.status_code, 400)
        self.assertEqual(res_delete.status_code, 400)
        self.assertEqual(res_delete.data["detail"], "Cliente padrão não pode ser excluído.")
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.CLIENT_DELETE_DENIED).exists())

    def test_indicadores_refletem_carteira_de_clientes(self):
        Cliente.objects.create(
            empresa=self.empresa,
            nome_cliente="Ativo PF",
            tipo_pessoa=Cliente.TIPO_PESSOA_FISICA,
            documento="52998224725",
        )
        Cliente.objects.create(
            empresa=self.empresa,
            nome_cliente="Bloqueado PJ",
            tipo_pessoa=Cliente.TIPO_PESSOA_JURIDICA,
            documento="11222333000181",
            bloqueio=True,
            motivo_bloqueio="inadimplencia",
        )
        self.client.force_authenticate(self.user_view)

        res = self.client.get("/api/cadastros/clientes/indicadores/")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["total"], 2)
        self.assertEqual(res.data["ativos"], 2)
        self.assertEqual(res.data["bloqueados"], 1)

    def test_consentimento_preenche_data_sem_apagar_historico(self):
        self.client.force_authenticate(self.user_edit)
        res = self.client.post(
            "/api/cadastros/clientes/",
            {
                "nome_cliente": "Cliente Consentimento",
                "tipo_pessoa": Cliente.TIPO_PESSOA_FISICA,
                "documento": "52998224725",
                "aceita_email": True,
            },
            format="json",
        )

        self.assertEqual(res.status_code, 201)
        cliente = Cliente.objects.get(pk=res.data["id"])
        self.assertIsNotNone(cliente.consentimento_em)
        consentimento_em = cliente.consentimento_em

        res = self.client.patch(
            f"/api/cadastros/clientes/{cliente.pk}/",
            {"aceita_email": False, "aceita_whatsapp": False, "aceita_sms": False},
            format="json",
        )

        self.assertEqual(res.status_code, 200)
        cliente.refresh_from_db()
        self.assertEqual(cliente.consentimento_em, consentimento_em)

    def test_api_nao_permite_criar_cliente_padrao_manualmente(self):
        self.client.force_authenticate(self.user_edit)

        res = self.client.post(
            "/api/cadastros/clientes/",
            {
                "nome_cliente": "Consumidor Final Manual",
                "tipo_pessoa": Cliente.TIPO_PESSOA_FISICA,
                "documento": Cliente.DOCUMENTO_CONSUMIDOR_FINAL,
                "cliente_padrao": True,
            },
            format="json",
        )

        self.assertEqual(res.status_code, 400)
        self.assertIn("cliente_padrao", res.data)
        self.assertFalse(Cliente.objects.filter(empresa=self.empresa, cliente_padrao=True).exists())

    def test_api_nao_permite_marcar_cliente_comum_como_padrao(self):
        cliente = Cliente.objects.create(
            empresa=self.empresa,
            nome_cliente="Cliente Comum",
            tipo_pessoa=Cliente.TIPO_PESSOA_FISICA,
            documento="52998224725",
        )
        self.client.force_authenticate(self.user_edit)

        res = self.client.patch(
            f"/api/cadastros/clientes/{cliente.pk}/",
            {"cliente_padrao": True},
            format="json",
        )

        self.assertEqual(res.status_code, 400)
        cliente.refresh_from_db()
        self.assertFalse(cliente.cliente_padrao)

    def test_servico_oficial_cria_cliente_padrao(self):
        cliente, created = ClientePadraoService.obter_ou_criar(self.empresa, aplicar=True)

        self.assertTrue(created)
        self.assertTrue(cliente.cliente_padrao)
        self.assertEqual(cliente.documento, Cliente.DOCUMENTO_CONSUMIDOR_FINAL)
        self.assertEqual(cliente.cpf, Cliente.DOCUMENTO_CONSUMIDOR_FINAL)

    def test_diagnosticar_clientes_padrao_bloqueia_apply_ambiguo(self):
        cliente = Cliente.objects.create(
            empresa=self.empresa,
            nome_cliente="Cliente Comum",
            tipo_pessoa=Cliente.TIPO_PESSOA_FISICA,
            documento="52998224725",
        )
        Cliente.objects.filter(pk=cliente.pk).update(cliente_padrao=True)

        with self.assertRaises(CommandError):
            call_command("diagnosticar_clientes_padrao", "--empresa-id", str(self.empresa.pk), "--apply", stdout=StringIO())

    def _cliente(self, nome="Cliente Ciclo", documento="52998224725", **kwargs):
        defaults = {
            "empresa": self.empresa,
            "nome_cliente": nome,
            "tipo_pessoa": Cliente.TIPO_PESSOA_FISICA,
            "documento": documento,
        }
        defaults.update(kwargs)
        return Cliente.objects.create(**defaults)

    def _sku(self, empresa=None):
        empresa = empresa or self.empresa
        unidade = Unidade.objects.create(empresa=empresa, Descricao="UN")
        grade = Grade.objects.create(empresa=empresa, Descricao="Grade")
        tamanho = Tamanho.objects.create(empresa=empresa, idgrade=grade, Tamanho="M")
        cor = Cor.objects.create(empresa=empresa, Descricao="Preto", Codigo="PR", Cor="Preto")
        config, _ = ConfigEan.objects.get_or_create(
            empresa=empresa,
            country_prefix="789",
            company_prefix=str(empresa.pk).zfill(4)[-4:],
            defaults={"next_itemref": 1, "ativo": True},
        )
        produto = Produto.objects.create(empresa=empresa, tipo_produto="2", descricao="Produto Teste", unidade=unidade)
        return ProdutoDetalhe.objects.create(produto=produto, idcor=cor, idtamanho=tamanho, config_ean=config)

    def _venda(self, cliente, documento, total="100.00", subtotal=None, status_venda=None, data=None, quantidade=2, desconto_item="0.00", desconto_geral="0.00", empresa=None, loja=None, vendedor=None, pagamentos=None):
        empresa = empresa or cliente.empresa
        loja = loja or self.loja
        vendedor = vendedor or self.user_edit
        if not isinstance(vendedor, Funcionarios):
            vendedor = Funcionarios.objects.create(empresa=empresa, idloja=loja, nomefuncionario=f"Vendedor {documento}", categoria="Vendedor")
        sku = self._sku(empresa)
        venda = VendaPdv.objects.create(
            empresa=empresa,
            loja=loja,
            cliente=cliente,
            vendedor=vendedor,
            documento=documento,
            status=status_venda or VendaPdv.Status.FINALIZADA,
            forma_pagamento="DINHEIRO",
            data_venda=data or timezone.now(),
            subtotal=Decimal(subtotal or total) + Decimal(desconto_item or "0.00") + Decimal(desconto_geral or "0.00"),
            desconto_itens=Decimal(desconto_item or "0.00"),
            desconto_geral=Decimal(desconto_geral or "0.00"),
            total=Decimal(total),
        )
        VendaPdvItem.objects.create(
            venda=venda,
            produto=sku.produto,
            sku=sku,
            ean=sku.ean13,
            descricao="Produto Teste",
            quantidade=quantidade,
            preco_unitario=Decimal(total) / Decimal(max(quantidade, 1)),
            desconto=Decimal(desconto_item or "0.00"),
        )
        for pagamento in pagamentos or [{"forma": "DINHEIRO", "descricao": "Dinheiro", "valor": Decimal(total)}]:
            VendaPdvPagamento.objects.create(venda=venda, **pagamento)
        return venda

    def test_api_comum_bloqueia_mass_assignment_de_status_e_bloqueio_no_create(self):
        self.client.force_authenticate(self.user_edit)

        res_ativo = self.client.post(
            "/api/cadastros/clientes/",
            {"nome_cliente": "Status Direto", "tipo_pessoa": "PF", "documento": "52998224725", "ativo": False},
            format="json",
        )
        res_bloqueio = self.client.post(
            "/api/cadastros/clientes/",
            {"nome_cliente": "Bloqueio Direto", "tipo_pessoa": "PF", "documento": "39053344705", "bloqueio": True, "motivo_bloqueio": "manual"},
            format="json",
        )

        self.assertEqual(res_ativo.status_code, 400)
        self.assertIn("ativo", res_ativo.data)
        self.assertEqual(res_bloqueio.status_code, 400)
        self.assertIn("bloqueio", res_bloqueio.data)
        self.assertFalse(Cliente.objects.filter(nome_cliente__in=["Status Direto", "Bloqueio Direto"]).exists())

    def test_api_comum_bloqueia_mass_assignment_de_ciclo_no_patch_e_put(self):
        cliente = self._cliente()
        self.client.force_authenticate(self.user_edit)

        res_patch = self.client.patch(f"/api/cadastros/clientes/{cliente.pk}/", {"ativo": False}, format="json")
        res_put = self.client.put(
            f"/api/cadastros/clientes/{cliente.pk}/",
            {
                "nome_cliente": "Cliente Ciclo",
                "tipo_pessoa": "PF",
                "documento": "52998224725",
                "bloqueio": True,
                "motivo_bloqueio": "manual",
            },
            format="json",
        )

        self.assertEqual(res_patch.status_code, 400)
        self.assertEqual(res_put.status_code, 400)
        cliente.refresh_from_db()
        self.assertTrue(cliente.ativo)
        self.assertFalse(cliente.bloqueio)

    def test_api_comum_bloqueia_campos_de_bloqueio_read_only_no_payload(self):
        cliente = self._cliente()
        self.client.force_authenticate(self.user_edit)

        res = self.client.patch(
            f"/api/cadastros/clientes/{cliente.pk}/",
            {"bloqueado_em": timezone.now().isoformat(), "bloqueado_por": self.user_edit.pk, "observacao_bloqueio": "manual"},
            format="json",
        )

        self.assertEqual(res.status_code, 400)
        self.assertIn("bloqueado_em", res.data)
        cliente.refresh_from_db()
        self.assertIsNone(cliente.bloqueado_em)
        self.assertIsNone(cliente.bloqueado_por)

    def test_acoes_oficiais_alteram_ciclo_e_registram_auditoria(self):
        cliente = self._cliente()
        self.client.force_authenticate(self.user_edit)

        res_inativar = self.client.post(f"/api/cadastros/clientes/{cliente.pk}/inativar/")
        res_ativar = self.client.post(f"/api/cadastros/clientes/{cliente.pk}/ativar/")
        res_bloquear = self.client.post(
            f"/api/cadastros/clientes/{cliente.pk}/bloquear/",
            {"motivo": "inadimplencia", "observacao": "parcela 2"},
            format="json",
        )
        res_desbloquear = self.client.post(f"/api/cadastros/clientes/{cliente.pk}/desbloquear/")

        self.assertEqual(res_inativar.status_code, 200)
        self.assertFalse(res_inativar.data["ativo"])
        self.assertEqual(res_ativar.status_code, 200)
        self.assertTrue(res_ativar.data["ativo"])
        self.assertEqual(res_bloquear.status_code, 200)
        self.assertTrue(res_bloquear.data["bloqueio"])
        self.assertEqual(res_bloquear.data["motivo_bloqueio"], "inadimplencia")
        self.assertEqual(res_bloquear.data["observacao_bloqueio"], "parcela 2")
        self.assertEqual(res_bloquear.data["bloqueado_por"], self.user_edit.pk)
        self.assertEqual(res_bloquear.data["bloqueado_por_nome"], self.user_edit.username)
        self.assertEqual(res_desbloquear.status_code, 200)
        self.assertFalse(res_desbloquear.data["bloqueio"])
        self.assertIsNone(res_desbloquear.data["motivo_bloqueio"])
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.CLIENT_DEACTIVATED, object_id=str(cliente.pk)).exists())
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.CLIENT_ACTIVATED, object_id=str(cliente.pk)).exists())
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.CLIENT_BLOCKED, object_id=str(cliente.pk), metadata__motivo="inadimplencia").exists())
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.CLIENT_UNBLOCKED, object_id=str(cliente.pk), metadata__observacao="parcela 2").exists())

    def test_bloquear_exige_motivo_e_preserva_cliente(self):
        cliente = self._cliente()
        self.client.force_authenticate(self.user_edit)

        res = self.client.post(f"/api/cadastros/clientes/{cliente.pk}/bloquear/", {"observacao": "sem motivo"}, format="json")

        self.assertEqual(res.status_code, 400)
        self.assertIn("motivo", res.data)
        cliente.refresh_from_db()
        self.assertFalse(cliente.bloqueio)

    def test_cliente_padrao_nao_pode_ser_bloqueado(self):
        padrao = Cliente.objects.create(
            empresa=self.empresa,
            nome_cliente="Consumidor Final",
            tipo_pessoa=Cliente.TIPO_PESSOA_FISICA,
            documento=Cliente.DOCUMENTO_CONSUMIDOR_FINAL,
            cliente_padrao=True,
        )
        self.client.force_authenticate(self.user_edit)

        res = self.client.post(f"/api/cadastros/clientes/{padrao.pk}/bloquear/", {"motivo": "manual"}, format="json")

        self.assertEqual(res.status_code, 400)
        padrao.refresh_from_db()
        self.assertFalse(padrao.bloqueio)
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.CLIENT_OPERATION_DENIED, object_id=str(padrao.pk)).exists())

    def test_historico_retorna_auditlog_paginado_mais_recente_sem_dados_sensiveis(self):
        cliente = self._cliente()
        self.client.force_authenticate(self.user_edit)
        self.client.post(f"/api/cadastros/clientes/{cliente.pk}/inativar/")
        self.client.post(f"/api/cadastros/clientes/{cliente.pk}/ativar/")
        self.client.post(f"/api/cadastros/clientes/{cliente.pk}/bloquear/", {"motivo": "inadimplencia", "observacao": "parcela"}, format="json")

        self.client.force_authenticate(self.user_view)
        res = self.client.get(f"/api/cadastros/clientes/{cliente.pk}/historico/?page=1&page_size=2")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 3)
        self.assertEqual(len(res.data["results"]), 2)
        primeiro = res.data["results"][0]
        self.assertEqual(primeiro["acao"], AuditAction.CLIENT_BLOCKED)
        self.assertEqual(primeiro["motivo"], "inadimplencia")
        self.assertEqual(primeiro["observacao"], "parcela")
        self.assertIn("campos_alterados", primeiro)
        self.assertNotIn("before_data", primeiro)
        self.assertNotIn("after_data", primeiro)
        self.assertNotIn("documento", primeiro)

    def test_historico_respeita_escopo_multiempresa(self):
        cliente = self._cliente()
        outro = Cliente.objects.create(
            empresa=self.outra,
            nome_cliente="Outra Carteira",
            tipo_pessoa=Cliente.TIPO_PESSOA_FISICA,
            documento="39053344705",
        )
        AuditLog.objects.internal_create(
            empresa=self.outra,
            action=AuditAction.CLIENT_BLOCKED,
            category="CADASTRO",
            app_label="cadastros",
            model="cliente",
            object_id=str(outro.pk),
        )
        self.client.force_authenticate(self.user_view)

        res_cliente = self.client.get(f"/api/cadastros/clientes/{cliente.pk}/historico/")
        res_outro = self.client.get(f"/api/cadastros/clientes/{outro.pk}/historico/")

        self.assertEqual(res_cliente.status_code, 200)
        self.assertEqual(res_cliente.data["count"], 0)
        self.assertEqual(res_outro.status_code, 404)

    def test_permissoes_view_acessa_historico_mas_nao_altera_ciclo(self):
        cliente = self._cliente()
        self.client.force_authenticate(self.user_view)

        res_hist = self.client.get(f"/api/cadastros/clientes/{cliente.pk}/historico/")
        res_action = self.client.post(f"/api/cadastros/clientes/{cliente.pk}/bloquear/", {"motivo": "sem permissao"}, format="json")

        self.assertEqual(res_hist.status_code, 200)
        self.assertEqual(res_action.status_code, 403)

    def test_falha_de_auditoria_obrigatoria_gera_rollback_em_acao_de_ciclo(self):
        cliente = self._cliente()
        self.client.force_authenticate(self.user_edit)

        with patch("cadastros.views.AuditService.required_success", side_effect=Exception("falha")):
            with self.assertRaises(Exception):
                self.client.post(f"/api/cadastros/clientes/{cliente.pk}/bloquear/", {"motivo": "inadimplencia"}, format="json")

        cliente.refresh_from_db()
        self.assertFalse(cliente.bloqueio)
        self.assertIsNone(cliente.motivo_bloqueio)

    def test_detalhe_cliente_retorna_indicadores_comerciais(self):
        cliente = self._cliente()
        antiga = timezone.now() - timedelta(days=2)
        recente = timezone.now() - timedelta(days=1)
        self._venda(cliente, "VENDA-1", total="100.00", data=antiga)
        self._venda(cliente, "VENDA-2", total="50.00", data=recente)
        self.client.force_authenticate(self.user_view)

        res = self.client.get(f"/api/cadastros/clientes/{cliente.pk}/")

        self.assertEqual(res.status_code, 200)
        self.assertIsNotNone(res.data["ultima_compra"])
        self.assertEqual(Decimal(str(res.data["total_comprado"])), Decimal("150.00"))
        self.assertEqual(res.data["quantidade_compras"], 2)
        self.assertEqual(Decimal(str(res.data["ticket_medio"])), Decimal("75.00"))

    def test_cliente_sem_compras_retorna_indicadores_zerados(self):
        cliente = self._cliente()
        self.client.force_authenticate(self.user_view)

        res = self.client.get(f"/api/cadastros/clientes/{cliente.pk}/")

        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.data["ultima_compra"])
        self.assertEqual(Decimal(str(res.data["total_comprado"])), Decimal("0.00"))
        self.assertEqual(res.data["quantidade_compras"], 0)
        self.assertEqual(Decimal(str(res.data["ticket_medio"])), Decimal("0.00"))

    def test_indicadores_ignoram_canceladas_e_outra_empresa_e_reduzem_devolucao(self):
        cliente = self._cliente()
        outro_cliente = Cliente.objects.create(empresa=self.outra, nome_cliente="Outro", tipo_pessoa="PF", documento="39053344705")
        outra_loja = Loja.objects.create(empresa=self.outra, nome_loja="Outra Loja", apelido_loja="OL", cnpj="22333444000102")
        venda = self._venda(cliente, "VENDA-VALIDA", total="100.00")
        self._venda(cliente, "VENDA-CANCELADA", total="80.00", status_venda=VendaPdv.Status.CANCELADA)
        self._venda(outro_cliente, "VENDA-OUTRA", total="500.00", empresa=self.outra, loja=outra_loja)
        VendaDevolucao.objects.create(
            empresa=self.empresa,
            venda=venda,
            loja=self.loja,
            cliente=cliente,
            documento="DEV-1",
            status=VendaDevolucao.Status.FINALIZADA,
            credito_cliente=Decimal("25.00"),
        )
        self.client.force_authenticate(self.user_view)

        res = self.client.get(f"/api/cadastros/clientes/{cliente.pk}/")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(Decimal(str(res.data["total_comprado"])), Decimal("75.00"))
        self.assertEqual(res.data["quantidade_compras"], 1)
        self.assertEqual(Decimal(str(res.data["ticket_medio"])), Decimal("75.00"))

    def test_compras_retorna_vendas_do_cliente_paginadas_e_mais_recentes(self):
        cliente = self._cliente()
        outro = self._cliente("Outro Cliente", "39053344705")
        antiga = timezone.now() - timedelta(days=2)
        recente = timezone.now() - timedelta(days=1)
        self._venda(cliente, "ANTIGA", total="100.00", data=antiga, quantidade=1)
        self._venda(cliente, "RECENTE", total="200.00", data=recente, quantidade=3, desconto_item="10.00", pagamentos=[
            {"forma": "DINHEIRO", "descricao": "Dinheiro", "valor": Decimal("100.00")},
            {"forma": "PIX", "descricao": "Pix", "valor": Decimal("100.00")},
        ])
        self._venda(outro, "OUTRO", total="500.00")
        self.client.force_authenticate(self.user_view)

        res = self.client.get(f"/api/cadastros/clientes/{cliente.pk}/compras/?page=1&page_size=1")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 2)
        self.assertEqual(len(res.data["results"]), 1)
        row = res.data["results"][0]
        self.assertEqual(row["numero_venda"], "RECENTE")
        self.assertEqual(row["quantidade_itens"], 3)
        self.assertEqual(row["forma_pagamento"], "Múltiplas")
        self.assertEqual(Decimal(row["valor_bruto"]), Decimal("210.00"))
        self.assertEqual(Decimal(row["desconto"]), Decimal("10.00"))
        self.assertEqual(Decimal(row["valor_final"]), Decimal("200.00"))

    def test_compras_nao_retorna_vendas_de_outra_empresa(self):
        cliente = self._cliente()
        outro_cliente = Cliente.objects.create(empresa=self.outra, nome_cliente="Outro", tipo_pessoa="PF", documento="39053344705")
        outra_loja = Loja.objects.create(empresa=self.outra, nome_loja="Outra Loja", apelido_loja="OL", cnpj="22333444000102")
        self._venda(cliente, "EMPRESA-1", total="100.00")
        self._venda(outro_cliente, "EMPRESA-2", total="500.00", empresa=self.outra, loja=outra_loja)
        self.client.force_authenticate(self.user_view)

        res = self.client.get(f"/api/cadastros/clientes/{cliente.pk}/compras/")
        res_outro = self.client.get(f"/api/cadastros/clientes/{outro_cliente.pk}/compras/")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 1)
        self.assertEqual(res.data["results"][0]["numero_venda"], "EMPRESA-1")
        self.assertEqual(res_outro.status_code, 404)

    def test_compras_permissoes_view_e_none(self):
        cliente = self._cliente()
        self._venda(cliente, "VENDA-1", total="100.00")

        self.client.force_authenticate(self.user_view)
        res_view = self.client.get(f"/api/cadastros/clientes/{cliente.pk}/compras/")
        self.client.force_authenticate(self.user_none)
        res_none = self.client.get(f"/api/cadastros/clientes/{cliente.pk}/compras/")

        self.assertEqual(res_view.status_code, 200)
        self.assertEqual(res_none.status_code, 403)

    def test_compras_identifica_cancelada_e_devolvida(self):
        cliente = self._cliente()
        venda = self._venda(cliente, "DEVOLVIDA", total="100.00")
        self._venda(cliente, "CANCELADA", total="80.00", status_venda=VendaPdv.Status.CANCELADA)
        VendaDevolucao.objects.create(
            empresa=self.empresa,
            venda=venda,
            loja=self.loja,
            cliente=cliente,
            documento="DEV-1",
            status=VendaDevolucao.Status.FINALIZADA,
            credito_cliente=Decimal("100.00"),
        )
        self.client.force_authenticate(self.user_view)

        res = self.client.get(f"/api/cadastros/clientes/{cliente.pk}/compras/?ordering=documento")
        rows = {row["numero_venda"]: row for row in res.data["results"]}

        self.assertEqual(rows["CANCELADA"]["status_descricao"], "Cancelada")
        self.assertTrue(rows["CANCELADA"]["cancelada"])
        self.assertEqual(rows["DEVOLVIDA"]["status_descricao"], "Devolvida")
        self.assertTrue(rows["DEVOLVIDA"]["devolvida"])

    def test_cliente_padrao_mostra_compras_da_propria_empresa(self):
        padrao = Cliente.objects.create(
            empresa=self.empresa,
            nome_cliente="Consumidor Final",
            tipo_pessoa=Cliente.TIPO_PESSOA_FISICA,
            documento=Cliente.DOCUMENTO_CONSUMIDOR_FINAL,
            cliente_padrao=True,
        )
        self._venda(padrao, "CF-1", total="30.00")
        self.client.force_authenticate(self.user_view)

        detail = self.client.get(f"/api/cadastros/clientes/{padrao.pk}/")
        compras = self.client.get(f"/api/cadastros/clientes/{padrao.pk}/compras/")

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(Decimal(str(detail.data["total_comprado"])), Decimal("30.00"))
        self.assertEqual(compras.status_code, 200)
        self.assertEqual(compras.data["count"], 1)

    def test_cliente_com_venda_nao_pode_ser_excluido_e_audita(self):
        cliente = self._cliente()
        self._venda(cliente, "VENDA-1", total="100.00")
        self.client.force_authenticate(self.user_edit)

        res = self.client.delete(f"/api/cadastros/clientes/{cliente.pk}/")

        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data, {"detail": "Este cliente possui vendas ou outros registros vinculados e não pode ser excluído. Utilize a inativação."})
        self.assertTrue(Cliente.objects.filter(pk=cliente.pk).exists())
        self.assertTrue(VendaPdv.objects.filter(cliente=cliente).exists())
        logs = AuditLog.objects.filter(action=AuditAction.CLIENT_DELETE_DENIED, object_id=str(cliente.pk))
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.get().error_message, "Este cliente possui vendas ou outros registros vinculados e não pode ser excluído. Utilize a inativação.")

        historico = self.client.get(f"/api/cadastros/clientes/{cliente.pk}/historico/")
        self.assertEqual(historico.status_code, 200)
        self.assertEqual(historico.data["results"][0]["acao"], AuditAction.CLIENT_DELETE_DENIED)
        self.assertIn("Exclusão negada", historico.data["results"][0]["acao_descricao"])
        self.assertIn("Utilize a inativação", historico.data["results"][0]["motivo"])

    def test_cliente_sem_vinculos_pode_ser_excluido(self):
        cliente = self._cliente()
        self.client.force_authenticate(self.user_edit)

        res = self.client.delete(f"/api/cadastros/clientes/{cliente.pk}/")

        self.assertEqual(res.status_code, 204)
        self.assertFalse(Cliente.objects.filter(pk=cliente.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.CLIENT_DELETED, object_id=str(cliente.pk)).exists())

    def test_cliente_de_outra_empresa_nao_pode_ser_excluido(self):
        outro_cliente = Cliente.objects.create(empresa=self.outra, nome_cliente="Outro", tipo_pessoa="PF", documento="39053344705")
        self.client.force_authenticate(self.user_edit)

        res = self.client.delete(f"/api/cadastros/clientes/{outro_cliente.pk}/")

        self.assertEqual(res.status_code, 404)
        self.assertTrue(Cliente.objects.filter(pk=outro_cliente.pk).exists())

    def test_cliente_com_cashback_nao_pode_ser_excluido(self):
        cliente = self._cliente()
        CashbackMovimento.objects.create(
            empresa=self.empresa,
            cliente=cliente,
            tipo=CashbackMovimento.TIPO_CREDITO,
            valor=Decimal("10.00"),
        )
        self.client.force_authenticate(self.user_edit)

        res = self.client.delete(f"/api/cadastros/clientes/{cliente.pk}/")

        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["detail"], "Este cliente possui vendas ou outros registros vinculados e não pode ser excluído. Utilize a inativação.")
        self.assertTrue(Cliente.objects.filter(pk=cliente.pk).exists())
        self.assertTrue(CashbackMovimento.objects.filter(cliente=cliente).exists())

    def test_protected_error_na_exclusao_de_cliente_retorna_mensagem_amigavel(self):
        cliente = self._cliente()
        self.client.force_authenticate(self.user_edit)

        with patch("cadastros.models.Cliente.delete", side_effect=ProtectedError("Cannot delete", [])):
            res = self.client.delete(f"/api/cadastros/clientes/{cliente.pk}/")

        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data, {"detail": "Este cliente possui vendas ou outros registros vinculados e não pode ser excluído. Utilize a inativação."})
        self.assertNotIn("Cannot delete", str(res.data))
        self.assertEqual(AuditLog.objects.filter(action=AuditAction.CLIENT_DELETE_DENIED, object_id=str(cliente.pk)).count(), 1)

    def test_integrity_error_na_exclusao_de_cliente_nao_vaza_detalhe_tecnico(self):
        cliente = self._cliente()
        self.client.force_authenticate(self.user_edit)

        with patch("cadastros.models.Cliente.delete", side_effect=IntegrityError("foreign key constraint fails")):
            res = self.client.delete(f"/api/cadastros/clientes/{cliente.pk}/")

        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data, {"detail": "Este cliente possui vendas ou outros registros vinculados e não pode ser excluído. Utilize a inativação."})
        self.assertNotIn("foreign key", str(res.data).lower())
        self.assertEqual(AuditLog.objects.filter(action=AuditAction.CLIENT_DELETE_DENIED, object_id=str(cliente.pk)).count(), 1)

    def test_compras_endpoint_nao_executa_n_mais_um_e_indicadores_sao_coerentes(self):
        cliente = self._cliente()
        self._venda(cliente, "VENDA-1", total="100.00", quantidade=1)
        self._venda(cliente, "VENDA-2", total="50.00", quantidade=2)
        self.client.force_authenticate(self.user_view)

        with self.assertNumQueries(11):
            res = self.client.get(f"/api/cadastros/clientes/{cliente.pk}/compras/?page_size=20")
        detail = self.client.get(f"/api/cadastros/clientes/{cliente.pk}/")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 2)
        self.assertEqual(sum(Decimal(row["valor_final"]) for row in res.data["results"]), Decimal(str(detail.data["total_comprado"])))


class FornecedorFase1Tests(OperacionalBaseTest):
    def _fornecedor(self, nome="Fornecedor Teste", documento="11222333000181", empresa=None, **kwargs):
        defaults = {
            "empresa": empresa or self.empresa,
            "nome_fornecedor": nome,
            "tipo_pessoa": Fornecedor.TIPO_PESSOA_JURIDICA,
            "documento": documento,
        }
        defaults.update(kwargs)
        return Fornecedor.objects.create(**defaults)

    def test_cria_pf_pj_sem_documento_e_permite_multiplos_sem_documento(self):
        self.client.force_authenticate(self.user_edit)

        pf = self.client.post("/api/cadastros/fornecedores/", {
            "nome_fornecedor": "Fornecedor PF",
            "tipo_pessoa": "PF",
            "documento": "529.982.247-25",
        }, format="json")
        pj = self.client.post("/api/cadastros/fornecedores/", {
            "nome_fornecedor": "Fornecedor PJ",
            "tipo_pessoa": "PJ",
            "documento": "11.222.333/0001-81",
        }, format="json")
        sem_doc_1 = self.client.post("/api/cadastros/fornecedores/", {"nome_fornecedor": "Sem Doc 1", "tipo_pessoa": "PJ"}, format="json")
        sem_doc_2 = self.client.post("/api/cadastros/fornecedores/", {"nome_fornecedor": "Sem Doc 2", "tipo_pessoa": "PJ"}, format="json")

        self.assertEqual(pf.status_code, 201)
        self.assertEqual(pf.data["documento"], "52998224725")
        self.assertEqual(pj.status_code, 201)
        self.assertEqual(pj.data["documento"], "11222333000181")
        self.assertEqual(pj.data["cnpj"], "11222333000181")
        self.assertEqual(sem_doc_1.status_code, 201)
        self.assertEqual(sem_doc_2.status_code, 201)

    def test_documento_invalido_duplicado_e_mesmo_documento_em_outra_empresa(self):
        self._fornecedor(documento="11222333000181")
        self.client.force_authenticate(self.user_edit)

        invalido = self.client.post("/api/cadastros/fornecedores/", {"nome_fornecedor": "Inválido", "tipo_pessoa": "PJ", "documento": "11111111111111"}, format="json")
        duplicado = self.client.post("/api/cadastros/fornecedores/", {"nome_fornecedor": "Duplicado", "tipo_pessoa": "PJ", "documento": "11222333000181"}, format="json")
        outro = self._fornecedor(nome="Outra Empresa", documento="11222333000181", empresa=self.outra)

        self.assertEqual(invalido.status_code, 400)
        self.assertIn("documento", invalido.data)
        self.assertEqual(duplicado.status_code, 400)
        self.assertIn("documento", duplicado.data)
        self.assertEqual(outro.empresa, self.outra)

    def test_isolamento_multiempresa_e_mass_assignment_de_empresa(self):
        outro = self._fornecedor(nome="Fornecedor Outra", documento="04252011000110", empresa=self.outra)
        self.client.force_authenticate(self.user_edit)

        listagem = self.client.get("/api/cadastros/fornecedores/")
        detalhe_outro = self.client.get(f"/api/cadastros/fornecedores/{outro.pk}/")
        criar_outra_empresa = self.client.post("/api/cadastros/fornecedores/", {
            "empresa": self.outra.pk,
            "nome_fornecedor": "Tentativa Cruzada",
            "tipo_pessoa": "PJ",
        }, format="json")

        self.assertEqual(listagem.status_code, 200)
        self.assertFalse(any(row["id"] == outro.pk for row in listagem.data["results"]))
        self.assertEqual(detalhe_outro.status_code, 404)
        self.assertEqual(criar_outra_empresa.status_code, 400)

    def test_categorias_multiplas_contatos_enderecos_e_principal_por_tipo(self):
        self.client.force_authenticate(self.user_edit)

        res = self.client.post("/api/cadastros/fornecedores/", {
            "nome_fornecedor": "Fornecedor Completo",
            "tipo_pessoa": "PJ",
            "documento": "11222333000181",
            "categorias": ["MATERIA_PRIMA", "AVIAMENTO"],
            "contatos": [
                {"nome": "Ana", "tipo": "COMERCIAL", "principal": True},
                {"nome": "Bruno", "tipo": "COMERCIAL", "principal": True},
                {"nome": "Carla", "tipo": "FINANCEIRO", "principal": True},
            ],
            "enderecos": [
                {"tipo": "FISCAL", "endereco": "Rua Central", "estado": "sp", "principal": True},
                {"tipo": "FISCAL", "endereco": "Rua Secundaria", "principal": True},
            ],
        }, format="json")

        self.assertEqual(res.status_code, 201)
        fornecedor = Fornecedor.objects.get(pk=res.data["id"])
        self.assertEqual(set(fornecedor.categorias_rel.values_list("categoria", flat=True)), {"MATERIA_PRIMA", "AVIAMENTO"})
        self.assertEqual(fornecedor.contatos.filter(tipo="COMERCIAL", principal=True).count(), 1)
        self.assertEqual(fornecedor.contatos.filter(tipo="FINANCEIRO", principal=True).count(), 1)
        self.assertEqual(fornecedor.enderecos.filter(tipo="FISCAL", principal=True).count(), 1)

    def test_padroes_fiscais_financeiros_estruturados_e_multiempresa(self):
        prazo = PrazoPagamento.objects.create(empresa=self.empresa, codigo="30", descricao="30 dias", num_parcelas=1, intervalo_dias=30, ativo=True)
        prazo_outra = PrazoPagamento.objects.create(empresa=self.outra, codigo="60", descricao="60 dias", num_parcelas=1, intervalo_dias=60, ativo=True)
        conta = PlanoContabil.objects.create(empresa=self.empresa, codigo="2.1.01.001", descricao="Fornecedores Nacionais", classe=PlanoContabil.CLASSE_PASSIVO, natureza=PlanoContabil.NATUREZA_CREDITO, analitica=True, ativa=True)
        conta_outra = PlanoContabil.objects.create(empresa=self.outra, codigo="2.1.01.999", descricao="Fornecedor Outra Empresa", classe=PlanoContabil.CLASSE_PASSIVO, natureza=PlanoContabil.NATUREZA_CREDITO, analitica=True, ativa=True)
        natureza = Nat_Lancamento.objects.create(empresa=self.empresa, codigo="FORN", categoria_principal="Financeiro", subcategoria="Fornecedores", descricao="Pagamento fornecedor", tipo="DESPESA", status="ATIVO", tipo_natureza="DEBITO", natureza_operacao="DESPESA", ativo=True)
        natureza_outra = Nat_Lancamento.objects.create(empresa=self.outra, codigo="OUTR", categoria_principal="Financeiro", subcategoria="Fornecedores", descricao="Outra natureza", tipo="DESPESA", status="ATIVO", tipo_natureza="DEBITO", natureza_operacao="DESPESA", ativo=True)

        self.client.force_authenticate(self.user_edit)
        res = self.client.post("/api/cadastros/fornecedores/", {
            "nome_fornecedor": "Fornecedor Padroes",
            "tipo_pessoa": "PJ",
            "documento": "11444777000161",
            "contribuinte_icms": "SIM",
            "prazo_padrao_pagamento_ref": prazo.pk,
            "conta_contabil_padrao": conta.pk,
            "natureza_padrao": natureza.pk,
        }, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["contribuinte_icms_descricao"], "Sim")
        self.assertEqual(res.data["prazo_padrao_descricao"], "30 dias")
        self.assertEqual(res.data["conta_contabil_codigo"], "2.1.01.001")
        self.assertEqual(res.data["natureza_padrao_descricao"], "Pagamento fornecedor")
        fornecedor = Fornecedor.objects.get(pk=res.data["id"])
        self.assertEqual(fornecedor.prazo_padrao_pagamento_ref_id, prazo.pk)
        self.assertEqual(fornecedor.prazo_padrao_pagamento, prazo.pk)
        self.assertEqual(fornecedor.conta_contabil_padrao_id, conta.pk)
        self.assertEqual(fornecedor.conta_contabil, conta.codigo)

        patch = self.client.patch(f"/api/cadastros/fornecedores/{fornecedor.pk}/", {"apelido": "Mantem"}, format="json")
        self.assertEqual(patch.status_code, 200)
        fornecedor.refresh_from_db()
        self.assertEqual(fornecedor.prazo_padrao_pagamento_ref_id, prazo.pk)
        self.assertEqual(fornecedor.conta_contabil_padrao_id, conta.pk)
        self.assertEqual(fornecedor.natureza_padrao_id, natureza.pk)

        for campo, valor in [
            ("prazo_padrao_pagamento_ref", prazo_outra.pk),
            ("conta_contabil_padrao", conta_outra.pk),
            ("natureza_padrao", natureza_outra.pk),
        ]:
            cruzado = self.client.post("/api/cadastros/fornecedores/", {
                "nome_fornecedor": f"Fornecedor {campo}",
                "tipo_pessoa": "PJ",
                campo: valor,
            }, format="json")
            self.assertEqual(cruzado.status_code, 400)
            self.assertIn(campo, cruzado.data)

        invalido = self.client.post("/api/cadastros/fornecedores/", {
            "nome_fornecedor": "ICMS Invalido",
            "tipo_pessoa": "PJ",
            "contribuinte_icms": "TALVEZ",
        }, format="json")
        self.assertEqual(invalido.status_code, 400)
        self.assertIn("contribuinte_icms", invalido.data)

        self.client.force_authenticate(self.superuser)
        for tipo_conta, descricao in [
            ("CORRENTE", "Conta corrente"),
            ("POUPANCA", "Conta poupança"),
            ("PAGAMENTO", "Conta de pagamento"),
            ("OUTRA", "Outra"),
        ]:
            conta_res = self.client.post("/api/cadastros/fornecedores/", {
                "empresa": self.empresa.pk,
                "nome_fornecedor": f"Fornecedor {tipo_conta}",
                "tipo_pessoa": "PJ",
                "tipo_conta": tipo_conta,
            }, format="json")
            self.assertEqual(conta_res.status_code, 201)
            self.assertEqual(conta_res.data["tipo_conta_descricao"], descricao)
        tipo_invalido = self.client.post("/api/cadastros/fornecedores/", {
            "empresa": self.empresa.pk,
            "nome_fornecedor": "Fornecedor CC",
            "tipo_pessoa": "PJ",
            "tipo_conta": "CC",
        }, format="json")
        self.assertEqual(tipo_invalido.status_code, 400)
        self.assertIn("tipo_conta", tipo_invalido.data)

    def test_ciclo_de_vida_e_historico_auditado(self):
        fornecedor = self._fornecedor()
        self.client.force_authenticate(self.user_edit)

        inativar = self.client.post(f"/api/cadastros/fornecedores/{fornecedor.pk}/inativar/")
        ativar = self.client.post(f"/api/cadastros/fornecedores/{fornecedor.pk}/ativar/")
        bloquear_sem_motivo = self.client.post(f"/api/cadastros/fornecedores/{fornecedor.pk}/bloquear/", {}, format="json")
        bloquear = self.client.post(f"/api/cadastros/fornecedores/{fornecedor.pk}/bloquear/", {"motivo": "qualidade", "observacao": "lote recusado"}, format="json")
        desbloquear = self.client.post(f"/api/cadastros/fornecedores/{fornecedor.pk}/desbloquear/")
        historico = self.client.get(f"/api/cadastros/fornecedores/{fornecedor.pk}/historico/")

        self.assertEqual(inativar.status_code, 200)
        self.assertEqual(ativar.status_code, 200)
        self.assertEqual(bloquear_sem_motivo.status_code, 400)
        self.assertEqual(bloquear.status_code, 200)
        self.assertTrue(bloquear.data["bloqueio"])
        self.assertEqual(desbloquear.status_code, 200)
        self.assertFalse(desbloquear.data["bloqueio"])
        self.assertEqual(historico.status_code, 200)
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.SUPPLIER_BLOCKED, object_id=str(fornecedor.pk)).exists())

    def test_contato_e_endereco_actions_auditam(self):
        fornecedor = self._fornecedor()
        self.client.force_authenticate(self.user_edit)

        contato = self.client.post(f"/api/cadastros/fornecedores/{fornecedor.pk}/contatos/", {"nome": "Ana", "tipo": "COMERCIAL", "principal": True}, format="json")
        endereco = self.client.post(f"/api/cadastros/fornecedores/{fornecedor.pk}/enderecos/", {"tipo": "FISCAL", "endereco": "Rua Central", "principal": True}, format="json")
        contato_inativado = self.client.post(f"/api/cadastros/fornecedores/{fornecedor.pk}/contatos/{contato.data['id']}/inativar/")
        endereco_inativado = self.client.post(f"/api/cadastros/fornecedores/{fornecedor.pk}/enderecos/{endereco.data['id']}/inativar/")
        contato_reativado = self.client.post(f"/api/cadastros/fornecedores/{fornecedor.pk}/contatos/{contato.data['id']}/reativar/")
        endereco_reativado = self.client.post(f"/api/cadastros/fornecedores/{fornecedor.pk}/enderecos/{endereco.data['id']}/reativar/")
        contato_editado = self.client.patch(f"/api/cadastros/fornecedores/{fornecedor.pk}/contatos/{contato.data['id']}/", {"cargo_funcao": "Vendas"}, format="json")
        endereco_editado = self.client.patch(f"/api/cadastros/fornecedores/{fornecedor.pk}/enderecos/{endereco.data['id']}/", {"numero": "100"}, format="json")
        contatos_get = self.client.get(f"/api/cadastros/fornecedores/{fornecedor.pk}/contatos/")
        enderecos_get = self.client.get(f"/api/cadastros/fornecedores/{fornecedor.pk}/enderecos/")
        fornecedor_get = self.client.get(f"/api/cadastros/fornecedores/{fornecedor.pk}/")

        self.assertEqual(contato.status_code, 201)
        self.assertEqual(endereco.status_code, 201)
        self.assertEqual(contato_inativado.status_code, 200)
        self.assertEqual(endereco_inativado.status_code, 200)
        self.assertEqual(contato_reativado.status_code, 200)
        self.assertEqual(endereco_reativado.status_code, 200)
        self.assertEqual(contato_editado.status_code, 200)
        self.assertEqual(endereco_editado.status_code, 200)
        self.assertEqual(contatos_get.status_code, 200)
        self.assertEqual(enderecos_get.status_code, 200)
        self.assertEqual(fornecedor_get.status_code, 200)
        self.assertEqual(contatos_get.data[0]["nome"], "Ana")
        self.assertEqual(contatos_get.data[0]["cargo_funcao"], "Vendas")
        self.assertTrue(contatos_get.data[0]["ativo"])
        self.assertEqual(enderecos_get.data[0]["endereco"], "Rua Central")
        self.assertEqual(enderecos_get.data[0]["numero"], "100")
        self.assertTrue(enderecos_get.data[0]["ativo"])
        self.assertEqual(fornecedor_get.data["contatos"][0]["nome"], "Ana")
        self.assertEqual(fornecedor_get.data["enderecos"][0]["endereco"], "Rua Central")
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.SUPPLIER_CONTACT_CREATED, object_id=str(contato.data["id"])).exists())
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.SUPPLIER_ADDRESS_CREATED, object_id=str(endereco.data["id"])).exists())

    def test_usuario_view_consulta_contatos_enderecos_sem_permitir_alterar(self):
        fornecedor = self._fornecedor()
        contato = FornecedorContato.objects.create(fornecedor=fornecedor, empresa=self.empresa, nome="Fausto", cargo_funcao="Vendedor", tipo="COMERCIAL", principal=True)
        endereco = FornecedorEndereco.objects.create(fornecedor=fornecedor, empresa=self.empresa, tipo="FISCAL", endereco="Rua Central", principal=True)
        self.client.force_authenticate(self.user_view)

        contatos_get = self.client.get(f"/api/cadastros/fornecedores/{fornecedor.pk}/contatos/")
        enderecos_get = self.client.get(f"/api/cadastros/fornecedores/{fornecedor.pk}/enderecos/")
        contato_patch = self.client.patch(f"/api/cadastros/fornecedores/{fornecedor.pk}/contatos/{contato.pk}/", {"nome": "Outro"}, format="json")
        endereco_patch = self.client.patch(f"/api/cadastros/fornecedores/{fornecedor.pk}/enderecos/{endereco.pk}/", {"endereco": "Outra"}, format="json")

        self.assertEqual(contatos_get.status_code, 200)
        self.assertEqual(enderecos_get.status_code, 200)
        self.assertEqual(contatos_get.data[0]["nome"], "Fausto")
        self.assertEqual(enderecos_get.data[0]["endereco"], "Rua Central")
        self.assertEqual(contato_patch.status_code, 403)
        self.assertEqual(endereco_patch.status_code, 403)

    def test_exclusao_negada_por_compra_e_bloqueio_de_uso_em_novo_pedido(self):
        from compras.models import PedidoCompra

        compras_modulo = ModuloSistema.objects.update_or_create(
            chave="compras",
            defaults={"nome": "Compras", "categoria": "BASICO", "basico": True, "ativo": True, "ordem": 3},
        )[0]
        PerfilModuloPermissao.objects.update_or_create(
            perfil=self.edit_profile,
            modulo=compras_modulo,
            defaults={"acesso": UserModulePermission.Access.EDIT},
        )
        fornecedor = self._fornecedor()
        PedidoCompra.objects.create(empresa=self.empresa, tipo="1", loja=self.loja, fornecedor=fornecedor, status="AB")
        self.client.force_authenticate(self.user_edit)

        delete = self.client.delete(f"/api/cadastros/fornecedores/{fornecedor.pk}/")
        fornecedor.bloqueio = True
        fornecedor.motivo_bloqueio = "restrição"
        fornecedor.save()
        novo_pedido = self.client.post("/api/compras/pedidos/", {
            "tipo": "1",
            "loja": self.loja.pk,
            "fornecedor": fornecedor.pk,
            "emissao": timezone.localdate().isoformat(),
        }, format="json")

        self.assertEqual(delete.status_code, 400)
        self.assertEqual(delete.data["detail"], "Este fornecedor possui compras ou outros registros vinculados e não pode ser excluído. Utilize a inativação.")
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.SUPPLIER_DELETE_DENIED, object_id=str(fornecedor.pk)).exists())
        self.assertEqual(novo_pedido.status_code, 400)
        self.assertIn("fornecedor", novo_pedido.data)

    def test_financeiro_recusa_fornecedor_inativo_em_titulo_novo(self):
        financeiro_modulo = ModuloSistema.objects.update_or_create(
            chave="financeiro",
            defaults={"nome": "Financeiro", "categoria": "BASICO", "basico": True, "ativo": True, "ordem": 4},
        )[0]
        PerfilModuloPermissao.objects.update_or_create(
            perfil=self.edit_profile,
            modulo=financeiro_modulo,
            defaults={"acesso": UserModulePermission.Access.EDIT},
        )
        fornecedor = self._fornecedor(ativo=False)
        natureza = Nat_Lancamento.objects.create(
            empresa=self.empresa,
            codigo="FORN",
            categoria_principal="Compras",
            subcategoria="Fornecedores",
            descricao="Fornecedor",
            tipo="Despesa",
            status="Ativo",
            tipo_natureza="DEBITO",
            natureza_operacao="DESPESA",
        )
        self.client.force_authenticate(self.user_edit)

        res = self.client.post("/api/financeiro/pagar/", {
            "idloja": self.loja.pk,
            "idfornecedor": fornecedor.pk,
            "Titulo": "Teste",
            "Data_emissao": timezone.localdate().isoformat(),
            "Valor_total": "10.00",
            "Idnatureza": natureza.pk,
        }, format="json")

        self.assertEqual(res.status_code, 400)
        self.assertIn("idfornecedor", res.data)

    def test_dados_bancarios_ocultos_sem_permissao_de_campo(self):
        fornecedor = self._fornecedor(banco="Banco", agencia="0001", conta="123", chave_pix="pix")
        self.client.force_authenticate(self.user_view)

        res = self.client.get(f"/api/cadastros/fornecedores/{fornecedor.pk}/")

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["dados_bancarios_ocultos"])
        self.assertIsNone(res.data["banco"])

    def test_contato_e_endereco_principal_unico_por_tipo_via_endpoint(self):
        fornecedor = self._fornecedor()
        self.client.force_authenticate(self.user_edit)

        contato_a = self.client.post(f"/api/cadastros/fornecedores/{fornecedor.pk}/contatos/", {"nome": "Ana", "tipo": "COMERCIAL", "principal": True}, format="json")
        contato_b = self.client.post(f"/api/cadastros/fornecedores/{fornecedor.pk}/contatos/", {"nome": "Bruno", "tipo": "COMERCIAL", "principal": True}, format="json")
        contato_c = self.client.post(f"/api/cadastros/fornecedores/{fornecedor.pk}/contatos/", {"nome": "Carla", "tipo": "FINANCEIRO", "principal": True}, format="json")
        endereco_a = self.client.post(f"/api/cadastros/fornecedores/{fornecedor.pk}/enderecos/", {"tipo": "FISCAL", "endereco": "Rua A", "principal": True}, format="json")
        endereco_b = self.client.post(f"/api/cadastros/fornecedores/{fornecedor.pk}/enderecos/", {"tipo": "FISCAL", "endereco": "Rua B", "principal": True}, format="json")

        self.assertEqual(contato_a.status_code, 201)
        self.assertEqual(contato_b.status_code, 201)
        self.assertEqual(contato_c.status_code, 201)
        self.assertEqual(endereco_a.status_code, 201)
        self.assertEqual(endereco_b.status_code, 201)
        self.assertEqual(fornecedor.contatos.filter(tipo="COMERCIAL", ativo=True, principal=True).count(), 1)
        self.assertEqual(fornecedor.contatos.filter(tipo="FINANCEIRO", ativo=True, principal=True).count(), 1)
        self.assertEqual(fornecedor.enderecos.filter(tipo="FISCAL", ativo=True, principal=True).count(), 1)

    def test_indicador_e_financeiro_calculam_saldo_real_aberto(self):
        from financeiro.models import Pagar, PagarItem

        fornecedor = self._fornecedor()
        natureza = Nat_Lancamento.objects.create(
            empresa=self.empresa,
            codigo="SALDO",
            categoria_principal="Compras",
            subcategoria="Fornecedores",
            descricao="Fornecedor",
            tipo="Despesa",
            status="Ativo",
            tipo_natureza="DEBITO",
            natureza_operacao="DESPESA",
        )
        titulo = Pagar.objects.create(
            empresa=self.empresa,
            idloja=self.loja,
            idfornecedor=fornecedor,
            Titulo="Saldo fornecedor",
            Valor_total=Decimal("450.00"),
            Idnatureza=natureza,
        )
        PagarItem.objects.create(Idpagar=titulo, parcela_n=1, status=PagarItem.STATUS_EFETIVO, Data_vencimento=timezone.localdate(), valor_parcela=Decimal("100.00"), valor_baixa=Decimal("30.00"), Idnatureza=natureza)
        PagarItem.objects.create(Idpagar=titulo, parcela_n=2, status=PagarItem.STATUS_BAIXADO, Data_vencimento=timezone.localdate(), valor_parcela=Decimal("200.00"), valor_baixa=Decimal("200.00"), Idnatureza=natureza)
        PagarItem.objects.create(Idpagar=titulo, parcela_n=3, status=PagarItem.STATUS_CANCELADO, Data_vencimento=timezone.localdate(), valor_parcela=Decimal("150.00"), Idnatureza=natureza)
        self.client.force_authenticate(self.user_edit)

        detalhe = self.client.get(f"/api/cadastros/fornecedores/{fornecedor.pk}/")
        financeiro = self.client.get(f"/api/cadastros/fornecedores/{fornecedor.pk}/financeiro/")
        indicadores = self.client.get("/api/cadastros/fornecedores/indicadores/")

        self.assertEqual(detalhe.status_code, 200)
        self.assertEqual(Decimal(str(detalhe.data["saldo_a_pagar"])), Decimal("70.00"))
        self.assertEqual(financeiro.status_code, 200)
        saldos = [Decimal(str(item["saldo"])) for item in financeiro.data["results"]]
        self.assertEqual(saldos, [Decimal("70.00"), Decimal("0.00"), Decimal("0.00")])
        self.assertEqual(indicadores.status_code, 200)
        self.assertEqual(Decimal(str(indicadores.data["saldo_a_pagar"])), Decimal("70.00"))

    def test_paginacao_filtros_e_duplicidade_respeitam_empresa(self):
        self._fornecedor(nome="Alpha Aviamento", documento="04252011000110", categoria="AVIAMENTO")
        self._fornecedor(nome="Beta Facção", documento="11444777000161", categoria="FACCAO", tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA)
        self._fornecedor(nome="Alpha Outra", documento="11222333000181", empresa=self.outra)
        self.client.force_authenticate(self.user_edit)

        pagina = self.client.get("/api/cadastros/fornecedores/", {"page": 1, "page_size": 1, "search": "Alpha"})
        filtro_categoria = self.client.get("/api/cadastros/fornecedores/", {"categoria": "FACCAO"})
        duplicados = self.client.get("/api/cadastros/fornecedores/possiveis-duplicados/", {"nome": "Alpha"})

        self.assertEqual(pagina.status_code, 200)
        self.assertEqual(pagina.data["count"], 1)
        self.assertEqual(len(pagina.data["results"]), 1)
        self.assertEqual(filtro_categoria.status_code, 200)
        self.assertEqual(filtro_categoria.data["count"], 1)
        self.assertEqual(duplicados.status_code, 200)
        self.assertEqual(len(duplicados.data), 1)
        self.assertEqual(duplicados.data[0]["nome_fornecedor"], "Alpha Aviamento")
