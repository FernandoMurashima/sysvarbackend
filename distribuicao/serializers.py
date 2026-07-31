from decimal import Decimal

from rest_framework import serializers

from cadastros.models import Loja

from .models import (
    Distribuicao,
    DistribuicaoDestino,
    DistribuicaoItem,
    MercadoriaTransito,
    PedidoVendaDistribuicao,
    PedidoVendaDistribuicaoItem,
    PerfilDistribuicao,
    PerfilDistribuicaoItem,
)


class PerfilDistribuicaoItemSerializer(serializers.ModelSerializer):
    loja_nome = serializers.CharField(source="loja.nome_loja", read_only=True)

    class Meta:
        model = PerfilDistribuicaoItem
        fields = "__all__"

    def validate(self, attrs):
        perfil = attrs.get("perfil") or getattr(self.instance, "perfil", None)
        loja = attrs.get("loja") or getattr(self.instance, "loja", None)
        if perfil and loja:
            if loja.empresa_id != perfil.empresa_id:
                raise serializers.ValidationError({"loja": "Loja pertence a outra empresa."})
            if not loja.ativo:
                raise serializers.ValidationError({"loja": "Loja inativa não pode participar do perfil."})
            if loja.tipo_unidade == Loja.TIPO_FABRICA:
                raise serializers.ValidationError({"loja": "Fábrica não deve ser loja destino do perfil."})
        return attrs


class PerfilDistribuicaoSerializer(serializers.ModelSerializer):
    itens = PerfilDistribuicaoItemSerializer(many=True, read_only=True)
    total_percentual = serializers.SerializerMethodField()

    class Meta:
        model = PerfilDistribuicao
        fields = "__all__"
        read_only_fields = ("empresa", "criado_por", "data_cadastro", "atualizado_em")

    def get_total_percentual(self, obj):
        return sum((i.percentual or Decimal("0")) for i in obj.itens.filter(ativo=True))

    def validate(self, attrs):
        return attrs


class DistribuicaoDestinoSerializer(serializers.ModelSerializer):
    loja_nome = serializers.CharField(source="loja_destino.nome_loja", read_only=True)

    class Meta:
        model = DistribuicaoDestino
        fields = "__all__"
        read_only_fields = ("pedido", "pedido_item", "status")


class DistribuicaoItemSerializer(serializers.ModelSerializer):
    destinos = DistribuicaoDestinoSerializer(many=True, read_only=True)

    class Meta:
        model = DistribuicaoItem
        fields = "__all__"


class DistribuicaoSerializer(serializers.ModelSerializer):
    unidade_origem_nome = serializers.CharField(source="unidade_origem.nome_loja", read_only=True)
    perfil_descricao = serializers.CharField(source="perfil.descricao", read_only=True)
    itens = DistribuicaoItemSerializer(many=True, read_only=True)
    destinos = DistribuicaoDestinoSerializer(many=True, read_only=True)
    pedidos_count = serializers.SerializerMethodField()

    class Meta:
        model = Distribuicao
        fields = "__all__"
        read_only_fields = (
            "empresa",
            "numero",
            "status",
            "quantidade_total",
            "valor_total_custo",
            "valor_total_venda",
            "criado_por",
            "confirmado_por",
            "data_cadastro",
            "atualizado_em",
            "data_confirmacao",
            "data_cancelamento",
            "motivo_cancelamento",
        )

    def get_pedidos_count(self, obj):
        return obj.pedidos_venda.count()

    def validate(self, attrs):
        empresa = attrs.get("empresa") or getattr(self.instance, "empresa", None)
        origem = attrs.get("unidade_origem") or getattr(self.instance, "unidade_origem", None)
        perfil = attrs.get("perfil") or getattr(self.instance, "perfil", None)
        if empresa and origem and origem.empresa_id != empresa.id:
            raise serializers.ValidationError({"unidade_origem": "Origem pertence a outra empresa."})
        if origem and not origem.ativo:
            raise serializers.ValidationError({"unidade_origem": "Origem inativa."})
        if perfil and empresa and perfil.empresa_id != empresa.id:
            raise serializers.ValidationError({"perfil": "Perfil pertence a outra empresa."})
        return attrs


