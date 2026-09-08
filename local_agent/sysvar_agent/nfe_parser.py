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
        if current is None:
            return None
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


def _children(node, name):
    return [child for child in list(node) if _strip_ns(child.tag) == name] if node is not None else []


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


def _xml_to_dict(node):
    if node is None:
        return {}
    children = list(node)
    if not children:
        return _text_node(node)
    data = {}
    for child in children:
        key = _strip_ns(child.tag)
        value = _xml_to_dict(child)
        if key in data:
            if not isinstance(data[key], list):
                data[key] = [data[key]]
            data[key].append(value)
        else:
            data[key] = value
    return data


def _text_node(node):
    return (node.text or "").strip() if node is not None and node.text is not None else ""


def _gtin(value):
    value = str(value or "").strip()
    return "" if value.upper() in {"SEM GTIN", "SEMGTIN"} else re.sub(r"\D", "", value)


def _fiscal_data(root, inf):
    ide = _child(inf, "ide")
    emit = _child(inf, "emit")
    dest = _child(inf, "dest")
    total = _child(inf, "total", "ICMSTot")
    prot = _find_first(root, "infProt")
    inf_adic = _child(inf, "infAdic")
    dados = {
        "modelo": _text(ide, "mod"),
        "serie": _text(ide, "serie"),
        "numero": _text(ide, "nNF"),
        "chave_acesso": "",
        "dt_emissao": (_text(ide, "dhEmi") or _text(ide, "dEmi"))[:10],
        "dh_emissao": _text(ide, "dhEmi"),
        "dh_saida_entrada": _text(ide, "dhSaiEnt") or _text(ide, "dSaiEnt"),
        "natureza_operacao": _text(ide, "natOp"),
        "tipo_operacao": _text(ide, "tpNF"),
        "identificador_destino": _text(ide, "idDest"),
        "municipio_fato_gerador": _text(ide, "cMunFG"),
        "tipo_impressao": _text(ide, "tpImp"),
        "tipo_emissao": _text(ide, "tpEmis"),
        "digito_verificador": _text(ide, "cDV"),
        "ambiente": _text(ide, "tpAmb") or _text(prot, "tpAmb"),
        "finalidade_nfe": _text(ide, "finNFe"),
        "consumidor_final": _text(ide, "indFinal"),
        "presenca_comprador": _text(ide, "indPres"),
        "intermediador": _text(ide, "indIntermed"),
        "processo_emissao": _text(ide, "procEmi"),
        "versao_processo": _text(ide, "verProc"),
        "versao_leiaute": inf.attrib.get("versao", "") or root.attrib.get("versao", ""),
        "emitente": {"documento": _text(emit, "CNPJ") or _text(emit, "CPF"), "nome": _text(emit, "xNome"), "ie": _text(emit, "IE")},
        "destinatario": {"documento": _text(dest, "CNPJ") or _text(dest, "CPF"), "nome": _text(dest, "xNome")},
        "protocolo_autorizacao": _text(prot, "nProt"),
        "protocolo_chave_acesso": _text(prot, "chNFe"),
        "protocolo_recebido_em": _text(prot, "dhRecbto"),
        "protocolo_cstat": _text(prot, "cStat"),
        "protocolo_motivo": _text(prot, "xMotivo"),
        "situacao_fiscal": "AUTORIZADA" if _text(prot, "cStat") == "100" else "DESCONHECIDA",
        "valor_produtos": _text(total, "vProd") or "0.00",
        "valor_desconto": _text(total, "vDesc") or "0.00",
        "valor_frete": _text(total, "vFrete") or "0.00",
        "valor_total": _text(total, "vNF") or "0.00",
        "totais_fiscais": _xml_to_dict(total),
        "cobranca_fiscal": _xml_to_dict(_child(inf, "cobr")),
        "pagamentos_fiscais": [_xml_to_dict(node) for node in _children(_child(inf, "pag"), "detPag")],
        "documentos_referenciados": [_xml_to_dict(node) for node in _children(ide, "NFref")],
        "informacoes_complementares_fisco": _text(inf_adic, "infAdFisco"),
        "informacoes_complementares_contribuinte": _text(inf_adic, "infCpl"),
    }
    return dados


def _fiscal_items(inf):
    itens = []
    for det in _children(inf, "det"):
        prod = _child(det, "prod")
        if prod is None:
            continue
        itens.append({
            "numero_item": int(det.attrib.get("nItem") or len(itens) + 1),
            "codigo_produto_fornecedor": _text(prod, "cProd"),
            "descricao_produto": _text(prod, "xProd"),
            "gtin_ean": _gtin(_text(prod, "cEAN")),
            "ncm": _text(prod, "NCM"),
            "cfop": _text(prod, "CFOP"),
            "unidade_comercial": _text(prod, "uCom"),
            "quantidade_comercial": _text(prod, "qCom") or "0",
            "valor_unitario_comercial": _text(prod, "vUnCom") or "0",
            "valor_produto": _text(prod, "vProd") or "0.00",
            "valor_desconto": _text(prod, "vDesc") or "0.00",
            "informacoes_adicionais": _text(det, "infAdProd"),
            "impostos_fiscais": _xml_to_dict(_child(det, "imposto")),
        })
    return itens


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
    dados_fiscais = _fiscal_data(root, inf)
    dados_fiscais["chave_acesso"] = chave
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
        "dados_fiscais": dados_fiscais,
        "itens_fiscais": _fiscal_items(inf),
    }
