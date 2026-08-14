from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import UserModulePermission
from auditoria.models import AuditLog
from cadastros.models import Empresa, EmpresaContrato, EmpresaModulo, Loja, ModuloSistema
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
    ProdutoImagem,
    ProdutoInsumoHistorico,
    ProdutoUsoConsumoHistorico,
    ProdutoVendaHistorico,
    Pack,
    PackItem,
    Subgrupo,
    Tamanho,
    Unidade,
    Material,
)


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
        UserModulePermission.objects.create(user=self.user, modulo=UserModulePermission.Module.PRODUTOS, acesso=UserModulePermission.Access.EDIT)
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

