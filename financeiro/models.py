from django.db import models
from django.utils import timezone
from cadastros.models import Loja, Fornecedor, Nat_Lancamento

# =========================
# Formas de Pagamento
# =========================
class FormaPagamento(models.Model):
    Idformapagamento = models.BigAutoField(primary_key=True)
    codigo = models.CharField(max_length=10, unique=True)   # ex.: 'AV', '30/60', '01'
    descricao = models.CharField(max_length=120)
    num_parcelas = models.IntegerField(default=1)
    ativo = models.BooleanField(default=True)
    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'financeiro_forma_pagamento'
        ordering = ['codigo']

    def __str__(self):
        return f"{self.codigo} - {self.descricao}"


class FormaPagamentoParcela(models.Model):
    Idformapagparcela = models.BigAutoField(primary_key=True)
    forma = models.ForeignKey(FormaPagamento, on_delete=models.CASCADE, related_name='parcelas')
    ordem = models.IntegerField()                           # 1,2,3...
    dias = models.IntegerField()                            # prazo em dias
    percentual = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    valor_fixo = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'financeiro_forma_pagamento_parcela'
        constraints = [
            models.UniqueConstraint(fields=['forma', 'ordem'], name='uq_formapag_parcela_ordem')
        ]
        indexes = [models.Index(fields=['forma', 'ordem'])]
        ordering = ['forma', 'ordem']

    def __str__(self):
        return f"{self.forma.codigo} - Parcela {self.ordem} ({self.dias} dias)"


# =========================
# Contas a Pagar
# =========================
class Pagar(models.Model):
    Idpagar = models.BigAutoField(primary_key=True)

    idloja = models.ForeignKey(Loja, on_delete=models.PROTECT, db_index=True)
    idfornecedor = models.ForeignKey(Fornecedor, on_delete=models.PROTECT, db_index=True)

    Titulo = models.CharField(max_length=60)
    Documento = models.CharField(max_length=30, null=True, blank=True)
    Data_emissao = models.DateField(default=timezone.now)
    Valor_total = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    Previsao = models.BooleanField(default=False)
    FormaPagamento = models.CharField(max_length=30, null=True, blank=True)

    Idnatureza = models.ForeignKey(Nat_Lancamento, on_delete=models.PROTECT)
    conta_contabil = models.CharField(max_length=50, null=True, blank=True)

    pedido_compra = models.IntegerField(null=True, blank=True)
    nfe_id = models.IntegerField(null=True, blank=True)

    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'financeiro_pagar'
        indexes = [models.Index(fields=['idloja', 'idfornecedor', 'Data_emissao'])]

    def __str__(self):
        return f'{self.Idpagar} - {self.Titulo}'


class PagarItem(models.Model):
    STATUS_PREVISTO = 'PREVISTO'
    STATUS_EFETIVO = 'EFETIVO'
    STATUS_BAIXADO  = 'BAIXADO'
    STATUS_CANCELADO = 'CANCELADO'
    STATUS_CHOICES = [
        (STATUS_PREVISTO, 'Previsto'),
        (STATUS_EFETIVO, 'Efetivo'),
        (STATUS_BAIXADO, 'Baixado'),
        (STATUS_CANCELADO, 'Cancelado'),
    ]

    Idpagaritem = models.BigAutoField(primary_key=True)
    Idpagar = models.ForeignKey(Pagar, on_delete=models.CASCADE, related_name='itens', db_index=True)

    parcela_n = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PREVISTO)

    Data_vencimento = models.DateField()
    valor_parcela = models.DecimalField(max_digits=18, decimal_places=2)

    FormaPagamento = models.CharField(max_length=30, null=True, blank=True)
    idconta = models.IntegerField(null=True, blank=True)

    juros = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    desconto = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    data_baixa = models.DateField(null=True, blank=True)
    valor_baixa = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    Previsao = models.BooleanField(default=False)
    Idnatureza = models.ForeignKey(Nat_Lancamento, on_delete=models.PROTECT, null=True, blank=True)

    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'financeiro_pagar_item'
        constraints = [models.UniqueConstraint(fields=['Idpagar', 'parcela_n'], name='uq_pagar_parcela')]
        indexes = [models.Index(fields=['status']), models.Index(fields=['Data_vencimento'])]

    def __str__(self):
        return f'{self.Idpagar_id}-{self.parcela_n}'


class PagarRateio(models.Model):
    Idrateio = models.BigAutoField(primary_key=True)
    Idpagaritem = models.ForeignKey(PagarItem, on_delete=models.CASCADE, related_name='rateios', db_index=True)
    Idnatureza = models.ForeignKey(Nat_Lancamento, on_delete=models.PROTECT)
    valor = models.DecimalField(max_digits=18, decimal_places=2)
    centro_custo = models.CharField(max_length=50, null=True, blank=True)

    class Meta:
        db_table = 'financeiro_pagar_rateio'

    def __str__(self):
        return f'Rateio {self.Idrateio} - Parcela {self.Idpagaritem_id}'
