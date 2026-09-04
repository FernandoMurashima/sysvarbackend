from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from rest_framework import serializers

from compras.models import PedidoCompra
from fiscal.models import AgenteLocalSysvar, ConfiguracaoXmlFornecedor, FormaPagamentoFiscalMap, NotaFiscalEntrada, NotaFiscalEntradaDivergenciaXml, NotaFiscalEntradaEvento, NotaFiscalEntradaItem, NotaFiscalEntradaItemXml, RecebimentoMercadoriaConferenciaItem, RecebimentoMercadoriaEstoque, RecebimentoMercadoriaPedido, XmlFornecedorRecebido
from fiscal.services.nfe_conferencia import quantidade_interna_recebida
from fiscal.services.nfe_conciliacao import conversao_info
from fiscal.validators import normalizar_chave_acesso_nfe


def _money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class AgenteLocalSysvarSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgenteLocalSysvar
        fields = ("id", "empresa", "identificador", "nome", "token_prefixo", "ativo", "ultimo_contato", "versao", "hostname", "criado_em", "atualizado_em")
        read_only_fields = ("token_prefixo", "ultimo_contato", "criado_em", "atualizado_em")

    def validate(self, attrs):
        empresa = attrs.get("empresa") or getattr(self.instance, "empresa", None)
        identificador = attrs.get("identificador", getattr(self.instance, "identificador", ""))
        if not empresa:
            raise serializers.ValidationError({"empresa": "Empresa é obrigatória."})
        identificador = str(identificador or "").strip()
        if not identificador:
            raise serializers.ValidationError({"identificador": "Identificador é obrigatório."})
        attrs["identificador"] = identificador
        if "nome" in attrs:
            attrs["nome"] = str(attrs["nome"] or "").strip()
        duplicado = AgenteLocalSysvar.objects.filter(empresa=empresa, identificador=identificador)
        if self.instance:
            duplicado = duplicado.exclude(pk=self.instance.pk)
        if duplicado.exists():
            raise serializers.ValidationError({"identificador": "Já existe agente com este identificador nesta empresa."})
        return attrs


class NotaFiscalEntradaItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotaFiscalEntradaItem
        fields = "__all__"
        read_only_fields = ("criado_em", "atualizado_em", "total_item")

    def validate(self, attrs):
        nota = attrs.get("nota") or getattr(self.instance, "nota", None)
        pedido_item = attrs.get("pedido_item") or getattr(self.instance, "pedido_item", None)
        qtd_recebida = attrs.get("qtd_recebida", getattr(self.instance, "qtd_recebida", 0))
        preco_unit_nf = attrs.get("preco_unit_nf", getattr(self.instance, "preco_unit_nf", 0))
        desconto_item = attrs.get("desconto_item", getattr(self.instance, "desconto_item", 0))

        if nota and nota.status != NotaFiscalEntrada.Status.ABERTA:
            raise serializers.ValidationError({"nota": "Somente notas abertas podem receber alterações."})

        if nota and pedido_item and pedido_item.pedido_id != nota.pedido_compra_id:
            raise serializers.ValidationError(
                {"pedido_item": "O item informado não pertence ao pedido de compra da nota."}
            )
        if nota and not nota.pedido_compra_id:
            raise serializers.ValidationError({"nota": "Itens de NF sem pedido serão implementados em etapa posterior."})

        if Decimal(qtd_recebida or 0) < 0:
            raise serializers.ValidationError({"qtd_recebida": "Informe uma quantidade maior ou igual a zero."})
        if Decimal(preco_unit_nf or 0) < 0:
            raise serializers.ValidationError({"preco_unit_nf": "Informe um preço maior ou igual a zero."})
        if Decimal(desconto_item or 0) < 0:
            raise serializers.ValidationError({"desconto_item": "Informe um desconto maior ou igual a zero."})
        valor_bruto = Decimal(qtd_recebida or 0) * Decimal(preco_unit_nf or 0)
        if Decimal(desconto_item or 0) > valor_bruto:
            raise serializers.ValidationError({"desconto_item": "Desconto do item não pode ser maior que o valor bruto."})

        if pedido_item and nota:
            recebidos = NotaFiscalEntradaItem.objects.filter(
                pedido_item=pedido_item,
                nota__pedido_compra_id=nota.pedido_compra_id,
            ).exclude(nota__status=NotaFiscalEntrada.Status.CANCELADA)
            if self.instance:
                recebidos = recebidos.exclude(pk=self.instance.pk)
            qtd_ja_recebida = sum(Decimal(item.qtd_recebida or 0) for item in recebidos)
            qtd_pedido = Decimal(pedido_item.qtd or 0)
            if qtd_ja_recebida + Decimal(qtd_recebida or 0) > qtd_pedido:
                raise serializers.ValidationError(
                    {"qtd_recebida": "Quantidade recebida ultrapassa a quantidade do item do pedido."}
                )

        return attrs

    def _apply_totals(self, instance):
        bruto = Decimal(instance.qtd_recebida or 0) * Decimal(instance.preco_unit_nf or 0)
        instance.total_item = _money(bruto - Decimal(instance.desconto_item or 0))
        if instance.total_item < 0:
            raise serializers.ValidationError({"total_item": "Total do item não pode ser negativo."})

    @transaction.atomic
    def create(self, validated_data):
        obj = NotaFiscalEntradaItem(**validated_data)
        self._apply_totals(obj)
        obj.save()
        obj.nota.recalcular_totais()
        return obj

    @transaction.atomic
    def update(self, instance, validated_data):
        for key, value in validated_data.items():
            setattr(instance, key, value)
        self._apply_totals(instance)
        instance.save()
        instance.nota.recalcular_totais()
        return instance


