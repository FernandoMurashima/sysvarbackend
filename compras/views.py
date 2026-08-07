from rest_framework import viewsets, status
from rest_framework.exceptions import ValidationError
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from django.db.models import Q
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from accounts.permissions import HasModuleRole

from auditoria.models import AuditAction, AuditCategory
from auditoria.services import AuditService

from .models import (
    PedidoCompra,
    PedidoCompraItem,
    PedidoCompraEntrega,
    PedidoCompraParcela,
)
from .serializers import (
    PedidoCompraSerializer,
    PedidoCompraItemSerializer,
    PedidoCompraEntregaSerializer,
    PedidoCompraParcelaSerializer,
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
        if tipo in ("1", "2"):
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
        serializer.save(empresa=loja.empresa)

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

        # atualiza totais
        obj.recomputa_totais()
        obj.save(update_fields=["total_itens", "total_desconto", "frete", "total_pedido"])

        total = Decimal(obj.total_pedido or 0)
        emissao = obj.emissao

        # remove anteriores PLAN
        PedidoCompraParcela.objects.filter(pedido=obj, status="PLAN").delete()

        vals = []
        restante = total
        n = len(cfg)
        for i, par in enumerate(cfg, start=1):
            if par.percentual is not None:
                # par.percentual pode vir como 33 (33%) ou 0.33
                perc = Decimal(str(par.percentual))
                if perc > 1:
                    perc = (perc / Decimal("100"))
                val = (total * perc).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            else:
                if i < n:
                    val = (total / n).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                else:
                    val = restante.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            restante -= val

            vencto = (emissao + timedelta(days=int(par.dias)))
            p = PedidoCompraParcela.objects.create(
                pedido=obj,
                parcela_n=i,
                vencimento=vencto,
                valor=val,
                percentual=par.percentual,
                origem="FORMA",
                status="PLAN",
            )
            vals.append({"parcela_n": i, "vencimento": vencto.isoformat(), "valor": float(val)})

        before = obj.forma_pagamento
        before_prazo = obj.prazo_pagamento_id
        obj.forma_pagamento = forma.codigo
        obj.prazo_pagamento = prazo
        obj.save(update_fields=["forma_pagamento", "prazo_pagamento"])

        _audit(
            "pedidocompra",
            obj.pk,
            {"set_forma": {"before": before, "after": forma.codigo}, "set_prazo": {"before": before_prazo, "after": getattr(prazo, "pk", None)}, "parcelas": vals},
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
        serializer.save()

    def perform_update(self, serializer):
        data = {**serializer.validated_data}
        data.setdefault("pedido", serializer.instance.pedido)
        data.setdefault("produto", serializer.validated_data.get("produto", serializer.instance.produto))
        data.setdefault("pack", serializer.validated_data.get("pack", serializer.instance.pack))
        self._validar_item_empresa(data)
        serializer.save()

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
