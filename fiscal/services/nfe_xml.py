from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import re
import xml.etree.ElementTree as ET

from rest_framework.exceptions import ValidationError


NFE_NS = "http://www.portalfiscal.inf.br/nfe"
MAX_XML_BYTES = 2 * 1024 * 1024


@dataclass
class NFeXmlItem:
    numero_item: int
    codigo_produto_fornecedor: str
    descricao_produto: str
    gtin_ean: str
    ncm: str
    cfop: str
    unidade_comercial: str
    quantidade_comercial: Decimal
    valor_unitario_comercial: Decimal
    valor_produto: Decimal
    valor_desconto: Decimal
    informacoes_adicionais: str
    impostos_fiscais: dict


@dataclass
class NFeXmlData:
    chave_acesso: str
    modelo: str
    serie: str
    numero: str
    dt_emissao: date
    dh_emissao: datetime | None
    dh_saida_entrada: datetime | None
    natureza_operacao: str
    emitente_documento: str
    emitente_nome: str
    emitente_ie: str
    destinatario_documento: str
    destinatario_nome: str
    valor_produtos: Decimal
    valor_desconto: Decimal
    valor_frete: Decimal
    valor_total: Decimal
    protocolo_autorizacao: str
    situacao_fiscal: str
    versao_leiaute: str
    nfe_id_xml: str
    codigo_uf: str
    codigo_numerico: str
    tipo_operacao: str
    identificador_destino: str
    municipio_fato_gerador: str
    tipo_impressao: str
    tipo_emissao: str
    digito_verificador: str
    ambiente: str
    finalidade_nfe: str
    consumidor_final: str
    presenca_comprador: str
    intermediador: str
    processo_emissao: str
    versao_processo: str
    protocolo_chave_acesso: str
    protocolo_recebido_em: datetime | None
    protocolo_cstat: str
    protocolo_motivo: str
    totais_fiscais: dict
    cobranca_fiscal: dict
    pagamentos_fiscais: list
    documentos_referenciados: list
    informacoes_complementares_fisco: str
    informacoes_complementares_contribuinte: str
    itens: list[NFeXmlItem]


@dataclass
class NFeEventoXmlData:
    chave_acesso: str
    id_evento: str
    tipo_evento: str
    sequencia: int
    tipo_evento_descricao: str
    data_hora_evento: datetime | None
    protocolo: str
    cstat: str
    xmotivo: str
    ambiente: str


