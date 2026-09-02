from decimal import Decimal, ROUND_HALF_UP
from datetime import date

from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework import parsers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from accounts.permissions import HasModuleRole
from cadastros.models import Fornecedor, Loja, Nat_Lancamento
from compras.models import OrdemServicoMaterial, PedidoCompra, PedidoCompraEntrega, RequisicaoItem
from compras.services_necessidade import sincronizar_requisicao_disponivel_para_atendimento
from compras.services_requisicao import atualizar_status_material_ordem_servico, atualizar_status_material_os
from produto.models import Estoque, EstoqueMovimentacao, PackItem, Produto, ProdutoDetalhe, ProdutoUsoConsumoEstoque, ProdutoUsoConsumoMovimentacao

from auditoria.models import AuditAction, AuditCategory
from auditoria.services import AuditService

FIN_OK = True
try:
    from financeiro.models import FormaPagamento, MovimentacaoFinanceira, Pagar, PagarItem
except Exception:
    FIN_OK = False
    FormaPagamento = MovimentacaoFinanceira = Pagar = PagarItem = None

from fiscal.models import FormaPagamentoFiscalMap, NotaFiscalEntrada, NotaFiscalEntradaDivergenciaXml, NotaFiscalEntradaEvento, NotaFiscalEntradaItem, NotaFiscalEntradaItemXml, XmlFornecedorRecebido
from fiscal.services.nfe_conferencia import registrar_conferencia, resolver_divergencia, resumo_conferencia
from fiscal.services.nfe_conciliacao import candidatos_item, conciliar_automaticamente, conciliar_manual, resumo_conciliacao
from fiscal.services.nfe_xml import only_digits, parse_nfe_evento_xml, parse_nfe_xml
from fiscal.serializers import (
    FormaPagamentoFiscalMapSerializer,
    NotaFiscalEntradaDivergenciaXmlSerializer,
    NotaFiscalEntradaEventoSerializer,
    NotaFiscalEntradaItemSerializer,
    NotaFiscalEntradaItemXmlSerializer,
    NotaFiscalEntradaSerializer,
    XmlFornecedorRecebidoSerializer,
)


def _q4(valor) -> Decimal:
    return Decimal(valor or 0).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _money(valor) -> Decimal:
    return Decimal(valor or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _q3(valor) -> Decimal:
    return Decimal(valor or 0).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def _audit(model_name: str, obj_id: str, changes: dict, request, action: str = "custom"):
    payload = {
        "action": AuditAction.OBJECT_UPDATED,
        "category": AuditCategory.FISCAL,
        "request": request,
        "user": getattr(request, "user", None),
        "app_label": "fiscal",
        "model": model_name,
        "object_id": obj_id,
        "metadata": {"legacy_action": action, "changes": changes},
    }
    if transaction.get_connection().in_atomic_block:
        transaction.on_commit(lambda: AuditService.success(**payload))
    else:
        AuditService.success(**payload)


def _documento_nota(nota: NotaFiscalEntrada) -> str:
    partes = [nota.modelo]
    if nota.serie:
        partes.append(nota.serie)
    partes.append(nota.numero)
    return "/".join(partes)[:30]


class BaseViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModuleRole]
    required_module = "compras"
    read_roles = ["Admin", "Diretor", "Gerente", "AssistentePagar"]
    write_roles = ["Admin", "Diretor", "Gerente", "AssistentePagar"]

    def _empresa_id_usuario(self):
        user = self.request.user
        if user.is_superuser:
            return self.request.query_params.get("empresa")
        return getattr(user, "empresa_id", None)


