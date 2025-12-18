from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone
from django.db import transaction

try:
    from auditoria.models import AuditLog
except Exception:
    AuditLog = None

from .models import (
    Pagar, PagarItem, PagarRateio,
    FormaPagamento, FormaPagamentoParcela
)
from .serializers import (
    PagarSerializer, PagarItemSerializer, PagarRateioSerializer,
    FormaPagamentoSerializer, FormaPagamentoParcelaSerializer
)


def _audit(model_name: str, obj_id: str, changes: dict, request, action: str = "custom"):
    if not AuditLog:
        return
    try:
        safe_action = (action or "custom")[:32]
        ip = (request.META.get("REMOTE_ADDR") or "")[:45]
        ua = (request.META.get("HTTP_USER_AGENT") or "")[:512]

        payload = dict(
            action=safe_action,
            app_label="financeiro",
            model=(model_name or "")[:100],
            object_id=(str(obj_id) if obj_id is not None else "")[:64],
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
        pass


class BaseViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]


class FormaPagamentoViewSet(BaseViewSet):
    queryset = FormaPagamento.objects.all().order_by('codigo')
    serializer_class = FormaPagamentoSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        ativo = self.request.query_params.get('ativo')
        codigo = self.request.query_params.get('codigo')
        if ativo in ('true', 'false', '1', '0'):
            v = ativo in ('true', '1')
            qs = qs.filter(ativo=v)
        if codigo:
            qs = qs.filter(codigo=codigo)
        return qs


class FormaPagamentoParcelaViewSet(BaseViewSet):
    queryset = FormaPagamentoParcela.objects.select_related('forma').all().order_by('forma__codigo', 'ordem')
    serializer_class = FormaPagamentoParcelaSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        forma = self.request.query_params.get('forma')
        codigo = self.request.query_params.get('codigo')
        if forma:
            qs = qs.filter(forma_id=forma)
        if codigo:
            qs = qs.filter(forma__codigo=codigo)
        return qs


class PagarViewSet(BaseViewSet):
    queryset = Pagar.objects.all().order_by('-Data_emissao', '-Idpagar')
    serializer_class = PagarSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        loja = self.request.query_params.get('loja')
        fornecedor = self.request.query_params.get('fornecedor')
        previsao = self.request.query_params.get('previsao')
        if loja:
            qs = qs.filter(idloja_id=loja)
        if fornecedor:
            qs = qs.filter(idfornecedor_id=fornecedor)
        if previsao in ('true', 'false', '1', '0'):
            v = previsao in ('true', '1')
            qs = qs.filter(Previsao=v)
        return qs


class PagarItemViewSet(BaseViewSet):
    queryset = PagarItem.objects.all().order_by('Idpagar_id', 'parcela_n')
    serializer_class = PagarItemSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        pagar = self.request.query_params.get('pagar')
        status_q = self.request.query_params.get('status')
        if pagar:
            qs = qs.filter(Idpagar_id=pagar)
        if status_q:
            qs = qs.filter(status=status_q)
        return qs

    @action(detail=True, methods=['post'], url_path='efetivar')
    def efetivar(self, request, pk=None):
        obj = self.get_object()
        before = obj.status
        if obj.status == PagarItem.STATUS_PREVISTO:
            obj.status = PagarItem.STATUS_EFETIVO
            obj.Previsao = False
            obj.save(update_fields=['status', 'Previsao'])
            _audit('pagaritem', obj.pk, {'status': [before, obj.status]}, request, action='efetivar')
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=['post'], url_path='baixar')
    def baixar(self, request, pk=None):
        obj = self.get_object()
        valor_baixa = request.data.get('valor_baixa')
        data_baixa = request.data.get('data_baixa') or timezone.now().date().isoformat()
        try:
            valor_baixa = float(valor_baixa)
        except (TypeError, ValueError):
            return Response({'detail': 'Informe valor_baixa numérico.'}, status=status.HTTP_400_BAD_REQUEST)
        before = {'status': obj.status, 'valor_baixa': obj.valor_baixa, 'data_baixa': obj.data_baixa}
        obj.valor_baixa = valor_baixa
        obj.data_baixa = data_baixa
        obj.status = PagarItem.STATUS_BAIXADO
        obj.save(update_fields=['valor_baixa', 'data_baixa', 'status'])
        _audit('pagaritem', obj.pk, {'before': before, 'after': {
            'status': obj.status, 'valor_baixa': obj.valor_baixa, 'data_baixa': str(obj.data_baixa)
        }}, request, action='baixar')
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=['post'], url_path='cancelar')
    def cancelar(self, request, pk=None):
        obj = self.get_object()
        motivo = (request.data.get('motivo') or '').strip()
        before = obj.status
        if obj.status != PagarItem.STATUS_BAIXADO:
            obj.status = PagarItem.STATUS_CANCELADO
            obj.save(update_fields=['status'])
            _audit('pagaritem', obj.pk, {'status': [before, obj.status], 'motivo': motivo}, request, action='cancelar')
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=['post'], url_path='reabrir')
    def reabrir(self, request, pk=None):
        obj = self.get_object()
        motivo = (request.data.get('motivo') or '').strip()
        before = obj.status
        if obj.status in (PagarItem.STATUS_CANCELADO, PagarItem.STATUS_EFETIVO) and obj.data_baixa is None:
            obj.status = PagarItem.STATUS_PREVISTO
            obj.Previsao = True
            obj.save(update_fields=['status', 'Previsao'])
            _audit('pagaritem', obj.pk, {'status': [before, obj.status], 'motivo': motivo}, request, action='reabrir')
        return Response(self.get_serializer(obj).data)


class PagarRateioViewSet(BaseViewSet):
    queryset = PagarRateio.objects.all().order_by('Idpagaritem_id', 'Idrateio')
    serializer_class = PagarRateioSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        parcela = self.request.query_params.get('pagar_item')
        if parcela:
            qs = qs.filter(Idpagaritem_id=parcela)
        return qs
