from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from auditoria.models import AuditLog
from cadastros.models import Empresa, Loja
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
    ProdutoVendaHistorico,
    Subgrupo,
    Tamanho,
    Unidade,
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
