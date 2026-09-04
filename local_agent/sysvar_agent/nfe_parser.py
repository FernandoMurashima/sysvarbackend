import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation


class NFeParseError(ValueError):
    pass


def _strip_ns(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child(node, *path):
    current = node
    for part in path:
        found = None
        for child in list(current):
            if _strip_ns(child.tag) == part:
                found = child
                break
        if found is None:
            return None
        current = found
    return current


def _text(node, *path):
    child = _child(node, *path)
    return (child.text or "").strip() if child is not None and child.text is not None else ""


def _find_first(root, name):
    for node in root.iter():
        if _strip_ns(node.tag) == name:
            return node
    return None


def _quantidade_total_faturada(inf):
    total = Decimal("0")
    unidades = set()
    encontrou_item = False
    for det in list(inf):
        if _strip_ns(det.tag) != "det":
            continue
        prod = _child(det, "prod")
        if prod is None:
            continue
        qcom = _text(prod, "qCom")
        if not qcom:
            continue
        try:
            quantidade = Decimal(qcom)
        except InvalidOperation as exc:
            raise NFeParseError("Quantidade comercial inválida.") from exc
        total += quantidade
        encontrou_item = True
        unidade = _text(prod, "uCom")
        if unidade:
            unidades.add(unidade)

    if not encontrou_item:
        return None, ""
    if len(unidades) > 1:
        return None, "DIVERSAS"
    return total, next(iter(unidades), "")


def parse_nfe_file(path):
    parser = ET.XMLParser()
    tree = ET.parse(path, parser=parser)
    return parse_nfe_root(tree.getroot())


def parse_nfe_root(root):
    inf = _find_first(root, "infNFe")
    if inf is None:
        raise NFeParseError("XML sem infNFe.")
    raw_id = inf.attrib.get("Id", "")
    chave = raw_id[3:] if raw_id.startswith("NFe") else raw_id
    if not re.fullmatch(r"\d{44}", chave or ""):
        raise NFeParseError("Chave de acesso inválida.")

    cstat = _text(root, "protNFe", "infProt", "cStat")
    situacao = "AUTORIZADA" if cstat == "100" else "DESCONHECIDA"
    emit_doc = _text(inf, "emit", "CNPJ") or _text(inf, "emit", "CPF")
    dest_doc = _text(inf, "dest", "CNPJ") or _text(inf, "dest", "CPF")
    quantidade_total, unidade_comercial = _quantidade_total_faturada(inf)
    return {
        "chave_acesso": chave,
        "modelo": _text(inf, "ide", "mod"),
        "serie": _text(inf, "ide", "serie"),
        "numero": _text(inf, "ide", "nNF"),
        "dh_emissao": _text(inf, "ide", "dhEmi"),
        "emitente_documento": emit_doc,
        "emitente_nome": _text(inf, "emit", "xNome"),
        "destinatario_documento": dest_doc,
        "destinatario_nome": _text(inf, "dest", "xNome"),
        "valor_total": _text(inf, "total", "ICMSTot", "vNF") or "0.00",
        "quantidade_total_faturada": str(quantidade_total) if quantidade_total is not None else None,
        "unidade_comercial": unidade_comercial,
        "situacao_fiscal": situacao,
    }
