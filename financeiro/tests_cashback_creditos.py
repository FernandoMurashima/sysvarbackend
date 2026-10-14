import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.db import IntegrityError, close_old_connections, connection, models, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from cadastros.models import Cliente, Empresa, Loja
from financeiro.cashback_creditos import (
    CashbackHistoricoPendente, consumir_cashback_fefo, saldo_cashback_alocado,
    vincular_estorno_cashback,
)
from financeiro.models import (
    CashbackConfig, CashbackConsumo, CashbackConsumoCredito, CashbackEstornoCredito,
    CashbackMovimento, CashbackReserva, saldo_cashback_cliente,
)
from financeiro.services import CashbackErro, reservar_cashback_venda
from hub.models import SysvarHub


class CashbackCreditoMixin:
    def setUp(self):
        super().setUp()
        self.empresa = Empresa.objects.create(nome="Cashback FEFO", documento="31222333000180")
        self.cliente = Cliente.objects.create(empresa=self.empresa, nome_cliente="Cliente FEFO",
                                              tipo_pessoa="PF", documento="39053344705", cpf="39053344705")
        self.hoje = timezone.localdate()

    def credito(self, valor, dias=None):
        return CashbackMovimento.objects.create(
            empresa=self.empresa, cliente=self.cliente, tipo=CashbackMovimento.TIPO_CREDITO,
            valor=Decimal(valor), validade=self.hoje + timedelta(days=dias) if dias is not None else None,
        )

    def consumir(self, valor, operacao=None, **extras):
        return consumir_cashback_fefo(empresa=self.empresa, cliente=self.cliente,
                                     operacao_uuid=operacao or uuid.uuid4(), valor=valor, **extras)

    def saldo(self):
        return saldo_cashback_alocado(self.cliente, self.empresa)

    def estorno(self, valor, parcelas):
        movimento = CashbackMovimento.objects.create(
            empresa=self.empresa, cliente=self.cliente, tipo=CashbackMovimento.TIPO_ESTORNO, valor=Decimal(valor),
        )
        vincular_estorno_cashback(empresa=self.empresa, estorno=movimento, parcelas=parcelas)
        return movimento


