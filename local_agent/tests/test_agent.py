import json
import os
import runpy
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from sysvar_agent.api_client import AuthApiError, NonRetryableApiError, SysvarApiClient, TransientApiError
from sysvar_agent.config import ConfigError, load_config
from sysvar_agent.host import run_agent
from sysvar_agent.nfe_parser import NFeParseError, parse_nfe_file
from sysvar_agent.queue import AgentQueue
from sysvar_agent.runner import AgentRunner
from sysvar_agent.scanner import DirectoryScanner


CHAVE = "35260822345678000195550010000001234567890121"


def nfe_xml(chave=CHAVE):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe">
  <NFe>
    <infNFe Id="NFe{chave}">
      <ide><mod>55</mod><serie>1</serie><nNF>12345</nNF><dhEmi>2026-09-03T08:00:00-03:00</dhEmi></ide>
      <emit><CNPJ>21222333000181</CNPJ><xNome>Fornecedor Teste</xNome></emit>
      <dest><CNPJ>11222333000181</CNPJ><xNome>Loja Teste</xNome></dest>
      <total><ICMSTot><vNF>1000.00</vNF></ICMSTot></total>
    </infNFe>
  </NFe>
  <protNFe><infProt><cStat>100</cStat></infProt></protNFe>
</nfeProc>"""


class Response:
    def __init__(self, status_code=200, data=None, invalid_json=False):
        self.status_code = status_code
        self.data = data or {}
        self.invalid_json = invalid_json

    def json(self):
        if self.invalid_json:
            raise ValueError("invalid json")
        return self.data


class ConfigTests(unittest.TestCase):
    def test_carrega_config_valido_e_repr_mascara_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"api_base_url": "http://localhost:8000", "token": "segredo"}), encoding="utf-8")
            cfg = load_config(path)
            self.assertEqual(cfg.api_base_url, "http://localhost:8000")
            self.assertNotIn("segredo", repr(cfg))

    def test_variavel_ambiente_sobrescreve_token(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"SYSVAR_AGENT_TOKEN": "env-token"}):
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"api_base_url": "http://localhost:8000", "token": "arquivo"}), encoding="utf-8")
            self.assertEqual(load_config(path).token, "env-token")

    def test_ausencia_de_token_falha(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"api_base_url": "http://localhost:8000"}), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_config_ausente_gera_erro_controlado_e_caminhos_relativos_usam_pasta_do_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "agent"
            config_dir.mkdir()
            path = config_dir / "config.json"
            path.write_text(json.dumps({"api_base_url": "http://localhost:8000", "token": "segredo"}), encoding="utf-8")
            other_cwd = Path(tmp) / "cwd"
            other_cwd.mkdir()
            current = Path.cwd()
            try:
                os.chdir(other_cwd)
                cfg = load_config(path)
            finally:
                os.chdir(current)
            self.assertEqual(Path(cfg.database_path), config_dir / "data" / "agent.db")
            self.assertEqual(Path(cfg.log_file), config_dir / "logs" / "sysvar-agent.log")
            with self.assertRaises(ConfigError):
                load_config(config_dir / "missing.json")


class ParserTests(unittest.TestCase):
    def _write(self, text):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".xml", encoding="utf-8", delete=False)
        tmp.write(text)
        tmp.close()
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        return tmp.name

    def test_xml_nfe_autorizado_valido_extrai_metadados(self):
        data = parse_nfe_file(self._write(nfe_xml()))
        self.assertEqual(data["chave_acesso"], CHAVE)
        self.assertEqual(data["modelo"], "55")
        self.assertEqual(data["serie"], "1")
        self.assertEqual(data["numero"], "12345")
        self.assertEqual(data["dh_emissao"], "2026-09-03T08:00:00-03:00")
        self.assertEqual(data["emitente_documento"], "21222333000181")
        self.assertEqual(data["emitente_nome"], "Fornecedor Teste")
        self.assertEqual(data["destinatario_documento"], "11222333000181")
        self.assertEqual(data["destinatario_nome"], "Loja Teste")
        self.assertEqual(data["valor_total"], "1000.00")
        self.assertEqual(data["situacao_fiscal"], "AUTORIZADA")

    def test_xml_sem_infnfe_e_chave_invalida_sao_recusados(self):
        with self.assertRaises(NFeParseError):
            parse_nfe_file(self._write("<root />"))
        with self.assertRaises(NFeParseError):
            parse_nfe_file(self._write(nfe_xml("123")))


class ApiTests(unittest.TestCase):
    def test_headers_e_endpoints(self):
        session = Mock()
        session.headers = {}
        session.get.return_value = Response(data={"configuracoes": []})
        session.post.side_effect = [Response(data={"ok": True}), Response(data={"created": False})]
        client = SysvarApiClient("http://sysvar", "token-secreto", 7, session=session)
        self.assertEqual(session.headers["Authorization"], "Agent token-secreto")
        self.assertEqual(client.get_configuracoes(), {"configuracoes": []})
        self.assertEqual(client.heartbeat("0.1.0", "HOST"), {"ok": True})
        self.assertEqual(client.enviar_xml_detectado({"x": 1}), {"created": False})

    def test_erros_http_classificados(self):
        session = Mock()
        session.headers = {}
        session.get.side_effect = requests.Timeout()
        client = SysvarApiClient("http://sysvar", "token", session=session)
        with self.assertRaises(TransientApiError):
            client.get_configuracoes()
        session.get.side_effect = None
        session.get.return_value = Response(status_code=500)
        with self.assertRaises(TransientApiError):
            client.get_configuracoes()
        session.get.return_value = Response(status_code=400, data={"campo": "erro"})
        with self.assertRaises(NonRetryableApiError):
            client.get_configuracoes()


class QueueTests(unittest.TestCase):
    def test_fila_pendente_persiste_marca_enviado_nao_duplica_e_backoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "agent.db"
            q = AgentQueue(db)
            payload = {"chave_acesso": CHAVE}
            self.assertTrue(q.enqueue(1, "a.xml", CHAVE, 10, 1.0, payload))
            q.close()
            q = AgentQueue(db)
            item = q.due_items()[0]
            self.assertEqual(item["status"], "PENDENTE")
            q.mark_error(item["id"], "falha", retry=True)
            item = q.get_by_chave(CHAVE)
            self.assertEqual(item["tentativas"], 1)
            self.assertIsNotNone(item["proxima_tentativa"])
            q.mark_error(item["id"], "xml inválido", retry=False)
            self.assertEqual(q.due_items(), [])
            q.mark_sent(item["id"])
            self.assertTrue(q.is_sent(CHAVE))
            self.assertFalse(q.enqueue(1, "a.xml", CHAVE, 10, 1.0, payload))
            self.assertEqual(q.get_by_chave(CHAVE)["status"], "ENVIADO")
            q.close()

    def test_reenfileirar_pendente_nao_duplica(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = AgentQueue(Path(tmp) / "agent.db")
            payload = {"chave_acesso": CHAVE}
            self.assertTrue(q.enqueue(1, "a.xml", CHAVE, 10, 1.0, payload))
            self.assertFalse(q.enqueue(1, "a.xml", CHAVE, 10, 1.0, payload))
            self.assertEqual(q.conn.execute("SELECT COUNT(*) FROM fila_envio").fetchone()[0], 1)
            self.assertEqual(q.get_by_chave(CHAVE)["status"], "PENDENTE")
            q.close()

    def test_backoff_transitorio_due_items_e_persistencia(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "agent.db"
            q = AgentQueue(db)
            q.enqueue(1, "a.xml", CHAVE, 10, 1.0, {"chave_acesso": CHAVE})
            item = q.get_by_chave(CHAVE)
            first_due = q.mark_error(item["id"], "offline", retry=True)
            row = q.get_by_chave(CHAVE)
            self.assertEqual(row["status"], "ERRO")
            self.assertEqual(row["tentativas"], 1)
            self.assertEqual(row["proxima_tentativa"], first_due)
            self.assertEqual(q.due_items(), [])
            self.assertFalse(q.enqueue(1, "a.xml", CHAVE, 10, 1.0, {"chave_acesso": CHAVE}))
            row = q.get_by_chave(CHAVE)
            self.assertEqual(row["tentativas"], 1)
            self.assertEqual(row["proxima_tentativa"], first_due)
            past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            q.conn.execute("UPDATE fila_envio SET proxima_tentativa=? WHERE chave_acesso=?", (past, CHAVE))
            q.conn.commit()
            self.assertEqual(len(q.due_items()), 1)
            q.mark_error(row["id"], "offline de novo", retry=True)
            row = q.get_by_chave(CHAVE)
            self.assertEqual(row["tentativas"], 2)
            self.assertGreater(datetime.fromisoformat(row["proxima_tentativa"]), datetime.now(timezone.utc) + timedelta(seconds=100))
            q.close()
            q = AgentQueue(db)
            row = q.get_by_chave(CHAVE)
            self.assertEqual(row["status"], "ERRO")
            self.assertEqual(row["tentativas"], 2)
            self.assertIsNotNone(row["proxima_tentativa"])
            q.close()

    def test_erro_definitivo_nao_volta_para_pendente_ao_reenfileirar(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = AgentQueue(Path(tmp) / "agent.db")
            q.enqueue(1, "a.xml", CHAVE, 10, 1.0, {"chave_acesso": CHAVE})
            item = q.get_by_chave(CHAVE)
            q.mark_error(item["id"], "HTTP 400", retry=False)
            self.assertEqual(q.due_items(), [])
            self.assertFalse(q.enqueue(1, "a.xml", CHAVE, 10, 1.0, {"chave_acesso": CHAVE}))
            row = q.get_by_chave(CHAVE)
            self.assertEqual(row["status"], "ERRO")
            self.assertEqual(row["retryable"], 0)
            self.assertIsNone(row["proxima_tentativa"])
            q.close()

    def test_arquivo_alterado_atualiza_payload_sem_apagar_politica_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = AgentQueue(Path(tmp) / "agent.db")
            q.enqueue(1, "a.xml", CHAVE, 10, 1.0, {"chave_acesso": CHAVE, "numero": "1"})
            item = q.get_by_chave(CHAVE)
            proxima = q.mark_error(item["id"], "offline", retry=True)
            self.assertTrue(q.enqueue(1, "a.xml", CHAVE, 11, 2.0, {"chave_acesso": CHAVE, "numero": "2"}))
            row = q.get_by_chave(CHAVE)
            self.assertEqual(row["status"], "ERRO")
            self.assertEqual(row["tentativas"], 1)
            self.assertEqual(row["proxima_tentativa"], proxima)
            self.assertEqual(json.loads(row["payload_json"])["numero"], "2")
            self.assertNotIn("<NFe", row["payload_json"])
            q.close()


class ScannerRunnerTests(unittest.TestCase):
    def test_pasta_inexistente_nao_derruba(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = AgentQueue(Path(tmp) / "agent.db")
            scanner = DirectoryScanner(q, min_file_age_seconds=1)
            self.assertEqual(scanner.scan([{"id": 1, "caminho_local": str(Path(tmp) / "nao-existe")}]), 0)
            q.close()

    def test_xml_valido_gera_item_e_ja_enviado_nao_reenfileira(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "xmls"
            folder.mkdir()
            xml = folder / "nfe.xml"
            xml.write_text(nfe_xml(), encoding="utf-8")
            old = time.time() - 10
            os.utime(xml, (old, old))
            q = AgentQueue(Path(tmp) / "agent.db")
            scanner = DirectoryScanner(q, min_file_age_seconds=1)
            self.assertEqual(scanner.scan([{"id": 1, "caminho_local": str(folder)}]), 1)
            item = q.get_by_chave(CHAVE)
            q.mark_sent(item["id"])
            self.assertEqual(scanner.scan([{"id": 1, "caminho_local": str(folder)}]), 0)
            q.close()

    def test_scanner_preserva_backoff_e_erro_definitivo_ao_reencontrar_xml(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "xmls"
            folder.mkdir()
            xml = folder / "nfe.xml"
            xml.write_text(nfe_xml(), encoding="utf-8")
            old = time.time() - 10
            os.utime(xml, (old, old))
            q = AgentQueue(Path(tmp) / "agent.db")
            scanner = DirectoryScanner(q, min_file_age_seconds=1)
            scanner.scan([{"id": 1, "caminho_local": str(folder)}])
            item = q.get_by_chave(CHAVE)
            proxima = q.mark_error(item["id"], "offline", retry=True)
            scanner.scan([{"id": 1, "caminho_local": str(folder)}])
            row = q.get_by_chave(CHAVE)
            self.assertEqual(row["status"], "ERRO")
            self.assertEqual(row["tentativas"], 1)
            self.assertEqual(row["proxima_tentativa"], proxima)
            q.mark_error(row["id"], "HTTP 400", retry=False)
            scanner.scan([{"id": 1, "caminho_local": str(folder)}])
            row = q.get_by_chave(CHAVE)
            self.assertEqual(row["status"], "ERRO")
            self.assertEqual(row["retryable"], 0)
            self.assertEqual(q.due_items(), [])
            q.close()

    def test_runner_created_true_false_marcam_enviado_e_falha_transitoria_preserva_fila(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = AgentQueue(Path(tmp) / "agent.db")
            payload = {"chave_acesso": CHAVE}
            q.enqueue(1, "a.xml", CHAVE, 10, 1.0, payload)
            config = Mock(min_file_age_seconds=1, heartbeat_interval_seconds=60, poll_interval_seconds=1)
            api = Mock()
            api.enviar_xml_detectado.return_value = {"created": True}
            runner = AgentRunner(config, api, q)
            runner.flush_queue()
            self.assertTrue(q.is_sent(CHAVE))
            q.enqueue(1, "b.xml", "45260822345678000195550010000001234567890122", 10, 1.0, {"chave_acesso": "45260822345678000195550010000001234567890122"})
            api.enviar_xml_detectado.return_value = {"created": False}
            runner.flush_queue()
            self.assertTrue(q.is_sent("45260822345678000195550010000001234567890122"))
            q.enqueue(1, "c.xml", "55260822345678000195550010000001234567890123", 10, 1.0, {"chave_acesso": "55260822345678000195550010000001234567890123"})
            api.enviar_xml_detectado.side_effect = TransientApiError("offline")
            runner.flush_queue()
            self.assertEqual(q.get_by_chave("55260822345678000195550010000001234567890123")["status"], "ERRO")
            q.close()

    def test_runner_stop_event_interrompe_sem_esperar_poll_interval(self):
        q = Mock()
        config = Mock(min_file_age_seconds=1, heartbeat_interval_seconds=60, poll_interval_seconds=30)
        api = Mock()
        runner = AgentRunner(config, api, q)
        runner.run_once = Mock(side_effect=lambda: stop_event.set())
        stop_event = threading.Event()
        started = time.time()
        runner.run_forever(stop_event=stop_event)
        self.assertLess(time.time() - started, 2)
        runner.run_once.assert_called_once()

    def test_host_fecha_fila_no_encerramento(self):
        fake_queue = Mock()
        fake_runner = Mock()
        with patch("sysvar_agent.host.load_config") as load_config_mock, patch("sysvar_agent.host.configure_logging"), patch("sysvar_agent.host.AgentQueue", return_value=fake_queue), patch("sysvar_agent.host.SysvarApiClient"), patch("sysvar_agent.host.AgentRunner", return_value=fake_runner):
            load_config_mock.return_value = Mock(api_base_url="http://sysvar", token="token", request_timeout_seconds=1, database_path="agent.db", log_file="agent.log")
            run_agent(config_path="config.json", once=True)
        fake_runner.run_once.assert_called_once()
        fake_queue.close.assert_called_once()

    def test_main_once_e_continuo_usam_host_sem_quebrar_cli(self):
        with patch("sysvar_agent.host.run_agent") as run_mock, patch("sys.argv", ["sysvar_agent", "--config", "cfg.json", "--once"]):
            runpy.run_module("sysvar_agent.__main__", run_name="__main__")
        run_mock.assert_called_once_with(config_path="cfg.json", once=True)
        with patch("sysvar_agent.host.run_agent") as run_mock, patch("sys.argv", ["sysvar_agent", "--config", "cfg.json"]):
            runpy.run_module("sysvar_agent.__main__", run_name="__main__")
        run_mock.assert_called_once_with(config_path="cfg.json", once=False)

    def test_runner_401_403_preserva_fila_com_backoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = AgentQueue(Path(tmp) / "agent.db")
            q.enqueue(1, "a.xml", CHAVE, 10, 1.0, {"chave_acesso": CHAVE})
            config = Mock(min_file_age_seconds=1, heartbeat_interval_seconds=60, poll_interval_seconds=1)
            api = Mock()
            api.enviar_xml_detectado.side_effect = AuthApiError("Autenticação recusada")
            runner = AgentRunner(config, api, q)
            runner.flush_queue()
            row = q.get_by_chave(CHAVE)
            self.assertEqual(row["status"], "ERRO")
            self.assertEqual(row["retryable"], 1)
            self.assertEqual(row["tentativas"], 1)
            self.assertIsNotNone(row["proxima_tentativa"])
            self.assertEqual(q.due_items(), [])
            q.close()

    def test_windows_service_importavel_e_reaproveita_host(self):
        import sysvar_agent.windows_service as service

        self.assertEqual(service.SERVICE_NAME, "SysvarLocalAgent")
        self.assertEqual(service.SERVICE_DISPLAY_NAME, "Sysvar Local Agent")
        self.assertTrue(hasattr(service, "run_agent"))
        self.assertFalse(hasattr(service.SysvarLocalAgentService, "scanner"))

    def test_windows_service_install_insere_startup_auto_antes_do_comando(self):
        import sysvar_agent.windows_service as service

        fake_util = Mock()
        argv = ["windows_service.py", "install"]
        with patch.object(service, "win32serviceutil", fake_util), patch.object(service.sys, "argv", argv):
            service.main()
        self.assertEqual(argv, ["windows_service.py", "--startup", "auto", "install"])
        fake_util.HandleCommandLine.assert_called_once_with(service.SysvarLocalAgentService)

    def test_windows_service_startup_explicito_nao_e_modificado(self):
        import sysvar_agent.windows_service as service

        fake_util = Mock()
        argv = ["windows_service.py", "--startup", "delayed", "install"]
        with patch.object(service, "win32serviceutil", fake_util), patch.object(service.sys, "argv", argv):
            service.main()
        self.assertEqual(argv, ["windows_service.py", "--startup", "delayed", "install"])
        fake_util.HandleCommandLine.assert_called_once_with(service.SysvarLocalAgentService)


if __name__ == "__main__":
    unittest.main()
