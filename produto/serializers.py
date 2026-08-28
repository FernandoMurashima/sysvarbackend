from rest_framework import serializers
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from decimal import Decimal
import re
from accounts.permissions import has_field_permission
from cadastros.models import Fornecedor
from .models import (
    ConfigEan, Ncm, Grade, Tamanho, Cor, Material, Colecao, Unidade,
    Grupo, Subgrupo, Tabelapreco, Codigos, Produto, ProdutoDetalhe,
    ProdutoFornecedor,
    ProdutoVendaHistorico, ProdutoUsoConsumoHistorico, ProdutoInsumoHistorico, ProdutoImagem,
    TabelaprecoProduto, FichaTecnica, FichaTecnicaItem, OrdemProducao, OrdemProducaoItem, OrdemProducaoGrade,
    Promocao, Pack, PackItem, Estoque, EstoqueMovimentacao, ProdutoUsoConsumoEstoque, ProdutoUsoConsumoMovimentacao,
    InventarioEstoque, InventarioEstoqueItem
)

# ---------- Aux ----------
def ean13_check_digit(base12: str) -> str:
    s = 0
    for i, ch in enumerate(base12):
        n = int(ch)
        if i % 2 == 0:
            s += n
        else:
            s += 3 * n
    return str((10 - (s % 10)) % 10)

def _alocar_itemref_do_prefixo_ativo(empresa=None):
    qs = ConfigEan.objects.select_for_update().filter(ativo=True)
    if empresa is not None:
        qs = qs.filter(empresa=empresa)
    cfg = qs.order_by('id').first()
    if not cfg:
        raise serializers.ValidationError('Nenhum prefixo GS1 ativo encontrado. Cadastre/ative em ConfigEan.')
    val = cfg.next_itemref or 1
    if val > 99999:
        raise serializers.ValidationError(f'Prefixo {cfg.company_prefix} esgotado (>= 100000). Cadastre/ative outro.')
    item = f"{val:05d}"
    cfg.next_itemref = val + 1
    cfg.save(update_fields=['next_itemref'])
    return cfg, item

def _only_digits(s: str) -> str:
    return ''.join(ch for ch in (s or '') if ch.isdigit())

def _normalize_ncm_dotted(raw: str) -> str:
    if not raw:
        raise serializers.ValidationError({'ncm': 'Informe o NCM.'})
    s = str(raw).strip()
    if len(s) == 10 and s[4:5] == '.' and s[7:8] == '.' and s.replace('.', '').isdigit():
        return s
    d = _only_digits(s)
    if len(d) == 8:
        return f'{d[:4]}.{d[4:6]}.{d[6:8]}'
    raise serializers.ValidationError({'ncm': 'Formato inválido. Use ####.##.## ou 8 dígitos.'})

def _empresa_request(serializer):
    request = serializer.context.get('request') if getattr(serializer, 'context', None) else None
    user = getattr(request, 'user', None)
    if user and user.is_authenticated and not user.is_superuser:
        return getattr(user, 'empresa', None)
    return None

def _pode_ver_custo(serializer):
    request = serializer.context.get('request') if getattr(serializer, 'context', None) else None
    user = getattr(request, 'user', None)
    return has_field_permission(user, 'produto.custo', default_roles=['Admin', 'Diretor', 'Gerente'])

STATUS_ATIVO_INATIVO = {'ATIVO', 'INATIVO'}

def _norm_text(value):
    return value.strip() if isinstance(value, str) else value

def _norm_upper(value):
    value = _norm_text(value)
    return value.upper() if isinstance(value, str) else value

def _empresa_do_serializer(serializer):
    return serializer.initial_data.get('empresa') if getattr(serializer, 'initial_data', None) else None

def _unique_empresa(serializer, model, field, value, empresa, message, extra=None):
    if value in (None, '') or not empresa:
        return
    qs = model.objects.filter(**{field: value, 'empresa': empresa})
    if extra:
        qs = qs.filter(**extra)
    if serializer.instance:
        qs = qs.exclude(pk=serializer.instance.pk)
    if qs.exists():
        raise serializers.ValidationError({field: message})

def _normalize_status(attrs, field='Status'):
    if field in attrs:
        status = _norm_upper(attrs.get(field) or 'ATIVO')
        if status not in STATUS_ATIVO_INATIVO:
            raise serializers.ValidationError({field: 'Status deve ser ATIVO ou INATIVO.'})
        attrs[field] = status
    return attrs

# ---------- Cadastros mestres ----------
class ConfigEanSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConfigEan
        fields = '__all__'

class NcmSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ncm
        fields = '__all__'

class GradeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Grade
        fields = '__all__'

    def validate(self, attrs):
        attrs['Descricao'] = _norm_text(attrs.get('Descricao', getattr(self.instance, 'Descricao', '')))
        if not attrs['Descricao']:
            raise serializers.ValidationError({'Descricao': 'Descrição é obrigatória.'})
        return _normalize_status(attrs)

class TamanhoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tamanho
        fields = '__all__'

    def validate(self, attrs):
        empresa = attrs.get('empresa', getattr(self.instance, 'empresa', None)) or _empresa_request(self)
        grade = attrs.get('idgrade', getattr(self.instance, 'idgrade', None))
        attrs['Tamanho'] = _norm_upper(attrs.get('Tamanho', getattr(self.instance, 'Tamanho', '')))
        if not grade:
            raise serializers.ValidationError({'idgrade': 'Grade é obrigatória.'})
        if not attrs['Tamanho']:
            raise serializers.ValidationError({'Tamanho': 'Tamanho é obrigatório.'})
        if grade and empresa and grade.empresa_id and grade.empresa_id != empresa.id:
            raise serializers.ValidationError({'idgrade': 'A grade selecionada pertence a outra empresa.'})
        if grade and not attrs.get('empresa') and not empresa:
            attrs['empresa'] = grade.empresa
        qs = Tamanho.objects.filter(idgrade=grade, Tamanho=attrs['Tamanho'])
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError({'Tamanho': 'Tamanho já cadastrado nesta grade.'})
        _normalize_status(attrs)
        return attrs

class CorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cor
        fields = '__all__'

    def validate(self, attrs):
        empresa = attrs.get('empresa', getattr(self.instance, 'empresa', None)) or _empresa_request(self)
        attrs['Descricao'] = _norm_text(attrs.get('Descricao', getattr(self.instance, 'Descricao', '')))
        attrs['Cor'] = _norm_text(attrs.get('Cor', getattr(self.instance, 'Cor', '')))
        if 'Codigo' in attrs:
            attrs['Codigo'] = _norm_upper(attrs.get('Codigo'))
        if not attrs['Descricao']:
            raise serializers.ValidationError({'Descricao': 'Descrição é obrigatória.'})
        if not attrs['Cor']:
            raise serializers.ValidationError({'Cor': 'Cor é obrigatória.'})
        _unique_empresa(self, Cor, 'Codigo', attrs.get('Codigo'), empresa, 'Código já cadastrado nesta empresa.')
        return _normalize_status(attrs)