class XmlFornecedorRecebidoSerializer(serializers.ModelSerializer):
    loja_nome = serializers.CharField(source="loja.nome_loja", read_only=True)
    fornecedor_nome = serializers.CharField(source="fornecedor.nome_fornecedor", read_only=True)

    class Meta:
        model = XmlFornecedorRecebido
        fields = "__all__"
        read_only_fields = ("detectado_em", "atualizado_em")

    def validate(self, attrs):
        empresa = attrs.get("empresa") or getattr(self.instance, "empresa", None)
        loja = attrs.get("loja", getattr(self.instance, "loja", None))
        fornecedor = attrs.get("fornecedor", getattr(self.instance, "fornecedor", None))
        chave = attrs.get("chave_acesso")

        if not empresa:
            raise serializers.ValidationError({"empresa": "Empresa é obrigatória."})
        if loja and loja.empresa_id != empresa.id:
            raise serializers.ValidationError({"loja": "Loja pertence a outra empresa."})
        if fornecedor and fornecedor.empresa_id != empresa.id:
            raise serializers.ValidationError({"fornecedor": "Fornecedor pertence a outra empresa."})
        if chave is not None:
            try:
                attrs["chave_acesso"] = normalizar_chave_acesso_nfe(chave)
            except serializers.ValidationError as exc:
                raise serializers.ValidationError({"chave_acesso": exc.detail})

        for field in ("modelo", "serie", "numero", "emitente_documento", "destinatario_documento"):
            if field in attrs and attrs[field] is not None:
                attrs[field] = str(attrs[field]).strip()

        valor_total = attrs.get("valor_total", getattr(self.instance, "valor_total", 0))
        if Decimal(valor_total or 0) < 0:
            raise serializers.ValidationError({"valor_total": "Informe valor total maior ou igual a zero."})
        return attrs


