from rest_framework import serializers
from .models import (
    FormaPagamento, FormaPagamentoParcela,
    Caixa, ContaBancaria, MovimentacaoFinanceira,
    CashbackConfig, CashbackMovimento,
    ValeTroca, ValeTrocaMovimento,
    Pagar, PagarItem, PagarRateio,
    Receber, ReceberItem, ReceberRateio,
)

class FormaPagamentoParcelaSerializer(serializers.ModelSerializer):
    class Meta:
        model = FormaPagamentoParcela
        fields = '__all__'

class FormaPagamentoSerializer(serializers.ModelSerializer):
    parcelas = FormaPagamentoParcelaSerializer(many=True, read_only=True)

    class Meta:
        model = FormaPagamento
        fields = '__all__'


class CashbackConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = CashbackConfig
        fields = '__all__'

    def validate_percentual(self, value):
        if value < 0:
            raise serializers.ValidationError('O percentual não pode ser negativo.')
        if value > 100:
            raise serializers.ValidationError('O percentual não pode ser maior que 100%.')
        return value

    def validate_limite_uso_percentual(self, value):
        if value < 0:
            raise serializers.ValidationError('O limite de uso não pode ser negativo.')
        if value > 100:
            raise serializers.ValidationError('O limite de uso não pode ser maior que 100%.')
        return value

    def save(self, **kwargs):
        instance = super().save(**kwargs)
        if instance.ativo:
            CashbackConfig.objects.exclude(pk=instance.pk).update(ativo=False)
        return instance


class CashbackMovimentoSerializer(serializers.ModelSerializer):
    cliente_nome = serializers.CharField(source='cliente.nome_cliente', read_only=True)
    documento_origem = serializers.CharField(source='venda_origem.documento', read_only=True)
    documento_uso = serializers.CharField(source='venda_uso.documento', read_only=True)

    class Meta:
        model = CashbackMovimento
        fields = '__all__'
        read_only_fields = ('criado_por', 'criado_em')

    def validate_valor(self, value):
        if value <= 0:
            raise serializers.ValidationError('O valor deve ser maior que zero.')
        return value


class ValeTrocaMovimentoSerializer(serializers.ModelSerializer):
    venda_documento = serializers.CharField(source='venda_uso.documento', read_only=True)

    class Meta:
        model = ValeTrocaMovimento
        fields = '__all__'
        read_only_fields = ('criado_por', 'criado_em')


class ValeTrocaSerializer(serializers.ModelSerializer):
    cliente_nome = serializers.CharField(source='cliente.nome_cliente', read_only=True)
    loja_nome = serializers.CharField(source='loja.nome_loja', read_only=True)
    devolucao_documento = serializers.CharField(source='devolucao.documento', read_only=True)
    venda_origem_documento = serializers.CharField(source='devolucao.venda.documento', read_only=True)
    movimentos = ValeTrocaMovimentoSerializer(many=True, read_only=True)

    class Meta:
        model = ValeTroca
        fields = '__all__'
        read_only_fields = ('criado_por', 'criado_em', 'atualizado_em')

class PagarRateioSerializer(serializers.ModelSerializer):
    class Meta:
        model = PagarRateio
        fields = '__all__'

class PagarItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PagarItem
        fields = '__all__'

class PagarSerializer(serializers.ModelSerializer):
    itens = PagarItemSerializer(many=True, read_only=True)

    class Meta:
        model = Pagar
        fields = '__all__'


class CaixaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Caixa
        fields = '__all__'

    def validate(self, attrs):
        tipo = attrs.get('tipo_caixa', getattr(self.instance, 'tipo_caixa', Caixa.TIPO_LOJA))
        loja = attrs.get('idloja', getattr(self.instance, 'idloja', None))
        if tipo == Caixa.TIPO_LOJA and not loja:
            raise serializers.ValidationError('Informe a loja para caixa do tipo loja.')
        if tipo == Caixa.TIPO_MASTER:
            attrs['idloja'] = None
        if tipo == Caixa.TIPO_LOJA:
            codigo = attrs.get('codigo', getattr(self.instance, 'codigo', None))
            duplicado = Caixa.objects.filter(tipo_caixa=Caixa.TIPO_LOJA, codigo=codigo)
            if self.instance:
                duplicado = duplicado.exclude(pk=self.instance.pk)
            if codigo and duplicado.exists():
                raise serializers.ValidationError('Este código de caixa já está em uso por outra loja.')
        return attrs


class ContaBancariaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContaBancaria
        fields = '__all__'


class MovimentacaoFinanceiraSerializer(serializers.ModelSerializer):
    class Meta:
        model = MovimentacaoFinanceira
        fields = '__all__'

    def validate(self, attrs):
        caixa = attrs.get('caixa', getattr(self.instance, 'caixa', None))
        conta = attrs.get('conta_bancaria', getattr(self.instance, 'conta_bancaria', None))
        if not caixa and not conta:
            raise serializers.ValidationError('Informe um caixa ou uma conta bancária.')
        if caixa and conta:
            raise serializers.ValidationError('Informe apenas um destino: caixa ou conta bancária.')
        return attrs


class ReceberRateioSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReceberRateio
        fields = '__all__'


class ReceberItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReceberItem
        fields = '__all__'


class ReceberSerializer(serializers.ModelSerializer):
    itens = ReceberItemSerializer(many=True, read_only=True)

    class Meta:
        model = Receber
        fields = '__all__'
