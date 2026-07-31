from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from django.utils import timezone
from django.db import models, transaction
from decimal import Decimal, InvalidOperation
from accounts.permissions import HasModuleRole
from cadastros.models import Nat_Lancamento

try:
    from auditoria.models import AuditLog
except Exception:
    AuditLog = None

from .models import (
    ConfigFinanceira, TipoDespesaPdv,
    Caixa, ContaBancaria, MovimentacaoFinanceira, LancamentoContabil,
    CashbackConfig, CashbackMovimento, saldo_cashback_cliente,
    ValeTroca, ValeTrocaMovimento, saldo_vale_troca_cliente,
    Pagar, PagarItem, PagarRateio,
    Receber, ReceberItem, ReceberRateio,
    AntecipacaoRecebivel, AntecipacaoRecebivelItem,
    FormaPagamento, FormaPagamentoParcela, PrazoPagamento, PrazoPagamentoParcela
)
from .serializers import (
    ConfigFinanceiraSerializer, TipoDespesaPdvSerializer,
    CaixaSerializer, ContaBancariaSerializer, MovimentacaoFinanceiraSerializer,
    LancamentoContabilSerializer,
    CashbackConfigSerializer, CashbackMovimentoSerializer,
    ValeTrocaSerializer, ValeTrocaMovimentoSerializer,
    PagarSerializer, PagarItemSerializer, PagarRateioSerializer,
    ReceberSerializer, ReceberItemSerializer, ReceberRateioSerializer,
    AntecipacaoRecebivelSerializer,
    FormaPagamentoSerializer, FormaPagamentoParcelaSerializer,
    PrazoPagamentoSerializer, PrazoPagamentoParcelaSerializer
)
from .services import (
    estornar_lancamento_contabil_movimentacao,
    gerar_lancamento_contabil_movimentacao,
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


def _natureza_transferencia(empresa):
    natureza = (
        Nat_Lancamento.objects
        .filter(empresa=empresa, natureza_operacao='TRANSFERENCIA', ativo=True)
        .order_by('codigo')
        .first()
    )
    if natureza:
        return natureza
    return Nat_Lancamento.objects.create(
        empresa=empresa,
        codigo='9.01',
        categoria_principal='Transferências',
        subcategoria='Caixa e bancos',
        descricao='Transferência entre caixas e bancos',
        tipo='TRANSFERENCIA',
        status='ATIVO',
        tipo_natureza='NEUTRO',
        natureza_operacao='TRANSFERENCIA',
        categoria_gerencial='Transferências',
        movimenta_financeiro=True,
        entra_dre=False,
        ativo=True,
    )


def _natureza_taxa_antecipacao(empresa):
    natureza = (
        Nat_Lancamento.objects
        .filter(empresa=empresa, natureza_operacao='DESPESA', ativo=True)
        .filter(models.Q(descricao__icontains='antecip') | models.Q(descricao__icontains='financeira'))
        .order_by('codigo')
        .first()
    )
    if natureza:
        return natureza
    return Nat_Lancamento.objects.create(
        empresa=empresa,
        codigo='3.90',
        categoria_principal='Despesas financeiras',
        subcategoria='Taxas de cartão',
        descricao='Taxa de antecipação de recebíveis',
        tipo='DESPESA',
        status='ATIVO',
        tipo_natureza='DEBITO',
        natureza_operacao='DESPESA',
        categoria_gerencial='Financeiro',
        movimenta_financeiro=True,
        entra_dre=True,
        ativo=True,
    )


class BaseViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModuleRole]
    read_roles = ["Admin", "Diretor", "Gerente"]
    write_roles = ["Admin", "Diretor", "Gerente"]

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id and self._model_has_field(qs.model, 'empresa'):
            return qs.filter(empresa_id=empresa_id)
        if not self.request.user.is_superuser and self._model_has_field(qs.model, 'empresa'):
            return qs.none()
        return qs

    def perform_create(self, serializer):
        model = serializer.Meta.model
        user = self.request.user
        empresa_id = getattr(user, 'empresa_id', None)
        if self._model_has_field(model, 'empresa') and user.is_superuser:
            if not serializer.validated_data.get('empresa'):
                raise ValidationError({'empresa': 'Informe a empresa do cadastro.'})
            self._validar_vinculos_empresa(serializer.validated_data, serializer.validated_data['empresa'].id)
            serializer.save()
            return
        if not empresa_id and not user.is_superuser:
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
        if self.request.user.is_superuser:
            empresa = serializer.validated_data.get('empresa', getattr(serializer.instance, 'empresa', None))
            if not empresa and self._model_has_field(serializer.Meta.model, 'empresa'):
                raise ValidationError({'empresa': 'Informe a empresa do cadastro.'})
            if empresa:
                self._validar_vinculos_empresa(serializer.validated_data, empresa.id)
            serializer.save()
            return
        if not empresa_id and not self.request.user.is_superuser:
            raise ValidationError({'empresa': 'Usuário sem empresa vinculada.'})
        if empresa_id:
            self._validar_vinculos_empresa(serializer.validated_data, empresa_id)
        serializer.save()

    def _empresa_id_usuario(self):
        user = self.request.user
        if user.is_superuser:
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
        for campo in ('conta_bancaria', 'conta_liquidacao'):
            conta = data.get(campo)
            if conta and getattr(conta, 'empresa_id', None) and conta.empresa_id != empresa_id:
                raise ValidationError({campo: 'A conta bancária pertence a outra empresa.'})
        prazo = data.get('prazo_pagamento') or data.get('prazo')
        if prazo and getattr(prazo, 'empresa_id', None) and prazo.empresa_id != empresa_id:
            raise ValidationError({'prazo_pagamento': 'O prazo pertence a outra empresa.'})
        natureza = data.get('Idnatureza')
        if natureza and getattr(natureza, 'empresa_id', None) and natureza.empresa_id != empresa_id:
            raise ValidationError({'Idnatureza': 'A natureza informada pertence a outra empresa.'})
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


class PrazoPagamentoViewSet(BaseViewSet):
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa"]
    write_roles = ["Admin", "Diretor", "Gerente"]
    queryset = PrazoPagamento.objects.all().order_by('num_parcelas', 'codigo')
    serializer_class = PrazoPagamentoSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        ativo = self.request.query_params.get('ativo')
        codigo = self.request.query_params.get('codigo')
        if ativo in ('true', 'false', '1', '0'):
            qs = qs.filter(ativo=ativo in ('true', '1'))
        if codigo:
            qs = qs.filter(codigo=codigo)
        return qs


class PrazoPagamentoParcelaViewSet(BaseViewSet):
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa"]
    write_roles = ["Admin", "Diretor", "Gerente"]
    queryset = PrazoPagamentoParcela.objects.select_related('prazo').all().order_by('prazo__codigo', 'ordem')
    serializer_class = PrazoPagamentoParcelaSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        prazo = self.request.query_params.get('prazo')
        codigo = self.request.query_params.get('codigo')
        if empresa_id:
            qs = qs.filter(prazo__empresa_id=empresa_id)
        if prazo:
            qs = qs.filter(prazo_id=prazo)
        if codigo:
            qs = qs.filter(prazo__codigo=codigo)
        return qs


