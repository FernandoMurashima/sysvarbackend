from rest_framework import serializers

from fiscal.models import (
    NFCe,
    NFeDevolucao,
    VendaDevolucao,
    VendaDevolucaoItem,
    VendaPdv,
    VendaPdvItem,
    VendaPdvPagamento,
)


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


class NFeDevolucaoSerializer(serializers.ModelSerializer):
    class Meta:
        model = NFeDevolucao
        fields = "__all__"
        read_only_fields = (
            "status",
            "chave_acesso",
            "protocolo",
            "xml",
            "retorno_codigo",
            "retorno_mensagem",
            "autorizada_em",
            "criado_em",
            "atualizado_em",
        )


class VendaPdvPagamentoSerializer(serializers.ModelSerializer):
    class Meta:
        model = VendaPdvPagamento
        fields = "__all__"
        read_only_fields = ("venda", "criado_em")


class VendaPdvSerializer(serializers.ModelSerializer):
    itens = VendaPdvItemSerializer(many=True, read_only=True)
    pagamentos = VendaPdvPagamentoSerializer(many=True, read_only=True)
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


class VendaDevolucaoItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = VendaDevolucaoItem
        fields = "__all__"
        read_only_fields = ("devolucao", "total_item")


class VendaDevolucaoSerializer(serializers.ModelSerializer):
    itens = VendaDevolucaoItemSerializer(many=True, read_only=True)
    nfe_devolucao = NFeDevolucaoSerializer(read_only=True)
    vale_troca = serializers.SerializerMethodField()

    class Meta:
        model = VendaDevolucao
        fields = "__all__"
        read_only_fields = (
            "documento",
            "status",
            "subtotal",
            "credito_cliente",
            "criado_por",
            "criado_em",
            "atualizado_em",
        )

    def get_vale_troca(self, obj):
        vale = getattr(obj, "vale_troca", None)
        if not vale:
            return None
        return {
            "id": vale.Idvaletroca,
            "documento": vale.documento,
            "valor_original": str(vale.valor_original),
            "saldo": str(vale.saldo),
            "status": vale.status,
        }
