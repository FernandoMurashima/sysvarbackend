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
        self.assertEqual(service.PYTHON_CLASS, "sysvar_agent.windows_service.SysvarLocalAgentService")
        self.assertNotIn("\\", service.PYTHON_CLASS)
        self.assertNotIn(":", service.PYTHON_CLASS)
        self.assertTrue(hasattr(service, "run_agent"))
        self.assertFalse(hasattr(service.SysvarLocalAgentService, "scanner"))

    def test_windows_service_install_insere_startup_auto_antes_do_comando(self):
        import sysvar_agent.windows_service as service

        fake_util = Mock()
        argv = ["windows_service.py", "install"]
        with patch.object(service, "win32serviceutil", fake_util), patch.object(service, "prepare_windows_service_runtime", return_value={"host": Path("pythonservice.exe")}), patch.object(service, "configure_pythonservice_path"), patch.object(service, "validate_service_host_imports"), patch.object(service.sys, "argv", argv):
            service.main()
        self.assertEqual(argv, ["windows_service.py", "--startup", "auto", "install"])
        fake_util.HandleCommandLine.assert_called_once_with(service.SysvarLocalAgentService)
        self.assertEqual(service.SysvarLocalAgentService.__module__, "sysvar_agent.windows_service")

    def test_windows_service_startup_explicito_nao_e_modificado(self):
        import sysvar_agent.windows_service as service

        fake_util = Mock()
        argv = ["windows_service.py", "--startup", "delayed", "install"]
        with patch.object(service, "win32serviceutil", fake_util), patch.object(service, "prepare_windows_service_runtime", return_value={"host": Path("pythonservice.exe")}), patch.object(service, "configure_pythonservice_path"), patch.object(service, "validate_service_host_imports"), patch.object(service.sys, "argv", argv):
            service.main()
        self.assertEqual(argv, ["windows_service.py", "--startup", "delayed", "install"])
        fake_util.HandleCommandLine.assert_called_once_with(service.SysvarLocalAgentService)

    def test_windows_service_localiza_pythonservice_e_dll_runtime(self):
        import sysvar_agent.windows_service as service

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "pythonservice.exe"
            dll = root / "PythonBase" / f"python{service.sys.version_info.major}{service.sys.version_info.minor}.dll"
            host.write_text("host", encoding="utf-8")
            dll.parent.mkdir()
            dll.write_text("dll", encoding="utf-8")
            with patch.object(service.sys, "prefix", str(root)), patch.object(service.sys, "base_prefix", str(dll.parent)), patch.object(service.shutil, "which", return_value=None):
                self.assertEqual(service.locate_pythonservice_exe(), host)
                self.assertEqual(service.locate_python_runtime_dll(), dll)

    def test_windows_service_prepara_runtime_copiando_dlls_necessarias(self):
        import sysvar_agent.windows_service as service

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "venv" / "pythonservice.exe"
            host.parent.mkdir()
            host.write_text("host", encoding="utf-8")
            dll = root / "PythonBase" / f"python{service.sys.version_info.major}{service.sys.version_info.minor}.dll"
            py3 = root / "PythonBase" / f"python{service.sys.version_info.major}.dll"
            pywin = root / "site-packages" / f"pywintypes{service.sys.version_info.major}{service.sys.version_info.minor}.dll"
            pycom = root / "site-packages" / f"pythoncom{service.sys.version_info.major}{service.sys.version_info.minor}.dll"
            for file_path in (dll, py3, pywin, pycom):
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(file_path.name, encoding="utf-8")
            fake_pywintypes = Mock(__file__=str(pywin))
            fake_pythoncom = Mock(__file__=str(pycom))
            with patch.object(service.sys, "prefix", str(host.parent)), patch.object(service.sys, "base_prefix", str(dll.parent)), patch.object(service.sys, "executable", str(host.parent / "Scripts" / "python.exe")), patch.object(service, "pywintypes", fake_pywintypes), patch.object(service, "pythoncom", fake_pythoncom), patch.object(service.shutil, "which", return_value=None):
                prepared = service.prepare_windows_service_runtime()
            self.assertEqual(prepared["host"], host)
            self.assertTrue((host.parent / dll.name).exists())
            self.assertTrue((host.parent / py3.name).exists())
            self.assertTrue((host.parent / pywin.name).exists())
            self.assertTrue((host.parent / pycom.name).exists())

    def test_windows_service_preparo_nao_copia_quando_ja_esta_correto(self):
        import sysvar_agent.windows_service as service

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "pythonservice.exe"
            dll = root / f"python{service.sys.version_info.major}{service.sys.version_info.minor}.dll"
            host.write_text("host", encoding="utf-8")
            dll.write_text("dll", encoding="utf-8")
            with patch.object(service, "locate_pythonservice_exe", return_value=host), patch.object(service, "locate_python_runtime_dll", return_value=dll), patch.object(service, "locate_python_support_dlls", return_value=[]), patch.object(service, "locate_pywin32_runtime_dlls", return_value=[]), patch.object(service.shutil, "copy2") as copy_mock:
                service.prepare_windows_service_runtime()
            copy_mock.assert_not_called()

    def test_windows_service_erro_claro_quando_runtime_nao_e_localizado(self):
        import sysvar_agent.windows_service as service

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(service.sys, "prefix", str(root)), patch.object(service.sys, "base_prefix", str(root)), patch.object(service.sys, "executable", str(root / "python.exe")), patch.object(service.shutil, "which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "pythonservice.exe"):
                    service.locate_pythonservice_exe()
                host = root / "pythonservice.exe"
                host.write_text("host", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "DLL principal"):
                    service.locate_python_runtime_dll()

    def test_windows_service_install_e_update_preparam_antes_do_handle(self):
        import sysvar_agent.windows_service as service

        calls = []
        fake_util = Mock()
        fake_util.HandleCommandLine.side_effect = lambda *_: calls.append("handle")
        with patch.object(service, "win32serviceutil", fake_util), patch.object(service, "prepare_windows_service_runtime", side_effect=lambda: calls.append("prepare") or {"host": Path("pythonservice.exe")}), patch.object(service, "configure_pythonservice_path", side_effect=lambda *_: calls.append("paths")), patch.object(service, "validate_service_host_imports", side_effect=lambda: calls.append("imports")), patch.object(service.sys, "argv", ["windows_service.py", "install"]):
            service.main()
        self.assertEqual(calls, ["prepare", "paths", "imports", "handle"])
        calls.clear()
        with patch.object(service, "win32serviceutil", fake_util), patch.object(service, "prepare_windows_service_runtime", side_effect=lambda: calls.append("prepare") or {"host": Path("pythonservice.exe")}), patch.object(service, "configure_pythonservice_path", side_effect=lambda *_: calls.append("paths")), patch.object(service, "validate_service_host_imports", side_effect=lambda: calls.append("imports")), patch.object(service.sys, "argv", ["windows_service.py", "update"]):
            service.main()
        self.assertEqual(calls, ["prepare", "paths", "imports", "handle"])

    def test_windows_service_start_stop_restart_debug_nao_preparam_runtime(self):
        import sysvar_agent.windows_service as service

        for command in ("start", "stop", "restart", "debug"):
            fake_util = Mock()
            with patch.object(service, "win32serviceutil", fake_util), patch.object(service, "prepare_windows_service_runtime") as prepare_mock, patch.object(service.sys, "argv", ["windows_service.py", command]):
                service.main()
            prepare_mock.assert_not_called()

    def test_windows_service_preparo_nao_registra_token(self):
        import sysvar_agent.windows_service as service

        with patch.object(service, "locate_pythonservice_exe", side_effect=RuntimeError("pythonservice.exe não encontrado")):
            with self.assertRaises(RuntimeError) as exc:
                service.prepare_windows_service_runtime()
        self.assertNotIn("TOKEN_SUPER_SECRETO", str(exc.exception))

    def test_windows_service_monta_paths_do_servico_a_partir_do_prefix(self):
        import sysvar_agent.windows_service as service

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site = root / "venv" / "Lib" / "site-packages"
            for path in (root / "PythonBase" / "Lib", root / "PythonBase" / "DLLs", site, site / "win32", site / "win32" / "lib", site / "pythonwin"):
                path.mkdir(parents=True, exist_ok=True)
            with patch.object(service.sys, "prefix", str(root / "venv")), patch.object(service.sys, "base_prefix", str(root / "PythonBase")), patch.object(service.sysconfig, "get_path", return_value=str(root / "PythonBase" / "Lib")):
                paths = service.service_python_paths()
            expected = [root / "PythonBase" / "Lib", root / "PythonBase" / "DLLs", site, site / "win32", site / "win32" / "lib", site / "pythonwin"]
            self.assertEqual(paths, [p.resolve() for p in expected])
            self.assertTrue(all(str(path).startswith(str(root)) for path in paths))

    def test_windows_service_escreve_pythonservice_pth_com_paths_pywin32(self):
        import sysvar_agent.windows_service as service

        with tempfile.TemporaryDirectory() as tmp:
            host = Path(tmp) / "pythonservice.exe"
            host.write_text("host", encoding="utf-8")
            paths = [Path(tmp) / "site-packages", Path(tmp) / "site-packages" / "win32", Path(tmp) / "site-packages" / "win32" / "lib", Path(tmp) / "site-packages" / "pythonwin"]
            pth = service.write_pythonservice_pth(host, paths)
            content = pth.read_text(encoding="utf-8")
            self.assertIn("site-packages", content)
            self.assertIn("win32", content)
            self.assertIn("win32\\lib", content)
            self.assertIn("pythonwin", content)
            self.assertIn("import site", content)

    def test_windows_service_configura_pth_antes_de_validar_imports(self):
        import sysvar_agent.windows_service as service

        fake_util = Mock()
        calls = []
        with patch.object(service, "win32serviceutil", fake_util), patch.object(service, "prepare_windows_service_runtime", side_effect=lambda: calls.append("runtime") or {"host": Path("pythonservice.exe")}), patch.object(service, "configure_pythonservice_path", side_effect=lambda *_: calls.append("paths")), patch.object(service, "validate_service_host_imports", side_effect=lambda: calls.append("imports")), patch.object(service.sys, "argv", ["windows_service.py", "install"]):
            service.main()
        self.assertEqual(calls, ["runtime", "paths", "imports"])

    def test_windows_service_valida_imports_fora_do_cwd_sem_pythonpath(self):
        import sysvar_agent.windows_service as service

        completed = Mock(returncode=0, stderr="", stdout="")
        with patch.object(service.subprocess, "run", return_value=completed) as run_mock:
            self.assertTrue(service.validate_service_host_imports())
        kwargs = run_mock.call_args.kwargs
        self.assertNotEqual(Path(kwargs["cwd"]), Path.cwd())
        self.assertNotIn("PYTHONPATH", kwargs["env"])
        command = run_mock.call_args.args[0]
        self.assertIn("sysvar_agent.windows_service", command[2])
        self.assertIn("servicemanager", command[2])
        self.assertIn(service.PYTHON_CLASS, command[2])

    def test_windows_service_valida_imports_com_erro_claro(self):
        import sysvar_agent.windows_service as service

        completed = Mock(returncode=1, stderr="No module named sysvar_agent", stdout="")
        with patch.object(service.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "pip install"):
                service.validate_service_host_imports()


if __name__ == "__main__":
    unittest.main()
