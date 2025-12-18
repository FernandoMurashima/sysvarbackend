from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone

try:
    from auditoria.models import AuditLog
except Exception:  # auditoria opcionalmente ausente em dev
    AuditLog = None

from .permissions import CanToggleProductFlags

from .models import (
    ConfigEan, Ncm, Grade, Tamanho, Cor, Material, Colecao, Unidade,
    Grupo, Subgrupo, Tabelapreco, Codigos, Produto, ProdutoDetalhe,
    TabelaprecoProduto, Pack, PackItem, Estoque
)
from .serializers import (
    ConfigEanSerializer, NcmSerializer, GradeSerializer, TamanhoSerializer, CorSerializer,
    MaterialSerializer, ColecaoSerializer, UnidadeSerializer, GrupoSerializer, SubgrupoSerializer,
    TabelaprecoSerializer, CodigosSerializer, ProdutoSerializer, ProdutoDetalheSerializer,
    TabelaprecoProdutoSerializer, PackSerializer, PackItemSerializer, EstoqueSerializer
)


def _audit(model_name: str, obj_id: str, changes: dict, request, action: str):
    """Registra log simples na tabela de auditoria (se disponível)."""
    if not AuditLog:
        return
    try:
        ip = request.META.get("REMOTE_ADDR")
        ua = request.META.get("HTTP_USER_AGENT", "")[:400]
        AuditLog.objects.create(
            action=action,
            app_label="produto",
            model=model_name,
            object_id=str(obj_id),
            changes=changes,
            user=getattr(request, "user", None),
            ip=ip,
            user_agent=ua,
        )
    except Exception:
        # Não derruba a requisição se auditoria falhar
        pass


class BaseViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]


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

    # Usado por CanToggleProductFlags
    model_perm_codename = "produto.change_produto"

    @action(detail=True, methods=['post'], url_path='gerar-skus')
    def gerar_skus(self, request, pk=None):
        """
        Gera SKUs (ProdutoDetalhe) para um produto de Revenda.
        Body:
        {
          "cores": [Idcor, ...],          # obrigatório
          "tamanhos": [Idtamanho, ...]    # opcional; se omitido, usa todos da grade do produto
        }
        """
        produto = self.get_object()
        if produto.tipo_produto != '1':
            return Response({'detail': 'Apenas produtos de Revenda geram SKUs.'}, status=status.HTTP_400_BAD_REQUEST)
        if not produto.grade_id:
            return Response({'detail': 'Produto sem grade.'}, status=status.HTTP_400_BAD_REQUEST)

        cores = request.data.get('cores') or []
        if not isinstance(cores, list) or not cores:
            return Response({'detail': 'Informe "cores" como lista de Idcor.'}, status=status.HTTP_400_BAD_REQUEST)

        tamanho_ids = request.data.get('tamanhos')
        if tamanho_ids:
            tamanhos = list(Tamanho.objects.filter(pk__in=tamanho_ids, idgrade_id=produto.grade_id))
        else:
            tamanhos = list(Tamanho.objects.filter(idgrade_id=produto.grade_id))

        created, skipped = [], []

        for cor_id in cores:
            for tam in tamanhos:
                if ProdutoDetalhe.objects.filter(produto_id=produto.pk, idcor_id=cor_id, idtamanho_id=tam.pk).exists():
                    skipped.append({'idcor': cor_id, 'idtamanho': tam.pk})
                    continue
                payload = {'produto': produto.pk, 'idcor': cor_id, 'idtamanho': tam.pk}
                ser = ProdutoDetalheSerializer(data=payload)
                ser.is_valid(raise_exception=True)
                ser.save()  # gera config_ean + codigo_item_ref + ean13 automaticamente
                created.append(ser.data)

        return Response({
            'counts': {'created': len(created), 'skipped': len(skipped)},
            'created': created,
            'skipped': skipped
        }, status=status.HTTP_200_OK)

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

        if produto.tipo_produto != '1':
            return Response(
                {'detail': 'Estoque inicial só é permitido para produto de Revenda.'},
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
            _audit('produto', obj.pk, {'bloqueado_venda': [before, True], 'motivo': motivo}, request, action="custom")
        ser = self.get_serializer(obj)
        return Response(ser.data)

    @action(detail=True, methods=['post'], url_path='desbloquear-venda', permission_classes=[CanToggleProductFlags])
    def desbloquear_venda(self, request, pk=None):
        obj = self.get_object()
        if obj.bloqueado_venda:
            before = obj.bloqueado_venda
            obj.bloqueado_venda = False
            obj.save(update_fields=['bloqueado_venda'])
            _audit('produto', obj.pk, {'bloqueado_venda': [before, False]}, request, action="custom")
        ser = self.get_serializer(obj)
        return Response(ser.data)


class ProdutoDetalheViewSet(BaseViewSet):
    queryset = ProdutoDetalhe.objects.all()
    serializer_class = ProdutoDetalheSerializer

    # Usado por CanToggleProductFlags
    model_perm_codename = "produto.change_produtodetalhe"

    def get_queryset(self):
        qs = ProdutoDetalhe.objects.all().order_by('produto_id', 'idcor_id', 'idtamanho_id')
        pid = self.request.query_params.get('produto')
        cor = self.request.query_params.get('idcor')
        tam = self.request.query_params.get('idtamanho')
        if pid:
            qs = qs.filter(produto_id=pid)
        if cor:
            qs = qs.filter(idcor_id=cor)
        if tam:
            qs = qs.filter(idtamanho_id=tam)
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


class PackViewSet(BaseViewSet):
    queryset = Pack.objects.all().order_by('-data_cadastro')
    serializer_class = PackSerializer


class PackItemViewSet(BaseViewSet):
    queryset = PackItem.objects.all()
    serializer_class = PackItemSerializer

    def get_queryset(self):
        qs = PackItem.objects.all()
        pack_id = self.request.query_params.get('pack')
        if pack_id:
            qs = qs.filter(pack_id=pack_id)
        ordering = self.request.query_params.get('ordering')
        if ordering:
            qs = qs.order_by(ordering)
        return qs


class EstoqueViewSet(BaseViewSet):
    queryset = Estoque.objects.all()
    serializer_class = EstoqueSerializer
