from rest_framework import viewsets, status
from rest_framework.permissions import BasePermission
from rest_framework.exceptions import ValidationError
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from django.db.models import Max, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from accounts.permissions import HasModuleRole
from accounts.services.effective_access import EDIT, VIEW, EffectiveAccessService

from auditoria.models import AuditAction, AuditCategory
from auditoria.services import AuditService

from .models import (
    Cotacao,
    CotacaoItem,
    PedidoCompra,
    PedidoCompraItem,
    PedidoCompraEntrega,
    PedidoCompraParcela,
    Requisicao,
    RequisicaoHistorico,
    RequisicaoItem,
    RequisicaoFinalidadeAquisicao,
    RequisicaoMaterialCategoria,
    RequisicaoServicoCategoria,
    RequisicaoSetor,
)
from .serializers import (
    CotacaoItemSerializer,
    CotacaoSerializer,
    PedidoCompraSerializer,
    PedidoCompraItemSerializer,
    PedidoCompraEntregaSerializer,
    PedidoCompraParcelaSerializer,
    RequisicaoHistoricoSerializer,
    RequisicaoItemSerializer,
    RequisicaoSerializer,
    RequisicaoFinalidadeAquisicaoSerializer,
    RequisicaoMaterialCategoriaSerializer,
    RequisicaoServicoCategoriaSerializer,
    RequisicaoSetorSerializer,
)

# Integração Financeiro
FIN_OK = True
try:
    from financeiro.models import (
        Pagar,
        PagarItem,
        FormaPagamento,
        FormaPagamentoParcela,
        PrazoPagamento,
        PrazoPagamentoParcela,
    )
except Exception:
    FIN_OK = False
    Pagar = PagarItem = FormaPagamento = FormaPagamentoParcela = PrazoPagamento = PrazoPagamentoParcela = None

# Natureza para aprovar
from cadastros.models import Nat_Lancamento
from cadastros.models import Loja
from produto.models import ProdutoUsoConsumoEstoque, ProdutoUsoConsumoMovimentacao


# ----------------- Auditoria robusta -----------------
def _audit(model_name: str, obj_id: str, changes: dict, request, action: str = "custom"):
    payload = {
        "action": AuditAction.OBJECT_UPDATED,
        "category": AuditCategory.PURCHASE,
        "request": request,
        "user": getattr(request, "user", None),
        "app_label": "compras",
        "model": model_name,
        "object_id": obj_id,
        "metadata": {"legacy_action": action, "changes": changes},
    }
    if transaction.get_connection().in_atomic_block:
        transaction.on_commit(lambda: AuditService.success(**payload))
    else:
        AuditService.success(**payload)


def _historico(requisicao, request, acao, status_anterior="", status_novo="", item=None, valor_anterior=None, valor_novo=None, observacao=""):
    hist = RequisicaoHistorico.objects.create(
        requisicao=requisicao,
        item=item,
        usuario=getattr(request, "user", None),
        acao=acao,
        status_anterior=status_anterior or "",
        status_novo=status_novo or "",
        valor_anterior=valor_anterior,
        valor_novo=valor_novo,
        observacao=observacao or "",
    )
    _audit(
        "requisicao",
        requisicao.pk,
        {
            "acao": acao,
            "item": getattr(item, "pk", None),
            "status": [status_anterior, status_novo],
            "valor_anterior": valor_anterior,
            "valor_novo": valor_novo,
            "observacao": observacao,
        },
        request,
        action=acao.lower(),
    )
    return hist


REQ_FAZER = "requisicoes.fazer"
REQ_APROVAR = "requisicoes.aprovar"
REQ_ATENDER = "requisicoes.atender"


def _requisicao_access(user):
    return EffectiveAccessService(user)


def _is_requisicao_admin(user):
    service = _requisicao_access(user)
    return bool(getattr(user, "is_superuser", False) or service.is_company_master())


def _can_manage_requisicao(user):
    return _requisicao_access(user).has_process_permission(REQ_ATENDER)


def _can_approve_requisicao(user):
    return _requisicao_access(user).has_process_permission(REQ_APROVAR)


def _can_request_requisicao(user):
    return _requisicao_access(user).has_process_permission(REQ_FAZER)


def _can_view_all_requisicao(user):
    return _is_requisicao_admin(user) or _can_manage_requisicao(user)


def _can_edit_requisicao_content(user, requisicao):
    if _is_requisicao_admin(user):
        return True
    return _can_request_requisicao(user) and requisicao.requisitante_id == user.id and requisicao.status in {"RASCUNHO", "DEVOLVIDA_CORRECAO"}


def _scope_requisicao_queryset(qs, user):
    if _is_requisicao_admin(user):
        return qs
    allowed = EffectiveAccessService(user).allowed_store_ids()
    if allowed is not None:
        qs = qs.filter(loja_id__in=allowed)
    return qs


def _has_any_requisicao_permission(user):
    return _can_request_requisicao(user) or _can_approve_requisicao(user) or _can_manage_requisicao(user)


class HasRequisicaoProcessAccess(BasePermission):
    message = "Usuário sem autorização de requisições."

    ACTION_CODES = {
        "create": [REQ_FAZER],
        "partial_update": [REQ_FAZER],
        "update": [REQ_FAZER],
        "enviar": [REQ_FAZER],
        "salvar_enviar": [REQ_FAZER],
        "cancelar": [REQ_FAZER],
        "aprovar": [REQ_APROVAR],
        "rejeitar": [REQ_APROVAR],
        "devolver": [REQ_APROVAR],
        "atender": [REQ_ATENDER],
        "aguardar_cotacao": [REQ_ATENDER],
        "ativar": [REQ_FAZER],
        "inativar": [REQ_FAZER],
    }

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        if _is_requisicao_admin(user):
            return True
        action = getattr(view, "action", None)
        if action in {"list", "retrieve", "lojas_permitidas"}:
            if action == "lojas_permitidas" and getattr(user, "type", "") == "Admin":
                return True
            return _has_any_requisicao_permission(user)
        codes = self.ACTION_CODES.get(action, [])
        return any(_requisicao_access(user).has_process_permission(code) for code in codes)


def _ensure_default_requisicao_servico_categorias(empresa_id):
    if not empresa_id:
        return
    nomes = [
        "Ar-condicionado",
        "Eletrica",
        "Hidraulica",
        "Informatica",
        "Impressoras",
        "Moveis",
        "Equipamentos",
        "Seguranca",
        "Limpeza",
        "Pintura",
        "Outros",
    ]
    for nome in nomes:
        RequisicaoServicoCategoria.objects.get_or_create(empresa_id=empresa_id, nome=nome)


def _ensure_default_requisicao_setores(empresa_id):
    if not empresa_id:
        return
    nomes = [
        "Administrativo",
        "Financeiro",
        "TI",
        "Almoxarifado",
        "Estoque",
        "Vendas",
        "Diretoria",
        "Manutencao",
    ]
    for nome in nomes:
        defaults = {}
        if nome in {"Almoxarifado", "Estoque"}:
            defaults["controla_estoque_uso_consumo"] = True
        RequisicaoSetor.objects.get_or_create(empresa_id=empresa_id, nome=nome, defaults=defaults)


def _ensure_default_requisicao_material_categorias(empresa_id):
    if not empresa_id:
        return
    nomes = [
        "Informática",
        "Mobiliário",
        "Equipamentos",
        "Material de escritório",
        "Limpeza",
        "Segurança",
        "Comunicação",
        "Outros",
    ]
    for nome in nomes:
        RequisicaoMaterialCategoria.objects.get_or_create(empresa_id=empresa_id, nome=nome)