def only_digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def parse_nfe_xml(original_bytes):
    if not original_bytes:
        raise ValidationError({"arquivo": "Arquivo XML vazio."})
    if len(original_bytes) > MAX_XML_BYTES:
        raise ValidationError({"arquivo": "Arquivo XML excede o tamanho máximo permitido para NF-e."})
    head = original_bytes[:512].lower()
    if b"<!doctype" in head or b"<!entity" in original_bytes[:4096].lower():
        raise ValidationError({"arquivo": "XML com DTD ou entidades não é aceito."})
    try:
        root = ET.fromstring(original_bytes)
    except ET.ParseError as exc:
        raise ValidationError({"arquivo": "XML malformado ou inválido."}) from exc

    if _local_name(root.tag) not in {"nfeProc", "NFe"}:
        raise ValidationError({"arquivo": "Estrutura XML não aceita para importação de NF-e original."})
    if _find_first(root, "infEvento") is not None or _find_first(root, "retEvento") is not None:
        raise ValidationError({"arquivo": "XML de evento não é aceito como NF-e original."})

    inf = _find_first(root, "infNFe")
    if inf is None:
        raise ValidationError({"arquivo": "Documento não é uma NF-e modelo 55 suportada."})
    ide = _child(inf, "ide")
    emit = _child(inf, "emit")
    dest = _child(inf, "dest")
    total = _child(_child(inf, "total"), "ICMSTot")
    if ide is None or emit is None or dest is None or total is None:
        raise ValidationError({"arquivo": "XML de NF-e sem grupos obrigatórios."})

    chave_id = str(inf.attrib.get("Id") or "")
    chave = only_digits(chave_id[3:] if chave_id.startswith("NFe") else chave_id)
    prot_chave = only_digits(_text(_find_first(root, "chNFe")))
    if not chave and prot_chave:
        chave = prot_chave
    if len(chave) != 44:
        raise ValidationError({"chave_acesso": "Chave de acesso da NF-e inválida ou ausente."})
    if prot_chave and prot_chave != chave:
        raise ValidationError({"chave_acesso": "Chave de acesso inconsistente no XML."})

    modelo = _text(_child(ide, "mod"))
    if modelo != "55":
        raise ValidationError({"modelo": "Somente NF-e modelo 55 é suportada nesta etapa."})
    if len(chave) >= 22 and chave[20:22] != "55":
        raise ValidationError({"modelo": "Modelo da chave de acesso incompatível com NF-e modelo 55."})

    prot = _find_first(root, "infProt")
    prot_cstat = _text(_child(prot, "cStat"))
    if _local_name(root.tag) == "nfeProc" and prot_cstat and prot_cstat != "100":
        raise ValidationError({"situacao_fiscal": "NF-e sem autorização de uso não pode ser importada para entrada operacional."})

    itens = []
    for det in _children(inf, "det"):
        prod = _child(det, "prod")
        if prod is None:
            continue
        itens.append(
            NFeXmlItem(
                numero_item=int(det.attrib.get("nItem") or len(itens) + 1),
                codigo_produto_fornecedor=_text(_child(prod, "cProd")),
                descricao_produto=_text(_child(prod, "xProd")),
                gtin_ean=_gtin(_text(_child(prod, "cEAN"))),
                ncm=_text(_child(prod, "NCM")),
                cfop=_text(_child(prod, "CFOP")),
                unidade_comercial=_text(_child(prod, "uCom")),
                quantidade_comercial=_decimal(_text(_child(prod, "qCom"))),
                valor_unitario_comercial=_decimal(_text(_child(prod, "vUnCom"))),
                valor_produto=_decimal(_text(_child(prod, "vProd"))),
                valor_desconto=_decimal(_text(_child(prod, "vDesc"))),
                informacoes_adicionais=_text(_child(det, "infAdProd")),
                impostos_fiscais=_xml_to_dict(_child(det, "imposto")),
            )
        )
    if not itens:
        raise ValidationError({"itens": "XML de NF-e sem itens."})

    return NFeXmlData(
        chave_acesso=chave,
        modelo=modelo,
        serie=_text(_child(ide, "serie")),
        numero=_text(_child(ide, "nNF")),
        dt_emissao=_date(_text(_child(ide, "dhEmi")) or _text(_child(ide, "dEmi"))),
        dh_emissao=_datetime_or_none(_text(_child(ide, "dhEmi"))),
        dh_saida_entrada=_datetime_or_none(_text(_child(ide, "dhSaiEnt")) or _text(_child(ide, "dSaiEnt"))),
        natureza_operacao=_text(_child(ide, "natOp")),
        emitente_documento=only_digits(_text(_child(emit, "CNPJ")) or _text(_child(emit, "CPF"))),
        emitente_nome=_text(_child(emit, "xNome")),
        emitente_ie=_text(_child(emit, "IE")),
        destinatario_documento=only_digits(_text(_child(dest, "CNPJ")) or _text(_child(dest, "CPF"))),
        destinatario_nome=_text(_child(dest, "xNome")),
        valor_produtos=_decimal(_text(_child(total, "vProd"))),
        valor_desconto=_decimal(_text(_child(total, "vDesc"))),
        valor_frete=_decimal(_text(_child(total, "vFrete"))),
        valor_total=_decimal(_text(_child(total, "vNF"))),
        protocolo_autorizacao=_text(_child(prot, "nProt")),
        situacao_fiscal="AUTORIZADA" if prot_cstat == "100" else "DESCONHECIDA",
        versao_leiaute=str(inf.attrib.get("versao") or root.attrib.get("versao") or ""),
        nfe_id_xml=str(inf.attrib.get("Id") or ""),
        codigo_uf=_text(_child(ide, "cUF")),
        codigo_numerico=_text(_child(ide, "cNF")),
        tipo_operacao=_text(_child(ide, "tpNF")),
        identificador_destino=_text(_child(ide, "idDest")),
        municipio_fato_gerador=_text(_child(ide, "cMunFG")),
        tipo_impressao=_text(_child(ide, "tpImp")),
        tipo_emissao=_text(_child(ide, "tpEmis")),
        digito_verificador=_text(_child(ide, "cDV")),
        ambiente=_text(_child(ide, "tpAmb")) or _text(_child(prot, "tpAmb")),
        finalidade_nfe=_text(_child(ide, "finNFe")),
        consumidor_final=_text(_child(ide, "indFinal")),
        presenca_comprador=_text(_child(ide, "indPres")),
        intermediador=_text(_child(ide, "indIntermed")),
        processo_emissao=_text(_child(ide, "procEmi")),
        versao_processo=_text(_child(ide, "verProc")),
        protocolo_chave_acesso=prot_chave,
        protocolo_recebido_em=_datetime_or_none(_text(_child(prot, "dhRecbto"))),
        protocolo_cstat=prot_cstat,
        protocolo_motivo=_text(_child(prot, "xMotivo")),
        totais_fiscais=_xml_to_dict(total),
        cobranca_fiscal=_xml_to_dict(_child(inf, "cobr")),
        pagamentos_fiscais=[_xml_to_dict(node) for node in _children(_child(inf, "pag"), "detPag")],
        documentos_referenciados=[_xml_to_dict(node) for node in _children(ide, "NFref")],
        informacoes_complementares_fisco=_text(_child(_child(inf, "infAdic"), "infAdFisco")),
        informacoes_complementares_contribuinte=_text(_child(_child(inf, "infAdic"), "infCpl")),
        itens=itens,
    )


