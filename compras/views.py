from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from accounts.permissions import HasModuleRole

try:
    from auditoria.models import AuditLog
except Exception:
    AuditLog = None

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
    )
except Exception:
    FIN_OK = False
    Pagar = PagarItem = FormaPagamento = FormaPagamentoParcela = None

# Natureza para aprovar
from cadastros.models import Nat_Lancamento


# ----------------- Auditoria robusta -----------------
def _audit(model_name: str, obj_id: str, changes: dict, request, action: str = "custom"):
    if not AuditLog:
        return
    try:
        safe_action = (action or "custom")[:20]           # campo curto e seguro
        ip = (request.META.get("REMOTE_ADDR") or "")[:45] # ipv4/ipv6
        ua = (request.META.get("HTTP_USER_AGENT") or "")[:400]

        payload = dict(
            action=safe_action,
            app_label="compras",
            model=model_name,
            object_id=str(obj_id),
            changes=changes,
            user=getattr(request, "user", None),
            ip=ip,
            user_agent=ua,
        )

        conn = transaction.get_connection()
        if conn.in_atomic_block:
            transaction.on_commit(lambda: AuditLog.objects.create(**payload))
        else:
            AuditLog.objects.create(**payload)
    except Exception:
        # auditoria nunca quebra o fluxo
        pass


class BaseViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModuleRole]
    read_roles = ["Admin", "Diretor", "Gerente", "AssistentePagar"]
    write_roles = ["Admin", "Diretor", "Gerente", "AssistentePagar"]


# ----------------- Pedido -----------------
class PedidoCompraViewSet(BaseViewSet):
    queryset = PedidoCompra.objects.all().order_by("-emissao", "-id")
    serializer_class = PedidoCompraSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        tipo = self.request.query_params.get("tipo")
        status_q = self.request.query_params.get("status")
        loja = self.request.query_params.get("loja")
        fornecedor = self.request.query_params.get("fornecedor")
        if tipo in ("1", "2"):
            qs = qs.filter(tipo=tipo)
        if status_q:
            qs = qs.filter(status=status_q)
        if loja:
            qs = qs.filter(loja_id=loja)
        if fornecedor:
            qs = qs.filter(fornecedor_id=fornecedor)
        return qs

    @action(detail=True, methods=["post"], url_path="set-forma-pagamento")
    @transaction.atomic
    def set_forma_pagamento(self, request, pk=None):
        """
        Seta forma de pagamento (por id ou codigo) e RECRIA as parcelas planejadas (PLAN)
        em compras_pedido_compra_parcela com base em FormaPagamentoParcela.
        Body: {"id_forma": 2} ou {"codigo_forma":"30/60"}.
        """
        obj: PedidoCompra = self.get_object()
        if obj.status != "AB":
            return Response({"detail": "Somente AB"}, status=status.HTTP_400_BAD_REQUEST)
        if not FIN_OK:
            return Response({"detail": "Financeiro indisponível"}, status=status.HTTP_400_BAD_REQUEST)

        id_forma = request.data.get("id_forma")
        codigo = (request.data.get("codigo_forma") or "").strip()

        try:
            if id_forma:
                forma = FormaPagamento.objects.get(pk=id_forma, ativo=True)
            elif codigo:
                forma = FormaPagamento.objects.get(codigo=codigo, ativo=True)
            else:
                return Response({"detail": "Informe id_forma ou codigo_forma"}, status=status.HTTP_400_BAD_REQUEST)
        except FormaPagamento.DoesNotExist:
            return Response({"detail": "Forma não encontrada/inativa"}, status=status.HTTP_400_BAD_REQUEST)

        cfg = list(FormaPagamentoParcela.objects.filter(forma=forma).order_by("ordem"))
        if not cfg:
            return Response({"detail": "Forma sem parcelas configuradas"}, status=status.HTTP_400_BAD_REQUEST)

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
        obj.forma_pagamento = forma.codigo
        obj.save(update_fields=["forma_pagamento"])

        _audit(
            "pedidocompra",
            obj.pk,
            {"set_forma": {"before": before, "after": forma.codigo}, "parcelas": vals},
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
        pedido = self.request.query_params.get("pedido")
        if pedido:
            qs = qs.filter(pedido_id=pedido)
        return qs


# ----------------- Entregas -----------------
class PedidoCompraEntregaViewSet(BaseViewSet):
    queryset = PedidoCompraEntrega.objects.all().order_by("item_id", "id")
    serializer_class = PedidoCompraEntregaSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        pedido = self.request.query_params.get("pedido")
        if pedido:
            qs = qs.filter(item__pedido_id=pedido)
        return qs


# ----------------- Parcelas (planejamento) -----------------
class PedidoCompraParcelaViewSet(BaseViewSet):
    queryset = PedidoCompraParcela.objects.all().order_by("pedido_id", "parcela_n")
    serializer_class = PedidoCompraParcelaSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        pedido = self.request.query_params.get("pedido")
        if pedido:
            qs = qs.filter(pedido_id=pedido)
        status_q = self.request.query_params.get("status")
        if status_q:
            qs = qs.filter(status=status_q)
        return qs