class NotaFiscalEntradaViewSet(BaseViewSet):
    queryset = (
        NotaFiscalEntrada.objects.select_related("empresa", "loja", "fornecedor", "pedido_compra", "criado_por")
        .prefetch_related("itens", "itens_xml")
        .all()
        .order_by("-dt_entrada", "-id")
    )
    serializer_class = NotaFiscalEntradaSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        pedido = self.request.query_params.get("pedido") or self.request.query_params.get("pedido_compra")
        status_q = self.request.query_params.get("status")
        numero = self.request.query_params.get("numero")
        chave = self.request.query_params.get("chave_acesso")
        fornecedor = self.request.query_params.get("fornecedor")
        loja = self.request.query_params.get("loja")
        dt_emissao_de = self.request.query_params.get("dt_emissao_de")
        dt_emissao_ate = self.request.query_params.get("dt_emissao_ate")
        dt_entrada_de = self.request.query_params.get("dt_entrada_de")
        dt_entrada_ate = self.request.query_params.get("dt_entrada_ate")
        valor_min = self.request.query_params.get("valor_min")
        valor_max = self.request.query_params.get("valor_max")
        search = self.request.query_params.get("search")

        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        if pedido:
            qs = qs.filter(pedido_compra_id=pedido)
        if status_q:
            qs = qs.filter(status=status_q)
        if numero:
            qs = qs.filter(numero__icontains=numero)
        if chave:
            qs = qs.filter(chave_acesso__icontains=chave)
        if fornecedor:
            qs = qs.filter(fornecedor_id=fornecedor)
        if loja:
            qs = qs.filter(loja_id=loja)
        if dt_emissao_de:
            qs = qs.filter(dt_emissao__gte=dt_emissao_de)
        if dt_emissao_ate:
            qs = qs.filter(dt_emissao__lte=dt_emissao_ate)
        if dt_entrada_de:
            qs = qs.filter(dt_entrada__gte=dt_entrada_de)
        if dt_entrada_ate:
            qs = qs.filter(dt_entrada__lte=dt_entrada_ate)
        if valor_min:
            qs = qs.filter(valor_total__gte=valor_min)
        if valor_max:
            qs = qs.filter(valor_total__lte=valor_max)
        if search:
            search_filter = (
                Q(modelo__icontains=search)
                | Q(serie__icontains=search)
                | Q(numero__icontains=search)
                | Q(chave_acesso__icontains=search)
                | Q(status__icontains=search)
                | Q(fornecedor__nome_fornecedor__icontains=search)
            )
            if str(search).isdigit():
                search_filter |= Q(pedido_compra_id=int(search))
            qs = qs.filter(search_filter)
        return qs

    @action(detail=False, methods=["get"], url_path="indicadores")
    def indicadores(self, request):
        qs = self.filter_queryset(self.get_queryset())
        agg = qs.aggregate(
            total=Count("id"),
            abertas=Count("id", filter=Q(status=NotaFiscalEntrada.Status.ABERTA)),
            fechadas=Count("id", filter=Q(status=NotaFiscalEntrada.Status.FECHADA)),
            canceladas=Count("id", filter=Q(status=NotaFiscalEntrada.Status.CANCELADA)),
            valor_total=Sum("valor_total"),
        )
        return Response(
            {
                "total": agg["total"] or 0,
                "abertas": agg["abertas"] or 0,
                "fechadas": agg["fechadas"] or 0,
                "canceladas": agg["canceladas"] or 0,
                "valor_total": str(_money(agg["valor_total"] or 0)),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="importar-xml", parser_classes=[parsers.MultiPartParser])
    @transaction.atomic
    def importar_xml(self, request):
        arquivo = request.FILES.get("arquivo") or request.FILES.get("xml")
        if not arquivo:
            raise ValidationError({"arquivo": "Informe o arquivo XML da NF-e."})
        original_bytes = arquivo.read()
        dados = parse_nfe_xml(original_bytes)
        if NotaFiscalEntrada.objects.select_for_update().filter(chave_acesso=dados.chave_acesso).exists():
            raise ValidationError({"chave_acesso": "NF-e já importada para esta chave de acesso."})

        empresa_id = self._empresa_id_usuario()
        if not empresa_id and not request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        if not empresa_id:
            empresa_id = request.data.get("empresa")
        if not empresa_id:
            raise ValidationError({"empresa": "Informe a empresa da importação."})

        fornecedor = self._identificar_fornecedor(empresa_id, dados.emitente_documento)
        loja = self._identificar_loja(empresa_id, dados.destinatario_documento)
        pedido = None
        pedido_id = request.data.get("pedido_compra") or request.data.get("pedido")
        if pedido_id:
            pedido = PedidoCompra.objects.select_related("empresa", "loja", "fornecedor").filter(pk=pedido_id).first()
            if not pedido:
                raise ValidationError({"pedido_compra": "Pedido de compra não encontrado."})
            if pedido.empresa_id != int(empresa_id) or pedido.loja_id != loja.id or pedido.fornecedor_id != fornecedor.id:
                raise ValidationError({"pedido_compra": "Pedido incompatível com empresa, loja ou fornecedor do XML."})

        try:
            xml_original = original_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValidationError({"arquivo": "XML deve estar em codificação textual válida."}) from exc

        nota = NotaFiscalEntrada.objects.create(
            empresa_id=empresa_id,
            loja=loja,
            fornecedor=fornecedor,
            pedido_compra=pedido,
            modelo=dados.modelo,
            serie=dados.serie,
            numero=dados.numero,
            chave_acesso=dados.chave_acesso,
            dt_emissao=dados.dt_emissao,
            dt_entrada=dados.dt_emissao,
            valor_produtos=dados.valor_produtos,
            valor_desconto=dados.valor_desconto,
            valor_frete=dados.valor_frete,
            valor_total=dados.valor_total,
            xml_original=xml_original,
            xml_importado=True,
            natureza_operacao=dados.natureza_operacao,
            emitente_documento=dados.emitente_documento,
            emitente_nome=dados.emitente_nome,
            emitente_ie=dados.emitente_ie,
            destinatario_documento=dados.destinatario_documento,
            destinatario_nome=dados.destinatario_nome,
            protocolo_autorizacao=dados.protocolo_autorizacao,
            situacao_fiscal=dados.situacao_fiscal,
            versao_leiaute=dados.versao_leiaute,
            nfe_id_xml=dados.nfe_id_xml,
            codigo_uf=dados.codigo_uf,
            codigo_numerico=dados.codigo_numerico,
            dh_emissao=dados.dh_emissao,
            dh_saida_entrada=dados.dh_saida_entrada,
            tipo_operacao=dados.tipo_operacao,
            identificador_destino=dados.identificador_destino,
            municipio_fato_gerador=dados.municipio_fato_gerador,
            tipo_impressao=dados.tipo_impressao,
            tipo_emissao=dados.tipo_emissao,
            digito_verificador=dados.digito_verificador,
            ambiente=dados.ambiente,
            finalidade_nfe=dados.finalidade_nfe,
            consumidor_final=dados.consumidor_final,
            presenca_comprador=dados.presenca_comprador,
            intermediador=dados.intermediador,
            processo_emissao=dados.processo_emissao,
            versao_processo=dados.versao_processo,
            protocolo_chave_acesso=dados.protocolo_chave_acesso,
            protocolo_recebido_em=dados.protocolo_recebido_em,
            protocolo_cstat=dados.protocolo_cstat,
            protocolo_motivo=dados.protocolo_motivo,
            totais_fiscais=dados.totais_fiscais,
            cobranca_fiscal=dados.cobranca_fiscal,
            pagamentos_fiscais=dados.pagamentos_fiscais,
            documentos_referenciados=dados.documentos_referenciados,
            informacoes_complementares_fisco=dados.informacoes_complementares_fisco,
            informacoes_complementares_contribuinte=dados.informacoes_complementares_contribuinte,
            criado_por=request.user if request.user.is_authenticated else None,
        )
        NotaFiscalEntradaItemXml.objects.bulk_create(
            [
                NotaFiscalEntradaItemXml(
                    nota=nota,
                    numero_item=item.numero_item,
                    codigo_produto_fornecedor=item.codigo_produto_fornecedor,
                    descricao_produto=item.descricao_produto,
                    gtin_ean=item.gtin_ean,
                    ncm=item.ncm,
                    cfop=item.cfop,
                    unidade_comercial=item.unidade_comercial,
                    quantidade_comercial=item.quantidade_comercial,
                    valor_unitario_comercial=item.valor_unitario_comercial,
                    valor_produto=item.valor_produto,
                    valor_desconto=item.valor_desconto,
                    informacoes_adicionais=item.informacoes_adicionais,
                    impostos_fiscais=item.impostos_fiscais,
                )
                for item in dados.itens
            ]
        )
        AuditService.success(
            AuditAction.OBJECT_CREATED,
            category=AuditCategory.FISCAL,
            request=request,
            user=getattr(request, "user", None),
            instance=nota,
            after={
                "empresa": nota.empresa_id,
                "loja": nota.loja_id,
                "fornecedor": nota.fornecedor_id,
                "chave_acesso": nota.chave_acesso,
                "xml_importado": True,
                "itens_xml": len(dados.itens),
                "situacao_fiscal": nota.situacao_fiscal,
                "ambiente": nota.ambiente,
                "finalidade_nfe": nota.finalidade_nfe,
            },
            metadata={"legacy_action": "importar_xml", "origem": "upload_xml_nfe"},
        )
        data = self.get_serializer(nota).data
        data["itens_xml_count"] = len(dados.itens)
        conciliacao = conciliar_automaticamente(nota, user=request.user, request=request)
        data["conciliacao_automatica"] = conciliacao
        data["resumo_conciliacao"] = resumo_conciliacao(nota)
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="itens-xml")
    def itens_xml(self, request, pk=None):
        nota = self.get_object()
        qs = nota.itens_xml.select_related("produto", "produto__unidade", "produto_fornecedor").order_by("numero_item")
        status_filtro = request.query_params.get("status")
        if status_filtro in {"conciliados", "conciliado"}:
            qs = qs.filter(produto__isnull=False)
        elif status_filtro in {"pendentes", "pendente", "nao_conciliados"}:
            qs = qs.filter(produto__isnull=True)
        return Response(NotaFiscalEntradaItemXmlSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="pendencias-xml")
    def pendencias_xml(self, request, pk=None):
        nota = self.get_object()
        qs = nota.itens_xml.select_related("produto", "produto__unidade", "produto_fornecedor").filter(produto__isnull=True).order_by("numero_item")
        return Response(NotaFiscalEntradaItemXmlSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="recusar")
    @transaction.atomic
    def recusar_xml(self, request, pk=None):
        nota = self.filter_queryset(self.get_queryset()).select_for_update().get(pk=pk)
        if nota.status != NotaFiscalEntrada.Status.ABERTA:
            return Response({"detail": "Somente NF-e aberta e não efetivada pode ser recusada."}, status=status.HTTP_400_BAD_REQUEST)
        if not nota.xml_importado:
            return Response({"detail": "Recusa de entrada está disponível apenas para NF-e importada por XML."}, status=status.HTTP_400_BAD_REQUEST)
        documento = self._documento_estoque(nota, "ENTRADA")
        if (
            EstoqueMovimentacao.objects.filter(documento=documento).exists()
            or ProdutoUsoConsumoMovimentacao.objects.filter(documento=documento).exists()
            or (FIN_OK and Pagar.objects.filter(nfe_id=nota.pk).exists())
        ):
            return Response({"detail": "Esta NF-e já possui efeitos operacionais e deve seguir o fluxo de cancelamento."}, status=status.HTTP_400_BAD_REQUEST)
        nota_id = nota.pk
        chave = nota.chave_acesso
        itens = nota.itens_xml.count()
        divergencias = nota.divergencias_xml.count()
        _audit(
            "notafiscalentrada",
            nota_id,
            {"status": [nota.status, "RECUSADA"], "chave_acesso": chave, "itens_xml": itens, "divergencias": divergencias},
            request,
            action="recusar_importacao_xml",
        )
        nota.delete()
        return Response(
            {"detail": "Entrada recusada. O XML poderá ser importado novamente.", "id": nota_id, "chave_acesso": chave},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="conciliar-automaticamente")
    @transaction.atomic
    def conciliar_xml_automaticamente(self, request, pk=None):
        nota = self.get_object()
        stats = conciliar_automaticamente(nota, user=request.user, request=request)
        return Response({"resultado": stats, "resumo": resumo_conciliacao(nota)}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="resumo-conciliacao")
    def resumo_conciliacao_xml(self, request, pk=None):
        nota = self.get_object()
        return Response(resumo_conciliacao(nota), status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="item-xml-candidatos")
    def candidatos_xml(self, request, pk=None):
        nota = self.get_object()
        item = nota.itens_xml.get(pk=request.query_params.get("item"))
        produtos = candidatos_item(item, request.query_params.get("q") or request.query_params.get("produto"))
        return Response(
            [
                {
                    "id": produto.pk,
                    "referencia": produto.referencia,
                    "descricao": produto.descricao,
                    "unidade_interna": getattr(produto.unidade, "Codigo", ""),
                }
                for produto in produtos
            ],
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="item-xml-conciliar")
    def conciliar_item_xml(self, request, pk=None):
        nota = self.get_object()
        item = nota.itens_xml.get(pk=request.data.get("item"))
        item = conciliar_manual(item, request.data.get("produto"), user=request.user, request=request)
        return Response(NotaFiscalEntradaItemXmlSerializer(item).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="item-xml-conferir")
    def conferir_item_xml(self, request, pk=None):
        nota = self.get_object()
        item = nota.itens_xml.get(pk=request.data.get("item"))
        item, divergencia = registrar_conferencia(item, request.data.get("quantidade_recebida"), user=request.user, request=request)
        return Response(
            {
                "item": NotaFiscalEntradaItemXmlSerializer(item).data,
                "divergencia": NotaFiscalEntradaDivergenciaXmlSerializer(divergencia).data if divergencia else None,
                "resumo": resumo_conferencia(nota),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="conferir-itens-xml")
    @transaction.atomic
    def conferir_itens_xml(self, request, pk=None):
        nota = self.get_object()
        itens = request.data.get("itens") or []
        if not isinstance(itens, list) or not itens:
            raise ValidationError({"itens": "Informe a lista de itens para conferência."})
        conferidos = []
        for row in itens:
            item = nota.itens_xml.get(pk=row.get("item"))
            item, _ = registrar_conferencia(item, row.get("quantidade_recebida"), user=request.user, request=request)
            conferidos.append(item)
        return Response(
            {
                "itens": NotaFiscalEntradaItemXmlSerializer(conferidos, many=True).data,
                "resumo": resumo_conferencia(nota),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"], url_path="resumo-conferencia")
    def resumo_conferencia_xml(self, request, pk=None):
        nota = self.get_object()
        return Response(resumo_conferencia(nota), status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="divergencias-xml")
    def divergencias_xml(self, request, pk=None):
        nota = self.get_object()
        qs = nota.divergencias_xml.select_related("produto", "item_xml", "fornecedor").order_by("item_xml__numero_item")
        status_filtro = request.query_params.get("status")
        if status_filtro:
            qs = qs.filter(status=str(status_filtro).upper())
        return Response(NotaFiscalEntradaDivergenciaXmlSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="resolver-divergencia-xml")
    def resolver_divergencia_xml(self, request, pk=None):
        nota = self.get_object()
        divergencia = nota.divergencias_xml.select_related("nota").get(pk=request.data.get("divergencia"))
        divergencia = resolver_divergencia(divergencia, user=request.user, request=request)
        return Response(NotaFiscalEntradaDivergenciaXmlSerializer(divergencia).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="eventos-fiscais")
    def eventos_fiscais(self, request, pk=None):
        nota = self.get_object()
        qs = nota.eventos_fiscais.order_by("tipo_evento", "sequencia", "criado_em")
        return Response(NotaFiscalEntradaEventoSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="cobranca-financeira")
    def cobranca_financeira(self, request, pk=None):
        nota = self.get_object()
        return Response(self._cobranca_financeira_xml(nota), status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="vincular-forma-pagamento-fiscal")
    @transaction.atomic
    def vincular_forma_pagamento_fiscal(self, request, pk=None):
        nota = self.get_object()
        codigo_tpag = str(request.data.get("codigo_tpag") or "").strip().zfill(2)
        forma_id = request.data.get("forma_pagamento")
        pagamentos = self._pagamentos_xml(nota)
        pagamento = next((pag for pag in pagamentos if pag["codigo_tpag"] == codigo_tpag), None)
        if not pagamento:
            raise ValidationError({"codigo_tpag": "Forma fiscal não encontrada no XML da NF-e."})
        forma = FormaPagamento.objects.filter(pk=forma_id, empresa=nota.empresa, ativo=True).first()
        if not forma:
            raise ValidationError({"forma_pagamento": "Forma de Pagamento Sysvar ativa não encontrada para a empresa."})
        mapa, _ = FormaPagamentoFiscalMap.objects.update_or_create(
            empresa=nota.empresa,
            codigo_tpag=codigo_tpag,
            defaults={
                "descricao_fiscal": pagamento["descricao_tpag"],
                "forma_pagamento": forma,
                "ativo": True,
                "criado_por": request.user if getattr(request.user, "is_authenticated", False) else None,
            },
        )
        AuditService.success(
            AuditAction.OBJECT_CREATED,
            category=AuditCategory.FISCAL,
            request=request,
            user=getattr(request, "user", None),
            instance=mapa,
            after={"empresa": nota.empresa_id, "codigo_tpag": codigo_tpag, "forma_pagamento": forma.codigo},
            metadata={"legacy_action": "vincular_forma_pagamento_fiscal_nfe"},
        )
        return Response({"vinculo": FormaPagamentoFiscalMapSerializer(mapa).data, "cobranca": self._cobranca_financeira_xml(nota)}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="importar-evento-xml", parser_classes=[parsers.MultiPartParser])
    @transaction.atomic
    def importar_evento_xml(self, request, pk=None):
        nota = self.get_queryset().select_for_update().get(pk=pk)
        arquivo = request.FILES.get("arquivo") or request.FILES.get("xml")
        if not arquivo:
            raise ValidationError({"arquivo": "Informe o XML do evento da NF-e."})
        original_bytes = arquivo.read()
        evento = parse_nfe_evento_xml(original_bytes)
        if evento.chave_acesso != nota.chave_acesso:
            raise ValidationError({"chave_acesso": "Evento não pertence à chave de acesso desta NF-e."})
        try:
            xml_original = original_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValidationError({"arquivo": "XML deve estar em codificação textual válida."}) from exc
        obj, created = NotaFiscalEntradaEvento.objects.get_or_create(
            empresa=nota.empresa,
            nota=nota,
            chave_acesso=evento.chave_acesso,
            tipo_evento=evento.tipo_evento,
            sequencia=evento.sequencia,
            protocolo=evento.protocolo,
            defaults={
                "id_evento": evento.id_evento,
                "tipo_evento_descricao": evento.tipo_evento_descricao,
                "data_hora_evento": evento.data_hora_evento,
                "cstat": evento.cstat,
                "xmotivo": evento.xmotivo,
                "ambiente": evento.ambiente,
                "xml_original": xml_original,
                "situacao_processamento": NotaFiscalEntradaEvento.SituacaoProcessamento.PROCESSADO if evento.cstat == "135" else NotaFiscalEntradaEvento.SituacaoProcessamento.REGISTRADO,
            },
        )
        if created and evento.tipo_evento == "110111" and evento.cstat == "135":
            nota.situacao_fiscal = NotaFiscalEntrada.SituacaoFiscal.CANCELADA
            nota.save(update_fields=["situacao_fiscal", "atualizado_em"])
        if created:
            AuditService.success(
                AuditAction.OBJECT_CREATED,
                category=AuditCategory.FISCAL,
                request=request,
                user=getattr(request, "user", None),
                instance=obj,
                after={"nota": nota.pk, "chave_acesso": evento.chave_acesso, "tipo_evento": evento.tipo_evento, "sequencia": evento.sequencia, "cstat": evento.cstat},
                metadata={"legacy_action": "importar_evento_xml_nfe"},
            )
        return Response(NotaFiscalEntradaEventoSerializer(obj).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    def _identificar_fornecedor(self, empresa_id, documento):
        documento = only_digits(documento)
        if not documento:
            raise ValidationError({"fornecedor": "Documento do emitente ausente no XML."})
        fornecedor = (
            Fornecedor.objects.filter(empresa_id=empresa_id)
            .filter(Q(documento=documento) | Q(cnpj=documento))
            .first()
        )
        if not fornecedor:
            raise ValidationError({"fornecedor": "Fornecedor do XML não cadastrado para a empresa."})
        return fornecedor

    def _identificar_loja(self, empresa_id, documento):
        documento = only_digits(documento)
        if not documento:
            raise ValidationError({"loja": "Documento do destinatário ausente no XML."})
        loja = Loja.objects.filter(empresa_id=empresa_id, cnpj=documento).first()
        if not loja:
            raise ValidationError({"loja": "Destinatário do XML não corresponde a uma loja da empresa."})
        return loja

    @transaction.atomic
    def perform_create(self, serializer):
        self._validar_nota_empresa(serializer.validated_data)
        self._validar_duplicidade_nota(serializer.validated_data)
        try:
            serializer.save()
        except IntegrityError as exc:
            raise ValidationError({"chave_acesso": "Chave de acesso já utilizada em outra nota fiscal de entrada."}) from exc

    @transaction.atomic
    def perform_update(self, serializer):
        data = {**serializer.validated_data}
        data.setdefault("empresa", serializer.instance.empresa)
        data.setdefault("loja", serializer.instance.loja)
        data.setdefault("fornecedor", serializer.instance.fornecedor)
        data.setdefault("pedido_compra", serializer.instance.pedido_compra)
        data.setdefault("modelo", serializer.instance.modelo)
        data.setdefault("serie", serializer.instance.serie)
        data.setdefault("numero", serializer.instance.numero)
        data.setdefault("chave_acesso", serializer.instance.chave_acesso)
        self._validar_nota_empresa(data)
        self._validar_duplicidade_nota(data, instance=serializer.instance)
        try:
            serializer.save()
        except IntegrityError as exc:
            raise ValidationError({"chave_acesso": "Chave de acesso já utilizada em outra nota fiscal de entrada."}) from exc

    def destroy(self, request, *args, **kwargs):
        return Response(
            {"detail": "Exclusão física de nota fiscal de entrada não é permitida. Utilize o cancelamento da nota."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def _validar_nota_empresa(self, data):
        pedido = data.get("pedido_compra")
        empresa = data.get("empresa")
        loja = data.get("loja")
        fornecedor = data.get("fornecedor")
        empresa_id = getattr(empresa, "id", None) or getattr(pedido, "empresa_id", None)
        user_empresa_id = self._empresa_id_usuario()
        if not user_empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        if user_empresa_id and empresa_id and int(user_empresa_id) != empresa_id:
            field = "pedido_compra" if pedido else "empresa"
            raise ValidationError({field: "Nota fiscal pertence a outra empresa."})
        if pedido and pedido.empresa_id != empresa_id:
            raise ValidationError({"pedido_compra": "Pedido pertence a outra empresa."})
        if pedido and (pedido.loja_id != getattr(loja, "id", None) or pedido.fornecedor_id != getattr(fornecedor, "id", None)):
            raise ValidationError({"pedido_compra": "Empresa, loja e fornecedor da nota devem ser coerentes com o pedido."})
        if loja and loja.empresa_id != empresa_id:
            raise ValidationError({"loja": "A loja do pedido pertence a outra empresa."})
        if fornecedor and fornecedor.empresa_id != empresa_id:
            raise ValidationError({"fornecedor": "Fornecedor pertence a outra empresa."})

    def _validar_duplicidade_nota(self, data, instance=None):
        empresa = data.get("empresa")
        fornecedor = data.get("fornecedor")
        empresa_id = getattr(empresa, "id", None)
        fornecedor_id = getattr(fornecedor, "id", None)
        if not empresa_id or not fornecedor_id:
            return
        modelo = str(data.get("modelo") or "55").strip()
        serie = str(data.get("serie") or "").strip()
        numero = str(data.get("numero") or "").strip()
        chave = data.get("chave_acesso")

        duplicadas = NotaFiscalEntrada.objects.select_for_update().filter(
            empresa_id=empresa_id,
            fornecedor_id=fornecedor_id,
            modelo=modelo,
            serie=serie,
            numero=numero,
        )
        if instance:
            duplicadas = duplicadas.exclude(pk=instance.pk)
        if duplicadas.exists():
            raise ValidationError(
                {"numero": "Nota fiscal de entrada já cadastrada para esta empresa, fornecedor, modelo, série e número."}
            )

        if chave:
            chave_duplicada = NotaFiscalEntrada.objects.select_for_update().filter(chave_acesso=chave)
            if instance:
                chave_duplicada = chave_duplicada.exclude(pk=instance.pk)
            if chave_duplicada.exists():
                raise ValidationError({"chave_acesso": "Chave de acesso já utilizada em outra nota fiscal de entrada."})

    @action(detail=True, methods=["post"], url_path="fechar")
    @transaction.atomic
    def fechar(self, request, pk=None):
        nota = self.get_queryset().select_for_update().get(pk=pk)
        if nota.status != NotaFiscalEntrada.Status.ABERTA:
            return Response({"detail": "Somente notas abertas podem ser fechadas."}, status=status.HTTP_400_BAD_REQUEST)
        if nota.xml_importado:
            try:
                resultado = self._fechar_xml(nota, request)
            except ValueError as exc:
                transaction.set_rollback(True)
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            data = self.get_serializer(nota).data
            data.update(resultado)
            return Response(data, status=status.HTTP_200_OK)
        if not nota.itens.exists():
            return Response({"detail": "Inclua ao menos um item antes de fechar a nota."}, status=status.HTTP_400_BAD_REQUEST)

        nota.recalcular_totais()
        before = nota.status
        nota.status = NotaFiscalEntrada.Status.FECHADA
        nota.save(update_fields=["status", "atualizado_em"])
        custos_produtos = self._atualizar_custos_produtos_nao_revenda(nota)

        try:
            estoque = self._movimentar_estoque_entrada(nota)
        except ValueError as exc:
            transaction.set_rollback(True)
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        financeiro = self._vincular_financeiro(nota)
        recebimento = self._atualizar_recebimento_pedido(nota)
        necessidades = self._recalcular_necessidades_vinculadas(nota)
        _audit("notafiscalentrada", nota.pk, {"status": [before, nota.status]}, request, action="fechar")
        data = self.get_serializer(nota).data
        data["financeiro"] = financeiro
        data["estoque"] = estoque
        data["custos_produtos"] = custos_produtos
        data["recebimento_pedido"] = recebimento
        data["necessidades"] = necessidades
        return Response(data, status=status.HTTP_200_OK)

    def _fechar_xml(self, nota, request):
        self._validar_pronto_xml(nota)
        estoque = self._movimentar_estoque_xml(nota)
        custos_produtos = self._atualizar_custos_xml(nota)
        financeiro = self._vincular_financeiro_xml(nota)
        recebimento = self._atualizar_recebimento_pedido_xml(nota)
        necessidades = self._recalcular_necessidades_vinculadas(nota)
        before = nota.status
        nota.status = NotaFiscalEntrada.Status.FECHADA
        nota.save(update_fields=["status", "atualizado_em"])
        resumo_conf = resumo_conferencia(nota)
        AuditService.success(
            AuditAction.OBJECT_UPDATED,
            category=AuditCategory.FISCAL,
            request=request,
            user=getattr(request, "user", None),
            instance=nota,
            after={
                "empresa": nota.empresa_id,
                "loja": nota.loja_id,
                "fornecedor": nota.fornecedor_id,
                "pedido_compra": nota.pedido_compra_id,
                "itens": nota.itens_xml.count(),
                "valor_total": str(nota.valor_total),
                "divergencia_pendente": resumo_conf["possui_divergencia_pendente"],
                "valor_divergente": resumo_conf["valor_divergente_total"],
                "status": nota.status,
                "estoque": estoque,
                "financeiro": financeiro,
            },
            metadata={"legacy_action": "fechar_xml", "status_anterior": before},
        )
        return {
            "financeiro": financeiro,
            "estoque": estoque,
            "custos_produtos": custos_produtos,
            "recebimento_pedido": recebimento,
            "necessidades": necessidades,
        }

    def _validar_pronto_xml(self, nota):
        if nota.situacao_fiscal != NotaFiscalEntrada.SituacaoFiscal.AUTORIZADA:
            raise ValueError("A NF-e XML precisa estar fiscalmente autorizada para efetivação operacional.")
        if nota.finalidade_nfe and nota.finalidade_nfe != "1":
            raise ValueError("NF-e com finalidade fiscal especial requer fluxo específico antes da efetivação operacional.")
        if not nota.pedido_compra_id:
            cobranca = self._cobranca_financeira_xml(nota)
            if not cobranca["financeiro_pronto"]:
                raise ValueError(cobranca["pendencias"][0])
        itens = list(
            nota.itens_xml.select_for_update()
            .select_related("produto", "produto_fornecedor", "pedido_item")
            .order_by("numero_item")
        )
        if not itens:
            raise ValueError("NF-e XML não possui itens importados.")
        if any(not item.produto_id for item in itens):
            raise ValueError("Concilie todos os itens XML da NF-e antes de fechar a nota.")
        if any(item.quantidade_recebida is None for item in itens):
            raise ValueError("Informe a conferência física de todos os itens XML da NF-e antes de fechar a nota.")
        for item in itens:
            if Decimal(item.quantidade_recebida or 0) < 0 or Decimal(item.quantidade_recebida or 0) > Decimal(item.quantidade_comercial or 0):
                raise ValueError("Quantidade recebida inválida para item XML.")
            if item.produto.empresa_id != nota.empresa_id:
                raise ValueError("Produto conciliado pertence a outra empresa.")
            if item.produto.tipo_produto == "1":
                self._codigo_estoque_item_xml(item)
            if not item.conversao_pronta:
                raise ValueError("Resolva as pendências de conversão dos itens XML da NF-e antes de fechar a nota.")
            if nota.pedido_compra_id:
                if not item.pedido_item_id or item.pedido_item.pedido_id != nota.pedido_compra_id:
                    raise ValueError("Item XML sem vínculo seguro com item do Pedido de Compra.")
                divergencias = [div for div in item.divergencias_pedido() if div.get("bloqueia")]
                if divergencias:
                    raise ValueError(divergencias[0]["mensagem"])
                saldo = Decimal(item.pedido_item.qtd or 0) - self._qtd_recebida_item(item.pedido_item)
                qtd_fiscal = Decimal(item.produto_fornecedor.converter_quantidade_fornecedor(item.quantidade_comercial or 0))
                if qtd_fiscal > saldo:
                    raise ValueError("Quantidade recebida do XML ultrapassa o saldo permitido do Pedido.")

    def _movimentar_estoque_xml(self, nota):
        documento = self._documento_estoque(nota, "ENTRADA")
        if (
            EstoqueMovimentacao.objects.filter(documento=documento, tipo=EstoqueMovimentacao.TIPO_ENTRADA).exists()
            or ProdutoUsoConsumoMovimentacao.objects.filter(documento=documento, tipo=ProdutoUsoConsumoMovimentacao.TIPO_ENTRADA).exists()
        ):
            return {"disponivel": True, "movimentos": 0, "ja_movimentada": True}
        movimentos = 0
        for item in nota.itens_xml.select_related("produto", "produto__unidade", "produto_fornecedor").order_by("numero_item"):
            qtd = Decimal(item.produto_fornecedor.converter_quantidade_fornecedor(item.quantidade_comercial or 0))
            item.unidade_fornecedor_efetivada = item.produto_fornecedor.unidade_fornecedor
            item.fator_conversao_efetivado = item.produto_fornecedor.fator_conversao
            item.quantidade_interna_efetivada = qtd
            item.efetivado_em = timezone.now()
            item.save(update_fields=["unidade_fornecedor_efetivada", "fator_conversao_efetivado", "quantidade_interna_efetivada", "efetivado_em"])
            if qtd <= 0:
                continue
            if item.produto.tipo_produto == "2":
                movimentos += self._movimentar_produto_xml_uso_consumo(nota, item, qtd, documento)
            else:
                movimentos += self._movimentar_produto_xml_estoque(nota, item, qtd, documento)
        return {"disponivel": True, "movimentos": movimentos}

    def _movimentar_produto_xml_uso_consumo(self, nota, item, qtd, documento):
        estoque, _ = ProdutoUsoConsumoEstoque.objects.select_for_update().get_or_create(
            empresa=nota.empresa,
            loja=nota.loja,
            produto=item.produto,
            defaults={"saldo": Decimal("0")},
        )
        anterior = Decimal(estoque.saldo or 0)
        posterior = anterior + qtd
        estoque.saldo = posterior
        estoque.save(update_fields=["saldo", "atualizado_em"])
        ProdutoUsoConsumoMovimentacao.objects.create(
            empresa=nota.empresa,
            produto=item.produto,
            loja=nota.loja,
            tipo=ProdutoUsoConsumoMovimentacao.TIPO_ENTRADA,
            quantidade=_q3(qtd),
            saldo_anterior=anterior,
            saldo_posterior=posterior,
            usuario=getattr(nota, "criado_por", None),
            motivo="Nota fiscal de entrada XML",
            destino=nota.loja.nome_loja,
            documento=documento,
            origem=f"NFE:{nota.pk};XML_ITEM:{item.pk}",
        )
        return 1

    def _movimentar_produto_xml_estoque(self, nota, item, qtd, documento):
        produto = item.produto
        codigo = self._codigo_estoque_item_xml(item)
        custo_movimento = _q4(item.valor_unitario_comercial or produto.custo_medio or produto.custo_ultima_compra or produto.custo_original or 0)
        estoque, _ = Estoque.objects.select_for_update().get_or_create(
            CodigodeBarra=codigo,
            Idloja=nota.loja,
            defaults={"referencia": produto.referencia or "", "Estoque": 0, "reserva": 0},
        )
        anterior = Decimal(estoque.Estoque or 0)
        posterior = anterior + qtd
        estoque.referencia = produto.referencia or estoque.referencia
        estoque.Estoque = posterior
        estoque.reserva = estoque.reserva or 0
        estoque.save(update_fields=["referencia", "Estoque", "reserva"])
        EstoqueMovimentacao.objects.create(
            Idloja=nota.loja,
            CodigodeBarra=codigo,
            referencia=produto.referencia or "",
            tipo=EstoqueMovimentacao.TIPO_ENTRADA,
            quantidade=_q3(qtd),
            custo_unitario=custo_movimento,
            custo_total=_money(qtd * custo_movimento),
            custo_medio_apos=_q4(produto.custo_medio or produto.custo_ultima_compra or produto.custo_original or custo_movimento),
            saldo_anterior=anterior,
            saldo_posterior=posterior,
            origem=EstoqueMovimentacao.ORIGEM_NFE,
            documento=documento,
            observacao=f"Nota fiscal de entrada XML {nota.numero};ITEM:{item.pk}",
        )
        return 1

    def _atualizar_custos_xml(self, nota):
        atualizados = 0
        for item in nota.itens_xml.select_related("produto"):
            produto = item.produto
            qtd = Decimal(item.quantidade_interna_efetivada or 0)
            if not produto or qtd <= 0 or produto.tipo_produto not in ("2", "4"):
                continue
            custo_entrada = _q4(item.valor_unitario_comercial or 0)
            if custo_entrada <= 0:
                continue
            if not Decimal(produto.custo_original or 0):
                produto.custo_original = custo_entrada
            produto.custo_ultima_compra = custo_entrada
            produto.custo_medio = custo_entrada
            produto.save(update_fields=["custo_original", "custo_ultima_compra", "custo_medio"])
            atualizados += 1
        return {"atualizados": atualizados}

    def _vincular_financeiro_xml(self, nota):
        financeiro = self._vincular_financeiro(nota) if nota.pedido_compra_id else self._criar_financeiro_xml_sem_pedido(nota)
        self._aplicar_alerta_financeiro_divergencia(nota)
        financeiro["alerta_divergencia"] = nota.resumo_conferencia_xml()["possui_divergencia_pendente"]
        return financeiro

    def _criar_financeiro_xml_sem_pedido(self, nota):
        if not FIN_OK:
            return {"disponivel": False, "titulos_criados": 0, "parcelas_efetivadas": 0}
        if Pagar.objects.filter(nfe_id=nota.pk).exists():
            return {"disponivel": True, "titulos_criados": 0, "parcelas_efetivadas": 0, "ja_vinculado": True}
        cobranca = self._cobranca_financeira_xml(nota)
        parcelas_xml = cobranca["parcelas"] if cobranca["usa_duplicatas"] else []
        forma_codigo = cobranca.get("forma_pagamento_sysvar_codigo")
        natureza = self._natureza_padrao_compra(nota.empresa)
        titulo = Pagar.objects.create(
            empresa=nota.empresa,
            idloja=nota.loja,
            idfornecedor=nota.fornecedor,
            Titulo=str(nota.numero)[:60],
            Documento=_documento_nota(nota),
            Data_emissao=nota.dt_emissao,
            Valor_total=_money(nota.valor_total or 0),
            Previsao=False,
            FormaPagamento=forma_codigo,
            Idnatureza=natureza,
            pedido_compra=None,
            nfe_id=nota.pk,
        )
        if parcelas_xml:
            for idx, parcela in enumerate(parcelas_xml, start=1):
                PagarItem.objects.create(
                    Idpagar=titulo,
                    parcela_n=idx,
                    status=PagarItem.STATUS_EFETIVO,
                    Data_vencimento=date.fromisoformat(parcela["vencimento"]),
                    valor_parcela=_money(parcela["valor"]),
                    FormaPagamento=forma_codigo,
                    Previsao=False,
                    Idnatureza=natureza,
                )
            return {"disponivel": True, "titulos_criados": 1, "parcelas_efetivadas": len(parcelas_xml), "origem": "xml_duplicatas"}
        PagarItem.objects.create(
            Idpagar=titulo,
            parcela_n=1,
            status=PagarItem.STATUS_EFETIVO,
            Data_vencimento=nota.dt_entrada,
            valor_parcela=titulo.Valor_total,
            FormaPagamento=None,
            Previsao=False,
            Idnatureza=natureza,
        )
        return {"disponivel": True, "titulos_criados": 1, "parcelas_efetivadas": 1, "origem": "fallback"}

    def _cobranca_financeira_xml(self, nota):
        parcelas = self._duplicatas_xml(nota)
        pagamentos = self._pagamentos_xml(nota)
        forma = self._forma_pagamento_fiscal_conciliada(nota, pagamentos)
        soma = _money(sum(_money(p["valor"]) for p in parcelas))
        valor_nf = _money(nota.valor_total or 0)
        pendencias = []
        usa_duplicatas = bool(parcelas)
        if usa_duplicatas and abs(soma - valor_nf) > Decimal("0.01"):
            pendencias.append(f"Soma das duplicatas ({soma}) difere do total da NF-e ({valor_nf}).")
        if usa_duplicatas and len(pagamentos) == 1 and not forma:
            pendencias.append("Concilie a forma de pagamento do XML antes de efetivar a NF-e.")
        if usa_duplicatas and len(pagamentos) > 1:
            pendencias.append("XML possui múltiplas formas de pagamento; defina regra financeira antes de efetivar.")
        return {
            "usa_duplicatas": usa_duplicatas,
            "valor_fatura": str(valor_nf),
            "parcelas": [{"numero": p["numero"], "vencimento": p["vencimento"], "valor": str(_money(p["valor"]))} for p in parcelas],
            "pagamentos": pagamentos,
            "forma_pagamento_conciliada": bool(forma),
            "forma_pagamento_sysvar_id": getattr(forma, "pk", None),
            "forma_pagamento_sysvar_codigo": getattr(forma, "codigo", None),
            "forma_pagamento_sysvar_descricao": getattr(forma, "descricao", None),
            "forma_pagamento_sysvar_tipo": getattr(forma, "tipo", None),
            "sugestoes": self._sugestoes_forma_pagamento(nota, pagamentos),
            "pendencias": pendencias,
            "financeiro_pronto": not pendencias,
        }

    def _duplicatas_xml(self, nota):
        cobr = nota.cobranca_fiscal or {}
        dup = cobr.get("dup") if isinstance(cobr, dict) else None
        rows = dup if isinstance(dup, list) else ([dup] if isinstance(dup, dict) else [])
        parcelas = []
        for idx, row in enumerate(rows, start=1):
            venc = str(row.get("dVenc") or "").strip()
            valor = _money(row.get("vDup"))
            numero = str(row.get("nDup") or idx).strip()
            if not venc or valor <= 0:
                continue
            try:
                date.fromisoformat(venc)
            except ValueError as exc:
                raise ValueError("Duplicata do XML possui data de vencimento inválida.") from exc
            parcelas.append({"numero": numero, "vencimento": venc, "valor": valor})
        return parcelas

    def _pagamentos_xml(self, nota):
        rows = nota.pagamentos_fiscais if isinstance(nota.pagamentos_fiscais, list) else []
        pagamentos = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            codigo = str(row.get("tPag") or "").strip().zfill(2)
            if not codigo:
                continue
            pagamentos.append({"codigo_tpag": codigo, "descricao_tpag": row.get("descricao_tpag") or "", "valor": str(_money(row.get("vPag")))})
        return pagamentos

    def _forma_pagamento_fiscal_conciliada(self, nota, pagamentos):
        if not FIN_OK or not pagamentos or len(pagamentos) != 1:
            return None
        mapa = FormaPagamentoFiscalMap.objects.select_related("forma_pagamento").filter(
            empresa=nota.empresa,
            codigo_tpag=pagamentos[0]["codigo_tpag"],
            ativo=True,
            forma_pagamento__ativo=True,
            forma_pagamento__empresa=nota.empresa,
        ).first()
        return mapa.forma_pagamento if mapa else None

    def _sugestoes_forma_pagamento(self, nota, pagamentos):
        if not FIN_OK or not pagamentos or len(pagamentos) != 1:
            return []
        tipo_sugerido = {"15": "BOLETO", "17": "PIX", "18": "TRANSFERENCIA", "01": "DINHEIRO"}.get(pagamentos[0]["codigo_tpag"])
        if not tipo_sugerido:
            return []
        qs = FormaPagamento.objects.filter(empresa=nota.empresa, ativo=True, tipo=tipo_sugerido).order_by("codigo")
        if qs.count() != 1:
            return []
        forma = qs.first()
        return [{"id": forma.pk, "codigo": forma.codigo, "descricao": forma.descricao, "tipo": forma.tipo}]

    def _natureza_padrao_compra(self, empresa):
        natureza = Nat_Lancamento.objects.filter(empresa=empresa, natureza_operacao="DESPESA", ativo=True).first()
        if natureza:
            return natureza
        return Nat_Lancamento.objects.create(
            empresa=empresa,
            codigo="COMPRA",
            categoria_principal="Compras",
            subcategoria="Mercadorias",
            descricao="Compras de mercadorias",
            tipo="SAIDA",
            status="ATIVO",
            tipo_natureza="DESPESA",
            natureza_operacao="DESPESA",
            movimenta_financeiro=True,
            entra_dre=True,
            ativo=True,
        )

    def _aplicar_alerta_financeiro_divergencia(self, nota):
        if not FIN_OK:
            return
        resumo = nota.resumo_conferencia_xml()
        Pagar.objects.filter(nfe_id=nota.pk).update(
            alerta_divergencia_mercadoria=resumo["possui_divergencia_pendente"],
            valor_divergencia_mercadoria=Decimal(resumo["valor_divergente_total"]),
        )

    def _atualizar_recebimento_pedido_xml(self, nota):
        if not nota.pedido_compra_id:
            return {"status_pedido": None, "itens_atualizados": 0}
        return self._atualizar_recebimento_pedido(nota)

    @action(detail=True, methods=["post"], url_path="cancelar")
    @transaction.atomic
    def cancelar(self, request, pk=None):
        nota = self.filter_queryset(self.get_queryset()).select_for_update().get(pk=pk)
        motivo = str(request.data.get("motivo") or "").strip()
        if not motivo:
            return Response({"motivo": "Informe o motivo do cancelamento."}, status=status.HTTP_400_BAD_REQUEST)
        if nota.status == NotaFiscalEntrada.Status.CANCELADA:
            return Response({"detail": "Nota fiscal de entrada já está cancelada."}, status=status.HTTP_400_BAD_REQUEST)

        before = nota.status
        estoque = {"disponivel": True, "movimentos": 0}
        analise = self._analisar_cancelamento(nota)
        if analise["bloqueios"]:
            return Response({"detail": analise["bloqueios"][0], "analise": analise}, status=status.HTTP_400_BAD_REQUEST)
        if analise["avisos"] and not self._confirmacao_avisos(request):
            return Response(
                {"detail": "Confirme os avisos para cancelar a NF.", "analise": analise},
                status=status.HTTP_409_CONFLICT,
            )
        if nota.status == NotaFiscalEntrada.Status.FECHADA:
            try:
                financeiro = self._cancelar_financeiro_nf(nota)
                estoque = self._movimentar_estoque_cancelamento(nota, motivo, request)
                custos = self._recalcular_custos_apos_cancelamento(nota)
                divergencias = self._encerrar_divergencias_cancelamento(nota, request)
            except ValueError as exc:
                transaction.set_rollback(True)
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        else:
            financeiro = {"disponivel": FIN_OK, "titulos_cancelados": 0}
            custos = {"skus_atualizados": 0, "produtos_atualizados": 0}
            divergencias = {"encerradas": 0}

        recebimento = self._atualizar_recebimento_pedido(nota, excluir_nota=nota)
        necessidades = self._recalcular_necessidades_vinculadas(nota)
        nota.status = NotaFiscalEntrada.Status.CANCELADA
        nota.motivo_cancelamento = motivo
        nota.cancelado_por = request.user if getattr(request.user, "is_authenticated", False) else None
        nota.cancelado_em = timezone.now()
        nota.save(update_fields=["status", "motivo_cancelamento", "cancelado_por", "cancelado_em", "atualizado_em"])
        self._auditar_cancelamento(nota, request, before, motivo, financeiro, estoque, custos, recebimento, necessidades, divergencias, analise)
        data = self.get_serializer(nota).data
        data["estoque"] = estoque
        data["financeiro"] = financeiro
        data["custos"] = custos
        data["divergencias"] = divergencias
        data["analise_cancelamento"] = analise
        data["recebimento_pedido"] = recebimento
        data["necessidades"] = necessidades
        return Response(data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get", "post"], url_path="analisar-cancelamento")
    @transaction.atomic
    def analisar_cancelamento(self, request, pk=None):
        nota = self.filter_queryset(self.get_queryset()).select_for_update().get(pk=pk)
        return Response(self._analisar_cancelamento(nota), status=status.HTTP_200_OK)

    def _confirmacao_avisos(self, request):
        return str(request.data.get("confirmar_avisos") or request.data.get("confirmacao_avisos") or "").strip().lower() in {"1", "true", "sim", "s"}

    def _analisar_cancelamento(self, nota):
        bloqueios = []
        avisos = []
        if nota.status == NotaFiscalEntrada.Status.CANCELADA:
            bloqueios.append("Nota fiscal de entrada já está cancelada.")
        elif nota.status != NotaFiscalEntrada.Status.FECHADA:
            return {"pode_cancelar": True, "bloqueios": [], "avisos": [], "pedido": nota.pedido_compra_id, "valor_financeiro": "0.00"}
        financeiro = self._resumo_financeiro_cancelamento(nota)
        if financeiro["baixado"]:
            bloqueios.append("O título financeiro vinculado à NF já possui baixa. Reverta/levante a baixa no Financeiro antes de cancelar a NF.")
        if financeiro["movimentacao_ativa"] and not financeiro["baixado"]:
            bloqueios.append("O título financeiro vinculado à NF já possui movimentação financeira ativa. Reverta/levante a baixa no Financeiro antes de cancelar a NF.")
        avisos.extend(self._avisos_estoque_cancelamento(nota))
        return {
            "pode_cancelar": not bloqueios,
            "bloqueios": bloqueios,
            "avisos": avisos,
            "pedido": nota.pedido_compra_id,
            "valor_financeiro": str(financeiro["valor"]),
        }

    def _resumo_financeiro_cancelamento(self, nota):
        if not FIN_OK:
            return {"valor": Decimal("0.00"), "baixado": False, "movimentacao_ativa": False}
        titulos = Pagar.objects.select_for_update().filter(nfe_id=nota.pk, pedido_compra=nota.pedido_compra_id)
        itens = PagarItem.objects.select_for_update().filter(Idpagar__in=titulos)
        baixado = (
            itens.filter(status=PagarItem.STATUS_BAIXADO).exists()
            or itens.filter(data_baixa__isnull=False).exists()
            or itens.filter(valor_baixa__gt=0).exists()
        )
        movimentacao_ativa = MovimentacaoFinanceira.objects.filter(pagar_item__in=itens).exclude(
            status=MovimentacaoFinanceira.STATUS_CANCELADA
        ).exists()
        valor = _money(titulos.aggregate(total=Sum("Valor_total"))["total"] or 0)
        return {"valor": valor, "baixado": baixado, "movimentacao_ativa": movimentacao_ativa}

    def _avisos_estoque_cancelamento(self, nota):
        documento_entrada = self._documento_estoque(nota, "ENTRADA")
        avisos = []
        for alvo in self._alvos_estoque_cancelamento(nota):
            saldo = Decimal(alvo["saldo"] or 0)
            qtd = Decimal(alvo["quantidade"] or 0)
            if saldo - qtd < 0:
                avisos.append(
                    {
                        "tipo": "SALDO_NEGATIVO",
                        "produto": alvo.get("produto"),
                        "codigo": alvo.get("codigo"),
                        "saldo_atual": str(saldo),
                        "quantidade_estorno": str(qtd),
                        "saldo_previsto": str(saldo - qtd),
                    }
                )
            if alvo["uso_consumo"]:
                entrada = ProdutoUsoConsumoMovimentacao.objects.filter(
                    documento=documento_entrada,
                    produto_id=alvo["produto"],
                    loja_id=nota.loja_id,
                    tipo=ProdutoUsoConsumoMovimentacao.TIPO_ENTRADA,
                ).order_by("data_movimento", "id").first()
                posterior = entrada and ProdutoUsoConsumoMovimentacao.objects.filter(
                    produto_id=alvo["produto"],
                    loja_id=nota.loja_id,
                    data_movimento__gt=entrada.data_movimento,
                ).exclude(documento=self._documento_estoque(nota, "CANCEL")).exists()
            else:
                entrada = EstoqueMovimentacao.objects.filter(
                    documento=documento_entrada,
                    CodigodeBarra=alvo["codigo"],
                    Idloja_id=nota.loja_id,
                    tipo=EstoqueMovimentacao.TIPO_ENTRADA,
                ).order_by("data_movimento", "Idmovimento").first()
                posterior = entrada and EstoqueMovimentacao.objects.filter(
                    CodigodeBarra=alvo["codigo"],
                    Idloja_id=nota.loja_id,
                    data_movimento__gt=entrada.data_movimento,
                ).exclude(documento=self._documento_estoque(nota, "CANCEL")).exists()
            if posterior:
                avisos.append({"tipo": "MOVIMENTACAO_POSTERIOR", "produto": alvo.get("produto"), "codigo": alvo.get("codigo")})
        return avisos

    def _alvos_estoque_cancelamento(self, nota):
        alvos = []
        if nota.xml_importado:
            for item in nota.itens_xml.select_related("produto"):
                produto = item.produto
                qtd = _q3(item.quantidade_interna_efetivada or 0)
                if not produto or qtd <= 0:
                    continue
                if produto.tipo_produto == "2":
                    saldo = ProdutoUsoConsumoEstoque.objects.filter(empresa=nota.empresa, loja=nota.loja, produto=produto).values_list("saldo", flat=True).first() or 0
                    alvos.append({"uso_consumo": True, "produto": produto.pk, "codigo": None, "quantidade": qtd, "saldo": saldo})
                else:
                    codigo = self._codigo_estoque_item_xml(item, nota=nota, preferir_movimento_original=True)
                    saldo = Estoque.objects.filter(CodigodeBarra=codigo, Idloja=nota.loja).values_list("Estoque", flat=True).first() or 0
                    alvos.append({"uso_consumo": False, "produto": produto.pk, "codigo": codigo, "quantidade": qtd, "saldo": saldo})
        elif nota.pedido_compra_id:
            for item_nf in nota.itens.select_related("pedido_item", "pedido_item__produto", "pedido_item__pack"):
                pedido_item = item_nf.pedido_item
                produto = pedido_item.produto if pedido_item else None
                if not produto:
                    continue
                qtd_recebida = _q3(item_nf.qtd_recebida or 0)
                if qtd_recebida <= 0:
                    continue
                if nota.pedido_compra.tipo == "1" and pedido_item.pack_id:
                    qtd_pedido = Decimal(pedido_item.qtd or 0)
                    if qtd_pedido <= 0:
                        continue
                    fator_recebido = qtd_recebida / qtd_pedido
                    for pack_item in PackItem.objects.filter(pack_id=pedido_item.pack_id):
                        sku = ProdutoDetalhe.objects.filter(
                            produto_id=pedido_item.produto_id,
                            idcor_id=pedido_item.cor_id,
                            idtamanho_id=pack_item.tamanho_id,
                        ).first()
                        if not sku:
                            continue
                        qtd = _q3(Decimal(pack_item.qtd or 0) * Decimal(pedido_item.n_packs or 0) * fator_recebido)
                        saldo = Estoque.objects.filter(CodigodeBarra=sku.ean13, Idloja=nota.loja).values_list("Estoque", flat=True).first() or 0
                        alvos.append({"uso_consumo": False, "produto": produto.pk, "codigo": sku.ean13, "quantidade": qtd, "saldo": saldo})
                elif produto.tipo_produto == "2":
                    saldo = ProdutoUsoConsumoEstoque.objects.filter(empresa=nota.empresa, loja=nota.loja, produto=produto).values_list("saldo", flat=True).first() or 0
                    alvos.append({"uso_consumo": True, "produto": produto.pk, "codigo": None, "quantidade": qtd_recebida, "saldo": saldo})
                elif produto.tipo_produto == "4":
                    codigo = self._codigo_estoque_produto(produto)
                    saldo = Estoque.objects.filter(CodigodeBarra=codigo, Idloja=nota.loja).values_list("Estoque", flat=True).first() or 0
                    alvos.append({"uso_consumo": False, "produto": produto.pk, "codigo": codigo, "quantidade": qtd_recebida, "saldo": saldo})
        return alvos

    @action(detail=True, methods=["get"], url_path="itens-pedido")
    def itens_pedido(self, request, pk=None):
        nota = self.get_object()
        if not nota.pedido_compra_id:
            return Response([], status=status.HTTP_200_OK)
        itens_nota = {item.pedido_item_id: item for item in nota.itens.all()}
        payload = []

        for pedido_item in nota.pedido_compra.itens.all().order_by("id"):
            qtd_outras_notas = sum(
                Decimal(item.qtd_recebida or 0)
                for item in NotaFiscalEntradaItem.objects.filter(
                    pedido_item=pedido_item,
                    nota__pedido_compra_id=nota.pedido_compra_id,
                )
                .exclude(nota_id=nota.pk)
                .exclude(nota__status=NotaFiscalEntrada.Status.CANCELADA)
            )
            item_nota = itens_nota.get(pedido_item.pk)
            qtd_na_nota = Decimal(item_nota.qtd_recebida or 0) if item_nota else Decimal("0")
            qtd_pedido = Decimal(pedido_item.qtd or 0)
            saldo_total = qtd_pedido - qtd_outras_notas
            saldo_pendente = saldo_total - qtd_na_nota
            pack_itens = list(PackItem.objects.filter(pack_id=pedido_item.pack_id)) if pedido_item.pack_id else []
            qtd_pack = sum(int(item.qtd or 0) for item in pack_itens)
            quantidades_validas = []
            if qtd_pack and pedido_item.n_packs:
                quantidades_validas = [
                    str(Decimal(qtd_pack * pack_num))
                    for pack_num in range(1, int(pedido_item.n_packs or 0) + 1)
                    if Decimal(qtd_pack * pack_num) <= saldo_total
                ]

            payload.append(
                {
                    "pedido_item": pedido_item.pk,
                    "nota_item": getattr(item_nota, "pk", None),
                    "produto": getattr(pedido_item, "produto_id", None),
                    "produto_descricao": getattr(getattr(pedido_item, "produto", None), "descricao", None),
                    "produto_referencia": getattr(getattr(pedido_item, "produto", None), "referencia", None),
                    "cor": getattr(pedido_item, "cor_id", None),
                    "cor_nome": getattr(getattr(pedido_item, "cor", None), "Descricao", None),
                    "pack": getattr(pedido_item, "pack_id", None),
                    "pack_nome": getattr(getattr(pedido_item, "pack", None), "nome", None),
                    "descricao_livre": pedido_item.descricao_livre,
                    "qtd_pedido": str(qtd_pedido),
                    "qtd_recebida_outras_notas": str(qtd_outras_notas),
                    "qtd_na_nota": str(qtd_na_nota),
                    "saldo_total_recebivel": str(saldo_total),
                    "saldo_pendente": str(saldo_pendente),
                    "preco_unit_pedido": str(pedido_item.preco_unit),
                    "qtd_pack": str(qtd_pack or ""),
                    "n_packs": pedido_item.n_packs or 0,
                    "quantidades_validas": quantidades_validas,
                }
            )

        return Response(payload, status=status.HTTP_200_OK)

    def _documento_estoque(self, nota, operacao):
        return f"NFE:{nota.pk}:{operacao}"[:50]

    def _codigo_estoque_produto(self, produto):
        referencia_numerica = ''.join(ch for ch in str(produto.referencia or '') if ch.isdigit())
        if len(referencia_numerica) == 13:
            return referencia_numerica
        return f"29{int(produto.pk) % 100000000000:011d}"

    def _codigo_estoque_item_xml(self, item, nota=None, preferir_movimento_original=False):
        produto = item.produto
        if not produto:
            raise ValueError(f"Item XML {item.numero_item or item.pk}: produto não conciliado.")
        if produto.tipo_produto != "1":
            return self._codigo_estoque_produto(produto)

        nota = nota or item.nota
        if preferir_movimento_original and nota:
            documento_entrada = self._documento_estoque(nota, "ENTRADA")
            movimento = (
                EstoqueMovimentacao.objects
                .filter(
                    documento=documento_entrada,
                    tipo=EstoqueMovimentacao.TIPO_ENTRADA,
                    Idloja=nota.loja,
                )
                .filter(Q(origem__contains=f"XML_ITEM:{item.pk}") | Q(observacao__contains=f"ITEM:{item.pk}"))
                .order_by("data_movimento", "Idmovimento")
                .first()
            )
            if movimento:
                return movimento.CodigodeBarra

        gtin = only_digits(item.gtin_ean or "")
        numero = item.numero_item or item.pk
        produto_label = produto.referencia or produto.descricao or str(produto.pk)
        if not gtin:
            raise ValueError(f"Item XML {numero}: produto {produto_label} de revenda sem GTIN/EAN para identificar o SKU.")

        sku = ProdutoDetalhe.objects.filter(produto_id=produto.pk, ean13=gtin, ativo=True).first()
        if not sku:
            existe_outro_produto = ProdutoDetalhe.objects.filter(ean13=gtin).exclude(produto_id=produto.pk).exists()
            if existe_outro_produto:
                raise ValueError(f"Item XML {numero}: GTIN/EAN {gtin} pertence a outro produto e não pode movimentar {produto_label}.")
            existe_inativo = ProdutoDetalhe.objects.filter(produto_id=produto.pk, ean13=gtin, ativo=False).exists()
            if existe_inativo:
                raise ValueError(f"Item XML {numero}: GTIN/EAN {gtin} corresponde a SKU inativo do produto {produto_label}.")
            raise ValueError(f"Item XML {numero}: GTIN/EAN {gtin} não corresponde a SKU ativo do produto {produto_label}.")
        return sku.ean13

    def _qtd_recebida_item(self, pedido_item, excluir_nota=None):
        itens = NotaFiscalEntradaItem.objects.filter(
            pedido_item=pedido_item,
            nota__pedido_compra_id=pedido_item.pedido_id,
            nota__status=NotaFiscalEntrada.Status.FECHADA,
        )
        if excluir_nota:
            itens = itens.exclude(nota=excluir_nota)
        total_legado = sum(Decimal(item.qtd_recebida or 0) for item in itens)
        total_xml_qs = NotaFiscalEntradaItemXml.objects.filter(
            pedido_item=pedido_item,
            nota__pedido_compra_id=pedido_item.pedido_id,
        ).exclude(nota__status=NotaFiscalEntrada.Status.CANCELADA)
        if excluir_nota:
            total_xml_qs = total_xml_qs.exclude(nota=excluir_nota)
        total_xml = sum(
            Decimal(item.quantidade_interna_efetivada or 0)
            for item in total_xml_qs.filter(Q(nota__status=NotaFiscalEntrada.Status.FECHADA) | Q(quantidade_interna_efetivada__isnull=False))
        )
        return total_legado + total_xml

    def _atualizar_recebimento_pedido(self, nota, excluir_nota=None):
        pedido = nota.pedido_compra
        if not pedido:
            return {"status_pedido": None, "itens_atualizados": 0}
        itens = list(pedido.itens.all().order_by("id"))
        if not itens:
            return {"status_pedido": pedido.status, "itens_atualizados": 0}

        atendidos = 0
        parciais = 0
        atualizados = 0
        for item in itens:
            prevista = Decimal(item.qtd or 0)
            recebida = self._qtd_recebida_item(item, excluir_nota=excluir_nota)
            entrega = item.entregas.order_by("id").first()
            if not entrega:
                entrega = PedidoCompraEntrega(item=item, qtd_prevista=prevista, data_prevista=pedido.previsao_entrega)

            entrega.qtd_prevista = prevista
            entrega.qtd_recebida = recebida
            if prevista > 0 and recebida >= prevista:
                entrega.status = "RECB"
                entrega.data_recebida = nota.dt_entrada
                atendidos += 1
            elif recebida > 0:
                entrega.status = "PARC"
                entrega.data_recebida = None
                parciais += 1
            else:
                entrega.status = "PREV"
                entrega.data_recebida = None
            entrega.save()
            atualizados += 1

        novo_status = "AT" if atendidos == len(itens) else "AP"
        if pedido.status != novo_status and pedido.status != "CA":
            pedido.status = novo_status
            pedido.save(update_fields=["status"])

        return {
            "status_pedido": pedido.status,
            "itens_atualizados": atualizados,
            "itens_atendidos": atendidos,
            "itens_parciais": parciais,
        }

    def _movimentar_estoque_entrada(self, nota):
        documento = self._documento_estoque(nota, "ENTRADA")
        if (
            EstoqueMovimentacao.objects.filter(documento=documento, tipo=EstoqueMovimentacao.TIPO_ENTRADA).exists()
            or ProdutoUsoConsumoMovimentacao.objects.filter(documento=documento, tipo=ProdutoUsoConsumoMovimentacao.TIPO_ENTRADA).exists()
        ):
            return {"disponivel": True, "movimentos": 0, "ja_movimentada": True}

        movimentos = 0
        for item_nf in nota.itens.select_related("pedido_item", "pedido_item__produto", "pedido_item__cor", "pedido_item__pack"):
            if nota.pedido_compra.tipo == "1":
                movimentos += self._movimentar_item_estoque(
                    nota=nota,
                    item_nf=item_nf,
                    tipo=EstoqueMovimentacao.TIPO_ENTRADA,
                    documento=documento,
                    sinal=1,
                )
            elif self._pedido_item_uso_consumo(item_nf.pedido_item):
                movimentos += self._movimentar_item_estoque_uso_consumo(
                    nota=nota,
                    item_nf=item_nf,
                    tipo=ProdutoUsoConsumoMovimentacao.TIPO_ENTRADA,
                    documento=documento,
                    sinal=1,
                )
            else:
                movimentos += self._movimentar_item_estoque_nao_revenda(
                    nota=nota,
                    item_nf=item_nf,
                    tipo=EstoqueMovimentacao.TIPO_ENTRADA,
                    documento=documento,
                    sinal=1,
                )

        return {"disponivel": True, "movimentos": movimentos}

    def _movimentar_estoque_cancelamento(self, nota, motivo="", request=None):
        documento = self._documento_estoque(nota, "CANCEL")
        if (
            EstoqueMovimentacao.objects.filter(documento=documento, tipo=EstoqueMovimentacao.TIPO_SAIDA).exists()
            or ProdutoUsoConsumoMovimentacao.objects.filter(documento=documento, tipo=ProdutoUsoConsumoMovimentacao.TIPO_AJUSTE_SAIDA).exists()
        ):
            return {"disponivel": True, "movimentos": 0, "ja_movimentada": True}
        if nota.xml_importado:
            return self._movimentar_estoque_cancelamento_xml(nota, motivo, request, documento)

        movimentos = 0
        for item_nf in nota.itens.select_related("pedido_item", "pedido_item__produto", "pedido_item__cor", "pedido_item__pack"):
            if nota.pedido_compra.tipo == "1":
                movimentos += self._movimentar_item_estoque(
                    nota=nota,
                    item_nf=item_nf,
                    tipo=EstoqueMovimentacao.TIPO_SAIDA,
                    documento=documento,
                    sinal=-1,
                )
            elif self._pedido_item_uso_consumo(item_nf.pedido_item):
                movimentos += self._movimentar_item_estoque_uso_consumo(
                    nota=nota,
                    item_nf=item_nf,
                    tipo=ProdutoUsoConsumoMovimentacao.TIPO_AJUSTE_SAIDA,
                    documento=documento,
                    sinal=-1,
                )
            else:
                movimentos += self._movimentar_item_estoque_nao_revenda(
                    nota=nota,
                    item_nf=item_nf,
                    tipo=EstoqueMovimentacao.TIPO_SAIDA,
                    documento=documento,
                    sinal=-1,
                )

        return {"disponivel": True, "movimentos": movimentos}

    def _movimentar_estoque_cancelamento_xml(self, nota, motivo, request, documento):
        movimentos = 0
        for item in nota.itens_xml.select_related("produto", "produto__unidade").order_by("numero_item"):
            qtd = _q3(item.quantidade_interna_efetivada or 0)
            if qtd <= 0:
                continue
            if item.produto.tipo_produto == "2":
                movimentos += self._estornar_produto_xml_uso_consumo(nota, item, qtd, motivo, request, documento)
            else:
                movimentos += self._estornar_produto_xml_estoque(nota, item, qtd, motivo, documento)
        return {"disponivel": True, "movimentos": movimentos}

    def _estornar_produto_xml_uso_consumo(self, nota, item, qtd, motivo, request, documento):
        estoque, _ = ProdutoUsoConsumoEstoque.objects.select_for_update().get_or_create(
            empresa=nota.empresa,
            loja=nota.loja,
            produto=item.produto,
            defaults={"saldo": Decimal("0")},
        )
        anterior = Decimal(estoque.saldo or 0)
        posterior = anterior - qtd
        estoque.saldo = posterior
        estoque.save(update_fields=["saldo", "atualizado_em"])
        ProdutoUsoConsumoMovimentacao.objects.create(
            empresa=nota.empresa,
            produto=item.produto,
            loja=nota.loja,
            tipo=ProdutoUsoConsumoMovimentacao.TIPO_AJUSTE_SAIDA,
            quantidade=qtd,
            saldo_anterior=anterior,
            saldo_posterior=posterior,
            usuario=getattr(request, "user", None) if request else getattr(nota, "criado_por", None),
            motivo=f"Cancelamento NF-e XML: {motivo}"[:255],
            destino=nota.loja.nome_loja,
            documento=documento,
            origem=f"NFE:{nota.pk};XML_ITEM:{item.pk};ESTORNO",
        )
        return 1

    def _estornar_produto_xml_estoque(self, nota, item, qtd, motivo, documento):
        produto = item.produto
        codigo = self._codigo_estoque_item_xml(item, nota=nota, preferir_movimento_original=True)
        custo_movimento = _q4(item.valor_unitario_comercial or produto.custo_medio or produto.custo_ultima_compra or produto.custo_original or 0)
        estoque, _ = Estoque.objects.select_for_update().get_or_create(
            CodigodeBarra=codigo,
            Idloja=nota.loja,
            defaults={"referencia": produto.referencia or "", "Estoque": 0, "reserva": 0},
        )
        anterior = Decimal(estoque.Estoque or 0)
        posterior = anterior - qtd
        estoque.referencia = produto.referencia or estoque.referencia
        estoque.Estoque = posterior
        estoque.reserva = estoque.reserva or 0
        estoque.save(update_fields=["referencia", "Estoque", "reserva"])
        EstoqueMovimentacao.objects.create(
            Idloja=nota.loja,
            CodigodeBarra=codigo,
            referencia=produto.referencia or "",
            tipo=EstoqueMovimentacao.TIPO_SAIDA,
            quantidade=qtd,
            custo_unitario=custo_movimento,
            custo_total=_money(qtd * custo_movimento),
            custo_medio_apos=_q4(produto.custo_medio or produto.custo_ultima_compra or produto.custo_original or custo_movimento),
            saldo_anterior=anterior,
            saldo_posterior=posterior,
            origem=EstoqueMovimentacao.ORIGEM_NFE,
            documento=documento,
            observacao=f"Cancelamento NF-e XML {nota.numero};ITEM:{item.pk};MOTIVO:{motivo}"[:255],
        )
        return 1

    def _validar_estoque_cancelamento(self, nota):
        for item_nf in nota.itens.select_related("pedido_item", "pedido_item__produto", "pedido_item__pack"):
            pedido_item = item_nf.pedido_item
            if nota.pedido_compra.tipo == "1":
                qtd_pedido = Decimal(pedido_item.qtd or 0)
                if qtd_pedido <= 0:
                    raise ValueError("Item do pedido sem quantidade calculada para cancelar a nota.")
                fator_recebido = Decimal(item_nf.qtd_recebida or 0) / qtd_pedido
                for pack_item in PackItem.objects.filter(pack_id=pedido_item.pack_id):
                    qtd = Decimal(pack_item.qtd or 0) * Decimal(pedido_item.n_packs or 0) * fator_recebido
                    sku = ProdutoDetalhe.objects.select_related("produto").filter(
                        produto_id=pedido_item.produto_id,
                        idcor_id=pedido_item.cor_id,
                        idtamanho_id=pack_item.tamanho_id,
                    ).first()
                    if not sku:
                        raise ValueError("SKU não encontrado para cancelar a nota.")
                    saldo = Decimal(
                        Estoque.objects.filter(CodigodeBarra=sku.ean13, Idloja=nota.pedido_compra.loja)
                        .values_list("Estoque", flat=True)
                        .first()
                        or 0
                    )
                    if saldo - qtd < 0 and (nota.pedido_compra.loja.EstoqueNegativo or "NAO").upper() != "SIM":
                        raise ValueError(f"Saldo insuficiente do SKU {sku.ean13} para cancelar a nota.")
            else:
                produto = pedido_item.produto if pedido_item else None
                if not produto:
                    continue
                if self._pedido_item_uso_consumo(pedido_item):
                    saldo = Decimal(
                        ProdutoUsoConsumoEstoque.objects.filter(produto=produto, loja=nota.pedido_compra.loja, empresa=nota.pedido_compra.empresa)
                        .values_list("saldo", flat=True)
                        .first()
                        or 0
                    )
                    qtd = Decimal(item_nf.qtd_recebida or 0)
                    if saldo - qtd < 0 and (nota.pedido_compra.loja.EstoqueNegativo or "NAO").upper() != "SIM":
                        raise ValueError(f"Saldo insuficiente do produto {produto.descricao} para cancelar a nota.")
                    continue
                codigo = self._codigo_estoque_produto(produto)
                saldo = Decimal(
                    Estoque.objects.filter(CodigodeBarra=codigo, Idloja=nota.pedido_compra.loja)
                    .values_list("Estoque", flat=True)
                    .first()
                    or 0
                )
                qtd = Decimal(item_nf.qtd_recebida or 0)
                if saldo - qtd < 0 and (nota.pedido_compra.loja.EstoqueNegativo or "NAO").upper() != "SIM":
                    raise ValueError(f"Saldo insuficiente do produto {produto.descricao} para cancelar a nota.")

    def _pedido_item_material_interno(self, pedido_item):
        if not pedido_item or not pedido_item.produto_id or pedido_item.produto.tipo_produto != "2":
            return False
        cotacao = getattr(pedido_item.pedido, "cotacao_origem", None)
        if not cotacao:
            return False
        marcador_req = f"REQ_ITEM:"
        marcador_os = f"OS_MATERIAL:"
        return marcador_req in (pedido_item.observacoes or "") or marcador_os in (pedido_item.observacoes or "")

    def _pedido_item_uso_consumo(self, pedido_item):
        return bool(pedido_item and pedido_item.produto_id and pedido_item.produto.tipo_produto == "2")

    def _movimentar_item_estoque_uso_consumo(self, nota, item_nf, tipo, documento, sinal):
        pedido_item = item_nf.pedido_item
        produto = pedido_item.produto if pedido_item else None
        if not produto or produto.tipo_produto != "2":
            return 0
        qtd = _q3(item_nf.qtd_recebida or 0)
        if qtd <= 0:
            return 0
        estoque, _ = ProdutoUsoConsumoEstoque.objects.select_for_update().get_or_create(
            empresa=nota.pedido_compra.empresa,
            loja=nota.pedido_compra.loja,
            produto=produto,
            defaults={"saldo": Decimal("0")},
        )
        anterior = Decimal(estoque.saldo or 0)
        posterior = anterior + (qtd * Decimal(sinal))
        if sinal > 0 and posterior < 0 and (nota.pedido_compra.loja.EstoqueNegativo or "NAO").upper() != "SIM":
            raise ValueError(f"Saldo insuficiente do produto {produto.descricao} para cancelar/movimentar a nota.")
        estoque.saldo = posterior
        estoque.save(update_fields=["saldo", "atualizado_em"])
        ProdutoUsoConsumoMovimentacao.objects.create(
            empresa=nota.pedido_compra.empresa,
            produto=produto,
            loja=nota.pedido_compra.loja,
            tipo=tipo,
            quantidade=qtd,
            saldo_anterior=anterior,
            saldo_posterior=posterior,
            usuario=getattr(nota, "criado_por", None),
            motivo="Nota fiscal de entrada",
            destino=nota.pedido_compra.loja.nome_loja,
            documento=documento,
            origem=f"NFE:{nota.pk};PEDIDO:{nota.pedido_compra_id}",
        )
        return 1

    def _recalcular_necessidades_vinculadas(self, nota):
        if not nota.pedido_compra_id:
            return {"requisicao_itens": 0, "materiais_os": 0}
        req_ids = set()
        os_material_ids = set()
        for item in nota.pedido_compra.itens.all():
            obs = item.observacoes or ""
            for token in obs.split():
                if token.startswith("REQ_ITEM:"):
                    req_ids.add(int(token.split(":", 1)[1]))
                if token.startswith("OS_MATERIAL:"):
                    os_material_ids.add(int(token.split(":", 1)[1]))
        req_atualizadas = 0
        requisicoes = set()
        for req_item in RequisicaoItem.objects.select_related("requisicao", "produto").filter(pk__in=req_ids):
            requisicoes.add(req_item.requisicao)
        for req in requisicoes:
            resultado = sincronizar_requisicao_disponivel_para_atendimento(req)
            req_atualizadas += resultado["itens"]
        os_atualizadas = 0
        ordens_servico = set()
        for material in OrdemServicoMaterial.objects.select_related("ordem_servico", "produto").filter(pk__in=os_material_ids):
            before = material.status
            atualizar_status_material_os(material)
            ordens_servico.add(material.ordem_servico)
            if material.status != before:
                os_atualizadas += 1
        for ordem_servico in ordens_servico:
            atualizar_status_material_ordem_servico(ordem_servico)
        return {"requisicao_itens": req_atualizadas, "materiais_os": os_atualizadas}

    def _movimentar_item_estoque_nao_revenda(self, nota, item_nf, tipo, documento, sinal):
        pedido_item = item_nf.pedido_item
        produto = pedido_item.produto if pedido_item else None
        if not produto or produto.tipo_produto != "4":
            return 0

        qtd = _q3(item_nf.qtd_recebida or 0)
        if qtd <= 0:
            return 0

        codigo = self._codigo_estoque_produto(produto)
        custo_movimento = _q4(
            item_nf.preco_unit_nf
            or produto.custo_medio
            or produto.custo_ultima_compra
            or produto.custo_original
            or 0
        )
        estoque, _ = Estoque.objects.select_for_update().get_or_create(
            CodigodeBarra=codigo,
            Idloja=nota.pedido_compra.loja,
            defaults={"referencia": produto.referencia or "", "Estoque": 0, "reserva": 0},
        )
        anterior = Decimal(estoque.Estoque or 0)
        posterior = anterior + (qtd * Decimal(sinal))
        if sinal > 0 and posterior < 0 and (nota.pedido_compra.loja.EstoqueNegativo or "NAO").upper() != "SIM":
            raise ValueError(
                f"Saldo insuficiente do produto {produto.descricao} para cancelar/movimentar a nota."
            )

        estoque.referencia = produto.referencia or estoque.referencia
        estoque.Estoque = posterior
        estoque.reserva = estoque.reserva or 0
        estoque.save(update_fields=["referencia", "Estoque", "reserva"])

        EstoqueMovimentacao.objects.create(
            Idloja=nota.pedido_compra.loja,
            CodigodeBarra=codigo,
            referencia=produto.referencia or "",
            tipo=tipo,
            quantidade=qtd,
            custo_unitario=custo_movimento,
            custo_total=_money(qtd * custo_movimento),
            custo_medio_apos=_q4(produto.custo_medio or produto.custo_ultima_compra or produto.custo_original or custo_movimento),
            saldo_anterior=anterior,
            saldo_posterior=posterior,
            origem=EstoqueMovimentacao.ORIGEM_NFE,
            documento=documento,
            observacao=f"Nota fiscal de entrada {nota.numero}",
        )
        return 1

    def _atualizar_custos_produtos_nao_revenda(self, nota):
        atualizados = 0
        for item_nf in nota.itens.select_related("pedido_item", "pedido_item__produto"):
            pedido_item = item_nf.pedido_item
            produto = pedido_item.produto if pedido_item else None
            if not produto or produto.tipo_produto not in ("2", "4"):
                continue

            qtd_recebida = Decimal(item_nf.qtd_recebida or 0)
            if qtd_recebida <= 0:
                continue

            total_liquido = Decimal(item_nf.total_item or 0)
            custo_entrada = _q4((total_liquido / qtd_recebida) if total_liquido > 0 else item_nf.preco_unit_nf)
            if custo_entrada <= 0:
                continue

            if not Decimal(produto.custo_original or 0):
                produto.custo_original = custo_entrada
            produto.custo_ultima_compra = custo_entrada
            produto.custo_medio = custo_entrada
            produto.save(update_fields=["custo_original", "custo_ultima_compra", "custo_medio"])
            atualizados += 1

        return {"atualizados": atualizados}

    def _recalcular_custos_apos_cancelamento(self, nota):
        if nota.xml_importado:
            return self._recalcular_custos_xml_apos_cancelamento(nota)
        produtos = set()
        skus = set()
        for item_nf in nota.itens.select_related("pedido_item"):
            pedido_item = item_nf.pedido_item
            if nota.pedido_compra.tipo == "1" and pedido_item.pack_id:
                for pack_item in PackItem.objects.filter(pack_id=pedido_item.pack_id):
                    sku = ProdutoDetalhe.objects.filter(
                        produto_id=pedido_item.produto_id,
                        idcor_id=pedido_item.cor_id,
                        idtamanho_id=pack_item.tamanho_id,
                    ).first()
                    if sku:
                        skus.add(sku.pk)
            elif pedido_item.produto_id:
                produtos.add(pedido_item.produto_id)

        for sku in ProdutoDetalhe.objects.filter(pk__in=skus):
            entradas = self._entradas_validas_sku(sku, excluir_nota=nota)
            self._aplicar_custos_historicos(sku, entradas)

        for produto_id in produtos:
            produto = Produto.objects.get(pk=produto_id)
            entradas = self._entradas_validas_produto(produto, excluir_nota=nota)
            self._aplicar_custos_historicos(produto, entradas)

        return {"skus_atualizados": len(skus), "produtos_atualizados": len(produtos)}

    def _entradas_validas_produto(self, produto, excluir_nota):
        rows = (
            NotaFiscalEntradaItem.objects.select_related("nota")
            .filter(
                pedido_item__produto=produto,
                nota__status=NotaFiscalEntrada.Status.FECHADA,
            )
            .exclude(nota=excluir_nota)
            .order_by("nota__dt_entrada", "nota_id", "id")
        )
        return [
            (Decimal(row.qtd_recebida or 0), _q4((Decimal(row.total_item or 0) / Decimal(row.qtd_recebida or 1)) if Decimal(row.qtd_recebida or 0) else row.preco_unit_nf))
            for row in rows
            if Decimal(row.qtd_recebida or 0) > 0
        ]

    def _entradas_validas_sku(self, sku, excluir_nota):
        entradas = []
        rows = (
            NotaFiscalEntradaItem.objects.select_related("nota", "pedido_item")
            .filter(
                pedido_item__produto_id=sku.produto_id,
                pedido_item__cor_id=sku.idcor_id,
                pedido_item__pack__isnull=False,
                nota__status=NotaFiscalEntrada.Status.FECHADA,
            )
            .exclude(nota=excluir_nota)
            .order_by("nota__dt_entrada", "nota_id", "id")
        )
        for row in rows:
            pack_qtd = PackItem.objects.filter(pack_id=row.pedido_item.pack_id, tamanho_id=sku.idtamanho_id).aggregate(total=Sum("qtd"))["total"] or 0
            if not pack_qtd or not row.pedido_item.qtd:
                continue
            qtd = Decimal(pack_qtd) * Decimal(row.pedido_item.n_packs or 0) * (Decimal(row.qtd_recebida or 0) / Decimal(row.pedido_item.qtd or 1))
            if qtd > 0:
                entradas.append((qtd, _q4(row.preco_unit_nf or 0)))
        return entradas

    def _aplicar_custos_historicos(self, obj, entradas):
        if not entradas:
            obj.custo_original = Decimal("0.0000")
            obj.custo_ultima_compra = Decimal("0.0000")
            obj.custo_medio = Decimal("0.0000")
        else:
            qtd_total = sum((qtd for qtd, _ in entradas), Decimal("0"))
            total = sum((qtd * custo for qtd, custo in entradas), Decimal("0"))
            obj.custo_original = _q4(entradas[0][1])
            obj.custo_ultima_compra = _q4(entradas[-1][1])
            obj.custo_medio = _q4(total / qtd_total) if qtd_total > 0 else obj.custo_ultima_compra
        obj.save(update_fields=["custo_original", "custo_ultima_compra", "custo_medio"])

    def _recalcular_custos_xml_apos_cancelamento(self, nota):
        produtos = {
            item.produto_id
            for item in nota.itens_xml.select_related("produto")
            if item.produto_id and item.produto.tipo_produto in ("2", "4")
        }
        for produto_id in produtos:
            produto = Produto.objects.get(pk=produto_id)
            entradas = self._entradas_validas_produto_xml(produto, excluir_nota=nota)
            self._aplicar_custos_historicos(produto, entradas)
        return {"skus_atualizados": 0, "produtos_atualizados": len(produtos)}

    def _entradas_validas_produto_xml(self, produto, excluir_nota):
        rows = (
            NotaFiscalEntradaItemXml.objects.select_related("nota")
            .filter(produto=produto, nota__status=NotaFiscalEntrada.Status.FECHADA)
            .exclude(nota=excluir_nota)
            .order_by("nota__dt_entrada", "nota_id", "id")
        )
        return [
            (Decimal(row.quantidade_interna_efetivada or 0), _q4(row.valor_unitario_comercial or 0))
            for row in rows
            if Decimal(row.quantidade_interna_efetivada or 0) > 0
        ]

    def _encerrar_divergencias_cancelamento(self, nota, request):
        now = timezone.now()
        user = request.user if getattr(request.user, "is_authenticated", False) else None
        atualizadas = NotaFiscalEntradaDivergenciaXml.objects.select_for_update().filter(
            nota=nota,
            status=NotaFiscalEntradaDivergenciaXml.Status.PENDENTE,
        ).update(
            status=NotaFiscalEntradaDivergenciaXml.Status.CANCELADA,
            resolvido_por=user,
            resolvido_em=now,
        )
        return {"encerradas": atualizadas}

    def _auditar_cancelamento(self, nota, request, before, motivo, financeiro, estoque, custos, recebimento, necessidades, divergencias, analise):
        AuditService.success(
            AuditAction.OBJECT_UPDATED,
            category=AuditCategory.FISCAL,
            request=request,
            user=getattr(request, "user", None),
            instance=nota,
            before={"status": before},
            after={
                "nf": nota.pk,
                "chave": nota.chave_acesso,
                "empresa": nota.empresa_id,
                "loja": nota.loja_id,
                "fornecedor": nota.fornecedor_id,
                "pedido_compra": nota.pedido_compra_id,
                "motivo": motivo,
                "cancelado_por": getattr(nota, "cancelado_por_id", None),
                "cancelado_em": nota.cancelado_em.isoformat() if nota.cancelado_em else None,
                "financeiro": financeiro,
                "estoque": estoque,
                "custos": custos,
                "recebimento_pedido": recebimento,
                "necessidades": necessidades,
                "divergencias": divergencias,
                "avisos": analise.get("avisos", []),
                "status": nota.status,
            },
            metadata={"legacy_action": "cancelar", "cancelamento_operacional": True},
        )

    def _movimentar_item_estoque(self, nota, item_nf, tipo, documento, sinal):
        pedido_item = item_nf.pedido_item
        if not pedido_item.produto_id or not pedido_item.cor_id or not pedido_item.pack_id:
            raise ValueError("Item de revenda sem produto, cor ou pack para movimentar estoque.")

        qtd_recebida = Decimal(item_nf.qtd_recebida or 0)
        qtd_pedido = Decimal(pedido_item.qtd or 0)
        if qtd_recebida <= 0:
            return 0
        if qtd_pedido <= 0:
            raise ValueError("Item do pedido sem quantidade calculada para movimentar estoque.")

        fator_recebido = qtd_recebida / qtd_pedido
        pack_itens = list(PackItem.objects.select_related("tamanho").filter(pack_id=pedido_item.pack_id))
        if not pack_itens:
            raise ValueError("Pack do item não possui tamanhos configurados.")

        custo_entrada = _q4(item_nf.preco_unit_nf or 0)
        movimentos = 0
        for pack_item in pack_itens:
            qtd_decimal = Decimal(pack_item.qtd or 0) * Decimal(pedido_item.n_packs or 0) * fator_recebido
            if qtd_decimal != qtd_decimal.to_integral_value():
                qtd_pack = sum(int(item.qtd or 0) for item in pack_itens)
                validas = [
                    str(qtd_pack * pack_num)
                    for pack_num in range(1, int(pedido_item.n_packs or 0) + 1)
                ] if qtd_pack and pedido_item.n_packs else []
                produto = getattr(pedido_item.produto, "descricao", f"produto {pedido_item.produto_id}")
                complemento = f" Quantidades válidas para este item: {', '.join(validas)}." if validas else ""
                raise ValueError(
                    f"Item {pedido_item.pk} ({produto}): a quantidade recebida {qtd_recebida} não fecha com a composição do pack."
                    f" O pedido tem {qtd_pedido} peça(s) neste item.{complemento}"
                )

            qtd = int(qtd_decimal)
            if qtd <= 0:
                continue

            sku = ProdutoDetalhe.objects.select_related("produto").filter(
                produto_id=pedido_item.produto_id,
                idcor_id=pedido_item.cor_id,
                idtamanho_id=pack_item.tamanho_id,
            ).first()
            if not sku:
                raise ValueError(
                    f"SKU não encontrado para produto {pedido_item.produto_id}, cor {pedido_item.cor_id}, tamanho {pack_item.tamanho_id}."
                )
            estoque, _ = Estoque.objects.select_for_update().get_or_create(
                CodigodeBarra=sku.ean13,
                Idloja=nota.pedido_compra.loja,
                defaults={"referencia": sku.produto.referencia or "", "Estoque": 0, "reserva": 0},
            )
            anterior = estoque.Estoque or 0
            posterior = anterior + (qtd * sinal)
            custo_movimento = self._custo_movimento_sku(sku, custo_entrada)
            custo_medio_apos = _q4(sku.custo_medio or sku.custo_ultima_compra or sku.custo_original or custo_movimento)
            if sinal > 0:
                custo_medio_apos = self._atualizar_custo_medio_sku(sku, anterior, qtd, custo_entrada)
            estoque.referencia = sku.produto.referencia or estoque.referencia
            estoque.Estoque = posterior
            estoque.reserva = estoque.reserva or 0
            estoque.save(update_fields=["referencia", "Estoque", "reserva"])

            EstoqueMovimentacao.objects.create(
                Idloja=nota.pedido_compra.loja,
                CodigodeBarra=sku.ean13,
                referencia=sku.produto.referencia or "",
                tipo=tipo,
                quantidade=qtd,
                custo_unitario=custo_movimento,
                custo_total=_money(Decimal(qtd) * custo_movimento),
                custo_medio_apos=custo_medio_apos,
                saldo_anterior=anterior,
                saldo_posterior=posterior,
                origem=EstoqueMovimentacao.ORIGEM_NFE,
                documento=documento,
                observacao=f"Nota fiscal de entrada {nota.numero}",
            )
            movimentos += 1

        return movimentos

    def _custo_movimento_sku(self, sku, custo_entrada: Decimal) -> Decimal:
        if custo_entrada > 0:
            return custo_entrada
        return _q4(sku.custo_medio or sku.custo_ultima_compra or sku.custo_original or 0)

    def _atualizar_custo_medio_sku(self, sku, saldo_anterior: int, quantidade: int, custo_entrada: Decimal) -> Decimal:
        custo_entrada = _q4(custo_entrada)
        custo_atual = _q4(sku.custo_medio or sku.custo_ultima_compra or sku.custo_original or 0)
        if custo_entrada <= 0:
            return custo_atual

        saldo_anterior_dec = Decimal(max(int(saldo_anterior or 0), 0))
        quantidade_dec = Decimal(max(int(quantidade or 0), 0))
        saldo_posterior = saldo_anterior_dec + quantidade_dec
        if saldo_posterior <= 0:
            custo_medio = custo_entrada
        else:
            custo_medio = ((saldo_anterior_dec * custo_atual) + (quantidade_dec * custo_entrada)) / saldo_posterior

        sku.custo_ultima_compra = custo_entrada
        if not Decimal(sku.custo_original or 0):
            sku.custo_original = custo_entrada
        sku.custo_medio = _q4(custo_medio)
        sku.save(update_fields=["custo_original", "custo_ultima_compra", "custo_medio"])
        return sku.custo_medio

    def _vincular_financeiro(self, nota):
        if not FIN_OK:
            return {"disponivel": False, "titulos_atualizados": 0, "parcelas_efetivadas": 0}

        titulos = Pagar.objects.filter(pedido_compra=nota.pedido_compra_id).filter(nfe_id__isnull=True, Previsao=True)
        documento = _documento_nota(nota)
        valor_nota = _money(nota.valor_total or 0)
        titulos_atualizados = 0
        titulos_criados = 0
        parcelas_efetivadas = 0
        previsoes_ajustadas = 0

        for titulo in titulos.order_by("Idpagar"):
            parcelas_previstas = list(
                PagarItem.objects.filter(Idpagar=titulo)
                .exclude(status=PagarItem.STATUS_CANCELADO)
                .order_by("parcela_n")
            )
            valor_previsao = _money(sum(Decimal(p.valor_parcela or 0) for p in parcelas_previstas))
            if valor_previsao <= 0 or valor_nota <= 0:
                continue

            if valor_nota >= valor_previsao - Decimal("0.01"):
                self._redistribuir_parcelas(parcelas_previstas, valor_nota)
                titulo.nfe_id = nota.pk
                titulo.Titulo = str(nota.numero)[:60]
                titulo.Documento = documento
                titulo.Data_emissao = nota.dt_emissao
                titulo.Valor_total = valor_nota
                titulo.Previsao = False
                titulo.save(update_fields=["nfe_id", "Titulo", "Documento", "Data_emissao", "Valor_total", "Previsao"])
                parcelas_efetivadas += PagarItem.objects.filter(Idpagar=titulo).exclude(
                    status=PagarItem.STATUS_CANCELADO
                ).update(status=PagarItem.STATUS_EFETIVO, Previsao=False)
                titulos_atualizados += 1
                valor_nota = Decimal("0.00")
                break

            titulo_nf = Pagar.objects.create(
                empresa=titulo.empresa,
                idloja=titulo.idloja,
                idfornecedor=titulo.idfornecedor,
                Titulo=str(nota.numero)[:60],
                Documento=documento,
                Data_emissao=nota.dt_emissao,
                Valor_total=valor_nota,
                Previsao=False,
                FormaPagamento=titulo.FormaPagamento,
                Idnatureza=titulo.Idnatureza,
                conta_contabil=titulo.conta_contabil,
                pedido_compra=nota.pedido_compra_id,
                nfe_id=nota.pk,
            )
            self._criar_parcelas_proporcionais(titulo_nf, parcelas_previstas, valor_previsao, valor_nota, efetivo=True)
            titulos_criados += 1
            parcelas_efetivadas += len(parcelas_previstas)

            saldo_previsao = _money(valor_previsao - valor_nota)
            self._redistribuir_parcelas(parcelas_previstas, saldo_previsao)
            titulo.Valor_total = saldo_previsao
            titulo.Titulo = f"PC {nota.pedido_compra_id} - saldo"[:60]
            titulo.save(update_fields=["Valor_total", "Titulo"])
            previsoes_ajustadas += 1
            valor_nota = Decimal("0.00")
            break

        return {
            "disponivel": True,
            "titulos_atualizados": titulos_atualizados,
            "titulos_criados": titulos_criados,
            "parcelas_efetivadas": parcelas_efetivadas,
            "previsoes_ajustadas": previsoes_ajustadas,
        }

    def _cancelar_financeiro_nf(self, nota):
        if not FIN_OK:
            return {"disponivel": False, "titulos_cancelados": 0}

        titulos_nf = list(Pagar.objects.select_for_update().filter(nfe_id=nota.pk, pedido_compra=nota.pedido_compra_id))
        itens_nf = PagarItem.objects.select_for_update().filter(Idpagar__in=titulos_nf)
        if itens_nf.filter(status=PagarItem.STATUS_BAIXADO).exists() or itens_nf.filter(data_baixa__isnull=False).exists() or itens_nf.filter(valor_baixa__gt=0).exists():
            raise ValueError("O título financeiro vinculado à NF já possui baixa. Reverta/levante a baixa no Financeiro antes de cancelar a NF.")
        if MovimentacaoFinanceira.objects.filter(pagar_item__in=itens_nf).exclude(status=MovimentacaoFinanceira.STATUS_CANCELADA).exists():
            raise ValueError("O título financeiro vinculado à NF já possui baixa. Reverta/levante a baixa no Financeiro antes de cancelar a NF.")

        modelo_previsao = None
        for titulo in titulos_nf:
            if modelo_previsao is None:
                modelo_previsao = titulo
            titulo.delete()

        if nota.pedido_compra_id:
            self._recalcular_previsao_financeira_pedido(nota, modelo_previsao)
        return {"disponivel": True, "titulos_cancelados": len(titulos_nf)}

    def _recalcular_previsao_financeira_pedido(self, nota, modelo_previsao):
        pedido = nota.pedido_compra
        total_fechado = _money(
            NotaFiscalEntrada.objects.filter(
                pedido_compra=pedido,
                status=NotaFiscalEntrada.Status.FECHADA,
            )
            .exclude(pk=nota.pk)
            .aggregate(total=Sum("valor_total"))["total"]
            or 0
        )
        saldo = _money(Decimal(pedido.total_pedido or 0) - total_fechado)
        if saldo < 0:
            raise ValueError("Saldo financeiro do pedido ficaria negativo após o cancelamento.")

        previsoes = list(Pagar.objects.select_for_update().filter(pedido_compra=pedido.pk, nfe_id__isnull=True, Previsao=True).order_by("Idpagar"))
        previsao = previsoes[0] if previsoes else None
        for extra in previsoes[1:]:
            extra.delete()

        if saldo <= 0:
            if previsao:
                previsao.delete()
            return

        base = previsao or modelo_previsao or Pagar.objects.filter(pedido_compra=pedido.pk).order_by("Idpagar").first()
        if not base:
            return
        if not previsao:
            previsao = Pagar.objects.create(
                empresa=pedido.empresa,
                idloja=pedido.loja,
                idfornecedor=pedido.fornecedor,
                Titulo=f"PC {pedido.pk} - saldo"[:60],
                Documento=None,
                Data_emissao=pedido.emissao,
                Valor_total=saldo,
                Previsao=True,
                FormaPagamento=base.FormaPagamento,
                Idnatureza=base.Idnatureza,
                conta_contabil=base.conta_contabil,
                pedido_compra=pedido.pk,
                nfe_id=None,
            )
            base_itens = list(PagarItem.objects.filter(Idpagar=base).order_by("parcela_n")) if base.pk != previsao.pk else []
            if base_itens:
                self._criar_parcelas_proporcionais(previsao, base_itens, sum(Decimal(i.valor_parcela or 0) for i in base_itens), saldo)
            else:
                PagarItem.objects.create(
                    Idpagar=previsao,
                    parcela_n=1,
                    status=PagarItem.STATUS_PREVISTO,
                    Data_vencimento=pedido.previsao_entrega or pedido.emissao,
                    valor_parcela=saldo,
                    FormaPagamento=base.FormaPagamento,
                    Previsao=True,
                    Idnatureza=base.Idnatureza,
                )
        else:
            itens = list(PagarItem.objects.select_for_update().filter(Idpagar=previsao).order_by("parcela_n"))
            self._redistribuir_parcelas(itens, saldo)
            PagarItem.objects.filter(Idpagar=previsao).update(status=PagarItem.STATUS_PREVISTO, Previsao=True, data_baixa=None, valor_baixa=None)
            previsao.Valor_total = saldo
            previsao.Titulo = f"PC {pedido.pk} - saldo"[:60]
            previsao.Previsao = True
            previsao.nfe_id = None
            previsao.save(update_fields=["Valor_total", "Titulo", "Previsao", "nfe_id"])

    def _criar_parcelas_proporcionais(self, titulo, parcelas_base, total_base, total_destino, efetivo=False):
        restante = _money(total_destino)
        total_base = _money(total_base)
        total_parcelas = len(parcelas_base)
        for idx, base in enumerate(parcelas_base, start=1):
            if idx == total_parcelas:
                valor = restante
            else:
                proporcao = Decimal(base.valor_parcela or 0) / total_base if total_base else Decimal("0")
                valor = _money(total_destino * proporcao)
                restante = _money(restante - valor)
            PagarItem.objects.create(
                Idpagar=titulo,
                parcela_n=base.parcela_n,
                status=PagarItem.STATUS_EFETIVO if efetivo else PagarItem.STATUS_PREVISTO,
                Data_vencimento=base.Data_vencimento,
                valor_parcela=valor,
                FormaPagamento=base.FormaPagamento,
                idconta=base.idconta,
                juros=0,
                multa=0,
                tarifa=0,
                desconto=0,
                data_baixa=None,
                valor_baixa=None,
                Previsao=not efetivo,
                Idnatureza=base.Idnatureza,
            )

    def _redistribuir_parcelas(self, parcelas, total_destino):
        total_atual = _money(sum(Decimal(p.valor_parcela or 0) for p in parcelas))
        restante = _money(total_destino)
        total_parcelas = len(parcelas)
        for idx, parcela in enumerate(parcelas, start=1):
            if idx == total_parcelas:
                valor = restante
            else:
                proporcao = Decimal(parcela.valor_parcela or 0) / total_atual if total_atual else Decimal("0")
                valor = _money(total_destino * proporcao)
                restante = _money(restante - valor)
            parcela.valor_parcela = valor
            parcela.save(update_fields=["valor_parcela"])


class XmlFornecedorRecebidoViewSet(BaseViewSet):
    queryset = (
        XmlFornecedorRecebido.objects
        .select_related("empresa", "loja", "fornecedor")
        .all()
        .order_by("-detectado_em", "-id")
    )
    serializer_class = XmlFornecedorRecebidoSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()

        loja = self.request.query_params.get("loja")
        fornecedor = self.request.query_params.get("fornecedor")
        status_operacional = self.request.query_params.get("status_operacional")
        situacao_fiscal = self.request.query_params.get("situacao_fiscal")
        chave = self.request.query_params.get("chave_acesso")
        search = self.request.query_params.get("search")

        if loja:
            qs = qs.filter(loja_id=loja)
        if fornecedor:
            qs = qs.filter(fornecedor_id=fornecedor)
        if status_operacional:
            qs = qs.filter(status_operacional=status_operacional)
        if situacao_fiscal:
            qs = qs.filter(situacao_fiscal=situacao_fiscal)
        if chave:
            qs = qs.filter(chave_acesso__icontains=chave)
        if search:
            qs = qs.filter(
                Q(chave_acesso__icontains=search)
                | Q(numero__icontains=search)
                | Q(emitente_documento__icontains=search)
                | Q(emitente_nome__icontains=search)
                | Q(destinatario_documento__icontains=search)
                | Q(destinatario_nome__icontains=search)
            )
        return qs

    def _validar_empresa_usuario(self, empresa):
        user_empresa_id = self._empresa_id_usuario()
        if not user_empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        if user_empresa_id and empresa and empresa.id != int(user_empresa_id):
            raise ValidationError({"empresa": "Empresa fora do escopo do usuário."})

    def perform_create(self, serializer):
        self._validar_empresa_usuario(serializer.validated_data.get("empresa"))
        serializer.save()

    def perform_update(self, serializer):
        self._validar_empresa_usuario(serializer.validated_data.get("empresa") or serializer.instance.empresa)
        serializer.save()


class NotaFiscalEntradaItemViewSet(BaseViewSet):
    queryset = NotaFiscalEntradaItem.objects.select_related("nota", "nota__empresa", "pedido_item").all().order_by("nota_id", "id")
    serializer_class = NotaFiscalEntradaItemSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        nota = self.request.query_params.get("nota")
        pedido = self.request.query_params.get("pedido") or self.request.query_params.get("pedido_compra")
        pedido_item = self.request.query_params.get("pedido_item")

        if empresa_id:
            qs = qs.filter(nota__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        if nota:
            qs = qs.filter(nota_id=nota)
        if pedido:
            qs = qs.filter(nota__pedido_compra_id=pedido)
        if pedido_item:
            qs = qs.filter(pedido_item_id=pedido_item)
        return qs

    def perform_create(self, serializer):
        self._validar_item_empresa(serializer.validated_data)
        serializer.save()

    def perform_update(self, serializer):
        data = {**serializer.validated_data}
        data.setdefault("nota", serializer.instance.nota)
        data.setdefault("pedido_item", serializer.instance.pedido_item)
        self._validar_item_empresa(data)
        serializer.save()

    def _validar_item_empresa(self, data):
        nota = data.get("nota")
        pedido_item = data.get("pedido_item")
        empresa_id = getattr(nota, "empresa_id", None)
        user_empresa_id = self._empresa_id_usuario()
        if not user_empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        if user_empresa_id and empresa_id and int(user_empresa_id) != empresa_id:
            raise ValidationError({"nota": "Nota fiscal pertence a outra empresa."})
        if nota and nota.loja_id and nota.loja.empresa_id != nota.empresa_id:
            raise ValidationError({"loja": "A loja da nota pertence a outra empresa."})
        if nota and not nota.pedido_compra_id:
            raise ValidationError({"nota": "Itens de NF sem pedido serão implementados em etapa posterior."})
        if pedido_item and nota and pedido_item.pedido_id != nota.pedido_compra_id:
            raise ValidationError({"pedido_item": "O item informado não pertence ao pedido de compra da nota."})
        if pedido_item and empresa_id and pedido_item.pedido.empresa_id != empresa_id:
            raise ValidationError({"pedido_item": "Item de pedido pertence a outra empresa."})

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        nota = instance.nota
        if nota.status != NotaFiscalEntrada.Status.ABERTA:
            return Response({"detail": "Somente notas abertas podem receber alterações."}, status=status.HTTP_400_BAD_REQUEST)

        response = super().destroy(request, *args, **kwargs)
        nota.recalcular_totais()
        return response
