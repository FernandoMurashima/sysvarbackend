from rest_framework import serializers
from django.db import transaction

from .models import (
    PedidoCompra,
    PedidoCompraItem,
    PedidoCompraEntrega,
    PedidoCompraParcela,
)

# ----------------- Itens -----------------
class PedidoCompraItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PedidoCompraItem
        fields = "__all__"

    def validate(self, attrs):
        pedido = attrs.get("pedido") or getattr(self.instance, "pedido", None)
        tipo = pedido.tipo if pedido else None

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

        elif tipo == "2":  # Uso/Consumo
            if attrs.get("pack") or attrs.get("n_packs", 0):
                raise serializers.ValidationError({"pack": "Não permitido em Uso/Consumo."})
        else:
            raise serializers.ValidationError({"pedido": "Tipo de pedido inválido."})

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        obj = PedidoCompraItem(**validated_data)
        obj.recalcular_totais()
        obj.save()
        pedido = obj.pedido
        pedido.recomputa_totais()
        pedido.save(update_fields=["total_itens", "total_desconto", "frete", "total_pedido"])
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

    # proteção: forma de pagamento setada via ação específica
    forma_pagamento = serializers.CharField(read_only=True)

    class Meta:
        model = PedidoCompra
        fields = "__all__"
        read_only_fields = (
            "total_itens",
            "total_desconto",
            "frete",
            "total_pedido",
            "data_cadastro",
            "forma_pagamento",
        )

    def validate(self, attrs):
        tipo = attrs.get("tipo", getattr(self.instance, "tipo", None))
        if tipo not in ("1", "2"):
            raise serializers.ValidationError({"tipo": "Tipo inválido (use 1=Revenda, 2=Uso/Consumo)."})
        return attrs