class DistribuicaoListSerializer(serializers.ModelSerializer):
    unidade_origem_nome = serializers.CharField(source="unidade_origem.nome_loja", read_only=True)
    perfil_descricao = serializers.CharField(source="perfil.descricao", read_only=True)
    pedidos_count = serializers.SerializerMethodField()

    class Meta:
        model = Distribuicao
        fields = (
            "id",
            "empresa",
            "numero",
            "unidade_origem",
            "unidade_origem_nome",
            "data",
            "tipo",
            "perfil",
            "perfil_descricao",
            "fator_preco",
            "origem_operacao",
            "origem_id",
            "status",
            "observacao",
            "quantidade_total",
            "valor_total_custo",
            "valor_total_venda",
            "pedidos_count",
        )

    def get_pedidos_count(self, obj):
        return obj.pedidos_venda.count()


class PedidoVendaDistribuicaoItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PedidoVendaDistribuicaoItem
        fields = "__all__"


class PedidoVendaDistribuicaoSerializer(serializers.ModelSerializer):
    loja_destino_nome = serializers.CharField(source="loja_destino.nome_loja", read_only=True)
    unidade_origem_nome = serializers.CharField(source="unidade_origem.nome_loja", read_only=True)
    itens = PedidoVendaDistribuicaoItemSerializer(many=True, read_only=True)

    class Meta:
        model = PedidoVendaDistribuicao
        fields = "__all__"


class PedidoVendaDistribuicaoListSerializer(serializers.ModelSerializer):
    loja_destino_nome = serializers.CharField(source="loja_destino.nome_loja", read_only=True)
    unidade_origem_nome = serializers.CharField(source="unidade_origem.nome_loja", read_only=True)
    distribuicao_numero = serializers.CharField(source="distribuicao.numero", read_only=True)
    itens_count = serializers.SerializerMethodField()

    class Meta:
        model = PedidoVendaDistribuicao
        fields = (
            "id",
            "numero",
            "distribuicao",
            "distribuicao_numero",
            "unidade_origem",
            "unidade_origem_nome",
            "loja_destino",
            "loja_destino_nome",
            "data_pedido",
            "status",
            "quantidade_total",
            "valor_total_custo",
            "valor_total_venda",
            "faturamento_status",
            "nfe_numero",
            "nfe_status",
            "itens_count",
        )

    def get_itens_count(self, obj):
        return obj.itens.count()


class MercadoriaTransitoSerializer(serializers.ModelSerializer):
    loja_destino_nome = serializers.CharField(source="loja_destino.nome_loja", read_only=True)
    unidade_origem_nome = serializers.CharField(source="unidade_origem.nome_loja", read_only=True)
    pedido_numero = serializers.CharField(source="pedido.numero", read_only=True)
    nfe_numero = serializers.CharField(source="pedido.nfe_numero", read_only=True)
    nfe_chave = serializers.CharField(source="pedido.nfe_chave", read_only=True)
    referencia = serializers.CharField(source="pedido_item.referencia", read_only=True)
    descricao = serializers.CharField(source="pedido_item.descricao", read_only=True)
    cor_descricao = serializers.CharField(source="pedido_item.cor_descricao", read_only=True)
    tamanho_descricao = serializers.CharField(source="pedido_item.tamanho_descricao", read_only=True)
    custo_unitario = serializers.DecimalField(source="pedido_item.custo_unitario", max_digits=12, decimal_places=4, read_only=True)
    valor_unitario = serializers.DecimalField(source="pedido_item.preco_unitario", max_digits=12, decimal_places=4, read_only=True)

    class Meta:
        model = MercadoriaTransito
        fields = "__all__"
