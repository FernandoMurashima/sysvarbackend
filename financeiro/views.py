from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone
from django.db import transaction
from accounts.permissions import HasModuleRole

try:
    from auditoria.models import AuditLog
except Exception:
    AuditLog = None

from .models import (
    Caixa, ContaBancaria, MovimentacaoFinanceira,
    Pagar, PagarItem, PagarRateio,
    Receber, ReceberItem, ReceberRateio,
    FormaPagamento, FormaPagamentoParcela
)
from .serializers import (
    CaixaSerializer, ContaBancariaSerializer, MovimentacaoFinanceiraSerializer,
    PagarSerializer, PagarItemSerializer, PagarRateioSerializer,
    ReceberSerializer, ReceberItemSerializer, ReceberRateioSerializer,
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
    permission_classes = [HasModuleRole]
    read_roles = ["Admin", "Diretor", "Gerente"]
    write_roles = ["Admin", "Diretor", "Gerente"]


class FormaPagamentoViewSet(BaseViewSet):
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa"]
    write_roles = ["Admin", "Diretor", "Gerente"]
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
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa"]
    write_roles = ["Admin", "Diretor", "Gerente"]
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


class CaixaViewSet(BaseViewSet):
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa"]
    write_roles = ["Admin", "Diretor", "Gerente"]
    queryset = Caixa.objects.select_related('idloja').all()
    serializer_class = CaixaSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        loja = self.request.query_params.get('loja')
        ativo = self.request.query_params.get('ativo')
        tipo_caixa = self.request.query_params.get('tipo_caixa')
        if loja:
            qs = qs.filter(idloja_id=loja)
        if ativo in ('true', 'false', '1', '0'):
            qs = qs.filter(ativo=ativo in ('true', '1'))
        if tipo_caixa:
            qs = qs.filter(tipo_caixa=tipo_caixa)
        return qs


class ContaBancariaViewSet(BaseViewSet):
    read_roles = ["Admin", "Diretor", "Gerente", "AssistenteReceber", "AssistentePagar"]
    write_roles = ["Admin", "Diretor", "Gerente"]
    queryset = ContaBancaria.objects.select_related('idloja').all()
    serializer_class = ContaBancariaSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        loja = self.request.query_params.get('loja')
        ativo = self.request.query_params.get('ativo')
        if loja:
            qs = qs.filter(idloja_id=loja)
        if ativo in ('true', 'false', '1', '0'):
            qs = qs.filter(ativo=ativo in ('true', '1'))
        return qs


class MovimentacaoFinanceiraViewSet(BaseViewSet):
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa", "AssistenteReceber", "AssistentePagar"]
    write_roles = ["Admin", "Diretor", "Gerente", "Caixa"]
    queryset = MovimentacaoFinanceira.objects.select_related(
        'idloja', 'Idnatureza', 'caixa', 'conta_bancaria'
    ).all()
    serializer_class = MovimentacaoFinanceiraSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        loja = self.request.query_params.get('loja')
        tipo = self.request.query_params.get('tipo')
        status_q = self.request.query_params.get('status')
        caixa = self.request.query_params.get('caixa')
        conta = self.request.query_params.get('conta_bancaria')
        data_ini = self.request.query_params.get('data_ini')
        data_fim = self.request.query_params.get('data_fim')
        if loja:
            qs = qs.filter(idloja_id=loja)
        if tipo:
            qs = qs.filter(tipo=tipo)
        if status_q:
            qs = qs.filter(status=status_q)
        if caixa:
            qs = qs.filter(caixa_id=caixa)
        if conta:
            qs = qs.filter(conta_bancaria_id=conta)
        if data_ini:
            qs = qs.filter(data_movimento__gte=data_ini)
        if data_fim:
            qs = qs.filter(data_movimento__lte=data_fim)
        return qs

    @action(detail=True, methods=['post'], url_path='cancelar')
    def cancelar(self, request, pk=None):
        obj = self.get_object()
        before = obj.status
        if obj.status != MovimentacaoFinanceira.STATUS_CANCELADA:
            obj.status = MovimentacaoFinanceira.STATUS_CANCELADA
            obj.save(update_fields=['status'])
            _audit('movimentacaofinanceira', obj.pk, {'status': [before, obj.status]}, request, action='cancelar')
        return Response(self.get_serializer(obj).data)


class PagarViewSet(BaseViewSet):
    read_roles = ["Admin", "Diretor", "Gerente", "AssistentePagar"]
    write_roles = ["Admin", "Diretor", "Gerente", "AssistentePagar"]
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
    read_roles = ["Admin", "Diretor", "Gerente", "AssistentePagar"]
    write_roles = ["Admin", "Diretor", "Gerente", "AssistentePagar"]
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
    read_roles = ["Admin", "Diretor", "Gerente", "AssistentePagar"]
    write_roles = ["Admin", "Diretor", "Gerente", "AssistentePagar"]
    queryset = PagarRateio.objects.all().order_by('Idpagaritem_id', 'Idrateio')
    serializer_class = PagarRateioSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        parcela = self.request.query_params.get('pagar_item')
        if parcela:
            qs = qs.filter(Idpagaritem_id=parcela)
        return qs


class ReceberViewSet(BaseViewSet):
    read_roles = ["Admin", "Diretor", "Gerente", "AssistenteReceber"]
    write_roles = ["Admin", "Diretor", "Gerente", "AssistenteReceber"]
    queryset = Receber.objects.all().order_by('-Data_emissao', '-Idreceber')
    serializer_class = ReceberSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        loja = self.request.query_params.get('loja')
        cliente = self.request.query_params.get('cliente')
        previsao = self.request.query_params.get('previsao')
        if loja:
            qs = qs.filter(idloja_id=loja)
        if cliente:
            qs = qs.filter(idcliente_id=cliente)
        if previsao in ('true', 'false', '1', '0'):
            v = previsao in ('true', '1')
            qs = qs.filter(Previsao=v)
        return qs


class ReceberItemViewSet(BaseViewSet):
    read_roles = ["Admin", "Diretor", "Gerente", "AssistenteReceber"]
    write_roles = ["Admin", "Diretor", "Gerente", "AssistenteReceber"]
    queryset = ReceberItem.objects.all().order_by('Idreceber_id', 'parcela_n')
    serializer_class = ReceberItemSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        receber = self.request.query_params.get('receber')
        status_q = self.request.query_params.get('status')
        if receber:
            qs = qs.filter(Idreceber_id=receber)
        if status_q:
            qs = qs.filter(status=status_q)
        return qs

    @action(detail=True, methods=['post'], url_path='efetivar')
    def efetivar(self, request, pk=None):
        obj = self.get_object()
        before = obj.status
        if obj.status == ReceberItem.STATUS_PREVISTO:
            obj.status = ReceberItem.STATUS_EFETIVO
            obj.Previsao = False
            obj.save(update_fields=['status', 'Previsao'])
            _audit('receberitem', obj.pk, {'status': [before, obj.status]}, request, action='efetivar')
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
        obj.status = ReceberItem.STATUS_BAIXADO
        obj.save(update_fields=['valor_baixa', 'data_baixa', 'status'])
        _audit('receberitem', obj.pk, {'before': before, 'after': {
            'status': obj.status, 'valor_baixa': obj.valor_baixa, 'data_baixa': str(obj.data_baixa)
        }}, request, action='baixar')
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=['post'], url_path='cancelar')
    def cancelar(self, request, pk=None):
        obj = self.get_object()
        motivo = (request.data.get('motivo') or '').strip()
        before = obj.status
        if obj.status != ReceberItem.STATUS_BAIXADO:
            obj.status = ReceberItem.STATUS_CANCELADO
            obj.save(update_fields=['status'])
            _audit('receberitem', obj.pk, {'status': [before, obj.status], 'motivo': motivo}, request, action='cancelar')
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=['post'], url_path='reabrir')
    def reabrir(self, request, pk=None):
        obj = self.get_object()
        motivo = (request.data.get('motivo') or '').strip()
        before = obj.status
        if obj.status in (ReceberItem.STATUS_CANCELADO, ReceberItem.STATUS_EFETIVO) and obj.data_baixa is None:
            obj.status = ReceberItem.STATUS_PREVISTO
            obj.Previsao = True
            obj.save(update_fields=['status', 'Previsao'])
            _audit('receberitem', obj.pk, {'status': [before, obj.status], 'motivo': motivo}, request, action='reabrir')
        return Response(self.get_serializer(obj).data)


class ReceberRateioViewSet(BaseViewSet):
    read_roles = ["Admin", "Diretor", "Gerente", "AssistenteReceber"]
    write_roles = ["Admin", "Diretor", "Gerente", "AssistenteReceber"]
    queryset = ReceberRateio.objects.all().order_by('Idreceberitem_id', 'Idrateio')
    serializer_class = ReceberRateioSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        parcela = self.request.query_params.get('receber_item')
        if parcela:
            qs = qs.filter(Idreceberitem_id=parcela)
        return qs
