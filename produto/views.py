from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

try:
    from auditoria.models import AuditLog
except Exception:  # auditoria opcionalmente ausente em dev
    AuditLog = None

from .permissions import CanToggleProductFlags
from accounts.permissions import HasModuleRole

from .models import (
    ConfigEan, Ncm, Grade, Tamanho, Cor, Material, Colecao, Unidade,
    Grupo, Subgrupo, Tabelapreco, Codigos, Produto, ProdutoDetalhe,
    TabelaprecoProduto, Promocao, Pack, PackItem, Estoque, EstoqueMovimentacao,
    InventarioEstoque, InventarioEstoqueItem
)
from .serializers import (
    ConfigEanSerializer, NcmSerializer, GradeSerializer, TamanhoSerializer, CorSerializer,
    MaterialSerializer, ColecaoSerializer, UnidadeSerializer, GrupoSerializer, SubgrupoSerializer,
    TabelaprecoSerializer, CodigosSerializer, ProdutoSerializer, ProdutoDetalheSerializer,
    TabelaprecoProdutoSerializer, PromocaoSerializer, PackSerializer, PackItemSerializer, EstoqueSerializer,
    EstoqueMovimentacaoSerializer, InventarioEstoqueSerializer, InventarioEstoqueItemSerializer
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
    permission_classes = [HasModuleRole]
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa", "Vendedor", "Auxiliar", "Assistente", "Regular"]
    write_roles = ["Admin", "Diretor", "Gerente"]


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

    def get_queryset(self):
        qs = super().get_queryset()
        tipo_produto = self.request.query_params.get('tipo_produto') or self.request.query_params.get('tipo')
        if tipo_produto in ('1', '2'):
            qs = qs.filter(tipo_produto=tipo_produto)

        ativo = self.request.query_params.get('ativo')
        if ativo in ('true', '1'):
            qs = qs.filter(ativo=True)
        elif ativo in ('false', '0'):
            qs = qs.filter(ativo=False)

        search = (self.request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(
                Q(descricao__icontains=search)
                | Q(descricao_reduzida__icontains=search)
                | Q(referencia__icontains=search)
            )

        return qs

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

    def get_queryset(self):
        qs = Estoque.objects.all().order_by('referencia', 'CodigodeBarra', 'Idloja_id')
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
            if colecao:
                produto_qs = produto_qs.filter(colecao__Codigo=colecao)
            if estacao:
                produto_qs = produto_qs.filter(colecao__Estacao=estacao)
            refs = produto_qs.exclude(referencia__isnull=True).values_list('referencia', flat=True)
            qs = qs.filter(referencia__in=refs)
        return qs


class EstoqueMovimentacaoViewSet(BaseViewSet):
    queryset = EstoqueMovimentacao.objects.all()
    serializer_class = EstoqueMovimentacaoSerializer

    def get_queryset(self):
        qs = EstoqueMovimentacao.objects.all()
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


class InventarioEstoqueViewSet(BaseViewSet):
    queryset = InventarioEstoque.objects.all()
    serializer_class = InventarioEstoqueSerializer

    def get_queryset(self):
        qs = InventarioEstoque.objects.all()
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
        estoques = Estoque.objects.filter(Idloja=inv.Idloja).order_by('referencia', 'CodigodeBarra')
        created = 0
        for est in estoques:
            _, was_created = InventarioEstoqueItem.objects.get_or_create(
                inventario=inv,
                CodigodeBarra=est.CodigodeBarra,
                defaults={
                    'referencia': est.referencia,
                    'saldo_sistema': est.Estoque or 0,
                    'saldo_contado': est.Estoque or 0,
                },
            )
            if was_created:
                created += 1
        return Response({'created': created})

    @action(detail=True, methods=['post'], url_path='fechar')
    @transaction.atomic
    def fechar(self, request, pk=None):
        inv = self.get_object()
        if inv.status != InventarioEstoque.STATUS_ABERTO:
            return Response({'detail': 'Inventário não está aberto.'}, status=status.HTTP_400_BAD_REQUEST)
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
                quantidade=item.saldo_contado,
                saldo_anterior=anterior,
                saldo_posterior=item.saldo_contado,
                documento=f'INV-{inv.pk}',
                observacao='Ajuste por inventário',
            )
        inv.status = InventarioEstoque.STATUS_FECHADO
        inv.data_fechamento = timezone.localdate()
        inv.save(update_fields=['status', 'data_fechamento'])
        return Response(self.get_serializer(inv).data)


class InventarioEstoqueItemViewSet(BaseViewSet):
    queryset = InventarioEstoqueItem.objects.all()
    serializer_class = InventarioEstoqueItemSerializer

    def get_queryset(self):
        qs = InventarioEstoqueItem.objects.all().order_by('referencia', 'CodigodeBarra')
        inventario = self.request.query_params.get('inventario')
        if inventario:
            qs = qs.filter(inventario_id=inventario)
        return qs
