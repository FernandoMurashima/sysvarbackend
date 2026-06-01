from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import models
from django.utils import timezone


def money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class VendaPdv(models.Model):
    class Status(models.TextChoices):
        ABERTA = "ABERTA", "Aberta"
        FINALIZADA = "FINALIZADA", "Finalizada"
        CANCELADA = "CANCELADA", "Cancelada"

    loja = models.ForeignKey("cadastros.Loja", on_delete=models.PROTECT, related_name="vendas_pdv")
    caixa = models.ForeignKey("financeiro.Caixa", on_delete=models.PROTECT, related_name="vendas_pdv", null=True, blank=True)
    cliente = models.ForeignKey("cadastros.Cliente", on_delete=models.PROTECT, related_name="vendas_pdv")
    vendedor = models.ForeignKey("cadastros.Funcionarios", on_delete=models.PROTECT, related_name="vendas_pdv")

    documento = models.CharField(max_length=50, unique=True, db_index=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.FINALIZADA, db_index=True)
    forma_pagamento = models.CharField(max_length=30)
    data_venda = models.DateTimeField(default=timezone.now, db_index=True)

    subtotal = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    desconto_itens = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    desconto_geral = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    valor_recebido = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    troco = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="vendas_pdv_criadas",
        null=True,
        blank=True,
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "fiscal_venda_pdv"
        ordering = ["-data_venda", "-id"]
        indexes = [
            models.Index(fields=["loja", "data_venda"], name="ix_venda_pdv_loja_data"),
            models.Index(fields=["caixa", "data_venda"], name="ix_venda_pdv_caixa_data"),
            models.Index(fields=["cliente", "data_venda"], name="ix_venda_pdv_cliente_data"),
            models.Index(fields=["status"], name="ix_venda_pdv_status"),
        ]

    def __str__(self) -> str:
        return f"{self.documento} - {self.total}"


class VendaPdvItem(models.Model):
    venda = models.ForeignKey(VendaPdv, on_delete=models.CASCADE, related_name="itens")
    produto = models.ForeignKey("produto.Produto", on_delete=models.PROTECT)
    sku = models.ForeignKey("produto.ProdutoDetalhe", on_delete=models.PROTECT)
    ean = models.CharField(max_length=13, db_index=True)
    referencia = models.CharField(max_length=30, blank=True, default="")
    descricao = models.CharField(max_length=120)
    cor = models.CharField(max_length=80, blank=True, default="")
    tamanho = models.CharField(max_length=30, blank=True, default="")
    quantidade = models.PositiveIntegerField()
    preco_unitario = models.DecimalField(max_digits=18, decimal_places=4)
    desconto = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_item = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    class Meta:
        db_table = "fiscal_venda_pdv_item"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["venda"], name="ix_venda_pdv_item_venda"),
            models.Index(fields=["ean"], name="ix_venda_pdv_item_ean"),
        ]

    def save(self, *args, **kwargs):
        bruto = Decimal(self.quantidade or 0) * Decimal(self.preco_unitario or 0)
        self.total_item = money(bruto - Decimal(self.desconto or 0))
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.venda_id} - {self.descricao}"


class VendaPdvPagamento(models.Model):
    venda = models.ForeignKey(VendaPdv, on_delete=models.CASCADE, related_name="pagamentos")
    forma = models.CharField(max_length=30, db_index=True)
    descricao = models.CharField(max_length=80, blank=True, default="")
    valor = models.DecimalField(max_digits=18, decimal_places=2)
    autorizacao = models.CharField(max_length=60, blank=True, default="")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fiscal_venda_pdv_pagamento"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["venda"], name="ix_venda_pdv_pag_venda"),
            models.Index(fields=["forma"], name="ix_venda_pdv_pag_forma"),
        ]

    def __str__(self) -> str:
        return f"{self.venda_id} - {self.forma} - {self.valor}"


class NFCe(models.Model):
    class Status(models.TextChoices):
        DIGITADA = "DIGITADA", "Digitada"
        EMITINDO = "EMITINDO", "Emitindo"
        AUTORIZADA = "AUTORIZADA", "Autorizada"
        REJEITADA = "REJEITADA", "Rejeitada"
        CANCELADA = "CANCELADA", "Cancelada"
        CONTINGENCIA = "CONTINGENCIA", "Contingencia"

    venda = models.OneToOneField(VendaPdv, on_delete=models.PROTECT, related_name="nfce")
    ambiente = models.CharField(max_length=12, default="HOMOLOGACAO")
    modelo = models.CharField(max_length=2, default="65")
    serie = models.PositiveIntegerField(default=1)
    numero = models.PositiveIntegerField(db_index=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DIGITADA, db_index=True)
    chave_acesso = models.CharField(max_length=44, blank=True, default="", db_index=True)
    protocolo = models.CharField(max_length=30, blank=True, default="")
    qr_code_url = models.TextField(blank=True, default="")
    xml = models.TextField(blank=True, default="")
    retorno_codigo = models.CharField(max_length=10, blank=True, default="")
    retorno_mensagem = models.CharField(max_length=255, blank=True, default="")
    autorizada_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "fiscal_nfce"
        ordering = ["-numero"]
        constraints = [
            models.UniqueConstraint(fields=["serie", "numero"], name="uq_nfce_serie_numero"),
        ]
        indexes = [
            models.Index(fields=["status"], name="ix_nfce_status"),
            models.Index(fields=["chave_acesso"], name="ix_nfce_chave"),
        ]

    def __str__(self) -> str:
        return f"NFC-e {self.serie}/{self.numero} - {self.status}"
