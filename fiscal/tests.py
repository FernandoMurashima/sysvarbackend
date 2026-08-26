from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import PerfilAcesso, PerfilModuloPermissao, UserModulePermission
from auditoria.models import AuditLog
from cadastros.models import Empresa, EmpresaContrato, EmpresaModulo, Fornecedor, Loja, ModuloSistema, Nat_Lancamento
from compras.models import PedidoCompra, PedidoCompraEntrega, PedidoCompraItem
from fiscal.models import NotaFiscalEntrada, NotaFiscalEntradaDivergenciaXml, NotaFiscalEntradaItem, NotaFiscalEntradaItemXml
from financeiro.models import MovimentacaoFinanceira, Pagar, PagarItem
from produto.models import Colecao, ConfigEan, Cor, Estoque, EstoqueMovimentacao, Grade, Grupo, Pack, PackItem, Produto, ProdutoDetalhe, ProdutoFornecedor, ProdutoUsoConsumoEstoque, ProdutoUsoConsumoMovimentacao, Tamanho, Unidade


@override_settings(ALLOWED_HOSTS=["testserver"])
class NotaFiscalEntradaXmlImportacaoTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa = Empresa.objects.create(nome="Empresa XML", documento="12345678000195", plano_completo=True)
        self.empresa_b = Empresa.objects.create(nome="Empresa XML B", documento="12345678000276", plano_completo=True)
        self.user = get_user_model().objects.create_user("xml-user", "xml@sysvar.test", "123", empresa=self.empresa, type="Gerente")
        self.modulo = ModuloSistema.objects.update_or_create(
            chave="compras",
            defaults={"nome": "Compras", "categoria": ModuloSistema.CATEGORIA_COMERCIAL, "basico": False, "ativo": True},
        )[0]
        EmpresaContrato.objects.update_or_create(
            empresa=self.empresa,
            defaults={"status": EmpresaContrato.STATUS_ATIVO, "plano_completo": True, "usuario_master": self.user},
        )
        EmpresaModulo.objects.update_or_create(empresa=self.empresa, modulo=self.modulo, defaults={"contratado": True})
        UserModulePermission.objects.create(user=self.user, modulo=UserModulePermission.Module.COMPRAS, acesso=UserModulePermission.Access.EDIT)
        self.client.force_authenticate(self.user)
        self.loja = Loja.objects.create(empresa=self.empresa, nome_loja="Loja XML", apelido_loja="XML", cnpj="12345678000195", estado="SP")
        self.loja_b = Loja.objects.create(empresa=self.empresa_b, nome_loja="Loja B", apelido_loja="B", cnpj="12345678000276", estado="SP")
        self.fornecedor = Fornecedor.objects.create(
            empresa=self.empresa,
            tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA,
            documento="22345678000195",
            cnpj="22345678000195",
            nome_fornecedor="Fornecedor XML",
            categoria="OUTROS",
        )
        self.fornecedor_b = Fornecedor.objects.create(
            empresa=self.empresa_b,
            tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA,
            documento="32345678000195",
            cnpj="32345678000195",
            nome_fornecedor="Fornecedor B",
            categoria="OUTROS",
        )
        self.fornecedor_incompativel = Fornecedor.objects.create(
            empresa=self.empresa,
            tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA,
            documento="42345678000195",
            cnpj="42345678000195",
            nome_fornecedor="Fornecedor Incompativel",
            categoria="OUTROS",
        )
        self.pedido = PedidoCompra.objects.create(empresa=self.empresa, tipo="2", loja=self.loja, fornecedor=self.fornecedor, status="AP")
        self.pedido_incompativel = PedidoCompra.objects.create(empresa=self.empresa, tipo="2", loja=self.loja, fornecedor=self.fornecedor_incompativel, status="AP")
        self.unidade = Unidade.objects.create(empresa=self.empresa, Descricao="Pacote", Codigo="PCT")
        self.unidade_b = Unidade.objects.create(empresa=self.empresa_b, Descricao="Pacote B", Codigo="PCT")
        self.produto = Produto.objects.create(empresa=self.empresa, tipo_produto="2", descricao="Papel A4", unidade=self.unidade)
        self.produto_alt = Produto.objects.create(empresa=self.empresa, tipo_produto="2", descricao="Caneta Azul", unidade=self.unidade)
        self.produto_b = Produto.objects.create(empresa=self.empresa_b, tipo_produto="2", descricao="Produto B", unidade=self.unidade_b)
        self.pedido_item = PedidoCompraItem.objects.create(
            pedido=self.pedido,
            produto=self.produto_alt,
            qtd=Decimal("2.000"),
            preco_unit=Decimal("5.00"),
            total_item=Decimal("10.00"),
        )
        self.pedido_item_produto = PedidoCompraItem.objects.create(
            pedido=self.pedido,
            produto=self.produto,
            qtd=Decimal("30.000"),
            preco_unit=Decimal("10.00"),
            total_item=Decimal("300.00"),
        )

    def xml(self, chave=None, modelo="55", emit_doc="22345678000195", dest_doc="12345678000195", numero=None):
        chave = chave or "35260822345678000195550010000001234567890123"
        numero = numero or "123"
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe>
    <infNFe Id="NFe{chave}" versao="4.00">
      <ide><cUF>35</cUF><natOp>Compra</natOp><mod>{modelo}</mod><serie>1</serie><nNF>{numero}</nNF><dhEmi>2026-08-26T10:00:00-03:00</dhEmi></ide>
      <emit><CNPJ>{emit_doc}</CNPJ><xNome>Fornecedor XML</xNome><IE>110042490114</IE></emit>
      <dest><CNPJ>{dest_doc}</CNPJ><xNome>Empresa XML</xNome></dest>
      <det nItem="1"><prod><cProd>BAX002</cProd><cEAN>7891234567895</cEAN><xProd>PAPEL SULFITE A4 75G</xProd><NCM>48025610</NCM><CFOP>5102</CFOP><uCom>FD</uCom><qCom>3.0000</qCom><vUnCom>10.0000000000</vUnCom><vProd>30.00</vProd><vDesc>1.00</vDesc></prod><infAdProd>Lote A</infAdProd></det>
      <det nItem="2"><prod><cProd>CAN001</cProd><cEAN>SEM GTIN</cEAN><xProd>CANETA AZUL</xProd><NCM>96081000</NCM><CFOP>5102</CFOP><uCom>UN</uCom><qCom>2.0000</qCom><vUnCom>5.0000000000</vUnCom><vProd>10.00</vProd></prod></det>
      <total><ICMSTot><vProd>40.00</vProd><vFrete>5.00</vFrete><vDesc>1.00</vDesc><vNF>44.00</vNF></ICMSTot></total>
    </infNFe>
  </NFe>
  <protNFe><infProt><chNFe>{chave}</chNFe><nProt>135260000000001</nProt></infProt></protNFe>