class MaterialSerializer(serializers.ModelSerializer):
    class Meta:
        model = Material
        fields = '__all__'

    def validate(self, attrs):
        attrs['Descricao'] = _norm_text(attrs.get('Descricao', getattr(self.instance, 'Descricao', '')))
        if 'Codigo' in attrs:
            attrs['Codigo'] = _norm_upper(attrs.get('Codigo'))
        if not attrs['Descricao']:
            raise serializers.ValidationError({'Descricao': 'Descrição é obrigatória.'})
        return _normalize_status(attrs)

class ColecaoSerializer(serializers.ModelSerializer):
    Contador = serializers.IntegerField(read_only=True)

    class Meta:
        model = Colecao
        fields = '__all__'

    def validate(self, attrs):
        codigo = _norm_text(attrs.get('Codigo', getattr(self.instance, 'Codigo', '')))
        estacao = attrs.get('Estacao', getattr(self.instance, 'Estacao', None))
        status = attrs.get('Status', getattr(self.instance, 'Status', None))
        if not re.fullmatch(r'\d{2}', codigo or ''):
            raise serializers.ValidationError({'Codigo': 'Código deve ter exatamente 2 dígitos.'})
        if estacao not in dict(Colecao.ESTACOES_CHOICES):
            raise serializers.ValidationError({'Estacao': 'Estação inválida.'})
        if status not in dict(Colecao.STATUS_CHOICES):
            raise serializers.ValidationError({'Status': 'Status inválido.'})
        attrs['Codigo'] = codigo
        return attrs

class UnidadeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Unidade
        fields = '__all__'

    def validate(self, attrs):
        empresa = attrs.get('empresa', getattr(self.instance, 'empresa', None)) or _empresa_request(self)
        attrs['Descricao'] = _norm_text(attrs.get('Descricao', getattr(self.instance, 'Descricao', '')))
        attrs['Codigo'] = _norm_upper(attrs.get('Codigo', getattr(self.instance, 'Codigo', '')))
        if not attrs['Descricao']:
            raise serializers.ValidationError({'Descricao': 'Descrição é obrigatória.'})
        if not attrs['Codigo']:
            raise serializers.ValidationError({'Codigo': 'Código é obrigatório.'})
        _unique_empresa(self, Unidade, 'Codigo', attrs['Codigo'], empresa, 'Código já cadastrado nesta empresa.')
        return attrs

class GrupoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Grupo
        fields = '__all__'

    def validate(self, attrs):
        empresa = attrs.get('empresa', getattr(self.instance, 'empresa', None)) or _empresa_request(self)
        attrs['Codigo'] = _norm_upper(attrs.get('Codigo', getattr(self.instance, 'Codigo', '')))
        attrs['CodigoRef'] = _norm_text(attrs.get('CodigoRef', getattr(self.instance, 'CodigoRef', '')))
        attrs['Descricao'] = _norm_text(attrs.get('Descricao', getattr(self.instance, 'Descricao', '')))
        if not attrs['Codigo']:
            raise serializers.ValidationError({'Codigo': 'Código é obrigatório.'})
        if not re.fullmatch(r'\d{2}', attrs['CodigoRef'] or ''):
            raise serializers.ValidationError({'CodigoRef': 'Código de referência deve ter exatamente 2 dígitos numéricos.'})
        if not attrs['Descricao']:
            raise serializers.ValidationError({'Descricao': 'Descrição é obrigatória.'})
        if attrs.get('Margem', getattr(self.instance, 'Margem', 0)) is None or Decimal(str(attrs.get('Margem', getattr(self.instance, 'Margem', 0)))) < 0:
            raise serializers.ValidationError({'Margem': 'Margem deve ser maior ou igual a zero.'})
        _unique_empresa(self, Grupo, 'Codigo', attrs['Codigo'], empresa, 'Código já cadastrado nesta empresa.')
        _unique_empresa(self, Grupo, 'CodigoRef', attrs['CodigoRef'], empresa, 'Código de referência já cadastrado nesta empresa.')
        return attrs

class SubgrupoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subgrupo
        fields = '__all__'

    def validate(self, attrs):
        empresa = attrs.get('empresa', getattr(self.instance, 'empresa', None)) or _empresa_request(self)
        grupo = attrs.get('Idgrupo', getattr(self.instance, 'Idgrupo', None))
        attrs['Descricao'] = _norm_text(attrs.get('Descricao', getattr(self.instance, 'Descricao', '')))
        if not grupo:
            raise serializers.ValidationError({'Idgrupo': 'Grupo é obrigatório.'})
        if not attrs['Descricao']:
            raise serializers.ValidationError({'Descricao': 'Descrição é obrigatória.'})
        if attrs.get('Margem', getattr(self.instance, 'Margem', 0)) is not None and Decimal(str(attrs.get('Margem', getattr(self.instance, 'Margem', 0)))) < 0:
            raise serializers.ValidationError({'Margem': 'Margem deve ser maior ou igual a zero.'})
        if grupo and empresa and grupo.empresa_id and grupo.empresa_id != empresa.id:
            raise serializers.ValidationError({'Idgrupo': 'O grupo selecionado pertence a outra empresa.'})
        if grupo and not attrs.get('empresa') and not empresa:
            attrs['empresa'] = grupo.empresa
        qs = Subgrupo.objects.filter(Idgrupo=grupo, Descricao=attrs['Descricao'])
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError({'Descricao': 'Subgrupo já cadastrado neste grupo.'})
        return attrs

class TabelaprecoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tabelapreco
        fields = '__all__'

class CodigosSerializer(serializers.ModelSerializer):
    class Meta:
        model = Codigos
        fields = '__all__'

class PackSerializer(serializers.ModelSerializer):
    class Meta:
        model = Pack
        fields = '__all__'

    def validate(self, attrs):
        grade = attrs.get('grade') or getattr(self.instance, 'grade', None)
        empresa = attrs.get('empresa') or getattr(self.instance, 'empresa', None)
        attrs['nome'] = _norm_text(attrs.get('nome', getattr(self.instance, 'nome', '')))
        if not attrs['nome']:
            raise serializers.ValidationError({'nome': 'Nome é obrigatório.'})
        if not grade:
            raise serializers.ValidationError({'grade': 'Grade é obrigatória.'})
        if grade and empresa and grade.empresa_id and grade.empresa_id != empresa.id:
            raise serializers.ValidationError({'grade': 'A grade selecionada pertence a outra empresa.'})
        return attrs

class PackItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PackItem
        fields = '__all__'

    def validate(self, attrs):
        pack = attrs.get('pack') or getattr(self.instance, 'pack', None)
        tamanho = attrs.get('tamanho') or getattr(self.instance, 'tamanho', None)
        qtd = attrs.get('qtd', getattr(self.instance, 'qtd', None))

        if not pack:
            raise serializers.ValidationError({'pack': 'Pack é obrigatório.'})
        if not tamanho:
            raise serializers.ValidationError({'tamanho': 'Tamanho é obrigatório.'})
        if qtd is None or int(qtd) <= 0:
            raise serializers.ValidationError({'qtd': 'Quantidade deve ser maior que zero.'})
        if pack and tamanho:
            if pack.empresa_id and tamanho.empresa_id and pack.empresa_id != tamanho.empresa_id:
                raise serializers.ValidationError({'tamanho': 'O tamanho selecionado pertence a outra empresa.'})
            if pack.grade_id and tamanho.idgrade_id != pack.grade_id:
                raise serializers.ValidationError({'tamanho': 'O tamanho selecionado não pertence à grade do pack.'})
            qs = PackItem.objects.filter(pack=pack, tamanho=tamanho)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({'tamanho': 'Tamanho já informado neste pack.'})
        return attrs

class EstoqueSerializer(serializers.ModelSerializer):
    class Meta:
        model = Estoque
        fields = '__all__'


class EstoqueMovimentacaoSerializer(serializers.ModelSerializer):
    cor = serializers.SerializerMethodField()
    tamanho = serializers.SerializerMethodField()

    class Meta:
        model = EstoqueMovimentacao
        fields = '__all__'
        read_only_fields = ('saldo_anterior', 'saldo_posterior', 'data_movimento')

    def _sku(self, obj):
        cache = self.context.setdefault('_estoque_mov_skus', {})
        if obj.CodigodeBarra not in cache:
            cache[obj.CodigodeBarra] = (
                ProdutoDetalhe.objects
                .select_related('idcor', 'idtamanho')
                .filter(ean13=obj.CodigodeBarra)
                .first()
            )
        return cache[obj.CodigodeBarra]

    def get_cor(self, obj):
        sku = self._sku(obj)
        return getattr(getattr(sku, 'idcor', None), 'Descricao', '') or ''

    def get_tamanho(self, obj):
        sku = self._sku(obj)
        return getattr(getattr(sku, 'idtamanho', None), 'Tamanho', '') or ''


class InventarioEstoqueItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InventarioEstoqueItem
        fields = '__all__'
        read_only_fields = ('diferenca',)


class InventarioEstoqueSerializer(serializers.ModelSerializer):
    itens = InventarioEstoqueItemSerializer(many=True, read_only=True)
    total_itens = serializers.SerializerMethodField()
    total_contados = serializers.SerializerMethodField()
    total_divergencias = serializers.SerializerMethodField()
    saldo_sistema_total = serializers.SerializerMethodField()
    saldo_contado_total = serializers.SerializerMethodField()
    diferenca_total = serializers.SerializerMethodField()

    class Meta:
        model = InventarioEstoque
        fields = '__all__'

    def get_total_itens(self, obj):
        return obj.itens.count()

    def get_total_contados(self, obj):
        return obj.itens.filter(contado=True).count()

    def get_total_divergencias(self, obj):
        return obj.itens.exclude(diferenca=0).count()

    def get_saldo_sistema_total(self, obj):
        return sum((item.saldo_sistema or 0) for item in obj.itens.all())

    def get_saldo_contado_total(self, obj):
        return sum((item.saldo_contado or 0) for item in obj.itens.all())

    def get_diferenca_total(self, obj):
        return sum((item.diferenca or 0) for item in obj.itens.all())