def parse_nfe_evento_xml(original_bytes):
    if not original_bytes:
        raise ValidationError({"arquivo": "Arquivo XML vazio."})
    if len(original_bytes) > MAX_XML_BYTES:
        raise ValidationError({"arquivo": "Arquivo XML excede o tamanho máximo permitido para evento NF-e."})
    head = original_bytes[:512].lower()
    if b"<!doctype" in head or b"<!entity" in original_bytes[:4096].lower():
        raise ValidationError({"arquivo": "XML com DTD ou entidades não é aceito."})
    try:
        root = ET.fromstring(original_bytes)
    except ET.ParseError as exc:
        raise ValidationError({"arquivo": "XML malformado ou inválido."}) from exc
    if _find_first(root, "infNFe") is not None:
        raise ValidationError({"arquivo": "XML de NF-e original não é aceito como evento."})
    inf_evento = _find_first(root, "infEvento")
    if inf_evento is None:
        raise ValidationError({"arquivo": "XML não possui estrutura de evento NF-e suportada."})
    ret = _find_first(root, "infEvento")
    ret_evento = _find_first(root, "retEvento")
    if ret_evento is not None:
        ret = _find_first(ret_evento, "infEvento") or ret
    chave = only_digits(_text(_child(inf_evento, "chNFe")) or _text(_child(ret, "chNFe")))
    if len(chave) != 44:
        raise ValidationError({"chave_acesso": "Chave de acesso do evento inválida ou ausente."})
    tipo = _text(_child(inf_evento, "tpEvento")) or _text(_child(ret, "tpEvento"))
    seq = int(_text(_child(inf_evento, "nSeqEvento")) or _text(_child(ret, "nSeqEvento")) or 1)
    return NFeEventoXmlData(
        chave_acesso=chave,
        id_evento=str(inf_evento.attrib.get("Id") or ""),
        tipo_evento=tipo,
        sequencia=seq,
        tipo_evento_descricao=_text(_find_first(inf_evento, "descEvento")) or _text(_child(ret, "xEvento")),
        data_hora_evento=_datetime_or_none(_text(_child(inf_evento, "dhEvento")) or _text(_child(ret, "dhRegEvento"))),
        protocolo=_text(_child(ret, "nProt")),
        cstat=_text(_child(ret, "cStat")),
        xmotivo=_text(_child(ret, "xMotivo")),
        ambiente=_text(_child(inf_evento, "tpAmb")) or _text(_child(ret, "tpAmb")),
    )


def _local_name(tag):
    return tag.rsplit("}", 1)[-1]


def _child(node, name):
    if node is None:
        return None
    for child in list(node):
        if _local_name(child.tag) == name:
            return child
    return None


def _children(node, name):
    return [child for child in list(node or []) if _local_name(child.tag) == name]


def _find_first(node, name):
    if node is None:
        return None
    if _local_name(node.tag) == name:
        return node
    for child in list(node):
        found = _find_first(child, name)
        if found is not None:
            return found
    return None


def _text(node):
    return (node.text or "").strip() if node is not None else ""


def _decimal(value):
    value = str(value or "0").strip()
    if not value:
        return Decimal("0")
    return Decimal(value)


def _date(value):
    value = str(value or "").strip()
    if not value:
        raise ValidationError({"dt_emissao": "Data de emissão ausente no XML."})
    if "T" in value:
        return datetime.fromisoformat(re.sub(r"Z$", "+00:00", value)).date()
    return date.fromisoformat(value[:10])


def _datetime_or_none(value):
    value = str(value or "").strip()
    if not value:
        return None
    if "T" in value:
        return datetime.fromisoformat(re.sub(r"Z$", "+00:00", value))
    return datetime.fromisoformat(value[:10])


def _gtin(value):
    value = str(value or "").strip()
    return "" if value.upper() in {"SEM GTIN", "SEMGTIN"} else only_digits(value)


def _xml_to_dict(node):
    if node is None:
        return {}
    children = list(node)
    if not children:
        return _text(node)
    data = {}
    for child in children:
        key = _local_name(child.tag)
        value = _xml_to_dict(child)
        if key in data:
            if not isinstance(data[key], list):
                data[key] = [data[key]]
            data[key].append(value)
        else:
            data[key] = value
    return data
