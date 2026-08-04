from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError

from accounts.permissions import HasModuleRole
from cadastros.models import Loja

from .models import (
    Distribuicao,
    DistribuicaoDestino,
    MercadoriaTransito,
    PedidoVendaDistribuicao,
    PerfilDistribuicao,
    PerfilDistribuicaoItem,
)
from .serializers import (
    DistribuicaoDestinoSerializer,
    DistribuicaoListSerializer,
    DistribuicaoSerializer,
    MercadoriaTransitoSerializer,
    PedidoVendaDistribuicaoListSerializer,
    PedidoVendaDistribuicaoSerializer,
    PerfilDistribuicaoItemSerializer,
    PerfilDistribuicaoSerializer,
)
from .services import (
    aplicar_perfil,
    buscar_skus_disponiveis,
    cancelar_distribuicao,
    carregar_estoque,
    confirmar_distribuicao,
    confirmar_recebimento,
    confirmar_recebimento_nota,
    atualizar_pedido_item,
    faturar_pedido_distribuicao,
    gerar_notas_faturamento_distribuicao,
    gerar_pedidos,
    montar_matriz_manual,
    proximo_numero,
)


class BaseDistribuicaoViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModuleRole]
    required_module = "distribuicao"
    read_roles = ["Admin", "Diretor", "Gerente", "Auxiliar"]
    write_roles = ["Admin", "Diretor", "Gerente"]

    def _empresa_id_usuario(self):
        user = self.request.user
        if user.is_superuser:
            return self.request.query_params.get("empresa")
        return getattr(user, "empresa_id", None)

    def _handle_error(self, exc):
        if hasattr(exc, "message_dict"):
            return Response(exc.message_dict, status=status.HTTP_400_BAD_REQUEST)
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class PerfilDistribuicaoViewSet(BaseDistribuicaoViewSet):
    queryset = PerfilDistribuicao.objects.prefetch_related("itens", "itens__loja").all()
    serializer_class = PerfilDistribuicaoSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        search = self.request.query_params.get("search")
        ativo = self.request.query_params.get("ativo")
        if search:
            qs = qs.filter(Q(codigo__icontains=search) | Q(descricao__icontains=search))
        if ativo in {"true", "false", "1", "0"}:
            qs = qs.filter(ativo=ativo in {"true", "1"})
        return qs

    def perform_create(self, serializer):
        empresa_id = self._empresa_id_usuario()
        if not empresa_id:
            empresa_id = getattr(self.request.user, "empresa_id", None)
        if not empresa_id:
            raise ValidationError({"empresa": "Informe a empresa."})
        serializer.save(empresa_id=empresa_id, criado_por=self.request.user)

    def perform_update(self, serializer):
        empresa_id = self._empresa_id_usuario()
        if empresa_id and serializer.instance.empresa_id != int(empresa_id):
            raise ValidationError({"empresa": "Perfil pertence a outra empresa."})
        serializer.save()


