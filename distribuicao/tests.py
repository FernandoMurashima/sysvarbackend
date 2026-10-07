from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from cadastros.models import Empresa, Loja
from financeiro.models import SequenciaDocumento
from financeiro.services import escopo_ano
from produto.models import (
    Colecao,
    ConfigEan,
    Cor,
    Estoque,
    FichaTecnica,
    Grade,
    Grupo,
    OrdemProducao,
    OrdemProducaoGrade,
    Produto,
    ProdutoDetalhe,
    Subgrupo,
    Tamanho,
    Unidade,
)

from .models import Distribuicao, DistribuicaoDestino, DistribuicaoItem, PedidoVendaDistribuicao, PedidoVendaDistribuicaoItem
from .services import (
    gerar_notas_faturamento_distribuicao,
    gerar_pedidos,
    preparar_distribuicao_producao,
    reservar_documento_distribuicao,
    reservar_documento_pedido_venda_distribuicao,
)


class DistribuicaoNumeracaoTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nome="Empresa Dist", documento="10222333000181", plano_completo=True)
        self.empresa_b = Empresa.objects.create(nome="Empresa Dist B", documento="20222333000181", plano_completo=True)
        self.loja_origem = Loja.objects.create(
            empresa=self.empresa,
            nome_loja="Fabrica Dist",
            apelido_loja="FD",
            cnpj="10222333000181",
            tipo_unidade=Loja.TIPO_FABRICA,
        )
        self.loja_destino = Loja.objects.create(
            empresa=self.empresa,
            nome_loja="Loja Dist",
            apelido_loja="LD",
            cnpj="10222333000262",
        )
        self.user = get_user_model().objects.create_superuser(
            username="admin-dist",
            email="admin-dist@example.com",
            password="12345678",
        )

    def test_reserva_primeira_e_segunda_distribuicao_2026(self):
        primeiro = reservar_documento_distribuicao(self.empresa, date(2026, 1, 10))
        segundo = reservar_documento_distribuicao(self.empresa, date(2026, 1, 11))

        self.assertEqual(primeiro, "DI260000001")
        self.assertEqual(segundo, "DI260000002")

    def test_reserva_por_empresa_reinicia_sequencia(self):
        reservar_documento_distribuicao(self.empresa, date(2026, 1, 10))

        documento_outra_empresa = reservar_documento_distribuicao(self.empresa_b, date(2026, 1, 10))

        self.assertEqual(documento_outra_empresa, "DI260000001")

    def test_reserva_por_ano_reinicia_sequencia(self):
        reservar_documento_distribuicao(self.empresa, date(2026, 12, 31))

        documento_ano_seguinte = reservar_documento_distribuicao(self.empresa, date(2027, 1, 1))

        self.assertEqual(documento_ano_seguinte, "DI270000001")

    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_criacao_manual_via_api_usa_numeracao_nova_e_data_efetiva(self):
        client = APIClient()
        client.force_authenticate(self.user)

        response = client.post(
            "/api/distribuicao/distribuicoes/",
            {
                "unidade_origem": self.loja_origem.pk,
                "data": "2026-06-15",
                "tipo": "MANUAL",
                "fator_preco": "0.2000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        distribuicao = Distribuicao.objects.get(pk=response.data["id"])
        self.assertEqual(distribuicao.numero, "DI260000001")
        self.assertEqual(distribuicao.data, date(2026, 6, 15))
        self.assertFalse(distribuicao.numero.startswith("DIST-"))

    def test_producao_usa_sequencia_oficial_da_distribuicao(self):
        ordem = self._ordem_producao_finalizada()

        with patch("distribuicao.services.timezone.localdate", return_value=date(2026, 7, 1)):
            distribuicao = preparar_distribuicao_producao(ordem)

        self.assertEqual(distribuicao.numero, "DI260000001")
        self.assertFalse(distribuicao.numero.startswith("DIST-"))
        self.assertEqual(
            SequenciaDocumento.objects.get(
                empresa=self.empresa,
                tipo_documento=SequenciaDocumento.TIPO_DISTRIBUICAO,
                escopo=escopo_ano(2026),
            ).proximo_numero,
            2,
        )

    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_manual_e_producao_compartilham_mesma_sequencia_sem_duplicar(self):
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.post(
            "/api/distribuicao/distribuicoes/",
            {
                "unidade_origem": self.loja_origem.pk,
                "data": "2026-08-01",
                "tipo": "MANUAL",
                "fator_preco": "0.2000",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        ordem = self._ordem_producao_finalizada(numero="OP-DIST-2")

        with patch("distribuicao.services.timezone.localdate", return_value=date(2026, 8, 2)):
            distribuicao_producao = preparar_distribuicao_producao(ordem)

        numeros = list(Distribuicao.objects.order_by("id").values_list("numero", flat=True))
        self.assertEqual(numeros, ["DI260000001", "DI260000002"])
        self.assertEqual(distribuicao_producao.numero, "DI260000002")
        self.assertEqual(len(numeros), len(set(numeros)))
        self.assertFalse(any(numero.startswith("DIST-") for numero in numeros))

    def test_reserva_pedido_venda_distribuicao_primeira_e_segunda_2026(self):
        primeiro = reservar_documento_pedido_venda_distribuicao(self.empresa, date(2026, 1, 10))
        segundo = reservar_documento_pedido_venda_distribuicao(self.empresa, date(2026, 1, 11))

        self.assertEqual(primeiro, "PV260000001")
        self.assertEqual(segundo, "PV260000002")

    def test_reserva_pedido_venda_distribuicao_por_empresa_reinicia_sequencia(self):
        reservar_documento_pedido_venda_distribuicao(self.empresa, date(2026, 1, 10))

        documento_outra_empresa = reservar_documento_pedido_venda_distribuicao(self.empresa_b, date(2026, 1, 10))

        self.assertEqual(documento_outra_empresa, "PV260000001")

    def test_reserva_pedido_venda_distribuicao_por_ano_reinicia_sequencia(self):
        reservar_documento_pedido_venda_distribuicao(self.empresa, date(2026, 12, 31))

        documento_ano_seguinte = reservar_documento_pedido_venda_distribuicao(self.empresa, date(2027, 1, 1))

        self.assertEqual(documento_ano_seguinte, "PV270000001")

    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_gerar_pedidos_usa_pv_consecutivo_idempotente_e_pesquisavel(self):
        distribuicao, lojas = self._distribuicao_confirmada_com_tres_lojas()

        with patch("distribuicao.services.timezone.localdate", return_value=date(2026, 9, 1)):
            pedidos = gerar_pedidos(distribuicao)

        self.assertEqual([pedido.numero for pedido in pedidos], ["PV260000001", "PV260000002", "PV260000003"])
        self.assertFalse(any(pedido.numero.startswith("PVD-") for pedido in pedidos))
        self.assertEqual({pedido.distribuicao_id for pedido in pedidos}, {distribuicao.pk})
        self.assertEqual([pedido.distribuicao.numero for pedido in pedidos], ["DI260000001", "DI260000001", "DI260000001"])
        self.assertEqual([pedido.loja_destino_id for pedido in pedidos], [loja.pk for loja in lojas])
        self.assertEqual(
            SequenciaDocumento.objects.get(
                empresa=self.empresa,
                tipo_documento=SequenciaDocumento.TIPO_PEDIDO_VENDA_DISTRIBUICAO,
                escopo=escopo_ano(2026),
            ).proximo_numero,
            4,
        )

        with patch("distribuicao.services.timezone.localdate", return_value=date(2026, 9, 2)):
            pedidos_segunda_chamada = gerar_pedidos(distribuicao)

        self.assertEqual(sorted(pedido.numero for pedido in pedidos_segunda_chamada), ["PV260000001", "PV260000002", "PV260000003"])
        self.assertEqual(PedidoVendaDistribuicao.objects.filter(distribuicao=distribuicao).count(), 3)
        self.assertEqual(
            SequenciaDocumento.objects.get(
                empresa=self.empresa,
                tipo_documento=SequenciaDocumento.TIPO_PEDIDO_VENDA_DISTRIBUICAO,
                escopo=escopo_ano(2026),
            ).proximo_numero,
            4,
        )

        client = APIClient()
        client.force_authenticate(self.user)
        resposta_pv = client.get("/api/distribuicao/pedidos-venda/", {"search": "PV260000001"})
        resposta_di = client.get("/api/distribuicao/pedidos-venda/", {"search": "DI260000001"})

        self.assertEqual(resposta_pv.status_code, 200, resposta_pv.content)
        self.assertEqual(resposta_di.status_code, 200, resposta_di.content)
        self.assertEqual([row["numero"] for row in self._results(resposta_pv.data)], ["PV260000001"])
        self.assertEqual({row["numero"] for row in self._results(resposta_di.data)}, {"PV260000001", "PV260000002", "PV260000003"})

    def test_geracao_nfe_preserva_pv_comercial_e_nao_altera_sequencia_fiscal(self):
        distribuicao, _lojas = self._distribuicao_confirmada_com_tres_lojas()

        with patch("distribuicao.services.timezone.localdate", return_value=date(2026, 9, 1)):
            pedidos = gerar_pedidos(distribuicao)
            item_base = pedidos[0].itens.first()
            pedido_extra = PedidoVendaDistribuicao.objects.create(
                empresa=self.empresa,
                distribuicao=Distribuicao.objects.create(
                    empresa=self.empresa,
                    numero="DI260000002",
                    unidade_origem=self.loja_origem,
                    data=date(2026, 9, 1),
                    tipo="MANUAL",
                    fator_preco=Decimal("0.2000"),
                    status=Distribuicao.STATUS_PEDIDOS_GERADOS,
                ),
                numero=reservar_documento_pedido_venda_distribuicao(self.empresa, date(2026, 9, 1)),
                unidade_origem=self.loja_origem,
                loja_destino=pedidos[0].loja_destino,
                data_pedido=date(2026, 9, 1),
                status=PedidoVendaDistribuicao.STATUS_AGUARDANDO_FATURAMENTO,
                quantidade_total=Decimal("1.000"),
                valor_total_custo=Decimal("10.00"),
                valor_total_venda=Decimal("12.00"),
            )
            PedidoVendaDistribuicaoItem.objects.create(
                pedido=pedido_extra,
                produto=item_base.produto,
                sku=item_base.sku,
                referencia=item_base.referencia,
                descricao=item_base.descricao,
                cor_descricao=item_base.cor_descricao,
                tamanho_descricao=item_base.tamanho_descricao,
                ean13=item_base.ean13,
                quantidade=Decimal("1.000"),
                custo_unitario=Decimal("10.0000"),
                preco_unitario=Decimal("12.0000"),
                total_custo=Decimal("10.00"),
                total_item=Decimal("12.00"),
            )
            nota = gerar_notas_faturamento_distribuicao([pedidos[0], pedido_extra], self.user)[0]

        self.assertEqual(nota.numero, "1")
        self.assertEqual(nota.documento_origem, "PV260000001, PV260000004")
        self.assertEqual(nota.observacoes, "NF-e agrupada dos pedidos: PV260000001, PV260000004")
        self.loja_origem.refresh_from_db()
        self.assertEqual(self.loja_origem.proximo_numero_nfe, 2)

    def _results(self, data):
        return data.get("results", data) if isinstance(data, dict) else data

    def _distribuicao_confirmada_com_tres_lojas(self):
        lojas = [
            self.loja_destino,
            Loja.objects.create(empresa=self.empresa, nome_loja="Loja Dist 2", apelido_loja="LD2", cnpj="10222333000343"),
            Loja.objects.create(empresa=self.empresa, nome_loja="Loja Dist 3", apelido_loja="LD3", cnpj="10222333000424"),
        ]
        unidade = Unidade.objects.create(empresa=self.empresa, Descricao="Unidade PV", Codigo="PV")
        colecao = Colecao.objects.create(empresa=self.empresa, Descricao="Colecao PV", Codigo="26", Estacao="03")
        grupo = Grupo.objects.create(empresa=self.empresa, Codigo="03", CodigoRef="03", Descricao="Grupo PV", Margem=0)
        subgrupo = Subgrupo.objects.create(empresa=self.empresa, Idgrupo=grupo, Descricao="Subgrupo PV", Margem=0)
        grade = Grade.objects.create(empresa=self.empresa, Descricao="Grade PV")
        tamanho = Tamanho.objects.create(empresa=self.empresa, idgrade=grade, Tamanho="PV")
        cor = Cor.objects.create(empresa=self.empresa, Descricao="Cor PV", Codigo="PV", Cor="Cor PV")
        ConfigEan.objects.create(empresa=self.empresa, company_prefix="1001")
        produto = Produto.objects.create(
            empresa=self.empresa,
            tipo_produto="3",
            descricao="Produto PV",
            descricao_reduzida="PV",
            unidade=unidade,
            grupo=grupo,
            subgrupo=subgrupo,
            colecao=colecao,
            grade=grade,
            custo_medio=Decimal("10.0000"),
        )
        sku = ProdutoDetalhe.objects.create(
            produto=produto,
            idcor=cor,
            idtamanho=tamanho,
            custo_medio=Decimal("10.0000"),
        )
        distribuicao = Distribuicao.objects.create(
            empresa=self.empresa,
            numero="DI260000001",
            unidade_origem=self.loja_origem,
            data=date(2026, 9, 1),
            tipo="MANUAL",
            fator_preco=Decimal("0.2000"),
            status=Distribuicao.STATUS_CONFIRMADA,
        )
        item = DistribuicaoItem.objects.create(
            distribuicao=distribuicao,
            produto=produto,
            sku=sku,
            referencia=produto.referencia,
            descricao=produto.descricao,
            ean13=sku.ean13,
            estoque_fisico=Decimal("10.000"),
            estoque_disponivel=Decimal("3.000"),
            quantidade_selecionada=Decimal("3.000"),
            custo_unitario=Decimal("10.0000"),
            custo_total=Decimal("30.00"),
        )
        for idx, loja in enumerate(lojas, start=1):
            DistribuicaoDestino.objects.create(
                distribuicao=distribuicao,
                item=item,
                loja_destino=loja,
                quantidade_sugerida=Decimal("1.000"),
                quantidade_ajustada=Decimal("1.000"),
                quantidade_confirmada=Decimal("1.000"),
                prioridade=idx,
                status=DistribuicaoDestino.STATUS_CONFIRMADO,
            )
        return distribuicao, lojas

    def _ordem_producao_finalizada(self, numero="OP-DIST-1"):
        sufixo = "02" if numero.endswith("2") else "01"
        unidade = Unidade.objects.create(empresa=self.empresa, Descricao=f"Unidade {numero}", Codigo=sufixo)
        colecao = Colecao.objects.create(empresa=self.empresa, Descricao=f"Colecao {numero}", Codigo="26", Estacao=sufixo)
        grupo = Grupo.objects.create(empresa=self.empresa, Codigo=sufixo, CodigoRef=sufixo, Descricao=f"Grupo {numero}", Margem=0)
        subgrupo = Subgrupo.objects.create(empresa=self.empresa, Idgrupo=grupo, Descricao=f"Subgrupo {numero}", Margem=0)
        grade = Grade.objects.create(empresa=self.empresa, Descricao=f"Grade {numero}")
        tamanho = Tamanho.objects.create(empresa=self.empresa, idgrade=grade, Tamanho=sufixo)
        cor = Cor.objects.create(empresa=self.empresa, Descricao=f"Cor {numero}", Codigo=sufixo, Cor=f"Cor {numero}")
        ConfigEan.objects.create(empresa=self.empresa, company_prefix=sufixo.zfill(4))
        produto = Produto.objects.create(
            empresa=self.empresa,
            tipo_produto="3",
            descricao=f"Produto {numero}",
            descricao_reduzida=numero,
            unidade=unidade,
            grupo=grupo,
            subgrupo=subgrupo,
            colecao=colecao,
            grade=grade,
            custo_medio=Decimal("10.0000"),
        )
        sku = ProdutoDetalhe.objects.create(
            produto=produto,
            idcor=cor,
            idtamanho=tamanho,
            custo_medio=Decimal("10.0000"),
        )
        Estoque.objects.create(
            Idloja=self.loja_origem,
            CodigodeBarra=sku.ean13,
            referencia=produto.referencia,
            Estoque=Decimal("5.000"),
            reserva=Decimal("0.000"),
        )
        ficha = FichaTecnica.objects.create(
            empresa=self.empresa,
            produto_final=produto,
            versao=numero,
            rendimento=Decimal("1.000"),
            status=FichaTecnica.STATUS_APROVADA,
        )
        ordem = OrdemProducao.objects.create(
            empresa=self.empresa,
            numero=numero,
            ficha_tecnica=ficha,
            produto_final=produto,
            sku_final=sku,
            quantidade=Decimal("1.000"),
            status=OrdemProducao.STATUS_FINALIZADA,
            custo_real=Decimal("10.00"),
        )
        OrdemProducaoGrade.objects.create(ordem=ordem, sku_final=sku, quantidade=Decimal("1.000"))
        return ordem
