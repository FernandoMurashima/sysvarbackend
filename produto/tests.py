from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import PerfilAcesso, PerfilModuloPermissao, UserModulePermission
from auditoria.models import AuditLog
from cadastros.models import Empresa, EmpresaContrato, EmpresaModulo, Fornecedor, Loja, ModuloSistema
from .models import (
    Colecao,
    ConfigEan,
    Cor,
    Estoque,
    EstoqueMovimentacao,
    Grade,
    Grupo,
    Ncm,
    Produto,
    ProdutoDetalhe,
    ProdutoFornecedor,
    ProdutoImagem,
    ProdutoInsumoHistorico,
    ProdutoUsoConsumoEstoque,
    ProdutoUsoConsumoHistorico,
    ProdutoUsoConsumoMovimentacao,
    ProdutoVendaHistorico,
    Pack,
    PackItem,
    Subgrupo,
    Tamanho,
    Unidade,
    Material,
)


@override_settings(ALLOWED_HOSTS=["testserver"])
class ProdutoFornecedorApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa = Empresa.objects.create(nome="Empresa PF", documento="33222333000181", plano_completo=True)
        self.empresa_b = Empresa.objects.create(nome="Empresa PF B", documento="33222333000182", plano_completo=True)
        self.user = get_user_model().objects.create_user(
            "produto-fornecedor",
            "produto-fornecedor@example.com",
            "123",
            empresa=self.empresa,
            type="Admin",
        )
        self.user_b = get_user_model().objects.create_user(
            "produto-fornecedor-b",
            "produto-fornecedor-b@example.com",
            "123",
            empresa=self.empresa_b,
            type="Admin",
        )
        self.produtos_modulo, _ = ModuloSistema.objects.get_or_create(
            chave="produtos",
            defaults={"nome": "Produtos", "categoria": ModuloSistema.CATEGORIA_BASICO, "basico": True},
        )
        for empresa, user in ((self.empresa, self.user), (self.empresa_b, self.user_b)):
            EmpresaContrato.objects.update_or_create(
                empresa=empresa,
                defaults={"status": EmpresaContrato.STATUS_ATIVO, "plano_completo": True, "usuario_master": user},
            )
            EmpresaModulo.objects.update_or_create(
                empresa=empresa,
                modulo=self.produtos_modulo,
                defaults={"contratado": True},
            )
        UserModulePermission.objects.create(user=self.user, modulo=UserModulePermission.Module.PRODUTOS, acesso=UserModulePermission.Access.EDIT)
        UserModulePermission.objects.create(user=self.user_b, modulo=UserModulePermission.Module.PRODUTOS, acesso=UserModulePermission.Access.EDIT)
        self.client.force_authenticate(self.user)
        self.unidade = Unidade.objects.create(empresa=self.empresa, Descricao="Unidade", Codigo="UN")
        self.unidade_b = Unidade.objects.create(empresa=self.empresa_b, Descricao="Unidade B", Codigo="UNB")
        self.produto = self.criar_produto(self.empresa, self.unidade, "Papel A4", "PAPEL")
        self.produto_2 = self.criar_produto(self.empresa, self.unidade, "Caneta Azul", "CANETA")
        self.produto_b = self.criar_produto(self.empresa_b, self.unidade_b, "Papel B", "PAPEL B")
        self.fornecedor = self.criar_fornecedor(self.empresa, "Fornecedor A", "33222333000191")
        self.fornecedor_2 = self.criar_fornecedor(self.empresa, "Fornecedor B", "33222333000272")
        self.fornecedor_b = self.criar_fornecedor(self.empresa_b, "Fornecedor C", "33222333000353")

    def criar_fornecedor(self, empresa, nome, documento):
        return Fornecedor.objects.create(
            empresa=empresa,
            tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA,
            documento=documento,
            cnpj=documento,
            nome_fornecedor=nome,
            categoria="OUTROS",
        )

    def criar_produto(self, empresa, unidade, descricao, reduzida):
        return Produto.objects.create(
            empresa=empresa,
            tipo_produto="2",
            descricao=descricao,
            descricao_reduzida=reduzida,
            unidade=unidade,
        )

    def payload(self, fornecedor=None, produto=None, codigo="BAX002", descricao="PAPEL SULFITE A4", gtin="7891234567895"):
        return {
            "empresa": self.empresa.pk,
            "fornecedor": (fornecedor or self.fornecedor).pk,
            "produto": (produto or self.produto).pk,
            "codigo_produto_fornecedor": codigo,
            "descricao_fornecedor": descricao,
            "gtin_ean": gtin,
        }

    def post_vinculo(self, payload=None, status_code=201):
        resp = self.client.post("/api/produto/produto-fornecedor/", payload or self.payload(), format="json")
        self.assertEqual(resp.status_code, status_code, resp.data)
        return resp

    def results(self, resp):
        return resp.data["results"] if isinstance(resp.data, dict) and "results" in resp.data else resp.data

    def criar_produto_com_sku(self, referencia_esperada="260101001", colecao_codigo="26", estacao="01"):
        unidade = Unidade.objects.create(empresa=self.empresa, Descricao=f"Unidade {referencia_esperada}", Codigo=f"U{referencia_esperada[-2:]}")
        grade = Grade.objects.create(empresa=self.empresa, Descricao=f"Grade {referencia_esperada}")
        cor = Cor.objects.create(empresa=self.empresa, Descricao=f"Azul {referencia_esperada}", Codigo=f"AZ{referencia_esperada[-2:]}", Cor="Azul")
        tamanho = Tamanho.objects.create(empresa=self.empresa, idgrade=grade, Tamanho=f"M{referencia_esperada[-1]}")
        colecao = Colecao.objects.create(empresa=self.empresa, Descricao=f"Colecao {referencia_esperada}", Codigo=colecao_codigo, Estacao=estacao)
        grupo = Grupo.objects.create(empresa=self.empresa, Codigo=referencia_esperada[-2:], CodigoRef=referencia_esperada[-2:], Descricao=f"Grupo {referencia_esperada}", Margem=0)
        subgrupo = Subgrupo.objects.create(empresa=self.empresa, Idgrupo=grupo, Descricao=f"Subgrupo {referencia_esperada}", Margem=0)
        Ncm.objects.get_or_create(empresa=self.empresa, ncm="6109.10.00", defaults={"descricao": "Produto SKU"})
        ConfigEan.objects.get_or_create(empresa=self.empresa, company_prefix="5555")
        produto = Produto.objects.create(
            empresa=self.empresa,
            tipo_produto="1",
            descricao=f"Produto {referencia_esperada}",
            descricao_reduzida=f"P{referencia_esperada[-3:]}",
            unidade=unidade,
            colecao=colecao,
            grupo=grupo,
            subgrupo=subgrupo,
            grade=grade,
            ncm="6109.10.00",
        )
        sku = ProdutoDetalhe.objects.create(produto=produto, idcor=cor, idtamanho=tamanho)
        return produto, sku, cor, tamanho, colecao

    def test_cria_vinculo_valido_com_descricao_gtin_e_auditoria(self):
        resp = self.post_vinculo()
        vinculo = ProdutoFornecedor.objects.get(pk=resp.data["id"])
        self.assertEqual(vinculo.empresa_id, self.empresa.pk)
        self.assertEqual(vinculo.codigo_produto_fornecedor, "BAX002")
        self.assertEqual(vinculo.codigo_normalizado, "BAX002")
        self.assertEqual(vinculo.codigo_vigente, "BAX002")
        self.assertEqual(vinculo.descricao_fornecedor, "PAPEL SULFITE A4")
        self.assertEqual(vinculo.gtin_ean, "7891234567895")
        self.assertTrue(AuditLog.objects.filter(app_label="produto", model="produtofornecedor", object_id=str(vinculo.pk), action="OBJECT_CREATED").exists())

    def test_permite_varios_fornecedores_e_codigos_para_mesmo_produto(self):
        self.post_vinculo(self.payload(codigo="BAX002"))
        self.post_vinculo(self.payload(fornecedor=self.fornecedor_2, codigo="KT004"))
        self.post_vinculo(self.payload(codigo="BAX003"))
        self.assertEqual(ProdutoFornecedor.objects.filter(produto=self.produto, ativo=True).count(), 3)

    def test_bloqueia_mesmo_fornecedor_codigo_para_produtos_diferentes(self):
        self.post_vinculo(self.payload(codigo=" DUP 001 "))
        resp = self.post_vinculo(self.payload(produto=self.produto_2, codigo="DUP 001"), status_code=400)
        self.assertIn("codigo_produto_fornecedor", resp.data)

    def test_permite_mesmo_codigo_em_fornecedores_e_empresas_diferentes(self):
        self.post_vinculo(self.payload(codigo="ABC"))
        self.post_vinculo(self.payload(fornecedor=self.fornecedor_2, codigo="ABC", produto=self.produto_2))
        self.client.force_authenticate(self.user_b)
        resp = self.client.post(
            "/api/produto/produto-fornecedor/",
            {
                "empresa": self.empresa_b.pk,
                "fornecedor": self.fornecedor_b.pk,
                "produto": self.produto_b.pk,
                "codigo_produto_fornecedor": "ABC",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_rejeita_produto_e_fornecedor_de_outra_empresa(self):
        resp = self.post_vinculo(self.payload(produto=self.produto_b), status_code=400)
        self.assertIn("produto", resp.data)
        resp = self.post_vinculo(self.payload(fornecedor=self.fornecedor_b), status_code=400)
        self.assertIn("fornecedor", resp.data)

    def test_bloqueia_acesso_cruzado_entre_empresas(self):
        resp = self.post_vinculo()
        self.client.force_authenticate(self.user_b)
        detail = self.client.get(f"/api/produto/produto-fornecedor/{resp.data['id']}/")
        self.assertEqual(detail.status_code, 404)
        lista = self.client.get("/api/produto/produto-fornecedor/")
        self.assertEqual(self.results(lista), [])

    def test_pesquisa_por_fornecedor_codigo_e_filtra_por_produto(self):
        self.post_vinculo(self.payload(codigo="COD-1"))
        self.post_vinculo(self.payload(codigo="COD-2", produto=self.produto_2))
        resp = self.client.get("/api/produto/produto-fornecedor/", {"fornecedor": self.fornecedor.pk, "codigo": "COD-1"})
        self.assertEqual([row["codigo_produto_fornecedor"] for row in self.results(resp)], ["COD-1"])
        resp = self.client.get("/api/produto/produto-fornecedor/", {"produto": self.produto_2.pk})
        self.assertEqual([row["produto"] for row in self.results(resp)], [self.produto_2.pk])

    def test_inativa_sem_perder_historico_e_audita(self):
        resp = self.post_vinculo()
        vinculo_id = resp.data["id"]
        resp = self.client.post(f"/api/produto/produto-fornecedor/{vinculo_id}/inativar/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        vinculo = ProdutoFornecedor.objects.get(pk=vinculo_id)
        self.assertFalse(vinculo.ativo)
        self.assertIsNone(vinculo.codigo_vigente)
        self.assertTrue(ProdutoFornecedor.objects.filter(pk=vinculo_id).exists())
        self.assertTrue(AuditLog.objects.filter(app_label="produto", model="produtofornecedor", object_id=str(vinculo_id), action="OBJECT_UPDATED").exists())

    def test_alteracao_do_produto_e_codigo_relevante_e_auditada(self):
        resp = self.post_vinculo()
        vinculo_id = resp.data["id"]
        resp = self.client.patch(
            f"/api/produto/produto-fornecedor/{vinculo_id}/",
            {"produto": self.produto_2.pk, "codigo_produto_fornecedor": "BAX009", "descricao_fornecedor": "Descricao nova"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        vinculo = ProdutoFornecedor.objects.get(pk=vinculo_id)
        self.assertEqual(vinculo.produto_id, self.produto_2.pk)
        self.assertEqual(vinculo.codigo_produto_fornecedor, "BAX009")
        log = AuditLog.objects.filter(app_label="produto", model="produtofornecedor", object_id=str(vinculo_id), action="OBJECT_UPDATED").latest("created_at")
        self.assertIn("produto", log.changed_fields)
        self.assertIn("codigo_produto_fornecedor", log.changed_fields)

    def test_produto_e_fornecedor_existentes_nao_sao_alterados(self):
        descricao_produto = self.produto.descricao
        nome_fornecedor = self.fornecedor.nome_fornecedor
        self.post_vinculo(self.payload(descricao="Descricao externa diferente"))
        self.produto.refresh_from_db()
        self.fornecedor.refresh_from_db()
        self.assertEqual(self.produto.descricao, descricao_produto)
        self.assertEqual(self.fornecedor.nome_fornecedor, nome_fornecedor)

    def test_vinculo_sem_conversao_explicita_continua_valido_com_fator_padrao_um(self):
        resp = self.post_vinculo()
        vinculo = ProdutoFornecedor.objects.get(pk=resp.data["id"])
        self.assertEqual(vinculo.fator_conversao, Decimal("1.000000"))
        self.assertEqual(vinculo.unidade_fornecedor, "")
        self.assertEqual(vinculo.converter_quantidade_fornecedor(Decimal("7")), Decimal("7.000000"))

    def test_cadastra_unidade_externa_e_fator_inteiro(self):
        payload = self.payload()
        payload.update({"unidade_fornecedor": " FARDO ", "fator_conversao": "10"})
        resp = self.post_vinculo(payload)
        vinculo = ProdutoFornecedor.objects.get(pk=resp.data["id"])
        self.assertEqual(vinculo.unidade_fornecedor, "FARDO")
        self.assertEqual(vinculo.fator_conversao, Decimal("10.000000"))
        self.assertEqual(resp.data["unidade_interna"], self.unidade.Codigo)
        self.assertEqual(resp.data["unidade_interna_descricao"], self.unidade.Descricao)

    def test_cadastra_fator_decimal_e_converte_com_decimal_sem_float(self):
        payload = self.payload(codigo="DEC")
        payload.update({"unidade_fornecedor": "PACOTE", "fator_conversao": "0.5"})
        resp = self.post_vinculo(payload)
        vinculo = ProdutoFornecedor.objects.get(pk=resp.data["id"])
        resultado = vinculo.converter_quantidade_fornecedor(Decimal("3"))
        self.assertIsInstance(resultado, Decimal)
        self.assertEqual(resultado, Decimal("1.500000"))

    def test_rejeita_fator_zero_negativo_e_unidade_apenas_espacos(self):
        payload = self.payload(codigo="ZERO")
        payload["fator_conversao"] = "0"
        resp = self.post_vinculo(payload, status_code=400)
        self.assertIn("fator_conversao", resp.data)

        payload = self.payload(codigo="NEG")
        payload["fator_conversao"] = "-1"
        resp = self.post_vinculo(payload, status_code=400)
        self.assertIn("fator_conversao", resp.data)

        payload = self.payload(codigo="ESP")
        payload["unidade_fornecedor"] = "   "
        resp = self.post_vinculo(payload, status_code=400)
        self.assertIn("unidade_fornecedor", resp.data)

    def test_conversao_tres_vezes_dez_resulta_em_trinta(self):
        payload = self.payload(codigo="FARDO")
        payload.update({"unidade_fornecedor": "FARDO", "fator_conversao": "10"})
        resp = self.post_vinculo(payload)
        vinculo = ProdutoFornecedor.objects.get(pk=resp.data["id"])
        self.assertEqual(vinculo.converter_quantidade_fornecedor(Decimal("3")), Decimal("30.000000"))

    def test_alterar_unidade_externa_e_fator_nao_altera_produto_e_audita_before_after(self):
        resp = self.post_vinculo(self.payload(codigo="ALT"))
        vinculo_id = resp.data["id"]
        unidade_interna_id = self.produto.unidade_id
        descricao_produto = self.produto.descricao
        resp = self.client.patch(
            f"/api/produto/produto-fornecedor/{vinculo_id}/",
            {"unidade_fornecedor": "CX", "fator_conversao": "24"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.produto.refresh_from_db()
        self.assertEqual(self.produto.unidade_id, unidade_interna_id)
        self.assertEqual(self.produto.descricao, descricao_produto)
        log = AuditLog.objects.filter(app_label="produto", model="produtofornecedor", object_id=str(vinculo_id), action="OBJECT_UPDATED").latest("created_at")
        self.assertIn("unidade_fornecedor", log.changed_fields)
        self.assertIn("fator_conversao", log.changed_fields)
        self.assertEqual(log.before_data["unidade_fornecedor"], "")
        self.assertEqual(log.after_data["unidade_fornecedor"], "CX")
        self.assertEqual(log.before_data["fator_conversao"], "1.000000")
        self.assertEqual(log.after_data["fator_conversao"], "24.000000")

    def test_fornecedores_podem_ter_fatores_diferentes_para_mesmo_codigo(self):
        payload_a = self.payload(codigo="CX")
        payload_a.update({"unidade_fornecedor": "CX", "fator_conversao": "10"})
        payload_b = self.payload(fornecedor=self.fornecedor_2, codigo="CX")
        payload_b.update({"unidade_fornecedor": "CX", "fator_conversao": "24"})
        self.post_vinculo(payload_a)
        self.post_vinculo(payload_b)
        fatores = list(ProdutoFornecedor.objects.filter(codigo_produto_fornecedor="CX").order_by("fornecedor_id").values_list("fator_conversao", flat=True))
        self.assertEqual(fatores, [Decimal("10.000000"), Decimal("24.000000")])

    def test_filtros_continuam_funcionando_com_campos_de_conversao(self):
        payload = self.payload(codigo="BUSCA")
        payload.update({"unidade_fornecedor": "FD", "fator_conversao": "10"})
        self.post_vinculo(payload)
        resp = self.client.get("/api/produto/produto-fornecedor/", {"fornecedor": self.fornecedor.pk, "codigo": "BUSCA"})
        rows = self.results(resp)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["unidade_fornecedor"], "FD")
        self.assertEqual(Decimal(rows[0]["fator_conversao"]), Decimal("10.000000"))


@override_settings(ALLOWED_HOSTS=["testserver"])
class ProdutoVendaApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa = Empresa.objects.create(nome="Empresa A", documento="11222333000181", plano_completo=True)
        self.outra_empresa = Empresa.objects.create(nome="Empresa B", documento="11222333000182", plano_completo=True)
        self.user = get_user_model().objects.create_user(
            "admin",
            "admin@example.com",
            "123",
            empresa=self.empresa,
            type="Admin",
        )
        self.produtos_modulo, _ = ModuloSistema.objects.get_or_create(
            chave="produtos",
            defaults={"nome": "Produtos", "categoria": ModuloSistema.CATEGORIA_BASICO, "basico": True},
        )
        EmpresaContrato.objects.update_or_create(
            empresa=self.empresa,
            defaults={"status": EmpresaContrato.STATUS_ATIVO, "plano_completo": True, "usuario_master": self.user},
        )
        EmpresaModulo.objects.update_or_create(
            empresa=self.empresa,
            modulo=self.produtos_modulo,
            defaults={"contratado": True},
        )
        self.estoque_modulo, _ = ModuloSistema.objects.get_or_create(
            chave="estoque",
            defaults={"nome": "Estoque", "categoria": ModuloSistema.CATEGORIA_BASICO, "basico": True},
        )
        EmpresaModulo.objects.update_or_create(
            empresa=self.empresa,
            modulo=self.estoque_modulo,
            defaults={"contratado": True},
        )
        UserModulePermission.objects.create(user=self.user, modulo=UserModulePermission.Module.PRODUTOS, acesso=UserModulePermission.Access.EDIT)
        UserModulePermission.objects.create(user=self.user, modulo=UserModulePermission.Module.ESTOQUE, acesso=UserModulePermission.Access.VIEW)
        self.client.force_authenticate(self.user)
        self.unidade = Unidade.objects.create(empresa=self.empresa, Descricao="Unidade", Codigo="UN")
        self.colecao = Colecao.objects.create(empresa=self.empresa, Descricao="Colecao", Codigo="26", Estacao="01")
        self.grupo = Grupo.objects.create(empresa=self.empresa, Codigo="01", CodigoRef="01", Descricao="Grupo", Margem=0)
        self.subgrupo = Subgrupo.objects.create(empresa=self.empresa, Idgrupo=self.grupo, Descricao="Subgrupo", Margem=0)
        self.grade = Grade.objects.create(empresa=self.empresa, Descricao="Grade")
        self.tam_p = Tamanho.objects.create(empresa=self.empresa, idgrade=self.grade, Tamanho="P")
        self.tam_m = Tamanho.objects.create(empresa=self.empresa, idgrade=self.grade, Tamanho="M")
        self.cor_azul = Cor.objects.create(empresa=self.empresa, Descricao="Azul", Codigo="AZ", Cor="Azul")
        self.cor_preta = Cor.objects.create(empresa=self.empresa, Descricao="Preta", Codigo="PR", Cor="Preta")
        self.ncm = Ncm.objects.create(empresa=self.empresa, ncm="6109.10.00", descricao="Camiseta")
        self.ncm2 = Ncm.objects.create(empresa=self.empresa, ncm="6204.42.00", descricao="Vestido")
        self.config = ConfigEan.objects.create(empresa=self.empresa, company_prefix="1234")
        self.loja = Loja.objects.create(
            empresa=self.empresa,
            nome_loja="Loja 1",
            apelido_loja="L1",
            cnpj="11222333000181",
        )

    def produto(self, **kwargs):
        defaults = {
            "empresa": self.empresa,
            "tipo_produto": "1",
            "descricao": "Produto Venda",
            "descricao_reduzida": "PV001",
            "unidade": self.unidade,
            "grupo": self.grupo,
            "subgrupo": self.subgrupo,
            "colecao": self.colecao,
            "grade": self.grade,
            "ncm": self.ncm.ncm,
        }
        defaults.update(kwargs)
        return Produto.objects.create(**defaults)

    def test_produto_uso_consumo_nao_depende_de_matriz_e_nao_expoe_controla_estoque(self):
        Loja.objects.filter(empresa=self.empresa).delete()
        resp = self.client.post("/api/produto/produto/", {
            "tipo_produto": "2",
            "descricao": "Papel A4",
            "descricao_reduzida": "PAPEL A4",
            "unidade": self.unidade.pk,
            "controla_estoque": True,
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        produto = Produto.objects.get(pk=resp.data["Idproduto"])
        self.assertEqual(produto.referencia, "USO-000001")
        self.assertFalse(hasattr(produto, "controla_estoque"))
        self.assertNotIn("controla_estoque", resp.data)
        self.assertTrue(ProdutoUsoConsumoHistorico.objects.filter(produto=produto, tipo_evento=ProdutoUsoConsumoHistorico.CRIACAO).exists())

    def test_consulta_estoque_uso_consumo_filtra_tipo_produto_loja_saldo_e_referencia(self):
        loja_filial = Loja.objects.create(
            empresa=self.empresa,
            nome_loja="Loja 2",
            apelido_loja="L2",
            cnpj="11222333000183",
        )
        produto_uso = self.produto(tipo_produto="2", descricao="Papel A4", descricao_reduzida="PAPEL")
        produto_uso_inativo = self.produto(
            tipo_produto="2",
            descricao="Tonner inativo",
            descricao_reduzida="TONNER",
            ativo=False,
        )
        produto_venda = self.produto(descricao="Produto venda")
        ProdutoUsoConsumoEstoque.objects.create(empresa=self.empresa, produto=produto_uso, loja=self.loja, saldo="7.000")
        ProdutoUsoConsumoEstoque.objects.create(empresa=self.empresa, produto=produto_uso, loja=loja_filial, saldo="0.000")
        ProdutoUsoConsumoEstoque.objects.create(empresa=self.empresa, produto=produto_uso_inativo, loja=self.loja, saldo="3.000")
        ProdutoUsoConsumoEstoque.objects.create(empresa=self.empresa, produto=produto_venda, loja=self.loja, saldo="99.000")

        resp = self.client.get("/api/produto/produto-uso-consumo-estoque/", {"search": produto_uso.referencia})
        self.assertEqual(resp.status_code, 200, resp.content)
        results = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        self.assertEqual({row["produto_tipo"] for row in results}, {"2"})
        self.assertEqual({row["loja"] for row in results}, {self.loja.id, loja_filial.id})

        resp_loja = self.client.get("/api/produto/produto-uso-consumo-estoque/", {"loja": self.loja.id})
        results_loja = resp_loja.data["results"] if isinstance(resp_loja.data, dict) else resp_loja.data
        self.assertEqual({row["loja"] for row in results_loja}, {self.loja.id})
        self.assertEqual({row["produto"] for row in results_loja}, {produto_uso.Idproduto, produto_uso_inativo.Idproduto})

        resp_com_saldo = self.client.get("/api/produto/produto-uso-consumo-estoque/", {"saldo": "com_saldo"})
        results_com_saldo = resp_com_saldo.data["results"] if isinstance(resp_com_saldo.data, dict) else resp_com_saldo.data
        self.assertEqual({row["produto"] for row in results_com_saldo}, {produto_uso.Idproduto, produto_uso_inativo.Idproduto})
        row_inativo = next(row for row in results_com_saldo if row["produto"] == produto_uso_inativo.Idproduto)
        self.assertFalse(row_inativo["produto_ativo"])

        resp_zerados = self.client.get("/api/produto/produto-uso-consumo-estoque/", {"saldo": "zerados"})
        results_zerados = resp_zerados.data["results"] if isinstance(resp_zerados.data, dict) else resp_zerados.data
        self.assertEqual([row["loja"] for row in results_zerados], [loja_filial.id])

    def test_consulta_movimentacao_uso_consumo_filtra_tipo_produto_referencia_e_loja(self):
        loja_filial = Loja.objects.create(
            empresa=self.empresa,
            nome_loja="Loja 2",
            apelido_loja="L2",
            cnpj="11222333000183",
        )
        produto_uso = self.produto(tipo_produto="2", descricao="Papel A4", descricao_reduzida="PAPEL")
        produto_venda = self.produto(descricao="Produto venda")
        ProdutoUsoConsumoEstoque.objects.create(empresa=self.empresa, produto=produto_uso, loja=self.loja, saldo="7.000")
        ProdutoUsoConsumoMovimentacao.objects.create(
            empresa=self.empresa,
            produto=produto_uso,
            loja=self.loja,
            tipo=ProdutoUsoConsumoMovimentacao.TIPO_ENTRADA,
            quantidade="7.000",
            saldo_anterior="0.000",
            saldo_posterior="7.000",
            documento="NFE:21:ENTRADA",
            origem="NFE:21",
            destino=self.loja.nome_loja,
        )
        ProdutoUsoConsumoMovimentacao.objects.create(
            empresa=self.empresa,
            produto=produto_venda,
            loja=loja_filial,
            tipo=ProdutoUsoConsumoMovimentacao.TIPO_ENTRADA,
            quantidade="99.000",
            saldo_anterior="0.000",
            saldo_posterior="99.000",
            documento="VENDA",
        )

        resp = self.client.get("/api/produto/produto-uso-consumo-movimentacao/", {"search": produto_uso.referencia})
        self.assertEqual(resp.status_code, 200, resp.content)
        results = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["produto_tipo"], "2")
        self.assertEqual(results[0]["loja"], self.loja.id)
        self.assertEqual(results[0]["quantidade"], "7.000")
        self.assertEqual(results[0]["saldo_posterior"], "7.000")

        resp_loja = self.client.get("/api/produto/produto-uso-consumo-movimentacao/", {"loja": loja_filial.id})
        results_loja = resp_loja.data["results"] if isinstance(resp_loja.data, dict) else resp_loja.data
        self.assertEqual(results_loja, [])

    def test_produto_uso_consumo_multiplas_matrizes_nao_bloqueiam_cadastro(self):
        Loja.objects.create(empresa=self.empresa, nome_loja="Matriz A", apelido_loja="MA", cnpj="11222333000183", tipo_unidade=Loja.TIPO_MATRIZ)
        Loja.objects.create(empresa=self.empresa, nome_loja="Matriz B", apelido_loja="MB", cnpj="11222333000184", tipo_unidade=Loja.TIPO_MATRIZ)
        resp = self.client.post("/api/produto/produto/", {
            "tipo_produto": "2",
            "descricao": "Toner",
            "descricao_reduzida": "TONER",
            "unidade": self.unidade.pk,
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.data["referencia"], "USO-000001")

    def test_insumo_cria_codigo_ins_material_fiscal_e_historico_proprio(self):
        material = Material.objects.create(empresa=self.empresa, Descricao="Algodão", Codigo="ALG")
        resp = self.client.post("/api/produto/produto/", {
            "tipo_produto": "4",
            "descricao": "Tecido Algodão",
            "descricao_reduzida": "TEC ALG",
            "unidade": self.unidade.pk,
            "material": material.pk,
            "ncm": self.ncm.ncm,
            "grade": None,
            "colecao": None,
            "grupo": None,
            "subgrupo": None,
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        produto = Produto.objects.get(pk=resp.data["Idproduto"])
        self.assertEqual(produto.tipo_produto, "4")
        self.assertEqual(produto.referencia, "INS-001")
        self.assertEqual(produto.material_id, material.pk)
        self.assertIsNone(produto.grade_id)
        self.assertIsNone(produto.colecao_id)
        self.assertIsNone(produto.grupo_id)
        self.assertIsNone(produto.subgrupo_id)
        self.assertFalse(resp.data["cadastro_fiscal_incompleto"])
        self.assertTrue(ProdutoInsumoHistorico.objects.filter(produto=produto, tipo_evento=ProdutoInsumoHistorico.CRIACAO).exists())

    def test_insumo_rejeita_material_de_outra_empresa_e_tipo_imutavel(self):
        material_outra = Material.objects.create(empresa=self.outra_empresa, Descricao="Metal", Codigo="MET")
        resp = self.client.post("/api/produto/produto/", {
            "tipo_produto": "4",
            "descricao": "Botão",
            "descricao_reduzida": "BOTAO",
            "unidade": self.unidade.pk,
            "material": material_outra.pk,
        }, format="json")
        self.assertEqual(resp.status_code, 400)

        produto = Produto.objects.create(empresa=self.empresa, tipo_produto="4", descricao="Linha", descricao_reduzida="LINHA", unidade=self.unidade)
        resp = self.client.patch(f"/api/produto/produto/{produto.pk}/", {"tipo_produto": "2"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_auxiliares_grupo_validacoes_multiempresa_e_auditoria(self):
        resp = self.client.post("/api/produto/grupo/", {"Codigo": "10", "CodigoRef": "1", "Descricao": "Jeans", "Margem": 10}, format="json")
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post("/api/produto/grupo/", {"Codigo": "10", "CodigoRef": "AA", "Descricao": "Jeans", "Margem": 10}, format="json")
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post("/api/produto/grupo/", {"Codigo": "10", "CodigoRef": "10", "Descricao": "Jeans", "Margem": 10}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        resp = self.client.post("/api/produto/grupo/", {"Codigo": "10", "CodigoRef": "11", "Descricao": "Malha", "Margem": 10}, format="json")
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post("/api/produto/grupo/", {"Codigo": "11", "CodigoRef": "10", "Descricao": "Malha", "Margem": 10}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(AuditLog.objects.filter(model="grupo").exists())

    def test_auxiliares_subgrupo_grade_tamanho_packitem(self):
        grupo_b = Grupo.objects.create(empresa=self.outra_empresa, Codigo="20", CodigoRef="20", Descricao="Outra", Margem=0)
        resp = self.client.post("/api/produto/subgrupo/", {"Idgrupo": grupo_b.pk, "Descricao": "Skinny", "Margem": 0}, format="json")
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post("/api/produto/subgrupo/", {"Idgrupo": self.grupo.pk, "Descricao": "Skinny", "Margem": 0}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        resp = self.client.post("/api/produto/subgrupo/", {"Idgrupo": self.grupo.pk, "Descricao": "Skinny", "Margem": 1}, format="json")
        self.assertEqual(resp.status_code, 400)

        resp = self.client.post("/api/produto/grade/", {"Descricao": "Adulto", "Status": "ATIVO"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        grade_id = resp.data["Idgrade"]
        resp = self.client.post("/api/produto/tamanho/", {"idgrade": grade_id, "Tamanho": "M", "Status": "ATIVO"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        tamanho_id = resp.data["Idtamanho"]
        resp = self.client.post("/api/produto/tamanho/", {"idgrade": grade_id, "Tamanho": "M", "Status": "ATIVO"}, format="json")
        self.assertEqual(resp.status_code, 400)
        grade_outra = Grade.objects.create(empresa=self.outra_empresa, Descricao="Outra", Status="ATIVO")
        resp = self.client.post("/api/produto/tamanho/", {"idgrade": grade_outra.pk, "Tamanho": "P", "Status": "ATIVO"}, format="json")
        self.assertEqual(resp.status_code, 400)

        resp = self.client.post("/api/produto/pack/", {"nome": "", "grade": grade_id, "ativo": True}, format="json")
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post("/api/produto/pack/", {"nome": "Pack Adulto", "grade": grade_id, "ativo": True}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        pack_id = resp.data["id"]
        resp = self.client.post("/api/produto/pack-item/", {"pack": pack_id, "tamanho": tamanho_id, "qtd": 0}, format="json")
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post("/api/produto/pack-item/", {"pack": pack_id, "tamanho": tamanho_id, "qtd": 2}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        resp = self.client.post("/api/produto/pack-item/", {"pack": pack_id, "tamanho": tamanho_id, "qtd": 1}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_auxiliares_colecao_unidade_cor_material_e_exclusao_protegida(self):
        resp = self.client.post("/api/produto/colecao/", {"Codigo": "2", "Estacao": "01", "Status": "AT", "Descricao": "Verão", "Contador": 99}, format="json")
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post("/api/produto/colecao/", {"Codigo": "27", "Estacao": "09", "Status": "AT", "Descricao": "Verão"}, format="json")
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post("/api/produto/colecao/", {"Codigo": "27", "Estacao": "01", "Status": "AT", "Descricao": "Verão", "Contador": 99}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.data["Contador"], 0)

        resp = self.client.post("/api/produto/unidade/", {"Codigo": "kg", "Descricao": "Quilo", "permite_decimal": True}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.data["Codigo"], "KG")
        resp = self.client.post("/api/produto/unidade/", {"Codigo": "KG", "Descricao": "Quilograma", "permite_decimal": True}, format="json")
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post("/api/produto/cor/", {"Descricao": "Azul Royal", "Codigo": "AZR", "Cor": "Azul", "Status": "ATIVO"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        resp = self.client.post("/api/produto/cor/", {"Descricao": "Azul Repetida", "Codigo": "AZR", "Cor": "Azul", "Status": "ATIVO"}, format="json")
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post("/api/produto/material/", {"Descricao": "Algodão", "Codigo": "alg", "Status": "ATIVO"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.data["Codigo"], "ALG")

        produto = self.produto(grupo=self.grupo)
        resp = self.client.delete(f"/api/produto/grupo/{self.grupo.pk}/")
        self.assertEqual(resp.status_code, 400)

    def test_edicao_uso_consumo_persiste_no_banco_get_e_historico_diferencas(self):
        outra_unidade = Unidade.objects.create(empresa=self.empresa, Descricao="Pacote", Codigo="PCT")
        produto = Produto.objects.create(
            empresa=self.empresa,
            tipo_produto="2",
            descricao="Papel A4",
            descricao_reduzida="PAPEL",
            unidade=self.unidade,
            observacoes="Observação original",
            ncm=self.ncm.ncm,
        )

        payload = {
            "tipo_produto": "2",
            "descricao": "Papel A4 75g",
            "descricao_reduzida": "PAPEL A4",
            "unidade": outra_unidade.pk,
            "observacoes": "Observação alterada",
            "ncm": self.ncm2.ncm,
        }
        resp = self.client.patch(f"/api/produto/produto/{produto.pk}/", payload, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["descricao"], "Papel A4 75g")
        self.assertEqual(resp.data["descricao_reduzida"], "PAPEL A4")
        self.assertEqual(resp.data["unidade"], outra_unidade.pk)
        self.assertEqual(resp.data["observacoes"], "Observação alterada")
        self.assertEqual(resp.data["ncm"], self.ncm2.ncm)

        produto_banco = Produto.objects.get(pk=produto.pk)
        produto_banco.refresh_from_db()
        self.assertEqual(produto_banco.descricao, "Papel A4 75g")
        self.assertEqual(produto_banco.descricao_reduzida, "PAPEL A4")
        self.assertEqual(produto_banco.unidade_id, outra_unidade.pk)
        self.assertEqual(produto_banco.observacoes, "Observação alterada")
        self.assertEqual(produto_banco.ncm, self.ncm2.ncm)

        get_resp = self.client.get(f"/api/produto/produto/{produto.pk}/")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.data["descricao"], "Papel A4 75g")
        self.assertEqual(get_resp.data["descricao_reduzida"], "PAPEL A4")
        self.assertEqual(get_resp.data["unidade"], outra_unidade.pk)
        self.assertEqual(get_resp.data["observacoes"], "Observação alterada")
        self.assertEqual(get_resp.data["ncm"], self.ncm2.ncm)

        cadastral = ProdutoUsoConsumoHistorico.objects.get(produto=produto, tipo_evento=ProdutoUsoConsumoHistorico.ALTERACAO_CADASTRAL)
        self.assertEqual(set(cadastral.dados_anteriores.keys()), {"descricao", "descricao_reduzida", "unidade", "observacoes"})
        self.assertEqual(cadastral.dados_anteriores["descricao"], "Papel A4")
        self.assertEqual(cadastral.dados_novos["descricao"], "Papel A4 75g")
        self.assertEqual(cadastral.dados_anteriores["descricao_reduzida"], "PAPEL")
        self.assertEqual(cadastral.dados_novos["descricao_reduzida"], "PAPEL A4")
        self.assertEqual(cadastral.dados_anteriores["unidade"], self.unidade.pk)
        self.assertEqual(cadastral.dados_novos["unidade"], outra_unidade.pk)
        self.assertEqual(cadastral.dados_anteriores["observacoes"], "Observação original")
        self.assertEqual(cadastral.dados_novos["observacoes"], "Observação alterada")

        fiscal = ProdutoUsoConsumoHistorico.objects.get(produto=produto, tipo_evento=ProdutoUsoConsumoHistorico.ALTERACAO_FISCAL)
        self.assertEqual(set(fiscal.dados_anteriores.keys()), {"ncm"})
        self.assertEqual(fiscal.dados_anteriores["ncm"], self.ncm.ncm)
        self.assertEqual(fiscal.dados_novos["ncm"], self.ncm2.ncm)

    def sync_cores(self, produto, cores):
        return self.client.post(f"/api/produto/produto/{produto.pk}/gerar-skus/", {"cores": cores}, format="json")

    def test_auditoria_e_historico_em_alteracoes_reais_sem_evento_extra(self):
        produto = self.produto()

        resp = self.client.patch(f"/api/produto/produto/{produto.pk}/", {"descricao": "Produto Venda Novo"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(ProdutoVendaHistorico.objects.filter(produto=produto, tipo_evento=ProdutoVendaHistorico.ALTERACAO_CADASTRAL).exists())
        self.assertTrue(AuditLog.objects.filter(model="produto", object_id=str(produto.pk), metadata__legacy_action="update_cadastral").exists())

        resp = self.client.patch(f"/api/produto/produto/{produto.pk}/", {"ncm": self.ncm2.ncm}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(ProdutoVendaHistorico.objects.filter(produto=produto, tipo_evento=ProdutoVendaHistorico.ALTERACAO_FISCAL).exists())
        self.assertTrue(AuditLog.objects.filter(model="produto", object_id=str(produto.pk), metadata__legacy_action="update_fiscal").exists())

        hist_count = ProdutoVendaHistorico.objects.filter(produto=produto).count()
        audit_count = AuditLog.objects.filter(model="produto", object_id=str(produto.pk)).count()
        resp = self.client.patch(f"/api/produto/produto/{produto.pk}/", {"descricao": "Produto Venda Novo"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ProdutoVendaHistorico.objects.filter(produto=produto).count(), hist_count)
        self.assertEqual(AuditLog.objects.filter(model="produto", object_id=str(produto.pk)).count(), audit_count)

    def test_sincronizacao_cores_inativa_ultima_cor_e_preserva_identificadores(self):
        produto = self.produto()
        resp = self.sync_cores(produto, [self.cor_azul.pk])
        self.assertEqual(resp.status_code, 200)
        sku = ProdutoDetalhe.objects.get(produto=produto, idcor=self.cor_azul, idtamanho=self.tam_p)
        ean = sku.ean13
        item_ref = sku.codigo_item_ref

        resp = self.sync_cores(produto, [])
        self.assertEqual(resp.status_code, 200)
        sku.refresh_from_db()
        self.assertFalse(sku.ativo)
        self.assertEqual(sku.ean13, ean)
        self.assertEqual(sku.codigo_item_ref, item_ref)
        self.assertEqual(ProdutoDetalhe.objects.filter(produto=produto).count(), 2)

        resp = self.sync_cores(produto, [self.cor_azul.pk])
        self.assertEqual(resp.status_code, 200)
        sku.refresh_from_db()
        self.assertTrue(sku.ativo)
        self.assertEqual(sku.ean13, ean)
        self.assertEqual(sku.codigo_item_ref, item_ref)

    def test_exclusao_remove_estoque_zerado_inicial_e_bloqueia_movimentacao(self):
        produto = self.produto()
        self.sync_cores(produto, [self.cor_azul.pk])
        self.client.post(f"/api/produto/produto/{produto.pk}/inicializar-estoque/", {"lojas": [self.loja.pk]}, format="json")
        eans = list(ProdutoDetalhe.objects.filter(produto=produto).values_list("ean13", flat=True))
        self.assertEqual(Estoque.objects.filter(CodigodeBarra__in=eans).count(), 2)

        resp = self.client.delete(f"/api/produto/produto/{produto.pk}/")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Produto.objects.filter(pk=produto.pk).exists())
        self.assertFalse(Estoque.objects.filter(CodigodeBarra__in=eans).exists())

        usado = self.produto(descricao="Produto Usado", descricao_reduzida="USADO")
        self.sync_cores(usado, [self.cor_preta.pk])
        sku = ProdutoDetalhe.objects.filter(produto=usado).first()
        EstoqueMovimentacao.objects.create(Idloja=self.loja, CodigodeBarra=sku.ean13, referencia=usado.referencia or "", tipo=EstoqueMovimentacao.TIPO_ENTRADA, quantidade=1)
        resp = self.client.delete(f"/api/produto/produto/{usado.pk}/")
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(Produto.objects.filter(pk=usado.pk).exists())

    def test_filtros_especificos_e_isolamento_empresa(self):
        p1 = self.produto(descricao="Camisa Azul", descricao_reduzida="COD15")
        p2 = self.produto(descricao="Vestido Preto", descricao_reduzida="COD99", bloqueado_venda=True)
        unidade_b = Unidade.objects.create(empresa=self.outra_empresa, Descricao="Unidade B", Codigo="UN")
        colecao_b = Colecao.objects.create(empresa=self.outra_empresa, Descricao="Colecao B", Codigo="27", Estacao="02")
        grupo_b = Grupo.objects.create(empresa=self.outra_empresa, Codigo="02", CodigoRef="02", Descricao="Grupo B", Margem=0)
        subgrupo_b = Subgrupo.objects.create(empresa=self.outra_empresa, Idgrupo=grupo_b, Descricao="Subgrupo B", Margem=0)
        grade_b = Grade.objects.create(empresa=self.outra_empresa, Descricao="Grade B")
        Ncm.objects.create(empresa=self.outra_empresa, ncm="6109.10.00", descricao="Camiseta B")
        Produto.objects.create(empresa=self.outra_empresa, tipo_produto="1", descricao="Outro Tenant", descricao_reduzida="COD15", unidade=unidade_b, grupo=grupo_b, subgrupo=subgrupo_b, colecao=colecao_b, grade=grade_b, ncm="6109.10.00")

        resp = self.client.get("/api/produto/produto/", {"search": "Camisa", "referencia": p1.referencia, "codigo": "COD15", "grupo": self.grupo.pk, "colecao": self.colecao.pk, "ativo": "true"})
        ids = [row["Idproduto"] for row in resp.data["results"]]
        self.assertIn(p1.pk, ids)
        self.assertNotIn(p2.pk, ids)

        resp = self.client.get("/api/produto/produto/", {"bloqueado_venda": "true"})
        ids = [row["Idproduto"] for row in resp.data["results"]]
        self.assertIn(p2.pk, ids)

    def test_historico_read_only_ordenado_e_imagem_regras(self):
        produto = self.produto()
        ProdutoVendaHistorico.objects.create(empresa=self.empresa, produto=produto, tipo_evento=ProdutoVendaHistorico.CRIACAO, descricao="1")
        ProdutoVendaHistorico.objects.create(empresa=self.empresa, produto=produto, tipo_evento=ProdutoVendaHistorico.ALTERACAO_CADASTRAL, descricao="2")

        resp = self.client.get(f"/api/produto/produto/{produto.pk}/historico/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["results"][0]["descricao"], "2")
        resp = self.client.post(f"/api/produto/produto/{produto.pk}/historico/", {}, format="json")
        self.assertEqual(resp.status_code, 405)

        for idx in range(3):
            resp = self.client.post(
                "/api/produto/produto-imagem/",
                {"produto": produto.pk, "ordem": idx + 1, "principal": idx == 1, "imagem": SimpleUploadedFile(f"img{idx}.jpg", b"img", content_type="image/jpeg")},
                format="multipart",
            )
            self.assertEqual(resp.status_code, 201)
        self.assertEqual(ProdutoImagem.objects.filter(produto=produto).count(), 3)
        self.assertEqual(ProdutoImagem.objects.filter(produto=produto, principal=True).count(), 1)
        resp = self.client.post(
            "/api/produto/produto-imagem/",
            {"produto": produto.pk, "ordem": 4, "imagem": SimpleUploadedFile("img4.jpg", b"img", content_type="image/jpeg")},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)
        principal = ProdutoImagem.objects.get(produto=produto, principal=True)
        self.client.delete(f"/api/produto/produto-imagem/{principal.pk}/")
        self.assertFalse(ProdutoImagem.objects.filter(produto=produto, principal=True).exists())

    def test_admin_funcional_pode_alternar_flags_e_usuario_sem_permissao_recebe_403(self):
        produto = self.produto()

        resp = self.client.post(f"/api/produto/produto/{produto.pk}/inativar/", {"motivo": "Homologacao", "senha": "123"}, format="json")
        self.assertEqual(resp.status_code, 200)
        produto.refresh_from_db()
        self.assertFalse(produto.ativo)

        resp = self.client.post(f"/api/produto/produto/{produto.pk}/ativar/", {}, format="json")
        self.assertEqual(resp.status_code, 200)
        produto.refresh_from_db()
        self.assertTrue(produto.ativo)

        resp = self.client.post(f"/api/produto/produto/{produto.pk}/bloquear-venda/", {"motivo": "Homologacao", "senha": "123"}, format="json")
        self.assertEqual(resp.status_code, 200)
        produto.refresh_from_db()
        self.assertTrue(produto.bloqueado_venda)

        resp = self.client.post(f"/api/produto/produto/{produto.pk}/desbloquear-venda/", {}, format="json")
        self.assertEqual(resp.status_code, 200)
        produto.refresh_from_db()
        self.assertFalse(produto.bloqueado_venda)

        comum = get_user_model().objects.create_user("comum", "comum@example.com", "123", empresa=self.empresa, type="Regular")
        UserModulePermission.objects.create(user=comum, modulo=UserModulePermission.Module.PRODUTOS, acesso=UserModulePermission.Access.VIEW)
        self.client.force_authenticate(comum)
        resp = self.client.post(f"/api/produto/produto/{produto.pk}/ativar/", {}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_senha_invalida_e_motivo_ausente_continuam_rejeitados(self):
        produto = self.produto()

        resp = self.client.post(f"/api/produto/produto/{produto.pk}/inativar/", {"motivo": "", "senha": "123"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["detail"], "Informe um motivo com pelo menos 3 caracteres.")

        resp = self.client.post(f"/api/produto/produto/{produto.pk}/bloquear-venda/", {"motivo": "Homologacao", "senha": "errada"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["detail"], "Senha inválida.")

    def test_alteracao_campos_fiscais_gera_historico_e_auditoria(self):
        produto = self.produto()
        payload = {
            "origem_mercadoria": 1,
            "csosn_ou_cst_icms": "102",
            "aliquota_icms": "18.00",
            "cfop_venda_dentro": "5102",
            "cfop_venda_fora": "6102",
            "cst_pis": "01",
            "aliq_pis": "1.65",
            "cst_cofins": "01",
            "aliq_cofins": "7.60",
            "ipi_situacao": "50",
            "aliq_ipi": "5.00",
        }

        resp = self.client.patch(f"/api/produto/produto/{produto.pk}/", payload, format="json")
        self.assertEqual(resp.status_code, 200)
        produto.refresh_from_db()
        self.assertEqual(produto.cfop_venda_dentro, "5102")
        self.assertTrue(ProdutoVendaHistorico.objects.filter(produto=produto, tipo_evento=ProdutoVendaHistorico.ALTERACAO_FISCAL).exists())
        self.assertTrue(AuditLog.objects.filter(model="produto", object_id=str(produto.pk), metadata__legacy_action="update_fiscal").exists())


@override_settings(ALLOWED_HOSTS=["testserver"])
class EstoqueAcessoLojaApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa = Empresa.objects.create(nome="Empresa Estoque", documento="55222333000181", plano_completo=True)
        self.outra_empresa = Empresa.objects.create(nome="Empresa Estoque B", documento="55222333000182", plano_completo=True)
        self.loja_1 = Loja.objects.create(empresa=self.empresa, nome_loja="Loja 1", apelido_loja="L1", cnpj="55222333000181")
        self.loja_2 = Loja.objects.create(empresa=self.empresa, nome_loja="Loja 2", apelido_loja="L2", cnpj="55222333000183")
        self.loja_outra_empresa = Loja.objects.create(empresa=self.outra_empresa, nome_loja="Loja B", apelido_loja="LB", cnpj="55222333000182")
        self.master = get_user_model().objects.create_user("estoque-master", "estoque-master@example.com", "123", empresa=self.empresa, type="Admin")
        self.restrito = get_user_model().objects.create_user(
            "estoque-restrito",
            "estoque-restrito@example.com",
            "123",
            empresa=self.empresa,
            loja=self.loja_1,
            type="Regular",
        )
        self.estoque_modulo, _ = ModuloSistema.objects.get_or_create(
            chave="estoque",
            defaults={"nome": "Estoque", "categoria": ModuloSistema.CATEGORIA_BASICO, "basico": True},
        )
        EmpresaContrato.objects.update_or_create(
            empresa=self.empresa,
            defaults={"status": EmpresaContrato.STATUS_ATIVO, "plano_completo": True, "usuario_master": self.master},
        )
        EmpresaContrato.objects.update_or_create(
            empresa=self.outra_empresa,
            defaults={"status": EmpresaContrato.STATUS_ATIVO, "plano_completo": True},
        )
        EmpresaModulo.objects.update_or_create(empresa=self.empresa, modulo=self.estoque_modulo, defaults={"contratado": True})
        EmpresaModulo.objects.update_or_create(empresa=self.outra_empresa, modulo=self.estoque_modulo, defaults={"contratado": True})
        perfil = PerfilAcesso.objects.create(empresa=self.empresa, nome="Consulta Estoque")
        PerfilModuloPermissao.objects.create(perfil=perfil, modulo=self.estoque_modulo, acesso=UserModulePermission.Access.VIEW)
        self.restrito.perfil_principal = perfil
        self.restrito.save(update_fields=["perfil_principal"])
        self.restrito.lojas.add(self.loja_1)
        self.estoque_1 = Estoque.objects.create(CodigodeBarra="7890000000001", referencia="REF-A", Idloja=self.loja_1, Estoque="5.000", reserva="0.000")
        self.estoque_2 = Estoque.objects.create(CodigodeBarra="7890000000002", referencia="REF-A", Idloja=self.loja_2, Estoque="9.000", reserva="0.000")
        self.estoque_outra_empresa = Estoque.objects.create(CodigodeBarra="7890000000003", referencia="REF-B", Idloja=self.loja_outra_empresa, Estoque="11.000", reserva="0.000")
        EstoqueMovimentacao.objects.create(Idloja=self.loja_1, CodigodeBarra=self.estoque_1.CodigodeBarra, referencia="REF-A", tipo=EstoqueMovimentacao.TIPO_ENTRADA, quantidade="5.000", saldo_anterior="0.000", saldo_posterior="5.000", documento="L1")
        EstoqueMovimentacao.objects.create(Idloja=self.loja_2, CodigodeBarra=self.estoque_2.CodigodeBarra, referencia="REF-A", tipo=EstoqueMovimentacao.TIPO_ENTRADA, quantidade="9.000", saldo_anterior="0.000", saldo_posterior="9.000", documento="L2")
        EstoqueMovimentacao.objects.create(Idloja=self.loja_outra_empresa, CodigodeBarra=self.estoque_outra_empresa.CodigodeBarra, referencia="REF-B", tipo=EstoqueMovimentacao.TIPO_ENTRADA, quantidade="11.000", saldo_anterior="0.000", saldo_posterior="11.000", documento="LB")

    def results(self, resp):
        return resp.data["results"] if isinstance(resp.data, dict) and "results" in resp.data else resp.data

    def criar_produto_com_sku(self, referencia_esperada="260101001", colecao_codigo="26", estacao="01"):
        unidade = Unidade.objects.create(empresa=self.empresa, Descricao=f"Unidade {referencia_esperada}", Codigo=f"U{referencia_esperada[-2:]}")
        grade = Grade.objects.create(empresa=self.empresa, Descricao=f"Grade {referencia_esperada}")
        cor = Cor.objects.create(empresa=self.empresa, Descricao=f"Azul {referencia_esperada}", Codigo=f"AZ{referencia_esperada[-2:]}", Cor="Azul")
        tamanho = Tamanho.objects.create(empresa=self.empresa, idgrade=grade, Tamanho=f"M{referencia_esperada[-1]}")
        colecao = Colecao.objects.create(empresa=self.empresa, Descricao=f"Colecao {referencia_esperada}", Codigo=colecao_codigo, Estacao=estacao)
        grupo = Grupo.objects.create(empresa=self.empresa, Codigo=referencia_esperada[-2:], CodigoRef=referencia_esperada[-2:], Descricao=f"Grupo {referencia_esperada}", Margem=0)
        subgrupo = Subgrupo.objects.create(empresa=self.empresa, Idgrupo=grupo, Descricao=f"Subgrupo {referencia_esperada}", Margem=0)
        Ncm.objects.get_or_create(empresa=self.empresa, ncm="6109.10.00", defaults={"descricao": "Produto SKU"})
        ConfigEan.objects.get_or_create(empresa=self.empresa, company_prefix="5555")
        produto = Produto.objects.create(
            empresa=self.empresa,
            tipo_produto="1",
            descricao=f"Produto {referencia_esperada}",
            descricao_reduzida=f"P{referencia_esperada[-3:]}",
            unidade=unidade,
            colecao=colecao,
            grupo=grupo,
            subgrupo=subgrupo,
            grade=grade,
            ncm="6109.10.00",
        )
        sku = ProdutoDetalhe.objects.create(produto=produto, idcor=cor, idtamanho=tamanho)
        return produto, sku, cor, tamanho, colecao

    def test_usuario_master_consulta_todas_as_lojas_da_empresa(self):
        self.client.force_authenticate(self.master)
        resp = self.client.get("/api/produto/estoque/", {"referencia": "REF-A"})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual({row["Idloja"] for row in self.results(resp)}, {self.loja_1.id, self.loja_2.id})

    def test_usuario_restrito_consulta_somente_estoque_da_loja_permitida(self):
        self.client.force_authenticate(self.restrito)
        resp = self.client.get("/api/produto/estoque/", {"referencia": "REF-A"})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual([row["Idloja"] for row in self.results(resp)], [self.loja_1.id])

    def test_usuario_restrito_nao_consulta_estoque_de_loja_nao_autorizada(self):
        self.client.force_authenticate(self.restrito)
        resp = self.client.get("/api/produto/estoque/", {"loja": self.loja_2.id})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.results(resp), [])

    def test_usuario_restrito_nao_consulta_movimentacao_de_loja_nao_autorizada(self):
        self.client.force_authenticate(self.restrito)
        resp = self.client.get("/api/produto/estoque-movimentacao/", {"loja": self.loja_2.id})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.results(resp), [])

    def test_consultas_de_estoque_isolam_empresas(self):
        self.client.force_authenticate(self.master)
        estoque_resp = self.client.get("/api/produto/estoque/")
        mov_resp = self.client.get("/api/produto/estoque-movimentacao/")
        self.assertEqual(estoque_resp.status_code, 200, estoque_resp.content)
        self.assertEqual(mov_resp.status_code, 200, mov_resp.content)
        self.assertNotIn(self.loja_outra_empresa.id, {row["Idloja"] for row in self.results(estoque_resp)})
        self.assertNotIn(self.loja_outra_empresa.id, {row["Idloja"] for row in self.results(mov_resp)})

    def test_consulta_referencia_especifica_isola_empresa_loja_e_retorna_saldos(self):
        produto, sku, cor, tamanho, _ = self.criar_produto_com_sku()
        Estoque.objects.create(CodigodeBarra=sku.ean13, referencia=produto.referencia, Idloja=self.loja_1, Estoque="8.000", reserva="2.000")
        Estoque.objects.create(CodigodeBarra=sku.ean13, referencia=produto.referencia, Idloja=self.loja_2, Estoque="3.000", reserva="0.000")
        Estoque.objects.create(CodigodeBarra=sku.ean13, referencia=produto.referencia, Idloja=self.loja_outra_empresa, Estoque="99.000", reserva="0.000")

        self.client.force_authenticate(self.restrito)
        resp = self.client.get("/api/produto/estoque/consulta-referencia/", {"referencia": produto.referencia})
        self.assertEqual(resp.status_code, 200, resp.content)
        rows = self.results(resp)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["referencia"], produto.referencia)
        self.assertEqual(row["produto"], produto.descricao_reduzida)
        self.assertEqual(row["loja"], self.loja_1.id)
        self.assertEqual(row["cor"], cor.Descricao)
        self.assertEqual(row["tamanho"], tamanho.Tamanho)
        self.assertEqual(row["ean"], sku.ean13)
        self.assertEqual(Decimal(str(row["fisico"])), Decimal("8.000"))
        self.assertEqual(Decimal(str(row["reservado"])), Decimal("2.000"))
        self.assertEqual(Decimal(str(row["disponivel"])), Decimal("6.000"))

        resp_loja = self.client.get("/api/produto/estoque/consulta-referencia/", {"referencia": produto.referencia, "loja": self.loja_2.id})
        self.assertEqual(self.results(resp_loja), [])

        self.client.force_authenticate(self.master)
        resp_ean = self.client.get("/api/produto/estoque/consulta-referencia/", {"ean": sku.ean13, "saldo": "com_saldo"})
        self.assertEqual({row["loja"] for row in self.results(resp_ean)}, {self.loja_1.id, self.loja_2.id})

    def test_consulta_colecao_especifica_filtra_estacao_colecao_referencia_e_saldos(self):
        produto_a, sku_a, _, _, colecao_a = self.criar_produto_com_sku(referencia_esperada="260101001", colecao_codigo="26", estacao="01")
        produto_b, sku_b, _, _, _ = self.criar_produto_com_sku(referencia_esperada="260201002", colecao_codigo="26", estacao="02")
        Estoque.objects.create(CodigodeBarra=sku_a.ean13, referencia=produto_a.referencia, Idloja=self.loja_1, Estoque="8.000", reserva="2.000")
        Estoque.objects.create(CodigodeBarra=sku_a.ean13, referencia=produto_a.referencia, Idloja=self.loja_2, Estoque="0.000", reserva="0.000")
        Estoque.objects.create(CodigodeBarra=sku_b.ean13, referencia=produto_b.referencia, Idloja=self.loja_1, Estoque="5.000", reserva="1.000")

        self.client.force_authenticate(self.master)
        resp_estacao = self.client.get("/api/produto/estoque/consulta-colecao/", {"estacao": "01"})
        self.assertEqual({row["referencia"] for row in self.results(resp_estacao)}, {produto_a.referencia})

        resp_colecao = self.client.get("/api/produto/estoque/consulta-colecao/", {"colecao": colecao_a.Idcolecao})
        self.assertEqual({row["referencia"] for row in self.results(resp_colecao)}, {produto_a.referencia})

        resp_combinado = self.client.get(
            "/api/produto/estoque/consulta-colecao/",
            {"estacao": "01", "colecao": colecao_a.Idcolecao, "referencia": produto_a.referencia, "saldo": "com_saldo"},
        )
        rows = self.results(resp_combinado)
        self.assertEqual([row["loja"] for row in rows], [self.loja_1.id])
        self.assertEqual(Decimal(str(rows[0]["fisico"])), Decimal("8.000"))
        self.assertEqual(Decimal(str(rows[0]["reservado"])), Decimal("2.000"))
        self.assertEqual(Decimal(str(rows[0]["disponivel"])), Decimal("6.000"))

    def test_movimentacao_filtra_por_loja_tipo_datas_e_combinacao(self):
        self.client.force_authenticate(self.master)
        mov_antigo = EstoqueMovimentacao.objects.get(documento="L1")
        mov_saida = EstoqueMovimentacao.objects.get(documento="L2")
        data_recente = timezone.localdate()
        data_antiga = data_recente - timedelta(days=5)
        data_inicio = data_recente - timedelta(days=1)
        data_fim_antiga = data_antiga
        data_fim_recente = data_recente + timedelta(days=1)
        mov_antigo.data_movimento = timezone.make_aware(datetime.combine(data_antiga, time(10, 0)))
        mov_antigo.save(update_fields=["data_movimento"])
        mov_saida.tipo = EstoqueMovimentacao.TIPO_SAIDA
        mov_saida.data_movimento = timezone.make_aware(datetime.combine(data_recente, time(10, 0)))
        mov_saida.save(update_fields=["tipo", "data_movimento"])

        resp_loja = self.client.get("/api/produto/estoque-movimentacao/", {"loja": self.loja_1.id})
        self.assertEqual({row["documento"] for row in self.results(resp_loja)}, {"L1"})

        resp_tipo = self.client.get("/api/produto/estoque-movimentacao/", {"tipo": EstoqueMovimentacao.TIPO_SAIDA})
        self.assertEqual({row["documento"] for row in self.results(resp_tipo)}, {"L2"})

        resp_inicio = self.client.get("/api/produto/estoque-movimentacao/", {"data_inicio": data_inicio.isoformat()})
        self.assertEqual({row["documento"] for row in self.results(resp_inicio)}, {"L2"})

        resp_fim = self.client.get("/api/produto/estoque-movimentacao/", {"data_fim": data_fim_antiga.isoformat()})
        self.assertEqual({row["documento"] for row in self.results(resp_fim)}, {"L1"})

        resp_combinado = self.client.get(
            "/api/produto/estoque-movimentacao/",
            {
                "loja": self.loja_2.id,
                "tipo": EstoqueMovimentacao.TIPO_SAIDA,
                "data_inicio": data_inicio.isoformat(),
                "data_fim": data_fim_recente.isoformat(),
                "search": "REF-A",
            },
        )
        self.assertEqual([row["documento"] for row in self.results(resp_combinado)], ["L2"])

    def test_movimentacao_expoe_origem_estruturada_quando_disponivel(self):
        self.client.force_authenticate(self.master)
        mov_nfe = EstoqueMovimentacao.objects.get(documento="L1")
        mov_nfe.origem = EstoqueMovimentacao.ORIGEM_NFE
        mov_nfe.save(update_fields=["origem"])
        mov_sem_origem = EstoqueMovimentacao.objects.get(documento="L2")
        mov_sem_origem.origem = ""
        mov_sem_origem.save(update_fields=["origem"])

        resp = self.client.get("/api/produto/estoque-movimentacao/", {"referencia": "REF-A"})
        self.assertEqual(resp.status_code, 200, resp.content)
        origens = {row["CodigodeBarra"]: row["origem"] for row in self.results(resp)}
        self.assertEqual(origens[self.estoque_1.CodigodeBarra], "NFE")
        self.assertEqual(origens[self.estoque_2.CodigodeBarra], "")

    def test_movimentacao_expoe_cor_e_tamanho_do_sku_quando_disponivel(self):
        unidade = Unidade.objects.create(empresa=self.empresa, Descricao="Unidade", Codigo="UN")
        grade = Grade.objects.create(empresa=self.empresa, Descricao="Grade")
        cor = Cor.objects.create(empresa=self.empresa, Descricao="Azul", Codigo="AZ", Cor="Azul")
        tamanho = Tamanho.objects.create(empresa=self.empresa, idgrade=grade, Tamanho="M")
        colecao = Colecao.objects.create(empresa=self.empresa, Descricao="Colecao SKU", Codigo="55", Estacao="01")
        grupo = Grupo.objects.create(empresa=self.empresa, Codigo="01", CodigoRef="01", Descricao="Grupo SKU", Margem=0)
        subgrupo = Subgrupo.objects.create(empresa=self.empresa, Idgrupo=grupo, Descricao="Subgrupo SKU", Margem=0)
        Ncm.objects.create(empresa=self.empresa, ncm="6109.10.00", descricao="Produto SKU")
        produto = Produto.objects.create(
            empresa=self.empresa,
            tipo_produto="1",
            descricao="Produto Grade",
            descricao_reduzida="GRADE",
            unidade=unidade,
            colecao=colecao,
            grupo=grupo,
            subgrupo=subgrupo,
            grade=grade,
            ncm="6109.10.00",
        )
        ConfigEan.objects.create(empresa=self.empresa, company_prefix="5555")
        sku = ProdutoDetalhe.objects.create(produto=produto, idcor=cor, idtamanho=tamanho)
        mov_com_sku = EstoqueMovimentacao.objects.create(
            Idloja=self.loja_1,
            CodigodeBarra=sku.ean13,
            referencia=produto.referencia or "REF-SKU",
            tipo=EstoqueMovimentacao.TIPO_ENTRADA,
            quantidade="1.000",
            saldo_anterior="0.000",
            saldo_posterior="1.000",
        )

        self.client.force_authenticate(self.master)
        resp = self.client.get("/api/produto/estoque-movimentacao/", {"ean": sku.ean13})
        self.assertEqual(resp.status_code, 200, resp.content)
        row = self.results(resp)[0]
        self.assertEqual(row["Idmovimento"], mov_com_sku.Idmovimento)
        self.assertEqual(row["cor"], "Azul")
        self.assertEqual(row["tamanho"], "M")

        resp_sem_sku = self.client.get("/api/produto/estoque-movimentacao/", {"ean": self.estoque_1.CodigodeBarra})
        row_sem_sku = self.results(resp_sem_sku)[0]
        self.assertEqual(row_sem_sku["cor"], "")
        self.assertEqual(row_sem_sku["tamanho"], "")

