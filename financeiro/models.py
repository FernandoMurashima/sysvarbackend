from django.db import models
from django.utils import timezone
from cadastros.models import Loja, Cliente, Fornecedor, Nat_Lancamento

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
# Caixa e Bancos
# =========================
class Caixa(models.Model):
    TIPO_LOJA = 'LOJA'
    TIPO_MASTER = 'MASTER'
    TIPO_CAIXA_CHOICES = [
        (TIPO_LOJA, 'Caixa da loja'),
        (TIPO_MASTER, 'Caixa master do grupo'),
    ]

    Idcaixa = models.BigAutoField(primary_key=True)
    idloja = models.ForeignKey(Loja, on_delete=models.PROTECT, related_name='caixas', db_index=True, null=True, blank=True)
    tipo_caixa = models.CharField(max_length=10, choices=TIPO_CAIXA_CHOICES, default=TIPO_LOJA, db_index=True)
    codigo = models.CharField(max_length=20)
    descricao = models.CharField(max_length=120)
    saldo_inicial = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    saldo_atual = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    ativo = models.BooleanField(default=True)
    data_abertura = models.DateField(default=timezone.now)
    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'financeiro_caixa'
        constraints = [
            models.UniqueConstraint(fields=['idloja', 'codigo'], name='uq_caixa_loja_codigo')
        ]
        indexes = [
            models.Index(fields=['idloja', 'ativo']),
            models.Index(fields=['tipo_caixa', 'ativo']),
            models.Index(fields=['codigo']),
        ]
        ordering = ['tipo_caixa', 'idloja', 'codigo']

    def __str__(self):
        return f'{self.codigo} - {self.descricao}'


class ContaBancaria(models.Model):
    TIPO_CONTA_CHOICES = [
        ('CORRENTE', 'Conta corrente'),
        ('POUPANCA', 'Poupança'),
        ('PAGAMENTO', 'Conta pagamento'),
    ]

    Idconta = models.BigAutoField(primary_key=True)
    idloja = models.ForeignKey(Loja, on_delete=models.PROTECT, related_name='contas_bancarias', db_index=True)
    descricao = models.CharField(max_length=120)
    banco = models.CharField(max_length=80)
    agencia = models.CharField(max_length=20)
    conta = models.CharField(max_length=30)
    tipo_conta = models.CharField(max_length=15, choices=TIPO_CONTA_CHOICES, default='CORRENTE')
    pix_chave = models.CharField(max_length=120, null=True, blank=True)
    saldo_inicial = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    saldo_atual = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    ativo = models.BooleanField(default=True)
    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'financeiro_conta_bancaria'
        constraints = [
            models.UniqueConstraint(fields=['idloja', 'banco', 'agencia', 'conta'], name='uq_conta_bancaria')
        ]
        indexes = [
            models.Index(fields=['idloja', 'ativo']),
            models.Index(fields=['banco', 'agencia', 'conta']),
        ]
        ordering = ['idloja', 'banco', 'agencia', 'conta']

    def __str__(self):
        return f'{self.banco} Ag {self.agencia} Cc {self.conta}'


