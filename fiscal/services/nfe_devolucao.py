from decimal import Decimal
from xml.sax.saxutils import escape

from django.db import transaction

from cadastros.models import Loja
from fiscal.models import NFeDevolucao, VendaDevolucao
from fiscal.models.venda_pdv import money


def registrar_nfe_devolucao(devolucao: VendaDevolucao) -> NFeDevolucao:
    existente = NFeDevolucao.objects.filter(devolucao=devolucao).first()
    if existente:
        return existente

    loja = Loja.objects.select_for_update().get(pk=devolucao.loja_id)
    serie = int(loja.serie_nfe or 1)
    numero = int(loja.proximo_numero_nfe or 1)
    loja.proximo_numero_nfe = numero + 1
    loja.save(update_fields=["proximo_numero_nfe"])

    nfce_origem = getattr(devolucao.venda, "nfce", None)
    nfe = NFeDevolucao.objects.create(
        devolucao=devolucao,
        loja=loja,
        nfce_origem=nfce_origem,
        ambiente=loja.ambiente_fiscal or "HOMOLOGACAO",
        modelo="55",
        serie=serie,
        numero=numero,
        status=NFeDevolucao.Status.DIGITADA,
    )
    return processar_nfe_devolucao(nfe.pk)


def processar_nfe_devolucao(nfe_id: int) -> NFeDevolucao:
    with transaction.atomic():
        nfe = (
            NFeDevolucao.objects.select_for_update()
            .select_related("loja", "devolucao__venda", "nfce_origem")
            .prefetch_related("devolucao__itens__venda_item")
            .get(pk=nfe_id)
        )
        if nfe.status == NFeDevolucao.Status.AUTORIZADA:
            return nfe

        pendencia = _pendencia_configuracao(nfe)
        nfe.xml = _gerar_xml_devolucao(nfe)
        if pendencia:
            nfe.status = NFeDevolucao.Status.ERRO_GERACAO
            nfe.retorno_codigo = "CONFIG"
            nfe.retorno_mensagem = pendencia
        else:
            nfe.status = NFeDevolucao.Status.PENDENTE_TRANSMISSAO
            nfe.retorno_codigo = "PENDENTE"
            nfe.retorno_mensagem = (
                "NF-e de devolucao registrada. Transmissor real de NF-e modelo 55 "
                "nao configurado para autorizacao automatica."
            )
        nfe.save(update_fields=["xml", "status", "retorno_codigo", "retorno_mensagem", "atualizado_em"])
        return nfe


def _pendencia_configuracao(nfe: NFeDevolucao) -> str:
    loja = nfe.loja
    if not loja.emite_nfe:
        return "Loja receptora nao esta habilitada para emitir NF-e."
    if not (loja.cnpj or "").strip():
        return "CNPJ da loja receptora nao configurado."
    if not (loja.ambiente_fiscal or "").strip():
        return "Ambiente fiscal da loja receptora nao configurado."
    if not (loja.regime_tributario or "").strip():
        return "Regime tributario da loja receptora nao configurado."
    if not int(loja.serie_nfe or 0):
        return "Serie de NF-e da loja receptora nao configurada."
    for item in nfe.devolucao.itens.all():
        venda_item = item.venda_item
        if not (venda_item.ncm or "").strip():
            return f"NCM ausente no item {item.descricao}."
        if not (venda_item.cfop or "").strip():
            return f"CFOP de devolucao nao configurado para o item {item.descricao}."
    return ""


def _gerar_xml_devolucao(nfe: NFeDevolucao) -> str:
    devolucao = nfe.devolucao
    nfce_chave = getattr(nfe.nfce_origem, "chave_acesso", "") or ""
    itens_xml = []
    valor_total = Decimal("0.00")
    desconto_total = Decimal("0.00")
    for idx, item in enumerate(devolucao.itens.all(), start=1):
        venda_item = item.venda_item
        valor_total += money(item.total_item)
        desconto_total += money(item.desconto)
        itens_xml.append(
            "<det nItem=\"{idx}\">"
            "<prod>"
            "<cProd>{ean}</cProd>"
            "<xProd>{descricao}</xProd>"
            "<NCM>{ncm}</NCM>"
            "<CFOP>{cfop}</CFOP>"
            "<qCom>{qtd}</qCom>"
            "<vUnCom>{unitario}</vUnCom>"
            "<vDesc>{desconto}</vDesc>"
            "<vProd>{total}</vProd>"
            "</prod>"
            "</det>".format(
                idx=idx,
                ean=escape(item.ean or ""),
                descricao=escape(item.descricao or ""),
                ncm=escape(venda_item.ncm or ""),
                cfop=escape(venda_item.cfop or ""),
                qtd=item.quantidade,
                unitario=Decimal(item.preco_unitario or 0).quantize(Decimal("0.0001")),
                desconto=money(item.desconto),
                total=money(item.total_item),
            )
        )

    ref_xml = f"<NFref><refNFe>{escape(nfce_chave)}</refNFe></NFref>" if nfce_chave else ""
    return (
        f"<NFeDevolucao ambiente=\"{escape(nfe.ambiente)}\" modelo=\"{escape(nfe.modelo)}\" "
        f"serie=\"{nfe.serie}\" numero=\"{nfe.numero}\">"
        f"<loja id=\"{nfe.loja_id}\" />"
        f"<devolucao documento=\"{escape(devolucao.documento)}\" />"
        f"<vendaOrigem documento=\"{escape(devolucao.venda.documento)}\" />"
        f"{ref_xml}"
        f"<itens>{''.join(itens_xml)}</itens>"
        f"<total><vProd>{money(valor_total)}</vProd><vDesc>{money(desconto_total)}</vDesc></total>"
        f"</NFeDevolucao>"
    )