class PerfilDistribuicaoItemViewSet(BaseDistribuicaoViewSet):
    queryset = PerfilDistribuicaoItem.objects.select_related("perfil", "loja").all()
    serializer_class = PerfilDistribuicaoItemSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(perfil__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        perfil = self.request.query_params.get("perfil")
        if perfil:
            qs = qs.filter(perfil_id=perfil)
        return qs


class DistribuicaoViewSet(BaseDistribuicaoViewSet):
    queryset = Distribuicao.objects.select_related("empresa", "unidade_origem", "perfil").prefetch_related("itens__destinos", "destinos__loja_destino", "pedidos_venda").all()
    serializer_class = DistribuicaoSerializer

    def get_serializer_class(self):
        if self.action == "list":
            return DistribuicaoListSerializer
        return DistribuicaoSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        search = self.request.query_params.get("search")
        status_q = self.request.query_params.get("status")
        origem = self.request.query_params.get("origem")
        data_ini = self.request.query_params.get("data_ini")
        data_fim = self.request.query_params.get("data_fim")
        if search:
            qs = qs.filter(Q(numero__icontains=search) | Q(observacao__icontains=search) | Q(unidade_origem__nome_loja__icontains=search))
        if status_q:
            qs = qs.filter(status=status_q)
        else:
            qs = qs.exclude(status=Distribuicao.STATUS_CANCELADA)
        if origem:
            qs = qs.filter(unidade_origem_id=origem)
        if data_ini:
            qs = qs.filter(data__gte=data_ini)
        if data_fim:
            qs = qs.filter(data__lte=data_fim)
        return qs

    def perform_create(self, serializer):
        origem = serializer.validated_data.get("unidade_origem")
        empresa_id = self._empresa_id_usuario()
        if not empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        if empresa_id and origem.empresa_id != int(empresa_id):
            raise ValidationError({"unidade_origem": "Origem pertence a outra empresa."})
        numero = proximo_numero(Distribuicao, origem.empresa_id, "DIST")
        serializer.save(empresa=origem.empresa, numero=numero, criado_por=self.request.user)

    def perform_update(self, serializer):
        if serializer.instance.status not in {Distribuicao.STATUS_RASCUNHO, Distribuicao.STATUS_CALCULADA}:
            raise ValidationError({"status": "Somente rascunho ou calculada pode ser editada."})
        serializer.save()

    @action(detail=False, methods=["get"], url_path="estoque-disponivel")
    def estoque_disponivel(self, request):
        empresa_id = self._empresa_id_usuario()
        origem = request.query_params.get("origem")
        if not empresa_id and origem:
            empresa_id = Loja.objects.filter(pk=origem).values_list("empresa_id", flat=True).first()
        if not empresa_id:
            raise ValidationError({"empresa": "Informe a empresa."})
        if not origem:
            raise ValidationError({"origem": "Informe a unidade de origem."})
        rows = buscar_skus_disponiveis(empresa_id, origem, request.query_params.get("search", ""))
        return Response([
            {
                "sku": row["sku"].pk,
                "produto": row["produto"].pk,
                "referencia": row["referencia"],
                "descricao": row["descricao"],
                "cor_descricao": row["cor_descricao"],
                "tamanho_descricao": row["tamanho_descricao"],
                "ean13": row["ean13"],
                "estoque_fisico": row["estoque_fisico"],
                "estoque_reservado": row["estoque_reservado"],
                "estoque_disponivel": row["estoque_disponivel"],
                "custo_unitario": row["custo_unitario"],
            }
            for row in rows
        ])

    @action(detail=True, methods=["post"], url_path="carregar-estoque")
    def carregar_estoque_action(self, request, pk=None):
        obj = self.get_object()
        try:
            qtd = carregar_estoque(
                obj,
                search=request.data.get("search", ""),
                quantidade=request.data.get("quantidade"),
                manter_minimo=request.data.get("manter_minimo", 0),
            )
            return Response({"itens_criados": qtd, "distribuicao": self.get_serializer(obj).data})
        except DjangoValidationError as exc:
            return self._handle_error(exc)

    @action(detail=True, methods=["post"], url_path="aplicar-perfil")
    def aplicar_perfil_action(self, request, pk=None):
        obj = self.get_object()
        perfil_id = request.data.get("perfil")
        if not perfil_id:
            raise ValidationError({"perfil": "Informe o perfil."})
        try:
            perfil = PerfilDistribuicao.objects.get(pk=perfil_id)
            aplicar_perfil(obj, perfil)
            obj.refresh_from_db()
            return Response(self.get_serializer(obj).data)
        except (PerfilDistribuicao.DoesNotExist, DjangoValidationError) as exc:
            return self._handle_error(exc)

    @action(detail=True, methods=["post"], url_path="montar-matriz")
    def montar_matriz_action(self, request, pk=None):
        obj = self.get_object()
        try:
            lojas = montar_matriz_manual(obj, request.data.get("lojas_destino") or None)
            obj.refresh_from_db()
            return Response({"lojas": lojas, "distribuicao": self.get_serializer(obj).data})
        except DjangoValidationError as exc:
            return self._handle_error(exc)

    @action(detail=True, methods=["post"], url_path="confirmar")
    def confirmar_action(self, request, pk=None):
        obj = self.get_object()
        try:
            confirmar_distribuicao(obj, request.user)
            obj.refresh_from_db()
            return Response(self.get_serializer(obj).data)
        except DjangoValidationError as exc:
            return self._handle_error(exc)

    @action(detail=True, methods=["post"], url_path="gerar-pedidos")
    def gerar_pedidos_action(self, request, pk=None):
        obj = self.get_object()
        try:
            pedidos = gerar_pedidos(obj)
            return Response(PedidoVendaDistribuicaoSerializer(pedidos, many=True).data)
        except DjangoValidationError as exc:
            return self._handle_error(exc)

    @action(detail=True, methods=["post"], url_path="cancelar")
    def cancelar_action(self, request, pk=None):
        obj = self.get_object()
        try:
            cancelar_distribuicao(obj, request.data.get("motivo", ""))
            obj.refresh_from_db()
            return Response(self.get_serializer(obj).data)
        except DjangoValidationError as exc:
            return self._handle_error(exc)

    @action(detail=True, methods=["post"], url_path="atualizar-destino")
    def atualizar_destino(self, request, pk=None):
        obj = self.get_object()
        if obj.status not in {Distribuicao.STATUS_RASCUNHO, Distribuicao.STATUS_CALCULADA}:
            raise ValidationError({"status": "Ajuste permitido apenas antes da confirmação."})
        destino = DistribuicaoDestino.objects.filter(distribuicao=obj, pk=request.data.get("destino")).first()
        if not destino:
            raise ValidationError({"destino": "Destino não encontrado."})
        destino.quantidade_ajustada = Decimal(str(request.data.get("quantidade", 0) or 0))
        destino.bloqueado_recalculo = bool(request.data.get("bloqueado_recalculo", destino.bloqueado_recalculo))
        destino.save(update_fields=["quantidade_ajustada", "bloqueado_recalculo"])
        obj.recomputar_totais()
        obj.save(update_fields=["quantidade_total", "valor_total_custo", "valor_total_venda", "atualizado_em"])
        return Response(DistribuicaoDestinoSerializer(destino).data)


class PedidoVendaDistribuicaoViewSet(BaseDistribuicaoViewSet):
    queryset = PedidoVendaDistribuicao.objects.select_related("empresa", "distribuicao", "unidade_origem", "loja_destino").prefetch_related("itens").all()
    serializer_class = PedidoVendaDistribuicaoSerializer

    def get_serializer_class(self):
        if self.action == "list":
            return PedidoVendaDistribuicaoListSerializer
        return PedidoVendaDistribuicaoSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        distribuicao = self.request.query_params.get("distribuicao")
        status_q = self.request.query_params.get("status")
        search = (self.request.query_params.get("search") or "").strip()
        if distribuicao:
            qs = qs.filter(distribuicao_id=distribuicao)
        if status_q:
            qs = qs.filter(status=status_q)
        if search:
            qs = qs.filter(Q(numero__icontains=search) | Q(distribuicao__numero__icontains=search) | Q(loja_destino__nome_loja__icontains=search))
        return qs

    @action(detail=True, methods=["post"], url_path="atualizar-item")
    def atualizar_item_action(self, request, pk=None):
        try:
            item = atualizar_pedido_item(
                self.get_object(),
                request.data.get("item"),
                quantidade=request.data.get("quantidade"),
                preco_unitario=request.data.get("preco_unitario"),
            )
            return Response(PedidoVendaDistribuicaoSerializer(item.pedido).data)
        except (DjangoValidationError, Exception) as exc:
            return self._handle_error(exc)

    @action(detail=True, methods=["post"], url_path="faturar")
    def faturar_action(self, request, pk=None):
        try:
            pedido = faturar_pedido_distribuicao(self.get_object(), request.user)
            return Response(PedidoVendaDistribuicaoSerializer(pedido).data)
        except DjangoValidationError as exc:
            return self._handle_error(exc)

    @action(detail=False, methods=["post"], url_path="gerar-notas")
    def gerar_notas_action(self, request):
        ids = request.data.get("pedidos") or request.data.get("ids") or []
        if not ids:
            raise ValidationError({"pedidos": "Selecione ao menos um pedido."})
        pedidos = self.get_queryset().filter(pk__in=ids)
        try:
            notas = gerar_notas_faturamento_distribuicao(pedidos, request.user)
            from fiscal.serializers import NotaFiscalSaidaSerializer
            return Response(NotaFiscalSaidaSerializer(notas, many=True).data)
        except DjangoValidationError as exc:
            return self._handle_error(exc)


class MercadoriaTransitoViewSet(BaseDistribuicaoViewSet):
    queryset = MercadoriaTransito.objects.select_related("pedido", "pedido_item", "distribuicao_destino", "unidade_origem", "loja_destino", "sku").all()
    serializer_class = MercadoriaTransitoSerializer
    read_roles = ["Admin", "Diretor", "Gerente", "Auxiliar", "Caixa"]
    write_roles = ["Admin", "Diretor", "Gerente", "Caixa"]
    action_roles = {
        "confirmar_recebimento_action": ["Admin", "Diretor", "Gerente", "Caixa"],
        "confirmar_nota_action": ["Admin", "Diretor", "Gerente", "Caixa"],
    }

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(pedido__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        user = self.request.user
        loja = self.request.query_params.get("loja")
        status_q = self.request.query_params.get("status")
        nfe = self.request.query_params.get("nfe")
        search = (self.request.query_params.get("search") or "").strip()
        if not user.is_superuser:
            lojas_ids = list(user.lojas.values_list("id", flat=True))
            if getattr(user, "loja_id", None) and user.loja_id not in lojas_ids:
                lojas_ids.append(user.loja_id)
            if lojas_ids:
                qs = qs.filter(loja_destino_id__in=lojas_ids)
        if loja:
            qs = qs.filter(loja_destino_id=loja)
        if status_q:
            qs = qs.filter(status=status_q)
        if nfe:
            qs = qs.filter(pedido__nfe_numero=nfe)
        if search:
            qs = qs.filter(
                Q(pedido__nfe_numero__icontains=search)
                | Q(pedido__numero__icontains=search)
                | Q(pedido_item__referencia__icontains=search)
                | Q(pedido_item__descricao__icontains=search)
                | Q(ean13__icontains=search)
                | Q(loja_destino__nome_loja__icontains=search)
            )
        return qs

    @action(detail=True, methods=["post"], url_path="confirmar-recebimento")
    def confirmar_recebimento_action(self, request, pk=None):
        try:
            obj = confirmar_recebimento(self.get_object(), request.data.get("quantidade_recebida", 0))
            return Response(self.get_serializer(obj).data)
        except DjangoValidationError as exc:
            return self._handle_error(exc)

    @action(detail=False, methods=["post"], url_path="confirmar-nota")
    def confirmar_nota_action(self, request):
        nfe = request.data.get("nfe_numero")
        loja = request.data.get("loja_destino")
        if not nfe:
            raise ValidationError({"nfe_numero": "Informe a NF-e."})
        qs = self.get_queryset().filter(pedido__nfe_numero=nfe, status=MercadoriaTransito.STATUS_EM_TRANSITO)
        if loja:
            qs = qs.filter(loja_destino_id=loja)
        try:
            recebidos = confirmar_recebimento_nota(qs.select_for_update(), request.data.get("itens") or [])
            return Response(self.get_serializer(recebidos, many=True).data)
        except DjangoValidationError as exc:
            return self._handle_error(exc)
