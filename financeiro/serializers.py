from rest_framework import serializers
from .models import (
    FormaPagamento, FormaPagamentoParcela,
    ConfigFinanceira, TipoDespesaPdv,
    Caixa, ContaBancaria, MovimentacaoFinanceira,
    LancamentoContabil,
    CashbackConfig, CashbackMovimento,
    ValeTroca, ValeTrocaMovimento,
    Pagar, PagarItem, PagarRateio,
    Receber, ReceberItem, ReceberRateio,
    AntecipacaoRecebivel, AntecipacaoRecebivelItem,
)

class ConfigFinanceiraSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConfigFinanceira
        fields = '__all__'
        read_only_fields = ('empresa', 'atualizado_em')


class TipoDespesaPdvSerializer(serializers.ModelSerializer):
    natureza_codigo = serializers.CharField(source='Idnatureza.codigo', read_only=True)
    natureza_descricao = serializers.CharField(source='Idnatureza.descricao', read_only=True)

    class Meta:
        model = TipoDespesaPdv
        fields = '__all__'
        read_only_fields = ('data_cadastro',)

    def validate(self, attrs):
        natureza = attrs.get('Idnatureza', getattr(self.instance, 'Idnatureza', None))
        empresa = attrs.get('empresa', getattr(self.instance, 'empresa', None))
        if not natureza:
            raise serializers.ValidationError({'Idnatureza': 'Informe a natureza vinculada.'})
        if getattr(natureza, 'ativo', True) is False:
            raise serializers.ValidationError({'Idnatureza': 'A natureza selecionada está inativa.'})
        if empresa and getattr(natureza, 'empresa_id', None) and natureza.empresa_id != empresa.id:
            raise serializers.ValidationError({'Idnatureza': 'A natureza pertence a outra empresa.'})
        if str(getattr(natureza, 'natureza_operacao', '') or '').upper() not in {'DESPESA', 'AJUSTE'}:
            raise serializers.ValidationError({'Idnatureza': 'Use uma natureza de despesa ou ajuste.'})
        return attrs


class FormaPagamentoParcelaSerializer(serializers.ModelSerializer):
    class Meta:
        model = FormaPagamentoParcela
        fields = '__all__'

class FormaPagamentoSerializer(serializers.ModelSerializer):
    parcelas = FormaPagamentoParcelaSerializer(many=True, read_only=True)

    class Meta:
        model = FormaPagamento
        fields = '__all__'

    def validate(self, attrs):
        gera = attrs.get('gera_recebivel_bancario', getattr(self.instance, 'gera_recebivel_bancario', False))
        conta = attrs.get('conta_liquidacao', getattr(self.instance, 'conta_liquidacao', None))
        empresa = attrs.get('empresa', getattr(self.instance, 'empresa', None))
        if gera and not conta:
            raise serializers.ValidationError({'conta_liquidacao': 'Informe a conta de liquidação.'})
        if conta and empresa and getattr(conta, 'empresa_id', None) and conta.empresa_id != empresa.id:
            raise serializers.ValidationError({'conta_liquidacao': 'A conta de liquidação pertence a outra empresa.'})
        return attrs


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
            CashbackConfig.objects.filter(empresa=instance.empresa).exclude(pk=instance.pk).update(ativo=False)
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

    def validate(self, attrs):
        natureza = attrs.get('Idnatureza', getattr(self.instance, 'Idnatureza', None))
        if not natureza:
            raise serializers.ValidationError({'Idnatureza': 'Informe a natureza de lançamento.'})
        if natureza and getattr(natureza, 'ativo', True) is False:
            raise serializers.ValidationError({'Idnatureza': 'A natureza selecionada está inativa.'})
        operacao = str(getattr(natureza, 'natureza_operacao', '') or '').upper()
        if operacao not in {'DESPESA', 'AJUSTE'}:
            raise serializers.ValidationError({'Idnatureza': 'Contas a pagar deve usar natureza de despesa ou ajuste.'})
        return attrs


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
        natureza = attrs.get('Idnatureza', getattr(self.instance, 'Idnatureza', None))
        if not caixa and not conta:
            raise serializers.ValidationError('Informe um caixa ou uma conta bancária.')
        if caixa and conta:
            raise serializers.ValidationError('Informe apenas um destino: caixa ou conta bancária.')
        if not natureza:
            raise serializers.ValidationError({'Idnatureza': 'Informe a natureza de lançamento.'})
        if natureza and getattr(natureza, 'ativo', True) is False:
            raise serializers.ValidationError({'Idnatureza': 'A natureza selecionada está inativa.'})
        tipo = attrs.get('tipo', getattr(self.instance, 'tipo', None))
        operacao = str(getattr(natureza, 'natureza_operacao', '') or '').upper()
        if tipo == MovimentacaoFinanceira.TIPO_ENTRADA and operacao not in {'RECEITA', 'AJUSTE'}:
            raise serializers.ValidationError({'Idnatureza': 'Entrada deve usar natureza de receita ou ajuste.'})
        if tipo == MovimentacaoFinanceira.TIPO_SAIDA and operacao not in {'DESPESA', 'AJUSTE'}:
            raise serializers.ValidationError({'Idnatureza': 'Saída deve usar natureza de despesa ou ajuste.'})
        if tipo == MovimentacaoFinanceira.TIPO_TRANSFERENCIA and operacao not in {'TRANSFERENCIA', 'AJUSTE'}:
            raise serializers.ValidationError({'Idnatureza': 'Transferência deve usar natureza de transferência ou ajuste.'})
        return attrs


