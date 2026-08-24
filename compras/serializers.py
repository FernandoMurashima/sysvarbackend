from rest_framework import serializers
from django.db import transaction
from django.db.models import Q
from decimal import Decimal

from accounts.services.effective_access import EffectiveAccessService
from compras.services_necessidade import indicador_requisicao_item

from .models import (
    Cotacao,
    CotacaoFornecedor,
    CotacaoItem,
    CotacaoProposta,
    CotacaoPropostaItem,
    CotacaoRequisicao,
    OrdemServico,
    PedidoCompra,
    PedidoCompraItem,
    PedidoCompraEntrega,
    PedidoCompraParcela,
    Requisicao,
    RequisicaoHistorico,
    RequisicaoItem,
    RequisicaoFinalidadeAquisicao,
    RequisicaoMaterialCategoria,
    RequisicaoMatrizResponsabilidade,
    RequisicaoServicoCategoria,
    RequisicaoSetor,
)

try:
    from financeiro.models import FormaPagamento, Pagar, PrazoPagamento
except Exception:
    FormaPagamento = None
    Pagar = None
    PrazoPagamento = None

# ----------------- Itens -----------------
TIPOS_COMPRA_PRODUTO = ("1", "2", "4")


class PedidoCompraItemSerializer(serializers.ModelSerializer):
    produto_descricao = serializers.CharField(source="produto.descricao", read_only=True)
    produto_referencia = serializers.CharField(source="produto.referencia", read_only=True)
    unidade_descricao = serializers.SerializerMethodField()

    class Meta:
        model = PedidoCompraItem
        fields = "__all__"

    def get_unidade_descricao(self, obj):
        unidade = getattr(obj, "unidade", None) or getattr(getattr(obj, "produto", None), "unidade", None)
        return getattr(unidade, "Descricao", "") or ""

    def validate(self, attrs):
        pedido = attrs.get("pedido") or getattr(self.instance, "pedido", None)
        produto = attrs.get("produto", getattr(self.instance, "produto", None))
        qtd = attrs.get("qtd", getattr(self.instance, "qtd", 0))

        if not pedido:
            raise serializers.ValidationError({"pedido": "Informe o pedido."})
        if pedido.cotacao_origem_id:
            raise serializers.ValidationError({"pedido": "Pedido originado de cotação aprovada não permite alteração comercial."})
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
            if unidade and not attrs.get("unidade", getattr(self.instance, "unidade", None)):
                attrs["unidade"] = unidade
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
    cotacao_origem_numero = serializers.IntegerField(source="cotacao_origem.numero", read_only=True)

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
        if self.instance and self.instance.cotacao_origem_id:
            protegidos = {"loja", "fornecedor", "forma_pagamento", "prazo_pagamento", "total_desconto", "frete", "outras_despesas", "observacoes"}
            if set(attrs.keys()) & protegidos:
                raise serializers.ValidationError("Pedido originado de cotação aprovada não permite alteração comercial.")
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
    loja_nome = serializers.CharField(source="loja.nome_loja", read_only=True)

    class Meta:
        model = RequisicaoSetor
        fields = "__all__"
        read_only_fields = ("empresa", "data_cadastro")

    def validate_nome(self, value):
        if not (value or "").strip():
            raise serializers.ValidationError("Nome do setor é obrigatório.")
        return value.strip()

    def validate(self, attrs):
        loja = attrs.get("loja", getattr(self.instance, "loja", None))
        request = self.context.get("request")
        user = getattr(request, "user", None)
        empresa = attrs.get("empresa", getattr(self.instance, "empresa", None)) or getattr(user, "empresa", None)
        if loja and empresa and loja.empresa_id != empresa.id:
            raise serializers.ValidationError({"loja": "Loja física do setor pertence a outra empresa."})
        return attrs


