from rest_framework import serializers
from django.db import transaction
from decimal import Decimal

from .models import (
    PedidoCompra,
    PedidoCompraItem,
    PedidoCompraEntrega,
    PedidoCompraParcela,
)

try:
    from financeiro.models import Pagar
except Exception:
    Pagar = None

# ----------------- Itens -----------------
TIPOS_COMPRA_PRODUTO = ("1", "2", "4")


class PedidoCompraItemSerializer(serializers.ModelSerializer):
    produto_descricao = serializers.CharField(source="produto.descricao", read_only=True)
    produto_referencia = serializers.CharField(source="produto.referencia", read_only=True)

    class Meta:
        model = PedidoCompraItem
        fields = "__all__"

    def validate(self, attrs):
        pedido = attrs.get("pedido") or getattr(self.instance, "pedido", None)
        produto = attrs.get("produto", getattr(self.instance, "produto", None))
        qtd = attrs.get("qtd", getattr(self.instance, "qtd", 0))

        if not pedido:
            raise serializers.ValidationError({"pedido": "Informe o pedido."})
        if pedido.status != "AB":
            raise serializers.ValidationError({"pedido": "Somente pedidos em aberto (AB) permitem alteração de itens."})
        if not produto:
            raise serializers.ValidationError({"produto": "Informe o produto."})

        produto_tipo = str(getattr(produto, "tipo_produto", "") or "")
        if produto_tipo not in TIPOS_COMPRA_PRODUTO:
            raise serializers.ValidationError({"produto": "Produto não participa de Compras."})
        if pedido.tipo and produto_tipo != pedido.tipo:
            raise serializers.ValidationError({"produto": "Pedido de Compra não permite misturar produtos de tipos diferentes."})

        tipo = pedido.tipo or produto_tipo
        if tipo == "1":  # Revenda
            # produto + cor + pack obrigatórios; n_packs >=1; sem descricao_livre
            for f in ("produto", "cor", "pack"):
                if not attrs.get(f) and not getattr(self.instance, f, None):
                    raise serializers.ValidationError({f: "Obrigatório para Revenda."})
            n_packs = attrs.get("n_packs", getattr(self.instance, "n_packs", 0))
            if not n_packs or n_packs < 1:
                raise serializers.ValidationError({"n_packs": "Informe n_packs >= 1."})
            if attrs.get("descricao_livre"):
                raise serializers.ValidationError({"descricao_livre": "Não permitido em Revenda."})
            if qtd is not None and Decimal(qtd) != Decimal(qtd).to_integral_value():
                raise serializers.ValidationError({"qtd": "Pedido de revenda não aceita quantidade decimal."})

        elif tipo in ("2", "4"):  # Uso/Consumo ou Insumo
            if attrs.get("pack") or attrs.get("n_packs", 0):
                raise serializers.ValidationError({"pack": "Não permitido em Uso/Consumo ou Insumo."})
            if qtd is None or Decimal(qtd) <= 0:
                raise serializers.ValidationError({"qtd": "Informe uma quantidade maior que zero."})
            unidade = getattr(produto, "unidade", None)
            if unidade and not unidade.permite_decimal and Decimal(qtd) != Decimal(qtd).to_integral_value():
                raise serializers.ValidationError({
                    "qtd": f"A unidade {unidade.Descricao} não aceita quantidade decimal."
                })
        else:
            raise serializers.ValidationError({"pedido": "Tipo de pedido inválido."})

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        obj = PedidoCompraItem(**validated_data)
        pedido = obj.pedido
        tipo_anterior = pedido.tipo
        if not pedido.tipo:
            pedido.tipo = obj.produto.tipo_produto
        obj.recalcular_totais()
        obj.save()
        pedido.recomputa_totais()
        update_fields = ["total_itens", "total_desconto", "frete", "total_pedido"]
        if pedido.tipo != tipo_anterior:
            update_fields.append("tipo")
        pedido.save(update_fields=update_fields)
        return obj

    @transaction.atomic
    def update(self, instance, validated_data):
        for k, v in validated_data.items():
            setattr(instance, k, v)
        instance.recalcular_totais()
        instance.save()
        pedido = instance.pedido
        pedido.recomputa_totais()
        pedido.save(update_fields=["total_itens", "total_desconto", "frete", "total_pedido"])
        return instance


# ----------------- Entregas -----------------
class PedidoCompraEntregaSerializer(serializers.ModelSerializer):
    class Meta:
        model = PedidoCompraEntrega
        fields = "__all__"


# ----------------- Parcelas do Pedido (planejamento) -----------------
class PedidoCompraParcelaSerializer(serializers.ModelSerializer):
    class Meta:
        model = PedidoCompraParcela
        fields = "__all__"
        read_only_fields = ("data_cadastro",)


# ----------------- Pedido -----------------
class PedidoCompraSerializer(serializers.ModelSerializer):
    itens = PedidoCompraItemSerializer(many=True, read_only=True)
    parcelas = PedidoCompraParcelaSerializer(many=True, read_only=True)
    idnatureza = serializers.SerializerMethodField()
    natureza_label = serializers.SerializerMethodField()

    # proteção: forma de pagamento setada via ação específica
    forma_pagamento = serializers.CharField(read_only=True)
    prazo_pagamento_descricao = serializers.CharField(source="prazo_pagamento.descricao", read_only=True)

    class Meta:
        model = PedidoCompra
        fields = "__all__"
        read_only_fields = (
            "total_itens",
            "total_pedido",
            "data_cadastro",
            "forma_pagamento",
            "tipo",
        )

    def validate(self, attrs):
        attrs.pop("tipo", None)
        frete = Decimal(attrs.get("frete", getattr(self.instance, "frete", 0)) or 0)
        total_desconto = Decimal(attrs.get("total_desconto", getattr(self.instance, "total_desconto", 0)) or 0)
        if frete < 0:
            raise serializers.ValidationError({"frete": "Informe frete maior ou igual a zero."})
        if total_desconto < 0:
            raise serializers.ValidationError({"total_desconto": "Informe desconto geral maior ou igual a zero."})
        total_itens = Decimal(getattr(self.instance, "total_itens", 0) or 0)
        if self.instance and (total_itens - total_desconto + frete) < 0:
            raise serializers.ValidationError({"total_pedido": "Total do pedido não pode ser negativo."})
        return attrs

    def _pagar_do_pedido(self, obj):
        if not Pagar:
            return None
        return (
            Pagar.objects
            .select_related("Idnatureza")
            .filter(empresa=obj.empresa, pedido_compra=obj.id)
            .order_by("-Idpagar")
            .first()
        )

    def get_idnatureza(self, obj):
        pagar = self._pagar_do_pedido(obj)
        return getattr(pagar, "Idnatureza_id", None)

    def get_natureza_label(self, obj):
        pagar = self._pagar_do_pedido(obj)
        natureza = getattr(pagar, "Idnatureza", None)
        if not natureza:
            return None
        return f"{natureza.codigo} - {natureza.descricao}"