class CashbackCreditoFefoTests(CashbackCreditoMixin, TestCase):
    def test_fefo_vencimento_antes_da_ordem_de_criacao_e_sem_validade_por_ultimo(self):
        permanente = self.credito("100.00")
        longe = self.credito("50.00", 30)
        proximo = self.credito("30.00", 2)
        self.credito("500.00", -1)
        consumo = self.consumir("60.00")
        self.assertEqual(list(consumo.alocacoes.order_by("pk").values_list("credito_id", "valor")),
                         [(proximo.pk, Decimal("30.00")), (longe.pk, Decimal("30.00"))])
        self.assertFalse(consumo.alocacoes.filter(credito=permanente).exists())
        self.assertEqual(self.saldo(), Decimal("120.00"))

    def test_consumo_parcial_de_varios_creditos_preserva_remanescente(self):
        primeiro = self.credito("30.00", 1)
        segundo = self.credito("40.00", 2)
        terceiro = self.credito("50.00", 3)
        consumo = self.consumir("85.00")
        self.assertEqual(dict(consumo.alocacoes.values_list("credito_id", "valor")),
                         {primeiro.pk: Decimal("30.00"), segundo.pk: Decimal("40.00"), terceiro.pk: Decimal("15.00")})
        self.assertEqual(self.saldo(), Decimal("35.00"))

    def test_credito_valido_hoje_e_aceito_e_expirado_nao_e_usado(self):
        expirado = self.credito("100.00", -1)
        hoje = self.credito("20.00", 0)
        self.assertEqual(self.saldo(), Decimal("20.00"))
        consumo = self.consumir("20.00")
        self.assertEqual(consumo.alocacoes.get().credito_id, hoje.pk)
        self.assertFalse(expirado.alocacoes_consumo.exists())
        self.assertEqual(self.saldo(), Decimal("0.00"))

    def test_expiracao_de_remanescente_nao_desconta_debito_antigo_em_novo_credito(self):
        credito = self.credito("100.00", 0)
        operacao = uuid.uuid4()
        consumo = self.consumir("80.00", operacao)
        self.assertEqual(self.saldo(), Decimal("20.00"))
        with patch("financeiro.cashback_creditos.timezone.localdate", return_value=self.hoje + timedelta(days=1)):
            self.assertEqual(self.saldo(), Decimal("0.00"))
            novo = self.credito("50.00", 30)
            self.assertEqual(self.saldo(), Decimal("50.00"))
            self.assertEqual(self.consumir("80.00", operacao).pk, consumo.pk)
        credito.refresh_from_db()
        consumo.movimento.refresh_from_db()
        self.assertEqual(credito.valor, Decimal("100.00"))
        self.assertEqual(consumo.movimento.valor, Decimal("80.00"))
        self.assertFalse(novo.alocacoes_consumo.exists())
        self.assertFalse(CashbackMovimento.objects.filter(tipo=CashbackMovimento.TIPO_EXPIRACAO).exists())

    def test_empate_de_vencimento_tem_ordem_estavel(self):
        primeiro = self.credito("30.00", 10)
        segundo = self.credito("30.00", 10)
        consumo = self.consumir("40.00")
        self.assertEqual(dict(consumo.alocacoes.values_list("credito_id", "valor")),
                         {primeiro.pk: Decimal("30.00"), segundo.pk: Decimal("10.00")})

    def test_retry_nao_cria_debitos_ou_alocacoes_duplicadas(self):
        self.credito("100.00")
        operacao = uuid.uuid4()
        primeiro = self.consumir("80.00", operacao)
        segundo = self.consumir("80.00", operacao)
        self.assertEqual(primeiro.pk, segundo.pk)
        self.assertEqual(CashbackConsumo.objects.count(), 1)
        self.assertEqual(CashbackConsumoCredito.objects.count(), 1)
        self.assertEqual(CashbackMovimento.objects.filter(tipo=CashbackMovimento.TIPO_DEBITO).count(), 1)
        self.assertEqual(self.saldo(), Decimal("20.00"))
        with self.assertRaises(CashbackErro):
            self.consumir("81.00", operacao)
        self.assertEqual(self.saldo(), Decimal("20.00"))

    def test_saldo_insuficiente_e_falha_de_gravacao_nao_deixam_consumo_parcial(self):
        self.credito("30.00")
        with self.assertRaises(CashbackErro):
            self.consumir("31.00")
        with patch("financeiro.cashback_creditos.CashbackConsumoCredito.objects.bulk_create", side_effect=RuntimeError("falha")):
            with self.assertRaises(RuntimeError):
                self.consumir("20.00")
        self.assertFalse(CashbackConsumo.objects.exists())
        self.assertFalse(CashbackConsumoCredito.objects.exists())
        self.assertFalse(CashbackMovimento.objects.filter(tipo=CashbackMovimento.TIPO_DEBITO).exists())
        self.assertEqual(self.saldo(), Decimal("30.00"))

    def test_valores_e_operacao_invalidos_nao_geram_movimentos(self):
        self.credito("100.00")
        for valor in ("0", "-1", "NaN", "Infinity", "1.001", True, "10000000000000000"):
            with self.subTest(valor=valor), self.assertRaises(CashbackErro):
                self.consumir(valor)
        with self.assertRaises(CashbackErro):
            self.consumir("1.00", "operacao-invalida")
        self.assertFalse(CashbackConsumo.objects.exists())

    def test_cliente_de_outra_empresa_e_reutilizacao_em_outro_cliente_bloqueados(self):
        outra = Empresa.objects.create(nome="Outra FEFO", documento="41222333000180")
        self.credito("100.00")
        operacao = uuid.uuid4()
        self.consumir("30.00", operacao)
        with self.assertRaises(CashbackErro):
            consumir_cashback_fefo(empresa=outra, cliente=self.cliente, operacao_uuid=uuid.uuid4(), valor="10.00")
        cliente = Cliente.objects.create(empresa=self.empresa, nome_cliente="Outro FEFO", tipo_pessoa="PF", documento="52998224725")
        with self.assertRaises(CashbackErro):
            consumir_cashback_fefo(empresa=self.empresa, cliente=cliente, operacao_uuid=operacao, valor="30.00")
        self.assertEqual(CashbackConsumo.objects.count(), 1)
        self.assertEqual(self.saldo(), Decimal("70.00"))

    def test_historico_sem_origem_nao_e_reconciliado_automaticamente(self):
        self.credito("100.00")
        for tipo in (CashbackMovimento.TIPO_DEBITO, CashbackMovimento.TIPO_ESTORNO, CashbackMovimento.TIPO_EXPIRACAO):
            with self.subTest(tipo=tipo), transaction.atomic():
                legado = CashbackMovimento.objects.create(empresa=self.empresa, cliente=self.cliente, tipo=tipo, valor=Decimal("20.00"))
                antes = list(CashbackMovimento.objects.order_by("pk").values())
                saldo_legado = saldo_cashback_cliente(self.cliente.pk, empresa=self.empresa)
                with self.assertRaises(CashbackHistoricoPendente):
                    self.consumir("10.00")
                with self.assertRaises(CashbackHistoricoPendente):
                    self.saldo()
                self.assertEqual(list(CashbackMovimento.objects.order_by("pk").values()), antes)
                self.assertEqual(saldo_cashback_cliente(self.cliente.pk, empresa=self.empresa), saldo_legado)
                self.assertFalse(CashbackConsumo.objects.exists())
                legado.delete()

    def test_movimento_historico_negativo_nao_e_interpretado_como_saldo_livre(self):
        self.credito("100.00")
        self.credito("-10.00")
        with self.assertRaises(CashbackHistoricoPendente):
            self.consumir("10.00")
        self.assertEqual(saldo_cashback_cliente(self.cliente.pk, empresa=self.empresa), Decimal("90.00"))
        self.assertFalse(CashbackConsumo.objects.exists())

    def test_alocacao_incompleta_nao_pode_aparentar_saldo_livre(self):
        credito = self.credito("100.00")
        debito = CashbackMovimento.objects.create(empresa=self.empresa, cliente=self.cliente, tipo=CashbackMovimento.TIPO_DEBITO, valor=Decimal("80.00"))
        consumo = CashbackConsumo.objects.create(empresa=self.empresa, operacao_uuid=uuid.uuid4(), movimento=debito)
        CashbackConsumoCredito.objects.create(consumo=consumo, credito=credito, valor=Decimal("10.00"))
        with self.assertRaises(CashbackHistoricoPendente):
            self.saldo()

    def test_estorno_explicito_parcial_idempotente_e_sem_somar_joins_em_duplicidade(self):
        self.credito("100.00", 10)
        consumo = self.consumir("80.00")
        origem = consumo.alocacoes.get()
        primeiro = self.estorno("10.00", [(origem.pk, "10.00")])
        segundo = self.estorno("15.00", [(origem.pk, "15.00")])
        vincular_estorno_cashback(empresa=self.empresa, estorno=primeiro, parcelas=[(origem.pk, "10.00")])
        self.assertEqual(CashbackEstornoCredito.objects.count(), 2)
        self.assertEqual(self.saldo(), Decimal("45.00"))
        self.assertEqual(primeiro.alocacoes_restituicao.get().consumo_credito_id, origem.pk)
        self.assertEqual(segundo.valor, Decimal("15.00"))

    def test_estorno_nao_restaura_validade_de_credito_expirado(self):
        self.credito("50.00", 0)
        self.credito("50.00", 30)
        consumo = self.consumir("80.00")
        origem = consumo.alocacoes.order_by("pk").first()
        with patch("financeiro.cashback_creditos.timezone.localdate", return_value=self.hoje + timedelta(days=1)):
            self.estorno("20.00", [(origem.pk, "20.00")])
            self.assertEqual(self.saldo(), Decimal("20.00"))
        self.assertEqual(origem.credito.valor, Decimal("50.00"))

    def test_estorno_distribuido_preserva_origens_de_dois_creditos(self):
        self.credito("30.00", 1)
        self.credito("50.00", 2)
        consumo = self.consumir("60.00")
        origens = list(consumo.alocacoes.order_by("pk"))
        movimento = self.estorno("20.00", [(origens[0].pk, "8.00"), (origens[1].pk, "12.00")])
        self.assertEqual(movimento.alocacoes_restituicao.count(), 2)
        self.assertEqual(self.saldo(), Decimal("40.00"))

    def test_estorno_excessivo_incompleto_ou_de_outro_cliente_nao_e_alocado(self):
        self.credito("100.00")
        origem = self.consumir("80.00").alocacoes.get()
        self.estorno("50.00", [(origem.pk, "50.00")])
        for valor, parcelas in (("40.00", [(origem.pk, "40.00")]), ("10.00", [(origem.pk, "5.00")]),
                                ("10.00", [(origem.pk, "5.00"), (origem.pk, "5.00")])):
            with self.subTest(valor=valor, parcelas=parcelas), self.assertRaises(CashbackErro):
                self.estorno(valor, parcelas)
        self.assertEqual(CashbackEstornoCredito.objects.count(), 1)
        outro = Cliente.objects.create(empresa=self.empresa, nome_cliente="Outro Estorno", tipo_pessoa="PF", documento="52998224725")
        estorno = CashbackMovimento.objects.create(empresa=self.empresa, cliente=outro, tipo=CashbackMovimento.TIPO_ESTORNO, valor=Decimal("10.00"))
        with self.assertRaises(CashbackErro):
            vincular_estorno_cashback(empresa=self.empresa, estorno=estorno, parcelas=[(origem.pk, "10.00")])
        self.assertFalse(estorno.alocacoes_restituicao.exists())

    def test_reserva_ativa_permanece_intacta_e_nao_e_ignorada_pelo_novo_consumo(self):
        self.credito("100.00")
        loja = Loja.objects.create(empresa=self.empresa, nome_loja="Loja FEFO", apelido_loja="FEFO", cnpj="31222333000180")
        hub = SysvarHub.objects.create(loja=loja)
        reserva = CashbackReserva.objects.create(empresa=self.empresa, hub=hub, cliente=self.cliente,
                                                 venda_uuid=uuid.uuid4(), operacao_uuid=uuid.uuid4(), valor=Decimal("30.00"))
        with self.assertRaises(CashbackHistoricoPendente):
            self.consumir("20.00")
        reserva.refresh_from_db()
        self.assertEqual(reserva.status, CashbackReserva.STATUS_RESERVADA)
        self.assertEqual(self.saldo(), Decimal("100.00"))
        self.assertFalse(CashbackConsumo.objects.exists())

    def test_constraints_e_protecao_de_historico(self):
        credito = self.credito("100.00")
        consumo = self.consumir("20.00")
        with self.assertRaises(IntegrityError), transaction.atomic():
            credito.delete()
            connection.check_constraints()
        with self.assertRaises(IntegrityError), transaction.atomic():
            consumo.movimento.delete()
            connection.check_constraints()
        with self.assertRaises(IntegrityError), transaction.atomic():
            CashbackConsumoCredito.objects.create(consumo=consumo, credito=credito, valor=Decimal("1.00"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            CashbackConsumo.objects.create(empresa=self.empresa, movimento=consumo.movimento, operacao_uuid=uuid.uuid4())


class CashbackCreditoConcorrenciaTests(CashbackCreditoMixin, TransactionTestCase):
    def executar_em_paralelo(self, operacoes):
        barreira = Barrier(2)
        empresa_id, cliente_id = self.empresa.pk, self.cliente.pk

        def executar(operacao):
            close_old_connections()
            try:
                empresa = Empresa.objects.get(pk=empresa_id)
                cliente = Cliente.objects.get(pk=cliente_id)
                barreira.wait(timeout=10)
                try:
                    return consumir_cashback_fefo(empresa=empresa, cliente=cliente, operacao_uuid=operacao, valor="80.00").pk
                except CashbackErro:
                    return None
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            tarefas = [executor.submit(executar, operacao) for operacao in operacoes]
            return [tarefa.result(timeout=20) for tarefa in tarefas]

    @skipUnlessDBFeature("has_select_for_update")
    def test_operacoes_simultaneas_nao_consumem_o_mesmo_saldo(self):
        self.credito("100.00")
        resultados = self.executar_em_paralelo([uuid.uuid4(), uuid.uuid4()])
        self.assertEqual(sum(resultado is not None for resultado in resultados), 1)
        self.assertEqual(CashbackConsumo.objects.count(), 1)
        self.assertEqual(self.saldo(), Decimal("20.00"))

    @skipUnlessDBFeature("has_select_for_update")
    def test_retry_simultaneo_retorna_o_mesmo_consumo(self):
        self.credito("100.00")
        operacao = uuid.uuid4()
        resultados = self.executar_em_paralelo([operacao, operacao])
        self.assertIsNotNone(resultados[0])
        self.assertEqual(resultados[0], resultados[1])
        self.assertEqual(CashbackConsumo.objects.count(), 1)
        self.assertEqual(CashbackConsumoCredito.objects.aggregate(total=models.Sum("valor"))["total"], Decimal("80.00"))


class CashbackCreditoSchemaAnteriorTests(CashbackCreditoMixin, TransactionTestCase):
    def test_fluxo_legado_funciona_sem_novas_tabelas(self):
        # Apenas o banco isolado criado pelo runner pode ser revertido neste teste.
        self.assertIn("test", str(connection.settings_dict["NAME"]).lower())
        anterior = ("financeiro", "0037_cashbackmovimento_devolucao")
        atual = ("financeiro", "0038_controle_creditos_cashback")
        try:
            MigrationExecutor(connection).migrate([anterior])
            self.assertNotIn(CashbackConsumo._meta.db_table, connection.introspection.table_names())
            self.credito("100.00")
            sem_vinculos = self.credito("1.00")
            sem_vinculos.delete()
            outra_empresa = Empresa.objects.create(nome="Schema anterior", documento="51222333000180")
            with self.assertRaises(ProtectedError):
                outra_empresa.delete()
            CashbackConfig.objects.create(empresa=self.empresa, ativo=True)
            loja = Loja.objects.create(empresa=self.empresa, nome_loja="Loja Schema", apelido_loja="SCHEMA", cnpj="31222333000180")
            hub = SysvarHub.objects.create(loja=loja)
            self.assertEqual(saldo_cashback_cliente(self.cliente.pk, empresa=self.empresa), Decimal("100.00"))
            reserva = reservar_cashback_venda(hub, uuid.uuid4(), uuid.uuid4(), self.cliente.pk, "30.00")
            self.assertEqual(reserva["status"], CashbackReserva.STATUS_RESERVADA)
            self.assertEqual(reserva["valor"], "30.00")
        finally:
            MigrationExecutor(connection).migrate([atual])
