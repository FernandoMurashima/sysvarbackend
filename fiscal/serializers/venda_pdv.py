from rest_framework import serializers

from fiscal.models import NFCe, VendaPdv, VendaPdvItem


class VendaPdvItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = VendaPdvItem
        fields = "__all__"
        read_only_fields = ("venda", "total_item")


class NFCeSerializer(serializers.ModelSerializer):
    class Meta:
        model = NFCe
        fields = "__all__"
        read_only_fields = (
            "status",
            "chave_acesso",
            "protocolo",
            "qr_code_url",
            "xml",
            "retorno_codigo",
            "retorno_mensagem",
            "autorizada_em",
            "criado_em",
            "atualizado_em",
        )


class VendaPdvSerializer(serializers.ModelSerializer):
    itens = VendaPdvItemSerializer(many=True, read_only=True)
    nfce = NFCeSerializer(read_only=True)

    class Meta:
        model = VendaPdv
        fields = "__all__"
        read_only_fields = (
            "documento",
            "status",
            "subtotal",
            "desconto_itens",
            "total",
            "troco",
            "criado_por",
            "criado_em",
            "atualizado_em",
        )
