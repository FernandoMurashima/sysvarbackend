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


@dataclass
class NFeXmlData:
    chave_acesso: str
    modelo: str
    serie: str
    numero: str
    dt_emissao: date
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
    itens: list[NFeXmlItem]


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
        protocolo_autorizacao=_text(_find_first(root, "nProt")),
        itens=itens,
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


def _gtin(value):
    value = str(value or "").strip()
    return "" if value.upper() in {"SEM GTIN", "SEMGTIN"} else only_digits(value)
