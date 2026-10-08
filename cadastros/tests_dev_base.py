from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.exceptions import ValidationError
from django.test import TransactionTestCase, override_settings
from django.db.models import Count
from django.utils import timezone

from auditoria.models import AuditAction, AuditLog
from cadastros.models import Empresa, Fornecedor, FornecedorCategoria, FornecedorContato, FornecedorEndereco, Loja
from compras.models import Cotacao, PedidoCompra, PedidoCompraItem, Requisicao
from distribuicao.models import Distribuicao, MercadoriaTransito, PerfilDistribuicao, PerfilDistribuicaoItem
from financeiro.models import CashbackConfig, ConfigFinanceira, FormaPagamento, FormaPagamentoCondicao, MovimentacaoFinanceira, Pagar, PrazoPagamento, Receber, SequenciaDocumento, ValeTroca, ValeTrocaMovimento, ValeTrocaReserva
from financeiro.services import escopo_empresa, escopo_loja
from fiscal.models.nota_fiscal_entrada import AgenteLocalSysvar, AtivacaoAgenteLocalSysvar, ConfiguracaoXmlFornecedor, FormaPagamentoFiscalMap, NotaFiscalEntrada, RecebimentoMercadoriaConferenciaItem, RecebimentoMercadoriaEfetivacaoEstoque, RecebimentoMercadoriaEstoque, RecebimentoMercadoriaPedido, RecebimentoMercadoriaTermo, XmlFornecedorRecebido
from fiscal.models.nota_fiscal_saida import NotaFiscalSaida
from fiscal.models.venda_pdv import NFCe, NFeDevolucao, VendaDevolucao, VendaPdv
from fiscal.services.documentos import reservar_documento_devolucao
from fiscal.services.nfe_devolucao import registrar_nfe_devolucao
from fiscal.views.venda_pdv import reservar_documento_venda
from hub.models import HubClienteMapeamento, HubDevolucaoFaixaNumeracao, HubDevolucaoMapeamento, HubEventoRecebido, HubNFCeMapeamento, HubVendaFaixaNumeracao, HubVendaMapeamento, SysvarHub
from produto.models import ConfigEan, Estoque, EstoqueMovimentacao, FichaTecnica, FichaTecnicaItem, Produto, ProdutoDetalhe, ProdutoFornecedor, ProdutoUsoConsumoEstoque, ProdutoUsoConsumoMovimentacao, Promocao
from sysvar_devtools.dev_base import SysvarDevBaseService