class LancamentoContabilSerializer(serializers.ModelSerializer):
    loja_nome = serializers.CharField(source='idloja.nome_loja', read_only=True)
    natureza_descricao = serializers.CharField(source='natureza.descricao', read_only=True)
    conta_debito_codigo = serializers.CharField(source='conta_debito.codigo', read_only=True)
    conta_debito_descricao = serializers.CharField(source='conta_debito.descricao', read_only=True)
    conta_credito_codigo = serializers.CharField(source='conta_credito.codigo', read_only=True)
    conta_credito_descricao = serializers.CharField(source='conta_credito.descricao', read_only=True)

    class Meta:
        model = LancamentoContabil
        fields = '__all__'
        read_only_fields = (
            'empresa', 'idloja', 'movimentacao', 'data_lancamento', 'documento',
            'historico', 'origem', 'natureza', 'conta_debito', 'conta_credito',
            'valor', 'status', 'observacao', 'data_cadastro',
        )


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

    def validate(self, attrs):
        natureza = attrs.get('Idnatureza', getattr(self.instance, 'Idnatureza', None))
        if not natureza:
            raise serializers.ValidationError({'Idnatureza': 'Informe a natureza de lançamento.'})
        if natureza and getattr(natureza, 'ativo', True) is False:
            raise serializers.ValidationError({'Idnatureza': 'A natureza selecionada está inativa.'})
        operacao = str(getattr(natureza, 'natureza_operacao', '') or '').upper()
        if operacao not in {'RECEITA', 'AJUSTE'}:
            raise serializers.ValidationError({'Idnatureza': 'Contas a receber deve usar natureza de receita ou ajuste.'})
        return attrs


class AntecipacaoRecebivelItemSerializer(serializers.ModelSerializer):
    documento = serializers.CharField(source='movimentacao.documento', read_only=True)
    vencimento = serializers.DateField(source='movimentacao.data_movimento', read_only=True)
    forma_pagamento = serializers.CharField(source='movimentacao.FormaPagamento', read_only=True)

    class Meta:
        model = AntecipacaoRecebivelItem
        fields = '__all__'


class AntecipacaoRecebivelSerializer(serializers.ModelSerializer):
    itens = AntecipacaoRecebivelItemSerializer(many=True, read_only=True)
    loja_nome = serializers.CharField(source='idloja.nome_loja', read_only=True)
    conta_nome = serializers.CharField(source='conta_bancaria.descricao', read_only=True)

    class Meta:
        model = AntecipacaoRecebivel
        fields = '__all__'
        read_only_fields = ('criado_por', 'criado_em')
