from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import models
from django.db.models import Index, UniqueConstraint


def _money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class NotaFiscalSaida(models.Model):
    class Status(models.TextChoices):
        DIGITADA = "DI", "Digitada"
        PRONTA = "PR", "Pronta para emissão"
        AUTORIZADA = "AU", "Autorizada"
        CANCELADA = "CA", "Cancelada"

    class TipoOperacao(models.TextChoices):
        TRANSFERENCIA = "TRANSFERENCIA", "Transferência"
        REMESSA = "REMESSA", "Remessa"
        VENDA = "VENDA", "Venda"

    empresa = models.ForeignKey("cadastros.Empresa", on_delete=models.PROTECT, related_name="notas_saida", db_index=True)
    loja_origem = models.ForeignKey("cadastros.Loja", on_delete=models.PROTECT, related_name="notas_saida_emitidas")
    loja_destino = models.ForeignKey("cadastros.Loja", on_delete=models.PROTECT, related_name="notas_saida_recebidas", null=True, blank=True)
    ordem_producao = models.ForeignKey("produto.OrdemProducao", on_delete=models.PROTECT, related_name="notas_saida", null=True, blank=True)

    tipo_operacao = models.CharField(max_length=20, choices=TipoOperacao.choices, default=TipoOperacao.TRANSFERENCIA, db_index=True)
    modelo = models.CharField(max_length=2, default="55")
    serie = models.CharField(max_length=10, blank=True, default="")
    numero = models.CharField(max_length=20)
    documento_origem = models.CharField(max_length=50, blank=True, default="", db_index=True)
    chave_acesso = models.CharField(max_length=60, blank=True, default="")
    protocolo_autorizacao = models.CharField(max_length=40, blank=True, default="")
    xml = models.TextField(blank=True, default="")
    cfop = models.CharField(max_length=4, blank=True, default="")
    natureza_operacao = models.CharField(max_length=120, blank=True, default="Transferência de produção")
    status = models.CharField(max_length=2, choices=Status.choices, default=Status.DIGITADA, db_index=True)

    dt_emissao = models.DateField()
    dt_saida = models.DateField()
    valor_produtos = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    valor_desconto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    valor_frete = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    valor_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    observacoes = models.CharField(max_length=255, blank=True, default="")
    autorizada_em = models.DateTimeField(null=True, blank=True)

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="nfe_saida_criadas",
        null=True,
        blank=True,
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "fiscal_nota_fiscal_saida"
        constraints = [
            UniqueConstraint(fields=["empresa", "modelo", "serie", "numero"], name="uq_fiscal_nfs_empresa_modelo_serie_numero"),
        ]
        indexes = [
            Index(fields=["empresa", "status"], name="ix_fiscal_nfs_empresa_status"),
            Index(fields=["loja_origem", "dt_emissao"], name="ix_fiscal_nfs_origem_data"),
            Index(fields=["loja_destino", "dt_saida"], name="ix_fiscal_nfs_destino_data"),
            Index(fields=["ordem_producao"], name="ix_fiscal_nfs_op"),
        ]

    def __str__(self):
        return f"NFE {self.modelo}/{self.serie}/{self.numero}"

    def recalcular_totais(self):
        itens = list(self.itens.all())
        self.valor_produtos = _money(sum(Decimal(item.quantidade or 0) * Decimal(item.valor_unitario or 0) for item in itens))
        self.valor_desconto = _money(sum((item.valor_desconto or 0) for item in itens))
        self.valor_total = _money((self.valor_produtos or 0) - (self.valor_desconto or 0) + (self.valor_frete or 0))
        self.save(update_fields=["valor_produtos", "valor_desconto", "valor_total", "atualizado_em"])


class NotaFiscalSaidaItem(models.Model):
    nota = models.ForeignKey("fiscal.NotaFiscalSaida", on_delete=models.CASCADE, related_name="itens", db_index=True)
    produto = models.ForeignKey("produto.Produto", on_delete=models.PROTECT)
    sku = models.ForeignKey("produto.ProdutoDetalhe", on_delete=models.PROTECT)
    ean = models.CharField(max_length=13, db_index=True)
    referencia = models.CharField(max_length=30, blank=True, default="")
    descricao = models.CharField(max_length=120)
    cor = models.CharField(max_length=80, blank=True, default="")
    tamanho = models.CharField(max_length=30, blank=True, default="")
    ncm = models.CharField(max_length=10, blank=True, default="")
    cfop = models.CharField(max_length=4, blank=True, default="")
    quantidade = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    valor_unitario = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    valor_desconto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    valor_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "fiscal_nota_fiscal_saida_item"
        indexes = [
            Index(fields=["nota"], name="ix_fiscal_nfs_item_nota"),
            Index(fields=["sku"], name="ix_fiscal_nfs_item_sku"),
            Index(fields=["ean"], name="ix_fiscal_nfs_item_ean"),
        ]

    def save(self, *args, **kwargs):
        self.valor_total = _money((Decimal(self.quantidade or 0) * Decimal(self.valor_unitario or 0)) - Decimal(self.valor_desconto or 0))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.nota_id} - {self.descricao}"
