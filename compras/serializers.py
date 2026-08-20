from rest_framework import serializers
from django.db import transaction
from decimal import Decimal

from .models import (
    PedidoCompra,
    PedidoCompraItem,
    PedidoCompraEntrega,
    PedidoCompraParcela,
    Requisicao,
    RequisicaoHistorico,
    RequisicaoItem,
    RequisicaoFinalidadeAquisicao,
    RequisicaoMaterialCategoria,
    RequisicaoServicoCategoria,
    RequisicaoSetor,
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


class RequisicaoServicoCategoriaSerializer(serializers.ModelSerializer):
    class Meta:
        model = RequisicaoServicoCategoria
        fields = "__all__"
        read_only_fields = ("empresa", "data_cadastro")


class RequisicaoSetorSerializer(serializers.ModelSerializer):
    empresa_nome = serializers.CharField(source="empresa.nome", read_only=True)

    class Meta:
        model = RequisicaoSetor
        fields = "__all__"
        read_only_fields = ("empresa", "data_cadastro")

    def validate_nome(self, value):
        if not (value or "").strip():
            raise serializers.ValidationError("Nome do setor é obrigatório.")
        return value.strip()


class RequisicaoMaterialCategoriaSerializer(serializers.ModelSerializer):
    empresa_nome = serializers.CharField(source="empresa.nome", read_only=True)

    class Meta:
        model = RequisicaoMaterialCategoria
        fields = "__all__"
        read_only_fields = ("empresa", "data_cadastro")

    def validate_nome(self, value):
        if not (value or "").strip():
            raise serializers.ValidationError("Nome da categoria é obrigatório.")
        return value.strip()


class RequisicaoFinalidadeAquisicaoSerializer(serializers.ModelSerializer):
    empresa_nome = serializers.CharField(source="empresa.nome", read_only=True)

    class Meta:
        model = RequisicaoFinalidadeAquisicao
        fields = "__all__"
        read_only_fields = ("empresa", "data_cadastro")

    def validate_nome(self, value):
        if not (value or "").strip():
            raise serializers.ValidationError("Nome da finalidade é obrigatório.")
        return value.strip()


class RequisicaoHistoricoSerializer(serializers.ModelSerializer):
    usuario_nome = serializers.CharField(source="usuario.username", read_only=True)

    class Meta:
        model = RequisicaoHistorico
        fields = "__all__"
        read_only_fields = (
            "id",
            "requisicao",
            "item",
            "usuario",
            "data_hora",
            "acao",
            "status_anterior",
            "status_novo",
            "valor_anterior",
            "valor_novo",
            "observacao",
        )


class RequisicaoItemSerializer(serializers.ModelSerializer):
    produto_descricao = serializers.CharField(source="produto.descricao", read_only=True)
    produto_referencia = serializers.CharField(source="produto.referencia", read_only=True)
    unidade_descricao = serializers.CharField(source="unidade.Descricao", read_only=True)
    categoria_material_nome = serializers.CharField(source="categoria_material.nome", read_only=True)
    finalidade_aquisicao_nome = serializers.CharField(source="finalidade_aquisicao.nome", read_only=True)
    finalidade_comportamento = serializers.CharField(source="finalidade_aquisicao.comportamento", read_only=True)
    categoria_servico_nome = serializers.CharField(source="categoria_servico.nome", read_only=True)

    class Meta:
        model = RequisicaoItem
        fields = "__all__"
        read_only_fields = ("qtd_atendida", "qtd_pendente", "status", "criado_em", "atualizado_em")

    def validate(self, attrs):
        requisicao = attrs.get("requisicao") or getattr(self.instance, "requisicao", None)
        tipo = attrs.get("tipo", getattr(self.instance, "tipo", None))
        origem = attrs.get("origem", getattr(self.instance, "origem", None))
        produto = attrs.get("produto", getattr(self.instance, "produto", None))
        unidade = attrs.get("unidade", getattr(self.instance, "unidade", None))
        categoria_servico = attrs.get("categoria_servico", getattr(self.instance, "categoria_servico", None))
        categoria_material = attrs.get("categoria_material", getattr(self.instance, "categoria_material", None))
        finalidade_aquisicao = attrs.get("finalidade_aquisicao", getattr(self.instance, "finalidade_aquisicao", None))
        finalidade = attrs.get("finalidade", getattr(self.instance, "finalidade", ""))
        qtd = Decimal(attrs.get("qtd_solicitada", getattr(self.instance, "qtd_solicitada", 0)) or 0)

        if not requisicao:
            raise serializers.ValidationError({"requisicao": "Informe a requisição."})
        if requisicao.status not in ("RASCUNHO",):
            raise serializers.ValidationError({"requisicao": "Somente requisições em rascunho permitem alterar itens."})
        if tipo not in ("MATERIAL", "SERVICO"):
            raise serializers.ValidationError({"tipo": "Tipo de item inválido."})

        empresa_id = requisicao.empresa_id
        if produto and produto.empresa_id and produto.empresa_id != empresa_id:
            raise serializers.ValidationError({"produto": "Produto pertence a outra empresa."})
        if unidade and unidade.empresa_id and unidade.empresa_id != empresa_id:
            raise serializers.ValidationError({"unidade": "Unidade pertence a outra empresa."})
        if categoria_servico and categoria_servico.empresa_id != empresa_id:
            raise serializers.ValidationError({"categoria_servico": "Categoria de serviço pertence a outra empresa."})
        if categoria_material and categoria_material.empresa_id != empresa_id:
            raise serializers.ValidationError({"categoria_material": "Categoria de material pertence a outra empresa."})
        if finalidade_aquisicao and finalidade_aquisicao.empresa_id != empresa_id:
            raise serializers.ValidationError({"finalidade_aquisicao": "Finalidade pertence a outra empresa."})
        if tipo == "MATERIAL" and not finalidade_aquisicao and finalidade:
            finalidade_aquisicao = RequisicaoFinalidadeAquisicao.objects.filter(
                empresa_id=empresa_id,
                comportamento=finalidade,
            ).first()
            if finalidade_aquisicao:
                attrs["finalidade_aquisicao"] = finalidade_aquisicao
            else:
                raise serializers.ValidationError({"finalidade": "Finalidade inválida."})

        if tipo == "MATERIAL":
            if origem not in ("PRODUTO", "LIVRE"):
                raise serializers.ValidationError({"origem": "Material deve ser cadastrado ou livre."})
            if not finalidade_aquisicao:
                raise serializers.ValidationError({"finalidade_aquisicao": "Informe a finalidade da aquisição."})
            if finalidade_aquisicao and not finalidade_aquisicao.ativo and not (self.instance and self.instance.finalidade_aquisicao_id == finalidade_aquisicao.id):
                raise serializers.ValidationError({"finalidade_aquisicao": "Finalidade inativa não pode ser utilizada em nova requisição."})
            attrs["finalidade"] = finalidade_aquisicao.comportamento if finalidade_aquisicao else finalidade
            if origem == "PRODUTO" and not produto:
                raise serializers.ValidationError({"produto": "Informe o produto do material cadastrado."})
            if origem == "PRODUTO":
                if produto.tipo_produto != "2":
                    raise serializers.ValidationError({"produto": "Use somente Produto de Uso e Consumo."})
                if not getattr(produto, "ativo", True):
                    raise serializers.ValidationError({"produto": "Produto inativo não pode ser requisitado."})
                attrs["unidade"] = produto.unidade
                unidade = produto.unidade
            if origem == "LIVRE" and not (attrs.get("descricao", getattr(self.instance, "descricao", "")) or "").strip():
                raise serializers.ValidationError({"descricao": "Informe a descrição do item livre."})
            if origem == "LIVRE":
                if not categoria_material:
                    raise serializers.ValidationError({"categoria_material": "Informe a categoria do material."})
                if not categoria_material.ativo and not (self.instance and self.instance.categoria_material_id == categoria_material.id):
                    raise serializers.ValidationError({"categoria_material": "Categoria de material inativa não pode ser utilizada em nova requisição."})
                attrs["categoria"] = categoria_material.nome
            if origem == "LIVRE" and not unidade:
                raise serializers.ValidationError({"unidade": "Informe a unidade do item livre."})
            if qtd <= 0:
                raise serializers.ValidationError({"qtd_solicitada": "Informe uma quantidade maior que zero."})
            if unidade and not unidade.permite_decimal and qtd != qtd.to_integral_value():
                raise serializers.ValidationError({"qtd_solicitada": f"A unidade {unidade.Descricao} não aceita quantidade decimal."})
        else:
            if origem != "SERVICO":
                raise serializers.ValidationError({"origem": "Serviço deve usar origem SERVICO."})
            if finalidade:
                raise serializers.ValidationError({"finalidade": "Serviço não utiliza finalidade de material."})
            if finalidade_aquisicao:
                raise serializers.ValidationError({"finalidade_aquisicao": "Serviço não utiliza finalidade de material."})
            if categoria_material:
                raise serializers.ValidationError({"categoria_material": "Serviço não utiliza categoria de material."})
            if produto:
                raise serializers.ValidationError({"produto": "Serviço não utiliza produto."})
            if not (attrs.get("titulo_servico", getattr(self.instance, "titulo_servico", "")) or "").strip():
                raise serializers.ValidationError({"titulo_servico": "Informe o título do serviço."})
            if not (attrs.get("descricao_servico", getattr(self.instance, "descricao_servico", "")) or "").strip():
                raise serializers.ValidationError({"descricao_servico": "Informe a descrição do serviço."})
            if not categoria_servico:
                raise serializers.ValidationError({"categoria_servico": "Informe a categoria do serviço."})
            if not attrs.get("tipo_servico", getattr(self.instance, "tipo_servico", "")):
                raise serializers.ValidationError({"tipo_servico": "Informe o tipo do serviço."})
            attrs["qtd_solicitada"] = Decimal("1.000")

        return attrs

    def _sync_quantities(self, obj):
        obj.qtd_atendida = Decimal(obj.qtd_atendida or 0)
        obj.qtd_pendente = max(Decimal(obj.qtd_solicitada or 0) - obj.qtd_atendida, Decimal("0"))

    def create(self, validated_data):
        obj = RequisicaoItem(**validated_data)
        self._sync_quantities(obj)
        obj.save()
        return obj

    def update(self, instance, validated_data):
        for key, value in validated_data.items():
            setattr(instance, key, value)
        self._sync_quantities(instance)
        instance.save()
        return instance


class RequisicaoSerializer(serializers.ModelSerializer):
    itens = RequisicaoItemSerializer(many=True, read_only=True)
    historico = RequisicaoHistoricoSerializer(many=True, read_only=True)
    requisitante_nome = serializers.CharField(source="requisitante.username", read_only=True)
    loja_nome = serializers.CharField(source="loja.nome_loja", read_only=True)
    setor_nome = serializers.CharField(source="setor.nome", read_only=True)

    class Meta:
        model = Requisicao
        fields = "__all__"
        read_only_fields = (
            "numero",
            "empresa",
            "requisitante",
            "status",
            "criado_por",
            "aprovado_por",
            "aprovado_em",
            "criado_em",
            "atualizado_em",
        )

    def validate(self, attrs):
        loja = attrs.get("loja", getattr(self.instance, "loja", None))
        if not loja:
            raise serializers.ValidationError({"loja": "Informe a loja/unidade."})
        if self.instance and self.instance.status not in ("RASCUNHO",):
            allowed = {"observacoes"}
            protected = set(attrs.keys()) - allowed
            if protected:
                raise serializers.ValidationError("Somente rascunhos podem alterar campos protegidos da requisição.")
        setor = attrs.get("setor", getattr(self.instance, "setor", None))
        if not setor:
            raise serializers.ValidationError({"setor": "Informe o setor."})
        if loja and setor and setor.empresa_id != loja.empresa_id:
            raise serializers.ValidationError({"setor": "Setor pertence a outra empresa."})
        if setor and (not setor.ativo or not setor.pode_fazer_requisicao):
            raise serializers.ValidationError({"setor": "Setor inativo ou não habilitado para requisições."})
        return attrs