def _ensure_default_requisicao_finalidades(empresa_id):
    if not empresa_id:
        return
    defaults = [
        ("Uso e Consumo", "USO_CONSUMO"),
        ("Estoque/Almoxarifado", "ALMOXARIFADO"),
        ("Imobilizado", "IMOBILIZADO"),
        ("Outro", "OUTRO"),
    ]
    for nome, comportamento in defaults:
        if not RequisicaoFinalidadeAquisicao.objects.filter(empresa_id=empresa_id, comportamento=comportamento).exists():
            RequisicaoFinalidadeAquisicao.objects.create(empresa_id=empresa_id, comportamento=comportamento, nome=nome)


def _recalcular_status_requisicao(req):
    itens = list(req.itens.all())
    if not itens or req.status in {"RASCUNHO", "SOLICITADA", "AGUARDANDO_APROVACAO", "REJEITADA", "CANCELADA"}:
        return req.status
    statuses = {i.status for i in itens}
    if statuses and statuses <= {"ATENDIDO", "SERVICO_CONCLUIDO", "RECEBIDO"}:
        return "CONCLUIDA"
    if any(i.qtd_atendida and i.qtd_atendida > 0 for i in itens):
        return "ATENDIDA_PARCIALMENTE"
    if statuses & {"AGUARDANDO_COTACAO", "EM_COTACAO", "PEDIDO_GERADO", "AGUARDANDO_RECEBIMENTO"}:
        return "EM_PROCESSO_COMPRA"
    return "EM_ATENDIMENTO" if req.status == "APROVADA" else req.status


def _parcelas_configuradas(pedido: PedidoCompra):
    if not FIN_OK or not pedido.forma_pagamento:
        return None, None, []
    forma = (
        FormaPagamento.objects
        .filter(Q(empresa=pedido.empresa) | Q(empresa__isnull=True), codigo=pedido.forma_pagamento, ativo=True)
        .first()
    )
    prazo = pedido.prazo_pagamento
    if prazo:
        cfg = list(PrazoPagamentoParcela.objects.filter(prazo=prazo).order_by("ordem"))
    elif forma:
        prazo = forma.prazo_pagamento
        cfg = list(FormaPagamentoParcela.objects.filter(forma=forma).order_by("ordem"))
    else:
        cfg = []
    return forma, prazo, cfg


def _sincronizar_parcelas_planejadas(pedido: PedidoCompra, request=None, motivo="sync_total"):
    if not FIN_OK or not pedido.forma_pagamento:
        return
    forma, prazo, cfg = _parcelas_configuradas(pedido)
    if not forma or not cfg:
        raise ValidationError({"parcelas": "Não foi possível regenerar as parcelas planejadas."})

    total = Decimal(pedido.total_pedido or 0).quantize(Decimal("0.01"))
    emissao = pedido.emissao
    PedidoCompraParcela.objects.filter(pedido=pedido, status="PLAN").delete()
    restante = total
    n = len(cfg)
    vals = []
    for i, par in enumerate(cfg, start=1):
        if par.percentual is not None:
            perc = Decimal(str(par.percentual))
            if perc > 1:
                perc = perc / Decimal("100")
            val = (total * perc).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        elif i < n:
            val = (total / n).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            val = restante.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        restante -= val
        vencto = emissao + timedelta(days=int(par.dias))
        PedidoCompraParcela.objects.create(
            pedido=pedido,
            parcela_n=i,
            vencimento=vencto,
            valor=val,
            percentual=par.percentual,
            origem="FORMA",
            status="PLAN",
        )
        vals.append({"parcela_n": i, "vencimento": vencto.isoformat(), "valor": float(val)})
    if request:
        _audit("pedidocompra", pedido.pk, {"parcelas_regeneradas": vals, "motivo": motivo}, request, action="sync_parcelas")


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


# ----------------- Pedido -----------------
class CotacaoViewSet(BaseViewSet):
    queryset = Cotacao.objects.select_related("empresa", "loja", "responsavel").all()
    serializer_class = CotacaoSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        loja = self.request.query_params.get("loja")
        status_q = self.request.query_params.get("status")
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        allowed = EffectiveAccessService(self.request.user).allowed_store_ids()
        if allowed is not None:
            qs = qs.filter(loja_id__in=allowed)
        if loja:
            qs = qs.filter(loja_id=loja)
        if status_q:
            qs = qs.filter(status=status_q)
        return qs.order_by("-data_abertura", "-numero")

    def _validate_loja(self, loja):
        empresa_id = self._empresa_id_usuario()
        if not loja:
            raise ValidationError({"loja": "Informe a loja."})
        if empresa_id and loja.empresa_id != int(empresa_id):
            raise ValidationError({"loja": "A loja informada pertence a outra empresa."})
        if not EffectiveAccessService(self.request.user).can_access_store(loja):
            raise ValidationError({"loja": "Loja fora do escopo permitido."})

    def perform_create(self, serializer):
        loja = serializer.validated_data.get("loja")
        self._validate_loja(loja)
        serializer.save(empresa=loja.empresa, responsavel=self.request.user, status="EM_ELABORACAO")

    def perform_update(self, serializer):
        if serializer.instance.status != "EM_ELABORACAO":
            raise ValidationError({"status": "Somente cotações em elaboração podem ser editadas."})
        loja = serializer.validated_data.get("loja") or serializer.instance.loja
        self._validate_loja(loja)
        serializer.save(empresa=loja.empresa)


