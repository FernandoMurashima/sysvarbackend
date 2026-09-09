from decimal import Decimal

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
from financeiro.models import CashbackConfig, ConfigFinanceira, MovimentacaoFinanceira, Pagar, Receber
from fiscal.models.nota_fiscal_entrada import NotaFiscalEntrada, RecebimentoMercadoriaConferenciaItem, RecebimentoMercadoriaEfetivacaoEstoque, RecebimentoMercadoriaEstoque, RecebimentoMercadoriaPedido, RecebimentoMercadoriaTermo, XmlFornecedorRecebido
from fiscal.models.nota_fiscal_saida import NotaFiscalSaida
from fiscal.models.venda_pdv import VendaPdv
from produto.models import ConfigEan, Estoque, EstoqueMovimentacao, FichaTecnica, FichaTecnicaItem, Produto, ProdutoDetalhe, ProdutoFornecedor, ProdutoUsoConsumoEstoque, ProdutoUsoConsumoMovimentacao, Promocao
from sysvar_devtools.dev_base import SysvarDevBaseService


class SysvarDevBaseTests(TransactionTestCase):
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

    def test_reset_idempotente_e_sem_operacional(self):
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        first = SysvarDevBaseService().validate().created
        call_command("sysvar_dev_base", "--reset", verbosity=0)
        second = SysvarDevBaseService().validate().created
        self.assertEqual(first, second)
        self.assertEqual(ConfigFinanceira.objects.count(), 1)
        self.assertEqual(CashbackConfig.objects.count(), 1)
        for model in [EstoqueMovimentacao, ProdutoUsoConsumoMovimentacao, Requisicao, Cotacao, PedidoCompra, Distribuicao, MercadoriaTransito, MovimentacaoFinanceira, Pagar, Receber, XmlFornecedorRecebido, RecebimentoMercadoriaEstoque, RecebimentoMercadoriaPedido, RecebimentoMercadoriaConferenciaItem, RecebimentoMercadoriaTermo, RecebimentoMercadoriaEfetivacaoEstoque, NotaFiscalEntrada, NotaFiscalSaida, VendaPdv]:
            self.assertFalse(model.objects.exists(), model.__name__)

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
        self.assertEqual(Estoque.objects.exclude(Estoque=0).count(), 0)
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