# ---------- Produto / SKU / Preço ----------
class ProdutoSerializer(serializers.ModelSerializer):
    referencia = serializers.CharField(read_only=True)
    cadastro_fiscal_incompleto = serializers.SerializerMethodField()

    class Meta:
        model = Produto
        fields = '__all__'

    def get_cadastro_fiscal_incompleto(self, obj):
        return obj.tipo_produto in ('2', '4') and not bool(obj.ncm)

    def validate(self, attrs):
        empresa = attrs.get('empresa', getattr(self.instance, 'empresa', None)) or _empresa_request(self)
        tipo = attrs.get('tipo_produto', getattr(self.instance, 'tipo_produto', None))
        grade = attrs.get('grade', getattr(self.instance, 'grade', None))
        unidade = attrs.get('unidade', getattr(self.instance, 'unidade', None))
        material = attrs.get('material', getattr(self.instance, 'material', None))
        colecao = attrs.get('colecao', getattr(self.instance, 'colecao', None))
        grupo = attrs.get('grupo', getattr(self.instance, 'grupo', None))
        subgrupo = attrs.get('subgrupo', getattr(self.instance, 'subgrupo', None))
        ncm_raw = attrs.get('ncm', getattr(self.instance, 'ncm', None))
        descricao_reduzida = attrs.get('descricao_reduzida', getattr(self.instance, 'descricao_reduzida', None))

        if self.instance and 'tipo_produto' in attrs and attrs['tipo_produto'] != self.instance.tipo_produto:
            raise serializers.ValidationError({'tipo_produto': 'Tipo do produto não pode ser alterado após a criação.'})

        if self.instance and 'grade' in attrs and attrs['grade'] != self.instance.grade:
            if ProdutoDetalhe.objects.filter(produto=self.instance).exists():
                raise serializers.ValidationError({'grade': 'Grade não pode ser alterada após a geração de SKUs.'})

        for campo, obj in (
            ('unidade', unidade),
            ('grade', grade),
            ('material', material),
            ('colecao', colecao),
            ('grupo', grupo),
            ('subgrupo', subgrupo),
        ):
            obj_empresa_id = getattr(obj, 'empresa_id', None)
            if empresa and obj_empresa_id and obj_empresa_id != empresa.id:
                raise serializers.ValidationError({campo: 'O cadastro selecionado pertence a outra empresa.'})

        if tipo in ('1', '3'):  # Revenda / Produto Próprio
            if 'referencia' in self.initial_data and self.initial_data.get('referencia'):
                raise serializers.ValidationError({'referencia': 'Gerada automaticamente para produtos vendáveis.'})
            if not colecao or not getattr(colecao, 'Codigo', None) or not getattr(colecao, 'Estacao', None):
                raise serializers.ValidationError({'colecao': 'Coleção com Código (2 dígitos) e Estação (2 dígitos) é obrigatória.'})
            if not grupo or not getattr(grupo, 'CodigoRef', None):
                raise serializers.ValidationError({'grupo': 'Grupo com CodigoRef (2 dígitos) é obrigatório.'})
            if not subgrupo:
                raise serializers.ValidationError({'subgrupo': 'Subgrupo é obrigatório para produtos vendáveis.'})
            if subgrupo and grupo and subgrupo.Idgrupo_id != grupo.Idgrupo:
                raise serializers.ValidationError({'subgrupo': 'O subgrupo selecionado não pertence ao grupo informado.'})
            if not descricao_reduzida or not str(descricao_reduzida).strip():
                raise serializers.ValidationError({'descricao_reduzida': 'Descrição reduzida é obrigatória para produtos vendáveis.'})
            if grade is None:
                raise serializers.ValidationError({'grade': 'Obrigatória para produtos vendáveis.'})
            ncm_fmt = _normalize_ncm_dotted(ncm_raw)
            ncm_qs = Ncm.objects.filter(ncm=ncm_fmt)
            if empresa:
                ncm_qs = ncm_qs.filter(empresa=empresa)
            if not ncm_qs.exists():
                raise serializers.ValidationError({'ncm': f'NCM {ncm_fmt} não cadastrado.'})
            attrs['ncm'] = ncm_fmt
        elif tipo in ('2', '4'):  # Uso/Consumo / Insumo de Produção
            if 'referencia' in self.initial_data and self.initial_data.get('referencia'):
                raise serializers.ValidationError({'referencia': 'Não deve ser informada para este tipo de produto.'})
            if grade is not None:
                raise serializers.ValidationError({'grade': 'Não deve ser informada para este tipo de produto.'})
            if tipo == '2':
                if not descricao_reduzida or not str(descricao_reduzida).strip():
                    raise serializers.ValidationError({'descricao_reduzida': 'Descrição reduzida é obrigatória para Produto Uso/Consumo.'})
                attrs['grupo'] = None
                attrs['subgrupo'] = None
                attrs['material'] = None
                attrs['grade'] = None
                attrs['colecao'] = None
            if tipo == '4':
                if not descricao_reduzida or not str(descricao_reduzida).strip():
                    raise serializers.ValidationError({'descricao_reduzida': 'Descrição reduzida é obrigatória para Insumo.'})
                attrs['grupo'] = None
                attrs['subgrupo'] = None
                attrs['grade'] = None
                attrs['colecao'] = None
            if ncm_raw:
                ncm_fmt = _normalize_ncm_dotted(ncm_raw)
                ncm_qs = Ncm.objects.filter(ncm=ncm_fmt)
                if empresa:
                    ncm_qs = ncm_qs.filter(empresa=empresa)
                if not ncm_qs.exists():
                    raise serializers.ValidationError({'ncm': f'NCM {ncm_fmt} não cadastrado.'})
                attrs['ncm'] = ncm_fmt
        else:
            raise serializers.ValidationError({'tipo_produto': 'Tipo inválido.'})

        return attrs

class ProdutoDetalheSerializer(serializers.ModelSerializer):
    ean13 = serializers.CharField(read_only=True)
    config_ean = serializers.PrimaryKeyRelatedField(read_only=True)
    produto_descricao = serializers.CharField(source='produto.descricao', read_only=True)
    produto_referencia = serializers.CharField(source='produto.referencia', read_only=True)
    produto_tipo = serializers.CharField(source='produto.tipo_produto', read_only=True)
    cor_descricao = serializers.CharField(source='idcor.Descricao', read_only=True)
    tamanho_descricao = serializers.CharField(source='idtamanho.Tamanho', read_only=True)
    preco_venda = serializers.SerializerMethodField()
    margem_valor = serializers.SerializerMethodField()
    margem_percentual = serializers.SerializerMethodField()
    estoque_total = serializers.SerializerMethodField()

    class Meta:
        model = ProdutoDetalhe
        fields = '__all__'
        extra_kwargs = {
            'codigo_item_ref': {'required': False},
        }

    def _preco_venda(self, obj):
        preco = (
            TabelaprecoProduto.objects
            .filter(produto=obj.produto, ativo=True)
            .order_by('-DataInicio', '-Idprodutopreco')
            .values_list('preco_promocional', 'preco')
            .first()
        )
        if not preco:
            return Decimal('0')
        promocional, normal = preco
        return Decimal(promocional or normal or 0)

    def get_preco_venda(self, obj):
        return self._preco_venda(obj)

    def get_margem_valor(self, obj):
        preco = self._preco_venda(obj)
        custo = Decimal(obj.custo_medio or obj.custo_ultima_compra or obj.custo_original or 0)
        return preco - custo

    def get_margem_percentual(self, obj):
        preco = self._preco_venda(obj)
        if not preco:
            return Decimal('0')
        margem = self.get_margem_valor(obj)
        return (margem / preco) * Decimal('100')

    def get_estoque_total(self, obj):
        total = (
            Estoque.objects
            .filter(CodigodeBarra=obj.ean13)
            .aggregate(total=Sum('Estoque'))
            .get('total')
        )
        return total or 0

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not _pode_ver_custo(self):
            for campo in (
                'custo_original',
                'custo_ultima_compra',
                'custo_medio',
                'margem_valor',
                'margem_percentual',
            ):
                data[campo] = None
        return data

    def validate(self, attrs):
        for f in ('codigo_item_ref', 'ean13', 'config_ean'):
            if f in self.initial_data and self.initial_data.get(f):
                raise serializers.ValidationError({f: 'É gerado automaticamente; não envie manualmente.'})

        produto = attrs.get('produto') or getattr(self.instance, 'produto', None)
        cor = attrs.get('idcor') or getattr(self.instance, 'idcor', None)
        tamanho = attrs.get('idtamanho') or getattr(self.instance, 'idtamanho', None)

        if not produto or not tamanho:
            return attrs

        if produto.tipo_produto not in ('1', '3'):
            raise serializers.ValidationError('ProdutoDetalhe só é permitido para produtos vendáveis com grade.')

        if produto.grade_id and tamanho.idgrade_id != produto.grade_id:
            raise serializers.ValidationError('Tamanho não pertence à grade do produto.')
        if produto.empresa_id:
            if cor and cor.empresa_id and cor.empresa_id != produto.empresa_id:
                raise serializers.ValidationError({'idcor': 'A cor selecionada pertence a outra empresa.'})
            if tamanho and tamanho.empresa_id and tamanho.empresa_id != produto.empresa_id:
                raise serializers.ValidationError({'idtamanho': 'O tamanho selecionado pertence a outra empresa.'})

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        produto = validated_data.get('produto')
        cfg, item = _alocar_itemref_do_prefixo_ativo(getattr(produto, 'empresa', None))
        validated_data['config_ean'] = cfg
        validated_data['codigo_item_ref'] = item

        base12 = f"{cfg.country_prefix}{cfg.company_prefix}{item}"
        if len(base12) != 12 or not base12.isdigit():
            raise serializers.ValidationError('Base EAN inválida (verifique prefixos e o item de 5 dígitos).')

        dv = ean13_check_digit(base12)
        validated_data['ean13'] = base12 + dv

        return super().create(validated_data)


