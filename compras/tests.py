from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from cadastros.models import Empresa, Fornecedor, Loja, Nat_Lancamento
from compras.models import PedidoCompra, PedidoCompraItem, PedidoCompraParcela
from financeiro.models import FormaPagamento, FormaPagamentoParcela, Pagar, PagarItem, PrazoPagamento, PrazoPagamentoParcela
from produto.models import Colecao, Cor, Grade, Grupo, Pack, PackItem, Produto, Tamanho, Unidade


@override_settings(ALLOWED_HOSTS=["testserver"])
class PedidoCompraUnificadoTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_superuser("compras-admin", "compras-admin@sysvar.test", "test")
        self.client.force_authenticate(self.user)

        self.empresa = Empresa.objects.create(nome="Empresa A", documento="11111111000191", plano_completo=True)
        self.empresa_b = Empresa.objects.create(nome="Empresa B", documento="22222222000191", plano_completo=True)
        self.loja = Loja.objects.create(empresa=self.empresa, nome_loja="Loja A", apelido_loja="Loja A", cnpj="11111111000100", estado="SP")
        self.loja_b = Loja.objects.create(empresa=self.empresa_b, nome_loja="Loja B", apelido_loja="Loja B", cnpj="22222222000100", estado="SP")
        self.fornecedor = self.criar_fornecedor(self.empresa, "Fornecedor A", "12345678000195")
        self.fornecedor_b = self.criar_fornecedor(self.empresa_b, "Fornecedor B", "22345678000195")
        self.un_int = Unidade.objects.create(empresa=self.empresa, Descricao="Unidade", Codigo="UN", permite_decimal=False)
        self.un_dec = Unidade.objects.create(empresa=self.empresa, Descricao="Quilo", Codigo="KG", permite_decimal=True)
        self.grade = Grade.objects.create(empresa=self.empresa, Descricao="Grade")
        self.tam_p = Tamanho.objects.create(empresa=self.empresa, idgrade=self.grade, Tamanho="P", Descricao="P")
        self.tam_m = Tamanho.objects.create(empresa=self.empresa, idgrade=self.grade, Tamanho="M", Descricao="M")
        self.cor = Cor.objects.create(empresa=self.empresa, Descricao="Azul", Codigo="AZ", Cor="Azul")
        self.grupo = Grupo.objects.create(empresa=self.empresa, Codigo="01", CodigoRef="01", Descricao="Grupo", Margem=0)
        self.colecao = Colecao.objects.create(empresa=self.empresa, Descricao="Colecao", Codigo="26", Estacao="01", Status="AT")
        self.prod_revenda = self.criar_produto("1", "Revenda", self.un_int)
        self.prod_uso = self.criar_produto("2", "Uso", self.un_int)
        self.prod_uso_dec = self.criar_produto("2", "Uso Decimal", self.un_dec)
        self.prod_proprio = self.criar_produto("3", "Proprio", self.un_int)
        self.prod_insumo = self.criar_produto("4", "Insumo", self.un_dec)
        self.pack = Pack.objects.create(empresa=self.empresa, nome="Pack 3", grade=self.grade)
        PackItem.objects.create(pack=self.pack, tamanho=self.tam_p, qtd=1)
        PackItem.objects.create(pack=self.pack, tamanho=self.tam_m, qtd=2)
        self.natureza = Nat_Lancamento.objects.create(
            empresa=self.empresa,
            codigo="CMP",
            categoria_principal="Compras",
            subcategoria="Pedido",
            descricao="Compra de mercadorias",
            tipo="SAIDA",
            status="ATIVO",
            tipo_natureza="D",
        )
        self.forma, self.prazo = self.criar_forma_pagamento()

    def criar_fornecedor(self, empresa, nome, documento, **extras):
        return Fornecedor.objects.create(
            empresa=empresa,
            tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA,
            documento=documento,
            cnpj=documento,
            nome_fornecedor=nome,
            categoria="REVENDA",
            **extras,
        )

    def criar_produto(self, tipo, descricao, unidade, empresa=None):
        empresa = empresa or self.empresa
        kwargs = {"empresa": empresa, "tipo_produto": tipo, "descricao": descricao, "unidade": unidade}
        if tipo in ("1", "3"):
            kwargs.update({"grupo": self.grupo, "colecao": self.colecao, "grade": self.grade})
        return Produto.objects.create(**kwargs)

    def criar_forma_pagamento(self):
        prazo = PrazoPagamento.objects.create(empresa=self.empresa, codigo="30/60", descricao="30/60", num_parcelas=2)
        PrazoPagamentoParcela.objects.create(prazo=prazo, ordem=1, dias=30, percentual=Decimal("50.000000"))
        PrazoPagamentoParcela.objects.create(prazo=prazo, ordem=2, dias=60, percentual=Decimal("50.000000"))
        forma = FormaPagamento.objects.create(
            empresa=self.empresa,
            codigo="BOL",
            descricao="Boleto",
            tipo=FormaPagamento.TIPO_BOLETO,
            num_parcelas=2,
            prazo_pagamento=prazo,
        )
        FormaPagamentoParcela.objects.create(forma=forma, ordem=1, dias=30, percentual=Decimal("50.000000"))
        FormaPagamentoParcela.objects.create(forma=forma, ordem=2, dias=60, percentual=Decimal("50.000000"))
        return forma, prazo

    def criar_pedido(self, **extras):
        data = {"loja": self.loja.id, "fornecedor": self.fornecedor.id, "observacoes": "Pedido teste"}
        data.update(extras)
        resp = self.client.post("/api/compras/pedidos/", data, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        return PedidoCompra.objects.get(pk=resp.data["id"])

    def payload_revenda(self, pedido, **extras):
        data = {
            "pedido": pedido.id,
            "produto": self.prod_revenda.Idproduto,
            "cor": self.cor.Idcor,
            "pack": self.pack.id,
            "n_packs": 2,
            "qtd": "999.000",
            "preco_unit": "10.00",
            "desconto_valor": "1.00",
        }
        data.update(extras)
        return data

    def payload_uso(self, pedido, produto=None, **extras):
        data = {
            "pedido": pedido.id,
            "produto": (produto or self.prod_uso).Idproduto,
            "qtd": "2.000",
            "preco_unit": "15.00",
            "desconto_valor": "0.00",
        }
        data.update(extras)
        return data

    def incluir_item(self, payload, status=201):
        resp = self.client.post("/api/compras/itens/", payload, format="json")
        self.assertEqual(resp.status_code, status, resp.data)
        if status == 201:
            return PedidoCompraItem.objects.get(pk=resp.data["id"])
        return resp

    def configurar_forma(self, pedido):
        resp = self.client.post(
            f"/api/compras/pedidos/{pedido.id}/set-forma-pagamento/",
            {"id_forma": self.forma.Idformapagamento, "id_prazo": self.prazo.Idprazo},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_pedido_nasce_sem_tipo_e_ignora_tipo_manual(self):
        pedido = self.criar_pedido(tipo="1")
        self.assertEqual(pedido.tipo, "")

    def test_primeiro_item_define_tipo_e_revenda_recalcula_qtd_no_backend(self):
        pedido = self.criar_pedido()
        item = self.incluir_item(self.payload_revenda(pedido))
        pedido.refresh_from_db()
        self.assertEqual(pedido.tipo, "1")
        self.assertEqual(item.qtd, Decimal("6.000"))
        self.assertEqual(pedido.total_itens, Decimal("59.00"))
        self.assertEqual(pedido.total_pedido, Decimal("59.00"))

    def test_rejeita_produto_proprio_tipo_3(self):
        pedido = self.criar_pedido()
        resp = self.incluir_item(self.payload_uso(pedido, produto=self.prod_proprio), status=400)
        self.assertIn("produto", resp.data)

    def test_rejeita_todas_as_misturas_de_tipos(self):
        casos = (
            (self.payload_revenda, lambda p: self.payload_uso(p, produto=self.prod_uso)),
            (lambda p: self.payload_uso(p, produto=self.prod_uso), self.payload_revenda),
            (lambda p: self.payload_uso(p, produto=self.prod_uso), lambda p: self.payload_uso(p, produto=self.prod_insumo)),
            (lambda p: self.payload_uso(p, produto=self.prod_insumo), lambda p: self.payload_uso(p, produto=self.prod_uso)),
            (self.payload_revenda, lambda p: self.payload_uso(p, produto=self.prod_insumo)),
            (lambda p: self.payload_uso(p, produto=self.prod_insumo), self.payload_revenda),
        )
        for primeiro, segundo in casos:
            pedido = self.criar_pedido()
            self.incluir_item(primeiro(pedido))
            self.incluir_item(segundo(pedido), status=400)

    def test_excluir_ultimo_item_limpa_tipo_e_permite_redefinir(self):
        pedido = self.criar_pedido()
        item = self.incluir_item(self.payload_revenda(pedido))
        resp = self.client.delete(f"/api/compras/itens/{item.id}/")
        self.assertEqual(resp.status_code, 204)
        pedido.refresh_from_db()
        self.assertEqual(pedido.tipo, "")
        self.assertEqual(pedido.total_pedido, Decimal("0.00"))
        item_uso = self.incluir_item(self.payload_uso(pedido))
        pedido.refresh_from_db()
        self.assertEqual(item_uso.produto.tipo_produto, "2")
        self.assertEqual(pedido.tipo, "2")

    def test_uso_consumo_e_insumo_respeitam_decimal_da_unidade(self):
        pedido = self.criar_pedido()
        self.incluir_item(self.payload_uso(pedido, qtd="1.500"), status=400)
        item = self.incluir_item(self.payload_uso(pedido, produto=self.prod_uso_dec, qtd="1.500"))
        pedido.refresh_from_db()
        self.assertEqual(pedido.tipo, "2")
        self.assertEqual(item.qtd, Decimal("1.500"))

        pedido_insumo = self.criar_pedido()
        item_insumo = self.incluir_item(self.payload_uso(pedido_insumo, produto=self.prod_insumo, qtd="1.250"))
        pedido_insumo.refresh_from_db()
        self.assertEqual(pedido_insumo.tipo, "4")
        self.assertEqual(item_insumo.qtd, Decimal("1.250"))

    def test_operacoes_de_item_somente_em_aberto(self):
        pedido = self.criar_pedido()
        item = self.incluir_item(self.payload_uso(pedido))
        for status_pc in ("AP", "AT", "CA"):
            PedidoCompra.objects.filter(pk=pedido.pk).update(status=status_pc)
            self.assertEqual(self.client.post("/api/compras/itens/", self.payload_uso(pedido), format="json").status_code, 400)
            self.assertEqual(self.client.patch(f"/api/compras/itens/{item.id}/", {"qtd": "3.000"}, format="json").status_code, 400)
            self.assertEqual(self.client.delete(f"/api/compras/itens/{item.id}/").status_code, 400)

    def test_totais_recalculam_em_inclusao_alteracao_exclusao_frete_e_desconto(self):
        pedido = self.criar_pedido()
        item = self.incluir_item(self.payload_uso(pedido, qtd="2.000", preco_unit="10.00"))
        pedido.refresh_from_db()
        self.assertEqual(pedido.total_pedido, Decimal("20.00"))
        resp = self.client.patch(f"/api/compras/itens/{item.id}/", {"qtd": "3.000"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        resp = self.client.patch(f"/api/compras/pedidos/{pedido.id}/", {"frete": "5.00", "total_desconto": "2.00"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        pedido.refresh_from_db()
        self.assertEqual(pedido.total_pedido, Decimal("33.00"))
        self.assertEqual(self.client.delete(f"/api/compras/itens/{item.id}/").status_code, 204)
        pedido.refresh_from_db()
        self.assertEqual(pedido.total_pedido, Decimal("3.00"))

    def test_frete_e_desconto_rejeitam_negativos_e_total_final_negativo(self):
        pedido = self.criar_pedido()
        self.incluir_item(self.payload_uso(pedido, qtd="1.000", preco_unit="10.00"))
        for payload in ({"frete": "-0.01"}, {"total_desconto": "-0.01"}, {"total_desconto": "10.01"}):
            resp = self.client.patch(f"/api/compras/pedidos/{pedido.id}/", payload, format="json")
            self.assertEqual(resp.status_code, 400, resp.data)
        resp = self.client.patch(f"/api/compras/pedidos/{pedido.id}/", {"frete": "0.00", "total_desconto": "10.00"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_forma_pagamento_sincroniza_parcelas_apos_mudar_total(self):
        pedido = self.criar_pedido()
        item = self.incluir_item(self.payload_uso(pedido, qtd="2.000", preco_unit="50.00"))
        self.configurar_forma(pedido)
        self.assertEqual(list(PedidoCompraParcela.objects.filter(pedido=pedido).values_list("valor", flat=True)), [Decimal("50.00"), Decimal("50.00")])
        resp = self.client.patch(f"/api/compras/itens/{item.id}/", {"qtd": "4.000"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(list(PedidoCompraParcela.objects.filter(pedido=pedido).values_list("valor", flat=True)), [Decimal("100.00"), Decimal("100.00")])

    def test_aprovacao_rejeita_sem_item_sem_forma_e_parcelas_divergentes(self):
        pedido = self.criar_pedido()
        resp = self.client.post(f"/api/compras/pedidos/{pedido.id}/aprovar/", {"idnatureza": self.natureza.idnatureza}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.incluir_item(self.payload_uso(pedido))
        resp = self.client.post(f"/api/compras/pedidos/{pedido.id}/aprovar/", {"idnatureza": self.natureza.idnatureza}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.configurar_forma(pedido)
        PedidoCompraParcela.objects.filter(pedido=pedido, parcela_n=1).update(valor=Decimal("1.00"))
        resp = self.client.post(f"/api/compras/pedidos/{pedido.id}/aprovar/", {"idnatureza": self.natureza.idnatureza}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_aprovacao_valida_gera_financeiro_parcelas_e_auditoria(self):
        pedido = self.criar_pedido()
        self.incluir_item(self.payload_uso(pedido, qtd="2.000", preco_unit="50.00"))
        self.configurar_forma(pedido)
        resp = self.client.post(f"/api/compras/pedidos/{pedido.id}/aprovar/", {"idnatureza": self.natureza.idnatureza}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "AP")
        pagar = Pagar.objects.get(pedido_compra=pedido.id)
        self.assertEqual(pagar.Valor_total, Decimal("100.00"))
        self.assertEqual(pagar.Idnatureza_id, self.natureza.idnatureza)
        self.assertEqual(PagarItem.objects.filter(Idpagar=pagar, status=PagarItem.STATUS_PREVISTO).count(), 2)
        self.assertEqual(PedidoCompraParcela.objects.filter(pedido=pedido, status="GERADA", pagar_item_id__isnull=False).count(), 2)

    def test_cancelamento_e_exclusao_somente_aberto(self):
        pedido = self.criar_pedido()
        resp = self.client.post(f"/api/compras/pedidos/{pedido.id}/cancelar/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        resp = self.client.delete(f"/api/compras/pedidos/{pedido.id}/")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_multiempresa_bloqueia_loja_fornecedor_produto_pack_e_queryset(self):
        pedido_b = PedidoCompra.objects.create(empresa=self.empresa_b, loja=self.loja_b, fornecedor=self.fornecedor_b, observacoes="Pedido B")
        resp = self.client.get("/api/compras/pedidos/", {"empresa": self.empresa.id})
        pedidos = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertNotIn(pedido_b.id, [p["id"] for p in pedidos])
        resp = self.client.post("/api/compras/pedidos/", {"loja": self.loja.id, "fornecedor": self.fornecedor_b.id}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        pedido = self.criar_pedido()
        prod_b = Produto.objects.create(empresa=self.empresa_b, tipo_produto="2", descricao="Uso B", unidade=self.un_int)
        resp = self.client.post("/api/compras/itens/", self.payload_uso(pedido, produto=prod_b), format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        pack_b = Pack.objects.create(empresa=self.empresa_b, nome="Pack B", grade=self.grade)
        resp = self.client.post("/api/compras/itens/", self.payload_revenda(pedido, pack=pack_b.id), format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_recebimento_manual_parcial_total_smoke(self):
        pedido = self.criar_pedido()
        item = self.incluir_item(self.payload_uso(pedido, qtd="10.000"))
        resp = self.client.post(
            "/api/compras/entregas/",
            {
                "item": item.id,
                "qtd_prevista": "10.000",
                "qtd_recebida": "4.000",
                "data_recebida": timezone.localdate().isoformat(),
                "status": "PARC",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        resp = self.client.patch(f"/api/compras/entregas/{resp.data['id']}/", {"qtd_recebida": "10.000", "status": "RECB"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