class SysvarDevBaseTests(TransactionTestCase):
    def _runtime_models_numeracao(self):
        return [
            SequenciaDocumento,
            VendaPdv,
            VendaDevolucao,
            NFCe,
            NFeDevolucao,
            ValeTroca,
            ValeTrocaMovimento,
            ValeTrocaReserva,
            SysvarHub,
            HubVendaFaixaNumeracao,
            HubDevolucaoFaixaNumeracao,
            HubVendaMapeamento,
            HubDevolucaoMapeamento,
            HubNFCeMapeamento,
            HubClienteMapeamento,
            HubEventoRecebido,
        ]

    def _assert_sem_runtime_numeracao(self):
        for model in self._runtime_models_numeracao():
            self.assertEqual(model.objects.count(), 0, model.__name__)

    def _objetos_oficiais_minimos(self):
        empresa = Empresa.objects.get(documento="42000001000186")
        loja = Loja.objects.filter(empresa=empresa).order_by("id").first()
        cliente = empresa.clientes.order_by("id").first()
        vendedor = empresa.funcionarios.order_by("id").first()
        self.assertIsNotNone(loja)
        self.assertIsNotNone(cliente)
        self.assertIsNotNone(vendedor)
        return empresa, loja, cliente, vendedor

    def _criar_runtime_numeracao_representativo(self):
        empresa, loja, cliente, vendedor = self._objetos_oficiais_minimos()
        SequenciaDocumento.objects.create(
            empresa=empresa,
            tipo_documento=SequenciaDocumento.TIPO_VENDA,
            escopo=escopo_loja(loja.pk),
            proximo_numero=10,
        )
        SequenciaDocumento.objects.create(
            empresa=empresa,
            tipo_documento=SequenciaDocumento.TIPO_DEVOLUCAO,
            escopo=escopo_empresa(),
            proximo_numero=20,
        )
        hub = SysvarHub.objects.create(loja=loja, nome="Hub runtime DEV")
        HubVendaFaixaNumeracao.objects.create(hub=hub, inicio=1, fim=100)
        HubDevolucaoFaixaNumeracao.objects.create(hub=hub, inicio=1, fim=100)
        HubClienteMapeamento.objects.create(hub=hub, cliente_uuid=uuid.uuid4(), cliente=cliente)
        venda = VendaPdv.objects.create(
            empresa=empresa,
            loja=loja,
            cliente=cliente,
            vendedor=vendedor,
            documento=f"VE{loja.pk:03d}0000001",
            forma_pagamento="DINHEIRO",
            subtotal=Decimal("10.00"),
            total=Decimal("10.00"),
        )
        devolucao = VendaDevolucao.objects.create(
            empresa=empresa,
            venda=venda,
            loja=loja,
            cliente=cliente,
            documento="DEV-0000001",
            subtotal=Decimal("10.00"),
            credito_cliente=Decimal("10.00"),
        )
        nfce = NFCe.objects.create(venda=venda, loja=loja, serie=1, numero=1, status=NFCe.Status.GERADA, xml="<NFCe />")
        NFeDevolucao.objects.create(devolucao=devolucao, loja=loja, nfce_origem=nfce, serie=1, numero=1, status=NFeDevolucao.Status.DIGITADA)
        vale = ValeTroca.objects.create(empresa=empresa, cliente=cliente, loja=loja, devolucao=devolucao, documento="VT0000001", valor_original=Decimal("10.00"), saldo=Decimal("10.00"))
        ValeTrocaMovimento.objects.create(vale=vale, tipo=ValeTrocaMovimento.TIPO_CREDITO, valor=Decimal("10.00"), saldo_apos=Decimal("10.00"))
        venda_uuid = uuid.uuid4()
        devolucao_uuid = uuid.uuid4()
        HubVendaMapeamento.objects.create(hub=hub, venda_uuid=venda_uuid, venda=venda, documento=venda.documento)
        HubDevolucaoMapeamento.objects.create(hub=hub, devolucao_uuid=devolucao_uuid, devolucao=devolucao, venda_uuid=venda_uuid, documento=devolucao.documento, vale_documento=vale.documento)
        HubNFCeMapeamento.objects.create(hub=hub, nfce_uuid=uuid.uuid4(), nfce=nfce, venda_uuid=venda_uuid, ultima_versao_evento=1)
        ValeTrocaReserva.objects.create(empresa=empresa, hub=hub, venda_uuid=uuid.uuid4(), operacao_uuid=uuid.uuid4(), vale=vale, valor=Decimal("5.00"))
        HubEventoRecebido.objects.create(hub=hub, evento_uuid=uuid.uuid4(), chave_idempotencia="runtime-dev", tipo="VENDA_FINALIZADA", payload_hash="a" * 64, payload={})

    def test_reset_bloqueia_ambiente_producao(self):
        service = SysvarDevBaseService()
        with override_settings(DEBUG=False, DATABASES={"default": {"ENGINE": "django.db.backends.mysql", "NAME": "sysvar_prod"}}):
            with self.assertRaises(CommandError):
                service.assert_not_production(destructive=True)

    def test_reset_carrega_jsons_e_valida(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        report = SysvarDevBaseService().validate()
        self.assertTrue(report.valid, report.problems)
        self.assertEqual(Empresa.objects.count(), 1)
        self.assertEqual(Loja.objects.count(), 4)
        self.assertEqual(get_user_model().objects.count(), 8)
        self.assertEqual(Fornecedor.objects.count(), 45)
        self.assertEqual(FornecedorCategoria.objects.count(), 45)
        self.assertEqual(FornecedorContato.objects.count(), 65)
        self.assertEqual(FornecedorEndereco.objects.count(), 65)
        self.assertEqual(ConfigFinanceira.objects.count(), 1)
        self.assertEqual(CashbackConfig.objects.count(), 1)
        self.assertEqual(Produto.objects.count(), 271)
        self.assertEqual(ProdutoDetalhe.objects.count(), 1480)
        self.assertEqual(Estoque.objects.count(), ProdutoDetalhe.objects.count() * Loja.objects.count())
        self.assertEqual(ProdutoUsoConsumoEstoque.objects.count(), Produto.objects.filter(tipo_produto="2").count() * Loja.objects.count())
        self.assertEqual(ProdutoFornecedor.objects.count(), 192)
        self.assertEqual(FichaTecnica.objects.count(), 45)
        self.assertEqual(FichaTecnicaItem.objects.count(), 167)
        self.assertEqual(Promocao.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)
        self._assert_sem_runtime_numeracao()

    def test_reset_idempotente_e_sem_operacional(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        first = SysvarDevBaseService().validate().created
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        second = SysvarDevBaseService().validate().created
        self.assertEqual(first, second)
        self.assertEqual(ConfigFinanceira.objects.count(), 1)
        self.assertEqual(CashbackConfig.objects.count(), 1)
        for model in [EstoqueMovimentacao, ProdutoUsoConsumoMovimentacao, Requisicao, Cotacao, PedidoCompra, Distribuicao, MercadoriaTransito, MovimentacaoFinanceira, Pagar, Receber, AgenteLocalSysvar, AtivacaoAgenteLocalSysvar, ConfiguracaoXmlFornecedor, XmlFornecedorRecebido, RecebimentoMercadoriaEstoque, RecebimentoMercadoriaPedido, RecebimentoMercadoriaConferenciaItem, RecebimentoMercadoriaTermo, RecebimentoMercadoriaEfetivacaoEstoque, NotaFiscalEntrada, NotaFiscalSaida, VendaPdv]:
            self.assertFalse(model.objects.exists(), model.__name__)
        self._assert_sem_runtime_numeracao()

    def test_reset_remove_estado_preexistente_da_nova_numeracao(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        self._criar_runtime_numeracao_representativo()
        self.assertTrue(SequenciaDocumento.objects.exists())
        self.assertTrue(HubVendaFaixaNumeracao.objects.exists())
        self.assertTrue(HubDevolucaoFaixaNumeracao.objects.exists())

        call_command("sysvar_dev_base", "--reset", verbosity=0)

        self._assert_sem_runtime_numeracao()
        self.assertEqual(Empresa.objects.count(), 1)
        self.assertEqual(Loja.objects.count(), 4)
        self.assertEqual(ConfigFinanceira.objects.count(), 1)
        self.assertEqual(CashbackConfig.objects.count(), 1)
        report = SysvarDevBaseService().validate()
        self.assertTrue(report.valid, report.problems)

    def test_validate_rejeita_residuos_da_nova_numeracao(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        empresa, loja, _cliente, _vendedor = self._objetos_oficiais_minimos()
        SequenciaDocumento.objects.create(
            empresa=empresa,
            tipo_documento=SequenciaDocumento.TIPO_VENDA,
            escopo=escopo_loja(loja.pk),
            proximo_numero=2,
        )

        report = SysvarDevBaseService().validate()

        self.assertFalse(report.valid)
        self.assertIn("financeiro.SequenciaDocumento", ", ".join(report.problems))

        SequenciaDocumento.objects.all().delete()
        hub = SysvarHub.objects.create(loja=loja, nome="Hub validacao")
        HubVendaFaixaNumeracao.objects.create(hub=hub, inicio=1, fim=10)
        HubDevolucaoFaixaNumeracao.objects.create(hub=hub, inicio=1, fim=10)

        report = SysvarDevBaseService().validate()

        self.assertFalse(report.valid)
        problemas = ", ".join(report.problems)
        self.assertIn("hub.HubVendaFaixaNumeracao", problemas)
        self.assertIn("hub.HubDevolucaoFaixaNumeracao", problemas)

    def test_primeira_venda_apos_base_limpa_usa_funcao_canonica(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        empresa, loja, _cliente, _vendedor = self._objetos_oficiais_minimos()

        primeiro = reservar_documento_venda(empresa, loja)
        segundo = reservar_documento_venda(empresa, loja)

        self.assertEqual(primeiro, f"VE{loja.id:03d}0000001")
        self.assertEqual(segundo, f"VE{loja.id:03d}0000002")
        sequencia = SequenciaDocumento.objects.get(empresa=empresa, tipo_documento=SequenciaDocumento.TIPO_VENDA, escopo=escopo_loja(loja.pk))
        self.assertEqual(sequencia.proximo_numero, 3)

    def test_primeira_devolucao_apos_base_limpa_usa_funcao_canonica(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        empresa = Empresa.objects.get(documento="42000001000186")

        primeiro = reservar_documento_devolucao(empresa)
        segundo = reservar_documento_devolucao(empresa)

        self.assertEqual(primeiro, "DEV-0000001")
        self.assertEqual(segundo, "DEV-0000002")
        sequencia = SequenciaDocumento.objects.get(empresa=empresa, tipo_documento=SequenciaDocumento.TIPO_DEVOLUCAO, escopo=escopo_empresa())
        self.assertEqual(sequencia.proximo_numero, 3)

    def test_documentos_comerciais_independentes_das_numeracoes_fiscais(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        empresa, loja, cliente, vendedor = self._objetos_oficiais_minimos()
        documento_venda = reservar_documento_venda(empresa, loja)
        venda = VendaPdv.objects.create(
            empresa=empresa,
            loja=loja,
            cliente=cliente,
            vendedor=vendedor,
            documento=documento_venda,
            forma_pagamento="DINHEIRO",
            subtotal=Decimal("10.00"),
            total=Decimal("10.00"),
        )
        nfce = NFCe.objects.create(venda=venda, loja=loja, serie=1, numero=1, status=NFCe.Status.GERADA, xml="<NFCe />")
        documento_devolucao = reservar_documento_devolucao(empresa)
        devolucao = VendaDevolucao.objects.create(
            empresa=empresa,
            venda=venda,
            loja=loja,
            cliente=cliente,
            documento=documento_devolucao,
            subtotal=Decimal("10.00"),
            credito_cliente=Decimal("10.00"),
        )

        nfe = registrar_nfe_devolucao(devolucao)

        self.assertEqual(venda.documento, f"VE{loja.id:03d}0000001")
        self.assertEqual(nfce.numero, 1)
        self.assertNotEqual(venda.documento, str(nfce.numero))
        self.assertEqual(devolucao.documento, "DEV-0000001")
        self.assertIsInstance(nfe.numero, int)
        self.assertNotEqual(devolucao.documento, str(nfe.numero))

    def test_reset_recria_mapas_fiscais_das_formas_pdv(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        empresa = Empresa.objects.get(documento="42000001000186")

        mapas = {
            (mapa.forma_pagamento.tipo, mapa.codigo_tpag)
            for mapa in FormaPagamentoFiscalMap.objects.select_related("forma_pagamento").filter(
                empresa=empresa,
                ativo=True,
                forma_pagamento__empresa=empresa,
            )
        }

        self.assertIn((FormaPagamento.TIPO_DINHEIRO, "01"), mapas)
        self.assertIn((FormaPagamento.TIPO_CREDITO, "03"), mapas)
        self.assertIn((FormaPagamento.TIPO_DEBITO, "04"), mapas)
        self.assertIn((FormaPagamento.TIPO_PIX, "17"), mapas)

    def test_reset_remove_registros_runtime_de_agente_e_configuracao_xml(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        empresa = Empresa.objects.get(documento="42000001000186")
        loja = Loja.objects.filter(empresa=empresa).order_by("id").first()
        usuario = get_user_model().objects.filter(empresa=empresa).order_by("id").first()
        agente = AgenteLocalSysvar.objects.create(
            empresa=empresa,
            identificador="AGENTE-TESTE-RESET",
            nome="Agente teste reset",
            token_hash="b" * 64,
            token_prefixo="TOKENRESET",
            hostname="dev-machine",
        )
        AtivacaoAgenteLocalSysvar.objects.create(
            empresa=empresa,
            codigo_hash="c" * 64,
            codigo_prefixo="TST1",
            criado_por=usuario,
            expira_em=timezone.now(),
            agente=agente,
        )
        ConfiguracaoXmlFornecedor.objects.create(
            empresa=empresa,
            loja=loja,
            caminho_local=r"C:\SysvarXML",
            identificador_agente=agente.identificador,
        )

        call_command("sysvar_dev_base", "--reset", verbosity=0)

        self.assertFalse(AgenteLocalSysvar.objects.exists())
        self.assertFalse(AtivacaoAgenteLocalSysvar.objects.exists())
        self.assertFalse(ConfiguracaoXmlFornecedor.objects.exists())
        self.assertEqual(Empresa.objects.count(), 1)
        self.assertEqual(Loja.objects.count(), 4)
        self.assertEqual(ConfigFinanceira.objects.count(), 1)
        self.assertEqual(CashbackConfig.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 0)
        self.assertFalse(SysvarDevBaseService().forbidden())
        report = SysvarDevBaseService().validate()
        self.assertTrue(report.valid, report.problems)

        call_command("sysvar_dev_base", "--reset", verbosity=0)
        self.assertTrue(SysvarDevBaseService().validate().valid)

    def test_reset_remove_recebimento_operacional_com_conferencia_termo_e_efetivacao(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        empresa = Empresa.objects.get(documento="42000001000186")
        loja = Loja.objects.filter(empresa=empresa).order_by("id").first()
        fornecedor = Fornecedor.objects.filter(empresa=empresa).order_by("id").first()
        usuario = get_user_model().objects.filter(empresa=empresa).order_by("id").first()
        sku = ProdutoDetalhe.objects.select_related("produto", "idcor", "idtamanho").filter(produto__empresa=empresa).order_by("IdprodutoDetalhe").first()
        pedido = PedidoCompra.objects.create(empresa=empresa, tipo="1", loja=loja, fornecedor=fornecedor, status="AP")
        pedido_item = PedidoCompraItem.objects.create(
            pedido=pedido,
            produto=sku.produto,
            cor=sku.idcor,
            qtd=Decimal("1.000"),
            preco_unit=Decimal("10.00"),
            total_item=Decimal("10.00"),
        )
        xml = XmlFornecedorRecebido.objects.create(
            empresa=empresa,
            loja=loja,
            fornecedor=fornecedor,
            chave_acesso="9" * 44,
            numero="999001",
            status_operacional=XmlFornecedorRecebido.StatusOperacional.RECEBIDO,
        )
        recebimento = RecebimentoMercadoriaEstoque.objects.create(
            empresa=empresa,
            loja=loja,
            fornecedor=fornecedor,
            xml_fornecedor=xml,
            status=RecebimentoMercadoriaEstoque.Status.CONCLUIDO,
            criado_por=usuario,
        )
        RecebimentoMercadoriaPedido.objects.create(recebimento=recebimento, pedido=pedido)
        RecebimentoMercadoriaConferenciaItem.objects.create(
            recebimento=recebimento,
            pedido=pedido,
            pedido_item=pedido_item,
            produto=sku.produto,
            cor=sku.idcor,
            tamanho=sku.idtamanho,
            produto_detalhe=sku,
            quantidade_esperada=Decimal("1.000"),
            quantidade_recebida=Decimal("1.000"),
        )
        termo = RecebimentoMercadoriaTermo.objects.create(
            recebimento=recebimento,
            empresa=empresa,
            encerrado_por=usuario,
            encerrado_em=timezone.now(),
            snapshot={"conferencia_sku": []},
            hash_sha256="a" * 64,
        )
        RecebimentoMercadoriaEfetivacaoEstoque.objects.create(
            recebimento=recebimento,
            termo=termo,
            empresa=empresa,
            loja=loja,
            efetivado_por=usuario,
            efetivado_em=timezone.now(),
            quantidade_total=Decimal("1.000"),
            quantidade_skus=1,
            hash_termo=termo.hash_sha256,
        )

        call_command("sysvar_dev_base", "--reset", verbosity=0)

        for model in [PedidoCompra, PedidoCompraItem, XmlFornecedorRecebido, RecebimentoMercadoriaEstoque, RecebimentoMercadoriaPedido, RecebimentoMercadoriaConferenciaItem, RecebimentoMercadoriaTermo, RecebimentoMercadoriaEfetivacaoEstoque]:
            self.assertFalse(model.objects.exists(), model.__name__)
        report = SysvarDevBaseService().validate()
        self.assertTrue(report.valid, report.problems)

        call_command("sysvar_dev_base", "--reset", verbosity=0)
        self.assertTrue(SysvarDevBaseService().validate().valid)

    def test_ean_e_distribuicao(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        self.assertEqual(ProdutoDetalhe.objects.exclude(ean13="").count(), 1480)
        self.assertFalse(ProdutoDetalhe.objects.values("ean13").annotate(c=Count("ean13")).filter(c__gt=1).exists())
        self.assertEqual(ConfigEan.objects.get().next_itemref, 1481)
        self.assertEqual(PerfilDistribuicao.objects.count(), 2)
        self.assertEqual(PerfilDistribuicaoItem.objects.count(), 6)

    def test_estoque_estrutural_sku_por_loja_sem_movimentacao(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        lojas_count = Loja.objects.count()
        skus_count = ProdutoDetalhe.objects.count()

        self.assertEqual(skus_count, 1480)
        self.assertEqual(lojas_count, 4)
        self.assertEqual(Estoque.objects.count(), skus_count * lojas_count)
        self.assertEqual(Estoque.objects.exclude(reserva=0).count(), 0)
        self.assertEqual(EstoqueMovimentacao.objects.count(), 0)
        self.assertFalse(Estoque.objects.values("CodigodeBarra", "Idloja").annotate(c=Count("Idestoque")).filter(c__gt=1).exists())

        por_sku = Estoque.objects.values("CodigodeBarra").annotate(lojas=Count("Idloja", distinct=True), linhas=Count("Idestoque"))
        self.assertEqual(por_sku.filter(lojas=lojas_count, linhas=lojas_count).count(), skus_count)
        self.assertFalse(Estoque.objects.exclude(CodigodeBarra__in=ProdutoDetalhe.objects.values("ean13")).exists())

        refs = {
            sku.ean13: sku.produto.referencia or ""
            for sku in ProdutoDetalhe.objects.select_related("produto").all()
        }
        divergentes = [
            estoque.pk
            for estoque in Estoque.objects.only("Idestoque", "CodigodeBarra", "referencia")
            if (estoque.referencia or "") != refs.get(estoque.CodigodeBarra, "")
        ]
        self.assertEqual(divergentes, [])

        saldos = {
            (estoque.Idloja.apelido_loja, estoque.CodigodeBarra): estoque.Estoque
            for estoque in Estoque.objects.select_related("Idloja")
        }
        comerciais = set(ProdutoDetalhe.objects.filter(produto__tipo_produto="1").values_list("ean13", flat=True))
        for (loja, ean), saldo in saldos.items():
            if ean not in comerciais:
                self.assertEqual(saldo, Decimal("0"))
            elif loja in {"Barra", "Tijuca", "Centro"}:
                self.assertEqual(saldo, Decimal("20"))
            elif loja == "Fábrica":
                self.assertEqual(saldo, Decimal("0"))

    def test_estoque_estrutural_uso_consumo_por_loja_sem_movimentacao(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        lojas_count = Loja.objects.count()
        uso_count = Produto.objects.filter(tipo_produto="2").count()

        self.assertEqual(uso_count, 34)
        self.assertEqual(ProdutoUsoConsumoEstoque.objects.count(), uso_count * lojas_count)
        self.assertEqual(ProdutoUsoConsumoEstoque.objects.exclude(saldo=0).count(), 0)
        self.assertEqual(ProdutoUsoConsumoMovimentacao.objects.count(), 0)
        self.assertFalse(ProdutoUsoConsumoEstoque.objects.values("empresa", "produto", "loja").annotate(c=Count("id")).filter(c__gt=1).exists())
        self.assertFalse(ProdutoUsoConsumoEstoque.objects.exclude(produto__tipo_produto="2").exists())

        por_produto = ProdutoUsoConsumoEstoque.objects.values("produto").annotate(lojas=Count("loja", distinct=True), linhas=Count("id"))
        self.assertEqual(por_produto.filter(lojas=lojas_count, linhas=lojas_count).count(), uso_count)

    def test_reset_recria_config_financeira_a_partir_do_seed(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        empresa = Empresa.objects.get(documento="42000001000186")
        config = ConfigFinanceira.objects.get(empresa=empresa)

        self.assertEqual(config.natureza_juros_pagos.codigo, "3505")
        self.assertEqual(config.natureza_juros_recebidos.codigo, "4301")
        self.assertEqual(config.natureza_tarifas_pagas.codigo, "3503")
        self.assertEqual(config.natureza_multas_pagas.codigo, "3506")
        self.assertEqual(config.natureza_multas_recebidas.codigo, "4302")
        self.assertEqual(config.natureza_descontos_concedidos.codigo, "2102")
        self.assertEqual(config.natureza_descontos_obtidos.codigo, "4303")

        call_command("sysvar_dev_base", "--reset", verbosity=0)

        empresa = Empresa.objects.get(documento="42000001000186")
        self.assertEqual(ConfigFinanceira.objects.filter(empresa=empresa).count(), 1)

    def test_reset_recria_cashback_config_a_partir_do_seed(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        empresa = Empresa.objects.get(documento="42000001000186")
        config = CashbackConfig.objects.get(empresa=empresa)

        self.assertEqual(config.nome, "Cashback Padrão Base Dev")
        self.assertTrue(config.ativo)
        self.assertEqual(str(config.percentual), "5.0000")
        self.assertEqual(config.validade_dias, 180)
        self.assertEqual(str(config.valor_minimo_geracao), "100.00")
        self.assertEqual(str(config.valor_minimo_uso), "20.00")
        self.assertEqual(str(config.limite_uso_percentual), "50.0000")
        self.assertFalse(config.consumidor_final_participa)

        call_command("sysvar_dev_base", "--reset", verbosity=0)

        empresa = Empresa.objects.get(documento="42000001000186")
        self.assertEqual(CashbackConfig.objects.filter(empresa=empresa).count(), 1)

    def test_reset_remove_auditlog_existente_e_termina_sem_auditoria(self):
        AuditLog.objects.internal_create(
            action=AuditAction.LEGACY_EVENT,
            app_label="cadastros",
            model="empresa",
            object_id="base-antiga",
        )
        self.assertEqual(AuditLog.objects.count(), 1)

        call_command("sysvar_dev_base", "--reset", verbosity=0)

        self.assertEqual(AuditLog.objects.count(), 0)
        report = SysvarDevBaseService().validate()
        self.assertTrue(report.valid, report.problems)

    def test_imutabilidade_normal_do_auditlog_permanece_ativa(self):
        log = AuditLog.objects.internal_create(
            action=AuditAction.LEGACY_EVENT,
            app_label="cadastros",
            model="empresa",
            object_id="imutavel",
        )

        with self.assertRaises(ValidationError):
            AuditLog.objects.filter(pk=log.pk).update(action=AuditAction.OBJECT_UPDATED)
        with self.assertRaises(ValidationError):
            AuditLog.objects.filter(pk=log.pk).delete()
        with self.assertRaises(ValidationError):
            log.delete()

    def test_create_sem_reset_materializa_estoque_sem_duplicar(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        estoque_count = Estoque.objects.count()
        uso_estoque_count = ProdutoUsoConsumoEstoque.objects.count()

        call_command("sysvar_dev_base", "--create", verbosity=0)

        self.assertEqual(Estoque.objects.count(), estoque_count)
        self.assertEqual(ProdutoUsoConsumoEstoque.objects.count(), uso_estoque_count)
        self.assertEqual(EstoqueMovimentacao.objects.count(), 0)
        self.assertEqual(ProdutoUsoConsumoMovimentacao.objects.count(), 0)

    def test_forma_credito_unica_e_prazos_separados(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        empresa = Empresa.objects.get(documento="42000001000186")

        creditos = list(FormaPagamento.objects.filter(empresa=empresa, tipo=FormaPagamento.TIPO_CREDITO, ativo=True).values_list("codigo", "descricao"))

        self.assertEqual(creditos, [("CRE", "Cartão de crédito")])
        self.assertFalse(FormaPagamento.objects.filter(empresa=empresa, codigo__in=["CCR", "CC2", "CC3", "CC4"]).exists())
        self.assertTrue({"AV", "30D", "30-60", "30-60-90", "30-60-90-120"}.issubset(set(PrazoPagamento.objects.filter(empresa=empresa).values_list("codigo", flat=True))))

    def test_reset_cria_formas_e_condicoes_pagamento_oficiais(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        empresa = Empresa.objects.get(documento="42000001000186")

        formas = {
            forma.codigo: forma
            for forma in FormaPagamento.objects.filter(empresa=empresa, codigo__in=["DIN", "PIX", "DEB", "CRE"])
        }
        self.assertEqual(set(formas), {"DIN", "PIX", "DEB", "CRE"})
        self.assertEqual(FormaPagamento.objects.filter(empresa=empresa, ativo=True, tipo=FormaPagamento.TIPO_CREDITO).count(), 1)
        self.assertEqual(FormaPagamento.objects.get(empresa=empresa, ativo=True, tipo=FormaPagamento.TIPO_CREDITO).codigo, "CRE")
        self.assertFalse(formas["DIN"].permite_parcelamento)
        self.assertFalse(formas["PIX"].permite_parcelamento)
        self.assertTrue(formas["DEB"].permite_parcelamento)
        self.assertTrue(formas["CRE"].permite_parcelamento)
        self.assertEqual(formas["DIN"].prazo_pagamento.codigo, "AV")
        self.assertEqual(formas["PIX"].prazo_pagamento.codigo, "AV")
        self.assertEqual(formas["DEB"].prazo_pagamento.codigo, "AV")
        self.assertEqual(formas["CRE"].prazo_pagamento.codigo, "30D")

        condicoes = {
            (c.forma_pagamento.codigo, c.prazo_pagamento.codigo): c
            for c in FormaPagamentoCondicao.objects.select_related("forma_pagamento", "prazo_pagamento").filter(empresa=empresa, ativo=True)
        }
        self.assertEqual(set(condicoes), {("DEB", "AV"), ("CRE", "30D"), ("CRE", "30-60"), ("CRE", "30-60-90")})
        self.assertEqual(FormaPagamentoCondicao.objects.filter(empresa=empresa, ativo=True).count(), 4)
        self.assertEqual(condicoes[("DEB", "AV")].taxa_percentual, Decimal("0.0000"))
        self.assertEqual(condicoes[("DEB", "AV")].taxa_fixa, Decimal("0.00"))
        self.assertEqual(condicoes[("CRE", "30D")].taxa_percentual, Decimal("2.0000"))
        self.assertEqual(condicoes[("CRE", "30D")].taxa_fixa, Decimal("0.00"))
        self.assertEqual(condicoes[("CRE", "30-60")].taxa_percentual, Decimal("2.5000"))
        self.assertEqual(condicoes[("CRE", "30-60")].taxa_fixa, Decimal("0.00"))
        self.assertEqual(condicoes[("CRE", "30-60-90")].taxa_percentual, Decimal("2.5000"))
        self.assertEqual(condicoes[("CRE", "30-60-90")].taxa_fixa, Decimal("0.00"))
        self.assertFalse(FormaPagamentoCondicao.objects.filter(empresa=empresa, forma_pagamento__codigo__in=["DIN", "PIX"], ativo=True).exists())
        self.assertFalse(FormaPagamentoCondicao.objects.filter(empresa=empresa, forma_pagamento__codigo="CRE", prazo_pagamento__codigo="30-60-90-120", ativo=True).exists())

    def test_create_idempotente_condicoes_pagamento_e_validate(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        empresa = Empresa.objects.get(documento="42000001000186")
        first = FormaPagamentoCondicao.objects.filter(empresa=empresa).count()

        call_command("sysvar_dev_base", "--create", verbosity=0)

        self.assertEqual(FormaPagamentoCondicao.objects.filter(empresa=empresa).count(), first)
        self.assertEqual(FormaPagamentoCondicao.objects.filter(empresa=empresa, ativo=True).count(), 4)
        report = SysvarDevBaseService().validate()
        self.assertTrue(report.valid, report.problems)

        condicao = FormaPagamentoCondicao.objects.get(empresa=empresa, forma_pagamento__codigo="CRE", prazo_pagamento__codigo="30D")
        condicao.taxa_percentual = Decimal("9.0000")
        condicao.save(update_fields=["taxa_percentual"])

        report = SysvarDevBaseService().validate()

        self.assertFalse(report.valid)
        self.assertIn("Taxas oficiais de FormaPagamentoCondicao", ", ".join(report.problems))

    def test_produtos_comerciais_possuem_fiscal_dev_completo(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        comerciais = Produto.objects.filter(tipo_produto="1")

        self.assertFalse(comerciais.filter(ncm__isnull=True).exists())
        self.assertFalse(comerciais.filter(ncm="").exists())
        self.assertFalse(
            comerciais.exclude(
                origem_mercadoria=0,
                csosn_ou_cst_icms="000",
                aliquota_icms=Decimal("18"),
                cfop_venda_dentro="5102",
                cfop_venda_fora="6102",
                cst_pis="01",
                aliq_pis=Decimal("1.65"),
                cst_cofins="01",
                aliq_cofins=Decimal("7.60"),
            ).exists()
        )

    def test_validate_detecta_saldo_inicial_incorreto(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        estoque = Estoque.objects.filter(Idloja__apelido_loja="Barra", CodigodeBarra__in=ProdutoDetalhe.objects.filter(produto__tipo_produto="1").values("ean13")).first()
        estoque.Estoque = Decimal("19")
        estoque.save(update_fields=["Estoque"])

        report = SysvarDevBaseService().validate()

        self.assertFalse(report.valid)
        self.assertIn("Estoque inicial comercial", ", ".join(report.problems))

    def test_validate_detecta_fiscal_comercial_incompleto(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        produto = Produto.objects.filter(tipo_produto="1").first()
        produto.cst_pis = None
        produto.save(update_fields=["cst_pis"])

        report = SysvarDevBaseService().validate()

        self.assertFalse(report.valid)
        self.assertIn("dados fiscais incompletos", ", ".join(report.problems))
