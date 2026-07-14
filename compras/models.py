from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator
from cadastros.models import Loja, Fornecedor
from produto.models import Produto, Cor, Pack, PackItem

STATUS_PC = (
    ('AB', 'Aberto'),
    ('AP', 'Aprovado'),
    ('AT', 'Atendido'),
    ('CA', 'Cancelado'),
)

TIPO_PC = (
    ('1', 'Revenda'),
    ('2', 'Uso/Consumo'),
)

ENTREGA_STATUS = (
    ('PREV', 'Prevista'),
    ('PARC', 'Parcial'),
    ('RECB', 'Recebida'),
    ('ATR',  'Atrasada'),
)

PARCELA_STATUS = (
    ('PLAN', 'Planejada'),
    ('GERADA', 'Gerada em Financeiro'),
    ('CANC', 'Cancelada'),
)

class PedidoCompra(models.Model):
    id = models.BigAutoField(primary_key=True)

    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, null=True, blank=True, related_name='pedidos_compra', db_index=True)
    tipo = models.CharField(max_length=1, choices=TIPO_PC)
    loja = models.ForeignKey(Loja, on_delete=models.PROTECT)
    fornecedor = models.ForeignKey(Fornecedor, on_delete=models.PROTECT)

    emissao = models.DateField(default=timezone.now)
    previsao_entrega = models.DateField(null=True, blank=True)

    # snapshot textual do código da forma (ex.: 'AV', '30/60')
    forma_pagamento = models.CharField(max_length=30, null=True, blank=True)

    status = models.CharField(max_length=2, choices=STATUS_PC, default='AB')

    total_itens = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_desconto = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    frete = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_pedido = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    observacoes = models.TextField(null=True, blank=True)
    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'compras_pedido_compra'
        ordering = ['-emissao', '-id']

    def __str__(self):
        return f'PC {self.id} - {self.get_tipo_display()} - {self.get_status_display()}'

    def recomputa_totais(self):
        itens = self.itens.all()
        self.total_itens = sum([i.total_item or 0 for i in itens])
        self.total_pedido = (self.total_itens - (self.total_desconto or 0)) + (self.frete or 0)


class PedidoCompraItem(models.Model):
    id = models.BigAutoField(primary_key=True)
    pedido = models.ForeignKey(PedidoCompra, on_delete=models.CASCADE, related_name='itens')

    # Revenda
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT, null=True, blank=True)
    cor = models.ForeignKey(Cor, on_delete=models.PROTECT, null=True, blank=True)
    pack = models.ForeignKey(Pack, on_delete=models.PROTECT, null=True, blank=True)
    n_packs = models.PositiveIntegerField(default=0)

    # Uso/Consumo
    descricao_livre = models.CharField(max_length=200, null=True, blank=True)

    # Números
    qtd = models.DecimalField(max_digits=12, decimal_places=3, default=0, validators=[MinValueValidator(0)])
    preco_unit = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(0)])
    desconto_valor = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_item = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    observacoes = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = 'compras_pedido_compra_item'
        ordering = ['id']

    def __str__(self):
        return f'Item {self.id} do PC {self.pedido_id}'

    def calcular_qtd_revenda(self):
        if not self.pack_id or self.n_packs <= 0:
            return 0
        soma_pack = PackItem.objects.filter(pack_id=self.pack_id).aggregate(total=models.Sum('qtd'))['total'] or 0
        return int(soma_pack) * int(self.n_packs)

    def recalcular_totais(self):
        if self.pedido.tipo == '1':  # Revenda: qtd calculada pelo pack
            self.qtd = self.calcular_qtd_revenda()
        bruto = (self.qtd or 0) * (self.preco_unit or 0)
        self.total_item = bruto - (self.desconto_valor or 0)


class PedidoCompraEntrega(models.Model):
    id = models.BigAutoField(primary_key=True)
    item = models.ForeignKey(PedidoCompraItem, on_delete=models.CASCADE, related_name='entregas')

    qtd_prevista = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    data_prevista = models.DateField(null=True, blank=True)

    qtd_recebida = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    data_recebida = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=4, choices=ENTREGA_STATUS, default='PREV')
    observacao = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = 'compras_pedido_compra_entrega'
        ordering = ['id']

    def __str__(self):
        return f'Entrega {self.id} do Item {self.item_id}'


class PedidoCompraParcela(models.Model):
    id = models.BigAutoField(primary_key=True)
    pedido = models.ForeignKey(PedidoCompra, on_delete=models.CASCADE, related_name='parcelas')
    parcela_n = models.PositiveIntegerField()
    vencimento = models.DateField()
    valor = models.DecimalField(max_digits=18, decimal_places=2)
    percentual = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    origem = models.CharField(max_length=6, choices=(('FORMA','FORMA'), ('MANUAL','MANUAL')), default='FORMA')
    status = models.CharField(max_length=8, choices=PARCELA_STATUS, default='PLAN')
    pagar_item_id = models.IntegerField(null=True, blank=True)
    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'compras_pedido_compra_parcela'
        constraints = [models.UniqueConstraint(fields=['pedido','parcela_n'], name='uq_pc_parcela')]
        indexes = [models.Index(fields=['pedido','parcela_n']), models.Index(fields=['status']), models.Index(fields=['vencimento'])]
        ordering = ['pedido_id', 'parcela_n']

    def __str__(self):
        return f'PC {self.pedido_id} - Parcela {self.parcela_n}'