class RequisicaoMatrizResponsabilidadeSerializer(serializers.ModelSerializer):
    empresa_nome = serializers.CharField(source="empresa.nome", read_only=True)
    tipo_requisicao_label = serializers.CharField(source="get_tipo_requisicao_display", read_only=True)
    setor_atendimento_nome = serializers.CharField(source="setor_atendimento.nome", read_only=True)
    setor_aquisicao_nome = serializers.CharField(source="setor_aquisicao.nome", read_only=True)

    class Meta:
        model = RequisicaoMatrizResponsabilidade
        fields = "__all__"
        read_only_fields = ("empresa", "criado_em", "atualizado_em")

    def validate(self, attrs):
        empresa = attrs.get("empresa", getattr(self.instance, "empresa", None))
        if not empresa:
            request = self.context.get("request")
            user = getattr(request, "user", None)
            empresa = getattr(user, "empresa", None)
        setor_atendimento = attrs.get("setor_atendimento", getattr(self.instance, "setor_atendimento", None))
        setor_aquisicao = attrs.get("setor_aquisicao", getattr(self.instance, "setor_aquisicao", None))
        tipo = attrs.get("tipo_requisicao", getattr(self.instance, "tipo_requisicao", None))
        ativo = attrs.get("ativo", getattr(self.instance, "ativo", True))
        if not tipo:
            raise serializers.ValidationError({"tipo_requisicao": "Informe o tipo da requisição."})
        if not setor_atendimento:
            raise serializers.ValidationError({"setor_atendimento": "Informe o setor responsável pelo atendimento."})
        if not setor_aquisicao:
            raise serializers.ValidationError({"setor_aquisicao": "Informe o setor responsável pela aquisição."})
        if empresa and setor_atendimento and setor_atendimento.empresa_id != empresa.id:
            raise serializers.ValidationError({"setor_atendimento": "Setor responsável pelo atendimento pertence a outra empresa."})
        if empresa and setor_aquisicao and setor_aquisicao.empresa_id != empresa.id:
            raise serializers.ValidationError({"setor_aquisicao": "Setor responsável pela aquisição pertence a outra empresa."})
        if ativo and empresa and tipo:
            qs = RequisicaoMatrizResponsabilidade.objects.filter(empresa=empresa, tipo_requisicao=tipo, ativo=True)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({"tipo_requisicao": "Já existe matriz ativa para este tipo nesta empresa."})
        return attrs


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
    indicador_compra = serializers.SerializerMethodField()

    class Meta:
        model = RequisicaoItem
        fields = "__all__"
        read_only_fields = ("qtd_atendida", "qtd_pendente", "status", "criado_em", "atualizado_em", "indicador_compra")

    def get_indicador_compra(self, obj):
        return indicador_requisicao_item(obj)

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
        if requisicao.status not in ("RASCUNHO", "DEVOLVIDA_CORRECAO"):
            raise serializers.ValidationError({"requisicao": "Somente requisições não enviadas ou devolvidas para correção permitem alterar itens."})
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
    setor_responsavel_nome = serializers.CharField(source="setor_responsavel.nome", read_only=True)
    ordem_servico_id = serializers.IntegerField(source="ordem_servico.id", read_only=True)
    ordem_servico_status = serializers.CharField(source="ordem_servico.status", read_only=True)

    class Meta:
        model = Requisicao
        fields = "__all__"
        read_only_fields = (
            "numero",
            "empresa",
            "requisitante",
            "status",
            "setor_responsavel",
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
        if self.instance and self.instance.status not in ("RASCUNHO", "DEVOLVIDA_CORRECAO"):
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


class OrdemServicoSerializer(serializers.ModelSerializer):
    requisicao_numero = serializers.IntegerField(source="requisicao.numero", read_only=True)
    loja_nome = serializers.CharField(source="loja.nome_loja", read_only=True)
    setor_solicitante_nome = serializers.CharField(source="setor_solicitante.nome", read_only=True)
    setor_responsavel_nome = serializers.CharField(source="setor_responsavel.nome", read_only=True)
    responsavel_nome = serializers.CharField(source="responsavel.username", read_only=True)
    tipo_label = serializers.CharField(source="get_tipo_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = OrdemServico
        fields = "__all__"
        read_only_fields = (
            "id",
            "requisicao",
            "empresa",
            "loja",
            "setor_solicitante",
            "setor_responsavel",
            "tipo",
            "origem",
            "descricao",
            "data_conclusao",
            "criado_em",
            "atualizado_em",
        )

    def validate(self, attrs):
        responsavel = attrs.get("responsavel", getattr(self.instance, "responsavel", None))
        if responsavel and responsavel.empresa_id and responsavel.empresa_id != self.instance.empresa_id:
            raise serializers.ValidationError({"responsavel": "Responsável pertence a outra empresa."})
        return attrs


class CotacaoRequisicaoSerializer(serializers.ModelSerializer):
    requisicao_numero = serializers.IntegerField(source="requisicao.numero", read_only=True)

    class Meta:
        model = CotacaoRequisicao
        fields = "__all__"
        read_only_fields = ("criado_em",)


class CotacaoItemSerializer(serializers.ModelSerializer):
    produto_descricao = serializers.CharField(source="produto.descricao", read_only=True)
    unidade_descricao = serializers.CharField(source="unidade.Descricao", read_only=True)
    requisicao_origem_numero = serializers.IntegerField(source="requisicao_item_origem.requisicao.numero", read_only=True)

    class Meta:
        model = CotacaoItem
        fields = "__all__"
        read_only_fields = ("criado_em", "atualizado_em")

    def validate(self, attrs):
        cotacao = attrs.get("cotacao", getattr(self.instance, "cotacao", None))
        produto = attrs.get("produto", getattr(self.instance, "produto", None))
        unidade = attrs.get("unidade", getattr(self.instance, "unidade", None))
        origem = attrs.get("origem", getattr(self.instance, "origem", "AVULSO"))
        req_item = attrs.get("requisicao_item_origem", getattr(self.instance, "requisicao_item_origem", None))
        quantidade = attrs.get("quantidade_cotar", getattr(self.instance, "quantidade_cotar", None))
        descricao = attrs.get("descricao", getattr(self.instance, "descricao", ""))
        if not cotacao:
            raise serializers.ValidationError({"cotacao": "Informe a cotação."})
        if cotacao.status != "EM_ELABORACAO":
            raise serializers.ValidationError({"cotacao": "Somente cotações em elaboração podem alterar itens."})
        if quantidade is None or Decimal(quantidade) <= 0:
            raise serializers.ValidationError({"quantidade_cotar": "Informe uma quantidade maior que zero."})
        if origem == "REQUISICAO" and not req_item:
            raise serializers.ValidationError({"requisicao_item_origem": "Informe o item de requisição de origem."})
        if produto:
            if produto.empresa_id != cotacao.empresa_id:
                raise serializers.ValidationError({"produto": "Produto pertence a outra empresa."})
            attrs["descricao"] = produto.descricao
            if not unidade and produto.unidade_id:
                attrs["unidade"] = produto.unidade
        elif not descricao:
            raise serializers.ValidationError({"descricao": "Informe a descrição do item avulso."})
        if not attrs.get("unidade", unidade):
            raise serializers.ValidationError({"unidade": "Informe a unidade."})
        unidade_final = attrs.get("unidade", unidade)
        if unidade_final and unidade_final.empresa_id != cotacao.empresa_id:
            raise serializers.ValidationError({"unidade": "Unidade pertence a outra empresa."})
        if req_item and req_item.requisicao.empresa_id != cotacao.empresa_id:
            raise serializers.ValidationError({"requisicao_item_origem": "Item de requisição pertence a outra empresa."})
        return attrs


class CotacaoFornecedorSerializer(serializers.ModelSerializer):
    fornecedor_nome = serializers.CharField(source="fornecedor.nome_fornecedor", read_only=True)

    class Meta:
        model = CotacaoFornecedor
        fields = "__all__"
        read_only_fields = ("criado_em", "atualizado_em")

    def validate(self, attrs):
        cotacao = attrs.get("cotacao", getattr(self.instance, "cotacao", None))
        fornecedor = attrs.get("fornecedor", getattr(self.instance, "fornecedor", None))
        status_participacao = attrs.get(
            "status_participacao",
            getattr(self.instance, "status_participacao", "CONVIDADO"),
        )
        motivo = attrs.get("motivo_desclassificacao", getattr(self.instance, "motivo_desclassificacao", ""))
        if not cotacao:
            raise serializers.ValidationError({"cotacao": "Informe a cotação."})
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            empresa_id = getattr(user, "empresa_id", None)
            if empresa_id and cotacao.empresa_id != empresa_id:
                raise serializers.ValidationError({"cotacao": "Cotação pertence a outra empresa."})
            if not EffectiveAccessService(user).can_access_store(cotacao.loja):
                raise serializers.ValidationError({"cotacao": "Cotação fora do escopo permitido."})
        if cotacao.status not in {"EM_ELABORACAO", "ABERTA", "PROPOSTAS_RECEBIDAS", "EM_ANALISE"}:
            raise serializers.ValidationError({"cotacao": "Cotação não permite alterar fornecedores neste status."})
        if not fornecedor:
            raise serializers.ValidationError({"fornecedor": "Informe o fornecedor."})
        if fornecedor.empresa_id != cotacao.empresa_id:
            raise serializers.ValidationError({"fornecedor": "Fornecedor pertence a outra empresa."})
        if not fornecedor.ativo:
            raise serializers.ValidationError({"fornecedor": "Fornecedor inativo não pode participar da cotação."})
        if status_participacao == "DESCLASSIFICADO" and not (motivo or "").strip():
            raise serializers.ValidationError({"motivo_desclassificacao": "Informe o motivo da desclassificação."})
        qs = CotacaoFornecedor.objects.filter(cotacao=cotacao, fornecedor=fornecedor)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError({"fornecedor": "Fornecedor já incluído na cotação."})
        return attrs


class CotacaoPropostaItemSerializer(serializers.ModelSerializer):
    cotacao_item_descricao = serializers.CharField(source="cotacao_item.descricao", read_only=True)
    quantidade_cotar = serializers.DecimalField(source="cotacao_item.quantidade_cotar", max_digits=14, decimal_places=3, read_only=True)

    class Meta:
        model = CotacaoPropostaItem
        fields = "__all__"
        read_only_fields = ("total_item",)
        extra_kwargs = {"proposta": {"required": False}}

    def validate(self, attrs):
        proposta = attrs.get("proposta", getattr(self.instance, "proposta", None))
        cotacao_item = attrs.get("cotacao_item", getattr(self.instance, "cotacao_item", None))
        quantidade = attrs.get("quantidade_ofertada", getattr(self.instance, "quantidade_ofertada", None))
        preco = attrs.get("preco_unitario", getattr(self.instance, "preco_unitario", None))
        desconto = attrs.get("desconto_item", getattr(self.instance, "desconto_item", 0))
        if proposta and proposta.cotacao.status not in {"EM_ELABORACAO", "ABERTA", "PROPOSTAS_RECEBIDAS", "EM_ANALISE"}:
            raise serializers.ValidationError({"proposta": "Cotação em status final não permite alterar propostas."})
        if not cotacao_item:
            raise serializers.ValidationError({"cotacao_item": "Informe o item da cotação."})
        cotacao = attrs.get("_cotacao") or getattr(proposta, "cotacao", None)
        if cotacao and cotacao_item.cotacao_id != cotacao.id:
            raise serializers.ValidationError({"cotacao_item": "Item não pertence à cotação da proposta."})
        if quantidade is None or Decimal(quantidade) <= 0:
            raise serializers.ValidationError({"quantidade_ofertada": "Informe uma quantidade maior que zero."})
        if preco is None or Decimal(preco) < 0:
            raise serializers.ValidationError({"preco_unitario": "Informe preço maior ou igual a zero."})
        if desconto is not None and Decimal(desconto) < 0:
            raise serializers.ValidationError({"desconto_item": "Informe desconto maior ou igual a zero."})
        return attrs


class CotacaoPropostaSerializer(serializers.ModelSerializer):
    itens = CotacaoPropostaItemSerializer(many=True, required=False)
    fornecedor_nome = serializers.CharField(source="cotacao_fornecedor.fornecedor.nome_fornecedor", read_only=True)
    prazo_pagamento_descricao = serializers.CharField(source="prazo_pagamento.descricao", read_only=True)
    forma_pagamento_descricao = serializers.SerializerMethodField()
    condicao_pagamento_legivel = serializers.SerializerMethodField()

    class Meta:
        model = CotacaoProposta
        fields = "__all__"
        read_only_fields = ("total_itens", "total_proposta", "criado_em", "atualizado_em")

    def get_condicao_pagamento_legivel(self, obj):
        if obj.prazo_pagamento_id:
            return obj.prazo_pagamento.descricao or obj.prazo_pagamento.codigo
        return obj.condicao_pagamento or ""

    def get_forma_pagamento_descricao(self, obj):
        forma = self._forma_pagamento_obj(obj)
        return getattr(forma, "descricao", "") or ""

    def _forma_pagamento_obj(self, obj):
        codigo = (getattr(obj, "forma_pagamento", None) or "").strip()
        cotacao = getattr(obj, "cotacao", None)
        if not codigo or not FormaPagamento:
            return None
        qs = FormaPagamento.objects.filter(codigo=codigo, ativo=True)
        if cotacao:
            qs = qs.filter(Q(empresa=cotacao.empresa) | Q(empresa__isnull=True))
        return qs.first()

    def validate(self, attrs):
        cotacao = attrs.get("cotacao", getattr(self.instance, "cotacao", None))
        participante = attrs.get("cotacao_fornecedor", getattr(self.instance, "cotacao_fornecedor", None))
        if not cotacao:
            raise serializers.ValidationError({"cotacao": "Informe a cotação."})
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            empresa_id = getattr(user, "empresa_id", None)
            if empresa_id and cotacao.empresa_id != empresa_id:
                raise serializers.ValidationError({"cotacao": "Cotação pertence a outra empresa."})
            if not EffectiveAccessService(user).can_access_store(cotacao.loja):
                raise serializers.ValidationError({"cotacao": "Cotação fora do escopo permitido."})
        if cotacao.status not in {"EM_ELABORACAO", "ABERTA", "PROPOSTAS_RECEBIDAS", "EM_ANALISE"}:
            raise serializers.ValidationError({"cotacao": "Cotação em status final não permite alterar propostas."})
        if not participante:
            raise serializers.ValidationError({"cotacao_fornecedor": "Informe o fornecedor participante."})
        if participante.cotacao_id != cotacao.id:
            raise serializers.ValidationError({"cotacao_fornecedor": "Fornecedor não participa desta cotação."})
        if CotacaoProposta.objects.filter(cotacao_fornecedor=participante, ativa=True).exclude(pk=getattr(self.instance, "pk", None)).exists():
            raise serializers.ValidationError({"cotacao_fornecedor": "Fornecedor já possui proposta ativa nesta cotação."})
        forma_pagamento = (attrs.get("forma_pagamento", getattr(self.instance, "forma_pagamento", None)) or "").strip()
        if forma_pagamento and FormaPagamento:
            forma_qs = FormaPagamento.objects.filter(codigo=forma_pagamento, ativo=True)
            if cotacao:
                forma_qs = forma_qs.filter(Q(empresa=cotacao.empresa) | Q(empresa__isnull=True))
            if not forma_qs.exists():
                raise serializers.ValidationError({"forma_pagamento": "Forma de pagamento não encontrada para a empresa."})
        prazo_pagamento = attrs.get("prazo_pagamento", getattr(self.instance, "prazo_pagamento", None))
        if prazo_pagamento and cotacao and prazo_pagamento.empresa_id and prazo_pagamento.empresa_id != cotacao.empresa_id:
            raise serializers.ValidationError({"prazo_pagamento": "Condição de pagamento pertence a outra empresa."})
        prazo_dias = attrs.get("prazo_entrega_dias", getattr(self.instance, "prazo_entrega_dias", None))
        if prazo_dias is not None and int(prazo_dias) < 0:
            raise serializers.ValidationError({"prazo_entrega_dias": "Informe prazo de entrega maior ou igual a zero."})
        for campo in ("frete", "outras_despesas", "desconto_geral"):
            valor = attrs.get(campo, getattr(self.instance, campo, 0))
            if valor is not None and Decimal(valor) < 0:
                raise serializers.ValidationError({campo: "Informe valor maior ou igual a zero."})
        itens = attrs.get("itens", [])
        item_ids = [item.get("cotacao_item").id for item in itens if item.get("cotacao_item")]
        if len(item_ids) != len(set(item_ids)):
            raise serializers.ValidationError({"itens": "Item duplicado na proposta."})
        for item in itens:
            item["_cotacao"] = cotacao
            CotacaoPropostaItemSerializer(context=self.context).validate(item)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        itens_data = validated_data.pop("itens", [])
        self._normalizar_campos_estruturados(validated_data)
        proposta = CotacaoProposta.objects.create(**validated_data)
        self._salvar_itens(proposta, itens_data)
        proposta.recomputar_totais()
        proposta.save(update_fields=["total_itens", "total_proposta", "atualizado_em"])
        participante = proposta.cotacao_fornecedor
        if participante.status_participacao != "PROPOSTA_RECEBIDA":
            participante.status_participacao = "PROPOSTA_RECEBIDA"
            participante.save(update_fields=["status_participacao", "atualizado_em"])
        return proposta

    @transaction.atomic
    def update(self, instance, validated_data):
        itens_data = validated_data.pop("itens", None)
        self._normalizar_campos_estruturados(validated_data)
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.save()
        if itens_data is not None:
            instance.itens.all().delete()
            self._salvar_itens(instance, itens_data)
        instance.recomputar_totais()
        instance.save(update_fields=["total_itens", "total_proposta", "atualizado_em"])
        return instance

    def _salvar_itens(self, proposta, itens_data):
        for item_data in itens_data:
            item_data.pop("_cotacao", None)
            CotacaoPropostaItem.objects.create(proposta=proposta, **item_data)

    def _normalizar_campos_estruturados(self, data):
        prazo = data.get("prazo_pagamento")
        if prazo:
            data["condicao_pagamento"] = prazo.descricao or prazo.codigo
        dias = data.get("prazo_entrega_dias")
        if dias is not None:
            data["prazo_entrega"] = str(dias)


class CotacaoSerializer(serializers.ModelSerializer):
    itens = CotacaoItemSerializer(many=True, read_only=True)
    requisicoes_vinculadas = CotacaoRequisicaoSerializer(many=True, read_only=True)
    loja_nome = serializers.CharField(source="loja.nome_loja", read_only=True)
    responsavel_nome = serializers.CharField(source="responsavel.username", read_only=True)
    pedido_compra_gerado_id = serializers.SerializerMethodField()
    status_operacional = serializers.SerializerMethodField()
    forma_pagamento_vencedora = serializers.SerializerMethodField()
    forma_pagamento_vencedora_legivel = serializers.SerializerMethodField()
    prazo_pagamento_vencedor = serializers.SerializerMethodField()
    prazo_pagamento_vencedor_legivel = serializers.SerializerMethodField()
    prazo_entrega_vencedor_dias = serializers.SerializerMethodField()

    class Meta:
        model = Cotacao
        fields = "__all__"
        read_only_fields = (
            "numero",
            "empresa",
            "responsavel",
            "status",
            "proposta_vencedora",
            "justificativa_vencedor",
            "aprovado_por",
            "aprovado_em",
            "rejeitado_por",
            "rejeitado_em",
            "motivo_rejeicao",
            "cancelado_por",
            "cancelado_em",
            "motivo_cancelamento",
            "snapshot_proposta_aprovada",
            "criado_em",
            "atualizado_em",
        )

    def get_pedido_compra_gerado_id(self, obj):
        pedido = getattr(obj, "pedido_compra_gerado", None)
        return getattr(pedido, "id", None)

    def _snapshot_ou_proposta_vencedora(self, obj):
        snapshot = obj.snapshot_proposta_aprovada or {}
        proposta = getattr(obj, "proposta_vencedora", None)
        return snapshot, proposta

    def get_forma_pagamento_vencedora(self, obj):
        snapshot, proposta = self._snapshot_ou_proposta_vencedora(obj)
        return snapshot.get("forma_pagamento") or getattr(proposta, "forma_pagamento", None) or ""

    def get_forma_pagamento_vencedora_legivel(self, obj):
        snapshot, proposta = self._snapshot_ou_proposta_vencedora(obj)
        if snapshot.get("forma_pagamento_legivel"):
            return snapshot.get("forma_pagamento_legivel")
        forma = CotacaoPropostaSerializer()._forma_pagamento_obj(proposta) if proposta else None
        return getattr(forma, "descricao", "") or ""

    def get_prazo_pagamento_vencedor(self, obj):
        snapshot, proposta = self._snapshot_ou_proposta_vencedora(obj)
        return snapshot.get("prazo_pagamento") or getattr(proposta, "prazo_pagamento_id", None)

    def get_prazo_pagamento_vencedor_legivel(self, obj):
        snapshot, proposta = self._snapshot_ou_proposta_vencedora(obj)
        return (
            snapshot.get("prazo_pagamento_legivel")
            or snapshot.get("condicao_pagamento_legivel")
            or getattr(getattr(proposta, "prazo_pagamento", None), "descricao", "")
            or getattr(proposta, "condicao_pagamento", "")
            or ""
        )

    def get_prazo_entrega_vencedor_dias(self, obj):
        snapshot, proposta = self._snapshot_ou_proposta_vencedora(obj)
        return snapshot.get("prazo_entrega_dias") if snapshot else getattr(proposta, "prazo_entrega_dias", None)

    def get_status_operacional(self, obj):
        labels_finais = {
            "AGUARDANDO_APROVACAO": "Aguardando aprovação",
            "APROVADA": "Aprovada",
            "REJEITADA": "Rejeitada",
            "CANCELADA": "Cancelada",
            "PEDIDO_GERADO": "Pedido de compra gerado",
            "ENCERRADA": "Encerrada",
        }
        if obj.status in labels_finais:
            return labels_finais[obj.status]
        itens_count = obj.itens.count()
        if not itens_count:
            return "Em elaboração — sem itens"
        fornecedores = list(obj.fornecedores_participantes.all())
        if not fornecedores:
            return "Aguardando fornecedores"
        ativos = [f for f in fornecedores if f.status_participacao not in {"RECUSOU", "DESCLASSIFICADO"}]
        total = len(ativos)
        recebidas = sum(1 for f in ativos if f.status_participacao == "PROPOSTA_RECEBIDA")
        if total and recebidas < total:
            return f"Aguardando propostas — {recebidas} de {total} recebidas"
        if recebidas:
            return "Pronta para análise"
        return "Aguardando propostas"
