from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import models
from django.db.models import UniqueConstraint, Index


def _money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class NotaFiscalEntrada(models.Model):
    class Status(models.TextChoices):
        ABERTA = "AB", "Aberta"
        FECHADA = "FE", "Fechada"
        CANCELADA = "CA", "Cancelada"

    empresa = models.ForeignKey(
        "cadastros.Empresa",
        on_delete=models.PROTECT,
        related_name="notas_fiscais_entrada",
        db_index=True,
    )
    loja = models.ForeignKey(
        "cadastros.Loja",
        on_delete=models.PROTECT,
        related_name="notas_fiscais_entrada",
        db_index=True,
    )
    fornecedor = models.ForeignKey(
        "cadastros.Fornecedor",
        on_delete=models.PROTECT,
        related_name="notas_fiscais_entrada",
        db_index=True,
    )
    pedido_compra = models.ForeignKey(
        "compras.PedidoCompra",
        on_delete=models.PROTECT,
        related_name="notas_entrada",
        null=True,
        blank=True,
        db_index=True,
    )

    # dados básicos da NF
    modelo = models.CharField(max_length=2, default="55")  # 55 = NFe (MVP)
    serie = models.CharField(max_length=10, blank=True, default="")
    numero = models.CharField(max_length=20)
    chave_acesso = models.CharField(max_length=44, blank=True, null=True, default=None, unique=True, db_index=True)

    dt_emissao = models.DateField()
    dt_entrada = models.DateField()

    status = models.CharField(
        max_length=2,
        choices=Status.choices,
        default=Status.ABERTA,
        db_index=True,
    )

    # totais (MVP)
    valor_produtos = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    valor_desconto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    valor_frete = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    valor_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    observacoes = models.CharField(max_length=255, blank=True, default="")
    xml_original = models.TextField(blank=True, default="")
    xml_importado = models.BooleanField(default=False, db_index=True)
    natureza_operacao = models.CharField(max_length=120, blank=True, default="")
    emitente_documento = models.CharField(max_length=14, blank=True, default="")
    emitente_nome = models.CharField(max_length=120, blank=True, default="")
    emitente_ie = models.CharField(max_length=20, blank=True, default="")
    destinatario_documento = models.CharField(max_length=14, blank=True, default="")
    destinatario_nome = models.CharField(max_length=120, blank=True, default="")
    protocolo_autorizacao = models.CharField(max_length=30, blank=True, default="")

    # auditoria básica
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="nfe_entrada_criadas",
        null=True,
        blank=True,
    )
    cancelado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="nfe_entrada_canceladas",
        null=True,
        blank=True,
    )
    cancelado_em = models.DateTimeField(null=True, blank=True)
    motivo_cancelamento = models.TextField(blank=True, default="")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "fiscal_nota_fiscal_entrada"
        indexes = [
            Index(fields=["empresa", "status"], name="ix_fiscal_nfe_empresa_status"),
            Index(fields=["pedido_compra", "status"], name="ix_fiscal_nfe_pedido_status"),
            Index(fields=["modelo", "serie", "numero"], name="ix_fiscal_nfe_num"),
        ]
        constraints = [
            UniqueConstraint(
                fields=["empresa", "fornecedor", "modelo", "serie", "numero"],
                name="uq_fiscal_nfe_emp_forn_doc",
            )
        ]

    def __str__(self) -> str:
        origem = f"Pedido {self.pedido_compra_id}" if self.pedido_compra_id else "sem pedido"
        return f"NFE {self.modelo}/{self.serie}/{self.numero} ({origem})"

    def save(self, *args, **kwargs):
        if self.pedido_compra_id:
            self.empresa_id = self.pedido_compra.empresa_id
            self.loja_id = self.pedido_compra.loja_id
            self.fornecedor_id = self.pedido_compra.fornecedor_id
        super().save(*args, **kwargs)

    def recalcular_totais(self):
        itens = list(self.itens.all())
        self.valor_produtos = _money(
            sum(Decimal(item.qtd_recebida or 0) * Decimal(item.preco_unit_nf or 0) for item in itens)
        )
        self.valor_desconto = _money(sum((item.desconto_item or 0) for item in itens))
        self.valor_total = _money((self.valor_produtos or 0) - (self.valor_desconto or 0) + (self.valor_frete or 0))
        if self.valor_total < 0:
            raise ValueError("Total da nota fiscal de entrada não pode ser negativo.")
        self.save(update_fields=["valor_produtos", "valor_desconto", "valor_total", "atualizado_em"])

    def resumo_conciliacao_xml(self):
        total = self.itens_xml.count()
        conciliados = self.itens_xml.filter(produto__isnull=False).count()
        return {
            "total_itens": total,
            "itens_conciliados": conciliados,
            "itens_pendentes": total - conciliados,
            "nota_conciliada": total > 0 and conciliados == total,
        }

    def resumo_conferencia_xml(self):
        itens = list(self.itens_xml.select_related("produto_fornecedor", "produto__unidade").all())
        total = len(itens)
        conferidos = sum(1 for item in itens if item.quantidade_recebida is not None)
        conversao_pendente = sum(1 for item in itens if item.produto_id and not item.conversao_pronta)
        divergencias = self.divergencias_xml.filter(status=NotaFiscalEntradaDivergenciaXml.Status.PENDENTE)
        valor_divergente = _money(sum((div.valor_divergente or 0) for div in divergencias))
        quantidade_faltante = sum((div.quantidade_faltante or 0) for div in divergencias)
        return {
            "total_itens": total,
            "itens_conferidos": conferidos,
            "itens_nao_conferidos": total - conferidos,
            "itens_com_divergencia": divergencias.count(),
            "quantidade_faltante_total": str(quantidade_faltante),
            "valor_divergente_total": str(valor_divergente),
            "possui_divergencia_pendente": divergencias.exists(),
            "conversoes_pendentes": conversao_pendente,
            "conferencia_completa": (
                total > 0
                and self.resumo_conciliacao_xml()["nota_conciliada"]
                and conferidos == total
                and conversao_pendente == 0
            ),
        }