class ProdutoFornecedorSerializer(serializers.ModelSerializer):
    fornecedor_nome = serializers.CharField(source='fornecedor.nome_fornecedor', read_only=True)
    produto_descricao = serializers.CharField(source='produto.descricao', read_only=True)
    produto_referencia = serializers.CharField(source='produto.referencia', read_only=True)
    produto_tipo = serializers.CharField(source='produto.tipo_produto', read_only=True)
    unidade_interna = serializers.CharField(source='produto.unidade.Codigo', read_only=True)
    unidade_interna_descricao = serializers.CharField(source='produto.unidade.Descricao', read_only=True)

    class Meta:
        model = ProdutoFornecedor
        fields = '__all__'
        read_only_fields = ('codigo_normalizado', 'codigo_vigente', 'criado_por', 'criado_em', 'atualizado_em')
        extra_kwargs = {
            'empresa': {'required': False},
            'ativo': {'required': False},
            'gtin_ean': {'required': False, 'allow_blank': True},
            'descricao_fornecedor': {'required': False, 'allow_blank': True},
            'unidade_fornecedor': {'required': False, 'allow_blank': True},
            'fator_conversao': {'required': False},
        }

    def validate(self, attrs):
        request = self.context.get('request') if getattr(self, 'context', None) else None
        user = getattr(request, 'user', None)
        empresa = attrs.get('empresa') or getattr(self.instance, 'empresa', None)
        if not empresa and user and getattr(user, 'is_authenticated', False) and not user.is_superuser:
            empresa = getattr(user, 'empresa', None)
            if empresa:
                attrs['empresa'] = empresa

        fornecedor = attrs.get('fornecedor') or getattr(self.instance, 'fornecedor', None)
        produto = attrs.get('produto') or getattr(self.instance, 'produto', None)
        codigo = ProdutoFornecedor.normalizar_codigo(
            attrs.get('codigo_produto_fornecedor', getattr(self.instance, 'codigo_produto_fornecedor', ''))
        )
        ativo = attrs.get('ativo', getattr(self.instance, 'ativo', True))
        unidade_fornecedor = attrs.get('unidade_fornecedor', getattr(self.instance, 'unidade_fornecedor', ''))
        fator_conversao = attrs.get('fator_conversao', getattr(self.instance, 'fator_conversao', Decimal('1')))

        if not empresa:
            raise serializers.ValidationError({'empresa': 'Empresa é obrigatória.'})
        if not fornecedor:
            raise serializers.ValidationError({'fornecedor': 'Fornecedor é obrigatório.'})
        if not produto:
            raise serializers.ValidationError({'produto': 'Produto é obrigatório.'})
        if not codigo:
            raise serializers.ValidationError({'codigo_produto_fornecedor': 'Código do produto no fornecedor é obrigatório.'})
        if fornecedor.empresa_id != empresa.id:
            raise serializers.ValidationError({'fornecedor': 'Fornecedor pertence a outra empresa.'})
        if produto.empresa_id != empresa.id:
            raise serializers.ValidationError({'produto': 'Produto pertence a outra empresa.'})
        if unidade_fornecedor is not None:
            unidade_normalizada = str(unidade_fornecedor).strip()
            unidade_raw = getattr(self, 'initial_data', {}).get('unidade_fornecedor')
            if unidade_raw is not None and str(unidade_raw) and not str(unidade_raw).strip():
                raise serializers.ValidationError({'unidade_fornecedor': 'Unidade do fornecedor não pode conter apenas espaços.'})
            attrs['unidade_fornecedor'] = unidade_normalizada
        if fator_conversao is None:
            raise serializers.ValidationError({'fator_conversao': 'Fator de conversão é obrigatório.'})
        fator_conversao = Decimal(str(fator_conversao))
        if fator_conversao <= 0:
            raise serializers.ValidationError({'fator_conversao': 'Fator de conversão deve ser maior que zero.'})
        attrs['fator_conversao'] = fator_conversao

        gtin = attrs.get('gtin_ean', getattr(self.instance, 'gtin_ean', ''))
        if gtin:
            gtin = ''.join(ch for ch in str(gtin) if ch.isdigit())
            if len(gtin) < 8 or len(gtin) > 14:
                raise serializers.ValidationError({'gtin_ean': 'GTIN/EAN deve conter entre 8 e 14 dígitos.'})
            attrs['gtin_ean'] = gtin

        attrs['codigo_produto_fornecedor'] = codigo
        conflito = ProdutoFornecedor.objects.filter(
            empresa=empresa,
            fornecedor=fornecedor,
            codigo_vigente=codigo if ativo else None,
        )
        if self.instance:
            conflito = conflito.exclude(pk=self.instance.pk)
        if ativo and conflito.exists():
            raise serializers.ValidationError({
                'codigo_produto_fornecedor': 'Já existe vínculo ativo para este fornecedor e código externo.'
            })
        return attrs

    def create(self, validated_data):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if user and user.is_authenticated:
            validated_data['criado_por'] = user
        return super().create(validated_data)


class ProdutoVendaHistoricoSerializer(serializers.ModelSerializer):
    usuario_nome = serializers.SerializerMethodField()

    class Meta:
        model = ProdutoVendaHistorico
        fields = (
            'id', 'empresa', 'produto', 'data_evento', 'tipo_evento', 'usuario',
            'usuario_nome', 'descricao', 'dados_anteriores', 'dados_novos',
        )
        read_only_fields = fields

    def get_usuario_nome(self, obj):
        user = obj.usuario
        if not user:
            return None
        return getattr(user, 'get_full_name', lambda: '')() or getattr(user, 'username', None) or str(user)


class ProdutoUsoConsumoHistoricoSerializer(ProdutoVendaHistoricoSerializer):
    class Meta:
        model = ProdutoUsoConsumoHistorico
        fields = (
            'id', 'empresa', 'produto', 'data_evento', 'tipo_evento', 'usuario',
            'usuario_nome', 'descricao', 'dados_anteriores', 'dados_novos',
        )
        read_only_fields = fields


