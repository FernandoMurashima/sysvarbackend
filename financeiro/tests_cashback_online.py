from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.db import close_old_connections, models
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from financeiro.models import (
    CashbackAutorizacao, CashbackConsumo, CashbackConsumoCredito, CashbackEstornoCredito,
    CashbackMovimento, CashbackReserva, CashbackReservaCredito, saldo_cashback_cliente,
)
from financeiro.services import (
    CashbackErro, cancelar_reserva_cashback, reservar_cashback_venda,
    saldo_disponivel_cashback_cliente, consumir_reservas_cashback_venda,
)
from financeiro.cashback_creditos import CashbackHistoricoPendente
from hub.models import HubEventoRecebido, HubVendaMapeamento
from hub.sync import payload_hash
from hub import tests as hub_tests
from fiscal.models import VendaPdv, VendaPdvPagamento


class CashbackOnlineFixture:
    _hub_autenticado = hub_tests.SysvarHubSyncPushApiTests._hub_autenticado
    _payload_venda = hub_tests.SysvarHubSyncPushApiTests._payload_venda
    _evento = hub_tests.SysvarHubSyncPushApiTests._evento
    _push = hub_tests.SysvarHubSyncPushApiTests._push
    _cashback_config = hub_tests.SysvarHubSyncPushApiTests._cashback_config
    _credito_cashback = hub_tests.SysvarHubSyncPushApiTests._credito_cashback

    def setUp(self):
        super().setUp()
        hub_tests.SysvarHubSyncPushApiTests.setUp(self)
        self._hub_autenticado()
        self.config = self._cashback_config()
        self.config.percentual = Decimal("0.0000")
        self.config.save()
        self.credito = self._credito_cashback()
        self.venda_uuid = uuid4()
        self.operacao = uuid4()

    def reservar(self, valor="30.00", **extras):
        dados = dict(hub=self.hub, venda_uuid=self.venda_uuid, operacao_uuid=self.operacao,
                     cliente_id=self.cliente.pk, valor=valor, total_venda="100.00")
        dados.update(extras)
        return reservar_cashback_venda(**dados)

    def evento_reservado(self, valor="30.00"):
        reserva = self.reservar(valor)
        payload = self._payload_venda(venda_uuid=str(self.venda_uuid), total="100.00", valor_recebido="100.00", pagamentos=[
            {"codigo": "CBK", "tipo": "CASHBACK", "valor": valor, "operacao_uuid": str(self.operacao), "cashback_reserva_id": reserva["id"]},
            {"codigo": "DINHEIRO", "valor": str(Decimal("100.00") - Decimal(valor))},
        ])
        payload["itens"][0]["preco_unitario"] = "100.00"
        return self._evento("VENDA_FINALIZADA", payload, evento_uuid=str(uuid4()), chave=str(uuid4()))

    def saldo(self):
        return saldo_disponivel_cashback_cliente(self.cliente.pk, self.empresa)