class CotacaoItemViewSet(BaseViewSet):
    queryset = CotacaoItem.objects.select_related("cotacao", "cotacao__empresa", "produto", "unidade", "requisicao_item_origem", "requisicao_item_origem__requisicao").all()
    serializer_class = CotacaoItemSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        cotacao = self.request.query_params.get("cotacao")
        if empresa_id:
            qs = qs.filter(cotacao__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        allowed = EffectiveAccessService(self.request.user).allowed_store_ids()
        if allowed is not None:
            qs = qs.filter(cotacao__loja_id__in=allowed)
        if cotacao:
            qs = qs.filter(cotacao_id=cotacao)
        return qs.order_by("id")

    def perform_destroy(self, instance):
        if instance.cotacao.status != "EM_ELABORACAO":
            raise ValidationError({"cotacao": "Somente cotações em elaboração podem excluir itens."})
        instance.delete()


class PedidoCompraViewSet(BaseViewSet):
    queryset = PedidoCompra.objects.all().order_by("-emissao", "-id")
    serializer_class = PedidoCompraSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        tipo = self.request.query_params.get("tipo")
        status_q = self.request.query_params.get("status")
        loja = self.request.query_params.get("loja")
        fornecedor = self.request.query_params.get("fornecedor")
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        if tipo in ("1", "2", "4"):
            qs = qs.filter(tipo=tipo)
        if status_q:
            qs = qs.filter(status=status_q)
        if loja:
            qs = qs.filter(loja_id=loja)
        if fornecedor:
            qs = qs.filter(fornecedor_id=fornecedor)
        return qs

    def perform_create(self, serializer):
        loja = serializer.validated_data.get("loja")
        fornecedor = serializer.validated_data.get("fornecedor")
        empresa_id = self._empresa_id_usuario()
        if not empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        if empresa_id and loja.empresa_id != int(empresa_id):
            raise ValidationError({"loja": "A loja informada pertence a outra empresa."})
        if loja.empresa_id and fornecedor.empresa_id and loja.empresa_id != fornecedor.empresa_id:
            raise ValidationError({"fornecedor": "O fornecedor informado pertence a outra empresa."})
        if fornecedor.ativo is False:
            raise ValidationError({"fornecedor": "Fornecedor inativo não pode ser utilizado em novo pedido."})
        if fornecedor.bloqueio:
            raise ValidationError({"fornecedor": "Fornecedor bloqueado não pode ser utilizado em novo pedido."})
        serializer.save(empresa=loja.empresa)

    def perform_update(self, serializer):
        loja = serializer.validated_data.get("loja") or serializer.instance.loja
        fornecedor = serializer.validated_data.get("fornecedor") or serializer.instance.fornecedor
        empresa_id = serializer.instance.empresa_id or getattr(loja, "empresa_id", None)
        user_empresa_id = self._empresa_id_usuario()
        if not user_empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        if user_empresa_id and empresa_id and int(user_empresa_id) != empresa_id:
            raise ValidationError({"empresa": "Pedido pertence a outra empresa."})
        if loja.empresa_id and fornecedor.empresa_id and loja.empresa_id != fornecedor.empresa_id:
            raise ValidationError({"fornecedor": "O fornecedor informado pertence a outra empresa."})
        if serializer.instance.status == "AB" and fornecedor.ativo is False:
            raise ValidationError({"fornecedor": "Fornecedor inativo não pode ser utilizado em novo pedido."})
        if serializer.instance.status == "AB" and fornecedor.bloqueio:
            raise ValidationError({"fornecedor": "Fornecedor bloqueado não pode ser utilizado em novo pedido."})
        obj = serializer.save(empresa=loja.empresa)
        obj.recomputa_totais()
        obj.save(update_fields=["total_itens", "total_desconto", "frete", "total_pedido"])
        _sincronizar_parcelas_planejadas(obj, self.request, motivo="alteracao_cabecalho")

    def perform_destroy(self, instance):
        if instance.status != "AB":
            raise ValidationError({"detail": "Somente pedidos em aberto (AB) podem ser excluídos."})
        instance.delete()

    @action(detail=True, methods=["post"], url_path="set-forma-pagamento")
    @transaction.atomic
    def set_forma_pagamento(self, request, pk=None):
        """
        Seta forma de pagamento (por id ou codigo) e RECRIA as parcelas planejadas (PLAN)
        em compras_pedido_compra_parcela com base em FormaPagamentoParcela.
        Body: {"id_forma": 2} ou {"codigo_forma":"30/60", "id_prazo": 1}.
        """
        obj: PedidoCompra = self.get_object()
        if obj.status != "AB":
            return Response({"detail": "Somente AB"}, status=status.HTTP_400_BAD_REQUEST)
        if not FIN_OK:
            return Response({"detail": "Financeiro indisponível"}, status=status.HTTP_400_BAD_REQUEST)

        id_forma = request.data.get("id_forma")
        codigo = (request.data.get("codigo_forma") or "").strip()
        id_prazo = request.data.get("id_prazo") or request.data.get("prazo_pagamento")
        codigo_prazo = (request.data.get("codigo_prazo") or "").strip()

        try:
            if id_forma:
                forma = FormaPagamento.objects.filter(Q(empresa=obj.empresa) | Q(empresa__isnull=True), pk=id_forma, ativo=True).get()
            elif codigo:
                forma = FormaPagamento.objects.filter(Q(empresa=obj.empresa) | Q(empresa__isnull=True), codigo=codigo, ativo=True).get()
            else:
                return Response({"detail": "Informe id_forma ou codigo_forma"}, status=status.HTTP_400_BAD_REQUEST)
        except FormaPagamento.DoesNotExist:
            return Response({"detail": "Forma não encontrada/inativa"}, status=status.HTTP_400_BAD_REQUEST)

        prazo = None
        if id_prazo or codigo_prazo:
            try:
                if id_prazo:
                    prazo = PrazoPagamento.objects.filter(Q(empresa=obj.empresa) | Q(empresa__isnull=True), pk=id_prazo, ativo=True).get()
                else:
                    prazo = PrazoPagamento.objects.filter(Q(empresa=obj.empresa) | Q(empresa__isnull=True), codigo=codigo_prazo, ativo=True).get()
            except PrazoPagamento.DoesNotExist:
                return Response({"detail": "Prazo não encontrado/inativo"}, status=status.HTTP_400_BAD_REQUEST)
        else:
            prazo = forma.prazo_pagamento

        if prazo:
            cfg = list(PrazoPagamentoParcela.objects.filter(prazo=prazo).order_by("ordem"))
        else:
            cfg = list(FormaPagamentoParcela.objects.filter(forma=forma).order_by("ordem"))
        if not cfg:
            return Response({"detail": "Prazo sem parcelas configuradas"}, status=status.HTTP_400_BAD_REQUEST)

        obj.recomputa_totais()
        obj.save(update_fields=["total_itens", "total_desconto", "frete", "total_pedido"])

        before = obj.forma_pagamento
        before_prazo = obj.prazo_pagamento_id
        obj.forma_pagamento = forma.codigo
        obj.prazo_pagamento = prazo
        obj.save(update_fields=["forma_pagamento", "prazo_pagamento"])
        _sincronizar_parcelas_planejadas(obj, request, motivo="set_forma_pagamento")

        _audit(
            "pedidocompra",
            obj.pk,
            {"set_forma": {"before": before, "after": forma.codigo}, "set_prazo": {"before": before_prazo, "after": getattr(prazo, "pk", None)}},
            request,
            action="set_forma_pagto",
        )
        return Response(self.get_serializer(obj).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="aprovar")
    @transaction.atomic
    def aprovar(self, request, pk=None):
        """
        Aprova pedido (AB->AP) e gera títulos PREVISTO no Financeiro a partir das parcelas PLAN.

        Body esperado:
            {"idnatureza": <ID obrigatório>}
        """
        obj: PedidoCompra = self.get_object()

        # 1) Status precisa ser AB
        if (obj.status or "").upper() != "AB":
            return Response(
                {"detail": "Só é possível aprovar pedidos em aberto (AB)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 2) Financeiro disponível
        if not FIN_OK:
            return Response(
                {"detail": "Financeiro indisponível para aprovação."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not obj.itens.exists():
            return Response({"detail": "Inclua ao menos um item antes de aprovar."}, status=status.HTTP_400_BAD_REQUEST)
        tipos_itens = set(obj.itens.values_list("produto__tipo_produto", flat=True))
        if len(tipos_itens) != 1 or obj.tipo not in tipos_itens or obj.tipo not in ("1", "2", "4"):
            return Response({"detail": "Pedido possui tipo indefinido ou itens de tipos diferentes."}, status=status.HTTP_400_BAD_REQUEST)

        # 3) Forma de pagamento precisa estar definida
        if not obj.forma_pagamento:
            return Response(
                {"detail": "Defina a forma de pagamento / gere parcelas antes de aprovar."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 4) Natureza (idnatureza) obrigatória e válida
        raw_idnat = request.data.get("idnatureza")
        try:
            idnat = int(raw_idnat)
        except (TypeError, ValueError):
            return Response(
                {"detail": 'Informe "idnatureza" numérico no corpo da requisição.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if idnat <= 0:
            return Response(
                {"detail": '"idnatureza" deve ser maior que zero.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            natureza = Nat_Lancamento.objects.get(pk=idnat)
        except Nat_Lancamento.DoesNotExist:
            return Response(
                {"detail": f"Natureza de lançamento {idnat} não encontrada."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if natureza.empresa_id and obj.empresa_id and natureza.empresa_id != obj.empresa_id:
            return Response(
                {"detail": "A natureza informada pertence a outra empresa."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 5) Recalcula totais para garantir consistência
        obj.recomputa_totais()
        obj.save(update_fields=["total_itens", "total_desconto", "frete", "total_pedido"])
        total = Decimal(obj.total_pedido or 0).quantize(Decimal("0.01"))
        if total <= 0:
            return Response({"detail": "Total do pedido deve ser maior que zero."}, status=status.HTTP_400_BAD_REQUEST)

        # 6) Parcelas PLAN obrigatórias
        parcelas_plan = list(
            PedidoCompraParcela.objects.filter(pedido=obj, status="PLAN").order_by("parcela_n")
        )
        if not parcelas_plan:
            return Response(
                {"detail": "Defina a forma de pagamento / parcelas antes de aprovar."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        soma = sum(Decimal(p.valor).quantize(Decimal("0.01")) for p in parcelas_plan)

        # tolerância de 1 centavo para arredondamento
        if (soma - total).copy_abs() > Decimal("0.01"):
            return Response(
                {"detail": f"Soma das parcelas ({soma}) difere do total do pedido ({total})."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 7) Cria Pagar (PREVISTO)
        pagar = Pagar.objects.create(
            empresa=obj.empresa,
            idloja=obj.loja,
            idfornecedor=obj.fornecedor,
            Titulo=f"PC {obj.id}",
            Documento=None,
            Data_emissao=obj.emissao,
            Valor_total=total,
            Previsao=True,
            FormaPagamento=obj.forma_pagamento,
            Idnatureza=natureza,
            conta_contabil=None,
            pedido_compra=obj.id,
            nfe_id=None,
        )

        # 8) Cria itens PagarItem a partir das parcelas
        for p in parcelas_plan:
            pi = PagarItem.objects.create(
                Idpagar=pagar,
                parcela_n=p.parcela_n,
                status=getattr(PagarItem, "STATUS_PREVISTO", "PREVISTO"),
                Data_vencimento=p.vencimento,
                valor_parcela=p.valor,
                FormaPagamento=obj.forma_pagamento,
                idconta=None,
                juros=0,
                desconto=0,
                data_baixa=None,
                valor_baixa=None,
                Previsao=True,
                Idnatureza=natureza,
            )
            p.status = "GERADA"
            # vínculo pelo campo inteiro pagar_item_id (já existente no model/tabela)
            p.pagar_item_id = getattr(pi, "Idpagaritem", pi.pk)
            p.save(update_fields=["status", "pagar_item_id"])

        # 9) Atualiza status do pedido para AP
        before = obj.status
        obj.status = "AP"
        obj.save(update_fields=["status"])

        _audit(
            "pedidocompra",
            obj.pk,
            {"status": [before, "AP"], "pagar_id": getattr(pagar, "Idpagar", pagar.pk)},
            request,
            action="aprovar",
        )

        return Response(self.get_serializer(obj).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="alterar-natureza")
    @transaction.atomic
    def alterar_natureza(self, request, pk=None):
        """
        Altera a natureza do contas a pagar gerado pelo pedido.
        Parcelas já baixadas não são alteradas para preservar o histórico financeiro.
        """
        obj: PedidoCompra = self.get_object()
        if not FIN_OK:
            return Response(
                {"detail": "Financeiro indisponível."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if (obj.status or "").upper() not in ("AP", "AT"):
            return Response(
                {"detail": "A natureza só pode ser alterada em pedido aprovado ou atendido."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_idnat = request.data.get("idnatureza")
        try:
            idnat = int(raw_idnat)
        except (TypeError, ValueError):
            return Response(
                {"detail": 'Informe "idnatureza" numérico no corpo da requisição.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            natureza = Nat_Lancamento.objects.get(pk=idnat)
        except Nat_Lancamento.DoesNotExist:
            return Response(
                {"detail": f"Natureza de lançamento {idnat} não encontrada."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if natureza.empresa_id and obj.empresa_id and natureza.empresa_id != obj.empresa_id:
            return Response(
                {"detail": "A natureza informada pertence a outra empresa."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pagar = (
            Pagar.objects
            .select_for_update()
            .filter(empresa=obj.empresa, pedido_compra=obj.id)
            .order_by("-Idpagar")
            .first()
        )
        if not pagar:
            return Response(
                {"detail": "Não existe contas a pagar gerado para este pedido."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        anterior = pagar.Idnatureza_id
        pagar.Idnatureza = natureza
        pagar.save(update_fields=["Idnatureza"])

        itens_editaveis = pagar.itens.exclude(status=getattr(PagarItem, "STATUS_BAIXADO", "BAIXADO"))
        alteradas = itens_editaveis.update(Idnatureza=natureza)
        baixadas = pagar.itens.filter(status=getattr(PagarItem, "STATUS_BAIXADO", "BAIXADO")).count()

        _audit(
            "pedidocompra",
            obj.pk,
            {
                "natureza": [anterior, natureza.pk],
                "pagar_id": getattr(pagar, "Idpagar", pagar.pk),
                "parcelas_alteradas": alteradas,
                "parcelas_baixadas_preservadas": baixadas,
            },
            request,
            action="alt_natureza",
        )

        return Response(self.get_serializer(obj).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='cancelar')
    @transaction.atomic
    def cancelar(self, request, pk=None):
        """
        Cancela um pedido de compra EM ABERTO (status = 'AB').

        - Não mexe em financeiro porque só permitimos cancelar pedidos em AB.
        """
        pedido: PedidoCompra = self.get_object()

        # Só permite cancelar se estiver em aberto
        if (pedido.status or '').upper() != 'AB':
            return Response(
                {'detail': 'Só é possível cancelar pedidos em aberto (AB).'},
                status=status.HTTP_400_BAD_REQUEST
            )

        antes = pedido.status
        pedido.status = 'CA'  # código de cancelado
        pedido.save(update_fields=['status'])

        _audit(
            'pedidocompra',
            pedido.pk,
            {'status': [antes, pedido.status]},
            request,
            action='cancelar',
        )

        ser = PedidoCompraSerializer(pedido)
        return Response(ser.data, status=status.HTTP_200_OK)


# ----------------- Itens -----------------
class PedidoCompraItemViewSet(BaseViewSet):
    queryset = PedidoCompraItem.objects.all().order_by("pedido_id", "id")
    serializer_class = PedidoCompraItemSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        pedido = self.request.query_params.get("pedido")
        if empresa_id:
            qs = qs.filter(pedido__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        if pedido:
            qs = qs.filter(pedido_id=pedido)
        return qs

    def perform_create(self, serializer):
        self._validar_item_empresa(serializer.validated_data)
        item = serializer.save()
        _sincronizar_parcelas_planejadas(item.pedido, self.request, motivo="alteracao_item")

    def perform_update(self, serializer):
        data = {**serializer.validated_data}
        data.setdefault("pedido", serializer.instance.pedido)
        data.setdefault("produto", serializer.validated_data.get("produto", serializer.instance.produto))
        data.setdefault("pack", serializer.validated_data.get("pack", serializer.instance.pack))
        self._validar_item_empresa(data)
        item = serializer.save()
        _sincronizar_parcelas_planejadas(item.pedido, self.request, motivo="alteracao_item")

    @transaction.atomic
    def perform_destroy(self, instance):
        pedido = instance.pedido
        if pedido.status != "AB":
            raise ValidationError({"pedido": "Somente pedidos em aberto (AB) permitem exclusão de itens."})
        instance.delete()
        pedido.recomputa_totais()
        update_fields = ["total_itens", "total_desconto", "frete", "total_pedido"]
        if not pedido.itens.exists() and pedido.tipo:
            pedido.tipo = ""
            update_fields.append("tipo")
        pedido.save(update_fields=update_fields)
        _sincronizar_parcelas_planejadas(pedido, self.request, motivo="exclusao_item")

    def _validar_item_empresa(self, data):
        pedido = data.get("pedido")
        produto = data.get("produto")
        pack = data.get("pack")
        empresa_id = pedido.empresa_id if pedido else None
        user_empresa_id = self._empresa_id_usuario()
        if not user_empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        if user_empresa_id and empresa_id and int(user_empresa_id) != empresa_id:
            raise ValidationError({"pedido": "Pedido pertence a outra empresa."})
        if produto and produto.empresa_id and empresa_id and produto.empresa_id != empresa_id:
            raise ValidationError({"produto": "Produto pertence a outra empresa."})
        if pack and pack.empresa_id and empresa_id and pack.empresa_id != empresa_id:
            raise ValidationError({"pack": "Pack pertence a outra empresa."})
        if produto and pack and produto.grade_id and pack.grade_id != produto.grade_id:
            raise ValidationError({"pack": "Pack incompatível com a grade do produto."})
        if produto and produto.tipo_produto == "3":
            raise ValidationError({"produto": "Produto de fabricação própria não participa de Compras."})
        if pedido and pedido.status != "AB":
            raise ValidationError({"pedido": "Somente pedidos em aberto (AB) permitem alteração de itens."})


# ----------------- Entregas -----------------
class PedidoCompraEntregaViewSet(BaseViewSet):
    queryset = PedidoCompraEntrega.objects.all().order_by("item_id", "id")
    serializer_class = PedidoCompraEntregaSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        pedido = self.request.query_params.get("pedido")
        if empresa_id:
            qs = qs.filter(item__pedido__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        if pedido:
            qs = qs.filter(item__pedido_id=pedido)
        return qs


# ----------------- Parcelas (planejamento) -----------------
class PedidoCompraParcelaViewSet(BaseViewSet):
    queryset = PedidoCompraParcela.objects.all().order_by("pedido_id", "parcela_n")
    serializer_class = PedidoCompraParcelaSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        pedido = self.request.query_params.get("pedido")
        if empresa_id:
            qs = qs.filter(pedido__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        if pedido:
            qs = qs.filter(pedido_id=pedido)
        status_q = self.request.query_params.get("status")
        if status_q:
            qs = qs.filter(status=status_q)
        return qs


class RequisicaoServicoCategoriaViewSet(BaseViewSet):
    queryset = RequisicaoServicoCategoria.objects.all().order_by("nome")
    serializer_class = RequisicaoServicoCategoriaSerializer
    permission_classes = [HasRequisicaoProcessAccess]

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            _ensure_default_requisicao_servico_categorias(empresa_id)
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        ativo = self.request.query_params.get("ativo")
        if ativo is not None:
            qs = qs.filter(ativo=str(ativo).lower() in {"1", "true", "sim"})
        return qs

    def perform_create(self, serializer):
        empresa_id = self._empresa_id_usuario()
        if not empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        empresa = self.request.user.empresa if empresa_id else serializer.validated_data.get("empresa")
        obj = serializer.save(empresa=empresa)
        _audit("requisicaoservicocategoria", obj.pk, {"created": True}, self.request, action="create")


class RequisicaoSetorViewSet(BaseViewSet):
    queryset = RequisicaoSetor.objects.all().order_by("nome")
    serializer_class = RequisicaoSetorSerializer
    permission_classes = [HasRequisicaoProcessAccess]

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            _ensure_default_requisicao_setores(empresa_id)
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        search = self.request.query_params.get("search")
        empresa = self.request.query_params.get("empresa")
        if self.request.user.is_superuser and empresa:
            qs = qs.filter(empresa_id=empresa)
        if search:
            qs = qs.filter(Q(nome__icontains=search) | Q(descricao__icontains=search))
        ativo = self.request.query_params.get("ativo")
        if ativo is not None:
            qs = qs.filter(ativo=str(ativo).lower() in {"1", "true", "sim"})
        pode_fazer = self.request.query_params.get("pode_fazer_requisicao")
        if pode_fazer is not None:
            qs = qs.filter(pode_fazer_requisicao=str(pode_fazer).lower() in {"1", "true", "sim"})
        return qs

    def perform_create(self, serializer):
        empresa_id = self._empresa_id_usuario()
        if not empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        empresa_payload = self.request.data.get("empresa")
        if empresa_id and empresa_payload and int(empresa_payload) != int(empresa_id):
            raise ValidationError({"empresa": "Usuário não pode criar setor para outra empresa."})
        empresa = self.request.user.empresa if empresa_id else serializer.validated_data.get("empresa")
        obj = serializer.save(empresa=empresa)
        _audit("requisicaosetor", obj.pk, {"created": True}, self.request, action="create")

    def perform_update(self, serializer):
        empresa_id = self._empresa_id_usuario()
        obj = serializer.instance
        if empresa_id and obj.empresa_id != int(empresa_id):
            raise ValidationError({"empresa": "Setor pertence a outra empresa."})
        updated = serializer.save(empresa=obj.empresa)
        _audit("requisicaosetor", updated.pk, {"updated": True}, self.request, action="update")

    def destroy(self, request, *args, **kwargs):
        return Response({"detail": "Exclusão física de setor não é permitida. Utilize inativação."}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    @action(detail=True, methods=["post"], url_path="ativar")
    def ativar(self, request, pk=None):
        obj = self.get_object()
        before = obj.ativo
        obj.ativo = True
        obj.save(update_fields=["ativo"])
        _audit("requisicaosetor", obj.pk, {"ativo": [before, True]}, request, action="ativar")
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"], url_path="inativar")
    def inativar(self, request, pk=None):
        obj = self.get_object()
        before = obj.ativo
        obj.ativo = False
        obj.save(update_fields=["ativo"])
        _audit("requisicaosetor", obj.pk, {"ativo": [before, False]}, request, action="inativar")
        return Response(self.get_serializer(obj).data)


class RequisicaoMaterialCategoriaViewSet(BaseViewSet):
    queryset = RequisicaoMaterialCategoria.objects.all().order_by("nome")
    serializer_class = RequisicaoMaterialCategoriaSerializer
    permission_classes = [HasRequisicaoProcessAccess]

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            _ensure_default_requisicao_material_categorias(empresa_id)
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        empresa = self.request.query_params.get("empresa")
        if self.request.user.is_superuser and empresa:
            qs = qs.filter(empresa_id=empresa)
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(Q(nome__icontains=search) | Q(descricao__icontains=search))
        ativo = self.request.query_params.get("ativo")
        if ativo is not None:
            qs = qs.filter(ativo=str(ativo).lower() in {"1", "true", "sim"})
        return qs

    def perform_create(self, serializer):
        empresa_id = self._empresa_id_usuario()
        if not empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        empresa_payload = self.request.data.get("empresa")
        if empresa_id and empresa_payload and int(empresa_payload) != int(empresa_id):
            raise ValidationError({"empresa": "Usuário não pode criar categoria para outra empresa."})
        empresa = self.request.user.empresa if empresa_id else serializer.validated_data.get("empresa")
        obj = serializer.save(empresa=empresa)
        _audit("requisicaomaterialcategoria", obj.pk, {"created": True}, self.request, action="create")

    def perform_update(self, serializer):
        empresa_id = self._empresa_id_usuario()
        obj = serializer.instance
        if empresa_id and obj.empresa_id != int(empresa_id):
            raise ValidationError({"empresa": "Categoria pertence a outra empresa."})
        updated = serializer.save(empresa=obj.empresa)
        _audit("requisicaomaterialcategoria", updated.pk, {"updated": True}, self.request, action="update")

    def destroy(self, request, *args, **kwargs):
        return Response({"detail": "Exclusão física de categoria de material não é permitida. Utilize inativação."}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    @action(detail=True, methods=["post"], url_path="ativar")
    def ativar(self, request, pk=None):
        obj = self.get_object()
        before = obj.ativo
        obj.ativo = True
        obj.save(update_fields=["ativo"])
        _audit("requisicaomaterialcategoria", obj.pk, {"ativo": [before, True]}, request, action="ativar")
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"], url_path="inativar")
    def inativar(self, request, pk=None):
        obj = self.get_object()
        before = obj.ativo
        obj.ativo = False
        obj.save(update_fields=["ativo"])
        _audit("requisicaomaterialcategoria", obj.pk, {"ativo": [before, False]}, request, action="inativar")
        return Response(self.get_serializer(obj).data)


class RequisicaoFinalidadeAquisicaoViewSet(BaseViewSet):
    queryset = RequisicaoFinalidadeAquisicao.objects.all().order_by("nome")
    serializer_class = RequisicaoFinalidadeAquisicaoSerializer
    permission_classes = [HasRequisicaoProcessAccess]

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            _ensure_default_requisicao_finalidades(empresa_id)
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        empresa = self.request.query_params.get("empresa")
        if self.request.user.is_superuser and empresa:
            qs = qs.filter(empresa_id=empresa)
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(Q(nome__icontains=search) | Q(descricao__icontains=search) | Q(comportamento__icontains=search))
        ativo = self.request.query_params.get("ativo")
        if ativo is not None:
            qs = qs.filter(ativo=str(ativo).lower() in {"1", "true", "sim"})
        return qs

    def perform_create(self, serializer):
        empresa_id = self._empresa_id_usuario()
        if not empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        empresa_payload = self.request.data.get("empresa")
        if empresa_id and empresa_payload and int(empresa_payload) != int(empresa_id):
            raise ValidationError({"empresa": "Usuário não pode criar finalidade para outra empresa."})
        empresa = self.request.user.empresa if empresa_id else serializer.validated_data.get("empresa")
        obj = serializer.save(empresa=empresa)
        _audit("requisicaofinalidadeaquisicao", obj.pk, {"created": True}, self.request, action="create")

    def perform_update(self, serializer):
        empresa_id = self._empresa_id_usuario()
        obj = serializer.instance
        if empresa_id and obj.empresa_id != int(empresa_id):
            raise ValidationError({"empresa": "Finalidade pertence a outra empresa."})
        updated = serializer.save(empresa=obj.empresa)
        _audit("requisicaofinalidadeaquisicao", updated.pk, {"updated": True}, self.request, action="update")

    def destroy(self, request, *args, **kwargs):
        return Response({"detail": "Exclusão física de finalidade não é permitida. Utilize inativação."}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    @action(detail=True, methods=["post"], url_path="ativar")
    def ativar(self, request, pk=None):
        obj = self.get_object()
        before = obj.ativo
        obj.ativo = True
        obj.save(update_fields=["ativo"])
        _audit("requisicaofinalidadeaquisicao", obj.pk, {"ativo": [before, True]}, request, action="ativar")
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"], url_path="inativar")
    def inativar(self, request, pk=None):
        obj = self.get_object()
        before = obj.ativo
        obj.ativo = False
        obj.save(update_fields=["ativo"])
        _audit("requisicaofinalidadeaquisicao", obj.pk, {"ativo": [before, False]}, request, action="inativar")
        return Response(self.get_serializer(obj).data)


class RequisicaoViewSet(BaseViewSet):
    queryset = Requisicao.objects.select_related("empresa", "loja", "setor", "requisitante", "criado_por").prefetch_related("itens", "historico").all()
    serializer_class = RequisicaoSerializer
    permission_classes = [HasRequisicaoProcessAccess]

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        qs = _scope_requisicao_queryset(qs, self.request.user)
        status_q = self.request.query_params.get("status")
        loja = self.request.query_params.get("loja")
        prioridade = self.request.query_params.get("prioridade")
        search = self.request.query_params.get("search")
        if status_q:
            qs = qs.filter(status=status_q)
        if loja:
            qs = qs.filter(loja_id=loja)
        if prioridade:
            qs = qs.filter(prioridade=prioridade)
        if search:
            f = Q(setor__nome__icontains=search) | Q(justificativa__icontains=search) | Q(requisitante__username__icontains=search)
            if str(search).isdigit():
                f |= Q(numero=int(search))
            qs = qs.filter(f)
        visao = self.request.query_params.get("visao") or self.request.query_params.get("view")
        if visao == "minhas":
            qs = qs.filter(requisitante=self.request.user)
        elif visao == "para_analisar":
            if not _can_approve_requisicao(self.request.user):
                return qs.none()
            qs = qs.filter(status__in=["AGUARDANDO_APROVACAO", "SOLICITADA", "EM_ANALISE"])
        elif visao == "para_atender":
            if not _can_manage_requisicao(self.request.user):
                return qs.none()
            qs = qs.filter(status__in=["APROVADA", "EM_ATENDIMENTO", "ATENDIDA_PARCIALMENTE"])
        elif visao == "todas":
            if not _can_view_all_requisicao(self.request.user):
                return qs.none()
        elif not self.request.user.is_superuser:
            allowed = Q()
            if _can_request_requisicao(self.request.user):
                allowed |= Q(requisitante=self.request.user)
            if _can_approve_requisicao(self.request.user):
                allowed |= Q(status__in=["AGUARDANDO_APROVACAO", "SOLICITADA", "EM_ANALISE"])
            if _can_manage_requisicao(self.request.user):
                allowed |= Q(status__in=["APROVADA", "EM_ATENDIMENTO", "ATENDIDA_PARCIALMENTE"])
            if not allowed:
                return qs.none()
            qs = qs.filter(allowed)
        return qs

    @action(detail=False, methods=["get"], url_path="lojas-permitidas")
    def lojas_permitidas(self, request):
        empresa_id = self._empresa_id_usuario()
        qs = Loja.objects.all().order_by("nome_loja")
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not request.user.is_superuser:
            return Response([])
        access = EffectiveAccessService(request.user)
        acesso_total_empresa = bool(request.user.is_superuser or access.is_company_master() or getattr(request.user, "type", "") == "Admin")
        allowed = access.allowed_store_ids()
        if allowed is not None and not acesso_total_empresa:
            qs = qs.filter(id__in=allowed)
        return Response([
            {
                "id": loja.id,
                "Idloja": loja.id,
                "empresa": loja.empresa_id,
                "nome_loja": loja.nome_loja,
                "apelido_loja": loja.apelido_loja,
            }
            for loja in qs
        ])

    @transaction.atomic
    def perform_create(self, serializer):
        loja = serializer.validated_data.get("loja")
        if not _can_request_requisicao(self.request.user):
            raise ValidationError({"detail": "Usuário sem permissão para criar requisições."})
        empresa_id = self._empresa_id_usuario()
        if not empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        if empresa_id and loja.empresa_id != int(empresa_id):
            raise ValidationError({"loja": "A loja informada pertence a outra empresa."})
        if not EffectiveAccessService(self.request.user).can_access_store(loja):
            raise ValidationError({"loja": "Usuário sem acesso à loja informada."})
        empresa = loja.empresa
        _ensure_default_requisicao_servico_categorias(empresa.pk)
        _ensure_default_requisicao_setores(empresa.pk)
        _ensure_default_requisicao_material_categorias(empresa.pk)
        _ensure_default_requisicao_finalidades(empresa.pk)
        proximo = (Requisicao.objects.select_for_update().filter(empresa=empresa).aggregate(max_num=Max("numero"))["max_num"] or 0) + 1
        obj = serializer.save(empresa=empresa, numero=proximo, requisitante=self.request.user, criado_por=self.request.user)
        _historico(obj, self.request, "CRIACAO", "", obj.status, observacao="Requisição criada.")

    @transaction.atomic
    def perform_update(self, serializer):
        obj = serializer.instance
        if not _can_edit_requisicao_content(self.request.user, obj):
            raise ValidationError({"status": "Somente o requisitante original pode editar requisições não enviadas ou devolvidas para correção."})
        loja = serializer.validated_data.get("loja", obj.loja)
        if loja.empresa_id != obj.empresa_id:
            raise ValidationError({"loja": "A loja informada pertence a outra empresa."})
        if not EffectiveAccessService(self.request.user).can_access_store(loja):
            raise ValidationError({"loja": "Usuário sem acesso à loja informada."})
        before = {"setor": obj.setor_id, "loja": obj.loja_id, "prioridade": obj.prioridade}
        updated = serializer.save(empresa=obj.empresa)
        after = {"setor": updated.setor_id, "loja": updated.loja_id, "prioridade": updated.prioridade}
        _historico(updated, self.request, "EDICAO", updated.status, updated.status, valor_anterior=before, valor_novo=after, observacao="Requisição editada.")

    def destroy(self, request, *args, **kwargs):
        return Response({"detail": "Exclusão física de requisição não é permitida. Utilize cancelamento."}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    @action(detail=True, methods=["post"], url_path="enviar")
    @transaction.atomic
    def enviar(self, request, pk=None):
        obj = self.get_object()
        if not _can_edit_requisicao_content(request.user, obj):
            return Response({"detail": "Somente o requisitante original pode enviar requisições não enviadas ou devolvidas para correção."}, status=status.HTTP_403_FORBIDDEN)
        if not obj.itens.exists():
            return Response({"detail": "Inclua ao menos um item antes de enviar."}, status=status.HTTP_400_BAD_REQUEST)
        before = obj.status
        obj.status = "AGUARDANDO_APROVACAO"
        obj.save(update_fields=["status", "atualizado_em"])
        _historico(obj, request, "ENVIO", before, obj.status, observacao=request.data.get("observacao", ""))
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"], url_path="salvar-enviar")
    @transaction.atomic
    def salvar_enviar(self, request, pk=None):
        obj = Requisicao.objects.select_for_update().get(pk=self.get_object().pk)
        if not _can_edit_requisicao_content(request.user, obj):
            return Response({"detail": "Somente o requisitante original pode enviar requisições não enviadas ou devolvidas para correção."}, status=status.HTTP_403_FORBIDDEN)
        dados = request.data.get("requisicao")
        if isinstance(dados, dict):
            serializer = self.get_serializer(obj, data=dados, partial=True)
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
            obj = serializer.instance
        if not obj.itens.exists():
            return Response({"itens": ["Inclua ao menos um item antes de enviar."]}, status=status.HTTP_400_BAD_REQUEST)
        before = obj.status
        obj.status = "AGUARDANDO_APROVACAO"
        obj.save(update_fields=["status", "atualizado_em"])
        _historico(obj, request, "ENVIO", before, obj.status, observacao=request.data.get("observacao", ""))
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"], url_path="aprovar")
    @transaction.atomic
    def aprovar(self, request, pk=None):
        if not _can_approve_requisicao(request.user):
            return Response({"detail": "Usuário sem permissão para aprovar requisição."}, status=status.HTTP_403_FORBIDDEN)
        obj = self.get_object()
        if obj.status not in {"AGUARDANDO_APROVACAO", "SOLICITADA", "EM_ANALISE"}:
            return Response({"detail": "Requisição não está aguardando aprovação."}, status=status.HTTP_400_BAD_REQUEST)
        before = obj.status
        obj.status = "APROVADA"
        obj.aprovado_por = request.user
        obj.aprovado_em = timezone.now()
        obj.save(update_fields=["status", "aprovado_por", "aprovado_em", "atualizado_em"])
        obj.itens.filter(status="PENDENTE").update(status="APROVADO")
        _historico(obj, request, "APROVACAO", before, obj.status, observacao=request.data.get("observacao", ""))
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"], url_path="rejeitar")
    @transaction.atomic
    def rejeitar(self, request, pk=None):
        if not _can_approve_requisicao(request.user):
            return Response({"detail": "Usuário sem permissão para rejeitar requisição."}, status=status.HTTP_403_FORBIDDEN)
        obj = self.get_object()
        if obj.status not in {"AGUARDANDO_APROVACAO", "SOLICITADA", "EM_ANALISE"}:
            return Response({"detail": "Requisição não pode ser rejeitada neste status."}, status=status.HTTP_400_BAD_REQUEST)
        before = obj.status
        obj.status = "REJEITADA"
        obj.save(update_fields=["status", "atualizado_em"])
        obj.itens.exclude(status__in=["ATENDIDO", "CANCELADO"]).update(status="REJEITADO")
        _historico(obj, request, "REJEICAO", before, obj.status, observacao=request.data.get("motivo", ""))
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"], url_path="devolver")
    @transaction.atomic
    def devolver(self, request, pk=None):
        if not _can_approve_requisicao(request.user):
            return Response({"detail": "Usuário sem permissão para devolver requisição."}, status=status.HTTP_403_FORBIDDEN)
        obj = self.get_object()
        if obj.status not in {"AGUARDANDO_APROVACAO", "SOLICITADA", "EM_ANALISE"}:
            return Response({"detail": "Requisição não pode ser devolvida neste status."}, status=status.HTTP_400_BAD_REQUEST)
        before = obj.status
        obj.status = "DEVOLVIDA_CORRECAO"
        obj.save(update_fields=["status", "atualizado_em"])
        _historico(obj, request, "DEVOLUCAO", before, obj.status, observacao=request.data.get("motivo", ""))
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"], url_path="cancelar")
    @transaction.atomic
    def cancelar(self, request, pk=None):
        obj = self.get_object()
        if not _can_edit_requisicao_content(request.user, obj):
            return Response({"detail": "Somente o requisitante original pode cancelar requisições editáveis."}, status=status.HTTP_403_FORBIDDEN)
        if obj.status in {"CONCLUIDA", "CANCELADA"}:
            return Response({"detail": "Requisição não pode ser cancelada neste status."}, status=status.HTTP_400_BAD_REQUEST)
        before = obj.status
        obj.status = "CANCELADA"
        obj.save(update_fields=["status", "atualizado_em"])
        obj.itens.exclude(status__in=["ATENDIDO", "SERVICO_CONCLUIDO"]).update(status="CANCELADO")
        _historico(obj, request, "CANCELAMENTO", before, obj.status, observacao=request.data.get("motivo", ""))
        return Response(self.get_serializer(obj).data)


class RequisicaoItemViewSet(BaseViewSet):
    queryset = RequisicaoItem.objects.select_related("requisicao", "produto", "unidade", "categoria_servico", "categoria_material", "finalidade_aquisicao").all()
    serializer_class = RequisicaoItemSerializer
    permission_classes = [HasRequisicaoProcessAccess]

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(requisicao__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        if not _is_requisicao_admin(self.request.user):
            allowed = EffectiveAccessService(self.request.user).allowed_store_ids()
            if allowed is not None:
                qs = qs.filter(requisicao__loja_id__in=allowed)
            visible = Q()
            if _can_request_requisicao(self.request.user):
                visible |= Q(requisicao__requisitante=self.request.user)
            if _can_approve_requisicao(self.request.user):
                visible |= Q(requisicao__status__in=["AGUARDANDO_APROVACAO", "SOLICITADA", "EM_ANALISE"])
            if _can_manage_requisicao(self.request.user):
                visible |= Q(requisicao__status__in=["APROVADA", "EM_ATENDIMENTO", "ATENDIDA_PARCIALMENTE"])
            if not visible:
                return qs.none()
            qs = qs.filter(visible)
        requisicao = self.request.query_params.get("requisicao")
        if requisicao:
            qs = qs.filter(requisicao_id=requisicao)
        return qs

    def _validar_empresa(self, data):
        req = data.get("requisicao")
        user_empresa_id = self._empresa_id_usuario()
        if not user_empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        if user_empresa_id and req and req.empresa_id != int(user_empresa_id):
            raise ValidationError({"requisicao": "Requisição pertence a outra empresa."})
        if req and not EffectiveAccessService(self.request.user).can_access_store(req.loja):
            raise ValidationError({"requisicao": "Usuário sem acesso à loja da requisição."})

    def perform_create(self, serializer):
        self._validar_empresa(serializer.validated_data)
        req = serializer.validated_data.get("requisicao")
        if req and not _can_edit_requisicao_content(self.request.user, req):
            raise ValidationError({"requisicao": "Somente o requisitante original pode alterar itens de requisições não enviadas ou devolvidas para correção."})
        item = serializer.save()
        _historico(item.requisicao, self.request, "EDICAO", item.requisicao.status, item.requisicao.status, item=item, valor_novo={"item": item.pk}, observacao="Item incluído.")

    def perform_update(self, serializer):
        data = {**serializer.validated_data}
        data.setdefault("requisicao", serializer.instance.requisicao)
        self._validar_empresa(data)
        req = data.get("requisicao")
        if req and not _can_edit_requisicao_content(self.request.user, req):
            raise ValidationError({"requisicao": "Somente o requisitante original pode alterar itens de requisições não enviadas ou devolvidas para correção."})
        item = serializer.save()
        _historico(item.requisicao, self.request, "EDICAO", item.requisicao.status, item.requisicao.status, item=item, observacao="Item editado.")

    def perform_destroy(self, instance):
        if not _can_edit_requisicao_content(self.request.user, instance.requisicao):
            raise ValidationError({"requisicao": "Somente o requisitante original pode excluir itens de requisições não enviadas ou devolvidas para correção."})
        req = instance.requisicao
        item_id = instance.pk
        instance.delete()
        _historico(req, self.request, "EDICAO", req.status, req.status, valor_anterior={"item": item_id}, observacao="Item removido.")

    @action(detail=True, methods=["post"], url_path="aguardar-cotacao")
    @transaction.atomic
    def aguardar_cotacao(self, request, pk=None):
        if not _can_manage_requisicao(request.user):
            return Response({"detail": "Usuário sem permissão para encaminhar requisição."}, status=status.HTTP_403_FORBIDDEN)
        item = get_object_or_404(self.get_queryset().select_for_update(), pk=pk)
        self.check_object_permissions(request, item)
        req = get_object_or_404(Requisicao.objects.select_for_update(), pk=item.requisicao_id)
        if req.status not in {"APROVADA", "EM_ATENDIMENTO", "ATENDIDA_PARCIALMENTE", "EM_PROCESSO_COMPRA"}:
            return Response({"detail": "A requisição precisa estar aprovada para encaminhar item."}, status=status.HTTP_400_BAD_REQUEST)
        if item.status in {"ATENDIDO", "CANCELADO", "REJEITADO", "SERVICO_CONCLUIDO"}:
            return Response({"detail": "Item não pode ser encaminhado neste status."}, status=status.HTTP_400_BAD_REQUEST)
        before_item = item.status
        item.status = "AGUARDANDO_COTACAO"
        item.save(update_fields=["status", "atualizado_em"])
        before_req = req.status
        req.status = _recalcular_status_requisicao(req)
        req.save(update_fields=["status", "atualizado_em"])
        _historico(req, request, "STATUS", before_req, req.status, item=item, valor_anterior={"item_status": before_item}, valor_novo={"item_status": item.status}, observacao="Item marcado aguardando cotação.")
        return Response(self.get_serializer(item).data)

    @action(detail=True, methods=["post"], url_path="atender")
    @transaction.atomic
    def atender(self, request, pk=None):
        if not _can_manage_requisicao(request.user):
            return Response({"detail": "Usuário sem permissão para atender requisição."}, status=status.HTTP_403_FORBIDDEN)
        item = get_object_or_404(self.get_queryset().select_for_update(), pk=pk)
        self.check_object_permissions(request, item)
        req = get_object_or_404(Requisicao.objects.select_for_update(), pk=item.requisicao_id)
        if req.status not in {"APROVADA", "EM_ATENDIMENTO", "ATENDIDA_PARCIALMENTE"}:
            return Response({"detail": "A requisição precisa estar aprovada para atendimento."}, status=status.HTTP_400_BAD_REQUEST)
        if item.status in {"ATENDIDO", "CANCELADO", "REJEITADO", "AGUARDANDO_COTACAO", "EM_COTACAO", "PEDIDO_GERADO", "SERVICO_CONCLUIDO"}:
            return Response({"detail": "Item não pode ser atendido neste status."}, status=status.HTTP_400_BAD_REQUEST)
        if item.tipo != "MATERIAL" or item.origem != "PRODUTO" or not item.produto_id:
            return Response({"detail": "Somente material cadastrado pode ser atendido pelo estoque nesta etapa."}, status=status.HTTP_400_BAD_REQUEST)
        qtd = Decimal(request.data.get("quantidade") or 0)
        if qtd <= 0:
            return Response({"quantidade": "Informe uma quantidade maior que zero."}, status=status.HTTP_400_BAD_REQUEST)
        saldo = Decimal(item.qtd_pendente or 0)
        if qtd > saldo:
            return Response({"quantidade": "Quantidade não pode ultrapassar o saldo pendente."}, status=status.HTTP_400_BAD_REQUEST)
        estoque = ProdutoUsoConsumoEstoque.objects.select_for_update().filter(empresa=req.empresa, produto=item.produto, loja=req.loja).first()
        disponivel = Decimal(getattr(estoque, "saldo", 0) or 0)
        if qtd > disponivel:
            return Response({"quantidade": "Estoque insuficiente para a quantidade informada.", "disponivel": str(disponivel)}, status=status.HTTP_400_BAD_REQUEST)
        before_item = item.status
        before_req = req.status
        anterior = disponivel
        posterior = disponivel - qtd
        estoque.saldo = posterior
        estoque.save(update_fields=["saldo", "atualizado_em"])
        ProdutoUsoConsumoMovimentacao.objects.create(
            empresa=req.empresa,
            produto=item.produto,
            loja=req.loja,
            tipo=ProdutoUsoConsumoMovimentacao.TIPO_CONSUMO_INTERNO,
            quantidade=qtd,
            saldo_anterior=anterior,
            saldo_posterior=posterior,
            usuario=request.user,
            motivo="Atendimento de requisição",
            destino=req.setor,
            documento=f"REQ {req.numero}",
            origem="REQUISICAO",
        )
        item.qtd_atendida = Decimal(item.qtd_atendida or 0) + qtd
        item.qtd_pendente = max(Decimal(item.qtd_solicitada or 0) - item.qtd_atendida, Decimal("0"))
        item.status = "ATENDIDO" if item.qtd_pendente == 0 else "ATENDIDO_PARCIALMENTE"
        item.save(update_fields=["qtd_atendida", "qtd_pendente", "status", "atualizado_em"])
        req.status = _recalcular_status_requisicao(req)
        req.save(update_fields=["status", "atualizado_em"])
        _historico(req, request, "ATENDIMENTO", before_req, req.status, item=item, valor_anterior={"item_status": before_item, "saldo_estoque": str(anterior)}, valor_novo={"item_status": item.status, "qtd_atendida": str(item.qtd_atendida), "saldo_estoque": str(posterior)}, observacao=request.data.get("observacao", ""))
        return Response(self.get_serializer(item).data)


class RequisicaoHistoricoViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = RequisicaoHistorico.objects.select_related("requisicao", "item", "usuario").all()
    serializer_class = RequisicaoHistoricoSerializer
    permission_classes = [HasRequisicaoProcessAccess]
    read_roles = ['Admin', 'Diretor', 'Gerente', 'AssistentePagar']

    def _empresa_id_usuario(self):
        usuario = self.request.user
        if getattr(usuario, "empresa_id", None):
            return usuario.empresa_id
        return None

    def get_queryset(self):
        qs = self.queryset
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(requisicao__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        if not _is_requisicao_admin(self.request.user):
            allowed = EffectiveAccessService(self.request.user).allowed_store_ids()
            if allowed is not None:
                qs = qs.filter(requisicao__loja_id__in=allowed)
            visible = Q()
            if _can_request_requisicao(self.request.user):
                visible |= Q(requisicao__requisitante=self.request.user)
            if _can_approve_requisicao(self.request.user):
                visible |= Q(requisicao__status__in=["AGUARDANDO_APROVACAO", "SOLICITADA", "EM_ANALISE"])
            if _can_manage_requisicao(self.request.user):
                visible |= Q(requisicao__status__in=["APROVADA", "EM_ATENDIMENTO", "ATENDIDA_PARCIALMENTE"])
            if not visible:
                return qs.none()
            qs = qs.filter(visible)
        requisicao = self.request.query_params.get("requisicao")
        if requisicao:
            qs = qs.filter(requisicao_id=requisicao)
        return qs