</nfeProc>'''

    def upload(self, xml_text=None, status_code=201, extra=None):
        xml_text = xml_text if xml_text is not None else self.xml()
        payload = {"arquivo": SimpleUploadedFile("nota.xml", xml_text.encode("utf-8"), content_type="application/xml")}
        if extra:
            payload.update(extra)
        resp = self.client.post("/api/fiscal/notas-entrada/importar-xml/", payload, format="multipart")
        self.assertEqual(resp.status_code, status_code, resp.data)
        return resp

    def test_importa_xml_valido_preserva_original_cabecalho_itens_e_nao_efetiva_operacao(self):
        original = self.xml()
        resp = self.upload(original)
        nota = NotaFiscalEntrada.objects.get(pk=resp.data["id"])
        self.assertEqual(nota.status, NotaFiscalEntrada.Status.ABERTA)
        self.assertTrue(nota.xml_importado)
        self.assertEqual(nota.xml_original, original)
        self.assertEqual(nota.chave_acesso, "35260822345678000195550010000001234567890123")
        self.assertEqual((nota.modelo, nota.serie, nota.numero), ("55", "1", "123"))
        self.assertEqual(nota.dt_emissao.isoformat(), "2026-08-26")
        self.assertEqual(nota.fornecedor_id, self.fornecedor.id)
        self.assertEqual(nota.loja_id, self.loja.id)
        self.assertEqual(nota.valor_produtos, Decimal("40.00"))
        self.assertEqual(nota.valor_desconto, Decimal("1.00"))
        self.assertEqual(nota.valor_frete, Decimal("5.00"))
        self.assertEqual(nota.valor_total, Decimal("44.00"))
        itens = list(nota.itens_xml.order_by("numero_item"))
        self.assertEqual(len(itens), 2)
        self.assertEqual(itens[0].codigo_produto_fornecedor, "BAX002")
        self.assertEqual(itens[0].descricao_produto, "PAPEL SULFITE A4 75G")
        self.assertEqual(itens[0].gtin_ean, "7891234567895")
        self.assertEqual(itens[0].ncm, "48025610")
        self.assertEqual(itens[0].cfop, "5102")
        self.assertEqual(itens[0].unidade_comercial, "FD")
        self.assertEqual(itens[0].quantidade_comercial, Decimal("3.000000"))
        self.assertEqual(itens[0].valor_unitario_comercial, Decimal("10.0000000000"))
        self.assertEqual(itens[0].valor_produto, Decimal("30.00"))
        self.assertEqual(itens[0].valor_desconto, Decimal("1.00"))
        self.assertFalse(nota.itens.exists())
        self.assertFalse(EstoqueMovimentacao.objects.exists())
        self.assertFalse(Pagar.objects.exists())
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.status, "AP")
        self.assertFalse(ProdutoFornecedor.objects.exists())

    def test_importa_com_pedido_compativel_e_rejeita_incompativel(self):
        resp = self.upload(extra={"pedido_compra": self.pedido.id})
        self.assertEqual(resp.data["pedido_compra"], self.pedido.id)
        self.upload(self.xml(chave="35260822345678000195550010000001234567890124"), status_code=400, extra={"pedido_compra": self.pedido_incompativel.id})

    def test_rejeita_fornecedor_destinatario_modelo_chave_duplicada_e_xml_invalido(self):
        self.upload(self.xml(emit_doc="99999999000199"), status_code=400)
        self.upload(self.xml(dest_doc=self.loja_b.cnpj), status_code=400)
        self.upload(self.xml(modelo="65"), status_code=400)
        self.upload()
        self.upload(status_code=400)
        self.upload("<NFe><infNFe>", status_code=400)
        self.upload("isso nao e xml", status_code=400)

    def test_importacao_falha_sem_registro_parcial_e_audita_sucesso(self):
        self.upload(self.xml(emit_doc="99999999000199"), status_code=400)
        self.assertFalse(NotaFiscalEntrada.objects.exists())
        self.assertFalse(NotaFiscalEntradaItemXml.objects.exists())
        resp = self.upload()
        self.assertTrue(AuditLog.objects.filter(app_label="fiscal", model="notafiscalentrada", object_id=str(resp.data["id"]), action="OBJECT_CREATED").exists())

    def criar_vinculo(self, produto=None, fornecedor=None, codigo="BAX002", ativo=True, unidade="FD", fator="10", gtin=""):
        vinculo, _ = ProdutoFornecedor.objects.get_or_create(
            empresa=(fornecedor or self.fornecedor).empresa,
            fornecedor=fornecedor or self.fornecedor,
            codigo_produto_fornecedor=codigo,
            defaults={
                "produto": produto or self.produto,
                "descricao_fornecedor": "PAPEL SULFITE A4",
                "unidade_fornecedor": unidade,
                "fator_conversao": Decimal(fator),
                "gtin_ean": gtin,
                "ativo": ativo,
            },
        )
        return vinculo

    def item_xml(self, chave="35260822345678000195550010000001234567890125", extra=None, **kwargs):
        kwargs.setdefault("numero", chave[-6:])
        resp = self.upload(self.xml(chave=chave, **kwargs), extra=extra)
        return NotaFiscalEntrada.objects.get(pk=resp.data["id"]).itens_xml.order_by("numero_item").first()

    def test_conciliacao_automatica_por_produto_fornecedor_e_idempotente(self):
        vinculo = self.criar_vinculo()
        item = self.item_xml()
        original = (item.codigo_produto_fornecedor, item.descricao_produto, item.unidade_comercial, item.quantidade_comercial)
        item.refresh_from_db()
        self.assertEqual(item.produto_id, self.produto.pk)
        self.assertEqual(item.produto_fornecedor_id, vinculo.id)
        self.assertEqual(item.origem_conciliacao, NotaFiscalEntradaItemXml.OrigemConciliacao.VINCULO)
        self.assertEqual(
            (item.codigo_produto_fornecedor, item.descricao_produto, item.unidade_comercial, item.quantidade_comercial),
            original,
        )
        resp = self.client.post(f"/api/fiscal/notas-entrada/{item.nota_id}/conciliar-automaticamente/")
        self.assertEqual(resp.status_code, 200, resp.data)
        item.refresh_from_db()
        self.assertEqual(item.produto_id, self.produto.pk)
        self.assertEqual(item.produto_fornecedor_id, vinculo.id)
        self.assertFalse(EstoqueMovimentacao.objects.exists())
        self.assertFalse(Pagar.objects.exists())
        self.assertFalse(NotaFiscalEntradaItem.objects.exists())
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.status, "AP")

    def test_conciliacao_automatica_respeita_empresa_fornecedor_ativo_e_unidade(self):
        self.criar_vinculo(produto=self.produto_b, fornecedor=self.fornecedor_b)
        item = self.item_xml(chave="35260822345678000195550010000001234567890126")
        self.assertIsNone(item.produto_id)
        self.assertIsNone(item.produto_fornecedor_id)

        self.criar_vinculo(codigo="CAN001", produto=self.produto_alt, ativo=False, unidade="UN")
        item2 = item.nota.itens_xml.get(numero_item=2)
        self.assertIsNone(item2.produto_id)

        self.criar_vinculo(codigo="DIV001", produto=self.produto, unidade="CX")
        item3 = self.item_xml(chave="35260822345678000195550010000001234567890127", emit_doc=self.fornecedor.documento)
        item3.codigo_produto_fornecedor = "DIV001"
        item3.save(update_fields=["codigo_produto_fornecedor"])
        resp = self.client.post(f"/api/fiscal/notas-entrada/{item3.nota_id}/conciliar-automaticamente/")
        self.assertEqual(resp.status_code, 200, resp.data)
        item3.refresh_from_db()
        self.assertIsNone(item3.produto_id)

    def test_gtin_unico_concilia_e_gtin_ambiguo_nao_concilia(self):
        self.criar_vinculo(codigo="OUTRO", produto=self.produto, gtin="7891234567895", unidade="")
        item = self.item_xml(chave="35260822345678000195550010000001234567890128")
        self.assertEqual(item.produto_id, self.produto.pk)
        self.assertEqual(item.origem_conciliacao, NotaFiscalEntradaItemXml.OrigemConciliacao.GTIN)

        self.criar_vinculo(codigo="GTIN1", produto=self.produto, gtin="7891234567895", unidade="")
        self.criar_vinculo(codigo="GTIN2", produto=self.produto_alt, gtin="7891234567895", unidade="")
        item2 = self.item_xml(chave="35260822345678000195550010000001234567890129")
        self.assertIsNone(item2.produto_id)

    def test_pedido_inequivoco_concilia_e_pedido_ambiguo_nao_concilia(self):
        item = self.item_xml(chave="35260822345678000195550010000001234567890130", extra={"pedido_compra": self.pedido.id})
        item2 = item.nota.itens_xml.get(numero_item=2)
        resp = self.client.post(f"/api/fiscal/notas-entrada/{item.nota_id}/conciliar-automaticamente/")
        self.assertEqual(resp.status_code, 200, resp.data)
        item2.refresh_from_db()
        self.assertEqual(item2.produto_id, self.produto_alt.pk)
        self.assertEqual(item2.origem_conciliacao, NotaFiscalEntradaItemXml.OrigemConciliacao.PEDIDO)

        PedidoCompraItem.objects.create(pedido=self.pedido, produto=self.produto, qtd=Decimal("2.000"), preco_unit=Decimal("5.00"), total_item=Decimal("10.00"))
        item_amb = self.item_xml(chave="35260822345678000195550010000001234567890131", extra={"pedido_compra": self.pedido.id})
        item_amb2 = item_amb.nota.itens_xml.get(numero_item=2)
        resp = self.client.post(f"/api/fiscal/notas-entrada/{item_amb.nota_id}/conciliar-automaticamente/")
        self.assertEqual(resp.status_code, 200, resp.data)
        item_amb2.refresh_from_db()
        self.assertIsNone(item_amb2.produto_id)

    def test_descricao_apenas_sugere_sem_conciliar(self):
        item = self.item_xml(chave="35260822345678000195550010000001234567890132")
        self.assertIsNone(item.produto_id)
        resp = self.client.get(f"/api/fiscal/notas-entrada/{item.nota_id}/item-xml-candidatos/", {"item": item.id, "q": "Papel"})
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIn(self.produto.pk, [row["id"] for row in resp.data])

    def test_conciliacao_manual_cria_vinculo_audita_e_rejeita_conflitos(self):
        item = self.item_xml(chave="35260822345678000195550010000001234567890133")
        resp = self.client.post(f"/api/fiscal/notas-entrada/{item.nota_id}/item-xml-conciliar/", {"item": item.id, "produto": self.produto.pk}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        item.refresh_from_db()
        vinculo = ProdutoFornecedor.objects.get(fornecedor=self.fornecedor, codigo_produto_fornecedor="BAX002")
        self.assertEqual(vinculo.produto_id, self.produto.pk)
        self.assertEqual(vinculo.descricao_fornecedor, item.descricao_produto)
        self.assertEqual(vinculo.gtin_ean, item.gtin_ean)
        self.assertEqual(vinculo.unidade_fornecedor, item.unidade_comercial)
        self.assertEqual(vinculo.fator_conversao, Decimal("1.000000"))
        self.assertEqual(item.origem_conciliacao, NotaFiscalEntradaItemXml.OrigemConciliacao.MANUAL)
        self.assertTrue(AuditLog.objects.filter(metadata__legacy_action="conciliacao_manual_xml").exists())
        self.assertTrue(AuditLog.objects.filter(metadata__legacy_action="produto_fornecedor_criado_conciliacao_nfe").exists())

        item2 = self.item_xml(chave="35260822345678000195550010000001234567890134")
        resp = self.client.post(f"/api/fiscal/notas-entrada/{item2.nota_id}/item-xml-conciliar/", {"item": item2.id, "produto": self.produto_alt.pk}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        vinculo.refresh_from_db()
        self.assertEqual(vinculo.produto_id, self.produto.pk)

    def test_conciliacao_manual_rejeita_produto_de_outra_empresa_e_sem_codigo_nao_cria_vinculo(self):
        item = self.item_xml(chave="35260822345678000195550010000001234567890135")
        resp = self.client.post(f"/api/fiscal/notas-entrada/{item.nota_id}/item-xml-conciliar/", {"item": item.id, "produto": self.produto_b.pk}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        item.codigo_produto_fornecedor = ""
        item.save(update_fields=["codigo_produto_fornecedor"])
        resp = self.client.post(f"/api/fiscal/notas-entrada/{item.nota_id}/item-xml-conciliar/", {"item": item.id, "produto": self.produto.pk}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(ProdutoFornecedor.objects.filter(codigo_produto_fornecedor="").exists())

    def test_resumo_quantidade_calculada_e_bloqueio_de_fechamento_pendente(self):
        self.criar_vinculo()
        item = self.item_xml(chave="35260822345678000195550010000001234567890136")
        resp = self.client.get(f"/api/fiscal/notas-entrada/{item.nota_id}/itens-xml/")
        self.assertEqual(resp.status_code, 200, resp.data)
        linha = next(row for row in resp.data if row["numero_item"] == 1)
        self.assertEqual(Decimal(linha["conversao"]["quantidade_interna_calculada"]), Decimal("30.000000"))
        self.assertEqual(linha["conversao"]["fator_conversao"], "10.000000")
        resp = self.client.get(f"/api/fiscal/notas-entrada/{item.nota_id}/resumo-conciliacao/")
        self.assertEqual(resp.data["total_itens"], 2)
        self.assertEqual(resp.data["itens_conciliados"], 1)
        self.assertEqual(resp.data["itens_pendentes"], 1)
        self.assertFalse(resp.data["nota_conciliada"])
        resp = self.client.post(f"/api/fiscal/notas-entrada/{item.nota_id}/fechar/")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("Concilie todos os itens XML", resp.data["detail"])

    def conferir(self, item, quantidade, status_code=200):
        resp = self.client.post(
            f"/api/fiscal/notas-entrada/{item.nota_id}/item-xml-conferir/",
            {"item": item.id, "quantidade_recebida": str(quantidade)},
            format="json",
        )
        self.assertEqual(resp.status_code, status_code, resp.data)
        return resp

    def test_conferencia_quantidade_igual_preserva_fiscal_xml_e_nao_cria_divergencia(self):
        self.criar_vinculo()
        item = self.item_xml(chave="35260822345678000195550010000001234567890137")
        original_xml = item.nota.xml_original
        fiscal = item.quantidade_comercial
        unitario = item.valor_unitario_comercial
        self.conferir(item, fiscal)
        item.refresh_from_db()
        self.assertEqual(item.quantidade_recebida, fiscal)
        self.assertTrue(item.conferido)
        self.assertEqual(item.quantidade_comercial, fiscal)
        self.assertEqual(item.valor_unitario_comercial, unitario)
        self.assertEqual(item.nota.xml_original, original_xml)
        self.assertFalse(NotaFiscalEntradaDivergenciaXml.objects.filter(item_xml=item, status=NotaFiscalEntradaDivergenciaXml.Status.PENDENTE).exists())
        self.assertFalse(EstoqueMovimentacao.objects.exists())
        self.assertFalse(Pagar.objects.exists())
        self.assertFalse(NotaFiscalEntradaItem.objects.exists())
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.status, "AP")
        item.nota.refresh_from_db()
        self.assertEqual(item.nota.valor_total, Decimal("44.00"))

    def test_conferencia_menor_zero_recebido_e_divergencia_decimal(self):
        self.criar_vinculo()
        item = self.item_xml(chave="35260822345678000195550010000001234567890138")
        self.conferir(item, Decimal("2"))
        div = NotaFiscalEntradaDivergenciaXml.objects.get(item_xml=item)
        self.assertEqual(div.status, NotaFiscalEntradaDivergenciaXml.Status.PENDENTE)
        self.assertEqual(div.quantidade_faltante, Decimal("1.000000"))
        self.assertEqual(div.valor_divergente, Decimal("10.00"))
        self.conferir(item, Decimal("0"))
        item.refresh_from_db()
        div.refresh_from_db()
        self.assertEqual(item.quantidade_recebida, Decimal("0.000000"))
        self.assertEqual(div.quantidade_faltante, Decimal("3.000000"))
        self.assertEqual(div.valor_divergente, Decimal("30.00"))
        self.assertEqual(NotaFiscalEntradaDivergenciaXml.objects.filter(item_xml=item, status=NotaFiscalEntradaDivergenciaXml.Status.PENDENTE).count(), 1)

    def test_conferencia_diferencia_zero_de_nao_conferido_e_valida_limites(self):
        self.criar_vinculo()
        item = self.item_xml(chave="35260822345678000195550010000001234567890139")
        outro = item.nota.itens_xml.get(numero_item=2)
        self.assertIsNone(item.quantidade_recebida)
        self.conferir(item, 0)
        item.refresh_from_db()
        outro.refresh_from_db()
        self.assertEqual(item.quantidade_recebida, Decimal("0.000000"))
        self.assertIsNone(outro.quantidade_recebida)
        self.conferir(item, -1, status_code=400)
        self.conferir(item, Decimal("3.000001"), status_code=400)

    def test_conferencia_atualiza_e_resolve_divergencia_sem_duplicar(self):
        self.criar_vinculo()
        item = self.item_xml(chave="35260822345678000195550010000001234567890140")
        self.conferir(item, 1)
        div = NotaFiscalEntradaDivergenciaXml.objects.get(item_xml=item)
        self.assertEqual(div.quantidade_faltante, Decimal("2.000000"))
        self.conferir(item, 2)
        div.refresh_from_db()
        self.assertEqual(div.status, NotaFiscalEntradaDivergenciaXml.Status.PENDENTE)
        self.assertEqual(div.quantidade_faltante, Decimal("1.000000"))
        self.conferir(item, 3)
        div.refresh_from_db()
        self.assertEqual(div.status, NotaFiscalEntradaDivergenciaXml.Status.RESOLVIDA)
        self.assertEqual(div.valor_divergente, Decimal("0.00"))
        self.assertEqual(NotaFiscalEntradaDivergenciaXml.objects.filter(item_xml=item).count(), 1)
        self.assertTrue(AuditLog.objects.filter(metadata__legacy_action="conferencia_fisica_xml").exists())
        self.assertTrue(AuditLog.objects.filter(metadata__legacy_action="divergencia_xml_atualizada").exists())

    def test_quantidade_interna_e_conversao_pendente_no_resumo(self):
        self.criar_vinculo(codigo="BAX002", unidade="FD", fator="10")
        item = self.item_xml(chave="35260822345678000195550010000001234567890141")
        self.conferir(item, Decimal("2"))
        resp = self.client.get(f"/api/fiscal/notas-entrada/{item.nota_id}/itens-xml/")
        linha = next(row for row in resp.data if row["id"] == item.id)
        self.assertEqual(Decimal(linha["quantidade_interna_recebida"]), Decimal("20.000000"))
        self.assertFalse(linha["conversao"]["conversao_pendente"])

        self.criar_vinculo(codigo="CAN001", produto=self.produto_alt, unidade="", fator="1")
        item2 = item.nota.itens_xml.get(numero_item=2)
        self.client.post(f"/api/fiscal/notas-entrada/{item.nota_id}/conciliar-automaticamente/")
        self.conferir(item2, 2)
        resp = self.client.get(f"/api/fiscal/notas-entrada/{item.nota_id}/resumo-conferencia/")
        self.assertEqual(resp.data["conversoes_pendentes"], 1)
        self.assertFalse(resp.data["conferencia_completa"])

    def test_conferencia_completa_admite_divergencia_pendente_e_fechamento_nao_bloqueia_por_falta(self):
        self.criar_vinculo(codigo="BAX002", unidade="FD", fator="10")
        self.criar_vinculo(codigo="CAN001", produto=self.produto_alt, unidade="UN", fator="1")
        item = self.item_xml(chave="35260822345678000195550010000001234567890142")
        item2 = item.nota.itens_xml.get(numero_item=2)
        self.conferir(item, 2)
        self.conferir(item2, 2)
        resp = self.client.get(f"/api/fiscal/notas-entrada/{item.nota_id}/resumo-conferencia/")
        self.assertEqual(resp.data["itens_conferidos"], 2)
        self.assertEqual(resp.data["itens_nao_conferidos"], 0)
        self.assertEqual(resp.data["itens_com_divergencia"], 1)
        self.assertEqual(Decimal(resp.data["valor_divergente_total"]), Decimal("10.00"))
        self.assertTrue(resp.data["possui_divergencia_pendente"])
        self.assertTrue(resp.data["conferencia_completa"])
        resp = self.client.post(f"/api/fiscal/notas-entrada/{item.nota_id}/fechar/")
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_item_nao_conciliado_nao_pode_ser_conferido_e_bloqueia_nf_nao_conferida(self):
        item = self.item_xml(chave="35260822345678000195550010000001234567890143")
        self.conferir(item, 1, status_code=400)
        resp = self.client.post(f"/api/fiscal/notas-entrada/{item.nota_id}/fechar/")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("Concilie todos", resp.data["detail"])

    def test_lote_e_transacional(self):
        self.criar_vinculo(codigo="BAX002", unidade="FD", fator="10")
        self.criar_vinculo(codigo="CAN001", produto=self.produto_alt, unidade="UN", fator="1")
        item = self.item_xml(chave="35260822345678000195550010000001234567890144")
        item2 = item.nota.itens_xml.get(numero_item=2)
        resp = self.client.post(
            f"/api/fiscal/notas-entrada/{item.nota_id}/conferir-itens-xml/",
            {"itens": [{"item": item.id, "quantidade_recebida": "3"}, {"item": item2.id, "quantidade_recebida": "3"}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.data)
        item.refresh_from_db()
        item2.refresh_from_db()
        self.assertIsNone(item.quantidade_recebida)
        self.assertIsNone(item2.quantidade_recebida)

    def test_divergencias_respeitam_multiempresa(self):
        self.criar_vinculo()
        item = self.item_xml(chave="35260822345678000195550010000001234567890145")
        self.conferir(item, 2)
        self.user.empresa = self.empresa_b
        self.user.save(update_fields=["empresa"])
        resp = self.client.get(f"/api/fiscal/notas-entrada/{item.nota_id}/divergencias-xml/")
        self.assertIn(resp.status_code, {403, 404})

    def preparar_nf_xml_efetivavel(self, chave="35260822345678000195550010000001234567890146", pedido=False, falta=False, zero_item2=False):
        self.criar_vinculo(codigo="BAX002", produto=self.produto, unidade="FD", fator="10")
        self.criar_vinculo(codigo="CAN001", produto=self.produto_alt, unidade="UN", fator="1")
        extra = {"pedido_compra": self.pedido.id} if pedido else None
        item = self.item_xml(chave=chave, extra=extra)
        nota = item.nota
        nota.refresh_from_db()
        item1 = nota.itens_xml.get(numero_item=1)
        item2 = nota.itens_xml.get(numero_item=2)
        self.conferir(item1, Decimal("2") if falta else Decimal("3"))
        self.conferir(item2, Decimal("0") if zero_item2 else Decimal("2"))
        nota.refresh_from_db()
        return nota, item1, item2

    def test_efetiva_nf_xml_sem_pedido_com_estoque_custo_financeiro_alerta_e_snapshot(self):
        nota, item1, item2 = self.preparar_nf_xml_efetivavel(falta=True, zero_item2=True)
        resp = self.client.post(f"/api/fiscal/notas-entrada/{nota.id}/fechar/")
        self.assertEqual(resp.status_code, 200, resp.data)
        nota.refresh_from_db()
        item1.refresh_from_db()
        item2.refresh_from_db()
        self.assertEqual(nota.status, NotaFiscalEntrada.Status.FECHADA)
        self.assertEqual(item1.quantidade_comercial, Decimal("3.000000"))
        self.assertEqual(item1.quantidade_recebida, Decimal("2.000000"))
        self.assertEqual(item1.quantidade_interna_efetivada, Decimal("20.000000"))
        self.assertEqual(item1.fator_conversao_efetivado, Decimal("10.000000"))
        self.assertEqual(item1.unidade_fornecedor_efetivada, "FD")
        self.assertEqual(item2.quantidade_interna_efetivada, Decimal("0.000000"))
        self.assertEqual(ProdutoUsoConsumoEstoque.objects.get(empresa=self.empresa, loja=self.loja, produto=self.produto).saldo, Decimal("20.000"))
        self.assertFalse(ProdutoUsoConsumoEstoque.objects.filter(empresa=self.empresa, loja=self.loja, produto=self.produto_alt).exists())
        div = NotaFiscalEntradaDivergenciaXml.objects.get(item_xml=item1)
        self.assertEqual(div.status, NotaFiscalEntradaDivergenciaXml.Status.PENDENTE)
        titulo = Pagar.objects.get(nfe_id=nota.pk)
        self.assertEqual(titulo.Valor_total, Decimal("44.00"))
        self.assertTrue(titulo.alerta_divergencia_mercadoria)
        self.assertEqual(titulo.valor_divergencia_mercadoria, Decimal("20.00"))
        self.assertFalse(PedidoCompra.objects.filter(pk=self.pedido.pk, status="AT").exists())
        self.assertTrue(AuditLog.objects.filter(metadata__legacy_action="fechar_xml").exists())

    def test_efetivacao_xml_e_idempotente_e_bloqueia_alteracoes_apos_fechamento(self):
        nota, item1, _ = self.preparar_nf_xml_efetivavel(chave="35260822345678000195550010000001234567890147")
        resp = self.client.post(f"/api/fiscal/notas-entrada/{nota.id}/fechar/")
        self.assertEqual(resp.status_code, 200, resp.data)
        resp = self.client.post(f"/api/fiscal/notas-entrada/{nota.id}/fechar/")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(ProdutoUsoConsumoMovimentacao.objects.filter(origem__contains=f"NFE:{nota.pk}").count(), 2)
        resp = self.client.post(f"/api/fiscal/notas-entrada/{nota.id}/item-xml-conferir/", {"item": item1.id, "quantidade_recebida": "1"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        resp = self.client.post(f"/api/fiscal/notas-entrada/{nota.id}/item-xml-conciliar/", {"item": item1.id, "produto": self.produto.pk}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_efetivacao_bloqueia_precondicoes_xml(self):
        item = self.item_xml(chave="35260822345678000195550010000001234567890148")
        resp = self.client.post(f"/api/fiscal/notas-entrada/{item.nota_id}/fechar/")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("Concilie todos", resp.data["detail"])

        self.criar_vinculo(codigo="BAX002", produto=self.produto, unidade="FD", fator="10")
        item = self.item_xml(chave="35260822345678000195550010000001234567890149")
        resp = self.client.post(f"/api/fiscal/notas-entrada/{item.nota_id}/fechar/")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("Concilie todos", resp.data["detail"])

        self.criar_vinculo(codigo="CAN001", produto=self.produto_alt, unidade="", fator="1")
        item = self.item_xml(chave="35260822345678000195550010000001234567890150")
        item2 = item.nota.itens_xml.get(numero_item=2)
        self.conferir(item, 3)
        self.conferir(item2, 2)
        resp = self.client.post(f"/api/fiscal/notas-entrada/{item.nota_id}/fechar/")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("conversão", resp.data["detail"])

    def test_efetiva_nf_xml_com_pedido_atualiza_recebimento_real_sem_exceder_saldo(self):
        nota, item1, item2 = self.preparar_nf_xml_efetivavel(chave="35260822345678000195550010000001234567890151", pedido=True, falta=True)
        self.assertEqual(item2.pedido_item_id, self.pedido_item.id)
        resp = self.client.post(f"/api/fiscal/notas-entrada/{nota.id}/fechar/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.pedido.refresh_from_db()
        entrega = self.pedido_item.entregas.get()
        self.assertEqual(entrega.qtd_recebida, Decimal("2.000"))
        self.assertEqual(self.pedido.status, "AP")
        self.assertEqual(Pagar.objects.filter(nfe_id=nota.pk).count(), 0)

        nota2, _, item2b = self.preparar_nf_xml_efetivavel(chave="35260822345678000195550010000001234567890152", pedido=True)
        self.assertEqual(item2b.pedido_item_id, self.pedido_item.id)
        resp = self.client.post(f"/api/fiscal/notas-entrada/{nota2.id}/fechar/")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("saldo permitido", resp.data["detail"])

    def test_snapshot_nao_muda_com_alteracao_posterior_do_vinculo(self):
        nota, item1, _ = self.preparar_nf_xml_efetivavel(chave="35260822345678000195550010000001234567890153")
        resp = self.client.post(f"/api/fiscal/notas-entrada/{nota.id}/fechar/")
        self.assertEqual(resp.status_code, 200, resp.data)
        vinculo = item1.produto_fornecedor
        vinculo.fator_conversao = Decimal("99")
        vinculo.save(update_fields=["fator_conversao"])
        item1.refresh_from_db()
        self.assertEqual(item1.fator_conversao_efetivado, Decimal("10.000000"))
        self.assertEqual(item1.quantidade_interna_efetivada, Decimal("30.000000"))


@override_settings(ALLOWED_HOSTS=["testserver"])
class NotaFiscalEntradaMultiempresaTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa_a = Empresa.objects.create(nome="Empresa A", documento="11111111000191", plano_completo=True)
        self.empresa_b = Empresa.objects.create(nome="Empresa B", documento="22222222000191", plano_completo=True)
        self.user_a = get_user_model().objects.create_superuser(
            "fiscal-a",
            "fiscal-a@sysvar.test",
            "test",
            type="Gerente",
            empresa=self.empresa_a,
        )
        self.client.force_authenticate(self.user_a)
        self.loja_a = Loja.objects.create(
            empresa=self.empresa_a,
            nome_loja="Loja A",
            apelido_loja="Loja A",
            cnpj="11111111000100",
            estado="SP",
        )
        self.loja_b = Loja.objects.create(
            empresa=self.empresa_b,
            nome_loja="Loja B",
            apelido_loja="Loja B",
            cnpj="22222222000100",
            estado="SP",
        )
        self.fornecedor_a = self.criar_fornecedor(self.empresa_a, "Fornecedor A", "12345678000195")
        self.fornecedor_b = self.criar_fornecedor(self.empresa_b, "Fornecedor B", "22345678000195")
        self.unidade_a = Unidade.objects.create(empresa=self.empresa_a, Descricao="Unidade", Codigo="UN")
        self.unidade_b = Unidade.objects.create(empresa=self.empresa_b, Descricao="Unidade B", Codigo="UNB")
        self.produto_a = Produto.objects.create(
            empresa=self.empresa_a,
            tipo_produto="2",
            descricao="Uso A",
            unidade=self.unidade_a,
        )
        self.produto_b = Produto.objects.create(
            empresa=self.empresa_b,
            tipo_produto="2",
            descricao="Uso B",
            unidade=self.unidade_b,
        )
        self.pedido_a = self.criar_pedido(self.empresa_a, self.loja_a, self.fornecedor_a)
        self.pedido_b = self.criar_pedido(self.empresa_b, self.loja_b, self.fornecedor_b)
        self.item_a = self.criar_item(self.pedido_a, self.produto_a)
        self.item_b = self.criar_item(self.pedido_b, self.produto_b)
        self.nota_a = self.criar_nota(self.pedido_a, "100")
        self.nota_b = self.criar_nota(self.pedido_b, "200")
        self.nota_item_a = NotaFiscalEntradaItem.objects.create(
            nota=self.nota_a,
            pedido_item=self.item_a,
            qtd_recebida=Decimal("1.000"),
            preco_unit_nf=Decimal("10.0000"),
            total_item=Decimal("10.00"),
        )
        self.nota_item_b = NotaFiscalEntradaItem.objects.create(
            nota=self.nota_b,
            pedido_item=self.item_b,
            qtd_recebida=Decimal("1.000"),
            preco_unit_nf=Decimal("10.0000"),
            total_item=Decimal("10.00"),
        )

    def criar_fornecedor(self, empresa, nome, documento):
        return Fornecedor.objects.create(
            empresa=empresa,
            tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA,
            documento=documento,
            cnpj=documento,
            nome_fornecedor=nome,
            categoria="USO_CONSUMO",
        )

    def criar_pedido(self, empresa, loja, fornecedor):
        return PedidoCompra.objects.create(
            empresa=empresa,
            tipo="2",
            loja=loja,
            fornecedor=fornecedor,
            status="AP",
            observacoes="Pedido teste",
        )

    def criar_item(self, pedido, produto):
        return PedidoCompraItem.objects.create(
            pedido=pedido,
            produto=produto,
            qtd=Decimal("2.000"),
            preco_unit=Decimal("10.00"),
            total_item=Decimal("20.00"),
        )

    def criar_nota(self, pedido, numero):
        return NotaFiscalEntrada.objects.create(
            pedido_compra=pedido,
            numero=numero,
            dt_emissao=timezone.localdate(),
            dt_entrada=timezone.localdate(),
        )

    def payload_nota(self, pedido, numero="300"):
        hoje = timezone.localdate().isoformat()
        return {
            "pedido_compra": pedido.id,
            "modelo": "55",
            "serie": "1",
            "numero": numero,
            "dt_emissao": hoje,
            "dt_entrada": hoje,
        }

    def payload_nota_sem_pedido(self, numero="400", loja=None, fornecedor=None):
        hoje = timezone.localdate().isoformat()
        payload = {
            "empresa": self.empresa_a.id,
            "loja": (loja or self.loja_a).id if loja is not None else self.loja_a.id,
            "fornecedor": (fornecedor or self.fornecedor_a).id if fornecedor is not None else self.fornecedor_a.id,
            "modelo": "55",
            "serie": "1",
            "numero": numero,
            "dt_emissao": hoje,
            "dt_entrada": hoje,
        }
        return payload

    def payload_item(self, nota, pedido_item):
        return {
            "nota": nota.id,
            "pedido_item": pedido_item.id,
            "qtd_recebida": "1.000",
            "preco_unit_nf": "10.0000",
            "desconto_item": "0.00",
        }

    def test_lista_e_filtros_respeitam_empresa_da_requisicao(self):
        resp = self.client.get("/api/fiscal/notas-entrada/", {"empresa": self.empresa_a.id})
        notas = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertIn(self.nota_a.id, [n["id"] for n in notas])
        self.assertNotIn(self.nota_b.id, [n["id"] for n in notas])

        resp = self.client.get("/api/fiscal/notas-entrada/", {"empresa": self.empresa_a.id, "pedido": self.pedido_b.id})
        notas = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertEqual(notas, [])

        resp = self.client.get("/api/fiscal/notas-entrada/", {"empresa": self.empresa_a.id, "numero": self.nota_b.numero})
        notas = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertEqual(notas, [])

    def test_get_direto_de_nota_de_outra_empresa_retorna_404(self):
        resp = self.client.get(f"/api/fiscal/notas-entrada/{self.nota_b.id}/", {"empresa": self.empresa_a.id})
        self.assertEqual(resp.status_code, 404)

    def test_create_e_update_bloqueiam_pedido_de_outra_empresa(self):
        resp = self.client.post(
            f"/api/fiscal/notas-entrada/?empresa={self.empresa_a.id}",
            self.payload_nota(self.pedido_b),
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.data)

        resp = self.client.patch(
            f"/api/fiscal/notas-entrada/{self.nota_a.id}/?empresa={self.empresa_a.id}",
            {"pedido_compra": self.pedido_b.id},
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_cria_nota_com_pedido_preenche_identidade_propria(self):
        resp = self.client.post(
            f"/api/fiscal/notas-entrada/?empresa={self.empresa_a.id}",
            self.payload_nota(self.pedido_a, numero="301"),
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        nota = NotaFiscalEntrada.objects.get(pk=resp.data["id"])
        self.assertEqual(nota.empresa_id, self.empresa_a.id)
        self.assertEqual(nota.loja_id, self.loja_a.id)
        self.assertEqual(nota.fornecedor_id, self.fornecedor_a.id)

    def test_cria_nota_sem_pedido_com_identidade_valida(self):
        resp = self.client.post(
            f"/api/fiscal/notas-entrada/?empresa={self.empresa_a.id}",
            self.payload_nota_sem_pedido(),
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIsNone(resp.data["pedido_compra"])
        self.assertEqual(resp.data["empresa"], self.empresa_a.id)
        self.assertEqual(resp.data["loja"], self.loja_a.id)
        self.assertEqual(resp.data["fornecedor"], self.fornecedor_a.id)

    def test_nota_sem_pedido_exige_loja_e_fornecedor(self):
        payload = self.payload_nota_sem_pedido(numero="401")
        payload.pop("loja")
        resp = self.client.post(f"/api/fiscal/notas-entrada/?empresa={self.empresa_a.id}", payload, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("loja", resp.data)

        payload = self.payload_nota_sem_pedido(numero="402")
        payload.pop("fornecedor")
        resp = self.client.post(f"/api/fiscal/notas-entrada/?empresa={self.empresa_a.id}", payload, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("fornecedor", resp.data)

    def test_nota_sem_pedido_rejeita_loja_e_fornecedor_de_outra_empresa(self):
        resp = self.client.post(
            f"/api/fiscal/notas-entrada/?empresa={self.empresa_a.id}",
            self.payload_nota_sem_pedido(numero="403", loja=self.loja_b),
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("loja", resp.data)

        resp = self.client.post(
            f"/api/fiscal/notas-entrada/?empresa={self.empresa_a.id}",
            self.payload_nota_sem_pedido(numero="404", fornecedor=self.fornecedor_b),
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("fornecedor", resp.data)

    def test_duplicidade_sem_pedido_usa_empresa_fornecedor_modelo_serie_numero(self):
        payload = self.payload_nota_sem_pedido(numero="405")
        resp = self.client.post(f"/api/fiscal/notas-entrada/?empresa={self.empresa_a.id}", payload, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)

        resp = self.client.post(f"/api/fiscal/notas-entrada/?empresa={self.empresa_a.id}", payload, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("numero", resp.data)

    def test_filtros_funcionam_para_notas_com_e_sem_pedido(self):
        resp = self.client.post(
            f"/api/fiscal/notas-entrada/?empresa={self.empresa_a.id}",
            self.payload_nota_sem_pedido(numero="406"),
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)

        resp = self.client.get("/api/fiscal/notas-entrada/", {"empresa": self.empresa_a.id, "fornecedor": self.fornecedor_a.id})
        notas = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        numeros = {n["numero"] for n in notas}
        self.assertIn("100", numeros)
        self.assertIn("406", numeros)

        resp = self.client.get("/api/fiscal/notas-entrada/", {"empresa": self.empresa_a.id, "loja": self.loja_a.id})
        notas = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        numeros = {n["numero"] for n in notas}
        self.assertIn("100", numeros)
        self.assertIn("406", numeros)

    def test_itens_respeitam_empresa_e_filtros_nao_escapam_tenant(self):
        resp = self.client.get(f"/api/fiscal/notas-entrada-itens/{self.nota_item_b.id}/", {"empresa": self.empresa_a.id})
        self.assertEqual(resp.status_code, 404)

        resp = self.client.get("/api/fiscal/notas-entrada-itens/", {"empresa": self.empresa_a.id, "nota": self.nota_b.id})
        itens = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertEqual(itens, [])

        resp = self.client.get("/api/fiscal/notas-entrada-itens/", {"empresa": self.empresa_a.id, "pedido_item": self.item_b.id})
        itens = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertEqual(itens, [])

    def test_create_e_update_item_bloqueiam_relacionamentos_cruzados(self):
        resp = self.client.post(
            f"/api/fiscal/notas-entrada-itens/?empresa={self.empresa_a.id}",
            self.payload_item(self.nota_b, self.item_b),
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.data)

        resp = self.client.post(
            f"/api/fiscal/notas-entrada-itens/?empresa={self.empresa_a.id}",
            self.payload_item(self.nota_a, self.item_b),
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.data)

        resp = self.client.patch(
            f"/api/fiscal/notas-entrada-itens/{self.nota_item_a.id}/?empresa={self.empresa_a.id}",
            {"pedido_item": self.item_b.id},
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_delete_direto_de_nota_fiscal_entrada_e_bloqueado(self):
        resp = self.client.delete(f"/api/fiscal/notas-entrada/{self.nota_a.id}/", {"empresa": self.empresa_a.id})
        self.assertEqual(resp.status_code, 405, resp.data)
        self.assertTrue(NotaFiscalEntrada.objects.filter(pk=self.nota_a.pk).exists())


@override_settings(ALLOWED_HOSTS=["testserver"])
class NotaFiscalEntradaPermissoesBloco4Tests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.compras_modulo = ModuloSistema.objects.update_or_create(
            chave="compras",
            defaults={"nome": "Compras", "categoria": ModuloSistema.CATEGORIA_COMERCIAL, "basico": False, "ativo": True},
        )[0]
        self.fiscal_modulo = ModuloSistema.objects.update_or_create(
            chave="fiscal",
            defaults={"nome": "Fiscal", "categoria": ModuloSistema.CATEGORIA_COMERCIAL, "basico": False, "ativo": True},
        )[0]
        self.empresa = self.criar_empresa("Empresa Permissoes", "44444444000191")
        self.outra_empresa = self.criar_empresa("Outra Empresa Permissoes", "55555555000191")
        self.loja = Loja.objects.create(
            empresa=self.empresa,
            nome_loja="Loja Permissoes",
            apelido_loja="LP",
            cnpj="44444444000100",
            estado="SP",
        )
        self.outra_loja = Loja.objects.create(
            empresa=self.outra_empresa,
            nome_loja="Loja Outra Permissoes",
            apelido_loja="LOP",
            cnpj="55555555000100",
            estado="SP",
        )
        self.fornecedor = self.criar_fornecedor(self.empresa, "Fornecedor Permissoes", "44445678000195")
        self.outra_fornecedor = self.criar_fornecedor(self.outra_empresa, "Fornecedor Outra Permissoes", "55545678000195")
        self.unidade = Unidade.objects.create(empresa=self.empresa, Descricao="Unidade", Codigo="UN")
        self.outra_unidade = Unidade.objects.create(empresa=self.outra_empresa, Descricao="Unidade Outra", Codigo="UO")
        self.produto = Produto.objects.create(empresa=self.empresa, tipo_produto="2", descricao="Uso Permissoes", unidade=self.unidade)
        self.outra_produto = Produto.objects.create(
            empresa=self.outra_empresa,
            tipo_produto="2",
            descricao="Uso Outra Permissoes",
            unidade=self.outra_unidade,
        )
        self.pedido = self.criar_pedido(self.empresa, self.loja, self.fornecedor)
        self.outra_pedido = self.criar_pedido(self.outra_empresa, self.outra_loja, self.outra_fornecedor)
        self.pedido_item = self.criar_item(self.pedido, self.produto)
        self.outra_pedido_item = self.criar_item(self.outra_pedido, self.outra_produto)
        self.nota = self.criar_nota(self.pedido, "9001")
        self.outra_nota = self.criar_nota(self.outra_pedido, "9002")
        self.nota_item = NotaFiscalEntradaItem.objects.create(
            nota=self.nota,
            pedido_item=self.pedido_item,
            qtd_recebida=Decimal("2.000"),
            preco_unit_nf=Decimal("10.0000"),
            total_item=Decimal("20.00"),
        )
        self.outra_nota_item = NotaFiscalEntradaItem.objects.create(
            nota=self.outra_nota,
            pedido_item=self.outra_pedido_item,
            qtd_recebida=Decimal("1.000"),
            preco_unit_nf=Decimal("10.0000"),
            total_item=Decimal("10.00"),
        )
        self.user_compras_edit = self.criar_usuario("compras-edit", self.empresa, compras=UserModulePermission.Access.EDIT)
        self.user_compras_view = self.criar_usuario("compras-view", self.empresa, compras=UserModulePermission.Access.VIEW)
        self.user_sem_compras = self.criar_usuario("sem-compras", self.empresa)
        self.user_fiscal_edit = self.criar_usuario("fiscal-edit", self.empresa, fiscal=UserModulePermission.Access.EDIT)
        self.user_outra_empresa = self.criar_usuario("compras-outra", self.outra_empresa, compras=UserModulePermission.Access.EDIT)

    def criar_empresa(self, nome, documento):
        empresa = Empresa.objects.create(nome=nome, documento=documento, plano_completo=False, usa_compras=True, usa_fiscal=False)
        EmpresaContrato.objects.update_or_create(
            empresa=empresa,
            defaults={
                "status": EmpresaContrato.STATUS_ATIVO,
                "plano_completo": False,
                "limite_sessoes_simultaneas": 3,
            },
        )
        EmpresaModulo.objects.update_or_create(empresa=empresa, modulo=self.compras_modulo, defaults={"contratado": True})
        EmpresaModulo.objects.update_or_create(empresa=empresa, modulo=self.fiscal_modulo, defaults={"contratado": True})
        return empresa

    def criar_usuario(self, username, empresa, compras=None, fiscal=None):
        perfil = PerfilAcesso.objects.create(empresa=empresa, nome=f"Perfil {username}")
        if compras:
            PerfilModuloPermissao.objects.create(perfil=perfil, modulo=self.compras_modulo, acesso=compras)
        if fiscal:
            PerfilModuloPermissao.objects.create(perfil=perfil, modulo=self.fiscal_modulo, acesso=fiscal)
        return get_user_model().objects.create_user(
            username=username,
            password="12345678",
            type="Gerente",
            empresa=empresa,
            perfil_principal=perfil,
        )

    def criar_fornecedor(self, empresa, nome, documento):
        return Fornecedor.objects.create(
            empresa=empresa,
            tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA,
            documento=documento,
            cnpj=documento,
            nome_fornecedor=nome,
            categoria="USO_CONSUMO",
        )

    def criar_pedido(self, empresa, loja, fornecedor):
        return PedidoCompra.objects.create(
            empresa=empresa,
            tipo="2",
            loja=loja,
            fornecedor=fornecedor,
            status="AP",
            total_pedido=Decimal("20.00"),
        )

    def criar_item(self, pedido, produto):
        return PedidoCompraItem.objects.create(
            pedido=pedido,
            produto=produto,
            qtd=Decimal("2.000"),
            preco_unit=Decimal("10.00"),
            total_item=Decimal("20.00"),
        )

    def criar_nota(self, pedido, numero):
        return NotaFiscalEntrada.objects.create(
            pedido_compra=pedido,
            numero=numero,
            dt_emissao=timezone.localdate(),
            dt_entrada=timezone.localdate(),
        )

    def test_usuario_com_compras_sem_fiscal_lista_abre_itens_e_endpoints_operacionais(self):
        self.client.force_authenticate(self.user_compras_edit)

        lista = self.client.get("/api/fiscal/notas-entrada/")
        notas = lista.data.get("results", lista.data) if isinstance(lista.data, dict) else lista.data
        self.assertEqual(lista.status_code, 200)
        self.assertIn(self.nota.id, [nota["id"] for nota in notas])

        detalhe = self.client.get(f"/api/fiscal/notas-entrada/{self.nota.id}/")
        self.assertEqual(detalhe.status_code, 200)
        self.assertEqual(detalhe.data["id"], self.nota.id)

        itens = self.client.get("/api/fiscal/notas-entrada-itens/", {"nota": self.nota.id})
        itens_data = itens.data.get("results", itens.data) if isinstance(itens.data, dict) else itens.data
        self.assertEqual(itens.status_code, 200)
        self.assertEqual([item["id"] for item in itens_data], [self.nota_item.id])

        itens_pedido = self.client.get(f"/api/fiscal/notas-entrada/{self.nota.id}/itens-pedido/")
        self.assertEqual(itens_pedido.status_code, 200)
        self.assertEqual(itens_pedido.data[0]["pedido_item"], self.pedido_item.id)

        fechar = self.client.post(f"/api/fiscal/notas-entrada/{self.nota.id}/fechar/", {}, format="json")
        self.assertEqual(fechar.status_code, 200, fechar.data)

        cancelar = self.client.post(f"/api/fiscal/notas-entrada/{self.nota.id}/cancelar/", {"motivo": "Teste"}, format="json")
        self.assertEqual(cancelar.status_code, 200, cancelar.data)

    def test_usuario_sem_compras_e_usuario_apenas_fiscal_sao_bloqueados(self):
        for user in (self.user_sem_compras, self.user_fiscal_edit):
            with self.subTest(user=user.username):
                self.client.force_authenticate(user)
                lista = self.client.get("/api/fiscal/notas-entrada/")
                detalhe = self.client.get(f"/api/fiscal/notas-entrada/{self.nota.id}/")
                itens = self.client.get("/api/fiscal/notas-entrada-itens/", {"nota": self.nota.id})
                fechar = self.client.post(f"/api/fiscal/notas-entrada/{self.nota.id}/fechar/", {}, format="json")
                self.assertEqual(lista.status_code, 403)
                self.assertEqual(detalhe.status_code, 403)
                self.assertEqual(itens.status_code, 403)
                self.assertEqual(fechar.status_code, 403)

    def test_usuario_de_outra_empresa_com_compras_nao_acessa_registros_alheios(self):
        self.client.force_authenticate(self.user_outra_empresa)

        lista = self.client.get("/api/fiscal/notas-entrada/")
        notas = lista.data.get("results", lista.data) if isinstance(lista.data, dict) else lista.data
        self.assertEqual(lista.status_code, 200)
        self.assertNotIn(self.nota.id, [nota["id"] for nota in notas])
        self.assertIn(self.outra_nota.id, [nota["id"] for nota in notas])

        detalhe = self.client.get(f"/api/fiscal/notas-entrada/{self.nota.id}/")
        item = self.client.get(f"/api/fiscal/notas-entrada-itens/{self.nota_item.id}/")
        self.assertEqual(detalhe.status_code, 404)
        self.assertEqual(item.status_code, 404)

    def test_nivel_view_em_compras_mantem_leitura_e_bloqueia_escrita(self):
        self.client.force_authenticate(self.user_compras_view)

        lista = self.client.get("/api/fiscal/notas-entrada/")
        criar = self.client.post(
            "/api/fiscal/notas-entrada/",
            {
                "pedido_compra": self.pedido.id,
                "modelo": "55",
                "serie": "2",
                "numero": "9003",
                "dt_emissao": timezone.localdate().isoformat(),
                "dt_entrada": timezone.localdate().isoformat(),
            },
            format="json",
        )

        self.assertEqual(lista.status_code, 200)
        self.assertEqual(criar.status_code, 403)


@override_settings(ALLOWED_HOSTS=["testserver"])
class NotaFiscalEntradaPaginacaoFiltrosBloco5Tests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa = Empresa.objects.create(nome="Empresa Bloco 5", documento="66666666000191", plano_completo=True)
        self.outra_empresa = Empresa.objects.create(nome="Outra Empresa Bloco 5", documento="77777777000191", plano_completo=True)
        self.compras_modulo = ModuloSistema.objects.update_or_create(
            chave="compras",
            defaults={"nome": "Compras", "categoria": ModuloSistema.CATEGORIA_COMERCIAL, "basico": False, "ativo": True},
        )[0]
        EmpresaModulo.objects.update_or_create(empresa=self.empresa, modulo=self.compras_modulo, defaults={"contratado": True})
        perfil = PerfilAcesso.objects.create(empresa=self.empresa, nome="Compras B5")
        PerfilModuloPermissao.objects.create(
            perfil=perfil,
            modulo=self.compras_modulo,
            acesso=UserModulePermission.Access.EDIT,
        )
        self.user = get_user_model().objects.create_user(
            username="nf-b5",
            password="test",
            type="Gerente",
            empresa=self.empresa,
            perfil_principal=perfil,
        )
        self.client.force_authenticate(self.user)
        self.loja_a = Loja.objects.create(empresa=self.empresa, nome_loja="Loja B5 A", apelido_loja="B5A", cnpj="66666666000100", estado="SP")
        self.loja_b = Loja.objects.create(empresa=self.empresa, nome_loja="Loja B5 B", apelido_loja="B5B", cnpj="66666666000200", estado="SP")
        self.outra_loja = Loja.objects.create(empresa=self.outra_empresa, nome_loja="Loja B5 Outra", apelido_loja="B5O", cnpj="77777777000100", estado="SP")
        self.fornecedor_a = self.criar_fornecedor(self.empresa, "Fornecedor Alpha B5", "66645678000195")
        self.fornecedor_b = self.criar_fornecedor(self.empresa, "Fornecedor Beta B5", "66645678000276")
        self.outra_fornecedor = self.criar_fornecedor(self.outra_empresa, "Fornecedor Outra B5", "77745678000195")
        self.pedido_a = self.criar_pedido(self.empresa, self.loja_a, self.fornecedor_a)
        self.pedido_b = self.criar_pedido(self.empresa, self.loja_b, self.fornecedor_b)
        self.outra_pedido = self.criar_pedido(self.outra_empresa, self.outra_loja, self.outra_fornecedor)
        self.nota_ab = self.criar_nota(self.pedido_a, "1001", "2026-01-01", "2026-01-02", "AB", "100.00", "35140130290862000106550010000000011000000016")
        self.nota_fe = self.criar_nota(self.pedido_a, "1002", "2026-01-10", "2026-01-11", "FE", "200.00", "35140130290862000106550010000000021000000011")
        self.nota_ca = self.criar_nota(self.pedido_b, "2001", "2026-02-01", "2026-02-02", "CA", "300.00", "35140130290862000106550010000000031000000006")
        self.nota_combo = self.criar_nota(self.pedido_b, "2002", "2026-02-10", "2026-02-11", "FE", "400.00", "35140130290862000106550010000000041000000000")
        self.outra_nota = self.criar_nota(self.outra_pedido, "9999", "2026-03-01", "2026-03-02", "AB", "999.00", "35140130290862000106550010000000051000000005")

    def criar_fornecedor(self, empresa, nome, documento):
        return Fornecedor.objects.create(
            empresa=empresa,
            tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA,
            documento=documento,
            cnpj=documento,
            nome_fornecedor=nome,
            categoria="USO_CONSUMO",
        )

    def criar_pedido(self, empresa, loja, fornecedor):
        return PedidoCompra.objects.create(empresa=empresa, tipo="2", loja=loja, fornecedor=fornecedor, status="AP")

    def criar_nota(self, pedido, numero, emissao, entrada, status_nf, valor, chave):
        return NotaFiscalEntrada.objects.create(
            pedido_compra=pedido,
            numero=numero,
            dt_emissao=emissao,
            dt_entrada=entrada,
            status=status_nf,
            valor_total=Decimal(valor),
            valor_produtos=Decimal(valor),
            chave_acesso=chave,
        )

    def results(self, response):
        self.assertIn(response.status_code, (200,), response.data)
        return response.data["results"]

    def ids(self, response):
        return [row["id"] for row in self.results(response)]

    def test_paginacao_retorna_limite_proxima_pagina_count_e_isola_empresa(self):
        resp1 = self.client.get("/api/fiscal/notas-entrada/", {"page": 1, "page_size": 2})
        resp2 = self.client.get("/api/fiscal/notas-entrada/", {"page": 2, "page_size": 2})

        self.assertEqual(resp1.data["count"], 4)
        self.assertEqual(len(resp1.data["results"]), 2)
        self.assertEqual(len(resp2.data["results"]), 2)
        self.assertNotEqual(self.ids(resp1), self.ids(resp2))
        self.assertNotIn(self.outra_nota.id, self.ids(resp1) + self.ids(resp2))

    def test_filtros_status_pedido_fornecedor_loja_numero_chave_e_search(self):
        checks = [
            ({"status": "AB"}, [self.nota_ab.id]),
            ({"status": "FE"}, [self.nota_combo.id, self.nota_fe.id]),
            ({"status": "CA"}, [self.nota_ca.id]),
            ({"pedido": self.pedido_a.id}, [self.nota_fe.id, self.nota_ab.id]),
            ({"fornecedor": self.fornecedor_b.id}, [self.nota_combo.id, self.nota_ca.id]),
            ({"loja": self.loja_a.id}, [self.nota_fe.id, self.nota_ab.id]),
            ({"numero": "200"}, [self.nota_combo.id, self.nota_ca.id]),
            ({"chave_acesso": self.nota_ca.chave_acesso[-8:]}, [self.nota_ca.id]),
            ({"search": "Alpha"}, [self.nota_fe.id, self.nota_ab.id]),
        ]
        for params, expected in checks:
            with self.subTest(params=params):
                resp = self.client.get("/api/fiscal/notas-entrada/", params)
                self.assertEqual(self.ids(resp), expected)
                self.assertEqual(resp.data["count"], len(expected))

    def test_filtros_periodo_emissao_entrada_valor_e_combinacao_sao_inclusivos(self):
        checks = [
            ({"dt_emissao_de": "2026-01-10"}, [self.nota_combo.id, self.nota_ca.id, self.nota_fe.id]),
            ({"dt_emissao_ate": "2026-02-01"}, [self.nota_ca.id, self.nota_fe.id, self.nota_ab.id]),
            ({"dt_emissao_de": "2026-01-10", "dt_emissao_ate": "2026-02-01"}, [self.nota_ca.id, self.nota_fe.id]),
            ({"dt_entrada_de": "2026-01-11", "dt_entrada_ate": "2026-02-02"}, [self.nota_ca.id, self.nota_fe.id]),
            ({"valor_min": "200.00"}, [self.nota_combo.id, self.nota_ca.id, self.nota_fe.id]),
            ({"valor_max": "300.00"}, [self.nota_ca.id, self.nota_fe.id, self.nota_ab.id]),
            ({"fornecedor": self.fornecedor_b.id, "status": "FE", "valor_min": "350.00"}, [self.nota_combo.id]),
        ]
        for params, expected in checks:
            with self.subTest(params=params):
                resp = self.client.get("/api/fiscal/notas-entrada/", params)
                self.assertEqual(self.ids(resp), expected)
                self.assertEqual(resp.data["count"], len(expected))

    def test_indicadores_consideram_conjunto_filtrado_completo_nao_apenas_pagina(self):
        resp = self.client.get(
            "/api/fiscal/notas-entrada/indicadores/",
            {"page": 1, "page_size": 1, "valor_min": "200.00"},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["total"], 3)
        self.assertEqual(resp.data["abertas"], 0)
        self.assertEqual(resp.data["fechadas"], 2)
        self.assertEqual(resp.data["canceladas"], 1)
        self.assertEqual(resp.data["valor_total"], "900.00")


@override_settings(ALLOWED_HOSTS=["testserver"])
class NotaFiscalEntradaValidacoesBloco7Tests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa = Empresa.objects.create(nome="Empresa Bloco 7", documento="88888888000191", plano_completo=True)
        self.user = get_user_model().objects.create_superuser(
            "nf-b7",
            "nf-b7@sysvar.test",
            "test",
            type="Gerente",
            empresa=self.empresa,
        )
        self.client.force_authenticate(self.user)
        self.loja = Loja.objects.create(empresa=self.empresa, nome_loja="Loja B7", apelido_loja="B7", cnpj="88888888000100", estado="SP")
        self.fornecedor = Fornecedor.objects.create(
            empresa=self.empresa,
            tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA,
            documento="88845678000195",
            cnpj="88845678000195",
            nome_fornecedor="Fornecedor B7",
            categoria="USO_CONSUMO",
        )
        self.unidade = Unidade.objects.create(empresa=self.empresa, Descricao="Unidade", Codigo="UN")
        self.produto = Produto.objects.create(empresa=self.empresa, tipo_produto="2", descricao="Uso B7", unidade=self.unidade)
        self.pedido = PedidoCompra.objects.create(empresa=self.empresa, tipo="2", loja=self.loja, fornecedor=self.fornecedor, status="AP")
        self.pedido_item = PedidoCompraItem.objects.create(
            pedido=self.pedido,
            produto=self.produto,
            qtd=Decimal("10.000"),
            preco_unit=Decimal("5.00"),
            total_item=Decimal("50.00"),
        )
        self.nota = NotaFiscalEntrada.objects.create(
            pedido_compra=self.pedido,
            numero="7001",
            dt_emissao="2026-08-10",
            dt_entrada="2026-08-10",
        )

    def item_payload(self, desconto="0.00", qtd="10.000", preco="5.0000"):
        return {
            "nota": self.nota.id,
            "pedido_item": self.pedido_item.id,
            "qtd_recebida": qtd,
            "preco_unit_nf": preco,
            "desconto_item": desconto,
        }

    def nota_payload(self, emissao, entrada, numero="7002"):
        return {
            "pedido_compra": self.pedido.id,
            "modelo": "55",
            "serie": "1",
            "numero": numero,
            "dt_emissao": emissao,
            "dt_entrada": entrada,
        }

    def test_desconto_igual_ao_bruto_e_permitido_e_total_item_zero(self):
        resp = self.client.post("/api/fiscal/notas-entrada-itens/", self.item_payload(desconto="50.00"), format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        item = NotaFiscalEntradaItem.objects.get(pk=resp.data["id"])
        self.assertEqual(item.total_item, Decimal("0.00"))
        self.nota.refresh_from_db()
        self.assertEqual(self.nota.valor_total, Decimal("0.00"))

    def test_desconto_maior_que_bruto_e_desconto_negativo_sao_rejeitados(self):
        maior = self.client.post("/api/fiscal/notas-entrada-itens/", self.item_payload(desconto="50.01"), format="json")
        negativo = self.client.post("/api/fiscal/notas-entrada-itens/", self.item_payload(desconto="-0.01"), format="json")

        self.assertEqual(maior.status_code, 400)
        self.assertIn("desconto_item", maior.data)
        self.assertEqual(negativo.status_code, 400)
        self.assertIn("desconto_item", negativo.data)
        self.assertFalse(NotaFiscalEntradaItem.objects.filter(total_item__lt=0).exists())

    def test_preco_e_quantidade_negativos_continuam_rejeitados(self):
        preco = self.client.post("/api/fiscal/notas-entrada-itens/", self.item_payload(preco="-1.0000"), format="json")
        qtd = self.client.post("/api/fiscal/notas-entrada-itens/", self.item_payload(qtd="-1.000"), format="json")

        self.assertEqual(preco.status_code, 400)
        self.assertIn("preco_unit_nf", preco.data)
        self.assertEqual(qtd.status_code, 400)
        self.assertIn("qtd_recebida", qtd.data)

    def test_nf_recalcula_total_valido_e_impede_total_negativo(self):
        item = NotaFiscalEntradaItem.objects.create(
            nota=self.nota,
            pedido_item=self.pedido_item,
            qtd_recebida=Decimal("10.000"),
            preco_unit_nf=Decimal("5.0000"),
            desconto_item=Decimal("10.00"),
            total_item=Decimal("40.00"),
        )
        self.nota.recalcular_totais()
        self.nota.refresh_from_db()
        self.assertEqual(self.nota.valor_total, Decimal("40.00"))

        item.desconto_item = Decimal("60.00")
        item.save(update_fields=["desconto_item"])
        with self.assertRaises(ValueError):
            self.nota.recalcular_totais()

    def test_datas_de_emissao_e_entrada_mesmo_dia_ou_entrada_posterior_sao_permitidas(self):
        mesmo_dia = self.client.post(
            "/api/fiscal/notas-entrada/",
            self.nota_payload("2026-08-10", "2026-08-10", "7003"),
            format="json",
        )
        posterior = self.client.post(
            "/api/fiscal/notas-entrada/",
            self.nota_payload("2026-08-10", "2026-08-11", "7004"),
            format="json",
        )

        self.assertEqual(mesmo_dia.status_code, 201, mesmo_dia.data)
        self.assertEqual(posterior.status_code, 201, posterior.data)

    def test_data_de_entrada_anterior_a_emissao_e_rejeitada_em_criacao_e_edicao_aberta(self):
        criacao = self.client.post(
            "/api/fiscal/notas-entrada/",
            self.nota_payload("2026-08-11", "2026-08-10", "7005"),
            format="json",
        )
        edicao = self.client.patch(
            f"/api/fiscal/notas-entrada/{self.nota.id}/",
            {"dt_emissao": "2026-08-11", "dt_entrada": "2026-08-10"},
            format="json",
        )

        self.assertEqual(criacao.status_code, 400)
        self.assertIn("dt_entrada", criacao.data)
        self.assertEqual(edicao.status_code, 400)
        self.assertIn("dt_entrada", edicao.data)

    def test_validacao_de_saldo_permanece_funcionando(self):
        resp = self.client.post("/api/fiscal/notas-entrada-itens/", self.item_payload(qtd="10.001"), format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("qtd_recebida", resp.data)


@override_settings(ALLOWED_HOSTS=["testserver"])
class NotaFiscalEntradaCancelamentoBloco2Tests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa = Empresa.objects.create(nome="Empresa Bloco 2", documento="33333333000191", plano_completo=True)
        self.user = get_user_model().objects.create_superuser("nf-cancel", "nf-cancel@sysvar.test", "test")
        self.client.force_authenticate(self.user)
        self.loja = Loja.objects.create(
            empresa=self.empresa,
            nome_loja="Loja B2",
            apelido_loja="Loja B2",
            cnpj="33333333000100",
            estado="SP",
            EstoqueNegativo="NAO",
        )
        self.fornecedor = Fornecedor.objects.create(
            empresa=self.empresa,
            tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA,
            documento="33345678000195",
            cnpj="33345678000195",
            nome_fornecedor="Fornecedor B2",
            categoria="USO_CONSUMO",
        )
        self.natureza = Nat_Lancamento.objects.create(
            empresa=self.empresa,
            codigo="CMPB2",
            categoria_principal="Compras",
            subcategoria="NF",
            descricao="Compra NF",
            tipo="SAIDA",
            status="ATIVO",
            tipo_natureza="D",
        )
        self.unidade = Unidade.objects.create(empresa=self.empresa, Descricao="Unidade", Codigo="UN")

    def criar_produto(self, tipo="4", descricao="Produto"):
        return Produto.objects.create(empresa=self.empresa, tipo_produto=tipo, descricao=descricao, unidade=self.unidade)

    def criar_pedido(self, produto, qtd=Decimal("10.000"), preco=Decimal("10.00"), tipo="2"):
        pedido = PedidoCompra.objects.create(
            empresa=self.empresa,
            tipo=tipo,
            loja=self.loja,
            fornecedor=self.fornecedor,
            status="AP",
            total_pedido=qtd * preco,
        )
        item = PedidoCompraItem.objects.create(
            pedido=pedido,
            produto=produto,
            qtd=qtd,
            preco_unit=preco,
            total_item=qtd * preco,
        )
        self.criar_previsao(pedido)
        return pedido, item

    def criar_previsao(self, pedido, valor=None):
        valor = Decimal(valor or pedido.total_pedido).quantize(Decimal("0.01"))
        titulo = Pagar.objects.create(
            empresa=self.empresa,
            idloja=self.loja,
            idfornecedor=self.fornecedor,
            Titulo=f"PC {pedido.pk}",
            Data_emissao=timezone.localdate(),
            Valor_total=valor,
            Previsao=True,
            FormaPagamento="BOL",
            Idnatureza=self.natureza,
            pedido_compra=pedido.pk,
        )
        PagarItem.objects.create(
            Idpagar=titulo,
            parcela_n=1,
            status=PagarItem.STATUS_PREVISTO,
            Data_vencimento=timezone.localdate(),
            valor_parcela=valor,
            FormaPagamento="BOL",
            Previsao=True,
            Idnatureza=self.natureza,
        )
        return titulo

    def criar_nota(self, pedido, item, numero, qtd, preco):
        nota = NotaFiscalEntrada.objects.create(
            pedido_compra=pedido,
            numero=numero,
            dt_emissao=timezone.localdate(),
            dt_entrada=timezone.localdate(),
        )
        NotaFiscalEntradaItem.objects.create(
            nota=nota,
            pedido_item=item,
            qtd_recebida=qtd,
            preco_unit_nf=preco,
            total_item=(qtd * preco).quantize(Decimal("0.01")),
        )
        return nota

    def fechar(self, nota):
        resp = self.client.post(f"/api/fiscal/notas-entrada/{nota.pk}/fechar/?empresa={self.empresa.pk}", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        nota.refresh_from_db()
        return resp

    def cancelar(self, nota, status_code=200, confirmar_avisos=True):
        resp = self.client.post(
            f"/api/fiscal/notas-entrada/{nota.pk}/cancelar/?empresa={self.empresa.pk}",
            {"motivo": "Teste de cancelamento", "confirmar_avisos": confirmar_avisos},
            format="json",
        )
        self.assertEqual(resp.status_code, status_code, resp.data)
        nota.refresh_from_db()
        return resp

    def financeiro(self, pedido):
        return list(Pagar.objects.filter(pedido_compra=pedido.pk).order_by("Idpagar"))

    def test_cancelamento_nf_total_restaura_previsao(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto)
        nota = self.criar_nota(pedido, item, "900", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota)
        self.assertTrue(Pagar.objects.get(nfe_id=nota.pk).itens.filter(status=PagarItem.STATUS_EFETIVO).exists())

        self.cancelar(nota)
        self.assertFalse(Pagar.objects.filter(nfe_id=nota.pk).exists())
        previsao = Pagar.objects.get(pedido_compra=pedido.pk, nfe_id__isnull=True, Previsao=True)
        self.assertEqual(previsao.Valor_total, Decimal("100.00"))
        self.assertEqual(previsao.itens.get().status, PagarItem.STATUS_PREVISTO)
        nota.refresh_from_db()
        self.assertEqual(nota.motivo_cancelamento, "Teste de cancelamento")
        self.assertEqual(nota.cancelado_por, self.user)
        self.assertTrue(AuditLog.objects.filter(model="notafiscalentrada", object_id=str(nota.pk), metadata__cancelamento_operacional=True).exists())

    def test_cancelamento_exige_motivo(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto)
        nota = self.criar_nota(pedido, item, "920", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota)

        resp = self.client.post(f"/api/fiscal/notas-entrada/{nota.pk}/cancelar/?empresa={self.empresa.pk}", {"motivo": "   "}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(NotaFiscalEntrada.objects.get(pk=nota.pk).status, NotaFiscalEntrada.Status.FECHADA)

    def test_cancelamento_parcial_preserva_financeiro_de_outra_nf(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto)
        nota1 = self.criar_nota(pedido, item, "901", Decimal("4.000"), Decimal("10.00"))
        nota2 = self.criar_nota(pedido, item, "902", Decimal("6.000"), Decimal("10.00"))
        self.fechar(nota1)
        self.fechar(nota2)

        self.cancelar(nota1)
        self.assertTrue(Pagar.objects.filter(nfe_id=nota2.pk, Previsao=False).exists())
        previsao = Pagar.objects.get(pedido_compra=pedido.pk, nfe_id__isnull=True, Previsao=True)
        self.assertEqual(previsao.Valor_total, Decimal("40.00"))

    def test_cancelamento_da_segunda_nf_preserva_primeira(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto)
        nota1 = self.criar_nota(pedido, item, "903", Decimal("4.000"), Decimal("10.00"))
        nota2 = self.criar_nota(pedido, item, "904", Decimal("6.000"), Decimal("10.00"))
        self.fechar(nota1)
        self.fechar(nota2)

        self.cancelar(nota2)
        self.assertTrue(Pagar.objects.filter(nfe_id=nota1.pk, Previsao=False).exists())
        previsao = Pagar.objects.get(pedido_compra=pedido.pk, nfe_id__isnull=True, Previsao=True)
        self.assertEqual(previsao.Valor_total, Decimal("60.00"))

    def test_cancelamento_bloqueia_parcela_baixada_e_faz_rollback(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto)
        nota = self.criar_nota(pedido, item, "905", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota)
        PagarItem.objects.filter(Idpagar__nfe_id=nota.pk).update(status=PagarItem.STATUS_BAIXADO, valor_baixa=Decimal("100.00"), data_baixa=timezone.localdate())

        self.cancelar(nota, status_code=400)
        self.assertEqual(nota.status, NotaFiscalEntrada.Status.FECHADA)
        self.assertTrue(Pagar.objects.filter(nfe_id=nota.pk).exists())

    def test_cancelamento_bloqueia_movimentacao_financeira_vinculada(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto)
        nota = self.criar_nota(pedido, item, "917", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota)
        parcela = PagarItem.objects.get(Idpagar__nfe_id=nota.pk)
        MovimentacaoFinanceira.objects.create(
            empresa=self.empresa,
            idloja=self.loja,
            tipo=MovimentacaoFinanceira.TIPO_SAIDA,
            status=MovimentacaoFinanceira.STATUS_EFETIVA,
            origem=MovimentacaoFinanceira.ORIGEM_PAGAR,
            valor=Decimal("100.00"),
            historico="Baixa teste",
            documento="NF917",
            pagar_item=parcela,
        )

        self.cancelar(nota, status_code=400)
        self.assertEqual(nota.status, NotaFiscalEntrada.Status.FECHADA)
        self.assertTrue(Pagar.objects.filter(nfe_id=nota.pk).exists())

    def test_cancelamento_repetido_nao_duplica_estornos_ou_previsoes(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto)
        nota = self.criar_nota(pedido, item, "906", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota)
        self.cancelar(nota)
        self.cancelar(nota, status_code=400)
        self.assertEqual(Pagar.objects.filter(pedido_compra=pedido.pk, Previsao=True).count(), 1)
        self.assertEqual(EstoqueMovimentacao.objects.filter(documento=f"NFE:{nota.pk}:CANCEL").count(), 1)

    def test_uso_consumo_estorna_estoque_e_recalcula_custos(self):
        produto = self.criar_produto(tipo="2")
        pedido, item = self.criar_pedido(produto, qtd=Decimal("5.000"), preco=Decimal("20.00"))
        nota = self.criar_nota(pedido, item, "907", Decimal("5.000"), Decimal("20.00"))
        self.fechar(nota)
        self.assertEqual(ProdutoUsoConsumoEstoque.objects.get(empresa=self.empresa, produto=produto, loja=self.loja).saldo, Decimal("5.000"))
        self.assertTrue(ProdutoUsoConsumoMovimentacao.objects.filter(documento=f"NFE:{nota.pk}:ENTRADA", produto=produto).exists())
        self.assertFalse(EstoqueMovimentacao.objects.filter(documento=f"NFE:{nota.pk}:ENTRADA").exists())
        self.assertEqual(produto.__class__.objects.get(pk=produto.pk).custo_medio, Decimal("20.0000"))

        self.cancelar(nota)
        self.assertEqual(ProdutoUsoConsumoEstoque.objects.get(empresa=self.empresa, produto=produto, loja=self.loja).saldo, Decimal("0.000"))
        self.assertTrue(ProdutoUsoConsumoMovimentacao.objects.filter(documento=f"NFE:{nota.pk}:CANCEL", produto=produto).exists())
        self.assertFalse(EstoqueMovimentacao.objects.filter(documento=f"NFE:{nota.pk}:CANCEL").exists())
        self.assertEqual(produto.__class__.objects.get(pk=produto.pk).custo_medio, Decimal("0.0000"))

    def criar_revenda(self):
        grade = Grade.objects.create(empresa=self.empresa, Descricao="Grade")
        tamanho = Tamanho.objects.create(empresa=self.empresa, idgrade=grade, Tamanho="P", Descricao="P")
        cor = Cor.objects.create(empresa=self.empresa, Descricao="Azul", Codigo="AZ", Cor="Azul")
        grupo = Grupo.objects.create(empresa=self.empresa, Codigo="01", CodigoRef="01", Descricao="Grupo", Margem=0)
        colecao = Colecao.objects.create(empresa=self.empresa, Descricao="Colecao", Codigo="26", Estacao="01", Status="AT")
        produto = Produto.objects.create(
            empresa=self.empresa,
            tipo_produto="1",
            descricao="Revenda",
            unidade=self.unidade,
            grade=grade,
            grupo=grupo,
            colecao=colecao,
        )
        ConfigEan.objects.create(empresa=self.empresa, country_prefix="789", company_prefix="3333", ativo=True)
        sku = ProdutoDetalhe.objects.create(produto=produto, idcor=cor, idtamanho=tamanho)
        pack = Pack.objects.create(empresa=self.empresa, nome="Pack 1", grade=grade)
        PackItem.objects.create(pack=pack, tamanho=tamanho, qtd=1)
        pedido = PedidoCompra.objects.create(
            empresa=self.empresa,
            tipo="1",
            loja=self.loja,
            fornecedor=self.fornecedor,
            status="AP",
            total_pedido=Decimal("100.00"),
        )
        item = PedidoCompraItem.objects.create(
            pedido=pedido,
            produto=produto,
            cor=cor,
            pack=pack,
            n_packs=10,
            qtd=Decimal("10.000"),
            preco_unit=Decimal("10.00"),
            total_item=Decimal("100.00"),
        )
        self.criar_previsao(pedido)
        return pedido, item, sku

    def test_revenda_estorna_com_saldo_suficiente(self):
        pedido, item, sku = self.criar_revenda()
        nota = self.criar_nota(pedido, item, "908", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota)
        self.cancelar(nota)
        self.assertEqual(Estoque.objects.get(Idloja=self.loja, CodigodeBarra=sku.ean13).Estoque, Decimal("0.000"))

    def test_revenda_bloqueia_saldo_negativo_e_rollback_total(self):
        pedido, item, sku = self.criar_revenda()
        nota = self.criar_nota(pedido, item, "909", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota)
        Estoque.objects.filter(Idloja=self.loja, CodigodeBarra=sku.ean13).update(Estoque=Decimal("2.000"))

        resp = self.cancelar(nota, status_code=200)
        self.assertTrue(any(aviso["tipo"] == "SALDO_NEGATIVO" for aviso in resp.data["analise_cancelamento"]["avisos"]))
        self.assertEqual(Estoque.objects.get(Idloja=self.loja, CodigodeBarra=sku.ean13).Estoque, Decimal("-8.000"))
        self.assertFalse(Pagar.objects.filter(nfe_id=nota.pk).exists())

    def test_aviso_de_saldo_negativo_exige_confirmacao(self):
        pedido, item, sku = self.criar_revenda()
        nota = self.criar_nota(pedido, item, "921", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota)
        Estoque.objects.filter(Idloja=self.loja, CodigodeBarra=sku.ean13).update(Estoque=Decimal("2.000"))

        resp = self.cancelar(nota, status_code=409, confirmar_avisos=False)
        self.assertEqual(NotaFiscalEntrada.objects.get(pk=nota.pk).status, NotaFiscalEntrada.Status.FECHADA)
        self.assertTrue(any(aviso["tipo"] == "SALDO_NEGATIVO" for aviso in resp.data["analise"]["avisos"]))

    def test_revenda_permite_saldo_negativo_quando_loja_permite(self):
        self.loja.EstoqueNegativo = "SIM"
        self.loja.save(update_fields=["EstoqueNegativo"])
        pedido, item, sku = self.criar_revenda()
        nota = self.criar_nota(pedido, item, "910", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota)
        Estoque.objects.filter(Idloja=self.loja, CodigodeBarra=sku.ean13).update(Estoque=Decimal("2.000"))

        self.cancelar(nota)
        self.assertEqual(Estoque.objects.get(Idloja=self.loja, CodigodeBarra=sku.ean13).Estoque, Decimal("-8.000"))

    def test_duas_entradas_recalculam_custos_apos_cancelar_primeira_ou_segunda(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto, preco=Decimal("16.00"))
        nota1 = self.criar_nota(pedido, item, "911", Decimal("4.000"), Decimal("10.00"))
        nota2 = self.criar_nota(pedido, item, "912", Decimal("6.000"), Decimal("20.00"))
        self.fechar(nota1)
        self.fechar(nota2)
        self.cancelar(nota1)
        produto.refresh_from_db()
        self.assertEqual(produto.custo_ultima_compra, Decimal("20.0000"))
        self.assertEqual(produto.custo_medio, Decimal("20.0000"))

        produto2 = self.criar_produto(descricao="Produto 2")
        pedido2, item2 = self.criar_pedido(produto2, preco=Decimal("16.00"))
        nota3 = self.criar_nota(pedido2, item2, "913", Decimal("4.000"), Decimal("10.00"))
        nota4 = self.criar_nota(pedido2, item2, "914", Decimal("6.000"), Decimal("20.00"))
        self.fechar(nota3)
        self.fechar(nota4)
        self.cancelar(nota4)
        produto2.refresh_from_db()
        self.assertEqual(produto2.custo_ultima_compra, Decimal("10.0000"))
        self.assertEqual(produto2.custo_medio, Decimal("10.0000"))

    def test_pedido_at_retorna_para_ap_quando_cancelamento_reduz_recebimento(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto)
        nota = self.criar_nota(pedido, item, "915", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota)
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "AT")
        self.cancelar(nota)
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "AP")

    def test_pedido_permanece_at_quando_outra_nf_valida_atende_totalmente(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto)
        nota1 = self.criar_nota(pedido, item, "918", Decimal("10.000"), Decimal("10.00"))
        nota2 = self.criar_nota(pedido, item, "919", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota1)
        self.fechar(nota2)
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "AT")

        self.cancelar(nota1)
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "AT")

    def test_impedimento_financeiro_nao_altera_estoque_custos_nf_ou_pedido(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto)
        nota = self.criar_nota(pedido, item, "916", Decimal("10.000"), Decimal("10.00"))
        self.fechar(nota)
        estoque_antes = Estoque.objects.get(Idloja=self.loja, CodigodeBarra__startswith="29").Estoque
        PagarItem.objects.filter(Idpagar__nfe_id=nota.pk).update(status=PagarItem.STATUS_BAIXADO, valor_baixa=Decimal("100.00"), data_baixa=timezone.localdate())

        self.cancelar(nota, status_code=400)
        self.assertEqual(NotaFiscalEntrada.objects.get(pk=nota.pk).status, NotaFiscalEntrada.Status.FECHADA)
        self.assertEqual(Estoque.objects.get(Idloja=self.loja, CodigodeBarra__startswith="29").Estoque, estoque_antes)
        produto.refresh_from_db()
        self.assertEqual(produto.custo_medio, Decimal("10.0000"))
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "AT")


@override_settings(ALLOWED_HOSTS=["testserver"])
class NotaFiscalEntradaIdentidadeBloco3Tests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa = Empresa.objects.create(nome="Empresa B3", documento="44444444000191", plano_completo=True)
        self.empresa_b = Empresa.objects.create(nome="Empresa B3 Outra", documento="55555555000191", plano_completo=True)
        self.user = get_user_model().objects.create_superuser("nf-ident", "nf-ident@sysvar.test", "test")
        self.client.force_authenticate(self.user)
        self.loja = Loja.objects.create(empresa=self.empresa, nome_loja="Loja B3", apelido_loja="Loja B3", cnpj="44444444000100", estado="SP")
        self.loja_b = Loja.objects.create(empresa=self.empresa_b, nome_loja="Loja B3B", apelido_loja="Loja B3B", cnpj="55555555000100", estado="SP")
        self.fornecedor = self.criar_fornecedor(self.empresa, "Fornecedor 1", "44445678000195")
        self.fornecedor_2 = self.criar_fornecedor(self.empresa, "Fornecedor 2", "44445678000276")
        self.fornecedor_b = self.criar_fornecedor(self.empresa_b, "Fornecedor B", "55545678000195")
        self.unidade = Unidade.objects.create(empresa=self.empresa, Descricao="Unidade B3", Codigo="U3")
        self.unidade_b = Unidade.objects.create(empresa=self.empresa_b, Descricao="Unidade B3B", Codigo="U4")
        self.produto = Produto.objects.create(empresa=self.empresa, tipo_produto="2", descricao="Produto B3", unidade=self.unidade)
        self.produto_b = Produto.objects.create(empresa=self.empresa_b, tipo_produto="2", descricao="Produto B3B", unidade=self.unidade_b)

    def chave_valida(self, sequencia=1):
        base = f"35{timezone.localdate():%y%m}4444444400019155001000000{sequencia:03d}12345678"
        base = base[:43].ljust(43, "0")
        pesos = [2, 3, 4, 5, 6, 7, 8, 9]
        soma = sum(int(digito) * pesos[index % len(pesos)] for index, digito in enumerate(reversed(base)))
        dv = 11 - (soma % 11)
        if dv >= 10:
            dv = 0
        return f"{base}{dv}"

    def criar_fornecedor(self, empresa, nome, documento):
        return Fornecedor.objects.create(
            empresa=empresa,
            tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA,
            documento=documento,
            cnpj=documento,
            nome_fornecedor=nome,
            categoria="USO_CONSUMO",
        )

    def criar_pedido(self, fornecedor=None, empresa=None, loja=None, produto=None, total=Decimal("10.00")):
        empresa = empresa or self.empresa
        loja = loja or self.loja
        fornecedor = fornecedor or self.fornecedor
        produto = produto or self.produto
        pedido = PedidoCompra.objects.create(empresa=empresa, tipo="2", loja=loja, fornecedor=fornecedor, status="AP", total_pedido=total)
        item = PedidoCompraItem.objects.create(pedido=pedido, produto=produto, qtd=Decimal("1.000"), preco_unit=total, total_item=total)
        return pedido, item

    def payload_nota(self, pedido, numero="123", serie="1", chave=None):
        payload = {
            "pedido_compra": pedido.pk,
            "modelo": "55",
            "serie": serie,
            "numero": numero,
            "dt_emissao": timezone.localdate().isoformat(),
            "dt_entrada": timezone.localdate().isoformat(),
        }
        if chave is not None:
            payload["chave_acesso"] = chave
        return payload

    def criar_nota_api(self, pedido, numero="123", serie="1", chave=None, status_code=201):
        resp = self.client.post(
            f"/api/fiscal/notas-entrada/?empresa={pedido.empresa_id}",
            self.payload_nota(pedido, numero=numero, serie=serie, chave=chave),
            format="json",
        )
        self.assertEqual(resp.status_code, status_code, resp.data)
        return NotaFiscalEntrada.objects.get(pk=resp.data["id"]) if status_code == 201 else resp

    def criar_item(self, nota, item):
        NotaFiscalEntradaItem.objects.create(
            nota=nota,
            pedido_item=item,
            qtd_recebida=Decimal("1.000"),
            preco_unit_nf=item.preco_unit,
            total_item=item.total_item,
        )
        nota.recalcular_totais()

    def fechar(self, nota):
        resp = self.client.post(f"/api/fiscal/notas-entrada/{nota.pk}/fechar/?empresa={nota.pedido_compra.empresa_id}", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        nota.refresh_from_db()
        return resp

    def cancelar(self, nota):
        resp = self.client.post(
            f"/api/fiscal/notas-entrada/{nota.pk}/cancelar/?empresa={nota.pedido_compra.empresa_id}",
            {"motivo": "Teste de cancelamento", "confirmar_avisos": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        nota.refresh_from_db()
        return resp

    def test_identidade_documental_bloqueia_mesmo_fornecedor_empresa_modelo_serie_numero(self):
        pedido1, _ = self.criar_pedido()
        pedido2, _ = self.criar_pedido()
        self.criar_nota_api(pedido1, numero="100", serie="1")
        self.criar_nota_api(pedido2, numero="100", serie="1", status_code=400)

    def test_mesmo_numero_fornecedor_diferente_serie_diferente_empresa_diferente_e_pedido_igual_permitidos(self):
        pedido1, _ = self.criar_pedido()
        pedido_fornecedor_2, _ = self.criar_pedido(fornecedor=self.fornecedor_2)
        pedido_empresa_b, _ = self.criar_pedido(empresa=self.empresa_b, loja=self.loja_b, fornecedor=self.fornecedor_b, produto=self.produto_b)
        self.criar_nota_api(pedido1, numero="101", serie="1")
        self.criar_nota_api(pedido_fornecedor_2, numero="101", serie="1")
        self.criar_nota_api(pedido1, numero="101", serie="2")
        self.criar_nota_api(pedido_empresa_b, numero="101", serie="1")
        self.criar_nota_api(pedido1, numero="102", serie="1")

    def test_edicao_propria_permitida_e_colisao_bloqueada(self):
        pedido1, _ = self.criar_pedido()
        pedido2, _ = self.criar_pedido()
        nota1 = self.criar_nota_api(pedido1, numero="103")
        self.criar_nota_api(pedido2, numero="104")
        resp = self.client.patch(f"/api/fiscal/notas-entrada/{nota1.pk}/?empresa={self.empresa.pk}", {"observacoes": "ok"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        resp = self.client.patch(f"/api/fiscal/notas-entrada/{nota1.pk}/?empresa={self.empresa.pk}", {"numero": "104"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_validacao_e_duplicidade_da_chave_de_acesso(self):
        pedido1, _ = self.criar_pedido()
        pedido2, _ = self.criar_pedido()
        chave = self.chave_valida(1)
        nota = self.criar_nota_api(pedido1, numero="105", chave=chave)
        self.assertEqual(nota.chave_acesso, chave)

        for chave_invalida in ("123", "1" * 45, "1" * 43 + "A", chave[:-1] + str((int(chave[-1]) + 1) % 10)):
            self.criar_nota_api(pedido2, numero=f"20{len(chave_invalida)}", chave=chave_invalida, status_code=400)

        self.criar_nota_api(pedido2, numero="106", chave=chave, status_code=400)
        resp = self.client.patch(f"/api/fiscal/notas-entrada/{nota.pk}/?empresa={self.empresa.pk}", {"chave_acesso": chave}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        NotaFiscalEntrada.objects.filter(pk=nota.pk).update(status=NotaFiscalEntrada.Status.CANCELADA)
        self.criar_nota_api(pedido2, numero="107", chave=chave, status_code=400)

    def test_estoque_identifica_movimentos_por_id_da_nf_e_nao_por_numero(self):
        pedido1, item1 = self.criar_pedido()
        pedido2, item2 = self.criar_pedido(fornecedor=self.fornecedor_2)
        nota1 = self.criar_nota_api(pedido1, numero="108", serie="1")
        nota2 = self.criar_nota_api(pedido2, numero="108", serie="1")
        self.criar_item(nota1, item1)
        self.criar_item(nota2, item2)
        self.fechar(nota1)
        self.fechar(nota2)
        resp = self.client.post(f"/api/fiscal/notas-entrada/{nota1.pk}/fechar/?empresa={self.empresa.pk}", {}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(ProdutoUsoConsumoMovimentacao.objects.filter(documento=f"NFE:{nota1.pk}:ENTRADA").count(), 1)
        self.assertEqual(ProdutoUsoConsumoMovimentacao.objects.filter(documento=f"NFE:{nota2.pk}:ENTRADA").count(), 1)

        self.cancelar(nota1)
        self.assertEqual(ProdutoUsoConsumoMovimentacao.objects.filter(documento=f"NFE:{nota1.pk}:CANCEL").count(), 1)
        self.assertFalse(ProdutoUsoConsumoMovimentacao.objects.filter(documento=f"NFE:{nota2.pk}:CANCEL").exists())

    def test_series_e_empresas_iguais_no_numero_movimentam_sem_interferencia(self):
        pedido1, item1 = self.criar_pedido()
        pedido2, item2 = self.criar_pedido()
        pedido_b, item_b = self.criar_pedido(empresa=self.empresa_b, loja=self.loja_b, fornecedor=self.fornecedor_b, produto=self.produto_b)
        nota1 = self.criar_nota_api(pedido1, numero="109", serie="1")
        nota2 = self.criar_nota_api(pedido2, numero="109", serie="2")
        nota_b = self.criar_nota_api(pedido_b, numero="109", serie="1")
        for nota, item in ((nota1, item1), (nota2, item2), (nota_b, item_b)):
            self.criar_item(nota, item)
            self.fechar(nota)
            self.assertEqual(ProdutoUsoConsumoMovimentacao.objects.filter(documento=f"NFE:{nota.pk}:ENTRADA").count(), 1)


@override_settings(ALLOWED_HOSTS=["testserver"])
class NotaFiscalEntradaIntegracaoBloco8Tests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.empresa = Empresa.objects.create(nome="Empresa Bloco 8", documento="88888888000191", plano_completo=True)
        self.empresa_b = Empresa.objects.create(nome="Empresa Bloco 8 B", documento="88888888000192", plano_completo=True)
        self.user = get_user_model().objects.create_superuser("nf-b8", "nf-b8@sysvar.test", "test")
        self.client.force_authenticate(self.user)
        self.loja = Loja.objects.create(
            empresa=self.empresa,
            nome_loja="Loja B8",
            apelido_loja="Loja B8",
            cnpj="88888888000100",
            estado="SP",
            EstoqueNegativo="NAO",
        )
        self.loja_b = Loja.objects.create(
            empresa=self.empresa_b,
            nome_loja="Loja B8 B",
            apelido_loja="Loja B8 B",
            cnpj="88888888000101",
            estado="SP",
            EstoqueNegativo="NAO",
        )
        self.fornecedor = Fornecedor.objects.create(
            empresa=self.empresa,
            tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA,
            documento="88845678000195",
            cnpj="88845678000195",
            nome_fornecedor="Fornecedor B8",
            categoria="USO_CONSUMO",
        )
        self.fornecedor_b = Fornecedor.objects.create(
            empresa=self.empresa_b,
            tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA,
            documento="88845678000196",
            cnpj="88845678000196",
            nome_fornecedor="Fornecedor B8 B",
            categoria="USO_CONSUMO",
        )
        self.natureza = Nat_Lancamento.objects.create(
            empresa=self.empresa,
            codigo="CMPB8",
            categoria_principal="Compras",
            subcategoria="NF",
            descricao="Compra NF B8",
            tipo="SAIDA",
            status="ATIVO",
            tipo_natureza="D",
        )
        self.unidade = Unidade.objects.create(empresa=self.empresa, Descricao="Unidade B8", Codigo="UNB8")
        self.grupo = Grupo.objects.create(empresa=self.empresa, Codigo="88", CodigoRef="88", Descricao="Grupo B8", Margem=0)
        self.colecao = Colecao.objects.create(empresa=self.empresa, Descricao="Colecao B8", Codigo="28", Estacao="08", Status="AT")
        ConfigEan.objects.create(empresa=self.empresa, country_prefix="789", company_prefix="8888", ativo=True)

    def criar_produto(self, tipo="2", descricao="Produto B8", empresa=None):
        return Produto.objects.create(
            empresa=empresa or self.empresa,
            tipo_produto=tipo,
            descricao=descricao,
            unidade=self.unidade,
        )

    def criar_pedido(self, produto, qtd, preco, tipo="2", empresa=None, loja=None, fornecedor=None, cor=None, pack=None, n_packs=0):
        empresa = empresa or self.empresa
        loja = loja or self.loja
        fornecedor = fornecedor or self.fornecedor
        pedido = PedidoCompra.objects.create(
            empresa=empresa,
            tipo=tipo,
            loja=loja,
            fornecedor=fornecedor,
            status="AP",
            total_pedido=(qtd * preco).quantize(Decimal("0.01")),
        )
        item = PedidoCompraItem.objects.create(
            pedido=pedido,
            produto=produto,
            cor=cor,
            pack=pack,
            n_packs=n_packs,
            qtd=qtd,
            preco_unit=preco,
            total_item=(qtd * preco).quantize(Decimal("0.01")),
        )
        PedidoCompraEntrega.objects.create(item=item, qtd_prevista=qtd, status="PREV")
        self.criar_previsao(pedido)
        return pedido, item

    def criar_previsao(self, pedido):
        titulo = Pagar.objects.create(
            empresa=pedido.empresa,
            idloja=pedido.loja,
            idfornecedor=pedido.fornecedor,
            Titulo=f"PC B8 {pedido.pk}",
            Data_emissao=timezone.localdate(),
            Valor_total=pedido.total_pedido,
            Previsao=True,
            FormaPagamento="BOL",
            Idnatureza=self.natureza,
            pedido_compra=pedido.pk,
        )
        PagarItem.objects.create(
            Idpagar=titulo,
            parcela_n=1,
            status=PagarItem.STATUS_PREVISTO,
            Data_vencimento=timezone.localdate(),
            valor_parcela=pedido.total_pedido,
            FormaPagamento="BOL",
            Previsao=True,
            Idnatureza=self.natureza,
        )

    def criar_nota_api(self, pedido, numero, status_code=201):
        resp = self.client.post(
            f"/api/fiscal/notas-entrada/?empresa={pedido.empresa_id}",
            {
                "pedido_compra": pedido.pk,
                "modelo": "55",
                "serie": "1",
                "numero": numero,
                "dt_emissao": str(timezone.localdate()),
                "dt_entrada": str(timezone.localdate()),
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status_code, resp.data)
        return NotaFiscalEntrada.objects.get(pk=resp.data["id"]) if status_code < 400 else None

    def criar_item_api(self, nota, pedido_item, qtd, preco, status_code=201):
        resp = self.client.post(
            f"/api/fiscal/notas-entrada-itens/?empresa={nota.pedido_compra.empresa_id}",
            {
                "nota": nota.pk,
                "pedido_item": pedido_item.pk,
                "qtd_recebida": str(qtd),
                "preco_unit_nf": str(preco),
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status_code, resp.data)
        return resp

    def estoque_produto(self, produto, loja=None):
        codigo = f"29{int(produto.pk) % 100000000000:011d}"
        return Estoque.objects.get(Idloja=loja or self.loja, CodigodeBarra=codigo)

    def fechar(self, nota, status_code=200):
        resp = self.client.post(f"/api/fiscal/notas-entrada/{nota.pk}/fechar/?empresa={nota.pedido_compra.empresa_id}", {}, format="json")
        self.assertEqual(resp.status_code, status_code, resp.data)
        nota.refresh_from_db()
        return resp

    def cancelar(self, nota, status_code=200):
        resp = self.client.post(
            f"/api/fiscal/notas-entrada/{nota.pk}/cancelar/?empresa={nota.pedido_compra.empresa_id}",
            {"motivo": "Teste de cancelamento", "confirmar_avisos": True},
            format="json",
        )
        self.assertEqual(resp.status_code, status_code, resp.data)
        nota.refresh_from_db()
        return resp

    def test_multiplas_nfs_mesmo_pedido_cancelamento_parcial_e_reentrada_do_saldo(self):
        produto = self.criar_produto()
        pedido, item = self.criar_pedido(produto, Decimal("100.000"), Decimal("10.00"))
        nota1 = self.criar_nota_api(pedido, "8001")
        nota2 = self.criar_nota_api(pedido, "8002")
        self.criar_item_api(nota1, item, Decimal("40.000"), Decimal("10.00"))
        self.criar_item_api(nota2, item, Decimal("60.000"), Decimal("10.00"))

        self.fechar(nota1)
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "AP")
        self.fechar(nota2)
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "AT")
        self.assertEqual(ProdutoUsoConsumoEstoque.objects.get(empresa=self.empresa, produto=produto, loja=self.loja).saldo, Decimal("100.000"))

        self.cancelar(nota1)
        pedido.refresh_from_db()
        entrega = item.entregas.get()
        self.assertEqual(pedido.status, "AP")
        self.assertEqual(entrega.qtd_recebida, Decimal("60.000"))
        self.assertEqual(entrega.status, "PARC")
        self.assertTrue(Pagar.objects.filter(nfe_id=nota2.pk, Previsao=False).exists())
        self.assertEqual(Pagar.objects.get(pedido_compra=pedido.pk, nfe_id__isnull=True, Previsao=True).Valor_total, Decimal("400.00"))
        self.assertEqual(ProdutoUsoConsumoMovimentacao.objects.filter(documento=f"NFE:{nota1.pk}:CANCEL").count(), 1)
        self.assertFalse(ProdutoUsoConsumoMovimentacao.objects.filter(documento=f"NFE:{nota2.pk}:CANCEL").exists())

        nota3 = self.criar_nota_api(pedido, "8003")
        self.criar_item_api(nota3, item, Decimal("40.000"), Decimal("10.00"))
        self.fechar(nota3)
        pedido.refresh_from_db()
        entrega.refresh_from_db()
        self.assertEqual(pedido.status, "AT")
        self.assertEqual(entrega.qtd_recebida, Decimal("100.000"))
        self.assertEqual(entrega.status, "RECB")
        self.assertEqual(ProdutoUsoConsumoEstoque.objects.get(empresa=self.empresa, produto=produto, loja=self.loja).saldo, Decimal("100.000"))
        self.assertEqual(Pagar.objects.filter(nfe_id__in=[nota2.pk, nota3.pk], Previsao=False).count(), 2)

    def test_fluxo_revenda_pack_multitamanho_isolado_por_empresa_e_cancelamento(self):
        grade = Grade.objects.create(empresa=self.empresa, Descricao="Grade B8")
        tam_p = Tamanho.objects.create(empresa=self.empresa, Tamanho="P", idgrade=grade)
        tam_m = Tamanho.objects.create(empresa=self.empresa, Tamanho="M", idgrade=grade)
        tam_g = Tamanho.objects.create(empresa=self.empresa, Tamanho="G", idgrade=grade)
        cor = Cor.objects.create(empresa=self.empresa, Descricao="Azul B8")
        pack = Pack.objects.create(empresa=self.empresa, nome="Pack B8", grade=grade)
        PackItem.objects.create(pack=pack, tamanho=tam_p, qtd=1)
        PackItem.objects.create(pack=pack, tamanho=tam_m, qtd=2)
        PackItem.objects.create(pack=pack, tamanho=tam_g, qtd=1)
        produto = Produto.objects.create(
            empresa=self.empresa,
            tipo_produto="1",
            descricao="Revenda Pack B8",
            unidade=self.unidade,
            grade=grade,
            grupo=self.grupo,
            colecao=self.colecao,
        )
        skus = {
            "P": ProdutoDetalhe.objects.create(produto=produto, idcor=cor, idtamanho=tam_p),
            "M": ProdutoDetalhe.objects.create(produto=produto, idcor=cor, idtamanho=tam_m),
            "G": ProdutoDetalhe.objects.create(produto=produto, idcor=cor, idtamanho=tam_g),
        }
        pedido, item = self.criar_pedido(produto, Decimal("8.000"), Decimal("10.00"), tipo="1", cor=cor, pack=pack, n_packs=2)
        nota = self.criar_nota_api(pedido, "8010")
        self.criar_item_api(nota, item, Decimal("8.000"), Decimal("10.00"))

        produto_b = Produto.objects.create(empresa=self.empresa_b, tipo_produto="2", descricao="Produto B8 B", unidade=self.unidade)
        pedido_b, item_b = self.criar_pedido(
            produto_b,
            Decimal("5.000"),
            Decimal("7.00"),
            empresa=self.empresa_b,
            loja=self.loja_b,
            fornecedor=self.fornecedor_b,
        )
        nota_b = self.criar_nota_api(pedido_b, "8010")
        self.criar_item_api(nota_b, item_b, Decimal("5.000"), Decimal("7.00"))

        self.fechar(nota)
        self.fechar(nota_b)
        self.assertEqual(Estoque.objects.get(Idloja=self.loja, CodigodeBarra=skus["P"].ean13).Estoque, Decimal("2.000"))
        self.assertEqual(Estoque.objects.get(Idloja=self.loja, CodigodeBarra=skus["M"].ean13).Estoque, Decimal("4.000"))
        self.assertEqual(Estoque.objects.get(Idloja=self.loja, CodigodeBarra=skus["G"].ean13).Estoque, Decimal("2.000"))
        self.assertEqual(EstoqueMovimentacao.objects.filter(documento=f"NFE:{nota.pk}:ENTRADA", Idloja=self.loja).count(), 3)
        self.assertEqual(ProdutoUsoConsumoEstoque.objects.get(empresa=self.empresa_b, produto=produto_b, loja=self.loja_b).saldo, Decimal("5.000"))

        self.cancelar(nota)
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "AP")
        for sku in skus.values():
            self.assertEqual(Estoque.objects.get(Idloja=self.loja, CodigodeBarra=sku.ean13).Estoque, Decimal("0.000"))
            sku.refresh_from_db()
            self.assertEqual(sku.custo_medio, Decimal("0.0000"))
        self.assertEqual(EstoqueMovimentacao.objects.filter(documento=f"NFE:{nota.pk}:CANCEL", Idloja=self.loja).count(), 3)
        self.assertEqual(ProdutoUsoConsumoEstoque.objects.get(empresa=self.empresa_b, produto=produto_b, loja=self.loja_b).saldo, Decimal("5.000"))
        self.assertTrue(Pagar.objects.filter(nfe_id=nota_b.pk, Previsao=False).exists())

    def test_fluxos_uso_consumo_e_insumo_decimais_financeiro_custo_e_cancelamento(self):
        for tipo in ("2", "4"):
            produto = self.criar_produto(tipo=tipo, descricao=f"Produto tipo {tipo} B8")
            pedido, item = self.criar_pedido(produto, Decimal("3.500"), Decimal("12.00"), tipo=tipo)
            nota = self.criar_nota_api(pedido, f"802{tipo}")
            self.criar_item_api(nota, item, Decimal("3.500"), Decimal("12.00"))

            self.fechar(nota)
            pedido.refresh_from_db()
            produto.refresh_from_db()
            self.assertEqual(pedido.status, "AT")
            self.assertEqual(item.entregas.get().qtd_recebida, Decimal("3.500"))
            if tipo == "2":
                self.assertEqual(ProdutoUsoConsumoEstoque.objects.get(empresa=self.empresa, produto=produto, loja=self.loja).saldo, Decimal("3.500"))
                self.assertTrue(ProdutoUsoConsumoMovimentacao.objects.filter(documento=f"NFE:{nota.pk}:ENTRADA", produto=produto, loja=self.loja).exists())
                self.assertFalse(EstoqueMovimentacao.objects.filter(documento=f"NFE:{nota.pk}:ENTRADA").exists())
                self.assertFalse(Estoque.objects.filter(Idloja=self.loja, referencia=produto.referencia or "").exists())
            else:
                self.assertEqual(self.estoque_produto(produto).Estoque, Decimal("3.500"))
            self.assertEqual(produto.custo_medio, Decimal("12.0000"))
            self.assertTrue(Pagar.objects.filter(nfe_id=nota.pk, Previsao=False).exists())

            self.cancelar(nota)
            pedido.refresh_from_db()
            produto.refresh_from_db()
            self.assertEqual(pedido.status, "AP")
            self.assertEqual(item.entregas.get().qtd_recebida, Decimal("0.000"))
            if tipo == "2":
                self.assertEqual(ProdutoUsoConsumoEstoque.objects.get(empresa=self.empresa, produto=produto, loja=self.loja).saldo, Decimal("0.000"))
                self.assertTrue(ProdutoUsoConsumoMovimentacao.objects.filter(documento=f"NFE:{nota.pk}:CANCEL", produto=produto, loja=self.loja).exists())
                self.assertFalse(EstoqueMovimentacao.objects.filter(documento=f"NFE:{nota.pk}:CANCEL").exists())
            else:
                self.assertEqual(self.estoque_produto(produto).Estoque, Decimal("0.000"))
            self.assertEqual(produto.custo_medio, Decimal("0.0000"))
            self.assertFalse(Pagar.objects.filter(nfe_id=nota.pk).exists())
            self.assertEqual(Pagar.objects.get(pedido_compra=pedido.pk, Previsao=True).Valor_total, Decimal("42.00"))

    def test_falha_de_pack_invalido_no_fechamento_faz_rollback_integral(self):
        grade = Grade.objects.create(empresa=self.empresa, Descricao="Grade Rollback B8")
        tam_p = Tamanho.objects.create(empresa=self.empresa, Tamanho="P", idgrade=grade)
        tam_m = Tamanho.objects.create(empresa=self.empresa, Tamanho="M", idgrade=grade)
        cor = Cor.objects.create(empresa=self.empresa, Descricao="Preto B8")
        pack = Pack.objects.create(empresa=self.empresa, nome="Pack Rollback B8", grade=grade)
        PackItem.objects.create(pack=pack, tamanho=tam_p, qtd=1)
        PackItem.objects.create(pack=pack, tamanho=tam_m, qtd=1)
        produto = Produto.objects.create(
            empresa=self.empresa,
            tipo_produto="1",
            descricao="Revenda Rollback B8",
            unidade=self.unidade,
            grade=grade,
            grupo=self.grupo,
            colecao=self.colecao,
        )
        sku_p = ProdutoDetalhe.objects.create(produto=produto, idcor=cor, idtamanho=tam_p)
        sku_m = ProdutoDetalhe.objects.create(produto=produto, idcor=cor, idtamanho=tam_m)
        pedido, item = self.criar_pedido(produto, Decimal("4.000"), Decimal("10.00"), tipo="1", cor=cor, pack=pack, n_packs=2)
        nota = self.criar_nota_api(pedido, "8030")
        self.criar_item_api(nota, item, Decimal("3.000"), Decimal("10.00"))

        self.fechar(nota, status_code=400)
        pedido.refresh_from_db()
        produto.refresh_from_db()
        sku_p.refresh_from_db()
        sku_m.refresh_from_db()
        self.assertEqual(nota.status, NotaFiscalEntrada.Status.ABERTA)
        self.assertEqual(pedido.status, "AP")
        self.assertFalse(EstoqueMovimentacao.objects.filter(documento=f"NFE:{nota.pk}:ENTRADA").exists())
        self.assertFalse(Estoque.objects.filter(Idloja=self.loja, CodigodeBarra__in=[sku_p.ean13, sku_m.ean13]).exists())
        self.assertFalse(Pagar.objects.filter(nfe_id=nota.pk).exists())
        self.assertEqual(produto.custo_medio, Decimal("0.0000"))
