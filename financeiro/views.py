from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from django.utils import timezone
from django.db import models, transaction
from accounts.permissions import HasModuleRole

try:
    from auditoria.models import AuditLog
except Exception:
    AuditLog = None

from .models import (
    Caixa, ContaBancaria, MovimentacaoFinanceira,
    CashbackConfig, CashbackMovimento, saldo_cashback_cliente,
    ValeTroca, ValeTrocaMovimento, saldo_vale_troca_cliente,
    Pagar, PagarItem, PagarRateio,
    Receber, ReceberItem, ReceberRateio,
    FormaPagamento, FormaPagamentoParcela
)
from .serializers import (
    CaixaSerializer, ContaBancariaSerializer, MovimentacaoFinanceiraSerializer,
    CashbackConfigSerializer, CashbackMovimentoSerializer,
    ValeTrocaSerializer, ValeTrocaMovimentoSerializer,
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

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id and self._model_has_field(qs.model, 'empresa'):
            return qs.filter(empresa_id=empresa_id)
        if not (self.request.user.is_superuser or self.request.user.is_staff) and self._model_has_field(qs.model, 'empresa'):
            return qs.none()
        return qs

    def perform_create(self, serializer):
        model = serializer.Meta.model
        user = self.request.user
        empresa_id = getattr(user, 'empresa_id', None)
        if not empresa_id and not (user.is_superuser or user.is_staff):
            raise ValidationError({'empresa': 'Usuário sem empresa vinculada.'})
        if empresa_id:
            self._validar_vinculos_empresa(serializer.validated_data, empresa_id)
        if self._model_has_field(model, 'empresa') and empresa_id:
            empresa = serializer.validated_data.get('empresa')
            if empresa and empresa.id != empresa_id:
                raise ValidationError({'empresa': 'O cadastro pertence a outra empresa.'})
            serializer.save(empresa=user.empresa)
            return
        serializer.save()

    def perform_update(self, serializer):
        empresa_id = getattr(self.request.user, 'empresa_id', None)
        if not empresa_id and not (self.request.user.is_superuser or self.request.user.is_staff):
            raise ValidationError({'empresa': 'Usuário sem empresa vinculada.'})
        if empresa_id:
            self._validar_vinculos_empresa(serializer.validated_data, empresa_id)
        serializer.save()

    def _empresa_id_usuario(self):
        user = self.request.user
        if user.is_superuser or user.is_staff:
            return self.request.query_params.get('empresa')
        return getattr(user, 'empresa_id', None)

    def _model_has_field(self, model, field_name):
        try:
            model._meta.get_field(field_name)
            return True
        except Exception:
            return False

    def _validar_vinculos_empresa(self, data, empresa_id):
        for campo in ('idloja', 'loja'):
            loja = data.get(campo)
            if loja and getattr(loja, 'empresa_id', None) and loja.empresa_id != empresa_id:
                raise ValidationError({campo: 'A loja informada pertence a outra empresa.'})
        cliente = data.get('idcliente') or data.get('cliente')
        if cliente and getattr(cliente, 'empresa_id', None) and cliente.empresa_id != empresa_id:
            raise ValidationError({'cliente': 'O cliente informado pertence a outra empresa.'})
        fornecedor = data.get('idfornecedor') or data.get('fornecedor')
        if fornecedor and getattr(fornecedor, 'empresa_id', None) and fornecedor.empresa_id != empresa_id:
            raise ValidationError({'fornecedor': 'O fornecedor informado pertence a outra empresa.'})
        forma = data.get('forma')
        if forma and getattr(forma, 'empresa_id', None) and forma.empresa_id != empresa_id:
            raise ValidationError({'forma': 'A forma de pagamento pertence a outra empresa.'})
        caixa = data.get('caixa')
        if caixa and getattr(caixa, 'empresa_id', None) and caixa.empresa_id != empresa_id:
            raise ValidationError({'caixa': 'O caixa informado pertence a outra empresa.'})
        conta = data.get('conta_bancaria')
        if conta and getattr(conta, 'empresa_id', None) and conta.empresa_id != empresa_id:
            raise ValidationError({'conta_bancaria': 'A conta bancária pertence a outra empresa.'})
        vale = data.get('vale')
        if vale and getattr(vale, 'empresa_id', None) and vale.empresa_id != empresa_id:
            raise ValidationError({'vale': 'O vale-troca pertence a outra empresa.'})
        for campo in ('Idpagar', 'Idreceber'):
            titulo = data.get(campo)
            if titulo and getattr(titulo, 'empresa_id', None) and titulo.empresa_id != empresa_id:
                raise ValidationError({campo: 'O título pertence a outra empresa.'})
        for campo in ('Idpagaritem', 'Idreceberitem'):
            item = data.get(campo)
            titulo = getattr(item, 'Idpagar', None) or getattr(item, 'Idreceber', None)
            if titulo and getattr(titulo, 'empresa_id', None) and titulo.empresa_id != empresa_id:
                raise ValidationError({campo: 'A parcela pertence a outra empresa.'})
        for campo in ('venda_origem', 'venda_uso'):
            venda = data.get(campo)
            if venda and getattr(venda, 'empresa_id', None) and venda.empresa_id != empresa_id:
                raise ValidationError({campo: 'A venda pertence a outra empresa.'})


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
        empresa_id = self._empresa_id_usuario()
        forma = self.request.query_params.get('forma')
        codigo = self.request.query_params.get('codigo')
        if empresa_id:
            qs = qs.filter(forma__empresa_id=empresa_id)
        if forma:
            qs = qs.filter(forma_id=forma)
        if codigo:
            qs = qs.filter(forma__codigo=codigo)
        return qs


class CashbackConfigViewSet(BaseViewSet):
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa"]
    write_roles = ["Admin", "Diretor", "Gerente"]
    queryset = CashbackConfig.objects.all()
    serializer_class = CashbackConfigSerializer

    @action(detail=False, methods=['get'], url_path='ativa')
    def ativa(self, request):
        empresa_id = self._empresa_id_usuario()
        empresa = None
        if empresa_id:
            from cadastros.models import Empresa
            empresa = Empresa.objects.filter(pk=empresa_id).first()
        config = CashbackConfig.regra_ativa(empresa) or self.get_queryset().order_by('Idcashbackconfig').first()
        if not config:
            config = CashbackConfig.objects.create(empresa=empresa, nome='Regra padrão', ativo=False, percentual=0)
        return Response(self.get_serializer(config).data)


class CashbackMovimentoViewSet(BaseViewSet):
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa", "Vendedor", "AssistenteReceber"]
    write_roles = ["Admin", "Diretor", "Gerente"]
    queryset = CashbackMovimento.objects.select_related('cliente', 'venda_origem', 'venda_uso').all()
    serializer_class = CashbackMovimentoSerializer

    def perform_create(self, serializer):
        kwargs = {'criado_por': self.request.user if self.request.user.is_authenticated else None}
        if getattr(self.request.user, 'empresa_id', None):
            kwargs['empresa'] = self.request.user.empresa
        serializer.save(**kwargs)

    def get_queryset(self):
        qs = super().get_queryset()
        cliente = self.request.query_params.get('cliente')
        tipo = self.request.query_params.get('tipo')
        status_q = self.request.query_params.get('status')
        data_ini = self.request.query_params.get('data_ini')
        data_fim = self.request.query_params.get('data_fim')
        if cliente:
            qs = qs.filter(cliente_id=cliente)
        if tipo:
            qs = qs.filter(tipo=tipo)
        if status_q:
            qs = qs.filter(status=status_q)
        if data_ini:
            qs = qs.filter(criado_em__date__gte=data_ini)
        if data_fim:
            qs = qs.filter(criado_em__date__lte=data_fim)
        return qs

    @action(detail=False, methods=['get'], url_path='saldo')
    def saldo(self, request):
        cliente = request.query_params.get('cliente')
        if not cliente:
            return Response({'detail': 'Informe o cliente.'}, status=status.HTTP_400_BAD_REQUEST)
        empresa_id = self._empresa_id_usuario()
        return Response({'cliente': int(cliente), 'saldo': str(saldo_cashback_cliente(cliente, empresa=empresa_id))})


class ValeTrocaViewSet(BaseViewSet):
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa", "Vendedor", "AssistenteReceber"]
    write_roles = ["Admin", "Diretor", "Gerente"]
    queryset = ValeTroca.objects.select_related('cliente', 'loja', 'devolucao', 'devolucao__venda').prefetch_related('movimentos').all()
    serializer_class = ValeTrocaSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        cliente = self.request.query_params.get('cliente')
        loja = self.request.query_params.get('loja')
        status_q = self.request.query_params.get('status')
        documento = (self.request.query_params.get('documento') or '').strip()
        if cliente:
            qs = qs.filter(cliente_id=cliente)
        if loja:
            qs = qs.filter(loja_id=loja)
        if status_q:
            qs = qs.filter(status=status_q)
        if documento:
            qs = qs.filter(documento__icontains=documento)
        return qs

    @action(detail=False, methods=['get'], url_path='saldo')
    def saldo(self, request):
        cliente = request.query_params.get('cliente')
        if not cliente:
            return Response({'detail': 'Informe o cliente.'}, status=status.HTTP_400_BAD_REQUEST)
        empresa_id = self._empresa_id_usuario()
        return Response({'cliente': int(cliente), 'saldo': str(saldo_vale_troca_cliente(cliente, empresa=empresa_id))})

    @action(detail=False, methods=['get'], url_path='disponiveis')
    def disponiveis(self, request):
        cliente = request.query_params.get('cliente')
        if not cliente:
            return Response({'detail': 'Informe o cliente.'}, status=status.HTTP_400_BAD_REQUEST)
        hoje = timezone.localdate()
        qs = (
            self.get_queryset()
            .filter(cliente_id=cliente, status=ValeTroca.STATUS_ABERTO, saldo__gt=0)
            .filter(models.Q(validade__isnull=True) | models.Q(validade__gte=hoje))
            .order_by('criado_em')
        )
        return Response(self.get_serializer(qs, many=True).data)


class ValeTrocaMovimentoViewSet(BaseViewSet):
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa", "Vendedor", "AssistenteReceber"]
    write_roles = ["Admin", "Diretor", "Gerente"]
    queryset = ValeTrocaMovimento.objects.select_related('vale', 'venda_uso').all()
    serializer_class = ValeTrocaMovimentoSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(vale__empresa_id=empresa_id)
        vale = self.request.query_params.get('vale')
        cliente = self.request.query_params.get('cliente')
        if vale:
            qs = qs.filter(vale_id=vale)
        if cliente:
            qs = qs.filter(vale__cliente_id=cliente)
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
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(Idpagar__empresa_id=empresa_id)
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
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(Idpagaritem__Idpagar__empresa_id=empresa_id)
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
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(Idreceber__empresa_id=empresa_id)
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
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(Idreceberitem__Idreceber__empresa_id=empresa_id)
        parcela = self.request.query_params.get('receber_item')
        if parcela:
            qs = qs.filter(Idreceberitem_id=parcela)
        return qs
