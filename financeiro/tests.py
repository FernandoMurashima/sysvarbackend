from decimal import Decimal

from django.test import TestCase

from cadastros.models import Cliente, Empresa, Funcionarios, Loja
from financeiro.models import SequenciaDocumento, ValeTroca, ValeTrocaMovimento, ValeTrocaReserva
from financeiro.services import (
    ValeTrocaErro,
    consultar_vale_troca_online,
    reservar_documento_vale_troca,
)
from fiscal.models import VendaDevolucao, VendaPdv
from hub.models import SysvarHub


class ValeTrocaSequenciaTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nome="Empresa VT", documento="11222333000181")
        self.empresa_b = Empresa.objects.create(nome="Empresa VT B", documento="21222333000181")
        self.loja = Loja.objects.create(empresa=self.empresa, nome_loja="Loja A", apelido_loja="A", cnpj="11222333000181", estado="SP")
        self.loja_b = Loja.objects.create(empresa=self.empresa, nome_loja="Loja B", apelido_loja="B", cnpj="11222333000182", estado="SP")
        self.loja_outra = Loja.objects.create(empresa=self.empresa_b, nome_loja="Loja C", apelido_loja="C", cnpj="21222333000181", estado="SP")

    def test_primeiro_segundo_e_formato_por_empresa(self):
        self.assertEqual(reservar_documento_vale_troca(self.empresa), "VT0000001")
        self.assertEqual(reservar_documento_vale_troca(self.empresa), "VT0000002")
        self.assertRegex(reservar_documento_vale_troca(self.empresa), r"^VT[0-9]{7}$")

    def test_duas_lojas_da_mesma_empresa_compartilham_sequencia(self):
        self.assertEqual(reservar_documento_vale_troca(self.loja.empresa), "VT0000001")
        self.assertEqual(reservar_documento_vale_troca(self.loja_b.empresa), "VT0000002")

    def test_empresas_diferentes_tem_sequencias_independentes(self):
        self.assertEqual(reservar_documento_vale_troca(self.empresa), "VT0000001")
        self.assertEqual(reservar_documento_vale_troca(self.empresa_b), "VT0000001")

    def test_limite_final_e_esgotamento(self):
        SequenciaDocumento.objects.create(
            empresa=self.empresa,
            tipo_documento=SequenciaDocumento.TIPO_VALE_TROCA,
            proximo_numero=9999999,
        )

        self.assertEqual(reservar_documento_vale_troca(self.empresa), "VT9999999")
        with self.assertRaisesMessage(ValeTrocaErro, "Faixa de numeracao de Vale-Troca esgotada."):
            reservar_documento_vale_troca(self.empresa)

    def test_consulta_documento_novo_e_legado_retorna_documento_comercial(self):
        cliente = Cliente.objects.create(empresa=self.empresa, nome_cliente="Maria VT", documento="12345678901")
        vendedor = Funcionarios.objects.create(empresa=self.empresa, nomefuncionario="Vendedor VT")
        venda = VendaPdv.objects.create(
            empresa=self.empresa,
            loja=self.loja,
            cliente=cliente,
            vendedor=vendedor,
            documento="VD-VT",
            forma_pagamento="DINHEIRO",
            total=Decimal("100.00"),
            valor_recebido=Decimal("100.00"),
        )
        devolucao = VendaDevolucao.objects.create(
            empresa=self.empresa,
            venda=venda,
            loja=self.loja,
            cliente=cliente,
            documento="DEV-VT",
            credito_cliente=Decimal("100.00"),
        )
        vale = ValeTroca.objects.create(
            empresa=self.empresa,
            cliente=cliente,
            loja=self.loja,
            devolucao=devolucao,
            documento="VT0000001",
            documento_legado="VT-HUB-DEV-2-ca1a6ced47464aac",
            valor_original=Decimal("100.00"),
            saldo=Decimal("75.00"),
        )
        hub = SysvarHub.objects.create(loja=self.loja)

        novo = consultar_vale_troca_online(hub, "vt0000001")
        legado = consultar_vale_troca_online(hub, "VT-HUB-DEV-2-ca1a6ced47464aac")

        self.assertEqual(novo["documento"], "VT0000001")
        self.assertEqual(legado["documento"], "VT0000001")
        self.assertEqual(ValeTroca.objects.get(pk=vale.pk).saldo, Decimal("75.00"))

    def test_reserva_e_movimento_preservam_vale_oficial(self):
        cliente = Cliente.objects.create(empresa=self.empresa, nome_cliente="Cliente Reserva", documento="12345678902")
        vendedor = Funcionarios.objects.create(empresa=self.empresa, nomefuncionario="Vendedor Reserva")
        venda = VendaPdv.objects.create(empresa=self.empresa, loja=self.loja, cliente=cliente, vendedor=vendedor, documento="VD-RES", forma_pagamento="DINHEIRO", total=Decimal("50.00"), valor_recebido=Decimal("50.00"))
        devolucao = VendaDevolucao.objects.create(empresa=self.empresa, venda=venda, loja=self.loja, cliente=cliente, documento="DEV-RES", credito_cliente=Decimal("50.00"))
        vale = ValeTroca.objects.create(empresa=self.empresa, cliente=cliente, loja=self.loja, devolucao=devolucao, documento="VT0000001", valor_original=Decimal("50.00"), saldo=Decimal("50.00"))
        hub = SysvarHub.objects.create(loja=self.loja)

        reserva = ValeTrocaReserva.objects.create(empresa=self.empresa, hub=hub, venda_uuid="11111111-1111-4111-8111-111111111111", operacao_uuid="22222222-2222-4222-8222-222222222222", vale=vale, valor=Decimal("25.00"))
        movimento = ValeTrocaMovimento.objects.create(vale=vale, venda_uso=venda, tipo=ValeTrocaMovimento.TIPO_USO, valor=Decimal("25.00"), saldo_apos=Decimal("25.00"))

        self.assertEqual(reserva.vale.documento, "VT0000001")
        self.assertEqual(movimento.vale.documento, "VT0000001")