class NotaFiscalEntradaItem(models.Model):
    nota = models.ForeignKey(
        "fiscal.NotaFiscalEntrada",
        on_delete=models.CASCADE,
        related_name="itens",
        db_index=True,
    )

    # link direto ao item do pedido (MVP)
    pedido_item = models.ForeignKey(
        "compras.PedidoCompraItem",
        on_delete=models.PROTECT,
        related_name="itens_nf_entrada",
        db_index=True,
    )

    qtd_recebida = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    preco_unit_nf = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    desconto_item = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_item = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "fiscal_nota_fiscal_entrada_item"
        constraints = [
            UniqueConstraint(
                fields=["nota", "pedido_item"],
                name="uq_fiscal_nfe_item_nota_pedido_item",
            )
        ]
        indexes = [
            Index(fields=["nota"], name="ix_fiscal_nfe_item_nota"),
            Index(fields=["pedido_item"], name="ix_fiscal_nfe_item_pedido_item"),
        ]

    def __str__(self) -> str:
        return f"Item NF {self.nota_id} / PedidoItem {self.pedido_item_id}"


class NotaFiscalEntradaItemXml(models.Model):
    class OrigemConciliacao(models.TextChoices):
        VINCULO = "VINCULO", "Vínculo existente"
        PEDIDO = "PEDIDO", "Pedido"
        GTIN = "GTIN", "GTIN/EAN"
        MANUAL = "MANUAL", "Manual"

    nota = models.ForeignKey(
        "fiscal.NotaFiscalEntrada",
        on_delete=models.CASCADE,
        related_name="itens_xml",
        db_index=True,
    )
    numero_item = models.PositiveIntegerField()
    codigo_produto_fornecedor = models.CharField(max_length=80, blank=True, default="")
    descricao_produto = models.CharField(max_length=255, blank=True, default="")
    gtin_ean = models.CharField(max_length=14, blank=True, default="")
    ncm = models.CharField(max_length=10, blank=True, default="")
    cfop = models.CharField(max_length=4, blank=True, default="")
    unidade_comercial = models.CharField(max_length=20, blank=True, default="")
    quantidade_comercial = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    quantidade_recebida = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    valor_unitario_comercial = models.DecimalField(max_digits=18, decimal_places=10, default=0)
    valor_produto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    valor_desconto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    informacoes_adicionais = models.TextField(blank=True, default="")
    produto = models.ForeignKey(
        "produto.Produto",
        on_delete=models.PROTECT,
        related_name="itens_xml_nfe",
        null=True,
        blank=True,
        db_index=True,
    )
    produto_fornecedor = models.ForeignKey(
        "produto.ProdutoFornecedor",
        on_delete=models.PROTECT,
        related_name="itens_xml_nfe",
        null=True,
        blank=True,
        db_index=True,
    )
    pedido_item = models.ForeignKey(
        "compras.PedidoCompraItem",
        on_delete=models.PROTECT,
        related_name="itens_xml_nfe",
        null=True,
        blank=True,
        db_index=True,
    )
    origem_conciliacao = models.CharField(max_length=10, choices=OrigemConciliacao.choices, blank=True, default="")
    conciliado_em = models.DateTimeField(null=True, blank=True)
    conciliado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="itens_xml_nfe_conciliados",
        null=True,
        blank=True,
    )
    conferido_em = models.DateTimeField(null=True, blank=True)
    conferido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="itens_xml_nfe_conferidos",
        null=True,
        blank=True,
    )
    unidade_fornecedor_efetivada = models.CharField(max_length=20, blank=True, default="")
    fator_conversao_efetivado = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    quantidade_interna_efetivada = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    efetivado_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fiscal_nota_fiscal_entrada_item_xml"
        constraints = [
            UniqueConstraint(fields=["nota", "numero_item"], name="uq_fiscal_nfe_xml_item_numero"),
        ]
        indexes = [
            Index(fields=["nota", "codigo_produto_fornecedor"], name="ix_fiscal_nfe_xml_cod"),
            Index(fields=["gtin_ean"], name="ix_fiscal_nfe_xml_gtin"),
            Index(fields=["nota", "produto"], name="ix_fiscal_nfe_xml_prod"),
        ]

    def __str__(self) -> str:
        return f"Item XML NF {self.nota_id} #{self.numero_item}"

    @property
    def conciliado(self):
        return self.produto_id is not None

    @property
    def conferido(self):
        return self.quantidade_recebida is not None

    @property
    def quantidade_faltante(self):
        if self.quantidade_recebida is None:
            return None
        return Decimal(self.quantidade_comercial or 0) - Decimal(self.quantidade_recebida or 0)

    @property
    def valor_divergente(self):
        if self.quantidade_faltante is None:
            return None
        return _money(Decimal(self.quantidade_faltante or 0) * Decimal(self.valor_unitario_comercial or 0))

    @property
    def conversao_pronta(self):
        if not self.produto_id or not self.produto_fornecedor_id:
            return False
        unidade_xml = str(self.unidade_comercial or "").strip().upper()
        unidade_vinculo = str(self.produto_fornecedor.unidade_fornecedor or "").strip().upper()
        return bool(unidade_vinculo and unidade_xml == unidade_vinculo)


