from rest_framework import serializers

from fiscal.models import NotaFiscalSaida, NotaFiscalSaidaItem


class NotaFiscalSaidaItemSerializer(serializers.ModelSerializer):
    produto_descricao = serializers.CharField(source="produto.descricao", read_only=True)
    sku_ean = serializers.CharField(source="sku.ean13", read_only=True)

    class Meta:
        model = NotaFiscalSaidaItem
        fields = "__all__"


class NotaFiscalSaidaSerializer(serializers.ModelSerializer):
    itens = NotaFiscalSaidaItemSerializer(many=True, read_only=True)
    loja_origem_nome = serializers.CharField(source="loja_origem.nome_loja", read_only=True)
    loja_destino_nome = serializers.CharField(source="loja_destino.nome_loja", read_only=True)
    ordem_producao_numero = serializers.CharField(source="ordem_producao.numero", read_only=True)

    class Meta:
        model = NotaFiscalSaida
        fields = "__all__"
