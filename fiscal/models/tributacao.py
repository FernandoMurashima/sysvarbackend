from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone


class Tributo(models.Model):
    ESFERA_FEDERAL = "FEDERAL"
    ESFERA_ESTADUAL = "ESTADUAL"
    ESFERA_MUNICIPAL = "MUNICIPAL"
    ESFERA_CHOICES = [
        (ESFERA_FEDERAL, "Federal"),
        (ESFERA_ESTADUAL, "Estadual"),
        (ESFERA_MUNICIPAL, "Municipal"),
    ]

    empresa = models.ForeignKey("cadastros.Empresa", on_delete=models.PROTECT, related_name="tributos", db_index=True)
    codigo = models.CharField(max_length=20, db_index=True)
    descricao = models.CharField(max_length=120)
    esfera = models.CharField(max_length=12, choices=ESFERA_CHOICES, default=ESFERA_FEDERAL, db_index=True)
    atual = models.BooleanField(default=True, db_index=True)
    ativo = models.BooleanField(default=True, db_index=True)
    observacoes = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        ordering = ["codigo"]
        constraints = [
            models.UniqueConstraint(fields=["empresa", "codigo"], name="uq_tributo_empresa_codigo"),
        ]
        indexes = [
            models.Index(fields=["empresa", "codigo"]),
            models.Index(fields=["ativo"]),
        ]

    def __str__(self):
        return f"{self.codigo} - {self.descricao}"


class RegraTributaria(models.Model):
    REGIME_TODOS = "TODOS"
    REGIME_SIMPLES = "SIMPLES"
    REGIME_LUCRO_PRESUMIDO = "LUCRO_PRESUMIDO"
    REGIME_LUCRO_REAL = "LUCRO_REAL"
    REGIME_CHOICES = [
        (REGIME_TODOS, "Todos"),
        (REGIME_SIMPLES, "Simples Nacional"),
        (REGIME_LUCRO_PRESUMIDO, "Lucro Presumido"),
        (REGIME_LUCRO_REAL, "Lucro Real"),
    ]

    TIPO_PRODUTO_TODOS = "TODOS"
    TIPO_PRODUTO_REVENDA = "REVENDA"
    TIPO_PRODUTO_USO_CONSUMO = "USO_CONSUMO"
    TIPO_PRODUTO_INSUMO = "INSUMO"
    TIPO_PRODUTO_PROPRIO = "PROPRIO"
    TIPO_PRODUTO_CHOICES = [
        (TIPO_PRODUTO_TODOS, "Todos"),
        (TIPO_PRODUTO_REVENDA, "Revenda"),
        (TIPO_PRODUTO_USO_CONSUMO, "Uso/Consumo"),
        (TIPO_PRODUTO_INSUMO, "Insumo"),
        (TIPO_PRODUTO_PROPRIO, "Produto próprio"),
    ]

    BASE_VALOR_ITEM = "VALOR_ITEM"
    BASE_VALOR_TOTAL = "VALOR_TOTAL"
    BASE_CHOICES = [
        (BASE_VALOR_ITEM, "Valor do item"),
        (BASE_VALOR_TOTAL, "Valor total"),
    ]

    empresa = models.ForeignKey("cadastros.Empresa", on_delete=models.PROTECT, related_name="regras_tributarias", db_index=True)
    nome = models.CharField(max_length=120, db_index=True)
    tributo = models.ForeignKey(Tributo, on_delete=models.PROTECT, related_name="regras", db_index=True)
    cfop = models.ForeignKey("fiscal.Cfop", on_delete=models.PROTECT, null=True, blank=True, related_name="regras_tributarias", db_index=True)
    ncm = models.ForeignKey("produto.Ncm", on_delete=models.PROTECT, null=True, blank=True, related_name="regras_tributarias", db_index=True)
    tipo_operacao = models.CharField(max_length=20, default="VENDA", db_index=True)
    regime_tributario = models.CharField(max_length=20, choices=REGIME_CHOICES, default=REGIME_TODOS, db_index=True)
    tipo_produto = models.CharField(max_length=20, choices=TIPO_PRODUTO_CHOICES, default=TIPO_PRODUTO_TODOS, db_index=True)
    uf_origem = models.CharField(max_length=2, null=True, blank=True, db_index=True)
    uf_destino = models.CharField(max_length=2, null=True, blank=True, db_index=True)
    cst_csosn = models.CharField(max_length=4, null=True, blank=True)
    base_calculo = models.CharField(max_length=20, choices=BASE_CHOICES, default=BASE_VALOR_ITEM)
    aliquota = models.DecimalField(max_digits=7, decimal_places=4, default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    reducao_base = models.DecimalField(max_digits=7, decimal_places=4, default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    permite_credito = models.BooleanField(default=False)
    compoe_custo = models.BooleanField(default=False)
    entra_dre = models.BooleanField(default=True)
    ativo = models.BooleanField(default=True, db_index=True)
    vigencia_inicio = models.DateField(default=timezone.localdate, db_index=True)
    vigencia_fim = models.DateField(null=True, blank=True)
    observacoes = models.CharField(max_length=255, null=True, blank=True)
    criado_em = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["nome", "tributo__codigo"]
        indexes = [
            models.Index(fields=["empresa", "ativo"]),
            models.Index(fields=["tipo_operacao", "regime_tributario"]),
            models.Index(fields=["uf_origem", "uf_destino"]),
            models.Index(fields=["vigencia_inicio", "vigencia_fim"]),
        ]

    def __str__(self):
        return f"{self.nome} - {self.tributo.codigo}"
