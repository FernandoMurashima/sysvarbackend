import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from sysvar_agent.api_client import NonRetryableApiError, SysvarApiClient, TransientApiError
from sysvar_agent.config import ConfigError, load_config
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


if __name__ == "__main__":
    unittest.main()
