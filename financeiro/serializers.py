from rest_framework import serializers
from .models import FormaPagamento, FormaPagamentoParcela, Pagar, PagarItem, PagarRateio

class FormaPagamentoParcelaSerializer(serializers.ModelSerializer):
    class Meta:
        model = FormaPagamentoParcela
        fields = '__all__'

class FormaPagamentoSerializer(serializers.ModelSerializer):
    parcelas = FormaPagamentoParcelaSerializer(many=True, read_only=True)

    class Meta:
        model = FormaPagamento
        fields = '__all__'

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
