from decimal import Decimal

from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from accounts.permissions import HasModuleRole

try:
    from auditoria.models import AuditLog
except Exception:
    AuditLog = None

FIN_OK = True
try:
    from financeiro.models import Pagar, PagarItem
except Exception:
    FIN_OK = False
    Pagar = PagarItem = None

from fiscal.models import NotaFiscalEntrada, NotaFiscalEntradaItem
from fiscal.serializers import NotaFiscalEntradaItemSerializer, NotaFiscalEntradaSerializer


def _audit(model_name: str, obj_id: str, changes: dict, request, action: str = "custom"):
    if not AuditLog:
        return
    try:
        AuditLog.objects.create(
            action=(action or "custom")[:20],
            app_label="fiscal",
            model=model_name,
            object_id=str(obj_id),
            changes=changes,
            user=getattr(request, "user", None),
            ip=(request.META.get("REMOTE_ADDR") or "")[:45],
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:400],
        )
    except Exception:
        pass


def _documento_nota(nota: NotaFiscalEntrada) -> str:
    partes = [nota.modelo]
    if nota.serie:
        partes.append(nota.serie)
    partes.append(nota.numero)
    return "/".join(partes)[:30]


class BaseViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModuleRole]
    read_roles = ["Admin", "Diretor", "Gerente", "AssistentePagar"]
    write_roles = ["Admin", "Diretor", "Gerente", "AssistentePagar"]


class NotaFiscalEntradaViewSet(BaseViewSet):
    queryset = (
        NotaFiscalEntrada.objects.select_related("pedido_compra", "criado_por")
        .prefetch_related("itens")
        .all()
        .order_by("-dt_entrada", "-id")
    )
    serializer_class = NotaFiscalEntradaSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        pedido = self.request.query_params.get("pedido") or self.request.query_params.get("pedido_compra")
        status_q = self.request.query_params.get("status")
        numero = self.request.query_params.get("numero")
        chave = self.request.query_params.get("chave_acesso")

        if pedido:
            qs = qs.filter(pedido_compra_id=pedido)
        if status_q:
            qs = qs.filter(status=status_q)
        if numero:
            qs = qs.filter(numero__icontains=numero)
        if chave:
            qs = qs.filter(chave_acesso__icontains=chave)
        return qs

    @action(detail=True, methods=["post"], url_path="fechar")
    @transaction.atomic
    def fechar(self, request, pk=None):
        nota = self.get_object()
        if nota.status != NotaFiscalEntrada.Status.ABERTA:
            return Response({"detail": "Somente notas abertas podem ser fechadas."}, status=status.HTTP_400_BAD_REQUEST)
        if not nota.itens.exists():
            return Response({"detail": "Inclua ao menos um item antes de fechar a nota."}, status=status.HTTP_400_BAD_REQUEST)

        nota.recalcular_totais()
        before = nota.status
        nota.status = NotaFiscalEntrada.Status.FECHADA
        nota.save(update_fields=["status", "atualizado_em"])

        financeiro = self._vincular_financeiro(nota)
        _audit("notafiscalentrada", nota.pk, {"status": [before, nota.status]}, request, action="fechar")
        data = self.get_serializer(nota).data
        data["financeiro"] = financeiro
        return Response(data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="cancelar")
    @transaction.atomic
    def cancelar(self, request, pk=None):
        nota = self.get_object()
        if nota.status == NotaFiscalEntrada.Status.CANCELADA:
            return Response(self.get_serializer(nota).data, status=status.HTTP_200_OK)

        before = nota.status
        nota.status = NotaFiscalEntrada.Status.CANCELADA
        nota.save(update_fields=["status", "atualizado_em"])
        _audit("notafiscalentrada", nota.pk, {"status": [before, nota.status]}, request, action="cancelar")
        return Response(self.get_serializer(nota).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="itens-pedido")
    def itens_pedido(self, request, pk=None):
        nota = self.get_object()
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

            payload.append(
                {
                    "pedido_item": pedido_item.pk,
                    "nota_item": getattr(item_nota, "pk", None),
                    "produto": getattr(pedido_item, "produto_id", None),
                    "cor": getattr(pedido_item, "cor_id", None),
                    "pack": getattr(pedido_item, "pack_id", None),
                    "descricao_livre": pedido_item.descricao_livre,
                    "qtd_pedido": str(qtd_pedido),
                    "qtd_recebida_outras_notas": str(qtd_outras_notas),
                    "qtd_na_nota": str(qtd_na_nota),
                    "saldo_total_recebivel": str(saldo_total),
                    "saldo_pendente": str(saldo_pendente),
                    "preco_unit_pedido": str(pedido_item.preco_unit),
                }
            )

        return Response(payload, status=status.HTTP_200_OK)

    def _vincular_financeiro(self, nota):
        if not FIN_OK:
            return {"disponivel": False, "titulos_atualizados": 0, "parcelas_efetivadas": 0}

        titulos = Pagar.objects.filter(pedido_compra=nota.pedido_compra_id).filter(nfe_id__isnull=True)
        documento = _documento_nota(nota)
        titulos_atualizados = 0
        parcelas_efetivadas = 0

        for titulo in titulos:
            titulo.nfe_id = nota.pk
            titulo.Documento = documento
            titulo.Data_emissao = nota.dt_emissao
            titulo.Previsao = False
            titulo.save(update_fields=["nfe_id", "Documento", "Data_emissao", "Previsao"])
            titulos_atualizados += 1

            parcelas = PagarItem.objects.filter(Idpagar=titulo, status=PagarItem.STATUS_PREVISTO)
            parcelas_efetivadas += parcelas.update(status=PagarItem.STATUS_EFETIVO, Previsao=False)

        return {
            "disponivel": True,
            "titulos_atualizados": titulos_atualizados,
            "parcelas_efetivadas": parcelas_efetivadas,
        }


class NotaFiscalEntradaItemViewSet(BaseViewSet):
    queryset = NotaFiscalEntradaItem.objects.select_related("nota", "pedido_item").all().order_by("nota_id", "id")
    serializer_class = NotaFiscalEntradaItemSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        nota = self.request.query_params.get("nota")
        pedido = self.request.query_params.get("pedido") or self.request.query_params.get("pedido_compra")
        pedido_item = self.request.query_params.get("pedido_item")

        if nota:
            qs = qs.filter(nota_id=nota)
        if pedido:
            qs = qs.filter(nota__pedido_compra_id=pedido)
        if pedido_item:
            qs = qs.filter(pedido_item_id=pedido_item)
        return qs

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        nota = instance.nota
        if nota.status != NotaFiscalEntrada.Status.ABERTA:
            return Response({"detail": "Somente notas abertas podem receber alterações."}, status=status.HTTP_400_BAD_REQUEST)

        response = super().destroy(request, *args, **kwargs)
        nota.recalcular_totais()
        return response