class ConfigFinanceiraViewSet(BaseViewSet):
    read_roles = ["Admin", "Diretor", "Gerente", "AssistenteReceber", "AssistentePagar"]
    write_roles = ["Admin", "Diretor", "Gerente"]
    queryset = ConfigFinanceira.objects.select_related(
        'empresa',
        'natureza_juros_pagos',
        'natureza_juros_recebidos',
        'natureza_tarifas_pagas',
        'natureza_multas_pagas',
        'natureza_multas_recebidas',
        'natureza_descontos_concedidos',
        'natureza_descontos_obtidos',
    ).all()
    serializer_class = ConfigFinanceiraSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_superuser:
            empresa = self.request.query_params.get('empresa')
            return qs.filter(empresa_id=empresa) if empresa else qs
        empresa_id = getattr(self.request.user, 'empresa_id', None)
        return qs.filter(empresa_id=empresa_id) if empresa_id else qs.none()

    @action(detail=False, methods=['get', 'patch'], url_path='atual')
    def atual(self, request):
        empresa = getattr(request.user, 'empresa', None)
        if request.user.is_superuser:
            empresa_id = request.query_params.get('empresa') or request.data.get('empresa')
            if empresa_id:
                from cadastros.models import Empresa
                empresa = Empresa.objects.filter(pk=empresa_id).first()
        if not empresa:
            return Response({'detail': 'Usuário sem empresa vinculada.'}, status=status.HTTP_400_BAD_REQUEST)
        config, _ = ConfigFinanceira.objects.get_or_create(empresa=empresa)
        if request.method.lower() == 'patch':
            serializer = self.get_serializer(config, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            self._validar_naturezas_config(serializer.validated_data, empresa.id)
            serializer.save()
            return Response(serializer.data)
        return Response(self.get_serializer(config).data)

    def _validar_naturezas_config(self, data, empresa_id):
        for campo, natureza in data.items():
            if campo.startswith('natureza_') and natureza and natureza.empresa_id != empresa_id:
                raise ValidationError({campo: 'A natureza selecionada pertence a outra empresa.'})


class TipoDespesaPdvViewSet(BaseViewSet):
    read_module_keys = ["financeiro", "vendas"]
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa"]
    write_roles = ["Admin", "Diretor", "Gerente"]
    queryset = TipoDespesaPdv.objects.select_related('empresa', 'Idnatureza').all()
    serializer_class = TipoDespesaPdvSerializer
    search_fields = ['codigo', 'descricao', 'Idnatureza__codigo', 'Idnatureza__descricao']
    ordering_fields = ['codigo', 'descricao', 'ativo', 'data_cadastro']

    def get_queryset(self):
        qs = super().get_queryset()
        ativo = self.request.query_params.get('ativo')
        if ativo in ('true', 'false', '1', '0'):
            qs = qs.filter(ativo=ativo in ('true', '1'))
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
    read_module_keys = ["financeiro", "vendas"]
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa"]
    write_roles = ["Admin", "Diretor", "Gerente"]
    action_roles = {
        "lancar_despesa": ["Admin", "Diretor", "Gerente", "Caixa"],
        "transferir": ["Admin", "Diretor", "Gerente", "Caixa"],
    }
    queryset = Caixa.objects.select_related('idloja').all()
    serializer_class = CaixaSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        tipo_usuario = getattr(user, "type", None)
        if not user.is_superuser and tipo_usuario in {"Caixa", "Vendedor"}:
            lojas_ids = list(user.lojas.values_list("id", flat=True))
            if getattr(user, "loja_id", None) and user.loja_id not in lojas_ids:
                lojas_ids.append(user.loja_id)
            if tipo_usuario == "Caixa":
                qs = qs.filter(
                    models.Q(idloja_id__in=lojas_ids) | models.Q(tipo_caixa=Caixa.TIPO_MASTER)
                ) if lojas_ids else qs.filter(tipo_caixa=Caixa.TIPO_MASTER)
            else:
                qs = qs.filter(idloja_id__in=lojas_ids) if lojas_ids else qs.none()
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

    @action(detail=False, methods=['post'], url_path='transferir')
    def transferir(self, request):
        origem_id = request.data.get('caixa_origem')
        destino_id = request.data.get('caixa_destino')
        data_movimento = request.data.get('data_movimento') or timezone.localdate().isoformat()
        documento_informado = (request.data.get('documento') or '').strip()
        observacao = (request.data.get('observacao') or '').strip()
        try:
            valor = Decimal(str(request.data.get('valor')))
        except (TypeError, ValueError, InvalidOperation):
            return Response({'detail': 'Informe valor numérico.'}, status=status.HTTP_400_BAD_REQUEST)

        if valor <= 0:
            return Response({'detail': 'Informe valor maior que zero.'}, status=status.HTTP_400_BAD_REQUEST)
        if not origem_id or not destino_id:
            return Response({'detail': 'Informe caixa de origem e destino.'}, status=status.HTTP_400_BAD_REQUEST)
        if str(origem_id) == str(destino_id):
            return Response({'detail': 'Caixa de origem e destino devem ser diferentes.'}, status=status.HTTP_400_BAD_REQUEST)

        empresa_id = self._empresa_id_usuario()
        with transaction.atomic():
            caixas = (
                Caixa.objects
                .select_for_update()
                .select_related('idloja', 'empresa')
                .filter(pk__in=[origem_id, destino_id], ativo=True)
            )
            if empresa_id:
                caixas = caixas.filter(empresa_id=empresa_id)
            elif not request.user.is_superuser:
                caixas = caixas.none()

            caixas_por_id = {str(caixa.pk): caixa for caixa in caixas}
            origem = caixas_por_id.get(str(origem_id))
            destino = caixas_por_id.get(str(destino_id))
            if not origem or not destino:
                return Response({'detail': 'Caixa de origem ou destino não encontrado/ativo para a empresa.'}, status=status.HTTP_400_BAD_REQUEST)
            if origem.empresa_id != destino.empresa_id:
                return Response({'detail': 'Transferência entre empresas diferentes não é permitida.'}, status=status.HTTP_400_BAD_REQUEST)
            if Decimal(origem.saldo_atual or 0) < valor:
                return Response({'detail': 'Saldo insuficiente no caixa de origem.'}, status=status.HTTP_400_BAD_REQUEST)

            agora = timezone.localtime()
            documento = (documento_informado[:50] or f"TRANSF-{agora:%Y%m%d%H%M%S}-{request.user.pk or '0'}")
            loja_saida = origem.idloja or destino.idloja
            loja_entrada = destino.idloja or origem.idloja
            if not loja_saida or not loja_entrada:
                return Response({'detail': 'A transferência precisa ter ao menos uma loja vinculada nos caixas.'}, status=status.HTTP_400_BAD_REQUEST)

            origem.saldo_atual = Decimal(origem.saldo_atual or 0) - valor
            destino.saldo_atual = Decimal(destino.saldo_atual or 0) + valor
            origem.save(update_fields=['saldo_atual'])
            destino.save(update_fields=['saldo_atual'])

            hist_saida = f"Transferência para {destino.codigo} - {destino.descricao}"
            hist_entrada = f"Transferência de {origem.codigo} - {origem.descricao}"
            if observacao:
                hist_saida = f"{hist_saida} | {observacao}"
                hist_entrada = f"{hist_entrada} | {observacao}"
            natureza = _natureza_transferencia(origem.empresa)

            mov_saida = MovimentacaoFinanceira.objects.create(
                empresa=origem.empresa,
                idloja=loja_saida,
                data_movimento=data_movimento,
                tipo=MovimentacaoFinanceira.TIPO_SAIDA,
                status=MovimentacaoFinanceira.STATUS_EFETIVA,
                origem=MovimentacaoFinanceira.ORIGEM_TRANSFERENCIA,
                valor=valor,
                historico=hist_saida[:255],
                documento=documento,
                Idnatureza=natureza,
                caixa=origem,
            )
            mov_entrada = MovimentacaoFinanceira.objects.create(
                empresa=destino.empresa,
                idloja=loja_entrada,
                data_movimento=data_movimento,
                tipo=MovimentacaoFinanceira.TIPO_ENTRADA,
                status=MovimentacaoFinanceira.STATUS_EFETIVA,
                origem=MovimentacaoFinanceira.ORIGEM_TRANSFERENCIA,
                valor=valor,
                historico=hist_entrada[:255],
                documento=documento,
                Idnatureza=natureza,
                caixa=destino,
            )
            gerar_lancamento_contabil_movimentacao(mov_saida)
            gerar_lancamento_contabil_movimentacao(mov_entrada)

        _audit('caixa', origem.pk, {
            'documento': documento,
            'origem': origem.pk,
            'destino': destino.pk,
            'valor': str(valor),
            'movimentacoes': [mov_saida.pk, mov_entrada.pk],
        }, request, action='transferir')
        return Response({
            'documento': documento,
            'saida': MovimentacaoFinanceiraSerializer(mov_saida).data,
            'entrada': MovimentacaoFinanceiraSerializer(mov_entrada).data,
            'caixa_origem': CaixaSerializer(origem).data,
            'caixa_destino': CaixaSerializer(destino).data,
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='lancar-despesa')
    def lancar_despesa(self, request, pk=None):
        try:
            valor = Decimal(str(request.data.get('valor')))
        except (TypeError, ValueError, InvalidOperation):
            return Response({'detail': 'Informe valor numérico.'}, status=status.HTTP_400_BAD_REQUEST)

        if valor <= 0:
            return Response({'detail': 'Informe valor maior que zero.'}, status=status.HTTP_400_BAD_REQUEST)

        tipo_despesa_id = request.data.get('tipo_despesa') or request.data.get('tipo_despesa_pdv') or request.data.get('Idtipodespesapdv')
        natureza_id = request.data.get('Idnatureza') or request.data.get('natureza')
        if not tipo_despesa_id and not natureza_id:
            return Response({'detail': 'Informe o tipo de despesa.'}, status=status.HTTP_400_BAD_REQUEST)

        data_movimento = request.data.get('data_movimento') or timezone.localdate().isoformat()
        documento_informado = (request.data.get('documento') or '').strip()
        historico_informado = (request.data.get('historico') or request.data.get('observacao') or '').strip()

        with transaction.atomic():
            caixa = (
                self.get_queryset()
                .select_for_update()
                .select_related('empresa', 'idloja')
                .filter(pk=pk, ativo=True)
                .first()
            )
            if not caixa:
                return Response({'detail': 'Caixa não encontrado ou inativo.'}, status=status.HTTP_404_NOT_FOUND)
            if not caixa.idloja:
                return Response({'detail': 'O caixa precisa estar vinculado a uma loja.'}, status=status.HTTP_400_BAD_REQUEST)

            tipo_despesa = None
            if tipo_despesa_id:
                tipo_despesa = (
                    TipoDespesaPdv.objects
                    .select_related('Idnatureza')
                    .filter(pk=tipo_despesa_id, empresa=caixa.empresa, ativo=True)
                    .first()
                )
                if not tipo_despesa:
                    return Response({'detail': 'Tipo de despesa não encontrado para a empresa.'}, status=status.HTTP_400_BAD_REQUEST)
                if tipo_despesa.exige_documento and not documento_informado:
                    return Response({'detail': 'Informe o documento desta despesa.'}, status=status.HTTP_400_BAD_REQUEST)
                natureza = tipo_despesa.Idnatureza
            else:
                natureza = (
                    Nat_Lancamento.objects
                    .filter(pk=natureza_id, empresa=caixa.empresa, ativo=True)
                    .first()
                )
            if not natureza:
                return Response({'detail': 'Natureza de despesa não encontrada para a empresa.'}, status=status.HTTP_400_BAD_REQUEST)
            if str(natureza.natureza_operacao or '').upper() not in {'DESPESA', 'AJUSTE'}:
                return Response({'detail': 'A natureza deve ser de despesa ou ajuste.'}, status=status.HTTP_400_BAD_REQUEST)

            agora = timezone.localtime()
            documento = (documento_informado[:50] or f"DESP-{agora:%Y%m%d%H%M%S}-{request.user.pk or '0'}")
            descricao_tipo = getattr(tipo_despesa, 'descricao', '') if tipo_despesa else ''
            historico = historico_informado or f"Despesa PDV {descricao_tipo or natureza.descricao} {documento}"

            caixa.saldo_atual = Decimal(caixa.saldo_atual or 0) - valor
            caixa.save(update_fields=['saldo_atual'])

            movimento = MovimentacaoFinanceira.objects.create(
                empresa=caixa.empresa,
                idloja=caixa.idloja,
                data_movimento=data_movimento,
                tipo=MovimentacaoFinanceira.TIPO_SAIDA,
                status=MovimentacaoFinanceira.STATUS_EFETIVA,
                origem=MovimentacaoFinanceira.ORIGEM_MANUAL,
                valor=valor,
                historico=historico[:255],
                documento=documento,
                Idnatureza=natureza,
                caixa=caixa,
            )
            gerar_lancamento_contabil_movimentacao(movimento)

        _audit('caixa', caixa.pk, {
            'documento': documento,
            'valor': str(valor),
            'tipo_despesa': tipo_despesa.pk if tipo_despesa else None,
            'natureza': natureza.pk,
            'movimentacao': movimento.pk,
        }, request, action='lancar_despesa')
        return Response({
            'movimentacao': MovimentacaoFinanceiraSerializer(movimento).data,
            'caixa': CaixaSerializer(caixa).data,
        }, status=status.HTTP_201_CREATED)


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

    @action(detail=False, methods=['post'], url_path='transferir')
    def transferir(self, request):
        origem_tipo = (request.data.get('origem_tipo') or '').upper()
        destino_tipo = (request.data.get('destino_tipo') or '').upper()
        origem_id = request.data.get('origem_id')
        destino_id = request.data.get('destino_id')
        data_movimento = request.data.get('data_movimento') or timezone.localdate().isoformat()
        documento_informado = (request.data.get('documento') or '').strip()
        observacao = (request.data.get('observacao') or '').strip()
        try:
            valor = Decimal(str(request.data.get('valor')))
        except (TypeError, ValueError, InvalidOperation):
            return Response({'detail': 'Informe valor numérico.'}, status=status.HTTP_400_BAD_REQUEST)

        if valor <= 0:
            return Response({'detail': 'Informe valor maior que zero.'}, status=status.HTTP_400_BAD_REQUEST)
        if origem_tipo not in ('CAIXA', 'CONTA') or destino_tipo not in ('CAIXA', 'CONTA'):
            return Response({'detail': 'Informe origem e destino válidos.'}, status=status.HTTP_400_BAD_REQUEST)
        if not origem_id or not destino_id:
            return Response({'detail': 'Informe origem e destino.'}, status=status.HTTP_400_BAD_REQUEST)
        if origem_tipo == destino_tipo and str(origem_id) == str(destino_id):
            return Response({'detail': 'Origem e destino devem ser diferentes.'}, status=status.HTTP_400_BAD_REQUEST)

        empresa_id = self._empresa_id_usuario()
        with transaction.atomic():
            origem = self._buscar_destino_transferencia(origem_tipo, origem_id, empresa_id)
            destino = self._buscar_destino_transferencia(destino_tipo, destino_id, empresa_id)
            if not origem or not destino:
                return Response({'detail': 'Origem ou destino não encontrado/ativo para a empresa.'}, status=status.HTTP_400_BAD_REQUEST)
            if origem.empresa_id != destino.empresa_id:
                return Response({'detail': 'Transferência entre empresas diferentes não é permitida.'}, status=status.HTTP_400_BAD_REQUEST)
            if Decimal(origem.saldo_atual or 0) < valor:
                return Response({'detail': 'Saldo insuficiente na origem.'}, status=status.HTTP_400_BAD_REQUEST)

            agora = timezone.localtime()
            documento = (documento_informado[:50] or f"TRANSF-{agora:%Y%m%d%H%M%S}-{request.user.pk or '0'}")
            origem.saldo_atual = Decimal(origem.saldo_atual or 0) - valor
            destino.saldo_atual = Decimal(destino.saldo_atual or 0) + valor
            origem.save(update_fields=['saldo_atual'])
            destino.save(update_fields=['saldo_atual'])

            origem_nome = self._destino_label(origem_tipo, origem)
            destino_nome = self._destino_label(destino_tipo, destino)
            loja_saida = origem.idloja or destino.idloja
            loja_entrada = destino.idloja or origem.idloja
            hist_saida = f"Transferência para {destino_nome}"
            hist_entrada = f"Transferência de {origem_nome}"
            if observacao:
                hist_saida = f"{hist_saida} | {observacao}"
                hist_entrada = f"{hist_entrada} | {observacao}"
            natureza = _natureza_transferencia(origem.empresa)

            mov_saida = MovimentacaoFinanceira.objects.create(
                empresa=origem.empresa,
                idloja=loja_saida,
                data_movimento=data_movimento,
                tipo=MovimentacaoFinanceira.TIPO_SAIDA,
                status=MovimentacaoFinanceira.STATUS_EFETIVA,
                origem=MovimentacaoFinanceira.ORIGEM_TRANSFERENCIA,
                valor=valor,
                historico=hist_saida[:255],
                documento=documento,
                Idnatureza=natureza,
                caixa=origem if origem_tipo == 'CAIXA' else None,
                conta_bancaria=origem if origem_tipo == 'CONTA' else None,
            )
            mov_entrada = MovimentacaoFinanceira.objects.create(
                empresa=destino.empresa,
                idloja=loja_entrada,
                data_movimento=data_movimento,
                tipo=MovimentacaoFinanceira.TIPO_ENTRADA,
                status=MovimentacaoFinanceira.STATUS_EFETIVA,
                origem=MovimentacaoFinanceira.ORIGEM_TRANSFERENCIA,
                valor=valor,
                historico=hist_entrada[:255],
                documento=documento,
                Idnatureza=natureza,
                caixa=destino if destino_tipo == 'CAIXA' else None,
                conta_bancaria=destino if destino_tipo == 'CONTA' else None,
            )
            gerar_lancamento_contabil_movimentacao(mov_saida)
            gerar_lancamento_contabil_movimentacao(mov_entrada)

        _audit('contabancaria', destino.pk if destino_tipo == 'CONTA' else origem.pk, {
            'documento': documento,
            'origem_tipo': origem_tipo,
            'origem_id': origem.pk,
            'destino_tipo': destino_tipo,
            'destino_id': destino.pk,
            'valor': str(valor),
            'movimentacoes': [mov_saida.pk, mov_entrada.pk],
        }, request, action='transferir')
        return Response({
            'documento': documento,
            'saida': MovimentacaoFinanceiraSerializer(mov_saida).data,
            'entrada': MovimentacaoFinanceiraSerializer(mov_entrada).data,
        }, status=status.HTTP_201_CREATED)

    def _buscar_destino_transferencia(self, tipo, pk, empresa_id):
        model = Caixa if tipo == 'CAIXA' else ContaBancaria
        qs = model.objects.select_for_update().select_related('idloja', 'empresa').filter(pk=pk, ativo=True)
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            qs = qs.none()
        return qs.first()

    def _destino_label(self, tipo, obj):
        if tipo == 'CAIXA':
            return f"{obj.codigo} - {obj.descricao}"
        return f"{obj.descricao} - {obj.banco} Ag {obj.agencia} Cc {obj.conta}"


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
        forma_pagamento = self.request.query_params.get('forma_pagamento')
        data_movimento = self.request.query_params.get('data_movimento')
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
        if forma_pagamento:
            qs = qs.filter(FormaPagamento=forma_pagamento)
        if data_movimento:
            qs = qs.filter(data_movimento=data_movimento)
        if data_ini:
            qs = qs.filter(data_movimento__gte=data_ini)
        if data_fim:
            qs = qs.filter(data_movimento__lte=data_fim)
        return qs

    def _valor_consulta_natureza(self, mov):
        valor = Decimal(mov.valor or 0)
        operacao = ((mov.Idnatureza.natureza_operacao if mov.Idnatureza_id else '') or '').upper()
        receita = Decimal('0.00')
        despesa = Decimal('0.00')
        transferencia = Decimal('0.00')

        if operacao == 'TRANSFERENCIA':
            if mov.tipo == MovimentacaoFinanceira.TIPO_SAIDA:
                transferencia = valor
        elif mov.tipo == MovimentacaoFinanceira.TIPO_ENTRADA:
            receita = valor
        elif mov.tipo == MovimentacaoFinanceira.TIPO_SAIDA:
            despesa = valor

        return receita, despesa, transferencia

    def _money(self, value):
        return str(Decimal(value or 0).quantize(Decimal('0.01')))

    def _documento_parcela_generico(self, titulo, parcela_n):
        titulo = str(titulo or '').strip()
        sufixo = f"-{parcela_n}"
        if titulo.endswith(sufixo):
            return titulo
        return f"{titulo}{sufixo}" if titulo else sufixo.lstrip('-')

    @action(detail=False, methods=['get'], url_path='consulta-naturezas')
    def consulta_naturezas(self, request):
        qs = self.get_queryset().select_related('Idnatureza', 'idloja').filter(Idnatureza__isnull=False)

        natureza = request.query_params.get('natureza')
        operacao = (request.query_params.get('operacao') or '').upper()
        status_q = (request.query_params.get('status') or '').upper()

        if natureza:
            qs = qs.filter(Idnatureza_id=natureza)
        if operacao:
            qs = qs.filter(Idnatureza__natureza_operacao=operacao)
        if status_q and status_q != 'TODOS':
            qs = qs.filter(status=status_q)
        elif not status_q:
            qs = qs.filter(status=MovimentacaoFinanceira.STATUS_EFETIVA)

        totais = {
            'receitas': Decimal('0.00'),
            'despesas': Decimal('0.00'),
            'transferencias': Decimal('0.00'),
            'resultado': Decimal('0.00'),
            'movimentacoes': 0,
        }
        por_natureza = {}
        por_categoria = {}
        detalhes = []

        for mov in qs.order_by('-data_movimento', '-Idmovimentacao'):
            nat = mov.Idnatureza
            receita, despesa, transferencia = self._valor_consulta_natureza(mov)
            resultado = receita - despesa
            categoria = nat.categoria_gerencial or nat.categoria_principal or 'Sem categoria'
            nat_key = nat.pk
            cat_key = categoria

            totais['receitas'] += receita
            totais['despesas'] += despesa
            totais['transferencias'] += transferencia
            totais['resultado'] += resultado
            totais['movimentacoes'] += 1

            if nat_key not in por_natureza:
                por_natureza[nat_key] = {
                    'natureza_id': nat.pk,
                    'codigo': nat.codigo,
                    'descricao': nat.descricao,
                    'operacao': nat.natureza_operacao,
                    'categoria_gerencial': categoria,
                    'receitas': Decimal('0.00'),
                    'despesas': Decimal('0.00'),
                    'transferencias': Decimal('0.00'),
                    'resultado': Decimal('0.00'),
                    'quantidade': 0,
                }
            por_natureza[nat_key]['receitas'] += receita
            por_natureza[nat_key]['despesas'] += despesa
            por_natureza[nat_key]['transferencias'] += transferencia
            por_natureza[nat_key]['resultado'] += resultado
            por_natureza[nat_key]['quantidade'] += 1

            if cat_key not in por_categoria:
                por_categoria[cat_key] = {
                    'categoria_gerencial': categoria,
                    'receitas': Decimal('0.00'),
                    'despesas': Decimal('0.00'),
                    'transferencias': Decimal('0.00'),
                    'resultado': Decimal('0.00'),
                    'quantidade': 0,
                }
            por_categoria[cat_key]['receitas'] += receita
            por_categoria[cat_key]['despesas'] += despesa
            por_categoria[cat_key]['transferencias'] += transferencia
            por_categoria[cat_key]['resultado'] += resultado
            por_categoria[cat_key]['quantidade'] += 1

            detalhes.append({
                'id': mov.Idmovimentacao,
                'data_movimento': mov.data_movimento,
                'loja': getattr(mov.idloja, 'nome_loja', ''),
                'documento': mov.documento or '',
                'historico': mov.historico,
                'tipo': mov.tipo,
                'status': mov.status,
                'origem': mov.origem,
                'natureza': f'{nat.codigo} - {nat.descricao}',
                'categoria_gerencial': categoria,
                'valor': self._money(mov.valor),
                'receita': self._money(receita),
                'despesa': self._money(despesa),
                'transferencia': self._money(transferencia),
            })

        def serialize_rows(rows):
            out = []
            for row in rows:
                item = row.copy()
                item['receitas'] = self._money(item['receitas'])
                item['despesas'] = self._money(item['despesas'])
                item['transferencias'] = self._money(item['transferencias'])
                item['resultado'] = self._money(item['resultado'])
                out.append(item)
            return out

        return Response({
            'periodo': {
                'data_ini': request.query_params.get('data_ini') or '',
                'data_fim': request.query_params.get('data_fim') or '',
            },
            'totais': {
                'receitas': self._money(totais['receitas']),
                'despesas': self._money(totais['despesas']),
                'transferencias': self._money(totais['transferencias']),
                'resultado': self._money(totais['resultado']),
                'movimentacoes': totais['movimentacoes'],
            },
            'por_natureza': serialize_rows(
                sorted(por_natureza.values(), key=lambda x: (x['operacao'], x['codigo'] or ''))
            ),
            'por_categoria': serialize_rows(
                sorted(por_categoria.values(), key=lambda x: x['categoria_gerencial'] or '')
            ),
            'detalhes': detalhes,
        })

    def _classificar_linha_dre(self, natureza, mov):
        operacao = ((natureza.natureza_operacao if natureza else '') or '').upper()
        categoria = (getattr(natureza, 'categoria_gerencial', '') or '').lower()
        texto = ' '.join([
            getattr(natureza, 'categoria_principal', '') or '',
            getattr(natureza, 'subcategoria', '') or '',
            getattr(natureza, 'descricao', '') or '',
            getattr(natureza, 'categoria_gerencial', '') or '',
        ]).lower()

        if operacao == 'RECEITA':
            return 'RECEITA_BRUTA'
        if operacao == 'DESPESA':
            if any(palavra in texto for palavra in ('devolu', 'desconto', 'abatimento', 'cancelamento')):
                return 'DEDUCOES'
            if any(palavra in texto for palavra in ('cmv', 'custo', 'mercadoria vendida')):
                return 'CUSTOS'
            if any(palavra in texto for palavra in ('financeir', 'taxa', 'tarifa', 'juros', 'cartao', 'cartão', 'bancar', 'antecip')):
                return 'DESPESAS_FINANCEIRAS'
            if any(palavra in texto for palavra in ('tribut', 'imposto', 'icms', 'pis', 'cofins', 'csll', 'irpj', 'simples')):
                return 'TRIBUTOS'
            if 'administr' in categoria:
                return 'DESPESAS_ADMINISTRATIVAS'
            if any(palavra in categoria for palavra in ('venda', 'comercial')):
                return 'DESPESAS_VENDAS'
            if getattr(mov, 'origem', '') == MovimentacaoFinanceira.ORIGEM_COMISSAO or any(
                palavra in texto for palavra in ('comiss', 'venda', 'marketing', 'frete')
            ):
                return 'DESPESAS_VENDAS'
            return 'DESPESAS_ADMINISTRATIVAS'
        return 'OUTROS'

    @action(detail=False, methods=['get'], url_path='dre')
    def dre(self, request):
        regime = (request.query_params.get('regime') or 'caixa').strip().lower()
        if regime not in ('caixa', 'competencia'):
            regime = 'caixa'
        qs = (
            self.get_queryset()
            .select_related('Idnatureza', 'idloja')
            .filter(
                status=MovimentacaoFinanceira.STATUS_EFETIVA,
                Idnatureza__isnull=False,
                Idnatureza__entra_dre=True,
            )
            .exclude(Idnatureza__natureza_operacao='TRANSFERENCIA')
            .exclude(origem=MovimentacaoFinanceira.ORIGEM_MANUAL, historico__startswith='Consolidacao master PDV')
        )

        grupos = {
            'RECEITA_BRUTA': {'codigo': '1', 'grupo': 'Receita bruta', 'valor': Decimal('0.00'), 'linhas': {}},
            'DEDUCOES': {'codigo': '2', 'grupo': 'Deduções da receita', 'valor': Decimal('0.00'), 'linhas': {}},
            'CUSTOS': {'codigo': '3', 'grupo': 'Custos', 'valor': Decimal('0.00'), 'linhas': {}},
            'DESPESAS_VENDAS': {'codigo': '4', 'grupo': 'Despesas com vendas', 'valor': Decimal('0.00'), 'linhas': {}},
            'DESPESAS_ADMINISTRATIVAS': {'codigo': '5', 'grupo': 'Despesas administrativas', 'valor': Decimal('0.00'), 'linhas': {}},
            'DESPESAS_FINANCEIRAS': {'codigo': '6', 'grupo': 'Despesas financeiras', 'valor': Decimal('0.00'), 'linhas': {}},
            'TRIBUTOS': {'codigo': '7', 'grupo': 'Tributos', 'valor': Decimal('0.00'), 'linhas': {}},
            'OUTROS': {'codigo': '8', 'grupo': 'Outros resultados', 'valor': Decimal('0.00'), 'linhas': {}},
        }
        detalhes = []

        def adicionar_linha(obj, nat, data_movimento, loja_nome, documento, historico, origem, tipo, valor, row_id):
            grupo_key = self._classificar_linha_dre(nat, obj)
            if grupo_key == 'RECEITA_BRUTA':
                sinal = Decimal('-1.00') if tipo == MovimentacaoFinanceira.TIPO_SAIDA else Decimal('1.00')
            else:
                sinal = Decimal('1.00') if tipo == MovimentacaoFinanceira.TIPO_ENTRADA else Decimal('-1.00')
            valor_dre = Decimal(valor or 0) * sinal
            categoria = nat.categoria_gerencial or nat.categoria_principal or 'Sem categoria'
            linha_key = nat.pk

            grupo = grupos[grupo_key]
            grupo['valor'] += valor_dre
            if linha_key not in grupo['linhas']:
                grupo['linhas'][linha_key] = {
                    'natureza_id': nat.pk,
                    'codigo': nat.codigo,
                    'descricao': nat.descricao,
                    'categoria_gerencial': categoria,
                    'valor': Decimal('0.00'),
                    'quantidade': 0,
                }
            grupo['linhas'][linha_key]['valor'] += valor_dre
            grupo['linhas'][linha_key]['quantidade'] += 1

            detalhes.append({
                'id': row_id,
                'data_movimento': data_movimento,
                'loja': loja_nome,
                'documento': documento or '',
                'historico': historico,
                'origem': origem,
                'grupo': grupo['grupo'],
                'natureza': f'{nat.codigo} - {nat.descricao}',
                'categoria_gerencial': categoria,
                'valor': self._money(valor_dre),
            })

        if regime == 'competencia':
            componente = (
                models.Q(documento__endswith='-JUR') |
                models.Q(documento__endswith='-MUL') |
                models.Q(documento__endswith='-TAR') |
                models.Q(documento__endswith='-DSC')
            )
            qs = qs.filter(
                ~models.Q(origem__in=[MovimentacaoFinanceira.ORIGEM_PAGAR, MovimentacaoFinanceira.ORIGEM_RECEBER]) |
                componente
            )

        for mov in qs.order_by('-data_movimento', '-Idmovimentacao'):
            nat = mov.Idnatureza
            adicionar_linha(
                mov,
                nat,
                mov.data_movimento,
                getattr(mov.idloja, 'nome_loja', ''),
                mov.documento,
                mov.historico,
                mov.origem,
                mov.tipo,
                mov.valor,
                mov.Idmovimentacao,
            )

        if regime == 'competencia':
            empresa_id = self._empresa_id_usuario()
            loja = request.query_params.get('loja')
            data_ini = request.query_params.get('data_ini')
            data_fim = request.query_params.get('data_fim')

            pagar_qs = (
                PagarItem.objects
                .select_related('Idpagar', 'Idpagar__idloja', 'Idnatureza', 'Idpagar__Idnatureza')
                .exclude(status=PagarItem.STATUS_CANCELADO)
                .filter(
                    models.Q(Idnatureza__entra_dre=True) |
                    models.Q(Idnatureza__isnull=True, Idpagar__Idnatureza__entra_dre=True)
                )
            )
            receber_qs = (
                ReceberItem.objects
                .select_related('Idreceber', 'Idreceber__idloja', 'Idnatureza', 'Idreceber__Idnatureza')
                .exclude(status=ReceberItem.STATUS_CANCELADO)
                .filter(
                    models.Q(Idnatureza__entra_dre=True) |
                    models.Q(Idnatureza__isnull=True, Idreceber__Idnatureza__entra_dre=True)
                )
            )
            if empresa_id:
                pagar_qs = pagar_qs.filter(Idpagar__empresa_id=empresa_id)
                receber_qs = receber_qs.filter(Idreceber__empresa_id=empresa_id)
            elif not request.user.is_superuser:
                pagar_qs = pagar_qs.none()
                receber_qs = receber_qs.none()
            if loja:
                pagar_qs = pagar_qs.filter(Idpagar__idloja_id=loja)
                receber_qs = receber_qs.filter(Idreceber__idloja_id=loja)
            if data_ini:
                pagar_qs = pagar_qs.filter(Idpagar__Data_emissao__gte=data_ini)
                receber_qs = receber_qs.filter(Idreceber__Data_emissao__gte=data_ini)
            if data_fim:
                pagar_qs = pagar_qs.filter(Idpagar__Data_emissao__lte=data_fim)
                receber_qs = receber_qs.filter(Idreceber__Data_emissao__lte=data_fim)

            for item in pagar_qs.order_by('-Idpagar__Data_emissao', '-Idpagaritem'):
                titulo = item.Idpagar
                nat = item.Idnatureza or titulo.Idnatureza
                documento = self._documento_parcela_generico(titulo.Titulo or titulo.pk, item.parcela_n)
                mov = type('DreObj', (), {'origem': 'PAGAR_COMPETENCIA'})()
                adicionar_linha(
                    mov,
                    nat,
                    titulo.Data_emissao,
                    getattr(titulo.idloja, 'nome_loja', ''),
                    documento,
                    f"Competência contas a pagar {documento}",
                    'PAGAR_COMPETENCIA',
                    MovimentacaoFinanceira.TIPO_SAIDA,
                    item.valor_parcela,
                    f"P{item.pk}",
                )

            for item in receber_qs.order_by('-Idreceber__Data_emissao', '-Idreceberitem'):
                titulo = item.Idreceber
                nat = item.Idnatureza or titulo.Idnatureza
                documento = self._documento_parcela_generico(titulo.Titulo or titulo.pk, item.parcela_n)
                mov = type('DreObj', (), {'origem': 'RECEBER_COMPETENCIA'})()
                adicionar_linha(
                    mov,
                    nat,
                    titulo.Data_emissao,
                    getattr(titulo.idloja, 'nome_loja', ''),
                    documento,
                    f"Competência contas a receber {documento}",
                    'RECEBER_COMPETENCIA',
                    MovimentacaoFinanceira.TIPO_ENTRADA,
                    item.valor_parcela,
                    f"R{item.pk}",
                )

        receita_bruta = grupos['RECEITA_BRUTA']['valor']
        deducoes = grupos['DEDUCOES']['valor']
        receita_liquida = receita_bruta + deducoes
        custos = grupos['CUSTOS']['valor']
        lucro_bruto = receita_liquida + custos
        despesas_vendas = grupos['DESPESAS_VENDAS']['valor']
        despesas_administrativas = grupos['DESPESAS_ADMINISTRATIVAS']['valor']
        despesas_financeiras = grupos['DESPESAS_FINANCEIRAS']['valor']
        tributos = grupos['TRIBUTOS']['valor']
        despesas = despesas_vendas + despesas_administrativas + despesas_financeiras + tributos
        outros = grupos['OUTROS']['valor']
        resultado = lucro_bruto + despesas + outros

        def serialize_grupos():
            saida = []
            for key in (
                'RECEITA_BRUTA',
                'DEDUCOES',
                'CUSTOS',
                'DESPESAS_VENDAS',
                'DESPESAS_ADMINISTRATIVAS',
                'DESPESAS_FINANCEIRAS',
                'TRIBUTOS',
                'OUTROS',
            ):
                grupo = grupos[key]
                linhas = []
                for linha in sorted(grupo['linhas'].values(), key=lambda x: x['codigo'] or ''):
                    item = linha.copy()
                    item['valor'] = self._money(item['valor'])
                    linhas.append(item)
                saida.append({
                    'codigo': grupo['codigo'],
                    'grupo': grupo['grupo'],
                    'valor': self._money(grupo['valor']),
                    'linhas': linhas,
                })
            return saida

        return Response({
            'periodo': {
                'data_ini': request.query_params.get('data_ini') or '',
                'data_fim': request.query_params.get('data_fim') or '',
            },
            'totais': {
                'receita_bruta': self._money(receita_bruta),
                'deducoes': self._money(deducoes),
                'receita_liquida': self._money(receita_liquida),
                'custos': self._money(custos),
                'lucro_bruto': self._money(lucro_bruto),
                'despesas_vendas': self._money(despesas_vendas),
                'despesas_administrativas': self._money(despesas_administrativas),
                'despesas_financeiras': self._money(despesas_financeiras),
                'tributos': self._money(tributos),
                'despesas': self._money(despesas),
                'outros': self._money(outros),
                'resultado': self._money(resultado),
                'movimentacoes': len(detalhes),
            },
            'grupos': serialize_grupos(),
            'detalhes': detalhes,
        })

    @action(detail=True, methods=['post'], url_path='cancelar')
    def cancelar(self, request, pk=None):
        obj = self.get_object()
        before = obj.status
        if obj.status != MovimentacaoFinanceira.STATUS_CANCELADA:
            obj.status = MovimentacaoFinanceira.STATUS_CANCELADA
            obj.save(update_fields=['status'])
            estornar_lancamento_contabil_movimentacao(obj, 'Movimentação financeira cancelada.')
            _audit('movimentacaofinanceira', obj.pk, {'status': [before, obj.status]}, request, action='cancelar')
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=['post'], url_path='conciliar')
    def conciliar(self, request, pk=None):
        data_conciliacao = request.data.get('data_conciliacao') or timezone.localdate().isoformat()
        try:
            valor_conciliado = Decimal(str(request.data.get('valor_conciliado', request.data.get('valor', ''))))
        except (TypeError, ValueError, InvalidOperation):
            return Response({'detail': 'Informe valor_conciliado numérico.'}, status=status.HTTP_400_BAD_REQUEST)
        if valor_conciliado <= 0:
            return Response({'detail': 'Informe valor_conciliado maior que zero.'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            obj = (
                MovimentacaoFinanceira.objects
                .select_for_update()
                .select_related('caixa', 'conta_bancaria')
                .get(pk=self.get_object().pk)
            )
            if obj.status == MovimentacaoFinanceira.STATUS_EFETIVA and obj.data_conciliacao:
                return Response({'detail': 'Movimentação já conciliada.'}, status=status.HTTP_400_BAD_REQUEST)
            if obj.status not in [MovimentacaoFinanceira.STATUS_PREVISTA, MovimentacaoFinanceira.STATUS_EFETIVA]:
                return Response({'detail': 'Apenas movimentações previstas ou efetivas não conciliadas podem ser conciliadas.'}, status=status.HTTP_400_BAD_REQUEST)
            destino = obj.conta_bancaria or obj.caixa
            if not destino:
                return Response({'detail': 'Movimentação sem caixa ou conta bancária vinculada.'}, status=status.HTTP_400_BAD_REQUEST)
            if obj.tipo not in [MovimentacaoFinanceira.TIPO_ENTRADA, MovimentacaoFinanceira.TIPO_SAIDA]:
                return Response({'detail': 'Tipo de movimentação inválido para conciliação.'}, status=status.HTTP_400_BAD_REQUEST)
            before = obj.status
            if obj.status == MovimentacaoFinanceira.STATUS_PREVISTA:
                if obj.tipo == MovimentacaoFinanceira.TIPO_ENTRADA:
                    destino.saldo_atual = Decimal(destino.saldo_atual or 0) + valor_conciliado
                else:
                    destino.saldo_atual = Decimal(destino.saldo_atual or 0) - valor_conciliado
                destino.save(update_fields=['saldo_atual'])
            obj.status = MovimentacaoFinanceira.STATUS_EFETIVA
            obj.data_conciliacao = data_conciliacao
            obj.valor_conciliado = valor_conciliado
            obj.save(update_fields=['status', 'data_conciliacao', 'valor_conciliado'])
            gerar_lancamento_contabil_movimentacao(obj)
            if obj.receber_item_id:
                obj.receber_item.status = ReceberItem.STATUS_BAIXADO
                obj.receber_item.data_baixa = data_conciliacao
                obj.receber_item.valor_baixa = valor_conciliado
                obj.receber_item.save(update_fields=['status', 'data_baixa', 'valor_baixa'])

        _audit('movimentacaofinanceira', obj.pk, {
            'status': [before, obj.status],
            'data_conciliacao': data_conciliacao,
            'valor_conciliado': str(valor_conciliado),
        }, request, action='conciliar')
        return Response(self.get_serializer(obj).data)

    @action(detail=False, methods=['get'], url_path='pendentes-conciliacao')
    def pendentes_conciliacao(self, request):
        data_movimento = request.query_params.get('data_movimento')
        forma_pagamento = request.query_params.get('forma_pagamento')
        conta = request.query_params.get('conta_bancaria')

        qs = (
            self.get_queryset()
            .filter(
                tipo=MovimentacaoFinanceira.TIPO_ENTRADA,
                status__in=[MovimentacaoFinanceira.STATUS_PREVISTA, MovimentacaoFinanceira.STATUS_EFETIVA],
                data_conciliacao__isnull=True,
                conta_bancaria__isnull=False,
            )
            .order_by('data_movimento', 'documento', 'Idmovimentacao')
        )
        if conta:
            qs = qs.filter(conta_bancaria_id=conta)
        if data_movimento:
            qs = qs.filter(data_movimento=data_movimento)
        if forma_pagamento:
            qs = qs.filter(FormaPagamento=forma_pagamento)
        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=False, methods=['post'], url_path='conciliar-lote')
    def conciliar_lote(self, request):
        ids = request.data.get('ids') or []
        data_conciliacao = request.data.get('data_conciliacao') or timezone.localdate().isoformat()
        valores = request.data.get('valores') or {}
        if not isinstance(ids, list) or not ids:
            return Response({'detail': 'Selecione ao menos uma movimentação para conciliar.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            ids = [int(item) for item in ids]
        except (TypeError, ValueError):
            return Response({'detail': 'Lista de movimentações inválida.'}, status=status.HTTP_400_BAD_REQUEST)

        conciliadas = []
        with transaction.atomic():
            qs = (
                self.get_queryset()
                .select_for_update()
                .select_related('caixa', 'conta_bancaria', 'receber_item')
                .filter(pk__in=ids)
            )
            movimentos = {mov.pk: mov for mov in qs}
            if len(movimentos) != len(set(ids)):
                return Response({'detail': 'Uma ou mais movimentações não foram encontradas.'}, status=status.HTTP_400_BAD_REQUEST)

            for mov_id in ids:
                obj = movimentos[mov_id]
                if obj.status == MovimentacaoFinanceira.STATUS_EFETIVA and obj.data_conciliacao:
                    return Response({'detail': f'Movimentação {obj.pk} já está conciliada.'}, status=status.HTTP_400_BAD_REQUEST)
                if obj.status not in [MovimentacaoFinanceira.STATUS_PREVISTA, MovimentacaoFinanceira.STATUS_EFETIVA]:
                    return Response({'detail': f'Movimentação {obj.pk} não pode ser conciliada.'}, status=status.HTTP_400_BAD_REQUEST)
                if obj.tipo != MovimentacaoFinanceira.TIPO_ENTRADA:
                    return Response({'detail': f'Movimentação {obj.pk} não é uma entrada.'}, status=status.HTTP_400_BAD_REQUEST)
                destino = obj.conta_bancaria or obj.caixa
                if not destino:
                    return Response({'detail': f'Movimentação {obj.pk} sem conta ou caixa vinculado.'}, status=status.HTTP_400_BAD_REQUEST)
                try:
                    valor = Decimal(str(valores.get(str(obj.pk), valores.get(obj.pk, obj.valor))))
                except (TypeError, ValueError, InvalidOperation):
                    return Response({'detail': f'Valor conciliado inválido na movimentação {obj.pk}.'}, status=status.HTTP_400_BAD_REQUEST)
                if valor <= 0:
                    return Response({'detail': f'Valor conciliado deve ser maior que zero na movimentação {obj.pk}.'}, status=status.HTTP_400_BAD_REQUEST)

                before = obj.status
                if obj.status == MovimentacaoFinanceira.STATUS_PREVISTA:
                    destino.saldo_atual = Decimal(destino.saldo_atual or 0) + valor
                    destino.save(update_fields=['saldo_atual'])
                obj.status = MovimentacaoFinanceira.STATUS_EFETIVA
                obj.data_conciliacao = data_conciliacao
                obj.valor_conciliado = valor
                obj.save(update_fields=['status', 'data_conciliacao', 'valor_conciliado'])
                gerar_lancamento_contabil_movimentacao(obj)
                if obj.receber_item_id:
                    obj.receber_item.status = ReceberItem.STATUS_BAIXADO
                    obj.receber_item.data_baixa = data_conciliacao
                    obj.receber_item.valor_baixa = valor
                    obj.receber_item.save(update_fields=['status', 'data_baixa', 'valor_baixa'])

                _audit('movimentacaofinanceira', obj.pk, {
                    'status': [before, obj.status],
                    'data_conciliacao': data_conciliacao,
                    'valor_conciliado': str(valor),
                }, request, action='conciliar_lote')
                conciliadas.append(obj)

        return Response({
            'quantidade': len(conciliadas),
            'total': self._money(sum((Decimal(mov.valor_conciliado or 0) for mov in conciliadas), Decimal('0.00'))),
            'movimentacoes': self.get_serializer(conciliadas, many=True).data,
        })

    @action(detail=True, methods=['post'], url_path='desfazer-conciliacao')
    def desfazer_conciliacao(self, request, pk=None):
        with transaction.atomic():
            obj = (
                MovimentacaoFinanceira.objects
                .select_for_update()
                .select_related('caixa', 'conta_bancaria')
                .get(pk=self.get_object().pk)
            )
            conciliacao_antiga_cartao = (
                obj.status == MovimentacaoFinanceira.STATUS_EFETIVA
                and obj.origem == MovimentacaoFinanceira.ORIGEM_CARTAO
                and obj.conta_bancaria_id
                and not obj.data_conciliacao
            )
            if obj.status != MovimentacaoFinanceira.STATUS_EFETIVA or (not obj.data_conciliacao and not conciliacao_antiga_cartao):
                return Response({'detail': 'Apenas movimentações conciliadas podem ser desfeitas.'}, status=status.HTTP_400_BAD_REQUEST)
            destino = obj.conta_bancaria or obj.caixa
            if not destino:
                return Response({'detail': 'Movimentação sem caixa ou conta bancária vinculada.'}, status=status.HTTP_400_BAD_REQUEST)
            valor = Decimal(obj.valor_conciliado or obj.valor or 0)
            if obj.tipo == MovimentacaoFinanceira.TIPO_ENTRADA:
                destino.saldo_atual = Decimal(destino.saldo_atual or 0) - valor
            elif obj.tipo == MovimentacaoFinanceira.TIPO_SAIDA:
                destino.saldo_atual = Decimal(destino.saldo_atual or 0) + valor
            else:
                return Response({'detail': 'Tipo de movimentação inválido para desfazer conciliação.'}, status=status.HTTP_400_BAD_REQUEST)
            destino.save(update_fields=['saldo_atual'])

            before = {
                'status': obj.status,
                'data_conciliacao': str(obj.data_conciliacao),
                'valor_conciliado': str(obj.valor_conciliado),
            }
            obj.status = MovimentacaoFinanceira.STATUS_PREVISTA
            obj.data_conciliacao = None
            obj.valor_conciliado = None
            obj.save(update_fields=['status', 'data_conciliacao', 'valor_conciliado'])
            estornar_lancamento_contabil_movimentacao(obj, 'Conciliação desfeita.')
            if obj.receber_item_id:
                obj.receber_item.status = ReceberItem.STATUS_EFETIVO
                obj.receber_item.data_baixa = None
                obj.receber_item.valor_baixa = None
                obj.receber_item.save(update_fields=['status', 'data_baixa', 'valor_baixa'])

        _audit('movimentacaofinanceira', obj.pk, {
            'before': before,
            'after': {'status': obj.status, 'data_conciliacao': None, 'valor_conciliado': None},
            'valor_estornado': str(valor),
        }, request, action='desfazer_conciliacao')
        return Response(self.get_serializer(obj).data)


class LancamentoContabilViewSet(BaseViewSet):
    read_roles = ["Admin", "Diretor", "Gerente", "AssistenteReceber", "AssistentePagar"]
    write_roles = ["Admin", "Diretor"]
    queryset = LancamentoContabil.objects.select_related(
        'empresa', 'idloja', 'movimentacao', 'natureza', 'conta_debito', 'conta_credito'
    ).all()
    serializer_class = LancamentoContabilSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        loja = self.request.query_params.get('loja')
        status_q = self.request.query_params.get('status')
        origem = self.request.query_params.get('origem')
        data_ini = self.request.query_params.get('data_ini')
        data_fim = self.request.query_params.get('data_fim')
        if loja:
            qs = qs.filter(idloja_id=loja)
        if status_q:
            qs = qs.filter(status=status_q)
        if origem:
            qs = qs.filter(origem=origem)
        if data_ini:
            qs = qs.filter(data_lancamento__gte=data_ini)
        if data_fim:
            qs = qs.filter(data_lancamento__lte=data_fim)
        return qs

    @action(detail=False, methods=['get'], url_path='pendentes')
    def pendentes(self, request):
        qs = self.get_queryset().filter(status=LancamentoContabil.STATUS_PENDENTE)
        return Response(self.get_serializer(qs, many=True).data)


class AntecipacaoRecebivelViewSet(BaseViewSet):
    read_roles = ["Admin", "Diretor", "Gerente", "AssistenteReceber"]
    write_roles = ["Admin", "Diretor", "Gerente", "AssistenteReceber"]
    queryset = AntecipacaoRecebivel.objects.select_related('idloja', 'conta_bancaria').prefetch_related('itens').all()
    serializer_class = AntecipacaoRecebivelSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        loja = self.request.query_params.get('loja')
        conta = self.request.query_params.get('conta_bancaria')
        data_ini = self.request.query_params.get('data_ini')
        data_fim = self.request.query_params.get('data_fim')
        if loja:
            qs = qs.filter(idloja_id=loja)
        if conta:
            qs = qs.filter(conta_bancaria_id=conta)
        if data_ini:
            qs = qs.filter(data_antecipacao__gte=data_ini)
        if data_fim:
            qs = qs.filter(data_antecipacao__lte=data_fim)
        return qs

    def _money(self, value):
        return Decimal(value or 0).quantize(Decimal('0.01'))

    @action(detail=False, methods=['get'], url_path='recebiveis')
    def recebiveis(self, request):
        qs = (
            MovimentacaoFinanceira.objects
            .select_related('idloja', 'conta_bancaria', 'receber_item')
            .filter(
                tipo=MovimentacaoFinanceira.TIPO_ENTRADA,
                status=MovimentacaoFinanceira.STATUS_PREVISTA,
                origem=MovimentacaoFinanceira.ORIGEM_CARTAO,
                conta_bancaria__isnull=False,
                receber_item__isnull=False,
            )
        )
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not request.user.is_superuser:
            qs = qs.none()

        loja = request.query_params.get('loja')
        conta = request.query_params.get('conta_bancaria')
        forma = request.query_params.get('forma_pagamento')
        data_ini = request.query_params.get('data_ini')
        data_fim = request.query_params.get('data_fim')
        if loja:
            qs = qs.filter(idloja_id=loja)
        if conta:
            qs = qs.filter(conta_bancaria_id=conta)
        if forma:
            qs = qs.filter(FormaPagamento=forma)
        if data_ini:
            qs = qs.filter(data_movimento__gte=data_ini)
        if data_fim:
            qs = qs.filter(data_movimento__lte=data_fim)

        return Response(MovimentacaoFinanceiraSerializer(qs.order_by('data_movimento', 'documento'), many=True).data)

    @action(detail=False, methods=['post'], url_path='executar')
    def executar(self, request):
        ids = request.data.get('movimentacoes') or request.data.get('ids') or []
        data_antecipacao = request.data.get('data_antecipacao') or timezone.localdate().isoformat()
        documento = (request.data.get('documento') or '').strip()
        observacao = (request.data.get('observacao') or '').strip()
        try:
            taxa_percentual = Decimal(str(request.data.get('taxa_percentual', '0')))
        except (TypeError, ValueError, InvalidOperation):
            return Response({'detail': 'Informe uma taxa percentual válida.'}, status=status.HTTP_400_BAD_REQUEST)
        if taxa_percentual < 0:
            return Response({'detail': 'A taxa percentual não pode ser negativa.'}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(ids, list) or not ids:
            return Response({'detail': 'Selecione ao menos um recebível para antecipar.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            ids = [int(item) for item in ids]
        except (TypeError, ValueError):
            return Response({'detail': 'Lista de recebíveis inválida.'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            qs = (
                MovimentacaoFinanceira.objects
                .select_for_update()
                .select_related('idloja', 'conta_bancaria', 'receber_item', 'Idnatureza', 'empresa')
                .filter(pk__in=ids)
            )
            empresa_id = self._empresa_id_usuario()
            if empresa_id:
                qs = qs.filter(empresa_id=empresa_id)
            elif not request.user.is_superuser:
                qs = qs.none()
            movimentos = {mov.pk: mov for mov in qs}
            if len(movimentos) != len(set(ids)):
                return Response({'detail': 'Um ou mais recebíveis não foram encontrados.'}, status=status.HTTP_400_BAD_REQUEST)

            primeiro = movimentos[ids[0]]
            conta = primeiro.conta_bancaria
            loja = primeiro.idloja
            empresa = primeiro.empresa
            for mov_id in ids:
                mov = movimentos[mov_id]
                if mov.status != MovimentacaoFinanceira.STATUS_PREVISTA or mov.origem != MovimentacaoFinanceira.ORIGEM_CARTAO:
                    return Response({'detail': f'Recebível {mov.pk} não está disponível para antecipação.'}, status=status.HTTP_400_BAD_REQUEST)
                if mov.conta_bancaria_id != conta.pk:
                    return Response({'detail': 'Antecipe apenas recebíveis da mesma conta bancária.'}, status=status.HTTP_400_BAD_REQUEST)
                if mov.idloja_id != loja.pk:
                    return Response({'detail': 'Antecipe apenas recebíveis da mesma loja.'}, status=status.HTTP_400_BAD_REQUEST)

            valor_bruto = sum((Decimal(movimentos[mov_id].valor or 0) for mov_id in ids), Decimal('0.00'))
            taxa_valor = self._money(valor_bruto * taxa_percentual / Decimal('100'))
            valor_liquido = self._money(valor_bruto - taxa_valor)
            if valor_liquido <= 0:
                return Response({'detail': 'O valor líquido da antecipação deve ser maior que zero.'}, status=status.HTTP_400_BAD_REQUEST)

            if not documento:
                agora = timezone.localtime()
                documento = f"ANT-{agora:%Y%m%d%H%M%S}-{request.user.pk or '0'}"

            antecipacao = AntecipacaoRecebivel.objects.create(
                empresa=empresa,
                idloja=loja,
                conta_bancaria=conta,
                documento=documento[:50],
                data_antecipacao=data_antecipacao,
                taxa_percentual=taxa_percentual,
                valor_bruto=self._money(valor_bruto),
                taxa_valor=taxa_valor,
                valor_liquido=valor_liquido,
                observacao=observacao[:255],
                criado_por=request.user if request.user.is_authenticated else None,
            )

            taxa_natureza = _natureza_taxa_antecipacao(empresa)
            conta.saldo_atual = Decimal(conta.saldo_atual or 0) + valor_bruto - taxa_valor
            conta.save(update_fields=['saldo_atual'])

            mov_entrada_antecipacao = MovimentacaoFinanceira.objects.create(
                empresa=empresa,
                idloja=loja,
                data_movimento=data_antecipacao,
                tipo=MovimentacaoFinanceira.TIPO_ENTRADA,
                status=MovimentacaoFinanceira.STATUS_EFETIVA,
                origem=MovimentacaoFinanceira.ORIGEM_ANTECIPACAO,
                valor=self._money(valor_bruto),
                historico=f"Antecipação de recebíveis {documento}"[:255],
                documento=documento[:50],
                Idnatureza=primeiro.Idnatureza,
                FormaPagamento='ANTECIPACAO',
                conta_bancaria=conta,
            )
            gerar_lancamento_contabil_movimentacao(mov_entrada_antecipacao)
            if taxa_valor > 0:
                mov_taxa_antecipacao = MovimentacaoFinanceira.objects.create(
                    empresa=empresa,
                    idloja=loja,
                    data_movimento=data_antecipacao,
                    tipo=MovimentacaoFinanceira.TIPO_SAIDA,
                    status=MovimentacaoFinanceira.STATUS_EFETIVA,
                    origem=MovimentacaoFinanceira.ORIGEM_ANTECIPACAO,
                    valor=taxa_valor,
                    historico=f"Taxa de antecipação {documento}"[:255],
                    documento=documento[:50],
                    Idnatureza=taxa_natureza,
                    FormaPagamento='ANTECIPACAO',
                    conta_bancaria=conta,
                )
                gerar_lancamento_contabil_movimentacao(mov_taxa_antecipacao)

            for mov_id in ids:
                mov = movimentos[mov_id]
                item_bruto = self._money(mov.valor)
                item_taxa = self._money(item_bruto * taxa_percentual / Decimal('100'))
                item_liquido = self._money(item_bruto - item_taxa)
                AntecipacaoRecebivelItem.objects.create(
                    antecipacao=antecipacao,
                    movimentacao=mov,
                    receber_item=mov.receber_item,
                    valor_bruto=item_bruto,
                    taxa_valor=item_taxa,
                    valor_liquido=item_liquido,
                )
                mov.status = MovimentacaoFinanceira.STATUS_ANTECIPADA
                mov.save(update_fields=['status'])
                mov.receber_item.status = ReceberItem.STATUS_ANTECIPADO
                mov.receber_item.data_baixa = data_antecipacao
                mov.receber_item.valor_baixa = item_bruto
                mov.receber_item.save(update_fields=['status', 'data_baixa', 'valor_baixa'])

        _audit('antecipacaorecebivel', antecipacao.pk, {
            'documento': antecipacao.documento,
            'movimentacoes': ids,
            'valor_bruto': str(antecipacao.valor_bruto),
            'taxa_valor': str(antecipacao.taxa_valor),
            'valor_liquido': str(antecipacao.valor_liquido),
        }, request, action='executar')
        return Response(self.get_serializer(antecipacao).data, status=status.HTTP_201_CREATED)


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
        juros = self._decimal_request(request, 'juros')
        multa = self._decimal_request(request, 'multa')
        tarifa = self._decimal_request(request, 'tarifa')
        desconto = self._decimal_request(request, 'desconto')
        try:
            valor_baixa = Decimal(str(valor_baixa))
        except (TypeError, ValueError, InvalidOperation):
            return Response({'detail': 'Informe valor_baixa numérico.'}, status=status.HTTP_400_BAD_REQUEST)
        if valor_baixa <= 0:
            return Response({'detail': 'Informe valor_baixa maior que zero.'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            obj = (
                PagarItem.objects
                .select_for_update()
                .select_related('Idpagar', 'Idpagar__idloja', 'Idpagar__idfornecedor', 'Idnatureza')
                .get(pk=obj.pk)
            )
            before = {'status': obj.status, 'valor_baixa': obj.valor_baixa, 'data_baixa': obj.data_baixa}
            obj.valor_baixa = valor_baixa
            obj.data_baixa = data_baixa
            obj.juros = juros
            obj.multa = multa
            obj.tarifa = tarifa
            obj.desconto = desconto
            obj.status = PagarItem.STATUS_BAIXADO
            obj.save(update_fields=['valor_baixa', 'data_baixa', 'juros', 'multa', 'tarifa', 'desconto', 'status'])
            movimento = self._criar_movimento_baixa(obj, valor_baixa, data_baixa, juros, multa, tarifa, desconto)

        _audit('pagaritem', obj.pk, {'before': before, 'after': {
            'status': obj.status, 'valor_baixa': obj.valor_baixa, 'data_baixa': str(obj.data_baixa),
            'movimentacao': movimento.pk if movimento else None,
        }}, request, action='baixar')
        return Response(self.get_serializer(obj).data)

    def _decimal_request(self, request, campo):
        try:
            valor = Decimal(str(request.data.get(campo, 0) or 0))
        except (TypeError, ValueError, InvalidOperation):
            raise ValidationError({campo: 'Informe um valor numérico.'})
        if valor < 0:
            raise ValidationError({campo: 'O valor não pode ser negativo.'})
        return valor

    def _documento_parcela(self, item):
        titulo = str(item.Idpagar.Titulo or item.Idpagar_id)
        sufixo = f"-{item.parcela_n}"
        return titulo if titulo.endswith(sufixo) else f"{titulo}{sufixo}"

    def _criar_movimento_baixa(self, item, valor_baixa, data_baixa, juros=0, multa=0, tarifa=0, desconto=0):
        existente = (
            MovimentacaoFinanceira.objects
            .filter(pagar_item=item, status=MovimentacaoFinanceira.STATUS_EFETIVA)
            .first()
        )
        if existente:
            return existente

        titulo = item.Idpagar
        caixa = (
            Caixa.objects
            .select_for_update()
            .filter(
                empresa=titulo.empresa,
                idloja=titulo.idloja,
                ativo=True,
            )
            .order_by('Idcaixa')
            .first()
        )
        if not caixa:
            raise ValidationError({'caixa': 'Nenhum caixa ativo encontrado para a loja do título.'})

        caixa.saldo_atual = Decimal(caixa.saldo_atual or 0) - Decimal(valor_baixa)
        caixa.save(update_fields=['saldo_atual'])

        documento = self._documento_parcela(item)
        fornecedor = getattr(titulo.idfornecedor, 'nome_fornecedor', '') or getattr(titulo.idfornecedor, 'apelido', '')
        config = getattr(titulo.empresa, 'config_financeira', None)
        principal = Decimal(valor_baixa or 0) - Decimal(juros or 0) - Decimal(multa or 0) - Decimal(tarifa or 0) + Decimal(desconto or 0)
        if principal < 0:
            raise ValidationError({'valor_baixa': 'O total dos acréscimos não pode superar o valor pago.'})

        movimento = self._criar_movimento_componentizado(
            titulo=titulo,
            item=item,
            data_baixa=data_baixa,
            tipo=MovimentacaoFinanceira.TIPO_SAIDA,
            valor=principal,
            historico=f"Baixa contas a pagar {documento}" + (f" - {fornecedor}" if fornecedor else ""),
            documento=documento,
            natureza=item.Idnatureza or titulo.Idnatureza,
            caixa=caixa,
        )
        self._criar_movimento_componentizado(titulo, item, data_baixa, MovimentacaoFinanceira.TIPO_SAIDA, juros, f"Juros pagos {documento}", f"{documento}-JUR", getattr(config, 'natureza_juros_pagos', None), caixa)
        self._criar_movimento_componentizado(titulo, item, data_baixa, MovimentacaoFinanceira.TIPO_SAIDA, multa, f"Multa paga {documento}", f"{documento}-MUL", getattr(config, 'natureza_multas_pagas', None), caixa)
        self._criar_movimento_componentizado(titulo, item, data_baixa, MovimentacaoFinanceira.TIPO_SAIDA, tarifa, f"Tarifa paga {documento}", f"{documento}-TAR", getattr(config, 'natureza_tarifas_pagas', None), caixa)
        self._criar_movimento_componentizado(titulo, item, data_baixa, MovimentacaoFinanceira.TIPO_ENTRADA, desconto, f"Desconto obtido {documento}", f"{documento}-DSC", getattr(config, 'natureza_descontos_obtidos', None), caixa)
        return movimento

    def _criar_movimento_componentizado(self, titulo, item, data_baixa, tipo, valor, historico, documento, natureza, caixa):
        valor = Decimal(valor or 0)
        if valor <= 0:
            return None
        movimento = MovimentacaoFinanceira.objects.create(
            empresa=titulo.empresa,
            idloja=titulo.idloja,
            data_movimento=data_baixa,
            tipo=tipo,
            status=MovimentacaoFinanceira.STATUS_EFETIVA,
            origem=MovimentacaoFinanceira.ORIGEM_PAGAR,
            valor=valor,
            historico=historico,
            documento=documento,
            Idnatureza=natureza or item.Idnatureza or titulo.Idnatureza,
            FormaPagamento=item.FormaPagamento or titulo.FormaPagamento,
            caixa=caixa,
            pagar_item=item,
        )
        gerar_lancamento_contabil_movimentacao(movimento)
        return movimento

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
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
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
        juros = self._decimal_request(request, 'juros')
        multa = self._decimal_request(request, 'multa')
        desconto = self._decimal_request(request, 'desconto')
        try:
            valor_baixa = Decimal(str(valor_baixa))
        except (TypeError, ValueError, InvalidOperation):
            return Response({'detail': 'Informe valor_baixa numérico.'}, status=status.HTTP_400_BAD_REQUEST)
        if valor_baixa <= 0:
            return Response({'detail': 'Informe valor_baixa maior que zero.'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            obj = (
                ReceberItem.objects
                .select_for_update()
                .select_related('Idreceber', 'Idreceber__idloja', 'Idreceber__idcliente', 'Idnatureza')
                .get(pk=obj.pk)
            )
            before = {'status': obj.status, 'valor_baixa': obj.valor_baixa, 'data_baixa': obj.data_baixa}
            obj.valor_baixa = valor_baixa
            obj.data_baixa = data_baixa
            obj.juros = juros
            obj.multa = multa
            obj.desconto = desconto
            obj.status = ReceberItem.STATUS_BAIXADO
            obj.save(update_fields=['valor_baixa', 'data_baixa', 'juros', 'multa', 'desconto', 'status'])
            movimento = self._criar_movimento_baixa(obj, valor_baixa, data_baixa, juros, multa, desconto)

        _audit('receberitem', obj.pk, {'before': before, 'after': {
            'status': obj.status, 'valor_baixa': obj.valor_baixa, 'data_baixa': str(obj.data_baixa),
            'movimentacao': movimento.pk if movimento else None,
        }}, request, action='baixar')
        return Response(self.get_serializer(obj).data)

    def _decimal_request(self, request, campo):
        try:
            valor = Decimal(str(request.data.get(campo, 0) or 0))
        except (TypeError, ValueError, InvalidOperation):
            raise ValidationError({campo: 'Informe um valor numérico.'})
        if valor < 0:
            raise ValidationError({campo: 'O valor não pode ser negativo.'})
        return valor

    def _documento_parcela(self, item):
        titulo = str(item.Idreceber.Titulo or item.Idreceber_id)
        sufixo = f"-{item.parcela_n}"
        return titulo if titulo.endswith(sufixo) else f"{titulo}{sufixo}"

    def _criar_movimento_baixa(self, item, valor_baixa, data_baixa, juros=0, multa=0, desconto=0):
        existente = (
            MovimentacaoFinanceira.objects
            .filter(receber_item=item, status=MovimentacaoFinanceira.STATUS_EFETIVA)
            .first()
        )
        if existente:
            return existente

        titulo = item.Idreceber
        caixa = (
            Caixa.objects
            .select_for_update()
            .filter(
                empresa=titulo.empresa,
                idloja=titulo.idloja,
                ativo=True,
            )
            .order_by('Idcaixa')
            .first()
        )
        if not caixa:
            raise ValidationError({'caixa': 'Nenhum caixa ativo encontrado para a loja do título.'})

        caixa.saldo_atual = Decimal(caixa.saldo_atual or 0) + Decimal(valor_baixa)
        caixa.save(update_fields=['saldo_atual'])

        documento = self._documento_parcela(item)
        cliente = (
            getattr(titulo.idcliente, 'nome_cliente', '')
            or getattr(titulo.idcliente, 'nome', '')
            or getattr(titulo.idcliente, 'apelido', '')
        )
        config = getattr(titulo.empresa, 'config_financeira', None)
        principal = Decimal(valor_baixa or 0) - Decimal(juros or 0) - Decimal(multa or 0) + Decimal(desconto or 0)
        if principal < 0:
            raise ValidationError({'valor_baixa': 'O total dos acréscimos não pode superar o valor recebido.'})

        movimento = self._criar_movimento_componentizado(
            titulo=titulo,
            item=item,
            data_baixa=data_baixa,
            tipo=MovimentacaoFinanceira.TIPO_ENTRADA,
            valor=principal,
            historico=f"Baixa contas a receber {documento}" + (f" - {cliente}" if cliente else ""),
            documento=documento,
            natureza=item.Idnatureza or titulo.Idnatureza,
            caixa=caixa,
        )
        self._criar_movimento_componentizado(titulo, item, data_baixa, MovimentacaoFinanceira.TIPO_ENTRADA, juros, f"Juros recebidos {documento}", f"{documento}-JUR", getattr(config, 'natureza_juros_recebidos', None), caixa)
        self._criar_movimento_componentizado(titulo, item, data_baixa, MovimentacaoFinanceira.TIPO_ENTRADA, multa, f"Multa recebida {documento}", f"{documento}-MUL", getattr(config, 'natureza_multas_recebidas', None), caixa)
        self._criar_movimento_componentizado(titulo, item, data_baixa, MovimentacaoFinanceira.TIPO_SAIDA, desconto, f"Desconto concedido {documento}", f"{documento}-DSC", getattr(config, 'natureza_descontos_concedidos', None), caixa)
        return movimento

    def _criar_movimento_componentizado(self, titulo, item, data_baixa, tipo, valor, historico, documento, natureza, caixa):
        valor = Decimal(valor or 0)
        if valor <= 0:
            return None
        movimento = MovimentacaoFinanceira.objects.create(
            empresa=titulo.empresa,
            idloja=titulo.idloja,
            data_movimento=data_baixa,
            tipo=tipo,
            status=MovimentacaoFinanceira.STATUS_EFETIVA,
            origem=MovimentacaoFinanceira.ORIGEM_RECEBER,
            valor=valor,
            historico=historico,
            documento=documento,
            Idnatureza=natureza or item.Idnatureza or titulo.Idnatureza,
            FormaPagamento=item.FormaPagamento or titulo.FormaPagamento,
            caixa=caixa,
            receber_item=item,
        )
        gerar_lancamento_contabil_movimentacao(movimento)
        return movimento

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
