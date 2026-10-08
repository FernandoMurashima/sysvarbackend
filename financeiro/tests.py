from decimal import Decimal
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from cadastros.models import Cliente, Empresa, Funcionarios, Loja
from financeiro.models import (
    FormaPagamento,
    FormaPagamentoCondicao,
    PrazoPagamento,
    SequenciaDocumento,
    ValeTroca,
    ValeTrocaMovimento,
    ValeTrocaReserva,
)
from financeiro.serializers import FormaPagamentoCondicaoSerializer
from financeiro.services import (
    ValeTrocaErro,
    consultar_vale_troca_online,
    listar_vales_troca_online_cliente,
    reservar_documento_vale_troca,
)
from fiscal.models import VendaDevolucao, VendaPdv
from hub.models import SysvarHub


class FormaPagamentoCondicaoTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nome="Empresa Financeiro", documento="11222333000180")
        self.outra_empresa = Empresa.objects.create(nome="Outra Empresa Financeiro", documento="21222333000180")
        self.forma = FormaPagamento.objects.create(
            empresa=self.empresa,
            codigo="CRE",
            descricao="Cartao de credito",
            tipo=FormaPagamento.TIPO_CREDITO,
            permite_parcelamento=True,
        )
        self.prazo_1x = PrazoPagamento.objects.create(
            empresa=self.empresa,
            codigo="1X",
            descricao="1x",
            num_parcelas=1,
            intervalo_dias=30,
        )
        self.prazo_2x = PrazoPagamento.objects.create(
            empresa=self.empresa,
            codigo="2X",
            descricao="2x",
            num_parcelas=2,
            intervalo_dias=30,
        )
        self.prazo_outra_empresa = PrazoPagamento.objects.create(
            empresa=self.outra_empresa,
            codigo="1X",
            descricao="1x outra empresa",
            num_parcelas=1,
            intervalo_dias=30,
        )

    def test_cria_forma_pagamento_com_condicoes_validas(self):
        condicao = FormaPagamentoCondicao.objects.create(
            empresa=self.empresa,
            forma_pagamento=self.forma,
            prazo_pagamento=self.prazo_1x,
            taxa_percentual=Decimal("2.1000"),
            taxa_fixa=Decimal("0.50"),
        )

        self.assertEqual(condicao.forma_pagamento, self.forma)
        self.assertEqual(condicao.prazo_pagamento, self.prazo_1x)
        self.assertTrue(self.forma.permite_parcelamento)

    def test_serializer_impede_prazo_de_outra_empresa(self):
        serializer = FormaPagamentoCondicaoSerializer(data={
            "empresa": self.empresa.pk,
            "forma_pagamento": self.forma.pk,
            "prazo_pagamento": self.prazo_outra_empresa.pk,
            "taxa_percentual": "2.1000",
            "taxa_fixa": "0.00",
            "ativo": True,
        })

        self.assertFalse(serializer.is_valid())
        self.assertIn("prazo_pagamento", serializer.errors)

    def test_api_formas_retorna_condicoes_ativas(self):
        FormaPagamentoCondicao.objects.create(
            empresa=self.empresa,
            forma_pagamento=self.forma,
            prazo_pagamento=self.prazo_1x,
            taxa_percentual=Decimal("2.1000"),
            taxa_fixa=Decimal("0.00"),
            ativo=True,
        )
        FormaPagamentoCondicao.objects.create(
            empresa=self.empresa,
            forma_pagamento=self.forma,
            prazo_pagamento=self.prazo_2x,
            taxa_percentual=Decimal("2.2500"),
            taxa_fixa=Decimal("0.00"),
            ativo=False,
        )
        user = get_user_model().objects.create_superuser("financeiro-admin", "admin@test.local", "123")
        client = APIClient()
        client.force_authenticate(user)

        resp = client.get("/api/financeiro/formas/", {"empresa": self.empresa.pk, "codigo": "CRE"})

        self.assertEqual(resp.status_code, 200)
        payload = resp.data["results"] if isinstance(resp.data, dict) and "results" in resp.data else resp.data
        forma_payload = payload[0]
        self.assertTrue(forma_payload["permite_parcelamento"])
        self.assertEqual(len(forma_payload["condicoes_parcelamento"]), 1)
        condicao_payload = forma_payload["condicoes_parcelamento"][0]
        self.assertEqual(condicao_payload["prazo_codigo"], "1X")
        self.assertEqual(condicao_payload["prazo_num_parcelas"], 1)
        self.assertEqual(condicao_payload["taxa_percentual"], "2.1000")


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

    def _criar_vale(self, cliente, documento, *, empresa=None, loja=None, saldo="100.00", status=ValeTroca.STATUS_ABERTO, validade=None, legado=""):
        vendedor = Funcionarios.objects.create(empresa=empresa or self.empresa, nomefuncionario=f"Vendedor {documento}")
        venda = VendaPdv.objects.create(
            empresa=empresa or self.empresa,
            loja=loja or self.loja,
            cliente=cliente,
            vendedor=vendedor,
            documento=f"VD-{(loja or self.loja).pk}-{documento}",
            forma_pagamento="DINHEIRO",
            total=Decimal("100.00"),
            valor_recebido=Decimal("100.00"),
        )
        devolucao = VendaDevolucao.objects.create(
            empresa=empresa or self.empresa,
            venda=venda,
            loja=loja or self.loja,
            cliente=cliente,
            documento=f"DEV-{documento}",
            credito_cliente=Decimal("100.00"),
        )
        return ValeTroca.objects.create(
            empresa=empresa or self.empresa,
            cliente=cliente,
            loja=loja or self.loja,
            devolucao=devolucao,
            documento=documento,
            documento_legado=legado,
            valor_original=Decimal("100.00"),
            saldo=Decimal(saldo),
            status=status,
            validade=validade,
        )

    def test_lista_vales_online_filtra_por_cliente_empresa_status_validade_e_saldo(self):
        cliente = Cliente.objects.create(empresa=self.empresa, nome_cliente="Fernanda Oliveira Lima", documento="12345678901")
        outro_cliente = Cliente.objects.create(empresa=self.empresa, nome_cliente="Outro Cliente", documento="12345678902")
        cliente_outra_empresa = Cliente.objects.create(empresa=self.empresa_b, nome_cliente="Outra Empresa", documento="22345678901")
        hub = SysvarHub.objects.create(loja=self.loja)

        vale_um = self._criar_vale(cliente, "VT0000001", saldo="219.90", legado="VT-HUB-DEV-ANTIGO")
        vale_dois = self._criar_vale(cliente, "VT0000002", saldo="219.90")
        reserva = ValeTrocaReserva.objects.create(
            empresa=self.empresa,
            hub=hub,
            venda_uuid="33333333-3333-4333-8333-333333333333",
            operacao_uuid="44444444-4444-4444-8444-444444444444",
            vale=vale_dois,
            valor=Decimal("100.00"),
        )
        self._criar_vale(cliente, "VT0000003", status=ValeTroca.STATUS_USADO)
        self._criar_vale(cliente, "VT0000004", status=ValeTroca.STATUS_CANCELADO)
        self._criar_vale(cliente, "VT0000005", status=ValeTroca.STATUS_EXPIRADO)
        self._criar_vale(cliente, "VT0000006", validade=timezone.localdate() - timedelta(days=1))
        self._criar_vale(cliente, "VT0000007", saldo="0.00")
        self._criar_vale(outro_cliente, "VT0000008")
        self._criar_vale(cliente_outra_empresa, "VT0000001", empresa=self.empresa_b, loja=self.loja_outra)

        payload = listar_vales_troca_online_cliente(hub, cliente.pk)

        self.assertEqual([vale["documento"] for vale in payload], ["VT0000001", "VT0000002"])
        self.assertEqual(payload[0]["saldo_disponivel"], "219.90")
        self.assertEqual(payload[1]["saldo_reservado"], "100.00")
        self.assertEqual(payload[1]["saldo_disponivel"], "119.90")
        self.assertNotIn("VT-HUB-DEV-ANTIGO", str(payload))
