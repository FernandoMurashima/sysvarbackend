from decimal import Decimal, ROUND_HALF_UP

from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from rest_framework import parsers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from accounts.permissions import HasModuleRole
from cadastros.models import Fornecedor, Loja
from compras.models import OrdemServicoMaterial, PedidoCompra, PedidoCompraEntrega, RequisicaoItem
from compras.services_necessidade import sincronizar_requisicao_disponivel_para_atendimento
from compras.services_requisicao import atualizar_status_material_ordem_servico, atualizar_status_material_os
from produto.models import Estoque, EstoqueMovimentacao, PackItem, Produto, ProdutoDetalhe, ProdutoUsoConsumoEstoque, ProdutoUsoConsumoMovimentacao

from auditoria.models import AuditAction, AuditCategory
from auditoria.services import AuditService

FIN_OK = True
try:
    from financeiro.models import MovimentacaoFinanceira, Pagar, PagarItem
except Exception:
    FIN_OK = False
    MovimentacaoFinanceira = Pagar = PagarItem = None

from fiscal.models import NotaFiscalEntrada, NotaFiscalEntradaItem, NotaFiscalEntradaItemXml
from fiscal.services.nfe_conciliacao import candidatos_item, conciliar_automaticamente, conciliar_manual, resumo_conciliacao
from fiscal.services.nfe_xml import only_digits, parse_nfe_xml
from fiscal.serializers import NotaFiscalEntradaItemSerializer, NotaFiscalEntradaItemXmlSerializer, NotaFiscalEntradaSerializer


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
        nota = self.get_object()
        if nota.status != NotaFiscalEntrada.Status.ABERTA:
            return Response({"detail": "Somente notas abertas podem ser fechadas."}, status=status.HTTP_400_BAD_REQUEST)
        if nota.xml_importado and nota.itens_xml.filter(produto__isnull=True).exists():
            return Response({"detail": "Concilie todos os itens XML da NF-e antes de fechar a nota."}, status=status.HTTP_400_BAD_REQUEST)
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

    @action(detail=True, methods=["post"], url_path="cancelar")
    @transaction.atomic
    def cancelar(self, request, pk=None):
        nota = self.get_object()
        if nota.status == NotaFiscalEntrada.Status.CANCELADA:
            return Response(self.get_serializer(nota).data, status=status.HTTP_200_OK)

        before = nota.status
        estoque = {"disponivel": True, "movimentos": 0}
        if nota.status == NotaFiscalEntrada.Status.FECHADA:
            try:
                financeiro = self._cancelar_financeiro_nf(nota)
                self._validar_estoque_cancelamento(nota)
                estoque = self._movimentar_estoque_cancelamento(nota)
                custos = self._recalcular_custos_apos_cancelamento(nota)
            except ValueError as exc:
                transaction.set_rollback(True)
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        else:
            financeiro = {"disponivel": FIN_OK, "titulos_cancelados": 0}
            custos = {"skus_atualizados": 0, "produtos_atualizados": 0}

        nota.status = NotaFiscalEntrada.Status.CANCELADA
        nota.save(update_fields=["status", "atualizado_em"])
        recebimento = self._atualizar_recebimento_pedido(nota)
        necessidades = self._recalcular_necessidades_vinculadas(nota)
        _audit("notafiscalentrada", nota.pk, {"status": [before, nota.status]}, request, action="cancelar")
        data = self.get_serializer(nota).data
        data["estoque"] = estoque
        data["financeiro"] = financeiro
        data["custos"] = custos
        data["recebimento_pedido"] = recebimento
        data["necessidades"] = necessidades
        return Response(data, status=status.HTTP_200_OK)

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

    def _qtd_recebida_item(self, pedido_item):
        itens = NotaFiscalEntradaItem.objects.filter(
            pedido_item=pedido_item,
            nota__pedido_compra_id=pedido_item.pedido_id,
            nota__status=NotaFiscalEntrada.Status.FECHADA,
        )
        return sum(Decimal(item.qtd_recebida or 0) for item in itens)

    def _atualizar_recebimento_pedido(self, nota):
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
            recebida = self._qtd_recebida_item(item)
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

    def _movimentar_estoque_cancelamento(self, nota):
        documento = self._documento_estoque(nota, "CANCEL")
        if (
            EstoqueMovimentacao.objects.filter(documento=documento, tipo=EstoqueMovimentacao.TIPO_SAIDA).exists()
            or ProdutoUsoConsumoMovimentacao.objects.filter(documento=documento, tipo=ProdutoUsoConsumoMovimentacao.TIPO_AJUSTE_SAIDA).exists()
        ):
            return {"disponivel": True, "movimentos": 0, "ja_movimentada": True}

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
        if posterior < 0 and (nota.pedido_compra.loja.EstoqueNegativo or "NAO").upper() != "SIM":
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
        if posterior < 0 and (nota.pedido_compra.loja.EstoqueNegativo or "NAO").upper() != "SIM":
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
            raise ValueError("Não é possível cancelar a NF porque há parcelas do contas a pagar já baixadas.")
        if MovimentacaoFinanceira.objects.filter(pagar_item__in=itens_nf).exclude(status=MovimentacaoFinanceira.STATUS_CANCELADA).exists():
            raise ValueError("Não é possível cancelar a NF porque há movimentações financeiras vinculadas às parcelas.")

        modelo_previsao = None
        for titulo in titulos_nf:
            if modelo_previsao is None:
                modelo_previsao = titulo
            titulo.delete()

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
