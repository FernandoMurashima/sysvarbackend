from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from decimal import Decimal, ROUND_HALF_UP

from auditoria.models import AuditAction, AuditCategory
from auditoria.services import AuditService

from .permissions import CanToggleProductFlags
from accounts.permissions import HasEmpresaModulo, HasModuleRole
from accounts.services.effective_access import EffectiveAccessService
from cadastros.models import Loja
from fiscal.models import Cfop, NotaFiscalSaida, NotaFiscalSaidaItem

from .models import (
    ConfigEan, Ncm, Grade, Tamanho, Cor, Material, Colecao, Unidade,
    Grupo, Subgrupo, Tabelapreco, Codigos, Produto, ProdutoDetalhe,
    ProdutoVendaHistorico, ProdutoUsoConsumoHistorico, ProdutoInsumoHistorico, ProdutoImagem,
    TabelaprecoProduto, FichaTecnica, FichaTecnicaItem, OrdemProducao, OrdemProducaoItem, OrdemProducaoGrade,
    Promocao, Pack, PackItem, Estoque, EstoqueMovimentacao, ProdutoUsoConsumoEstoque, ProdutoUsoConsumoMovimentacao,
    InventarioEstoque, InventarioEstoqueItem
)
from .serializers import (
    ConfigEanSerializer, NcmSerializer, GradeSerializer, TamanhoSerializer, CorSerializer,
    MaterialSerializer, ColecaoSerializer, UnidadeSerializer, GrupoSerializer, SubgrupoSerializer,
    TabelaprecoSerializer, CodigosSerializer, ProdutoSerializer, ProdutoDetalheSerializer,
    ProdutoVendaHistoricoSerializer, ProdutoImagemSerializer,
    TabelaprecoProdutoSerializer, FichaTecnicaSerializer, FichaTecnicaItemSerializer,
    OrdemProducaoSerializer, OrdemProducaoItemSerializer, PromocaoSerializer, PackSerializer, PackItemSerializer, EstoqueSerializer,
    EstoqueMovimentacaoSerializer, ProdutoUsoConsumoHistoricoSerializer, ProdutoInsumoHistoricoSerializer, ProdutoUsoConsumoEstoqueSerializer, ProdutoUsoConsumoMovimentacaoSerializer, InventarioEstoqueSerializer, InventarioEstoqueItemSerializer
)


def _audit(model_name: str, obj_id: str, changes: dict, request, action: str):
    AuditService.success(
        AuditAction.OBJECT_UPDATED,
        category=AuditCategory.PRODUCT,
        request=request,
        user=getattr(request, "user", None),
        app_label="produto",
        model=model_name,
        object_id=obj_id,
        metadata={"legacy_action": action, "changes": changes},
    )


def _audit_produto_update(model_name: str, obj_id: str, anteriores: dict, novos: dict, request, action: str):
    AuditService.success(
        AuditAction.OBJECT_UPDATED,
        category=AuditCategory.PRODUCT,
        request=request,
        user=getattr(request, "user", None),
        app_label="produto",
        model=model_name,
        object_id=obj_id,
        before=anteriores,
        after=novos,
        changed_fields=list(novos.keys()),
        metadata={"legacy_action": action},
    )


def _money(value):
    return Decimal(value or 0).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _q4(value):
    return Decimal(value or 0).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)


class BaseViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModuleRole]
    required_module = "produtos"
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa", "Vendedor", "Auxiliar", "Assistente", "Regular"]
    write_roles = ["Admin", "Diretor", "Gerente"]
    aux_filter_fields = {
        'Grupo': {'search': ['Codigo', 'CodigoRef', 'Descricao'], 'codigo': 'Codigo__icontains', 'Codigo': 'Codigo__icontains', 'CodigoRef': 'CodigoRef__icontains', 'descricao': 'Descricao__icontains'},
        'Subgrupo': {'search': ['Descricao'], 'Idgrupo': 'Idgrupo_id', 'grupo': 'Idgrupo_id', 'descricao': 'Descricao__icontains'},
        'Grade': {'search': ['Descricao'], 'descricao': 'Descricao__icontains', 'Status': 'Status', 'status': 'Status'},
        'Tamanho': {'search': ['Tamanho', 'Descricao'], 'idgrade': 'idgrade_id', 'grade': 'idgrade_id', 'Tamanho': 'Tamanho__icontains', 'status': 'Status', 'Status': 'Status'},
        'Colecao': {'search': ['Codigo', 'Descricao', 'Estacao'], 'codigo': 'Codigo__icontains', 'Codigo': 'Codigo__icontains', 'estacao': 'Estacao', 'Estacao': 'Estacao', 'status': 'Status', 'Status': 'Status'},
        'Cor': {'search': ['Codigo', 'Descricao', 'Cor'], 'codigo': 'Codigo__icontains', 'Codigo': 'Codigo__icontains', 'descricao': 'Descricao__icontains', 'cor': 'Cor__icontains', 'status': 'Status', 'Status': 'Status'},
        'Unidade': {'search': ['Codigo', 'Descricao'], 'codigo': 'Codigo__icontains', 'Codigo': 'Codigo__icontains', 'descricao': 'Descricao__icontains', 'permite_decimal': 'permite_decimal'},
        'Material': {'search': ['Codigo', 'Descricao'], 'codigo': 'Codigo__icontains', 'Codigo': 'Codigo__icontains', 'descricao': 'Descricao__icontains', 'status': 'Status', 'Status': 'Status'},
        'Pack': {'search': ['nome'], 'nome': 'nome__icontains', 'grade': 'grade_id', 'ativo': 'ativo'},
        'PackItem': {'pack': 'pack_id', 'tamanho': 'tamanho_id'},
    }
    aux_audit_models = {'Grupo', 'Subgrupo', 'Grade', 'Tamanho', 'Colecao', 'Cor', 'Unidade', 'Material', 'Pack', 'PackItem'}

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        empresa = self.request.query_params.get("empresa")
        if user.is_superuser:
            if empresa and self._model_has_field(qs.model, "empresa"):
                return self._apply_aux_filters(qs.filter(empresa_id=empresa))
            return self._apply_aux_filters(qs)
        empresa_id = getattr(user, "empresa_id", None)
        if empresa_id and self._model_has_field(qs.model, "empresa"):
            return self._apply_aux_filters(qs.filter(empresa_id=empresa_id))
        if self._model_has_field(qs.model, "empresa"):
            return qs.none()
        return self._apply_aux_filters(qs)

    def perform_create(self, serializer):
        self._save_with_empresa_scope(serializer)
        if serializer.Meta.model.__name__ in self.aux_audit_models:
            _audit(serializer.Meta.model.__name__, serializer.instance.pk, {'created': True}, self.request, 'create')

    def perform_update(self, serializer):
        before = {field: getattr(serializer.instance, field, None) for field in serializer.validated_data.keys()}
        self._save_with_empresa_scope(serializer)
        after = {field: getattr(serializer.instance, field, None) for field in serializer.validated_data.keys()}
        if serializer.Meta.model.__name__ in self.aux_audit_models:
            _audit(serializer.Meta.model.__name__, serializer.instance.pk, {'before': before, 'after': after}, self.request, 'update')

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.__class__.__name__ not in self.aux_audit_models:
            return super().destroy(request, *args, **kwargs)
        bloqueios = []
        for rel in instance._meta.related_objects:
            accessor = rel.get_accessor_name()
            manager = getattr(instance, accessor, None)
            if manager is not None and hasattr(manager, 'exists') and manager.exists():
                bloqueios.append(rel.related_model._meta.verbose_name_plural or rel.related_model.__name__)
        if bloqueios:
            return Response(
                {'detail': 'Cadastro possui vínculos e não pode ser excluído. Inative o registro quando houver lifecycle.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        pk = instance.pk
        response = super().destroy(request, *args, **kwargs)
        _audit(instance.__class__.__name__, pk, {'deleted': True}, request, 'delete')
        return response

    def _save_with_empresa_scope(self, serializer):
        model = serializer.Meta.model
        user = self.request.user
        if self._model_has_field(model, "empresa") and user.is_superuser:
            if not serializer.validated_data.get("empresa"):
                raise ValidationError({"empresa": "Informe a empresa do cadastro."})
            serializer.save()
            return
        if self._model_has_field(model, "empresa") and not getattr(user, "empresa_id", None) and not user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        if self._model_has_field(model, "empresa") and getattr(user, "empresa_id", None):
            empresa = serializer.validated_data.get("empresa")
            if empresa and empresa.id != user.empresa_id:
                raise ValidationError({"empresa": "O cadastro pertence a outra empresa."})
            serializer.save(empresa=user.empresa)
            return
        serializer.save()

    def _empresa_id_usuario(self):
        user = self.request.user
        if user.is_superuser:
            return self.request.query_params.get("empresa")
        return getattr(user, "empresa_id", None)

    def _model_has_field(self, model, field_name):
        try:
            model._meta.get_field(field_name)
            return True
        except Exception:
            return False

    def _apply_aux_filters(self, qs):
        mapping = self.aux_filter_fields.get(qs.model.__name__)
        if not mapping:
            return qs
        params = self.request.query_params
        search = (params.get('search') or '').strip()
        if search and mapping.get('search'):
            query = Q()
            for field in mapping['search']:
                query |= Q(**{f'{field}__icontains': search})
            qs = qs.filter(query)
        for param, lookup in mapping.items():
            if param == 'search':
                continue
            value = params.get(param)
            if value in (None, ''):
                continue
            if lookup in ('Status',) and isinstance(value, str):
                value = value.upper()
            if lookup == 'ativo':
                if str(value).lower() in ('true', '1'):
                    value = True
                elif str(value).lower() in ('false', '0'):
                    value = False
                else:
                    continue
            if lookup == 'permite_decimal':
                if str(value).lower() in ('true', '1'):
                    value = True
                elif str(value).lower() in ('false', '0'):
                    value = False
                else:
                    continue
            qs = qs.filter(**{lookup: value})
        return qs


class ConfigEanViewSet(BaseViewSet):
    queryset = ConfigEan.objects.all()
    serializer_class = ConfigEanSerializer


class NcmViewSet(BaseViewSet):
    queryset = Ncm.objects.all()
    serializer_class = NcmSerializer


class GradeViewSet(BaseViewSet):
    queryset = Grade.objects.all().order_by('Descricao')
    serializer_class = GradeSerializer


class TamanhoViewSet(BaseViewSet):
    queryset = Tamanho.objects.all().order_by('idgrade_id', 'Tamanho')
    serializer_class = TamanhoSerializer

    # FILTRO: traz apenas tamanhos da grade informada (idgrade)
    def get_queryset(self):
        qs = super().get_queryset()
        idgrade = self.request.query_params.get('idgrade') or self.request.query_params.get('Idgrade')
        if idgrade:
            qs = qs.filter(idgrade_id=idgrade)
        return qs


class CorViewSet(BaseViewSet):
    queryset = Cor.objects.all().order_by('Descricao')
    serializer_class = CorSerializer


class MaterialViewSet(BaseViewSet):
    queryset = Material.objects.all().order_by('Descricao')
    serializer_class = MaterialSerializer


class ColecaoViewSet(BaseViewSet):
    queryset = Colecao.objects.all().order_by('-Codigo', 'Estacao', 'Descricao')
    serializer_class = ColecaoSerializer


class UnidadeViewSet(BaseViewSet):
    queryset = Unidade.objects.all().order_by('Descricao')
    serializer_class = UnidadeSerializer


class GrupoViewSet(BaseViewSet):
    queryset = Grupo.objects.all().order_by('Descricao')
    serializer_class = GrupoSerializer


class SubgrupoViewSet(BaseViewSet):
    queryset = Subgrupo.objects.all().order_by('Descricao')
    serializer_class = SubgrupoSerializer

    # mantém o filtro por grupo (Idgrupo) para o cadastro de produto
    def get_queryset(self):
        qs = super().get_queryset()
        idgrupo = self.request.query_params.get('Idgrupo')
        if idgrupo:
            qs = qs.filter(Idgrupo_id=idgrupo)
        return qs


class TabelaprecoViewSet(BaseViewSet):
    queryset = Tabelapreco.objects.all().order_by('-DataInicio')
    serializer_class = TabelaprecoSerializer


class CodigosViewSet(BaseViewSet):
    queryset = Codigos.objects.all()
    serializer_class = CodigosSerializer


class ProdutoViewSet(BaseViewSet):
    queryset = Produto.objects.all().order_by('-data_cadastro')
    serializer_class = ProdutoSerializer
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ['data_cadastro', 'descricao', 'descricao_reduzida', 'referencia', 'tipo_produto', 'grupo', 'colecao', 'ativo']
    ordering = ['-data_cadastro']

    # Usado por CanToggleProductFlags
    model_perm_codename = "produto.change_produto"

    CADASTRAL_FIELDS = ['descricao', 'descricao_reduzida', 'unidade', 'grupo', 'subgrupo', 'colecao', 'material', 'grade', 'observacoes']
    FISCAL_FIELDS = ['ncm', 'origem_mercadoria', 'csosn_ou_cst_icms', 'aliquota_icms', 'cfop_venda_dentro', 'cfop_venda_fora', 'cst_pis', 'aliq_pis', 'cst_cofins', 'aliq_cofins', 'ipi_situacao', 'aliq_ipi']

    def _is_produto_venda(self, produto):
        return produto.tipo_produto in ('1', '3')

    def _is_produto_uso_consumo(self, produto):
        return produto.tipo_produto == '2'

    def _is_produto_insumo(self, produto):
        return produto.tipo_produto == '4'

    def _snapshot(self, produto, fields):
        data = {}
        for field in fields:
            value = getattr(produto, f'{field}_id', getattr(produto, field, None))
            data[field] = str(value) if isinstance(value, Decimal) else value
        return data

    def _registrar_historico(self, produto, tipo_evento, descricao='', anteriores=None, novos=None):
        if not produto or not self._is_produto_venda(produto):
            return
        user = getattr(self.request, 'user', None)
        ProdutoVendaHistorico.objects.create(
            empresa=produto.empresa,
            produto=produto,
            tipo_evento=tipo_evento,
            usuario=user if user and user.is_authenticated else None,
            descricao=descricao,
            dados_anteriores=anteriores or {},
            dados_novos=novos or {},
        )

    def _registrar_historico_uso(self, produto, tipo_evento, descricao='', anteriores=None, novos=None):
        if not produto or not self._is_produto_uso_consumo(produto):
            return
        user = getattr(self.request, 'user', None)
        ProdutoUsoConsumoHistorico.objects.create(
            empresa=produto.empresa,
            produto=produto,
            tipo_evento=tipo_evento,
            usuario=user if user and user.is_authenticated else None,
            descricao=descricao,
            dados_anteriores=anteriores or {},
            dados_novos=novos or {},
        )

    def _registrar_historico_insumo(self, produto, tipo_evento, descricao='', anteriores=None, novos=None):
        if not produto or not self._is_produto_insumo(produto):
            return
        user = getattr(self.request, 'user', None)
        ProdutoInsumoHistorico.objects.create(
            empresa=produto.empresa,
            produto=produto,
            tipo_evento=tipo_evento,
            usuario=user if user and user.is_authenticated else None,
            descricao=descricao,
            dados_anteriores=anteriores or {},
            dados_novos=novos or {},
        )

    def perform_create(self, serializer):
        super().perform_create(serializer)
        produto = serializer.instance
        self._registrar_historico(produto, ProdutoVendaHistorico.CRIACAO, 'Produto Venda criado.', novos=self._snapshot(produto, self.CADASTRAL_FIELDS + self.FISCAL_FIELDS))
        self._registrar_historico_uso(produto, ProdutoUsoConsumoHistorico.CRIACAO, 'Produto Uso/Consumo criado.', novos=self._snapshot(produto, self.CADASTRAL_FIELDS + self.FISCAL_FIELDS))
        self._registrar_historico_insumo(produto, ProdutoInsumoHistorico.CRIACAO, 'Insumo criado.', novos=self._snapshot(produto, self.CADASTRAL_FIELDS + self.FISCAL_FIELDS))

    def perform_update(self, serializer):
        produto = self.get_object()
        anteriores_cadastrais = self._snapshot(produto, self.CADASTRAL_FIELDS)
        anteriores_fiscais = self._snapshot(produto, self.FISCAL_FIELDS)
        super().perform_update(serializer)
        produto = serializer.instance
        produto.refresh_from_db()
        serializer.instance = produto
        novos_cadastrais = self._snapshot(produto, self.CADASTRAL_FIELDS)
        novos_fiscais = self._snapshot(produto, self.FISCAL_FIELDS)
        alterados_cadastrais = {k: anteriores_cadastrais[k] for k in self.CADASTRAL_FIELDS if anteriores_cadastrais.get(k) != novos_cadastrais.get(k)}
        alterados_fiscais = {k: anteriores_fiscais[k] for k in self.FISCAL_FIELDS if anteriores_fiscais.get(k) != novos_fiscais.get(k)}
        if alterados_cadastrais:
            novos_alterados = {k: novos_cadastrais[k] for k in alterados_cadastrais}
            self._registrar_historico(
                produto,
                ProdutoVendaHistorico.ALTERACAO_CADASTRAL,
                'Alteração cadastral do Produto Venda.',
                anteriores=alterados_cadastrais,
                novos=novos_alterados,
            )
            _audit_produto_update('produto', produto.pk, alterados_cadastrais, novos_alterados, self.request, action='update_cadastral')
            self._registrar_historico_uso(produto, ProdutoUsoConsumoHistorico.ALTERACAO_CADASTRAL, 'Alteração cadastral do Produto Uso/Consumo.', anteriores=alterados_cadastrais, novos=novos_alterados)
            self._registrar_historico_insumo(produto, ProdutoInsumoHistorico.ALTERACAO_CADASTRAL, 'Alteração cadastral do Insumo.', anteriores=alterados_cadastrais, novos=novos_alterados)
        if alterados_fiscais:
            novos_alterados = {k: novos_fiscais[k] for k in alterados_fiscais}
            self._registrar_historico(
                produto,
                ProdutoVendaHistorico.ALTERACAO_FISCAL,
                'Alteração fiscal do Produto Venda.',
                anteriores=alterados_fiscais,
                novos=novos_alterados,
            )
            _audit_produto_update('produto', produto.pk, alterados_fiscais, novos_alterados, self.request, action='update_fiscal')
            self._registrar_historico_uso(produto, ProdutoUsoConsumoHistorico.ALTERACAO_FISCAL, 'Alteração fiscal do Produto Uso/Consumo.', anteriores=alterados_fiscais, novos=novos_alterados)
            self._registrar_historico_insumo(produto, ProdutoInsumoHistorico.ALTERACAO_FISCAL, 'Alteração fiscal do Insumo.', anteriores=alterados_fiscais, novos=novos_alterados)

    def get_queryset(self):
        qs = super().get_queryset()
        tipo_produto = self.request.query_params.get('tipo_produto') or self.request.query_params.get('tipo')
        tipos_validos = {'1', '2', '3', '4'}
        if tipo_produto:
            tipos = [tipo.strip() for tipo in str(tipo_produto).split(',') if tipo.strip() in tipos_validos]
            if tipos:
                qs = qs.filter(tipo_produto__in=tipos)

        ativo = self.request.query_params.get('ativo')
        if ativo in ('true', '1'):
            qs = qs.filter(ativo=True)
        elif ativo in ('false', '0'):
            qs = qs.filter(ativo=False)

        bloqueado = self.request.query_params.get('bloqueado_venda')
        if bloqueado in ('true', '1'):
            qs = qs.filter(bloqueado_venda=True)
        elif bloqueado in ('false', '0'):
            qs = qs.filter(bloqueado_venda=False)

        fiscal_incompleto = self.request.query_params.get('cadastro_fiscal_incompleto')
        if fiscal_incompleto in ('true', '1'):
            qs = qs.filter(tipo_produto__in=('2', '4')).filter(Q(ncm__isnull=True) | Q(ncm=''))
        elif fiscal_incompleto in ('false', '0'):
            qs = qs.exclude(tipo_produto__in=('2', '4'), ncm__isnull=True).exclude(tipo_produto__in=('2', '4'), ncm='')

        grupo = self.request.query_params.get('grupo')
        colecao = self.request.query_params.get('colecao')
        subgrupo = self.request.query_params.get('subgrupo')
        if grupo:
            qs = qs.filter(grupo_id=grupo)
        if colecao:
            qs = qs.filter(colecao_id=colecao)
        if subgrupo:
            qs = qs.filter(subgrupo_id=subgrupo)

        search = (self.request.query_params.get('search') or '').strip()
        referencia = (self.request.query_params.get('referencia') or '').strip()
        codigo = (self.request.query_params.get('codigo') or '').strip()
      #  if search:
      #      qs = qs.filter(
      #          Q(descricao__icontains=search)
      #          | Q(descricao_reduzida__icontains=search)
      #          | Q(referencia__icontains=search)
      #      )


       # novo bloco inserido  
        if search:
            qs = qs.filter(
            Q(descricao__icontains=search)
            | Q(descricao_reduzida__icontains=search)
            | Q(referencia__icontains=search)
            | Q(skus__ean13__iexact=search)
            | Q(skus__codigo_item_ref__iexact=search)
            ).distinct()
        if referencia:
            qs = qs.filter(referencia__icontains=referencia)
        if codigo:
            qs = qs.filter(descricao_reduzida__icontains=codigo)
        unidade = self.request.query_params.get('unidade')
        material = self.request.query_params.get('material')
        ncm = (self.request.query_params.get('ncm') or '').strip()
        if unidade:
            qs = qs.filter(unidade_id=unidade)
        if material:
            qs = qs.filter(material_id=material)
        if ncm:
            qs = qs.filter(ncm__icontains=ncm)
        # fim do bloco

        return qs

    @action(detail=False, methods=['get'], url_path='indicadores-uso-consumo')
    def indicadores_uso_consumo(self, request):
        qs = self.filter_queryset(self.get_queryset().filter(tipo_produto='2'))
        return Response({
            'total': qs.count(),
            'ativos': qs.filter(ativo=True).count(),
            'inativos': qs.filter(ativo=False).count(),
            'cadastro_fiscal_incompleto': qs.filter(Q(ncm__isnull=True) | Q(ncm='')).count(),
        })

    @action(detail=False, methods=['get'], url_path='indicadores-insumos')
    def indicadores_insumos(self, request):
        qs = self.filter_queryset(self.get_queryset().filter(tipo_produto='4'))
        return Response({
            'total': qs.count(),
            'ativos': qs.filter(ativo=True).count(),
            'inativos': qs.filter(ativo=False).count(),
            'cadastro_fiscal_incompleto': qs.filter(Q(ncm__isnull=True) | Q(ncm='')).count(),
        })

    def _sku_info(self, sku):
        return {
            'sku_id': sku.pk,
            'cor': getattr(sku.idcor, 'Descricao', None),
            'cor_id': sku.idcor_id,
            'tamanho': getattr(sku.idtamanho, 'Tamanho', None),
            'tamanho_id': sku.idtamanho_id,
            'ean13': sku.ean13,
            'codigo_item_ref': sku.codigo_item_ref,
        }

    @action(detail=True, methods=['post'], url_path='gerar-skus')
    def gerar_skus(self, request, pk=None):
        """
        Gera SKUs (ProdutoDetalhe) para um produto vendável com grade.
        Body:
        {
          "cores": [Idcor, ...],          # obrigatório
          "tamanhos": [Idtamanho, ...]    # opcional; se omitido, usa todos da grade do produto
        }
        """
        produto = self.get_object()
        if produto.tipo_produto not in ('1', '3'):
            return Response({'detail': 'Apenas produtos vendáveis com grade geram SKUs.'}, status=status.HTTP_400_BAD_REQUEST)
        if not produto.grade_id:
            return Response({'detail': 'Produto sem grade.'}, status=status.HTTP_400_BAD_REQUEST)

        cores = request.data.get('cores') or []
        if not isinstance(cores, list):
            return Response({'detail': 'Informe "cores" como lista de Idcor.'}, status=status.HTTP_400_BAD_REQUEST)
        cores_ids = {int(cor_id) for cor_id in cores if str(cor_id).isdigit()}
        cores_validas = set(Cor.objects.filter(pk__in=cores_ids).values_list('pk', flat=True))
        if cores_ids != cores_validas:
            return Response({'detail': 'Uma ou mais cores informadas não existem.'}, status=status.HTTP_400_BAD_REQUEST)

        tamanho_ids = request.data.get('tamanhos')
        if tamanho_ids:
            tamanhos = list(Tamanho.objects.filter(pk__in=tamanho_ids, idgrade_id=produto.grade_id))
        else:
            tamanhos = list(Tamanho.objects.filter(idgrade_id=produto.grade_id))

        created, reactivated, inactivated, unchanged = [], [], [], []

        with transaction.atomic():
            atuais = ProdutoDetalhe.objects.select_for_update().select_related('idcor', 'idtamanho').filter(produto=produto)
            atuais_map = {(sku.idcor_id, sku.idtamanho_id): sku for sku in atuais}

            for cor_id in cores_ids:
                for tam in tamanhos:
                    sku = atuais_map.get((cor_id, tam.pk))
                    if sku is None:
                        payload = {'produto': produto.pk, 'idcor': cor_id, 'idtamanho': tam.pk}
                        ser = ProdutoDetalheSerializer(data=payload, context={'request': request})
                        ser.is_valid(raise_exception=True)
                        sku = ser.save()
                        sku = ProdutoDetalhe.objects.select_related('idcor', 'idtamanho').get(pk=sku.pk)
                        created.append(ProdutoDetalheSerializer(sku, context={'request': request}).data)
                        self._registrar_historico(produto, ProdutoVendaHistorico.SKU_CRIADO, 'SKU criado pela sincronização de cores.', novos=self._sku_info(sku))
                    elif not sku.ativo:
                        sku.ativo = True
                        sku.save(update_fields=['ativo'])
                        reactivated.append(self._sku_info(sku))
                        self._registrar_historico(produto, ProdutoVendaHistorico.SKU_REATIVADO, 'SKU reativado pela sincronização de cores.', novos=self._sku_info(sku))
                    else:
                        unchanged.append(self._sku_info(sku))

            for sku in atuais:
                if sku.idcor_id not in cores_ids and sku.ativo:
                    antes = self._sku_info(sku)
                    sku.ativo = False
                    sku.save(update_fields=['ativo'])
                    inactivated.append(antes)
                    self._registrar_historico(produto, ProdutoVendaHistorico.SKU_INATIVADO, 'SKU inativado pela sincronização de cores.', anteriores=antes)

        return Response({
            'counts': {'created': len(created), 'reactivated': len(reactivated), 'inactivated': len(inactivated), 'unchanged': len(unchanged)},
            'created': created,
            'reactivated': reactivated,
            'inactivated': inactivated,
            'unchanged': unchanged,
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'], url_path='historico')
    def historico(self, request, pk=None):
        produto = self.get_object()
        if produto.tipo_produto == '2':
            qs = produto.historico_uso_consumo.all().order_by('-data_evento', '-id')
            page = self.paginate_queryset(qs)
            if page is not None:
                ser = ProdutoUsoConsumoHistoricoSerializer(page, many=True, context={'request': request})
                return self.get_paginated_response(ser.data)
            ser = ProdutoUsoConsumoHistoricoSerializer(qs, many=True, context={'request': request})
            return Response(ser.data)
        if produto.tipo_produto == '4':
            qs = produto.historico_insumo.all().order_by('-data_evento', '-id')
            page = self.paginate_queryset(qs)
            if page is not None:
                ser = ProdutoInsumoHistoricoSerializer(page, many=True, context={'request': request})
                return self.get_paginated_response(ser.data)
            ser = ProdutoInsumoHistoricoSerializer(qs, many=True, context={'request': request})
            return Response(ser.data)
        qs = produto.historico_venda.all().order_by('-data_evento', '-id')
        page = self.paginate_queryset(qs)
        if page is not None:
            ser = ProdutoVendaHistoricoSerializer(page, many=True, context={'request': request})
            return self.get_paginated_response(ser.data)
        ser = ProdutoVendaHistoricoSerializer(qs, many=True, context={'request': request})
        return Response(ser.data)

    def destroy(self, request, *args, **kwargs):
        produto = self.get_object()
        bloqueios = []
        if produto.tipo_produto == '2' and produto.movimentacoes_uso_consumo.exists():
            bloqueios.append('movimentações de uso/consumo')
        for rel in produto._meta.related_objects:
            accessor = rel.get_accessor_name()
            if accessor in {'skus', 'precos', 'historico_venda', 'imagens', 'promocoes'}:
                continue
            if rel.related_model._meta.app_label == 'auditoria':
                continue
            manager = getattr(produto, accessor, None)
            if manager is not None and hasattr(manager, 'exists') and manager.exists():
                bloqueios.append(rel.related_model._meta.verbose_name_plural or rel.related_model.__name__)
        eans = list(produto.skus.values_list('ean13', flat=True))
        if eans and (
            EstoqueMovimentacao.objects.filter(CodigodeBarra__in=eans).exists()
            or InventarioEstoqueItem.objects.filter(CodigodeBarra__in=eans).exists()
        ):
            bloqueios.append('movimentações de estoque')
        if bloqueios:
            return Response(
                {'detail': 'Produto possui utilização/movimentação operacional e deve ser inativado ou bloqueado em vez de excluído.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if eans:
            Estoque.objects.filter(
                CodigodeBarra__in=eans,
            ).filter(
                Q(Estoque=0) | Q(Estoque__isnull=True),
                Q(reserva=0) | Q(reserva__isnull=True),
            ).delete()
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'], url_path='inicializar-estoque')
    def inicializar_estoque(self, request, pk=None):
        """
        Cria estoque inicial (quantidade 0) para todas as SKUs do produto
        nas lojas informadas.

        Body esperado:
        {
          "lojas": [Idloja, ...]
        }
        """
        produto = self.get_object()

        if produto.tipo_produto not in ('1', '3'):
            return Response(
                {'detail': 'Estoque inicial só é permitido para produtos vendáveis com grade.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lojas = request.data.get('lojas') or []
        if not isinstance(lojas, list) or not lojas:
            return Response(
                {'detail': 'Informe "lojas" como lista de IDs de loja.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        skus = list(ProdutoDetalhe.objects.filter(produto=produto))
        if not skus:
            return Response(
                {'detail': 'Produto sem SKUs; gere SKUs antes de inicializar o estoque.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        created = 0
        skipped = 0
        ref = produto.referencia or ''

        for loja_id in lojas:
            for sku in skus:
                obj, was_created = Estoque.objects.get_or_create(
                    CodigodeBarra=sku.ean13,
                    Idloja_id=loja_id,
                    defaults={
                        'referencia': ref,
                        'Estoque': 0,
                        'reserva': 0,
                    },
                )
                if was_created:
                    created += 1
                else:
                    skipped += 1

        return Response(
            {
                'counts': {
                    'created': created,
                    'skipped': skipped,
                }
            },
            status=status.HTTP_200_OK,
        )

    # -------------------- FLAGS (com permissão fina) --------------------

    @action(detail=True, methods=['post'], url_path='ativar', permission_classes=[CanToggleProductFlags])
    def ativar(self, request, pk=None):
        obj = self.get_object()
        if not obj.ativo:
            before = obj.ativo
            obj.ativo = True
            obj.data_inativo = None
            obj.save(update_fields=['ativo', 'data_inativo'])
            _audit('produto', obj.pk, {'ativo': [before, True]}, request, action="custom")
            self._registrar_historico(obj, ProdutoVendaHistorico.ATIVACAO, 'Produto Venda ativado.', anteriores={'ativo': before}, novos={'ativo': True})
            self._registrar_historico_uso(obj, ProdutoUsoConsumoHistorico.ATIVACAO, 'Produto Uso/Consumo ativado.', anteriores={'ativo': before}, novos={'ativo': True})
            self._registrar_historico_insumo(obj, ProdutoInsumoHistorico.ATIVACAO, 'Insumo ativado.', anteriores={'ativo': before}, novos={'ativo': True})
        ser = self.get_serializer(obj)
        return Response(ser.data)

    @action(detail=True, methods=['post'], url_path='inativar', permission_classes=[CanToggleProductFlags])
    def inativar(self, request, pk=None):
        obj = self.get_object()

        motivo = (request.data.get('motivo') or '').strip()
        if len(motivo) < 3:
            return Response({'detail': 'Informe um motivo com pelo menos 3 caracteres.'}, status=status.HTTP_400_BAD_REQUEST)

        senha = request.data.get('senha') or ''
        if not senha or not request.user.check_password(senha):
            return Response({'detail': 'Senha inválida.'}, status=status.HTTP_400_BAD_REQUEST)

        if obj.ativo:
            before = obj.ativo
            obj.ativo = False
            obj.data_inativo = timezone.now()
            obj.save(update_fields=['ativo', 'data_inativo'])
            changes = {'ativo': [before, False], 'motivo': motivo}
            _audit('produto', obj.pk, changes, request, action="custom")
            self._registrar_historico(obj, ProdutoVendaHistorico.INATIVACAO, 'Produto Venda inativado.', anteriores={'ativo': before}, novos={'ativo': False, 'motivo': motivo})
            self._registrar_historico_uso(obj, ProdutoUsoConsumoHistorico.INATIVACAO, 'Produto Uso/Consumo inativado.', anteriores={'ativo': before}, novos={'ativo': False, 'motivo': motivo})
            self._registrar_historico_insumo(obj, ProdutoInsumoHistorico.INATIVACAO, 'Insumo inativado.', anteriores={'ativo': before}, novos={'ativo': False, 'motivo': motivo})
        ser = self.get_serializer(obj)
        return Response(ser.data)

    @action(detail=True, methods=['post'], url_path='bloquear-venda', permission_classes=[CanToggleProductFlags])
    def bloquear_venda(self, request, pk=None):
        obj = self.get_object()
        if obj.tipo_produto in ('2', '4'):
            return Response({'detail': 'Este tipo de produto não possui bloqueio de venda.'}, status=status.HTTP_400_BAD_REQUEST)

        motivo = (request.data.get('motivo') or '').strip()
        if len(motivo) < 3:
            return Response({'detail': 'Informe um motivo com pelo menos 3 caracteres.'}, status=status.HTTP_400_BAD_REQUEST)

        senha = request.data.get('senha') or ''
        if not senha or not request.user.check_password(senha):
            return Response({'detail': 'Senha inválida.'}, status=status.HTTP_400_BAD_REQUEST)

        if not obj.bloqueado_venda:
            before = obj.bloqueado_venda
            obj.bloqueado_venda = True
            obj.save(update_fields=['bloqueado_venda'])
            _audit('produto', obj.pk, {'bloqueado_venda': [before, True], 'motivo': motivo}, request, action="custom")
            self._registrar_historico(obj, ProdutoVendaHistorico.BLOQUEIO_VENDA, 'Produto Venda bloqueado para venda.', anteriores={'bloqueado_venda': before}, novos={'bloqueado_venda': True, 'motivo': motivo})
        ser = self.get_serializer(obj)
        return Response(ser.data)

    @action(detail=True, methods=['post'], url_path='desbloquear-venda', permission_classes=[CanToggleProductFlags])
    def desbloquear_venda(self, request, pk=None):
        obj = self.get_object()
        if obj.tipo_produto in ('2', '4'):
            return Response({'detail': 'Este tipo de produto não possui desbloqueio de venda.'}, status=status.HTTP_400_BAD_REQUEST)
        if obj.bloqueado_venda:
            before = obj.bloqueado_venda
            obj.bloqueado_venda = False
            obj.save(update_fields=['bloqueado_venda'])
            _audit('produto', obj.pk, {'bloqueado_venda': [before, False]}, request, action="custom")
            self._registrar_historico(obj, ProdutoVendaHistorico.DESBLOQUEIO_VENDA, 'Produto Venda desbloqueado para venda.', anteriores={'bloqueado_venda': before}, novos={'bloqueado_venda': False})
        ser = self.get_serializer(obj)
        return Response(ser.data)

    def _movimenta_uso_consumo(self, produto, tipo, quantidade, loja_id, motivo='', destino='', documento='', origem=''):
        if produto.tipo_produto != '2':
            raise ValidationError({'produto': 'Operação exclusiva para Produto Uso/Consumo.'})
        quantidade = Decimal(str(quantidade or 0))
        if quantidade <= 0:
            raise ValidationError({'quantidade': 'Informe quantidade maior que zero.'})
        loja = Loja.objects.filter(pk=loja_id, empresa=produto.empresa, ativo=True).first()
        if not loja:
            raise ValidationError({'loja': 'Informe uma loja/unidade ativa da mesma empresa para a movimentação.'})
        with transaction.atomic():
            estoque, _ = ProdutoUsoConsumoEstoque.objects.select_for_update().get_or_create(
                empresa=produto.empresa, produto=produto, loja=loja, defaults={'saldo': Decimal('0')}
            )
            saldo_anterior = Decimal(estoque.saldo or 0)
            sinal = Decimal('1') if tipo in (ProdutoUsoConsumoMovimentacao.TIPO_ENTRADA, ProdutoUsoConsumoMovimentacao.TIPO_AJUSTE_ENTRADA) else Decimal('-1')
            saldo_posterior = saldo_anterior + (quantidade * sinal)
            if saldo_posterior < 0:
                raise ValidationError({'quantidade': 'Saldo insuficiente na unidade informada para Produto Uso/Consumo.'})
            estoque.saldo = saldo_posterior
            estoque.save(update_fields=['saldo'])
            movimento = ProdutoUsoConsumoMovimentacao.objects.create(
                empresa=produto.empresa, produto=produto, loja=loja, tipo=tipo, quantidade=quantidade,
                saldo_anterior=saldo_anterior, saldo_posterior=saldo_posterior,
                usuario=self.request.user if self.request.user.is_authenticated else None,
                motivo=motivo or '', destino=destino or '', documento=documento or '', origem=origem or '',
            )
            evento = ProdutoUsoConsumoHistorico.CONSUMO_INTERNO if tipo == ProdutoUsoConsumoMovimentacao.TIPO_CONSUMO_INTERNO else (
                ProdutoUsoConsumoHistorico.ENTRADA_ESTOQUE if tipo == ProdutoUsoConsumoMovimentacao.TIPO_ENTRADA else ProdutoUsoConsumoHistorico.AJUSTE_ESTOQUE
            )
            self._registrar_historico_uso(produto, evento, f'Movimentação {tipo} de Uso/Consumo.', novos={'quantidade': str(quantidade), 'saldo_anterior': str(saldo_anterior), 'saldo_posterior': str(saldo_posterior), 'motivo': motivo, 'destino': destino})
        return movimento

    @action(detail=True, methods=['post'], url_path='entrada-uso-consumo')
    def entrada_uso_consumo(self, request, pk=None):
        produto = self.get_object()
        try:
            mov = self._movimenta_uso_consumo(produto, ProdutoUsoConsumoMovimentacao.TIPO_ENTRADA, request.data.get('quantidade'), request.data.get('loja'), request.data.get('motivo') or '', request.data.get('destino') or '', request.data.get('documento') or '', request.data.get('origem') or 'MANUAL')
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
        return Response(ProdutoUsoConsumoMovimentacaoSerializer(mov, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='consumo-interno')
    def consumo_interno(self, request, pk=None):
        produto = self.get_object()
        motivo = (request.data.get('motivo') or '').strip()
        destino = (request.data.get('destino') or '').strip()
        if not motivo or not destino:
            return Response({'detail': 'Informe motivo e destino do consumo interno.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            mov = self._movimenta_uso_consumo(produto, ProdutoUsoConsumoMovimentacao.TIPO_CONSUMO_INTERNO, request.data.get('quantidade'), request.data.get('loja'), motivo, destino, request.data.get('documento') or '', request.data.get('origem') or 'CONSUMO_INTERNO')
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
        return Response(ProdutoUsoConsumoMovimentacaoSerializer(mov, context={'request': request}).data)

    @action(detail=True, methods=['get'], url_path='movimentacoes-uso-consumo')
    def movimentacoes_uso_consumo(self, request, pk=None):
        produto = self.get_object()
        if produto.tipo_produto != '2':
            return Response({'detail': 'Movimentações de Uso/Consumo são exclusivas para tipo 2.'}, status=status.HTTP_400_BAD_REQUEST)
        qs = produto.movimentacoes_uso_consumo.select_related('loja', 'usuario').order_by('-data_movimento', '-id')
        page = self.paginate_queryset(qs)
        if page is not None:
            ser = ProdutoUsoConsumoMovimentacaoSerializer(page, many=True, context={'request': request})
            return self.get_paginated_response(ser.data)
        ser = ProdutoUsoConsumoMovimentacaoSerializer(qs, many=True, context={'request': request})
        return Response(ser.data)


class ProdutoImagemViewSet(BaseViewSet):
    queryset = ProdutoImagem.objects.select_related('produto', 'produto__empresa').all()
    serializer_class = ProdutoImagemSerializer

    def get_queryset(self):
        qs = ProdutoImagem.objects.select_related('produto', 'produto__empresa').order_by('produto_id', 'ordem', 'id')
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(produto__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        produto_id = self.request.query_params.get('produto')
        if produto_id:
            qs = qs.filter(produto_id=produto_id)
        return qs

    def _registrar_historico_imagem(self, imagem, tipo_evento, descricao, anteriores=None, novos=None):
        produto = imagem.produto
        if produto.tipo_produto not in ('1', '3'):
            return
        user = getattr(self.request, 'user', None)
        ProdutoVendaHistorico.objects.create(
            empresa=produto.empresa,
            produto=produto,
            tipo_evento=tipo_evento,
            usuario=user if user and user.is_authenticated else None,
            descricao=descricao,
            dados_anteriores=anteriores or {},
            dados_novos=novos or {},
        )

    @transaction.atomic
    def perform_create(self, serializer):
        principal = serializer.validated_data.get('principal') is True
        produto = serializer.validated_data.get('produto')
        if principal:
            ProdutoImagem.objects.select_for_update().filter(produto=produto, principal=True).update(principal=False)
        imagem = serializer.save()
        self._registrar_historico_imagem(
            imagem,
            ProdutoVendaHistorico.IMAGEM_INCLUIDA,
            'Imagem incluída no Produto Venda.',
            novos={'imagem_id': imagem.pk, 'principal': imagem.principal, 'ordem': imagem.ordem},
        )
        if principal:
            self._registrar_historico_imagem(
                imagem,
                ProdutoVendaHistorico.IMAGEM_PRINCIPAL,
                'Imagem marcada como principal.',
                novos={'imagem_id': imagem.pk},
            )

    @transaction.atomic
    def perform_update(self, serializer):
        imagem = self.get_object()
        principal_antes = imagem.principal
        principal_novo = serializer.validated_data.get('principal', principal_antes)
        if principal_novo:
            ProdutoImagem.objects.select_for_update().filter(produto=imagem.produto, principal=True).exclude(pk=imagem.pk).update(principal=False)
        imagem = serializer.save()
        if principal_novo and not principal_antes:
            self._registrar_historico_imagem(
                imagem,
                ProdutoVendaHistorico.IMAGEM_PRINCIPAL,
                'Imagem marcada como principal.',
                anteriores={'imagem_id': imagem.pk, 'principal': principal_antes},
                novos={'imagem_id': imagem.pk, 'principal': True},
            )

    @action(detail=True, methods=['post'], url_path='marcar-principal')
    @transaction.atomic
    def marcar_principal(self, request, pk=None):
        imagem = self.get_object()
        ProdutoImagem.objects.select_for_update().filter(produto=imagem.produto, principal=True).exclude(pk=imagem.pk).update(principal=False)
        if not imagem.principal:
            imagem.principal = True
            imagem.save(update_fields=['principal'])
            self._registrar_historico_imagem(
                imagem,
                ProdutoVendaHistorico.IMAGEM_PRINCIPAL,
                'Imagem marcada como principal.',
                novos={'imagem_id': imagem.pk, 'principal': True},
            )
        return Response(self.get_serializer(imagem).data)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        imagem = self.get_object()
        historico_payload = {'imagem_id': imagem.pk, 'principal': imagem.principal, 'ordem': imagem.ordem}
        self._registrar_historico_imagem(
            imagem,
            ProdutoVendaHistorico.IMAGEM_REMOVIDA,
            'Imagem removida do Produto Venda.',
            anteriores=historico_payload,
        )
        return super().destroy(request, *args, **kwargs)


class ProdutoDetalheViewSet(BaseViewSet):
    queryset = ProdutoDetalhe.objects.all()
    serializer_class = ProdutoDetalheSerializer

    # Usado por CanToggleProductFlags
    model_perm_codename = "produto.change_produtodetalhe"

    def get_queryset(self):
        qs = ProdutoDetalhe.objects.all().order_by('produto_id', 'idcor_id', 'idtamanho_id')
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(produto__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        pid = self.request.query_params.get('produto')
        cor = self.request.query_params.get('idcor')
        tam = self.request.query_params.get('idtamanho')
        search = (self.request.query_params.get('search') or '').strip()
        if pid:
            qs = qs.filter(produto_id=pid)
        if cor:
            qs = qs.filter(idcor_id=cor)
        if tam:
            qs = qs.filter(idtamanho_id=tam)
        if search:
            qs = qs.filter(
                Q(ean13__icontains=search)
                | Q(codigo_item_ref__icontains=search)
                | Q(produto__referencia__icontains=search)
                | Q(produto__descricao__icontains=search)
                | Q(produto__descricao_reduzida__icontains=search)
            ).distinct()
        return qs

    # -------------------- FLAGS (com permissão fina) --------------------

    @action(detail=True, methods=['post'], url_path='ativar', permission_classes=[CanToggleProductFlags])
    def ativar(self, request, pk=None):
        obj = self.get_object()
        if not obj.ativo:
            before = obj.ativo
            obj.ativo = True
            obj.save(update_fields=['ativo'])
            _audit('produtodetalhe', obj.pk, {'ativo': [before, True]}, request, action="custom")
        ser = self.get_serializer(obj)
        return Response(ser.data)

    @action(detail=True, methods=['post'], url_path='inativar', permission_classes=[CanToggleProductFlags])
    def inativar(self, request, pk=None):
        obj = self.get_object()

        motivo = (request.data.get('motivo') or '').strip()
        if len(motivo) < 3:
            return Response({'detail': 'Informe um motivo com pelo menos 3 caracteres.'}, status=status.HTTP_400_BAD_REQUEST)

        senha = request.data.get('senha') or ''
        if not senha or not request.user.check_password(senha):
            return Response({'detail': 'Senha inválida.'}, status=status.HTTP_400_BAD_REQUEST)

        if obj.ativo:
            before = obj.ativo
            obj.ativo = False
            obj.save(update_fields=['ativo'])
            _audit('produtodetalhe', obj.pk, {'ativo': [before, False], 'motivo': motivo}, request, action="custom")
        ser = self.get_serializer(obj)
        return Response(ser.data)

    @action(detail=True, methods=['post'], url_path='bloquear-venda', permission_classes=[CanToggleProductFlags])
    def bloquear_venda(self, request, pk=None):
        obj = self.get_object()

        motivo = (request.data.get('motivo') or '').strip()
        if len(motivo) < 3:
            return Response({'detail': 'Informe um motivo com pelo menos 3 caracteres.'}, status=status.HTTP_400_BAD_REQUEST)

        senha = request.data.get('senha') or ''
        if not senha or not request.user.check_password(senha):
            return Response({'detail': 'Senha inválida.'}, status=status.HTTP_400_BAD_REQUEST)

        if not obj.bloqueado_venda:
            before = obj.bloqueado_venda
            obj.bloqueado_venda = True
            obj.save(update_fields=['bloqueado_venda'])
            _audit('produtodetalhe', obj.pk, {'bloqueado_venda': [before, True], 'motivo': motivo}, request, action="custom")
        ser = self.get_serializer(obj)
        return Response(ser.data)

    @action(detail=True, methods=['post'], url_path='desbloquear-venda', permission_classes=[CanToggleProductFlags])
    def desbloquear_venda(self, request, pk=None):
        obj = self.get_object()
        if obj.bloqueado_venda:
            before = obj.bloqueado_venda
            obj.bloqueado_venda = False
            obj.save(update_fields=['bloqueado_venda'])
            _audit('produtodetalhe', obj.pk, {'bloqueado_venda': [before, False]}, request, action="custom")
        ser = self.get_serializer(obj)
        return Response(ser.data)


class TabelaprecoProdutoViewSet(BaseViewSet):
    queryset = TabelaprecoProduto.objects.all().order_by('-DataInicio')
    serializer_class = TabelaprecoProdutoSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(Q(tabela__empresa_id=empresa_id) | Q(produto__empresa_id=empresa_id))
        elif not self.request.user.is_superuser:
            return qs.none()
        return qs

    def perform_create(self, serializer):
        self._validar_empresa_preco(serializer.validated_data)
        serializer.save()

    def perform_update(self, serializer):
        data = {
            "produto": serializer.validated_data.get("produto", serializer.instance.produto),
            "tabela": serializer.validated_data.get("tabela", serializer.instance.tabela),
        }
        self._validar_empresa_preco(data)
        serializer.save()

    def _validar_empresa_preco(self, data):
        produto = data.get("produto")
        tabela = data.get("tabela")
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            if produto and produto.empresa_id and produto.empresa_id != int(empresa_id):
                raise ValidationError({"produto": "Produto pertence a outra empresa."})
            if tabela and tabela.empresa_id and tabela.empresa_id != int(empresa_id):
                raise ValidationError({"tabela": "Tabela pertence a outra empresa."})
        if produto and tabela and produto.empresa_id and tabela.empresa_id and produto.empresa_id != tabela.empresa_id:
            raise ValidationError({"tabela": "Tabela e produto pertencem a empresas diferentes."})


class FichaTecnicaViewSet(BaseViewSet):
    permission_classes = [HasModuleRole, HasEmpresaModulo]
    empresa_modulo_field = 'usa_ficha_tecnica'
    required_module = "producao"
    queryset = FichaTecnica.objects.select_related('empresa', 'produto_final').prefetch_related('itens').order_by('-data_cadastro')
    serializer_class = FichaTecnicaSerializer
    read_roles = ["Admin", "Diretor", "Gerente", "Auxiliar"]
    write_roles = ["Admin", "Diretor", "Gerente"]

    def get_queryset(self):
        qs = super().get_queryset()
        produto = self.request.query_params.get('produto') or self.request.query_params.get('produto_final')
        status_param = self.request.query_params.get('status')
        ativa = self.request.query_params.get('ativa')
        search = (self.request.query_params.get('search') or '').strip()

        if produto:
            qs = qs.filter(produto_final_id=produto)
        if status_param:
            qs = qs.filter(status=status_param)
        if ativa in ('true', '1'):
            qs = qs.filter(ativa=True)
        elif ativa in ('false', '0'):
            qs = qs.filter(ativa=False)
        if search:
            qs = qs.filter(
                Q(produto_final__descricao__icontains=search)
                | Q(produto_final__referencia__icontains=search)
                | Q(descricao__icontains=search)
                | Q(versao__icontains=search)
            )
        return qs

    def perform_create(self, serializer):
        super().perform_create(serializer)
        _audit('FichaTecnica', serializer.instance.pk, {'created': True}, self.request, 'create')

    def perform_update(self, serializer):
        super().perform_update(serializer)
        _audit('FichaTecnica', serializer.instance.pk, {'updated': True}, self.request, 'update')

    @action(detail=True, methods=['post'], url_path='aprovar')
    def aprovar(self, request, pk=None):
        ficha = self.get_object()
        if not ficha.itens.exists():
            return Response({'detail': 'Inclua ao menos um item antes de aprovar a ficha.'}, status=status.HTTP_400_BAD_REQUEST)
        ficha.status = FichaTecnica.STATUS_APROVADA
        ficha.ativa = True
        ficha.save(update_fields=['status', 'ativa', 'atualizado_em'])
        _audit('FichaTecnica', ficha.pk, {'status': FichaTecnica.STATUS_APROVADA}, request, 'approve')
        return Response(self.get_serializer(ficha).data)


class FichaTecnicaItemViewSet(BaseViewSet):
    permission_classes = [HasModuleRole, HasEmpresaModulo]
    empresa_modulo_field = 'usa_ficha_tecnica'
    required_module = "producao"
    queryset = FichaTecnicaItem.objects.select_related('ficha', 'produto', 'fornecedor', 'unidade').order_by('ordem', 'id')
    serializer_class = FichaTecnicaItemSerializer
    read_roles = ["Admin", "Diretor", "Gerente", "Auxiliar"]
    write_roles = ["Admin", "Diretor", "Gerente"]

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = getattr(self.request.user, 'empresa_id', None)
        empresa_param = self.request.query_params.get('empresa')
        if self.request.user.is_superuser and empresa_param:
            qs = qs.filter(ficha__empresa_id=empresa_param)
        elif not self.request.user.is_superuser and empresa_id:
            qs = qs.filter(ficha__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            qs = qs.none()

        ficha = self.request.query_params.get('ficha')
        if ficha:
            qs = qs.filter(ficha_id=ficha)
        return qs

    def _save_with_empresa_scope(self, serializer):
        serializer.save()


class OrdemProducaoViewSet(BaseViewSet):
    permission_classes = [HasModuleRole, HasEmpresaModulo]
    empresa_modulo_field = 'usa_producao'
    required_module = "producao"
    queryset = (
        OrdemProducao.objects
        .select_related('empresa', 'ficha_tecnica', 'produto_final', 'sku_final', 'sku_final__idcor', 'sku_final__idtamanho')
        .prefetch_related('itens', 'grade_producao')
        .order_by('-data_emissao', '-id')
    )
    serializer_class = OrdemProducaoSerializer
    read_roles = ["Admin", "Diretor", "Gerente", "Auxiliar"]
    write_roles = ["Admin", "Diretor", "Gerente"]

    def get_queryset(self):
        qs = super().get_queryset()
        status_param = self.request.query_params.get('status')
        produto = self.request.query_params.get('produto') or self.request.query_params.get('produto_final')
        ficha = self.request.query_params.get('ficha') or self.request.query_params.get('ficha_tecnica')
        search = (self.request.query_params.get('search') or '').strip()

        if status_param:
            qs = qs.filter(status=status_param)
        if produto:
            qs = qs.filter(produto_final_id=produto)
        if ficha:
            qs = qs.filter(ficha_tecnica_id=ficha)
        if search:
            qs = qs.filter(
                Q(numero__icontains=search)
                | Q(produto_final__descricao__icontains=search)
                | Q(produto_final__referencia__icontains=search)
            )
        return qs

    @action(detail=False, methods=['get'], url_path='painel')
    def painel(self, request):
        qs = self.get_queryset()
        por_status = {
            row['status']: row['total']
            for row in qs.values('status').annotate(total=Count('id'))
        }
        faccoes = OrdemProducaoItem.objects.filter(
            ordem__in=qs,
            tipo=FichaTecnicaItem.TIPO_SERVICO,
        )
        faccao_status = {
            row['status_faccao']: row['total']
            for row in faccoes.values('status_faccao').annotate(total=Count('id'))
        }
        custos = qs.aggregate(
            previsto=Sum('custo_previsto'),
            real=Sum('custo_real'),
        )
        custo_previsto_total = custos['previsto'] or Decimal('0')
        custo_real_total = custos['real'] or Decimal('0')
        variacao_custo_total = custo_real_total - custo_previsto_total
        recentes = [
            {
                'id': ordem.id,
                'numero': ordem.numero,
                'produto': ordem.produto_final.descricao if ordem.produto_final_id else '',
                'referencia': ordem.produto_final.referencia if ordem.produto_final_id else '',
                'quantidade': ordem.quantidade,
                'status': ordem.status,
                'custo_previsto': ordem.custo_previsto,
                'custo_real': ordem.custo_real,
                'variacao_custo': Decimal(ordem.custo_real or 0) - Decimal(ordem.custo_previsto or 0),
                'data_emissao': ordem.data_emissao,
            }
            for ordem in qs.select_related('produto_final')[:8]
        ]
        pendencias_faccao = [
            {
                'op': item.ordem.numero,
                'produto': item.ordem.produto_final.descricao if item.ordem.produto_final_id else '',
                'fornecedor': item.fornecedor.nome_fornecedor if item.fornecedor_id else '',
                'servico': item.descricao or 'Serviço de facção',
                'quantidade': item.quantidade_necessaria,
                'status': item.status_faccao,
                'documento': item.documento_faccao or '',
            }
            for item in faccoes
                .exclude(status_faccao=OrdemProducaoItem.STATUS_FACCAO_RETORNADO)
                .select_related('ordem', 'ordem__produto_final', 'fornecedor')
                .order_by('ordem__data_emissao', 'id')[:8]
        ]
        alertas_insumos = []
        ops_ativas = qs.filter(status__in=[
            OrdemProducao.STATUS_ABERTA,
            OrdemProducao.STATUS_APROVADA,
            OrdemProducao.STATUS_EM_PRODUCAO,
        ])
        primeira_op = ops_ativas.select_related('empresa').first()
        loja_central = None
        if primeira_op:
            try:
                loja_central = self._loja_central_producao(primeira_op)
            except ValidationError:
                loja_central = None

        if loja_central:
            necessidades = (
                OrdemProducaoItem.objects
                .filter(
                    ordem__in=ops_ativas,
                    tipo__in=[FichaTecnicaItem.TIPO_INSUMO, FichaTecnicaItem.TIPO_AVIAMENTO],
                    produto__isnull=False,
                )
                .values('produto_id', 'produto__descricao', 'produto__referencia')
                .annotate(necessario=Sum('quantidade_necessaria'))
                .order_by('produto__descricao')
            )
            for row in necessidades:
                produto_stub = type('ProdutoCodigo', (), {
                    'pk': row['produto_id'],
                    'referencia': row['produto__referencia'],
                })()
                codigo = self._codigo_estoque_produto(produto_stub)
                estoque = Estoque.objects.filter(CodigodeBarra=codigo, Idloja=loja_central).first()
                saldo = Decimal(estoque.Estoque or 0) if estoque else Decimal('0')
                necessario = Decimal(row['necessario'] or 0)
                falta = necessario - saldo
                if falta > 0:
                    alertas_insumos.append({
                        'produto': row['produto__descricao'] or '',
                        'referencia': row['produto__referencia'] or '',
                        'necessario': necessario,
                        'saldo': saldo,
                        'falta': falta,
                        'loja_central': loja_central.nome_loja,
                    })
        return Response({
            'totais': {
                'abertas': por_status.get(OrdemProducao.STATUS_ABERTA, 0),
                'aprovadas': por_status.get(OrdemProducao.STATUS_APROVADA, 0),
                'em_producao': por_status.get(OrdemProducao.STATUS_EM_PRODUCAO, 0),
                'finalizadas': por_status.get(OrdemProducao.STATUS_FINALIZADA, 0),
                'canceladas': por_status.get(OrdemProducao.STATUS_CANCELADA, 0),
                'custo_previsto': custo_previsto_total,
                'custo_real': custo_real_total,
                'variacao_custo': variacao_custo_total,
            },
            'faccao': {
                'pendentes': faccao_status.get(OrdemProducaoItem.STATUS_FACCAO_PENDENTE, 0),
                'enviadas': faccao_status.get(OrdemProducaoItem.STATUS_FACCAO_ENVIADO, 0),
                'retornadas': faccao_status.get(OrdemProducaoItem.STATUS_FACCAO_RETORNADO, 0),
            },
            'recentes': recentes,
            'pendencias_faccao': pendencias_faccao,
            'alertas_insumos': alertas_insumos[:12],
        })

    def _validar_estoque_insumos(self, ordem):
        loja = self._loja_central_producao(ordem)
        faltas = []
        itens = (
            ordem.itens
            .select_related('produto')
            .filter(tipo__in=[FichaTecnicaItem.TIPO_INSUMO, FichaTecnicaItem.TIPO_AVIAMENTO], produto__isnull=False)
        )
        for item in itens:
            quantidade = Decimal(item.quantidade_necessaria or 0).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)
            if quantidade <= 0:
                continue
            codigo = self._codigo_estoque_produto(item.produto)
            estoque = Estoque.objects.filter(CodigodeBarra=codigo, Idloja=loja).first()
            saldo = Decimal(estoque.Estoque or 0) if estoque else Decimal('0')
            falta = quantidade - saldo
            if falta > 0:
                faltas.append({
                    'produto': item.produto.descricao or item.produto.referencia or str(item.produto_id),
                    'referencia': item.produto.referencia or '',
                    'codigo': codigo,
                    'loja': loja.nome_loja,
                    'necessario': quantidade,
                    'saldo': saldo,
                    'falta': falta,
                })
        return loja, faltas

    @action(detail=True, methods=['get'], url_path='validar-estoque')
    def validar_estoque(self, request, pk=None):
        ordem = self.get_object()
        try:
            loja, faltas = self._validar_estoque_insumos(ordem)
        except ValidationError as exc:
            raise exc
        return Response({
            'ok': not faltas,
            'loja': loja.nome_loja,
            'faltas': faltas,
        })

    @transaction.atomic
    def perform_create(self, serializer):
        super().perform_create(serializer)
        self._sincronizar_grade_ordem(serializer.instance)
        self._gerar_itens_ordem(serializer.instance)
        _audit('OrdemProducao', serializer.instance.pk, {'created': True}, self.request, 'create')

    @transaction.atomic
    def perform_update(self, serializer):
        ordem = self.get_object()
        if ordem.status != OrdemProducao.STATUS_ABERTA:
            raise ValidationError({'status': 'Somente OP aberta pode ser alterada.'})
        super().perform_update(serializer)
        self._sincronizar_grade_ordem(serializer.instance)
        ordem.itens.all().delete()
        self._gerar_itens_ordem(serializer.instance)
        _audit('OrdemProducao', serializer.instance.pk, {'updated': True}, self.request, 'update')

    def _sincronizar_grade_ordem(self, ordem):
        payload = self.request.data.get('grade_producao')
        ordem.grade_producao.all().delete()
        if payload is None:
            if ordem.sku_final_id and Decimal(ordem.quantidade or 0) > 0:
                OrdemProducaoGrade.objects.create(
                    ordem=ordem,
                    sku_final=ordem.sku_final,
                    quantidade=ordem.quantidade,
                )
            return

        linhas = []
        for linha in payload or []:
            sku_id = linha.get('sku_final') or linha.get('sku')
            qtd = Decimal(str(linha.get('quantidade') or 0))
            if qtd <= 0:
                continue
            linhas.append(OrdemProducaoGrade(
                ordem=ordem,
                sku_final_id=sku_id,
                quantidade=qtd,
            ))
        OrdemProducaoGrade.objects.bulk_create(linhas)

    def _gerar_itens_ordem(self, ordem):
        ficha = ordem.ficha_tecnica
        fator = Decimal(ordem.quantidade or 0) / Decimal(ficha.rendimento or 1)
        total_previsto = Decimal('0')
        itens = []
        for ficha_item in ficha.itens.select_related('produto', 'fornecedor', 'unidade').all():
            qtd_necessaria = Decimal(ficha_item.quantidade_com_perda or 0) * fator
            unidade = ficha_item.unidade
            if unidade and not unidade.permite_decimal and qtd_necessaria != qtd_necessaria.to_integral_value():
                item_nome = ficha_item.produto.descricao if ficha_item.produto_id else (ficha_item.descricao or ficha_item.fornecedor)
                raise ValidationError({
                    'quantidade': (
                        f'O item {item_nome} usa a unidade {unidade.Descricao}, que não aceita decimal. '
                        f'A quantidade calculada para a OP foi {qtd_necessaria}. Ajuste a quantidade, perda ou rendimento da ficha.'
                    )
                })
            custo_usado = Decimal(ficha_item.custo_unitario_usado or 0)
            custo_total = _money(qtd_necessaria * custo_usado)
            itens.append(OrdemProducaoItem(
                ordem=ordem,
                ficha_item=ficha_item,
                tipo=ficha_item.tipo,
                produto=ficha_item.produto,
                fornecedor=ficha_item.fornecedor,
                descricao=ficha_item.descricao,
                unidade=ficha_item.unidade,
                quantidade_base=ficha_item.quantidade,
                perda_percentual=ficha_item.perda_percentual,
                quantidade_necessaria=qtd_necessaria,
                custo_unitario_previsto=custo_usado,
                custo_unitario_real=custo_usado,
                custo_total_previsto=custo_total,
                custo_total_real=custo_total,
                observacoes=ficha_item.observacoes,
                ordem_linha=ficha_item.ordem,
            ))
            total_previsto += custo_total

        OrdemProducaoItem.objects.bulk_create(itens)
        ordem.custo_previsto = _money(total_previsto)
        ordem.custo_real = _money(total_previsto)
        ordem.save(update_fields=['custo_previsto', 'custo_real', 'atualizado_em'])

    def _loja_central_producao(self, ordem):
        loja = (
            Loja.objects
            .filter(empresa=ordem.empresa, ativo=True, tipo_unidade=Loja.TIPO_FABRICA)
            .order_by('id')
            .first()
        )
        if loja:
            return loja
        loja = (
            Loja.objects
            .filter(empresa=ordem.empresa, ativo=True, tipo_unidade=Loja.TIPO_MATRIZ)
            .order_by('id')
            .first()
        )
        if loja:
            return loja
        loja = (
            Loja.objects
            .filter(empresa=ordem.empresa, ativo=True, Matriz='SIM')
            .order_by('id')
            .first()
        )
        if loja:
            return loja
        loja = Loja.objects.filter(empresa=ordem.empresa, ativo=True).order_by('id').first()
        if not loja:
            raise ValidationError({'loja': 'Cadastre uma fábrica ou matriz/estoque central ativo para receber a produção.'})
        return loja

    def _codigo_estoque_produto(self, produto):
        referencia_numerica = ''.join(ch for ch in str(produto.referencia or '') if ch.isdigit())
        if len(referencia_numerica) == 13:
            return referencia_numerica
        return f"29{int(produto.pk) % 100000000000:011d}"

    def _custo_unitario_insumo(self, item):
        produto = item.produto
        return _q4(
            item.custo_unitario_real
            or item.custo_unitario_previsto
            or getattr(produto, 'custo_medio', 0)
            or getattr(produto, 'custo_ultima_compra', 0)
            or getattr(produto, 'custo_original', 0)
            or 0
        )

    def _baixar_insumos_ordem(self, ordem, loja):
        itens = (
            ordem.itens
            .select_related('produto')
            .filter(tipo__in=[FichaTecnicaItem.TIPO_INSUMO, FichaTecnicaItem.TIPO_AVIAMENTO], produto__isnull=False)
        )
        movimentos = 0
        for item in itens:
            quantidade = Decimal(item.quantidade_necessaria or 0).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)
            if quantidade <= 0:
                continue

            produto = item.produto
            codigo = self._codigo_estoque_produto(produto)
            custo_unitario = self._custo_unitario_insumo(item)
            custo_total = _money(quantidade * custo_unitario)

            estoque, _ = Estoque.objects.select_for_update().get_or_create(
                CodigodeBarra=codigo,
                Idloja=loja,
                defaults={
                    'referencia': produto.referencia or '',
                    'Estoque': Decimal('0'),
                    'reserva': Decimal('0'),
                },
            )
            anterior = Decimal(estoque.Estoque or 0)
            posterior = anterior - quantidade
            if posterior < 0 and (loja.EstoqueNegativo or 'NAO').upper() != 'SIM':
                nome = produto.descricao or produto.referencia or produto.pk
                raise ValidationError({
                    'estoque': (
                        f'Saldo insuficiente do insumo {nome} na fábrica/estoque central. '
                        f'Saldo atual: {anterior}; necessário: {quantidade}.'
                    )
                })

            estoque.Estoque = posterior
            estoque.referencia = produto.referencia or estoque.referencia
            estoque.reserva = estoque.reserva or 0
            estoque.save(update_fields=['Estoque', 'referencia', 'reserva'])

            item.custo_unitario_real = custo_unitario
            item.custo_total_real = custo_total
            item.save(update_fields=['custo_unitario_real', 'custo_total_real'])

            EstoqueMovimentacao.objects.create(
                Idloja=loja,
                CodigodeBarra=codigo,
                referencia=produto.referencia or '',
                tipo=EstoqueMovimentacao.TIPO_SAIDA,
                quantidade=quantidade,
                custo_unitario=custo_unitario,
                custo_total=custo_total,
                custo_medio_apos=custo_unitario,
                saldo_anterior=anterior,
                saldo_posterior=posterior,
                documento=ordem.numero,
                observacao=f'Baixa de insumo OP {ordem.numero}',
            )
            movimentos += 1

        return movimentos

    def _entrada_sku_produto_acabado(self, ordem, loja, sku, quantidade, custo_unitario):
        custo_total_linha = _money(Decimal(quantidade or 0) * Decimal(custo_unitario or 0))
        if quantidade <= 0:
            raise ValidationError({'quantidade': 'Quantidade produzida deve ser maior que zero.'})

        estoque, _ = Estoque.objects.select_for_update().get_or_create(
            CodigodeBarra=sku.ean13,
            Idloja=loja,
            defaults={'referencia': ordem.produto_final.referencia or '', 'Estoque': Decimal('0'), 'reserva': Decimal('0')},
        )
        anterior = Decimal(estoque.Estoque or 0)
        posterior = anterior + quantidade

        custo_atual = Decimal(sku.custo_medio or sku.custo_ultima_compra or sku.custo_original or 0)
        base_anterior = anterior if anterior > 0 else Decimal('0')
        custo_medio_apos = custo_unitario
        if posterior > 0:
            custo_medio_apos = (
                ((base_anterior * custo_atual) + (quantidade * custo_unitario)) / posterior
            ).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)

        estoque.Estoque = posterior
        estoque.referencia = ordem.produto_final.referencia or ''
        estoque.save(update_fields=['Estoque', 'referencia'])

        sku.custo_ultima_compra = custo_unitario
        sku.custo_medio = custo_medio_apos
        if not Decimal(sku.custo_original or 0):
            sku.custo_original = custo_unitario
        sku.save(update_fields=['custo_ultima_compra', 'custo_medio', 'custo_original'])
        self._atualizar_custo_produto_acabado(ordem.produto_final)

        EstoqueMovimentacao.objects.create(
            Idloja=loja,
            CodigodeBarra=sku.ean13,
            referencia=ordem.produto_final.referencia or '',
            tipo=EstoqueMovimentacao.TIPO_ENTRADA,
            quantidade=quantidade,
            custo_unitario=custo_unitario,
            custo_total=custo_total_linha,
            custo_medio_apos=custo_medio_apos,
            saldo_anterior=anterior,
            saldo_posterior=posterior,
            documento=ordem.numero,
            observacao=f'Entrada de produto acabado OP {ordem.numero}',
        )

    def _atualizar_custo_produto_acabado(self, produto):
        custos = (
            ProdutoDetalhe.objects
            .filter(produto=produto, custo_medio__gt=0)
            .values_list('custo_medio', flat=True)
        )
        custos = [Decimal(custo or 0) for custo in custos if Decimal(custo or 0) > 0]
        if not custos:
            return
        custo_medio = (sum(custos, Decimal('0')) / Decimal(len(custos))).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
        produto.custo_ultima_compra = custo_medio
        produto.custo_medio = custo_medio
        if not Decimal(produto.custo_original or 0):
            produto.custo_original = custo_medio
        produto.save(update_fields=['custo_ultima_compra', 'custo_medio', 'custo_original'])

    def _entrada_produto_acabado(self, ordem):
        grade = list(ordem.grade_producao.select_related('sku_final', 'sku_final__produto').all())
        if not grade and ordem.sku_final_id:
            grade = [OrdemProducaoGrade(ordem=ordem, sku_final=ordem.sku_final, quantidade=ordem.quantidade)]
        if not grade:
            raise ValidationError({'grade_producao': 'Informe a grade de SKUs produzidos antes de finalizar a OP.'})

        loja = self._loja_central_producao(ordem)
        quantidade_total = sum(Decimal(linha.quantidade or 0) for linha in grade)
        if quantidade_total <= 0:
            raise ValidationError({'quantidade': 'Quantidade produzida deve ser maior que zero.'})

        custo_total = Decimal(ordem.custo_real or ordem.custo_previsto or 0)
        custo_unitario = (custo_total / quantidade_total).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)

        for linha in grade:
            if linha.sku_final.produto_id != ordem.produto_final_id:
                raise ValidationError({'grade_producao': 'Todos os SKUs produzidos devem pertencer ao produto da OP.'})
            self._entrada_sku_produto_acabado(
                ordem=ordem,
                loja=loja,
                sku=linha.sku_final,
                quantidade=Decimal(linha.quantidade or 0),
                custo_unitario=custo_unitario,
            )

    @action(detail=True, methods=['post'], url_path='aprovar')
    def aprovar(self, request, pk=None):
        ordem = self.get_object()
        if ordem.status != OrdemProducao.STATUS_ABERTA:
            return Response({'detail': 'Somente OP aberta pode ser aprovada.'}, status=status.HTTP_400_BAD_REQUEST)
        if not ordem.itens.exists():
            return Response({'detail': 'A OP não possui itens calculados.'}, status=status.HTTP_400_BAD_REQUEST)
        ordem.status = OrdemProducao.STATUS_APROVADA
        ordem.save(update_fields=['status', 'atualizado_em'])
        _audit('OrdemProducao', ordem.pk, {'status': ordem.status}, request, 'approve')
        return Response(self.get_serializer(ordem).data)

    @action(detail=True, methods=['post'], url_path='iniciar')
    def iniciar(self, request, pk=None):
        ordem = self.get_object()
        if ordem.status != OrdemProducao.STATUS_APROVADA:
            return Response({'detail': 'Somente OP aprovada pode ser iniciada.'}, status=status.HTTP_400_BAD_REQUEST)
        ordem.status = OrdemProducao.STATUS_EM_PRODUCAO
        ordem.data_inicio = timezone.now()
        ordem.save(update_fields=['status', 'data_inicio', 'atualizado_em'])
        _audit('OrdemProducao', ordem.pk, {'status': ordem.status}, request, 'start')
        return Response(self.get_serializer(ordem).data)

    @action(detail=True, methods=['post'], url_path='finalizar')
    @transaction.atomic
    def finalizar(self, request, pk=None):
        ordem = self.get_object()
        if ordem.status not in (OrdemProducao.STATUS_APROVADA, OrdemProducao.STATUS_EM_PRODUCAO):
            return Response({'detail': 'Somente OP aprovada ou em produção pode ser finalizada.'}, status=status.HTTP_400_BAD_REQUEST)
        faccoes_pendentes = ordem.itens.filter(
            tipo=FichaTecnicaItem.TIPO_SERVICO,
        ).exclude(status_faccao=OrdemProducaoItem.STATUS_FACCAO_RETORNADO)
        if faccoes_pendentes.exists():
            return Response({
                'detail': (
                    'Antes de finalizar a OP, registre o retorno de todos os serviços/facções. '
                    'Esses retornos confirmam a quantidade executada e o custo real da produção.'
                )
            }, status=status.HTTP_400_BAD_REQUEST)
        loja = self._loja_central_producao(ordem)
        movimentos_insumos = self._baixar_insumos_ordem(ordem, loja)
        ordem.recalcular_totais()
        self._entrada_produto_acabado(ordem)
        ordem.status = OrdemProducao.STATUS_FINALIZADA
        ordem.data_finalizacao = timezone.now()
        ordem.save(update_fields=['status', 'data_finalizacao', 'atualizado_em'])
        _audit('OrdemProducao', ordem.pk, {'status': ordem.status, 'baixas_insumos': movimentos_insumos}, request, 'finish')
        data = self.get_serializer(ordem).data
        data['baixas_insumos'] = movimentos_insumos
        return Response(data)

    @action(detail=True, methods=['post'], url_path='distribuir')
    @transaction.atomic
    def distribuir(self, request, pk=None):
        from distribuicao.serializers import DistribuicaoSerializer
        from distribuicao.services import preparar_distribuicao_producao
        from distribuicao.models import PerfilDistribuicao

        ordem = self.get_object()
        perfil = None
        perfil_id = request.data.get('perfil')
        if perfil_id:
            try:
                perfil = PerfilDistribuicao.objects.get(pk=perfil_id, empresa=ordem.empresa, ativo=True)
            except PerfilDistribuicao.DoesNotExist:
                return Response({'detail': 'Perfil de distribuição não encontrado para esta empresa.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            distribuicao = preparar_distribuicao_producao(ordem, perfil=perfil, user=request.user)
            return Response(DistribuicaoSerializer(distribuicao).data, status=status.HTTP_200_OK)
        except Exception as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='distribuir-direto-legado')
    @transaction.atomic
    def distribuir_direto_legado(self, request, pk=None):
        ordem = self.get_object()
        if ordem.status != OrdemProducao.STATUS_FINALIZADA:
            return Response({'detail': 'Somente OP finalizada pode ser distribuída.'}, status=status.HTTP_400_BAD_REQUEST)

        loja_origem = self._loja_central_producao(ordem)
        loja_destino_id = request.data.get('loja_destino')
        if not loja_destino_id:
            return Response({'detail': 'Informe a loja de destino.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            loja_destino = Loja.objects.get(pk=loja_destino_id, empresa=ordem.empresa, ativo=True)
        except Loja.DoesNotExist:
            return Response({'detail': 'Loja de destino não encontrada para esta empresa.'}, status=status.HTTP_400_BAD_REQUEST)
        if loja_destino.pk == loja_origem.pk:
            return Response({'detail': 'A loja de destino deve ser diferente da fábrica/estoque central.'}, status=status.HTTP_400_BAD_REQUEST)

        itens = request.data.get('itens') or []
        if not isinstance(itens, list):
            return Response({'detail': 'Informe uma lista de SKUs e quantidades para distribuir.'}, status=status.HTTP_400_BAD_REQUEST)

        documento = (request.data.get('documento') or '').strip()
        if not documento:
            documento = f"DIST-{ordem.numero}-{timezone.now().strftime('%Y%m%d%H%M%S')}"
        documento = documento[:50]
        if EstoqueMovimentacao.objects.filter(documento=documento, observacao__icontains=f'Distribuição OP {ordem.numero}').exists():
            return Response({'detail': 'Já existe uma distribuição registrada com este documento.'}, status=status.HTTP_400_BAD_REQUEST)

        grade = {linha.sku_final_id: Decimal(linha.quantidade or 0) for linha in ordem.grade_producao.all()}
        distribuido_antes = {}
        movs_op = EstoqueMovimentacao.objects.filter(
            observacao__icontains=f'Distribuição OP {ordem.numero}',
            tipo=EstoqueMovimentacao.TIPO_ENTRADA,
        ).values_list('CodigodeBarra', 'quantidade')
        for ean, qtd in movs_op:
            distribuido_antes[ean] = distribuido_antes.get(ean, Decimal('0')) + Decimal(qtd or 0)

        movimentos = 0
        total_qtd = Decimal('0')
        nfe_itens = []
        for linha in itens:
            sku_id = linha.get('sku_final') or linha.get('sku')
            quantidade = Decimal(str(linha.get('quantidade') or 0)).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)
            if quantidade <= 0:
                continue
            try:
                sku = ProdutoDetalhe.objects.select_related('produto').get(pk=sku_id, produto=ordem.produto_final)
            except ProdutoDetalhe.DoesNotExist:
                return Response({'detail': f'SKU {sku_id} não pertence ao produto produzido.'}, status=status.HTTP_400_BAD_REQUEST)

            qtd_produzida = grade.get(sku.pk, Decimal('0'))
            qtd_ja_distribuida = distribuido_antes.get(sku.ean13, Decimal('0'))
            if qtd_produzida and (qtd_ja_distribuida + quantidade) > qtd_produzida:
                return Response({
                    'detail': (
                        f'Quantidade distribuída do SKU {sku.ean13} excede a quantidade produzida. '
                        f'Produzido: {qtd_produzida}; já distribuído: {qtd_ja_distribuida}; solicitado: {quantidade}.'
                    )
                }, status=status.HTTP_400_BAD_REQUEST)

            estoque_origem, _ = Estoque.objects.select_for_update().get_or_create(
                CodigodeBarra=sku.ean13,
                Idloja=loja_origem,
                defaults={'referencia': ordem.produto_final.referencia or '', 'Estoque': Decimal('0'), 'reserva': Decimal('0')},
            )
            origem_anterior = Decimal(estoque_origem.Estoque or 0)
            origem_posterior = origem_anterior - quantidade
            if origem_posterior < 0 and (loja_origem.EstoqueNegativo or 'NAO').upper() != 'SIM':
                return Response({
                    'detail': f'Saldo insuficiente na fábrica para o SKU {sku.ean13}. Saldo: {origem_anterior}; solicitado: {quantidade}.'
                }, status=status.HTTP_400_BAD_REQUEST)

            estoque_destino, _ = Estoque.objects.select_for_update().get_or_create(
                CodigodeBarra=sku.ean13,
                Idloja=loja_destino,
                defaults={'referencia': ordem.produto_final.referencia or '', 'Estoque': Decimal('0'), 'reserva': Decimal('0')},
            )
            destino_anterior = Decimal(estoque_destino.Estoque or 0)
            destino_posterior = destino_anterior + quantidade

            custo_unitario = _q4(sku.custo_medio or sku.custo_ultima_compra or sku.custo_original or 0)
            custo_total = _money(quantidade * custo_unitario)

            estoque_origem.Estoque = origem_posterior
            estoque_origem.referencia = ordem.produto_final.referencia or estoque_origem.referencia
            estoque_origem.reserva = estoque_origem.reserva or 0
            estoque_origem.save(update_fields=['Estoque', 'referencia', 'reserva'])

            estoque_destino.Estoque = destino_posterior
            estoque_destino.referencia = ordem.produto_final.referencia or estoque_destino.referencia
            estoque_destino.reserva = estoque_destino.reserva or 0
            estoque_destino.save(update_fields=['Estoque', 'referencia', 'reserva'])

            obs = f'Distribuição OP {ordem.numero} para {loja_destino.nome_loja}'
            EstoqueMovimentacao.objects.create(
                Idloja=loja_origem,
                CodigodeBarra=sku.ean13,
                referencia=ordem.produto_final.referencia or '',
                tipo=EstoqueMovimentacao.TIPO_SAIDA,
                quantidade=quantidade,
                custo_unitario=custo_unitario,
                custo_total=custo_total,
                custo_medio_apos=custo_unitario,
                saldo_anterior=origem_anterior,
                saldo_posterior=origem_posterior,
                documento=documento,
                observacao=obs,
            )
            EstoqueMovimentacao.objects.create(
                Idloja=loja_destino,
                CodigodeBarra=sku.ean13,
                referencia=ordem.produto_final.referencia or '',
                tipo=EstoqueMovimentacao.TIPO_ENTRADA,
                quantidade=quantidade,
                custo_unitario=custo_unitario,
                custo_total=custo_total,
                custo_medio_apos=custo_unitario,
                saldo_anterior=destino_anterior,
                saldo_posterior=destino_posterior,
                documento=documento,
                observacao=f'Distribuição OP {ordem.numero} recebida de {loja_origem.nome_loja}',
            )
            nfe_itens.append({
                'sku': sku,
                'quantidade': quantidade,
                'valor_unitario': custo_unitario,
                'cfop': self._cfop_transferencia(ordem.empresa),
            })
            distribuido_antes[sku.ean13] = qtd_ja_distribuida + quantidade
            movimentos += 2
            total_qtd += quantidade

        if movimentos == 0:
            return Response({'detail': 'Informe ao menos um SKU com quantidade maior que zero.'}, status=status.HTTP_400_BAD_REQUEST)

        nfe = self._criar_nfe_distribuicao(ordem, loja_origem, loja_destino, documento, nfe_itens, request)
        _audit('OrdemProducao', ordem.pk, {
            'documento': documento,
            'loja_destino': loja_destino.pk,
            'quantidade': str(total_qtd),
            'nfe_saida': nfe.pk,
            'nfe_numero': nfe.numero,
        }, request, 'distribute')
        return Response({
            'documento': documento,
            'movimentos': movimentos,
            'quantidade': str(total_qtd),
            'loja_origem': loja_origem.nome_loja,
            'loja_destino': loja_destino.nome_loja,
            'nfe_saida': nfe.pk,
            'nfe_numero': nfe.numero,
            'nfe_serie': nfe.serie,
            'nfe_status': nfe.status,
        }, status=status.HTTP_200_OK)

    def _cfop_transferencia(self, empresa):
        cfop = (
            Cfop.objects
            .filter(empresa=empresa, ativo=True, tipo_operacao=Cfop.TIPO_TRANSFERENCIA, codigo__in=['5152', '5949'])
            .order_by('codigo')
            .values_list('codigo', flat=True)
            .first()
        )
        return cfop or '5152'

    def _proximo_numero_nfe(self, loja_origem):
        serie = str(loja_origem.serie_nfe or 1)
        numero = str(loja_origem.proximo_numero_nfe or 1)
        loja_origem.proximo_numero_nfe = int(loja_origem.proximo_numero_nfe or 1) + 1
        loja_origem.save(update_fields=['proximo_numero_nfe'])
        return serie, numero

    def _criar_nfe_distribuicao(self, ordem, loja_origem, loja_destino, documento, itens, request):
        existente = NotaFiscalSaida.objects.filter(
            empresa=ordem.empresa,
            ordem_producao=ordem,
            documento_origem=documento,
        ).first()
        if existente:
            return existente

        serie, numero = self._proximo_numero_nfe(loja_origem)
        hoje = timezone.localdate()
        cfop_padrao = itens[0]['cfop'] if itens else self._cfop_transferencia(ordem.empresa)
        nfe = NotaFiscalSaida.objects.create(
            empresa=ordem.empresa,
            loja_origem=loja_origem,
            loja_destino=loja_destino,
            ordem_producao=ordem,
            tipo_operacao=NotaFiscalSaida.TipoOperacao.TRANSFERENCIA,
            modelo='55',
            serie=serie,
            numero=numero,
            documento_origem=documento,
            cfop=cfop_padrao,
            natureza_operacao='Transferência de produção',
            status=NotaFiscalSaida.Status.DIGITADA,
            dt_emissao=hoje,
            dt_saida=hoje,
            observacoes=f'NF-e gerada pela distribuição da OP {ordem.numero} para {loja_destino.nome_loja}',
            criado_por=getattr(request, 'user', None) if getattr(request, 'user', None) and request.user.is_authenticated else None,
        )
        for item in itens:
            sku = item['sku']
            produto = sku.produto
            NotaFiscalSaidaItem.objects.create(
                nota=nfe,
                produto=produto,
                sku=sku,
                ean=sku.ean13,
                referencia=produto.referencia or '',
                descricao=produto.descricao or '',
                cor=getattr(sku.idcor, 'Descricao', '') or '',
                tamanho=getattr(sku.idtamanho, 'Tamanho', '') or '',
                ncm=produto.ncm or '',
                cfop=item['cfop'],
                quantidade=item['quantidade'],
                valor_unitario=item['valor_unitario'],
            )
        nfe.recalcular_totais()
        return nfe

    @action(detail=True, methods=['post'], url_path='cancelar')
    def cancelar(self, request, pk=None):
        ordem = self.get_object()
        if ordem.status == OrdemProducao.STATUS_FINALIZADA:
            return Response({'detail': 'OP finalizada não pode ser cancelada nesta etapa.'}, status=status.HTTP_400_BAD_REQUEST)
        ordem.status = OrdemProducao.STATUS_CANCELADA
        ordem.save(update_fields=['status', 'atualizado_em'])
        _audit('OrdemProducao', ordem.pk, {'status': ordem.status}, request, 'cancel')
        return Response(self.get_serializer(ordem).data)


class OrdemProducaoItemViewSet(BaseViewSet):
    permission_classes = [HasModuleRole, HasEmpresaModulo]
    empresa_modulo_field = 'usa_producao'
    required_module = "producao"
    queryset = OrdemProducaoItem.objects.select_related('ordem', 'produto', 'fornecedor', 'unidade', 'ficha_item')
    serializer_class = OrdemProducaoItemSerializer
    read_roles = ["Admin", "Diretor", "Gerente", "Auxiliar"]
    write_roles = ["Admin", "Diretor", "Gerente"]

    def create(self, request, *args, **kwargs):
        return Response({'detail': 'Itens da OP são gerados pela ficha técnica.'}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def update(self, request, *args, **kwargs):
        return Response({'detail': 'Itens da OP são gerados pela ficha técnica.'}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def partial_update(self, request, *args, **kwargs):
        return Response({'detail': 'Itens da OP são gerados pela ficha técnica.'}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def destroy(self, request, *args, **kwargs):
        return Response({'detail': 'Itens da OP são gerados pela ficha técnica.'}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def _validar_item_faccao(self, item):
        if item.tipo != FichaTecnicaItem.TIPO_SERVICO:
            raise ValidationError({'tipo': 'Somente itens de serviço/facção podem usar este controle.'})
        if item.ordem.status in (OrdemProducao.STATUS_CANCELADA, OrdemProducao.STATUS_FINALIZADA):
            raise ValidationError({'status': 'OP cancelada ou finalizada não permite alterar facção.'})

    @action(detail=True, methods=['post'], url_path='enviar-faccao')
    @transaction.atomic
    def enviar_faccao(self, request, pk=None):
        item = self.get_object()
        self._validar_item_faccao(item)
        quantidade = Decimal(str(request.data.get('quantidade') or item.quantidade_necessaria or 0))
        if quantidade <= 0:
            return Response({'detail': 'Informe quantidade enviada maior que zero.'}, status=status.HTTP_400_BAD_REQUEST)

        item.status_faccao = OrdemProducaoItem.STATUS_FACCAO_ENVIADO
        item.quantidade_enviada_faccao = quantidade
        item.documento_faccao = (request.data.get('documento') or item.documento_faccao or '').strip()[:50] or None
        data_envio = request.data.get('data_envio')
        item.data_envio_faccao = data_envio or timezone.localdate()
        item.save(update_fields=[
            'status_faccao',
            'quantidade_enviada_faccao',
            'documento_faccao',
            'data_envio_faccao',
        ])
        _audit('OrdemProducaoItem', item.pk, {'status_faccao': item.status_faccao}, request, 'send_faccao')
        return Response(self.get_serializer(item).data)

    @action(detail=True, methods=['post'], url_path='retornar-faccao')
    @transaction.atomic
    def retornar_faccao(self, request, pk=None):
        item = self.get_object()
        self._validar_item_faccao(item)
        if item.status_faccao == OrdemProducaoItem.STATUS_FACCAO_PENDENTE:
            return Response({'detail': 'Envie o item para facção antes de registrar o retorno.'}, status=status.HTTP_400_BAD_REQUEST)

        quantidade = Decimal(str(request.data.get('quantidade') or item.quantidade_enviada_faccao or item.quantidade_necessaria or 0))
        if quantidade <= 0:
            return Response({'detail': 'Informe quantidade retornada maior que zero.'}, status=status.HTTP_400_BAD_REQUEST)
        enviada = Decimal(item.quantidade_enviada_faccao or 0)
        if enviada > 0 and quantidade > enviada:
            return Response({'detail': 'Quantidade retornada não pode ser maior que a quantidade enviada.'}, status=status.HTTP_400_BAD_REQUEST)

        custo_unitario = _q4(request.data.get('custo_unitario_real') or item.custo_unitario_real or item.custo_unitario_previsto or 0)
        item.status_faccao = OrdemProducaoItem.STATUS_FACCAO_RETORNADO
        item.quantidade_retornada_faccao = quantidade
        item.data_retorno_faccao = request.data.get('data_retorno') or timezone.localdate()
        item.custo_unitario_real = custo_unitario
        item.custo_total_real = _money(Decimal(item.quantidade_necessaria or 0) * custo_unitario)
        item.save(update_fields=[
            'status_faccao',
            'quantidade_retornada_faccao',
            'data_retorno_faccao',
            'custo_unitario_real',
            'custo_total_real',
        ])
        item.ordem.recalcular_totais()
        _audit('OrdemProducaoItem', item.pk, {'status_faccao': item.status_faccao, 'custo_unitario_real': str(custo_unitario)}, request, 'return_faccao')
        return Response(self.get_serializer(item).data)

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = getattr(self.request.user, 'empresa_id', None)
        empresa_param = self.request.query_params.get('empresa')
        if self.request.user.is_superuser and empresa_param:
            qs = qs.filter(ordem__empresa_id=empresa_param)
        elif not self.request.user.is_superuser and empresa_id:
            qs = qs.filter(ordem__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            qs = qs.none()

        ordem = self.request.query_params.get('ordem')
        if ordem:
            qs = qs.filter(ordem_id=ordem)
        return qs

    def _save_with_empresa_scope(self, serializer):
        serializer.save()


class PromocaoViewSet(BaseViewSet):
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa", "Vendedor"]
    write_roles = ["Admin", "Diretor", "Gerente"]
    queryset = (
        Promocao.objects
        .prefetch_related('lojas', 'produtos', 'colecoes', 'grupos', 'subgrupos')
        .all()
    )
    serializer_class = PromocaoSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        ativo = self.request.query_params.get('ativo')
        loja = self.request.query_params.get('loja')
        hoje = timezone.localdate()
        if ativo in ('true', '1'):
            qs = qs.filter(ativo=True, data_inicio__lte=hoje).filter(Q(data_fim__isnull=True) | Q(data_fim__gte=hoje))
        elif ativo in ('false', '0'):
            qs = qs.filter(ativo=False)
        return qs

    @action(detail=False, methods=['get'], url_path='aplicaveis')
    def aplicaveis(self, request):
        loja_id = request.query_params.get('loja')
        produto_ids = [
            int(value)
            for value in request.query_params.getlist('produto')
            if str(value).isdigit()
        ]
        if not produto_ids:
            raw = request.query_params.get('produtos') or ''
            produto_ids = [int(value) for value in raw.split(',') if value.strip().isdigit()]
        if not produto_ids:
            return Response({'results': []})

        produtos = Produto.objects.filter(pk__in=produto_ids).select_related('colecao', 'grupo', 'subgrupo')
        promocoes = list(self.get_queryset().filter(ativo=True).order_by('prioridade', '-data_inicio', 'Idpromocao'))
        payload = []
        for produto in produtos:
            melhor = None
            for promocao in promocoes:
                if loja_id and promocao.lojas.exists() and not promocao.lojas.filter(pk=loja_id).exists():
                    continue
                if promocao.aplica_produto(produto):
                    melhor = promocao
                    break
            if not melhor:
                continue
            payload.append({
                'produto': produto.pk,
                'promocao': melhor.pk,
                'nome': melhor.nome,
                'tipo': melhor.tipo,
                'valor': str(melhor.valor),
                'prioridade': melhor.prioridade,
                'acumula_cashback': melhor.acumula_cashback,
            })
        return Response({'results': payload})


class PackViewSet(BaseViewSet):
    queryset = Pack.objects.all().order_by('-data_cadastro')
    serializer_class = PackSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        grade = self.request.query_params.get('grade')
        ativo = self.request.query_params.get('ativo')
        search = (self.request.query_params.get('search') or '').strip()
        ordering = self.request.query_params.get('ordering')
        if grade:
            qs = qs.filter(grade_id=grade)
        if ativo is not None and ativo != '':
            qs = qs.filter(ativo=str(ativo).lower() in ('true', '1', 'sim'))
        if search:
            qs = qs.filter(nome__icontains=search)
        if ordering:
            qs = qs.order_by(ordering)
        return qs


class PackItemViewSet(BaseViewSet):
    queryset = PackItem.objects.all()
    serializer_class = PackItemSerializer

    def get_queryset(self):
        qs = PackItem.objects.all()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(pack__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        pack_id = self.request.query_params.get('pack')
        if pack_id:
            qs = qs.filter(pack_id=pack_id)
        ordering = self.request.query_params.get('ordering')
        if ordering:
            qs = qs.order_by(ordering)
        return qs


class EstoqueViewSet(BaseViewSet):
    required_module = "estoque"
    queryset = Estoque.objects.all()
    serializer_class = EstoqueSerializer

    def get_queryset(self):
        qs = Estoque.objects.all().order_by('referencia', 'CodigodeBarra', 'Idloja_id')
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(Idloja__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        loja = self.request.query_params.get('loja')
        referencia = self.request.query_params.get('referencia')
        ean = self.request.query_params.get('ean')
        colecao = self.request.query_params.get('colecao')
        estacao = self.request.query_params.get('estacao')
        search = self.request.query_params.get('search')
        if loja:
            qs = qs.filter(Idloja_id=loja)
        if referencia:
            qs = qs.filter(referencia__icontains=referencia)
        if ean:
            qs = qs.filter(CodigodeBarra__icontains=ean)
        if search:
            qs = qs.filter(Q(referencia__icontains=search) | Q(CodigodeBarra__icontains=search))
        if colecao or estacao:
            produto_qs = Produto.objects.all()
            if empresa_id:
                produto_qs = produto_qs.filter(empresa_id=empresa_id)
            if colecao:
                produto_qs = produto_qs.filter(colecao__Codigo=colecao)
            if estacao:
                produto_qs = produto_qs.filter(colecao__Estacao=estacao)
            refs = produto_qs.exclude(referencia__isnull=True).values_list('referencia', flat=True)
            qs = qs.filter(referencia__in=refs)
        return qs


class EstoqueMovimentacaoViewSet(BaseViewSet):
    required_module = "estoque"
    queryset = EstoqueMovimentacao.objects.all()
    serializer_class = EstoqueMovimentacaoSerializer

    def get_queryset(self):
        qs = EstoqueMovimentacao.objects.all()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(Idloja__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        loja = self.request.query_params.get('loja')
        referencia = self.request.query_params.get('referencia')
        ean = self.request.query_params.get('ean')
        tipo = self.request.query_params.get('tipo')
        search = self.request.query_params.get('search')
        if loja:
            qs = qs.filter(Idloja_id=loja)
        if referencia:
            qs = qs.filter(referencia__icontains=referencia)
        if ean:
            qs = qs.filter(CodigodeBarra__icontains=ean)
        if tipo:
            qs = qs.filter(tipo=tipo)
        if search:
            qs = qs.filter(Q(referencia__icontains=search) | Q(CodigodeBarra__icontains=search) | Q(documento__icontains=search))
        return qs

    @transaction.atomic
    def perform_create(self, serializer):
        ean = serializer.validated_data['CodigodeBarra']
        loja = serializer.validated_data['Idloja']
        empresa_id = getattr(self.request.user, "empresa_id", None)
        if not empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        if empresa_id and loja.empresa_id != empresa_id:
            raise ValidationError({"Idloja": "A loja informada pertence a outra empresa."})
        tipo = serializer.validated_data['tipo']
        qtd = serializer.validated_data['quantidade']
        sku = ProdutoDetalhe.objects.select_related('produto').filter(ean13=ean).first()
        referencia = serializer.validated_data.get('referencia') or (sku.produto.referencia if sku else '')

        estoque, _ = Estoque.objects.select_for_update().get_or_create(
            CodigodeBarra=ean,
            Idloja=loja,
            defaults={'referencia': referencia or '', 'Estoque': 0, 'reserva': 0},
        )
        anterior = estoque.Estoque or 0
        reserva = estoque.reserva or 0
        if tipo == EstoqueMovimentacao.TIPO_ENTRADA:
            posterior = anterior + abs(qtd)
        elif tipo == EstoqueMovimentacao.TIPO_SAIDA:
            posterior = anterior - abs(qtd)
        elif tipo == EstoqueMovimentacao.TIPO_RESERVA:
            posterior = anterior
            reserva = reserva + abs(qtd)
        else:
            posterior = qtd

        estoque.referencia = referencia or estoque.referencia
        estoque.Estoque = posterior
        estoque.reserva = reserva
        estoque.save(update_fields=['referencia', 'Estoque', 'reserva'])
        serializer.save(referencia=referencia or '', saldo_anterior=anterior, saldo_posterior=posterior)


class ProdutoUsoConsumoEstoqueViewSet(viewsets.ReadOnlyModelViewSet):
    required_module = "estoque"
    permission_classes = [HasModuleRole]
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa", "Vendedor", "Auxiliar", "Assistente", "Regular"]
    queryset = ProdutoUsoConsumoEstoque.objects.select_related("empresa", "produto", "produto__unidade", "loja")
    serializer_class = ProdutoUsoConsumoEstoqueSerializer

    def get_queryset(self):
        qs = self.queryset.filter(produto__tipo_produto="2").order_by("produto__referencia", "loja__nome_loja")
        user = self.request.user
        empresa_id = getattr(user, "empresa_id", None)
        empresa_param = self.request.query_params.get("empresa")
        if user.is_superuser and empresa_param:
            qs = qs.filter(empresa_id=empresa_param)
        elif empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not user.is_superuser:
            return qs.none()

        access = EffectiveAccessService(user)
        allowed = access.allowed_store_ids()
        if allowed is not None and not (user.is_superuser or access.is_company_master()):
            qs = qs.filter(loja_id__in=allowed)

        loja = self.request.query_params.get("loja")
        search = (self.request.query_params.get("search") or "").strip()
        saldo = self.request.query_params.get("saldo")
        if loja:
            qs = qs.filter(loja_id=loja)
        if search:
            qs = qs.filter(
                Q(produto__referencia__icontains=search)
                | Q(produto__descricao__icontains=search)
                | Q(produto__descricao_reduzida__icontains=search)
            )
        if saldo == "com_saldo":
            qs = qs.filter(saldo__gt=0)
        elif saldo == "zerados":
            qs = qs.filter(saldo=0)
        return qs


class InventarioEstoqueViewSet(BaseViewSet):
    required_module = "estoque"
    queryset = InventarioEstoque.objects.all()
    serializer_class = InventarioEstoqueSerializer

    def get_queryset(self):
        qs = InventarioEstoque.objects.prefetch_related('itens').select_related('Idloja')
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(Idloja__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        loja = self.request.query_params.get('loja')
        status_q = self.request.query_params.get('status')
        if loja:
            qs = qs.filter(Idloja_id=loja)
        if status_q:
            qs = qs.filter(status=status_q)
        return qs

    @action(detail=True, methods=['post'], url_path='gerar-itens')
    def gerar_itens(self, request, pk=None):
        inv = self.get_object()
        if inv.status != InventarioEstoque.STATUS_ABERTO:
            return Response({'detail': 'Somente inventário aberto pode gerar itens.'}, status=status.HTTP_400_BAD_REQUEST)
        estoques = Estoque.objects.filter(Idloja=inv.Idloja).order_by('referencia', 'CodigodeBarra')
        created = 0
        for est in estoques:
            _, was_created = InventarioEstoqueItem.objects.get_or_create(
                inventario=inv,
                CodigodeBarra=est.CodigodeBarra,
                defaults={
                    'referencia': est.referencia,
                    'saldo_sistema': est.Estoque or 0,
                    'saldo_contado': 0,
                    'contado': False,
                },
            )
            if was_created:
                created += 1
        return Response({'created': created})

    @action(detail=True, methods=['post'], url_path='validar')
    @transaction.atomic
    def validar(self, request, pk=None):
        inv = self.get_object()
        if inv.status != InventarioEstoque.STATUS_ABERTO:
            return Response({'detail': 'Somente inventário aberto pode ser validado.'}, status=status.HTTP_400_BAD_REQUEST)
        total_itens = inv.itens.count()
        if total_itens == 0:
            return Response({'detail': 'Gere os itens antes de validar o inventário.'}, status=status.HTTP_400_BAD_REQUEST)
        pendentes = inv.itens.filter(contado=False).count()
        if pendentes:
            return Response({'detail': f'Existem {pendentes} item(ns) sem contagem.'}, status=status.HTTP_400_BAD_REQUEST)
        inv.status = InventarioEstoque.STATUS_VALIDADO
        inv.save(update_fields=['status'])
        divergencias = inv.itens.exclude(diferenca=0).count()
        return Response({
            'inventario': self.get_serializer(inv).data,
            'total_itens': total_itens,
            'divergencias': divergencias,
            'diferenca_total': str(sum((item.diferenca or 0) for item in inv.itens.all())),
        })

    @action(detail=True, methods=['post'], url_path='fechar')
    def fechar(self, request, pk=None):
        return self._finalizar_inventario(request, pk)

    @action(detail=True, methods=['post'], url_path='finalizar')
    def finalizar(self, request, pk=None):
        return self._finalizar_inventario(request, pk)

    @transaction.atomic
    def _finalizar_inventario(self, request, pk=None):
        inv = self.get_object()
        if inv.status != InventarioEstoque.STATUS_VALIDADO:
            return Response({'detail': 'Valide o inventário antes de finalizar.'}, status=status.HTTP_400_BAD_REQUEST)
        documento = f'INV-{inv.pk}'
        movimentos = 0
        for item in inv.itens.all():
            if item.diferenca == 0:
                continue
            mov_tipo = EstoqueMovimentacao.TIPO_AJUSTE
            est, _ = Estoque.objects.select_for_update().get_or_create(
                CodigodeBarra=item.CodigodeBarra,
                Idloja=inv.Idloja,
                defaults={'referencia': item.referencia, 'Estoque': 0, 'reserva': 0},
            )
            anterior = est.Estoque or 0
            est.Estoque = item.saldo_contado
            est.referencia = item.referencia
            est.save(update_fields=['Estoque', 'referencia'])
            EstoqueMovimentacao.objects.create(
                Idloja=inv.Idloja,
                CodigodeBarra=item.CodigodeBarra,
                referencia=item.referencia,
                tipo=mov_tipo,
                quantidade=item.diferenca,
                saldo_anterior=anterior,
                saldo_posterior=item.saldo_contado,
                documento=documento,
                observacao=f'Ajuste por inventário {inv.descricao}',
            )
            movimentos += 1
        inv.status = InventarioEstoque.STATUS_FECHADO
        inv.data_fechamento = timezone.localdate()
        inv.save(update_fields=['status', 'data_fechamento'])
        data = self.get_serializer(inv).data
        data['movimentos_gerados'] = movimentos
        data['documento'] = documento
        return Response(data)


class InventarioEstoqueItemViewSet(BaseViewSet):
    required_module = "estoque"
    queryset = InventarioEstoqueItem.objects.all()
    serializer_class = InventarioEstoqueItemSerializer

    def get_queryset(self):
        qs = InventarioEstoqueItem.objects.all().order_by('referencia', 'CodigodeBarra')
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(inventario__Idloja__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        inventario = self.request.query_params.get('inventario')
        if inventario:
            qs = qs.filter(inventario_id=inventario)
        return qs

    def perform_update(self, serializer):
        item = self.get_object()
        if item.inventario.status != InventarioEstoque.STATUS_ABERTO:
            raise ValidationError({'inventario': 'Somente itens de inventário aberto podem ser alterados.'})
        serializer.save(contado=True)
