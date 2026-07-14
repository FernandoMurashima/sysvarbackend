from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone


class Cfop(models.Model):
    TIPO_VENDA = "VENDA"
    TIPO_COMPRA = "COMPRA"
    TIPO_DEVOLUCAO = "DEVOLUCAO"
    TIPO_TRANSFERENCIA = "TRANSFERENCIA"
    TIPO_OUTROS = "OUTROS"
    TIPO_CHOICES = [
        (TIPO_VENDA, "Venda"),
        (TIPO_COMPRA, "Compra"),
        (TIPO_DEVOLUCAO, "Devolução"),
        (TIPO_TRANSFERENCIA, "Transferência"),
        (TIPO_OUTROS, "Outros"),
    ]

    DESTINO_DENTRO = "DENTRO_UF"
    DESTINO_FORA = "FORA_UF"
    DESTINO_AMBOS = "AMBOS"
    DESTINO_CHOICES = [
        (DESTINO_DENTRO, "Dentro do estado"),
        (DESTINO_FORA, "Fora do estado"),
        (DESTINO_AMBOS, "Ambos"),
    ]

    empresa = models.ForeignKey(
        "cadastros.Empresa",
        on_delete=models.PROTECT,
        related_name="cfops",
        db_index=True,
    )
    codigo = models.CharField(
        max_length=4,
        validators=[RegexValidator(r"^\d{4}$", "CFOP deve ter 4 dígitos.")],
        db_index=True,
    )
    descricao = models.CharField(max_length=255)
    tipo_operacao = models.CharField(max_length=20, choices=TIPO_CHOICES, default=TIPO_OUTROS, db_index=True)
    destino = models.CharField(max_length=12, choices=DESTINO_CHOICES, default=DESTINO_AMBOS, db_index=True)
    movimenta_estoque = models.BooleanField(default=True)
    gera_financeiro = models.BooleanField(default=True)
    ativo = models.BooleanField(default=True, db_index=True)
    observacoes = models.CharField(max_length=255, null=True, blank=True)
    criado_em = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["codigo"]
        constraints = [
            models.UniqueConstraint(fields=["empresa", "codigo"], name="uq_cfop_empresa_codigo"),
        ]
        indexes = [
            models.Index(fields=["empresa", "codigo"]),
            models.Index(fields=["tipo_operacao", "destino"]),
            models.Index(fields=["ativo"]),
        ]

    def __str__(self):
        return f"{self.codigo} - {self.descricao}"