class MovimentacaoFinanceira(models.Model):
    TIPO_ENTRADA = 'ENTRADA'
    TIPO_SAIDA = 'SAIDA'
    TIPO_TRANSFERENCIA = 'TRANSFERENCIA'
    TIPO_CHOICES = [
        (TIPO_ENTRADA, 'Entrada'),
        (TIPO_SAIDA, 'Saída'),
        (TIPO_TRANSFERENCIA, 'Transferência'),
    ]

    STATUS_PREVISTA = 'PREVISTA'
    STATUS_EFETIVA = 'EFETIVA'
    STATUS_CANCELADA = 'CANCELADA'
    STATUS_CHOICES = [
        (STATUS_PREVISTA, 'Prevista'),
        (STATUS_EFETIVA, 'Efetiva'),
        (STATUS_CANCELADA, 'Cancelada'),
    ]

    ORIGEM_MANUAL = 'MANUAL'
    ORIGEM_PAGAR = 'PAGAR'
    ORIGEM_RECEBER = 'RECEBER'
    ORIGEM_CHOICES = [
        (ORIGEM_MANUAL, 'Manual'),
        (ORIGEM_PAGAR, 'Contas a pagar'),
        (ORIGEM_RECEBER, 'Contas a receber'),
    ]

    Idmovimentacao = models.BigAutoField(primary_key=True)
    idloja = models.ForeignKey(Loja, on_delete=models.PROTECT, db_index=True)
    data_movimento = models.DateField(default=timezone.now, db_index=True)
    tipo = models.CharField(max_length=15, choices=TIPO_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_EFETIVA, db_index=True)
    origem = models.CharField(max_length=10, choices=ORIGEM_CHOICES, default=ORIGEM_MANUAL, db_index=True)

    valor = models.DecimalField(max_digits=18, decimal_places=2)
    historico = models.CharField(max_length=255)
    documento = models.CharField(max_length=50, null=True, blank=True)

    Idnatureza = models.ForeignKey(Nat_Lancamento, on_delete=models.PROTECT, null=True, blank=True)
    FormaPagamento = models.CharField(max_length=30, null=True, blank=True)
    caixa = models.ForeignKey(Caixa, on_delete=models.PROTECT, null=True, blank=True, related_name='movimentacoes')
    conta_bancaria = models.ForeignKey(ContaBancaria, on_delete=models.PROTECT, null=True, blank=True, related_name='movimentacoes')
    pagar_item = models.ForeignKey('PagarItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='movimentacoes')
    receber_item = models.ForeignKey('ReceberItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='movimentacoes')

    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'financeiro_movimentacao'
        indexes = [
            models.Index(fields=['idloja', 'data_movimento']),
            models.Index(fields=['tipo', 'status']),
            models.Index(fields=['caixa']),
            models.Index(fields=['conta_bancaria']),
        ]
        ordering = ['-data_movimento', '-Idmovimentacao']

    def __str__(self):
        return f'{self.Idmovimentacao} - {self.tipo} - {self.valor}'


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


# =========================
# Contas a Receber
# =========================
class Receber(models.Model):
    Idreceber = models.BigAutoField(primary_key=True)

    idloja = models.ForeignKey(Loja, on_delete=models.PROTECT, db_index=True)
    idcliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, db_index=True)

    Titulo = models.CharField(max_length=60)
    Documento = models.CharField(max_length=30, null=True, blank=True)
    Data_emissao = models.DateField(default=timezone.now)
    Valor_total = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    Previsao = models.BooleanField(default=False)
    FormaPagamento = models.CharField(max_length=30, null=True, blank=True)

    Idnatureza = models.ForeignKey(Nat_Lancamento, on_delete=models.PROTECT)
    conta_contabil = models.CharField(max_length=50, null=True, blank=True)

    pedido_venda = models.IntegerField(null=True, blank=True)
    nfe_id = models.IntegerField(null=True, blank=True)

    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'financeiro_receber'
        indexes = [models.Index(fields=['idloja', 'idcliente', 'Data_emissao'])]

    def __str__(self):
        return f'{self.Idreceber} - {self.Titulo}'


class ReceberItem(models.Model):
    STATUS_PREVISTO = 'PREVISTO'
    STATUS_EFETIVO = 'EFETIVO'
    STATUS_BAIXADO = 'BAIXADO'
    STATUS_CANCELADO = 'CANCELADO'
    STATUS_CHOICES = [
        (STATUS_PREVISTO, 'Previsto'),
        (STATUS_EFETIVO, 'Efetivo'),
        (STATUS_BAIXADO, 'Baixado'),
        (STATUS_CANCELADO, 'Cancelado'),
    ]

    Idreceberitem = models.BigAutoField(primary_key=True)
    Idreceber = models.ForeignKey(Receber, on_delete=models.CASCADE, related_name='itens', db_index=True)

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
        db_table = 'financeiro_receber_item'
        constraints = [models.UniqueConstraint(fields=['Idreceber', 'parcela_n'], name='uq_receber_parcela')]
        indexes = [models.Index(fields=['status']), models.Index(fields=['Data_vencimento'])]

    def __str__(self):
        return f'{self.Idreceber_id}-{self.parcela_n}'


class ReceberRateio(models.Model):
    Idrateio = models.BigAutoField(primary_key=True)
    Idreceberitem = models.ForeignKey(ReceberItem, on_delete=models.CASCADE, related_name='rateios', db_index=True)
    Idnatureza = models.ForeignKey(Nat_Lancamento, on_delete=models.PROTECT)
    valor = models.DecimalField(max_digits=18, decimal_places=2)
    centro_custo = models.CharField(max_length=50, null=True, blank=True)

    class Meta:
        db_table = 'financeiro_receber_rateio'

    def __str__(self):
        return f'Rateio {self.Idrateio} - Parcela {self.Idreceberitem_id}'