class NotaFiscalEntradaDivergenciaXml(models.Model):
    class Status(models.TextChoices):
        PENDENTE = "PENDENTE", "Pendente"
        RESOLVIDA = "RESOLVIDA", "Resolvida"
        CANCELADA = "CANCELADA", "Cancelada"

    empresa = models.ForeignKey("cadastros.Empresa", on_delete=models.PROTECT, related_name="divergencias_nfe_xml", db_index=True)
    nota = models.ForeignKey("fiscal.NotaFiscalEntrada", on_delete=models.CASCADE, related_name="divergencias_xml", db_index=True)
    item_xml = models.OneToOneField("fiscal.NotaFiscalEntradaItemXml", on_delete=models.CASCADE, related_name="divergencia", db_index=True)
    fornecedor = models.ForeignKey("cadastros.Fornecedor", on_delete=models.PROTECT, related_name="divergencias_nfe_xml", db_index=True)
    produto = models.ForeignKey("produto.Produto", on_delete=models.PROTECT, related_name="divergencias_nfe_xml", db_index=True)
    quantidade_fiscal = models.DecimalField(max_digits=18, decimal_places=6)
    quantidade_recebida = models.DecimalField(max_digits=18, decimal_places=6)
    quantidade_faltante = models.DecimalField(max_digits=18, decimal_places=6)
    valor_divergente = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDENTE, db_index=True)
    conferido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="divergencias_nfe_xml_conferidas",
        null=True,
        blank=True,
    )
    resolvido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="divergencias_nfe_xml_resolvidas",
        null=True,
        blank=True,
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    resolvido_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "fiscal_nota_fiscal_entrada_divergencia_xml"
        indexes = [
            Index(fields=["nota", "status"], name="ix_fiscal_nfe_div_xml_st"),
            Index(fields=["empresa", "status"], name="ix_fiscal_nfe_div_emp_st"),
        ]

    def __str__(self) -> str:
        return f"Divergência XML NF {self.nota_id} item {self.item_xml_id}"