class ProdutoInsumoHistoricoSerializer(ProdutoVendaHistoricoSerializer):
    class Meta:
        model = ProdutoInsumoHistorico
        fields = (
            'id', 'empresa', 'produto', 'data_evento', 'tipo_evento', 'usuario',
            'usuario_nome', 'descricao', 'dados_anteriores', 'dados_novos',
        )
        read_only_fields = fields


class ProdutoUsoConsumoEstoqueSerializer(serializers.ModelSerializer):
    produto_descricao = serializers.CharField(source='produto.descricao', read_only=True)
    produto_referencia = serializers.CharField(source='produto.referencia', read_only=True)
    produto_tipo = serializers.CharField(source='produto.tipo_produto', read_only=True)
    produto_ativo = serializers.BooleanField(source='produto.ativo', read_only=True)
    unidade_codigo = serializers.CharField(source='produto.unidade.Codigo', read_only=True)
    unidade_descricao = serializers.CharField(source='produto.unidade.Descricao', read_only=True)
    loja_nome = serializers.CharField(source='loja.nome_loja', read_only=True)

    class Meta:
        model = ProdutoUsoConsumoEstoque
        fields = '__all__'
        read_only_fields = ('empresa', 'saldo', 'atualizado_em')


class ProdutoUsoConsumoMovimentacaoSerializer(serializers.ModelSerializer):
    produto_descricao = serializers.CharField(source='produto.descricao', read_only=True)
    produto_referencia = serializers.CharField(source='produto.referencia', read_only=True)
    produto_tipo = serializers.CharField(source='produto.tipo_produto', read_only=True)
    loja_nome = serializers.CharField(source='loja.nome_loja', read_only=True)
    usuario_nome = serializers.SerializerMethodField()

    class Meta:
        model = ProdutoUsoConsumoMovimentacao
        fields = '__all__'
        read_only_fields = ('empresa', 'saldo_anterior', 'saldo_posterior', 'data_movimento', 'usuario')

    def get_usuario_nome(self, obj):
        user = obj.usuario
        if not user:
            return None
        return getattr(user, 'get_full_name', lambda: '')() or getattr(user, 'username', None) or str(user)


class ProdutoImagemSerializer(serializers.ModelSerializer):
    imagem_url = serializers.SerializerMethodField()
    imagem_reduzida_url = serializers.SerializerMethodField()

    class Meta:
        model = ProdutoImagem
        fields = '__all__'
        read_only_fields = ('data_cadastro',)

    def get_imagem_url(self, obj):
        request = self.context.get('request')
        if not obj.imagem:
            return None
        url = obj.imagem.url
        return request.build_absolute_uri(url) if request else url

    def get_imagem_reduzida_url(self, obj):
        request = self.context.get('request')
        if not obj.imagem_reduzida:
            return None
        url = obj.imagem_reduzida.url
        return request.build_absolute_uri(url) if request else url

    def validate(self, attrs):
        produto = attrs.get('produto') or getattr(self.instance, 'produto', None)
        if not produto:
            raise serializers.ValidationError({'produto': 'Informe o produto.'})
        empresa = _empresa_request(self)
        if empresa and produto.empresa_id != empresa.id:
            raise serializers.ValidationError({'produto': 'O produto selecionado pertence a outra empresa.'})
        if not self.instance and ProdutoImagem.objects.filter(produto=produto).count() >= 3:
            raise serializers.ValidationError({'produto': 'Produto permite no máximo 3 imagens.'})
        return attrs

class TabelaprecoProdutoSerializer(serializers.ModelSerializer):
    class Meta:
        model = TabelaprecoProduto
        fields = '__all__'

    def validate(self, attrs):
        produto = attrs.get('produto') or getattr(self.instance, 'produto', None)
        if produto and produto.tipo_produto not in ('1', '3'):
            raise serializers.ValidationError('Preço só é permitido para produtos vendáveis.')
        return attrs

    # >>> FIX: garante date (não datetime) sem precisar migrar o Model
    def create(self, validated_data):
        if not validated_data.get('DataInicio'):
            # localdate() retorna date já no fuso configurado
            validated_data['DataInicio'] = timezone.localdate()
        # Se vier acidentalmente datetime em DataFim, converte
        df = validated_data.get('DataFim')
        if hasattr(df, 'date'):
            validated_data['DataFim'] = df.date()
        return super().create(validated_data)

    def update(self, instance, validated_data):
        di = validated_data.get('DataInicio', None)
        if di is None:
            # mantém como date caso model tenha recebido datetime antes de salvar
            instance.DataInicio = getattr(instance, 'DataInicio', None) or timezone.localdate()
        elif hasattr(di, 'date'):
            validated_data['DataInicio'] = di.date()
        df = validated_data.get('DataFim', None)
        if hasattr(df, 'date'):
            validated_data['DataFim'] = df.date()
        return super().update(instance, validated_data)
# <<< end serializer