class RecebimentoPedidoResumoSerializer(serializers.ModelSerializer):
    fornecedor_nome = serializers.CharField(source="fornecedor.nome_fornecedor", read_only=True)
    loja_nome = serializers.CharField(source="loja.nome_loja", read_only=True)
    quantidade = serializers.SerializerMethodField()
    valor = serializers.DecimalField(source="total_pedido", max_digits=18, decimal_places=2, read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = PedidoCompra
        fields = ("id", "emissao", "fornecedor", "fornecedor_nome", "loja", "loja_nome", "quantidade", "valor", "status", "status_label")

    def get_quantidade(self, obj):
        return str(sum((item.qtd or 0) for item in obj.itens.all()))


class RecebimentoMercadoriaConferenciaItemSerializer(serializers.ModelSerializer):
    produto_referencia = serializers.CharField(source="produto.referencia", read_only=True)
    produto_descricao = serializers.CharField(source="produto.descricao", read_only=True)
    cor_nome = serializers.CharField(source="cor.Descricao", read_only=True)
    tamanho_nome = serializers.CharField(source="tamanho.Tamanho", read_only=True)
    ean = serializers.CharField(source="produto_detalhe.ean13", read_only=True)
    diferenca = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    situacao = serializers.SerializerMethodField()

    class Meta:
        model = RecebimentoMercadoriaConferenciaItem
        fields = (
            "id", "recebimento", "pedido", "pedido_item", "produto", "produto_referencia", "produto_descricao",
            "cor", "cor_nome", "tamanho", "tamanho_nome", "produto_detalhe", "ean", "quantidade_esperada",
            "quantidade_recebida", "diferenca", "situacao", "criado_em", "atualizado_em",
        )
        read_only_fields = (
            "recebimento", "pedido", "pedido_item", "produto", "cor", "tamanho", "produto_detalhe",
            "quantidade_esperada", "criado_em", "atualizado_em",
        )

    def get_situacao(self, obj):
        if obj.diferenca == 0:
            return "OK"
        return "SOBRA" if obj.diferenca > 0 else "FALTA"


class RecebimentoMercadoriaEstoqueSerializer(serializers.ModelSerializer):
    loja_nome = serializers.CharField(source="loja.nome_loja", read_only=True)
    fornecedor_nome = serializers.CharField(source="fornecedor.nome_fornecedor", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    xml_fornecedor_dados = XmlFornecedorRecebidoSerializer(source="xml_fornecedor", read_only=True)
    pedidos = serializers.SerializerMethodField()
    conferencia_itens = RecebimentoMercadoriaConferenciaItemSerializer(many=True, read_only=True)
    conferencia_resumo = serializers.SerializerMethodField()

    class Meta:
        model = RecebimentoMercadoriaEstoque
        fields = (
            "id", "empresa", "loja", "loja_nome", "xml_fornecedor", "xml_fornecedor_dados", "fornecedor",
            "fornecedor_nome", "status", "status_label", "criado_por", "criado_em", "atualizado_em", "pedidos",
            "conferencia_itens", "conferencia_resumo",
        )
        read_only_fields = ("empresa", "criado_por", "criado_em", "atualizado_em", "pedidos")

    def get_pedidos(self, obj):
        pedidos = [vinculo.pedido for vinculo in obj.pedidos_vinculados.all()]
        return RecebimentoPedidoResumoSerializer(pedidos, many=True).data

    def get_conferencia_resumo(self, obj):
        itens = list(obj.conferencia_itens.all())
        esperado = sum((item.quantidade_esperada or 0) for item in itens)
        recebido = sum((item.quantidade_recebida or 0) for item in itens)
        divergentes = sum(1 for item in itens if item.diferenca != 0)
        return {
            "quantidade_esperada_total": str(esperado),
            "quantidade_recebida_total": str(recebido),
            "diferenca_total": str(recebido - esperado),
            "quantidade_skus": len(itens),
            "quantidade_skus_com_divergencia": divergentes,
        }

    def validate(self, attrs):
        xml = attrs.get("xml_fornecedor", getattr(self.instance, "xml_fornecedor", None))
        loja = attrs.get("loja", getattr(self.instance, "loja", None))
        fornecedor = attrs.get("fornecedor", getattr(self.instance, "fornecedor", None))
        empresa = getattr(self.instance, "empresa", None) or getattr(getattr(self.context.get("request"), "user", None), "empresa", None)
        if xml:
            empresa = xml.empresa
            if loja and loja.empresa_id != xml.empresa_id:
                raise serializers.ValidationError({"loja": "Loja pertence a outra empresa."})
            if fornecedor and fornecedor.empresa_id != xml.empresa_id:
                raise serializers.ValidationError({"fornecedor": "Fornecedor pertence a outra empresa."})
        elif empresa:
            if loja and loja.empresa_id != empresa.id:
                raise serializers.ValidationError({"loja": "Loja pertence a outra empresa."})
            if fornecedor and fornecedor.empresa_id != empresa.id:
                raise serializers.ValidationError({"fornecedor": "Fornecedor pertence a outra empresa."})
        return attrs


class AgenteLocalConfiguracaoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConfiguracaoXmlFornecedor
        fields = ("id", "loja", "caminho_local")


class AgenteLocalXmlDetectadoSerializer(XmlFornecedorRecebidoSerializer):
    configuracao_id = serializers.IntegerField(write_only=True)

    class Meta(XmlFornecedorRecebidoSerializer.Meta):
        fields = (
            "id", "configuracao_id", "loja", "fornecedor", "chave_acesso", "modelo", "serie", "numero", "dh_emissao",
            "emitente_documento", "emitente_nome", "destinatario_documento", "destinatario_nome", "valor_total",
            "situacao_fiscal", "status_operacional", "caminho_origem_local", "identificador_agente", "detectado_em",
            "atualizado_em",
        )
        read_only_fields = ("id", "loja", "fornecedor", "status_operacional", "identificador_agente", "detectado_em", "atualizado_em")
        extra_kwargs = {"chave_acesso": {"validators": []}}

    def validate(self, attrs):
        chave = attrs.get("chave_acesso")
        if chave is not None:
            try:
                attrs["chave_acesso"] = normalizar_chave_acesso_nfe(chave)
            except serializers.ValidationError as exc:
                raise serializers.ValidationError({"chave_acesso": exc.detail})
        for field in ("modelo", "serie", "numero", "emitente_documento", "destinatario_documento"):
            if field in attrs and attrs[field] is not None:
                attrs[field] = str(attrs[field]).strip()
        valor_total = attrs.get("valor_total", 0)
        if Decimal(valor_total or 0) < 0:
            raise serializers.ValidationError({"valor_total": "Informe valor total maior ou igual a zero."})
        return attrs


class ConfiguracaoXmlFornecedorSerializer(serializers.ModelSerializer):
    loja_nome = serializers.CharField(source="loja.nome_loja", read_only=True)

    class Meta:
        model = ConfiguracaoXmlFornecedor
        fields = "__all__"
        read_only_fields = ("loja_escopo_unicidade", "criado_em", "atualizado_em")

    def validate(self, attrs):
        empresa = attrs.get("empresa") or getattr(self.instance, "empresa", None)
        loja = attrs.get("loja", getattr(self.instance, "loja", None))
        caminho = attrs.get("caminho_local", getattr(self.instance, "caminho_local", None))
        identificador_agente = attrs.get("identificador_agente", getattr(self.instance, "identificador_agente", ""))

        if not empresa:
            raise serializers.ValidationError({"empresa": "Empresa é obrigatória."})
        if loja and loja.empresa_id != empresa.id:
            raise serializers.ValidationError({"loja": "Loja pertence a outra empresa."})
        if identificador_agente is not None:
            identificador_agente = str(identificador_agente).strip()
            attrs["identificador_agente"] = identificador_agente
            if identificador_agente and not AgenteLocalSysvar.objects.filter(empresa=empresa, identificador=identificador_agente).exists():
                raise serializers.ValidationError({"identificador_agente": "Agente local inválido para esta empresa."})

        if caminho is not None:
            caminho = str(caminho).strip()
            if not caminho:
                raise serializers.ValidationError({"caminho_local": "Caminho local é obrigatório."})
            attrs["caminho_local"] = caminho

        duplicado = ConfiguracaoXmlFornecedor.objects.filter(
            empresa=empresa,
            loja=loja,
            caminho_local=attrs.get("caminho_local", caminho),
        )
        if self.instance:
            duplicado = duplicado.exclude(pk=self.instance.pk)
        if duplicado.exists():
            raise serializers.ValidationError({"caminho_local": "Já existe configuração para este caminho neste escopo."})
        return attrs


class NotaFiscalEntradaSerializer(serializers.ModelSerializer):
    itens = NotaFiscalEntradaItemSerializer(many=True, read_only=True)
    itens_xml = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    destino_recebimento = serializers.CharField(source="loja.nome_loja", read_only=True)
    loja_estoque_id = serializers.IntegerField(source="loja_id", read_only=True)
    resumo_conciliacao = serializers.SerializerMethodField()
    resumo_conferencia = serializers.SerializerMethodField()

    class Meta:
        model = NotaFiscalEntrada
        fields = "__all__"
        extra_kwargs = {
            "empresa": {"required": False},
            "loja": {"required": False},
            "fornecedor": {"required": False},
            "pedido_compra": {"required": False, "allow_null": True},
        }
        read_only_fields = (
            "status",
            "situacao_fiscal",
            "valor_produtos",
            "valor_desconto",
            "valor_total",
            "criado_por",
            "cancelado_por",
            "cancelado_em",
            "motivo_cancelamento",
            "criado_em",
            "atualizado_em",
        )

    def get_resumo_conciliacao(self, obj):
        return obj.resumo_conciliacao_xml() if obj.xml_importado else None

    def get_resumo_conferencia(self, obj):
        return obj.resumo_conferencia_xml() if obj.xml_importado else None

    def validate(self, attrs):
        pedido = attrs.get("pedido_compra") or getattr(self.instance, "pedido_compra", None)
        empresa = attrs.get("empresa") or getattr(self.instance, "empresa", None)
        loja = attrs.get("loja") or getattr(self.instance, "loja", None)
        fornecedor = attrs.get("fornecedor") or getattr(self.instance, "fornecedor", None)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not empresa and user and not getattr(user, "is_superuser", False):
            empresa = getattr(user, "empresa", None)
            if empresa:
                attrs["empresa"] = empresa
        for field in ("modelo", "serie", "numero"):
            if field in attrs and attrs[field] is not None:
                attrs[field] = str(attrs[field]).strip()

        if pedido:
            attrs["empresa"] = pedido.empresa
            attrs["loja"] = pedido.loja
            attrs["fornecedor"] = pedido.fornecedor
            empresa = pedido.empresa
            loja = pedido.loja
            fornecedor = pedido.fornecedor
        else:
            if not empresa:
                raise serializers.ValidationError({"empresa": "Empresa é obrigatória para nota sem pedido."})
            if not loja:
                raise serializers.ValidationError({"loja": "Loja é obrigatória para nota sem pedido."})
            if not fornecedor:
                raise serializers.ValidationError({"fornecedor": "Fornecedor é obrigatório para nota sem pedido."})

        if loja and empresa and loja.empresa_id != empresa.id:
            raise serializers.ValidationError({"loja": "Loja pertence a outra empresa."})
        if fornecedor and empresa and fornecedor.empresa_id != empresa.id:
            raise serializers.ValidationError({"fornecedor": "Fornecedor pertence a outra empresa."})

        if pedido and (pedido.status or "").upper() == "CA":
            raise serializers.ValidationError({"pedido_compra": "Não é possível criar nota para pedido cancelado."})
        if pedido and (pedido.status or "").upper() == "AT":
            raise serializers.ValidationError({"pedido_compra": "Este pedido já foi totalmente atendido."})

        chave = attrs.get("chave_acesso")
        if chave is not None:
            try:
                attrs["chave_acesso"] = normalizar_chave_acesso_nfe(chave) or None
            except serializers.ValidationError as exc:
                raise serializers.ValidationError({"chave_acesso": exc.detail})

        if self.instance and self.instance.status != NotaFiscalEntrada.Status.ABERTA:
            protected = {
                "empresa",
                "loja",
                "fornecedor",
                "pedido_compra",
                "modelo",
                "serie",
                "numero",
                "chave_acesso",
                "dt_emissao",
                "dt_entrada",
            }
            if protected.intersection(attrs.keys()):
                raise serializers.ValidationError("Somente notas abertas podem ser alteradas.")

        valor_frete = attrs.get("valor_frete", getattr(self.instance, "valor_frete", 0))
        if Decimal(valor_frete or 0) < 0:
            raise serializers.ValidationError({"valor_frete": "Informe um frete maior ou igual a zero."})
        dt_emissao = attrs.get("dt_emissao", getattr(self.instance, "dt_emissao", None))
        dt_entrada = attrs.get("dt_entrada", getattr(self.instance, "dt_entrada", None))
        if dt_emissao and dt_entrada and dt_entrada < dt_emissao:
            raise serializers.ValidationError({"dt_entrada": "Data de entrada não pode ser anterior à data de emissão."})

        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        if request and getattr(request, "user", None) and request.user.is_authenticated:
            validated_data["criado_por"] = request.user
        return super().create(validated_data)

    def update(self, instance, validated_data):
        obj = super().update(instance, validated_data)
        try:
            obj.recalcular_totais()
        except ValueError as exc:
            raise serializers.ValidationError({"valor_total": str(exc)}) from exc
        return obj


class NotaFiscalEntradaItemXmlSerializer(serializers.ModelSerializer):
    conciliado = serializers.BooleanField(read_only=True)
    produto_descricao = serializers.CharField(source="produto.descricao", read_only=True)
    produto_referencia = serializers.CharField(source="produto.referencia", read_only=True)
    produto_fornecedor_codigo = serializers.CharField(source="produto_fornecedor.codigo_produto_fornecedor", read_only=True)
    conversao = serializers.SerializerMethodField()
    conferido = serializers.BooleanField(read_only=True)
    quantidade_faltante = serializers.SerializerMethodField()
    valor_divergente = serializers.SerializerMethodField()
    quantidade_interna_recebida = serializers.SerializerMethodField()
    divergencias_pedido = serializers.SerializerMethodField()

    class Meta:
        model = NotaFiscalEntradaItemXml
        fields = "__all__"
        read_only_fields = (
            "criado_em",
            "produto",
            "produto_fornecedor",
            "origem_conciliacao",
            "conciliado_em",
            "conciliado_por",
            "conferido_em",
            "conferido_por",
            "unidade_fornecedor_efetivada",
            "fator_conversao_efetivado",
            "quantidade_interna_efetivada",
            "efetivado_em",
            "impostos_fiscais",
        )

    def get_conversao(self, obj):
        return conversao_info(obj)

    def get_quantidade_faltante(self, obj):
        return str(obj.quantidade_faltante) if obj.quantidade_faltante is not None else None

    def get_valor_divergente(self, obj):
        return str(obj.valor_divergente) if obj.valor_divergente is not None else None

    def get_quantidade_interna_recebida(self, obj):
        value = quantidade_interna_recebida(obj)
        return str(value) if value is not None else None

    def get_divergencias_pedido(self, obj):
        return obj.divergencias_pedido()


class NotaFiscalEntradaDivergenciaXmlSerializer(serializers.ModelSerializer):
    produto_descricao = serializers.CharField(source="produto.descricao", read_only=True)
    produto_referencia = serializers.CharField(source="produto.referencia", read_only=True)
    numero_item = serializers.IntegerField(source="item_xml.numero_item", read_only=True)

    class Meta:
        model = NotaFiscalEntradaDivergenciaXml
        fields = "__all__"
        read_only_fields = (
            "empresa",
            "nota",
            "item_xml",
            "fornecedor",
            "produto",
            "quantidade_fiscal",
            "quantidade_recebida",
            "quantidade_faltante",
            "valor_divergente",
            "status",
            "conferido_por",
            "resolvido_por",
            "criado_em",
            "atualizado_em",
            "resolvido_em",
        )


class NotaFiscalEntradaEventoSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotaFiscalEntradaEvento
        fields = "__all__"
        read_only_fields = (
            "empresa",
            "nota",
            "chave_acesso",
            "id_evento",
            "tipo_evento",
            "tipo_evento_descricao",
            "sequencia",
            "data_hora_evento",
            "protocolo",
            "cstat",
            "xmotivo",
            "ambiente",
            "origem",
            "situacao_processamento",
            "xml_original",
            "criado_em",
        )


class FormaPagamentoFiscalMapSerializer(serializers.ModelSerializer):
    forma_pagamento_codigo = serializers.CharField(source="forma_pagamento.codigo", read_only=True)
    forma_pagamento_descricao = serializers.CharField(source="forma_pagamento.descricao", read_only=True)
    forma_pagamento_tipo = serializers.CharField(source="forma_pagamento.tipo", read_only=True)

    class Meta:
        model = FormaPagamentoFiscalMap
        fields = "__all__"
        read_only_fields = ("empresa", "descricao_fiscal", "criado_por", "criado_em", "atualizado_em")
