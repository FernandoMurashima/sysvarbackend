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
    valor_unitario_comercial = models.DecimalField(max_digits=18, decimal_places=10, default=0)
    valor_produto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    valor_desconto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    informacoes_adicionais = models.TextField(blank=True, default="")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fiscal_nota_fiscal_entrada_item_xml"
        constraints = [
            UniqueConstraint(fields=["nota", "numero_item"], name="uq_fiscal_nfe_xml_item_numero"),
        ]
        indexes = [
            Index(fields=["nota", "codigo_produto_fornecedor"], name="ix_fiscal_nfe_xml_cod"),
            Index(fields=["gtin_ean"], name="ix_fiscal_nfe_xml_gtin"),
        ]

    def __str__(self) -> str:
        return f"Item XML NF {self.nota_id} #{self.numero_item}"