class FichaTecnicaItemSerializer(serializers.ModelSerializer):
    produto_descricao = serializers.CharField(source='produto.descricao', read_only=True)
    fornecedor_nome = serializers.CharField(source='fornecedor.nome_fornecedor', read_only=True)
    unidade_descricao = serializers.CharField(source='unidade.Descricao', read_only=True)
    unidade_permite_decimal = serializers.BooleanField(source='unidade.permite_decimal', read_only=True)
    quantidade_com_perda = serializers.DecimalField(max_digits=14, decimal_places=4, read_only=True)
    custo_medio_produto = serializers.DecimalField(max_digits=12, decimal_places=4, read_only=True)
    custo_unitario_usado = serializers.DecimalField(max_digits=12, decimal_places=4, read_only=True)
    custo_total_previsto = serializers.DecimalField(max_digits=14, decimal_places=4, read_only=True)

    class Meta:
        model = FichaTecnicaItem
        fields = '__all__'

    def validate(self, attrs):
        ficha = attrs.get('ficha') or getattr(self.instance, 'ficha', None)
        produto = attrs.get('produto', getattr(self.instance, 'produto', None))
        fornecedor = attrs.get('fornecedor', getattr(self.instance, 'fornecedor', None))
        unidade = attrs.get('unidade', getattr(self.instance, 'unidade', None))
        tipo = attrs.get('tipo', getattr(self.instance, 'tipo', None))
        quantidade = attrs.get('quantidade', getattr(self.instance, 'quantidade', None))
        perda = attrs.get('perda_percentual', getattr(self.instance, 'perda_percentual', 0))

        if not ficha:
            raise serializers.ValidationError({'ficha': 'Informe a ficha técnica.'})
        if quantidade is not None and Decimal(quantidade) <= 0:
            raise serializers.ValidationError({'quantidade': 'Informe quantidade maior que zero.'})
        if perda is not None and Decimal(perda) < 0:
            raise serializers.ValidationError({'perda_percentual': 'A perda não pode ser negativa.'})

        empresa_id = ficha.empresa_id
        if produto:
            if produto.empresa_id != empresa_id:
                raise serializers.ValidationError({'produto': 'O produto selecionado pertence a outra empresa.'})
            if produto.tipo_produto != '4':
                raise serializers.ValidationError({'produto': 'Use somente Insumo de Produção na ficha técnica.'})
            if not unidade:
                attrs['unidade'] = produto.unidade
                unidade = produto.unidade
        if fornecedor and fornecedor.empresa_id != empresa_id:
            raise serializers.ValidationError({'fornecedor': 'O fornecedor selecionado pertence a outra empresa.'})
        if fornecedor and fornecedor.ativo is False and not self.instance:
            raise serializers.ValidationError({'fornecedor': 'Fornecedor inativo não pode ser utilizado em novo item.'})
        if fornecedor and fornecedor.bloqueio and not self.instance:
            raise serializers.ValidationError({'fornecedor': 'Fornecedor bloqueado não pode ser utilizado em novo item.'})
        if unidade and unidade.empresa_id and unidade.empresa_id != empresa_id:
            raise serializers.ValidationError({'unidade': 'A unidade selecionada pertence a outra empresa.'})
        if quantidade is not None and unidade and not unidade.permite_decimal:
            quantidade_dec = Decimal(quantidade)
            if quantidade_dec != quantidade_dec.to_integral_value():
                raise serializers.ValidationError({
                    'quantidade': f'A unidade {unidade.Descricao} não aceita quantidade decimal.'
                })
        if tipo == FichaTecnicaItem.TIPO_SERVICO and not fornecedor:
            raise serializers.ValidationError({'fornecedor': 'Informe o fornecedor/facção para item de serviço.'})
        if tipo in (FichaTecnicaItem.TIPO_INSUMO, FichaTecnicaItem.TIPO_AVIAMENTO) and not produto:
            raise serializers.ValidationError({'produto': 'Informe o produto/insumo deste item.'})

        return attrs


class FichaTecnicaSerializer(serializers.ModelSerializer):
    itens = FichaTecnicaItemSerializer(many=True, read_only=True)
    produto_descricao = serializers.CharField(source='produto_final.descricao', read_only=True)
    produto_referencia = serializers.CharField(source='produto_final.referencia', read_only=True)
    custo_previsto = serializers.SerializerMethodField()

    class Meta:
        model = FichaTecnica
        fields = '__all__'
        extra_kwargs = {
            'empresa': {'required': False, 'allow_null': True},
        }

    def get_custo_previsto(self, obj):
        total = Decimal('0')
        for item in obj.itens.all():
            total += item.custo_total_previsto
        return total

    def validate(self, attrs):
        empresa = attrs.get('empresa', getattr(self.instance, 'empresa', None)) or _empresa_request(self)
        produto_final = attrs.get('produto_final', getattr(self.instance, 'produto_final', None))
        rendimento = attrs.get('rendimento', getattr(self.instance, 'rendimento', 1))
        versao = attrs.get('versao', getattr(self.instance, 'versao', None))

        if not produto_final:
            raise serializers.ValidationError({'produto_final': 'Informe o produto próprio da ficha.'})
        if produto_final.tipo_produto != '3':
            raise serializers.ValidationError({'produto_final': 'Ficha técnica deve ser vinculada a Produto Próprio.'})
        if empresa and produto_final.empresa_id != empresa.id:
            raise serializers.ValidationError({'produto_final': 'O produto selecionado pertence a outra empresa.'})
        if rendimento is not None and Decimal(rendimento) <= 0:
            raise serializers.ValidationError({'rendimento': 'Informe rendimento maior que zero.'})
        if empresa and produto_final and versao:
            existente = FichaTecnica.objects.filter(
                empresa=empresa,
                produto_final=produto_final,
                versao=versao,
            )
            if self.instance:
                existente = existente.exclude(pk=self.instance.pk)
            if existente.exists():
                raise serializers.ValidationError({
                    'versao': 'Já existe ficha técnica para este produto nesta versão. Use outra versão ou edite a ficha existente.'
                })
        return attrs


class OrdemProducaoItemSerializer(serializers.ModelSerializer):
    produto_descricao = serializers.CharField(source='produto.descricao', read_only=True)
    fornecedor_nome = serializers.CharField(source='fornecedor.nome_fornecedor', read_only=True)
    unidade_descricao = serializers.CharField(source='unidade.Descricao', read_only=True)
    unidade_permite_decimal = serializers.BooleanField(source='unidade.permite_decimal', read_only=True)

    class Meta:
        model = OrdemProducaoItem
        fields = '__all__'

    def validate(self, attrs):
        ordem = attrs.get('ordem', getattr(self.instance, 'ordem', None))
        fornecedor = attrs.get('fornecedor', getattr(self.instance, 'fornecedor', None))
        produto = attrs.get('produto', getattr(self.instance, 'produto', None))
        empresa_id = getattr(ordem, 'empresa_id', None)
        if fornecedor and empresa_id and fornecedor.empresa_id != empresa_id:
            raise serializers.ValidationError({'fornecedor': 'O fornecedor selecionado pertence a outra empresa.'})
        if fornecedor and fornecedor.ativo is False and not self.instance:
            raise serializers.ValidationError({'fornecedor': 'Fornecedor inativo não pode ser utilizado em novo item.'})
        if fornecedor and fornecedor.bloqueio and not self.instance:
            raise serializers.ValidationError({'fornecedor': 'Fornecedor bloqueado não pode ser utilizado em novo item.'})
        if produto and empresa_id and produto.empresa_id != empresa_id:
            raise serializers.ValidationError({'produto': 'O produto selecionado pertence a outra empresa.'})
        return attrs


class OrdemProducaoGradeSerializer(serializers.ModelSerializer):
    sku_ean = serializers.CharField(source='sku_final.ean13', read_only=True)
    sku_cor = serializers.CharField(source='sku_final.idcor.Descricao', read_only=True)
    sku_tamanho = serializers.CharField(source='sku_final.idtamanho.Tamanho', read_only=True)

    class Meta:
        model = OrdemProducaoGrade
        fields = '__all__'


