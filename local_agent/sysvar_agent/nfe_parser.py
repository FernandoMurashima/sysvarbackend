import re
import xml.etree.ElementTree as ET


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
        "situacao_fiscal": situacao,
    }