@override_settings(CASHBACK_CENTRAL_FEFO_ATIVO=True)
class CashbackOnlineTests(CashbackOnlineFixture, TestCase):
    def test_cashback_reserva_fefo_multiplos_creditos_e_cancelamento(self):
        self.credito.validade = timezone.localdate() + timedelta(days=30)
        self.credito.save()
        proximo = self._credito_cashback("20.00")
        proximo.validade = timezone.localdate() + timedelta(days=1)
        proximo.save()
        reserva = self.reservar("50.00")
        self.assertEqual(list(CashbackReservaCredito.objects.order_by("pk").values_list("credito_id", "valor")),
                         [(proximo.pk, Decimal("20.00")), (self.credito.pk, Decimal("30.00"))])
        self.assertEqual(self.saldo(), Decimal("70.00"))
        cancelar_reserva_cashback(self.hub, reserva_id=reserva["id"], venda_uuid=self.venda_uuid, operacao_uuid=self.operacao)
        self.assertEqual(self.saldo(), Decimal("120.00"))
        self.assertEqual(CashbackReservaCredito.objects.count(), 2)

    def test_cashback_contexto_obrigatorio_e_limite_percentual_acumulado(self):
        self.config.limite_uso_percentual = Decimal("40.00")
        self.config.valor_minimo_uso = Decimal("10.00")
        self.config.save()
        for dados in ({"total_venda": None}, {"total_venda": "0"}, {"valor": "9.99"}, {"valor": "40.01"}):
            with self.subTest(dados=dados), self.assertRaises(CashbackErro):
                self.reservar(**dados)
        self.reservar("30.00")
        with self.assertRaises(CashbackErro):
            self.reservar("20.00", operacao_uuid=uuid4())
        self.reservar("10.00", operacao_uuid=uuid4())
        self.assertEqual(CashbackReserva.objects.count(), 2)
        self.assertEqual(self.saldo(), Decimal("60.00"))
        with self.assertRaises(CashbackErro):
            self.reservar("30.00", total_venda="200.00")
        self.assertEqual(CashbackAutorizacao.objects.count(), 2)

    def test_cashback_valores_invalidos_nao_geram_reservas(self):
        for campo in ("valor", "total_venda"):
            for valor in ("NaN", "Infinity", "1.001", "-1", True, "10000000000000000"):
                with self.subTest(campo=campo, valor=valor), self.assertRaises(CashbackErro):
                    self.reservar(**{campo: valor})
        self.assertFalse(CashbackReserva.objects.exists())
        self.assertFalse(CashbackAutorizacao.objects.exists())

    def test_cashback_retry_reserva_e_consumo_unicos(self):
        evento = self.evento_reservado("80.00")
        reserva = self.reservar("80.00")
        primeira = self._push([evento])
        retry = self._push([evento])
        self.assertEqual(primeira.data["resultados"][0]["status"], HubEventoRecebido.STATUS_PROCESSADO, primeira.data)
        self.assertEqual(retry.data["resultados"][0]["status"], HubEventoRecebido.STATUS_DUPLICADO)
        venda = HubVendaMapeamento.objects.get(hub=self.hub, venda_uuid=self.venda_uuid).venda
        consumir_reservas_cashback_venda(self.hub, self.venda_uuid, venda, evento["payload"]["pagamentos"])
        self.assertEqual(CashbackConsumo.objects.count(), 1)
        self.assertEqual(CashbackAutorizacao.objects.count(), 1)
        self.assertEqual(CashbackMovimento.objects.filter(tipo=CashbackMovimento.TIPO_DEBITO).count(), 1)
        self.assertEqual(self.saldo(), Decimal("20.00"))
        with self.assertRaises(CashbackErro):
            cancelar_reserva_cashback(self.hub, reserva_id=reserva["id"])

    def test_cashback_expiracao_nao_debita_credito_posterior(self):
        hoje = timezone.localdate()
        self.credito.validade = hoje
        self.credito.save()
        evento = self.evento_reservado("80.00")
        resultado = self._push([evento])
        self.assertEqual(resultado.data["resultados"][0]["status"], HubEventoRecebido.STATUS_PROCESSADO, resultado.data)
        with patch("django.utils.timezone.localdate", return_value=hoje + timedelta(days=1)):
            self._credito_cashback("50.00")
            self.assertEqual(saldo_cashback_cliente(self.cliente.pk, empresa=self.empresa), Decimal("50.00"))
            self.assertEqual(self.saldo(), Decimal("50.00"))
            retry = self._push([evento])
            self.assertEqual(retry.data["resultados"][0]["status"], HubEventoRecebido.STATUS_DUPLICADO)

    def test_cashback_reserva_expirada_nao_prende_novo_credito_nem_e_consumida(self):
        hoje = timezone.localdate()
        self.credito.validade = hoje
        self.credito.save()
        evento = self.evento_reservado("80.00")
        with patch("django.utils.timezone.localdate", return_value=hoje + timedelta(days=1)):
            self._credito_cashback("50.00")
            self.assertEqual(self.saldo(), Decimal("50.00"))
            resultado = self._push([evento])
            self.assertEqual(resultado.data["resultados"][0]["status"], HubEventoRecebido.STATUS_ERRO, resultado.data)
        self.assertFalse(CashbackConsumo.objects.exists())
        self.assertFalse(VendaPdv.objects.exists())
        self.assertEqual(CashbackReserva.objects.get().status, CashbackReserva.STATUS_RESERVADA)

    def test_cashback_sem_ativacao_nao_usa_tabelas_novas(self):
        with override_settings(CASHBACK_CENTRAL_FEFO_ATIVO=False), patch("financeiro.cashback_online.MigrationRecorder") as recorder:
            with self.assertRaisesMessage(CashbackErro, "ativação explícita"):
                self.reservar()
            recorder.assert_not_called()
        self.assertFalse(CashbackReserva.objects.exists())

    def test_cashback_ativacao_sem_migrations_falha_fechada(self):
        with patch("financeiro.cashback_online.MigrationRecorder") as recorder:
            recorder.return_value.applied_migrations.return_value = {}
            with self.assertRaisesMessage(CashbackErro, "migrations 0038 e 0039"):
                self.reservar()
        self.assertFalse(CashbackReserva.objects.exists())

    def test_cashback_api_antiga_rejeita_sem_contexto_sem_mudar_endpoint(self):
        payload = {"venda_uuid": str(self.venda_uuid), "operacao_uuid": str(self.operacao), "cliente_id": self.cliente.pk, "valor": "30.00"}
        antiga = self.client.post("/api/hub/cashback/reservar-venda/", payload, format="json")
        self.assertEqual(antiga.status_code, 409)
        self.assertIn("total_venda", antiga.data["detail"])
        nova = self.client.post("/api/hub/cashback/reservar-venda/", {**payload, "total_venda": "100.00"}, format="json")
        self.assertEqual(nova.status_code, 200, nova.data)

    def test_cashback_legacy_desconhecido_rejeitado_mesmo_com_data_antiga(self):
        payload = self._payload_venda(pagamentos=[{"codigo": "CASHBACK", "tipo": "CASHBACK", "valor": "10.00", "operacao_uuid": str(uuid4()), "origem": "LOCAL"}], finalizado_em="2020-01-01T10:00:00-03:00")
        evento = self._evento("VENDA_FINALIZADA", payload)
        for _ in range(2):
            resultado = self._push([evento])
            self.assertEqual(resultado.data["resultados"][0]["status"], HubEventoRecebido.STATUS_ERRO, resultado.data)
        self.assertFalse(CashbackConsumo.objects.exists())
        self.assertFalse(VendaPdv.objects.exists())
        self.assertEqual(CashbackMovimento.objects.count(), 1)

    def test_cashback_legacy_processado_na_central_preserva_retry_e_historico(self):
        venda = VendaPdv.objects.create(empresa=self.empresa, loja=self.loja, cliente=self.cliente, vendedor=self.vendedor, caixa=self.caixa, documento="VE-HISTORICO", forma_pagamento="CASHBACK", total="10.00")
        debito = CashbackMovimento.objects.create(empresa=self.empresa, cliente=self.cliente, venda_uso=venda, tipo=CashbackMovimento.TIPO_DEBITO, valor="10.00")
        payload = self._payload_venda(venda_uuid=str(self.venda_uuid), pagamentos=[{"tipo": "CASHBACK", "valor": "10.00", "origem": "LOCAL"}])
        evento = self._evento("VENDA_FINALIZADA", payload)
        HubVendaMapeamento.objects.create(hub=self.hub, venda_uuid=self.venda_uuid, venda=venda, documento=venda.documento)
        HubEventoRecebido.objects.create(hub=self.hub, evento_uuid=evento["evento_uuid"], chave_idempotencia=evento["chave_idempotencia"], tipo=evento["tipo"], payload=payload, payload_hash=payload_hash(payload), status=HubEventoRecebido.STATUS_PROCESSADO)
        antes = list(CashbackMovimento.objects.values())
        with override_settings(CASHBACK_CENTRAL_FEFO_ATIVO=False):
            resultado = self._push([evento])
        self.assertEqual(resultado.data["resultados"][0]["status"], HubEventoRecebido.STATUS_DUPLICADO, resultado.data)
        self.assertEqual(list(CashbackMovimento.objects.values()), antes)
        self.assertEqual(CashbackMovimento.objects.get(pk=debito.pk).tipo, CashbackMovimento.TIPO_DEBITO)
        from copy import deepcopy
        alterado = deepcopy(evento)
        alterado["evento_uuid"] = str(uuid4())
        alterado["chave_idempotencia"] = str(uuid4())
        alterado["payload"]["pagamentos"][0]["valor"] = "20.00"
        conflito = self._push([alterado])
        self.assertEqual(conflito.data["resultados"][0]["status"], HubEventoRecebido.STATUS_ERRO, conflito.data)
        self.assertIn("histórico processado", conflito.data["resultados"][0]["mensagem"])
        self.assertEqual(list(CashbackMovimento.objects.values()), antes)
        with self.assertRaisesMessage(CashbackErro, "já sincronizada"):
            self.reservar()
        with self.assertRaises(CashbackHistoricoPendente):
            self.reservar(venda_uuid=uuid4())
        self.assertFalse(CashbackConsumo.objects.exists())

    def test_cashback_reserva_antiga_pode_reautorizar_online_sem_alocar_debito_historico(self):
        antiga = CashbackReserva.objects.create(empresa=self.empresa, hub=self.hub, cliente=self.cliente, venda_uuid=self.venda_uuid, operacao_uuid=self.operacao, valor="30.00")
        with self.assertRaises(CashbackHistoricoPendente):
            self.saldo()
        reserva = self.reservar()
        self.assertEqual(reserva["id"], antiga.pk)
        self.assertEqual(CashbackReservaCredito.objects.get().valor, Decimal("30.00"))
        self.assertFalse(CashbackConsumo.objects.exists())

    def test_cashback_falha_alocacao_reverte_reserva_e_consumo(self):
        with patch("financeiro.cashback_online.CashbackReservaCredito.objects.bulk_create", side_effect=RuntimeError("falha")):
            with self.assertRaises(RuntimeError):
                self.reservar()
        self.assertFalse(CashbackAutorizacao.objects.exists())
        self.assertFalse(CashbackReserva.objects.exists())
        evento = self.evento_reservado()
        with patch("financeiro.cashback_online.CashbackConsumoCredito.objects.bulk_create", side_effect=RuntimeError("falha")):
            resultado = self._push([evento])
        self.assertEqual(resultado.data["resultados"][0]["status"], HubEventoRecebido.STATUS_ERRO, resultado.data)
        self.assertFalse(VendaPdv.objects.exists())
        self.assertFalse(CashbackConsumo.objects.exists())
        self.assertFalse(CashbackMovimento.objects.filter(tipo=CashbackMovimento.TIPO_DEBITO).exists())
        self.assertEqual(CashbackReserva.objects.get().status, CashbackReserva.STATUS_RESERVADA)
        sucesso = self._push([evento])
        self.assertEqual(sucesso.data["resultados"][0]["status"], HubEventoRecebido.STATUS_PROCESSADO, sucesso.data)

    def test_cashback_total_adulterado_na_sync_reverte_integralmente(self):
        evento = self.evento_reservado()
        evento["payload"]["total"] = "90.00"
        evento["payload"]["valor_recebido"] = "90.00"
        evento["payload"]["itens"][0]["preco_unitario"] = "90.00"
        evento["payload"]["pagamentos"][1]["valor"] = "60.00"
        resultado = self._push([evento])
        self.assertEqual(resultado.data["resultados"][0]["status"], HubEventoRecebido.STATUS_ERRO, resultado.data)
        self.assertIn("total", resultado.data["resultados"][0]["mensagem"])
        self.assertFalse(VendaPdv.objects.exists())
        self.assertFalse(VendaPdvPagamento.objects.exists())
        self.assertFalse(CashbackConsumo.objects.exists())
        self.assertEqual(CashbackReserva.objects.get().status, CashbackReserva.STATUS_RESERVADA)

    def test_cashback_consumo_com_identidade_divergente_rejeitado(self):
        evento = self.evento_reservado()
        for campo, valor in (("venda_uuid", str(uuid4())), ("cliente_retaguarda_id", 999999), ("operacao_uuid", str(uuid4()))):
            from copy import deepcopy
            alterado = deepcopy(evento)
            alterado["evento_uuid"] = str(uuid4())
            alterado["chave_idempotencia"] = str(uuid4())
            if campo == "operacao_uuid":
                alterado["payload"]["pagamentos"][0][campo] = valor
            else:
                alterado["payload"][campo] = valor
            with self.subTest(campo=campo):
                resultado = self._push([alterado])
                self.assertEqual(resultado.data["resultados"][0]["status"], HubEventoRecebido.STATUS_ERRO, resultado.data)
                self.assertFalse(VendaPdv.objects.exists())
                self.assertFalse(CashbackConsumo.objects.exists())
                self.assertEqual(CashbackReserva.objects.get().status, CashbackReserva.STATUS_RESERVADA)

    def test_cashback_restituicao_comprovada_mantem_origem_e_validade(self):
        from financeiro.cashback_creditos import vincular_restituicao_venda
        hoje = timezone.localdate()
        self.credito.validade = hoje
        self.credito.save()
        evento = self.evento_reservado("80.00")
        resultado = self._push([evento])
        self.assertEqual(resultado.data["resultados"][0]["status"], HubEventoRecebido.STATUS_PROCESSADO, resultado.data)
        venda = HubVendaMapeamento.objects.get(hub=self.hub, venda_uuid=self.venda_uuid).venda
        estorno = CashbackMovimento.objects.create(empresa=self.empresa, cliente=self.cliente, tipo=CashbackMovimento.TIPO_ESTORNO, valor="20.00")
        vincular_restituicao_venda(estorno, venda)
        vincular_restituicao_venda(estorno, venda)
        self.assertEqual(CashbackEstornoCredito.objects.count(), 1)
        self.assertEqual(CashbackEstornoCredito.objects.get().consumo_credito.credito_id, self.credito.pk)
        self.assertEqual(self.saldo(), Decimal("40.00"))
        with patch("django.utils.timezone.localdate", return_value=hoje + timedelta(days=1)):
            self._credito_cashback("50.00")
            self.assertEqual(self.saldo(), Decimal("50.00"))

    def test_cashback_geracao_offline_funciona_com_utilizacao_desativada(self):
        self.config.percentual = Decimal("10.0000")
        self.config.save()
        payload = self._payload_venda()
        evento = self._evento("VENDA_FINALIZADA", payload)
        with override_settings(CASHBACK_CENTRAL_FEFO_ATIVO=False):
            resultado = self._push([evento])
        self.assertEqual(resultado.data["resultados"][0]["status"], HubEventoRecebido.STATUS_PROCESSADO, resultado.data)
        self.assertEqual(CashbackMovimento.objects.filter(venda_origem__isnull=False).get().valor, Decimal("1.00"))
        self.assertFalse(CashbackReserva.objects.exists())


