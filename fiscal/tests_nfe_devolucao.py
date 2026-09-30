from decimal import Decimal
from datetime import date

from django.test import TestCase

from cadastros.models import Cliente, Empresa, Funcionarios, Loja
from fiscal.models import NFCe, NFeDevolucao, NotaFiscalSaida, VendaDevolucao, VendaDevolucaoItem, VendaPdv, VendaPdvItem
from fiscal.services.nfe_devolucao import processar_nfe_devolucao, registrar_nfe_devolucao
from fiscal.services.nfe_sequence import reconciliar_proximo_numero_nfe_loja
from produto.models import ConfigEan, Cor, Estoque, Grade, Grupo, Produto, ProdutoDetalhe, Tamanho, Unidade


class NFeDevolucaoServiceTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nome="Empresa Fiscal", documento="11222333000181")
        self.loja_centro = Loja.objects.create(
            empresa=self.empresa,
            nome_loja="Loja Centro",
            apelido_loja="Centro",
            cnpj="11222333000181",
            estado="SP",
            serie_nfe=5,
            proximo_numero_nfe=100,
            ambiente_fiscal=Empresa.AMBIENTE_HOMOLOGACAO,
            emite_nfe=True,
        )
        self.loja_barra = Loja.objects.create(
            empresa=self.empresa,
            nome_loja="Loja Barra",
            apelido_loja="Barra",
            cnpj="11222333000262",
            estado="SP",
            serie_nfe=7,
            proximo_numero_nfe=30,
            ambiente_fiscal=Empresa.AMBIENTE_HOMOLOGACAO,
            emite_nfe=True,
        )
        self.cliente = Cliente.objects.create(empresa=self.empresa, nome_cliente="Fernanda Oliveira Lima", documento="11122233344")
        self.vendedor = Funcionarios.objects.create(empresa=self.empresa, nomefuncionario="Vendedor", idloja=self.loja_centro)

        unidade = Unidade.objects.create(empresa=self.empresa, Codigo="UN", Descricao="Unidade")
        grupo = Grupo.objects.create(empresa=self.empresa, Codigo="01", CodigoRef="01", Descricao="Grupo", Margem=Decimal("0"))
        grade = Grade.objects.create(empresa=self.empresa, Descricao="Grade")
        tamanho = Tamanho.objects.create(empresa=self.empresa, idgrade=grade, Tamanho="M")
        cor = Cor.objects.create(empresa=self.empresa, Descricao="Preto", Codigo="PT", Cor="Preto")
        ConfigEan.objects.create(empresa=self.empresa, company_prefix="1234")
        self.produto = Produto.objects.create(
            empresa=self.empresa,
            referencia="CALCA01",
            descricao="Calca Alfaiataria Capri",
            unidade=unidade,
            grupo=grupo,
            ncm="6203.42.00",
        )
        self.sku = ProdutoDetalhe.objects.create(produto=self.produto, idcor=cor, idtamanho=tamanho, custo_medio=Decimal("80.0000"))
        Estoque.objects.create(Idloja=self.loja_barra, CodigodeBarra=self.sku.ean13, referencia="CALCA01", Estoque=Decimal("1"))

        self.venda = VendaPdv.objects.create(
            empresa=self.empresa,
            loja=self.loja_centro,
            cliente=self.cliente,
            vendedor=self.vendedor,
            documento="3",
            forma_pagamento="DINHEIRO",
            subtotal=Decimal("439.80"),
            total=Decimal("439.80"),
        )
        self.venda_item = VendaPdvItem.objects.create(
            venda=self.venda,
            produto=self.produto,
            sku=self.sku,
            ean=self.sku.ean13,
            referencia="CALCA01",
            descricao="Calca Alfaiataria Capri",
            cor="Preto",
            tamanho="M",
            quantidade=2,
            preco_unitario=Decimal("229.9000"),
            desconto=Decimal("20.00"),
            custo_unitario=Decimal("80.0000"),
            ncm="62034200",
            cfop="1202",
        )
        self.nfce = NFCe.objects.create(
            venda=self.venda,
            loja=self.loja_centro,
            ambiente=Empresa.AMBIENTE_HOMOLOGACAO,
            serie=1,
            numero=3,
            status=NFCe.Status.AUTORIZADA,
            chave_acesso="35260911222333000181650010000000031000000037",
        )
        self.devolucao = VendaDevolucao.objects.create(
            empresa=self.empresa,
            venda=self.venda,
            loja=self.loja_barra,
            cliente=self.cliente,
            documento="DEV-3",
            subtotal=Decimal("219.90"),
            credito_cliente=Decimal("219.90"),
        )
        self.devolucao_item = VendaDevolucaoItem.objects.create(
            devolucao=self.devolucao,
            venda_item=self.venda_item,
            produto=self.produto,
            sku=self.sku,
            ean=self.sku.ean13,
            referencia="CALCA01",
            descricao="Calca Alfaiataria Capri",
            cor="Preto",
            tamanho="M",
            quantidade=1,
            preco_unitario=Decimal("229.9000"),
            desconto=Decimal("10.00"),
            custo_unitario=Decimal("80.0000"),
        )

    def _nova_devolucao(self, documento="DEV-4", loja=None):
        devolucao = VendaDevolucao.objects.create(
            empresa=self.empresa,
            venda=self.venda,
            loja=loja or self.loja_barra,
            cliente=self.cliente,
            documento=documento,
            subtotal=Decimal("219.90"),
            credito_cliente=Decimal("219.90"),
        )
        VendaDevolucaoItem.objects.create(
            devolucao=devolucao,
            venda_item=self.venda_item,
            produto=self.produto,
            sku=self.sku,
            ean=self.sku.ean13,
            descricao="Calca Alfaiataria Capri",
            quantidade=1,
            preco_unitario=Decimal("229.9000"),
            desconto=Decimal("10.00"),
            custo_unitario=Decimal("80.0000"),
        )
        return devolucao

    def test_registra_nfe_devolucao_da_loja_receptora_e_nfce_origem(self):
        nfe = registrar_nfe_devolucao(self.devolucao)

        self.assertEqual(NFeDevolucao.objects.count(), 1)
        self.assertEqual(nfe.loja, self.loja_barra)
        self.assertEqual(nfe.nfce_origem, self.nfce)
        self.assertEqual(nfe.ambiente, Empresa.AMBIENTE_HOMOLOGACAO)
        self.assertEqual(nfe.serie, 7)
        self.assertEqual(nfe.numero, 30)
        self.assertEqual(nfe.status, NFeDevolucao.Status.PENDENTE_TRANSMISSAO)
        self.assertIn(self.nfce.chave_acesso, nfe.xml)

    def test_documento_contem_somente_quantidade_e_valor_devolvidos(self):
        nfe = registrar_nfe_devolucao(self.devolucao)

        self.assertIn("<qCom>1</qCom>", nfe.xml)
        self.assertIn("<vUnCom>229.9000</vUnCom>", nfe.xml)
        self.assertIn("<vDesc>10.00</vDesc>", nfe.xml)
        self.assertIn("<vProd>219.90</vProd>", nfe.xml)
        self.assertNotIn("<qCom>2</qCom>", nfe.xml)

    def test_numero_vem_da_sequencia_da_loja_e_idempotencia_nao_duplica(self):
        primeiro = registrar_nfe_devolucao(self.devolucao)
        segundo = registrar_nfe_devolucao(self.devolucao)

        self.assertEqual(primeiro.pk, segundo.pk)
        self.assertEqual(NFeDevolucao.objects.count(), 1)
        self.loja_barra.refresh_from_db()
        self.assertEqual(self.loja_barra.proximo_numero_nfe, 31)

    def test_reconcilia_base_legada_com_nfe_devolucao_existente(self):
        self.loja_barra.serie_nfe = 7
        self.loja_barra.proximo_numero_nfe = 1
        self.loja_barra.save(update_fields=["serie_nfe", "proximo_numero_nfe"])
        NFeDevolucao.objects.create(
            devolucao=self.devolucao,
            loja=self.loja_barra,
            nfce_origem=self.nfce,
            ambiente=Empresa.AMBIENTE_HOMOLOGACAO,
            modelo="55",
            serie=7,
            numero=1,
            status=NFeDevolucao.Status.PENDENTE_TRANSMISSAO,
        )

        reconciliar_proximo_numero_nfe_loja(self.loja_barra)

        self.loja_barra.refresh_from_db()
        self.assertEqual(self.loja_barra.proximo_numero_nfe, 2)

    def test_reconciliacao_nao_reduz_sequencia(self):
        self.loja_barra.proximo_numero_nfe = 10
        self.loja_barra.save(update_fields=["proximo_numero_nfe"])
        NFeDevolucao.objects.create(
            devolucao=self.devolucao,
            loja=self.loja_barra,
            nfce_origem=self.nfce,
            ambiente=Empresa.AMBIENTE_HOMOLOGACAO,
            modelo="55",
            serie=7,
            numero=3,
            status=NFeDevolucao.Status.PENDENTE_TRANSMISSAO,
        )

        reconciliar_proximo_numero_nfe_loja(self.loja_barra)

        self.loja_barra.refresh_from_db()
        self.assertEqual(self.loja_barra.proximo_numero_nfe, 10)

    def test_nova_devolucao_apos_registro_legado_usa_proximo_numero(self):
        self.loja_barra.serie_nfe = 7
        self.loja_barra.proximo_numero_nfe = 1
        self.loja_barra.save(update_fields=["serie_nfe", "proximo_numero_nfe"])
        NFeDevolucao.objects.create(
            devolucao=self.devolucao,
            loja=self.loja_barra,
            nfce_origem=self.nfce,
            ambiente=Empresa.AMBIENTE_HOMOLOGACAO,
            modelo="55",
            serie=7,
            numero=1,
            status=NFeDevolucao.Status.PENDENTE_TRANSMISSAO,
        )

        nfe = registrar_nfe_devolucao(self._nova_devolucao("DEV-4"))

        self.assertEqual(nfe.numero, 2)
        self.assertEqual(NFeDevolucao.objects.filter(loja=self.loja_barra, serie=7, numero=2).count(), 1)

    def test_duas_devolucoes_sequenciais_nao_duplicam_numero(self):
        primeira = registrar_nfe_devolucao(self.devolucao)
        segunda = registrar_nfe_devolucao(self._nova_devolucao("DEV-4"))

        self.assertEqual(segunda.numero, primeira.numero + 1)
        self.assertEqual(NFeDevolucao.objects.filter(loja=self.loja_barra, serie=7).count(), 2)

    def test_duas_lojas_usam_sequencias_independentes(self):
        outra_devolucao = self._nova_devolucao("DEV-4", loja=self.loja_centro)

        nfe_barra = registrar_nfe_devolucao(self.devolucao)
        nfe_centro = registrar_nfe_devolucao(outra_devolucao)

        self.assertEqual((nfe_barra.loja_id, nfe_barra.serie, nfe_barra.numero), (self.loja_barra.id, 7, 30))
        self.assertEqual((nfe_centro.loja_id, nfe_centro.serie, nfe_centro.numero), (self.loja_centro.id, 5, 100))

    def test_outra_serie_nao_interfere_na_serie_atual(self):
        NFeDevolucao.objects.create(
            devolucao=self.devolucao,
            loja=self.loja_barra,
            nfce_origem=self.nfce,
            ambiente=Empresa.AMBIENTE_HOMOLOGACAO,
            modelo="55",
            serie=99,
            numero=999,
            status=NFeDevolucao.Status.PENDENTE_TRANSMISSAO,
        )

        nfe = registrar_nfe_devolucao(self._nova_devolucao("DEV-4"))

        self.assertEqual((nfe.serie, nfe.numero), (7, 30))

    def test_outro_ambiente_nao_gera_combinacao_duplicada_no_ambiente_atual(self):
        NFeDevolucao.objects.create(
            devolucao=self.devolucao,
            loja=self.loja_barra,
            nfce_origem=self.nfce,
            ambiente=Empresa.AMBIENTE_PRODUCAO,
            modelo="55",
            serie=7,
            numero=30,
            status=NFeDevolucao.Status.PENDENTE_TRANSMISSAO,
        )

        nfe = registrar_nfe_devolucao(self._nova_devolucao("DEV-4"))

        self.assertEqual((nfe.ambiente, nfe.serie, nfe.numero), (Empresa.AMBIENTE_HOMOLOGACAO, 7, 30))

    def test_nota_fiscal_saida_modelo_55_compartilha_sequencia_da_loja(self):
        self.loja_barra.proximo_numero_nfe = 1
        self.loja_barra.save(update_fields=["proximo_numero_nfe"])
        NotaFiscalSaida.objects.create(
            empresa=self.empresa,
            loja_origem=self.loja_barra,
            loja_destino=self.loja_centro,
            modelo="55",
            serie="7",
            numero="8",
            documento_origem="OP-1",
            dt_emissao=date.today(),
            dt_saida=date.today(),
        )

        nfe = registrar_nfe_devolucao(self.devolucao)

        self.assertEqual(nfe.numero, 9)
        self.loja_barra.refresh_from_db()
        self.assertEqual(self.loja_barra.proximo_numero_nfe, 10)

    def test_loja_sem_emissao_mantem_documento_em_erro_sem_autorizar(self):
        self.loja_barra.emite_nfe = False
        self.loja_barra.save(update_fields=["emite_nfe"])

        nfe = registrar_nfe_devolucao(self.devolucao)

        self.assertEqual(nfe.status, NFeDevolucao.Status.ERRO_GERACAO)
        self.assertEqual(nfe.retorno_codigo, "CONFIG")
        self.assertIn("nao esta habilitada", nfe.retorno_mensagem)
        self.assertFalse(nfe.protocolo)
        self.assertFalse(nfe.autorizada_em)

    def test_documento_autorizado_nao_e_reprocessado(self):
        nfe = registrar_nfe_devolucao(self.devolucao)
        NFeDevolucao.objects.filter(pk=nfe.pk).update(
            status=NFeDevolucao.Status.AUTORIZADA,
            retorno_mensagem="Autorizado real",
            protocolo="123",
        )

        reprocessado = processar_nfe_devolucao(nfe.pk)

        self.assertEqual(reprocessado.status, NFeDevolucao.Status.AUTORIZADA)
        self.assertEqual(reprocessado.retorno_mensagem, "Autorizado real")
        self.assertEqual(reprocessado.protocolo, "123")
