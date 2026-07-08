from rest_framework import serializers
from django.db import transaction
from django.utils import timezone
from .models import (
    ConfigEan, Ncm, Grade, Tamanho, Cor, Material, Colecao, Unidade,
    Grupo, Subgrupo, Tabelapreco, Codigos, Produto, ProdutoDetalhe,
    TabelaprecoProduto, Promocao, Pack, PackItem, Estoque, EstoqueMovimentacao,
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

class TamanhoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tamanho
        fields = '__all__'

    def validate(self, attrs):
        empresa = attrs.get('empresa', getattr(self.instance, 'empresa', None)) or _empresa_request(self)
        grade = attrs.get('idgrade', getattr(self.instance, 'idgrade', None))
        if grade and empresa and grade.empresa_id and grade.empresa_id != empresa.id:
            raise serializers.ValidationError({'idgrade': 'A grade selecionada pertence a outra empresa.'})
        if grade and not attrs.get('empresa') and not empresa:
            attrs['empresa'] = grade.empresa
        return attrs

class CorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cor
        fields = '__all__'

class MaterialSerializer(serializers.ModelSerializer):
    class Meta:
        model = Material
        fields = '__all__'

class ColecaoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Colecao
        fields = '__all__'

class UnidadeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Unidade
        fields = '__all__'

class GrupoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Grupo
        fields = '__all__'

class SubgrupoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subgrupo
        fields = '__all__'

    def validate(self, attrs):
        empresa = attrs.get('empresa', getattr(self.instance, 'empresa', None)) or _empresa_request(self)
        grupo = attrs.get('Idgrupo', getattr(self.instance, 'Idgrupo', None))
        if grupo and empresa and grupo.empresa_id and grupo.empresa_id != empresa.id:
            raise serializers.ValidationError({'Idgrupo': 'O grupo selecionado pertence a outra empresa.'})
        if grupo and not attrs.get('empresa') and not empresa:
            attrs['empresa'] = grupo.empresa
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

        if pack and tamanho:
            if pack.empresa_id and tamanho.empresa_id and pack.empresa_id != tamanho.empresa_id:
                raise serializers.ValidationError({'tamanho': 'O tamanho selecionado pertence a outra empresa.'})
            if pack.grade_id and tamanho.idgrade_id != pack.grade_id:
                raise serializers.ValidationError({'tamanho': 'O tamanho selecionado não pertence à grade do pack.'})
        return attrs

class EstoqueSerializer(serializers.ModelSerializer):
    class Meta:
        model = Estoque
        fields = '__all__'


class EstoqueMovimentacaoSerializer(serializers.ModelSerializer):
    class Meta:
        model = EstoqueMovimentacao
        fields = '__all__'
        read_only_fields = ('saldo_anterior', 'saldo_posterior', 'data_movimento')


class InventarioEstoqueItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InventarioEstoqueItem
        fields = '__all__'
        read_only_fields = ('diferenca',)


class InventarioEstoqueSerializer(serializers.ModelSerializer):
    itens = InventarioEstoqueItemSerializer(many=True, read_only=True)

    class Meta:
        model = InventarioEstoque
        fields = '__all__'

# ---------- Produto / SKU / Preço ----------
class ProdutoSerializer(serializers.ModelSerializer):
    referencia = serializers.CharField(read_only=True)

    class Meta:
        model = Produto
        fields = '__all__'

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

        if tipo == '1':  # Revenda
            if 'referencia' in self.initial_data and self.initial_data.get('referencia'):
                raise serializers.ValidationError({'referencia': 'Gerada automaticamente para produto de Revenda.'})
            if not colecao or not getattr(colecao, 'Codigo', None) or not getattr(colecao, 'Estacao', None):
                raise serializers.ValidationError({'colecao': 'Coleção com Código (2 dígitos) e Estação (2 dígitos) é obrigatória.'})
            if not grupo or not getattr(grupo, 'CodigoRef', None):
                raise serializers.ValidationError({'grupo': 'Grupo com CodigoRef (2 dígitos) é obrigatório.'})
            if grade is None:
                raise serializers.ValidationError({'grade': 'Obrigatória para produto de Revenda.'})
            ncm_fmt = _normalize_ncm_dotted(ncm_raw)
            ncm_qs = Ncm.objects.filter(ncm=ncm_fmt)
            if empresa:
                ncm_qs = ncm_qs.filter(empresa=empresa)
            if not ncm_qs.exists():
                raise serializers.ValidationError({'ncm': f'NCM {ncm_fmt} não cadastrado.'})
            attrs['ncm'] = ncm_fmt
        elif tipo == '2':  # Uso/Consumo
            if 'referencia' in self.initial_data and self.initial_data.get('referencia'):
                raise serializers.ValidationError({'referencia': 'Não deve ser informada para Uso/Consumo.'})
            if grade is not None:
                raise serializers.ValidationError({'grade': 'Não deve ser informada para Uso/Consumo.'})
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

    class Meta:
        model = ProdutoDetalhe
        fields = '__all__'
        extra_kwargs = {
            'codigo_item_ref': {'required': False},
        }

    def validate(self, attrs):
        for f in ('codigo_item_ref', 'ean13', 'config_ean'):
            if f in self.initial_data and self.initial_data.get(f):
                raise serializers.ValidationError({f: 'É gerado automaticamente; não envie manualmente.'})

        produto = attrs.get('produto') or getattr(self.instance, 'produto', None)
        cor = attrs.get('idcor') or getattr(self.instance, 'idcor', None)
        tamanho = attrs.get('idtamanho') or getattr(self.instance, 'idtamanho', None)

        if not produto or not tamanho:
            return attrs

        if produto.tipo_produto != '1':
            raise serializers.ValidationError('ProdutoDetalhe só é permitido para produto de Revenda.')

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

class TabelaprecoProdutoSerializer(serializers.ModelSerializer):
    class Meta:
        model = TabelaprecoProduto
        fields = '__all__'

    def validate(self, attrs):
        produto = attrs.get('produto') or getattr(self.instance, 'produto', None)
        if produto and produto.tipo_produto != '1':
            raise serializers.ValidationError('Preço só é permitido para produto de Revenda.')
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