class OrdemProducaoSerializer(serializers.ModelSerializer):
    itens = OrdemProducaoItemSerializer(many=True, read_only=True)
    grade_producao = OrdemProducaoGradeSerializer(many=True, read_only=True)
    produto_descricao = serializers.CharField(source='produto_final.descricao', read_only=True)
    produto_referencia = serializers.CharField(source='produto_final.referencia', read_only=True)
    ficha_versao = serializers.CharField(source='ficha_tecnica.versao', read_only=True)
    sku_ean = serializers.CharField(source='sku_final.ean13', read_only=True)
    sku_cor = serializers.CharField(source='sku_final.idcor.Descricao', read_only=True)
    sku_tamanho = serializers.CharField(source='sku_final.idtamanho.Tamanho', read_only=True)
    distribuicao_id = serializers.SerializerMethodField()
    distribuicao_numero = serializers.SerializerMethodField()
    distribuicao_status = serializers.SerializerMethodField()

    def _distribuicao_producao(self, obj):
        from distribuicao.models import Distribuicao
        return (
            Distribuicao.objects
            .filter(empresa=obj.empresa, origem_operacao=Distribuicao.ORIGEM_PRODUCAO, origem_id=obj.pk)
            .exclude(status=Distribuicao.STATUS_CANCELADA)
            .order_by('-id')
            .first()
        )

    def get_distribuicao_id(self, obj):
        dist = self._distribuicao_producao(obj)
        return dist.pk if dist else None

    def get_distribuicao_numero(self, obj):
        dist = self._distribuicao_producao(obj)
        return dist.numero if dist else None

    def get_distribuicao_status(self, obj):
        dist = self._distribuicao_producao(obj)
        return dist.status if dist else None

    class Meta:
        model = OrdemProducao
        fields = '__all__'
        extra_kwargs = {
            'empresa': {'required': False, 'allow_null': True},
            'numero': {'required': False, 'allow_blank': True},
            'produto_final': {'required': False, 'allow_null': True},
            'sku_final': {'required': False, 'allow_null': True},
            'rendimento': {'required': False},
            'status': {'read_only': True},
            'custo_previsto': {'read_only': True},
            'custo_real': {'read_only': True},
            'data_inicio': {'read_only': True},
            'data_finalizacao': {'read_only': True},
        }

    def validate(self, attrs):
        empresa = attrs.get('empresa', getattr(self.instance, 'empresa', None)) or _empresa_request(self)
        ficha = attrs.get('ficha_tecnica', getattr(self.instance, 'ficha_tecnica', None))
        quantidade = attrs.get('quantidade', getattr(self.instance, 'quantidade', None))
        numero = attrs.get('numero', getattr(self.instance, 'numero', ''))
        grade_payload = self.initial_data.get('grade_producao') if hasattr(self, 'initial_data') else None

        if not ficha:
            raise serializers.ValidationError({'ficha_tecnica': 'Informe a ficha técnica.'})
        if empresa and ficha.empresa_id != empresa.id:
            raise serializers.ValidationError({'ficha_tecnica': 'A ficha técnica pertence a outra empresa.'})
        if ficha.status != FichaTecnica.STATUS_APROVADA or not ficha.ativa:
            raise serializers.ValidationError({'ficha_tecnica': 'Use uma ficha técnica aprovada e ativa.'})
        sku_final = attrs.get('sku_final', getattr(self.instance, 'sku_final', None))
        if grade_payload is not None:
            if not isinstance(grade_payload, list):
                raise serializers.ValidationError({'grade_producao': 'Informe uma lista de SKUs e quantidades.'})
            total_grade = Decimal('0')
            primeiro_sku = None
            skus_usados = set()
            for linha in grade_payload:
                sku_id = linha.get('sku_final') or linha.get('sku')
                qtd = Decimal(str(linha.get('quantidade') or 0))
                if qtd <= 0:
                    continue
                if sku_id in skus_usados:
                    raise serializers.ValidationError({'grade_producao': 'Existe SKU repetido na grade da OP.'})
                skus_usados.add(sku_id)
                try:
                    sku_linha = ProdutoDetalhe.objects.get(pk=sku_id)
                except ProdutoDetalhe.DoesNotExist:
                    raise serializers.ValidationError({'grade_producao': f'SKU {sku_id} não encontrado.'})
                if sku_linha.produto_id != ficha.produto_final_id:
                    raise serializers.ValidationError({'grade_producao': 'Todos os SKUs produzidos devem pertencer ao produto da ficha técnica.'})
                if not sku_linha.ativo:
                    raise serializers.ValidationError({'grade_producao': f'O SKU {sku_linha.ean13} está inativo.'})
                total_grade += qtd
                if primeiro_sku is None:
                    primeiro_sku = sku_linha
            if total_grade <= 0:
                raise serializers.ValidationError({'grade_producao': 'Informe ao menos um SKU com quantidade maior que zero.'})
            attrs['quantidade'] = total_grade
            attrs['sku_final'] = primeiro_sku
        else:
            if quantidade is None or Decimal(quantidade) <= 0:
                raise serializers.ValidationError({'quantidade': 'Informe quantidade a produzir maior que zero.'})
            if not sku_final:
                raise serializers.ValidationError({'sku_final': 'Informe o SKU produzido pela OP.'})
            if sku_final and sku_final.produto_id != ficha.produto_final_id:
                raise serializers.ValidationError({'sku_final': 'O SKU produzido deve pertencer ao produto da ficha técnica.'})
            if sku_final and not sku_final.ativo:
                raise serializers.ValidationError({'sku_final': 'O SKU produzido está inativo.'})
        if numero and empresa:
            qs = OrdemProducao.objects.filter(empresa=empresa, numero=numero)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({'numero': 'Já existe uma OP com este número nesta empresa.'})
        attrs['produto_final'] = ficha.produto_final
        attrs['rendimento'] = ficha.rendimento
        return attrs


class PromocaoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Promocao
        fields = '__all__'

    def validate(self, attrs):
        data_inicio = attrs.get('data_inicio', getattr(self.instance, 'data_inicio', None))
        data_fim = attrs.get('data_fim', getattr(self.instance, 'data_fim', None))
        valor = attrs.get('valor', getattr(self.instance, 'valor', None))
        tipo = attrs.get('tipo', getattr(self.instance, 'tipo', None))
        if data_fim and data_inicio and data_fim < data_inicio:
            raise serializers.ValidationError({'data_fim': 'Data final não pode ser menor que a inicial.'})
        if valor is not None and valor < 0:
            raise serializers.ValidationError({'valor': 'Valor da promoção não pode ser negativo.'})
        if tipo == Promocao.TIPO_DESCONTO_PERCENTUAL and valor is not None and valor > 100:
            raise serializers.ValidationError({'valor': 'Desconto percentual não pode ser maior que 100%.'})
        return attrs