@override_settings(CASHBACK_CENTRAL_FEFO_ATIVO=True)
class CashbackOnlineConcorrenciaTests(CashbackOnlineFixture, TransactionTestCase):
    def paralelas(self, operacoes):
        barreira = Barrier(len(operacoes))
        def executar(operacao):
            close_old_connections()
            try:
                barreira.wait(timeout=20)
                return self.reservar("80.00", operacao_uuid=operacao)
            except CashbackErro:
                return None
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(operacoes)) as pool:
            return list(pool.map(executar, operacoes))

    def test_cashback_reservas_concorrentes_nao_duplicam_saldo(self):
        resultados = self.paralelas([uuid4(), uuid4()])
        self.assertEqual(sum(r is not None for r in resultados), 1)
        self.assertEqual(CashbackReserva.objects.count(), 1)
        self.assertEqual(CashbackReservaCredito.objects.aggregate(total=models.Sum("valor"))["total"], Decimal("80.00"))
        self.assertEqual(self.saldo(), Decimal("20.00"))

    def test_cashback_retry_concorrente_retoma_mesma_reserva(self):
        resultados = self.paralelas([self.operacao, self.operacao])
        self.assertTrue(all(resultados))
        self.assertEqual(resultados[0]["id"], resultados[1]["id"])
        self.assertEqual(CashbackReserva.objects.count(), 1)
        self.assertEqual(CashbackAutorizacao.objects.count(), 1)
        self.assertEqual(self.saldo(), Decimal("20.00"))

    def test_cashback_consumo_concorrente_da_mesma_reserva_nao_duplica(self):
        evento = self.evento_reservado("80.00")
        venda = VendaPdv.objects.create(empresa=self.empresa, loja=self.loja, caixa=self.caixa,
            cliente=self.cliente, vendedor=self.vendedor, documento="VE-CBK-CONCORRENTE",
            forma_pagamento="CASHBACK", total=Decimal("100.00"))
        venda_id = venda.pk
        barreira = Barrier(2)
        def repetir(_):
            close_old_connections()
            try:
                venda = VendaPdv.objects.get(pk=venda_id)
                barreira.wait(timeout=20)
                consumir_reservas_cashback_venda(self.hub, self.venda_uuid, venda, evento["payload"]["pagamentos"])
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(repetir, range(2)))
        self.assertEqual(CashbackConsumo.objects.count(), 1)
        self.assertEqual(CashbackConsumoCredito.objects.count(), 1)
        self.assertEqual(CashbackMovimento.objects.filter(tipo=CashbackMovimento.TIPO_DEBITO).count(), 1)
        self.assertEqual(self.saldo(), Decimal("20.00"))
