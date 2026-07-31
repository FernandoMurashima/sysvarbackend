from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class PerfilDistribuicao(models.Model):
    TIPO_MANUAL = "MANUAL"
    TIPO_PERCENTUAL = "PERCENTUAL"
    TIPO_FIXA = "FIXA"
    TIPO_METRICA = "METRICA"
    TIPO_CHOICES = [
        (TIPO_MANUAL, "Manual"),
        (TIPO_PERCENTUAL, "Percentual"),
        (TIPO_FIXA, "Quantidade fixa"),
        (TIPO_METRICA, "Métrica"),
    ]

    empresa = models.ForeignKey("cadastros.Empresa", on_delete=models.PROTECT, related_name="perfis_distribuicao", db_index=True)
    codigo = models.CharField(max_length=20)
    descricao = models.CharField(max_length=120)
    tipo = models.CharField(max_length=12, choices=TIPO_CHOICES, default=TIPO_PERCENTUAL, db_index=True)
    fator_preco = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal("0.2000"), validators=[MinValueValidator(0)])
    ativo = models.BooleanField(default=True, db_index=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="perfis_distribuicao_criados")
    data_cadastro = models.DateTimeField(default=timezone.now)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["empresa", "codigo"], name="uq_empresa_perfil_distribuicao_codigo")]
        indexes = [models.Index(fields=["empresa", "ativo"]), models.Index(fields=["tipo"])]
        ordering = ["codigo"]

    def __str__(self):
        return f"{self.codigo} - {self.descricao}"


class PerfilDistribuicaoItem(models.Model):
    perfil = models.ForeignKey(PerfilDistribuicao, on_delete=models.CASCADE, related_name="itens")
    loja = models.ForeignKey("cadastros.Loja", on_delete=models.PROTECT, related_name="perfis_distribuicao")
    percentual = models.DecimalField(max_digits=7, decimal_places=4, default=0, validators=[MinValueValidator(0)])
    quantidade_fixa = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=[MinValueValidator(0)])
    prioridade = models.PositiveIntegerField(default=0)
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["perfil", "loja"], name="uq_perfil_distribuicao_loja")]
        indexes = [models.Index(fields=["perfil", "ativo"]), models.Index(fields=["loja"])]
        ordering = ["prioridade", "loja_id"]

    def __str__(self):
        return f"{self.perfil_id} - {self.loja_id}"


class Distribuicao(models.Model):
    STATUS_RASCUNHO = "RASC"
    STATUS_CALCULADA = "CALC"
    STATUS_CONFIRMADA = "CONF"
    STATUS_PEDIDOS_GERADOS = "PED"
    STATUS_EM_FATURAMENTO = "FATUR"
    STATUS_FATURADA = "NF"
    STATUS_EM_TRANSITO = "TRANS"
    STATUS_RECEBIDA_PARCIAL = "PARC"
    STATUS_RECEBIDA = "RECB"
    STATUS_CANCELADA = "CANC"
    STATUS_CHOICES = [
        (STATUS_RASCUNHO, "Rascunho"),
        (STATUS_CALCULADA, "Calculada"),
        (STATUS_CONFIRMADA, "Confirmada"),
        (STATUS_PEDIDOS_GERADOS, "Pedidos gerados"),
        (STATUS_EM_FATURAMENTO, "Em faturamento"),
        (STATUS_FATURADA, "Faturada"),
        (STATUS_EM_TRANSITO, "Em trânsito"),
        (STATUS_RECEBIDA_PARCIAL, "Parcialmente recebida"),
        (STATUS_RECEBIDA, "Recebida"),
        (STATUS_CANCELADA, "Cancelada"),
    ]

    ORIGEM_MANUAL = "MANUAL"
    ORIGEM_PRODUCAO = "PRODUCAO"
    ORIGEM_COMPRA = "COMPRA"
    ORIGEM_CHOICES = [
        (ORIGEM_MANUAL, "Manual"),
        (ORIGEM_PRODUCAO, "Produção"),
        (ORIGEM_COMPRA, "Compra"),
    ]

    empresa = models.ForeignKey("cadastros.Empresa", on_delete=models.PROTECT, related_name="distribuicoes", db_index=True)
    numero = models.CharField(max_length=24, db_index=True)
    unidade_origem = models.ForeignKey("cadastros.Loja", on_delete=models.PROTECT, related_name="distribuicoes_origem")
    data = models.DateField(default=timezone.localdate, db_index=True)
    tipo = models.CharField(max_length=12, choices=PerfilDistribuicao.TIPO_CHOICES, default=PerfilDistribuicao.TIPO_MANUAL, db_index=True)
    perfil = models.ForeignKey(PerfilDistribuicao, on_delete=models.PROTECT, null=True, blank=True, related_name="distribuicoes")
    fator_preco = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal("0.2000"), validators=[MinValueValidator(0)])
    origem_operacao = models.CharField(max_length=12, choices=ORIGEM_CHOICES, default=ORIGEM_MANUAL)
    origem_id = models.PositiveBigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default=STATUS_RASCUNHO, db_index=True)
    observacao = models.TextField(null=True, blank=True)
    quantidade_total = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    valor_total_custo = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    valor_total_venda = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="distribuicoes_criadas")
    confirmado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="distribuicoes_confirmadas")
    data_cadastro = models.DateTimeField(default=timezone.now)
    atualizado_em = models.DateTimeField(auto_now=True)
    data_confirmacao = models.DateTimeField(null=True, blank=True)
    data_cancelamento = models.DateTimeField(null=True, blank=True)
    motivo_cancelamento = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["empresa", "numero"], name="uq_empresa_distribuicao_numero")]
        indexes = [
            models.Index(fields=["empresa", "status"]),
            models.Index(fields=["unidade_origem", "data"]),
            models.Index(fields=["data"]),
        ]
        ordering = ["-data", "-id"]

    def __str__(self):
        return self.numero

    def recomputar_totais(self):
        destinos = self.destinos.filter(status__in=[DistribuicaoDestino.STATUS_RASCUNHO, DistribuicaoDestino.STATUS_CONFIRMADO, DistribuicaoDestino.STATUS_PEDIDO])
        self.quantidade_total = sum((d.quantidade_confirmada or d.quantidade_ajustada or d.quantidade_sugerida or Decimal("0")) for d in destinos)
        self.valor_total_custo = sum(((d.quantidade_confirmada or d.quantidade_ajustada or d.quantidade_sugerida or Decimal("0")) * (d.item.custo_unitario or Decimal("0"))) for d in destinos)
        self.valor_total_venda = self.valor_total_custo * (Decimal("1") + Decimal(self.fator_preco or 0))


class DistribuicaoItem(models.Model):
    distribuicao = models.ForeignKey(Distribuicao, on_delete=models.CASCADE, related_name="itens")
    produto = models.ForeignKey("produto.Produto", on_delete=models.PROTECT, related_name="itens_distribuicao")
    sku = models.ForeignKey("produto.ProdutoDetalhe", on_delete=models.PROTECT, related_name="itens_distribuicao")
    referencia = models.CharField(max_length=30, db_index=True)
    descricao = models.CharField(max_length=140)
    cor_descricao = models.CharField(max_length=80, null=True, blank=True)
    tamanho_descricao = models.CharField(max_length=40, null=True, blank=True)
    ean13 = models.CharField(max_length=13, db_index=True)
    estoque_fisico = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    estoque_reservado = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    estoque_disponivel = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantidade_selecionada = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=[MinValueValidator(0)])
    custo_unitario = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    custo_total = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    bloqueado_recalculo = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["distribuicao", "sku"], name="uq_distribuicao_sku")]
        indexes = [models.Index(fields=["distribuicao", "referencia"]), models.Index(fields=["sku"])]
        ordering = ["referencia", "cor_descricao", "tamanho_descricao"]

    def __str__(self):
        return f"{self.distribuicao_id} - {self.ean13}"


class DistribuicaoDestino(models.Model):
    STATUS_RASCUNHO = "RASC"
    STATUS_CONFIRMADO = "CONF"
    STATUS_PEDIDO = "PED"
    STATUS_CANCELADO = "CANC"
    STATUS_CHOICES = [
        (STATUS_RASCUNHO, "Rascunho"),
        (STATUS_CONFIRMADO, "Confirmado"),
        (STATUS_PEDIDO, "Pedido gerado"),
        (STATUS_CANCELADO, "Cancelado"),
    ]

    distribuicao = models.ForeignKey(Distribuicao, on_delete=models.CASCADE, related_name="destinos")
    item = models.ForeignKey(DistribuicaoItem, on_delete=models.CASCADE, related_name="destinos")
    loja_destino = models.ForeignKey("cadastros.Loja", on_delete=models.PROTECT, related_name="distribuicoes_destino")
    quantidade_sugerida = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=[MinValueValidator(0)])
    quantidade_ajustada = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=[MinValueValidator(0)])
    quantidade_confirmada = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=[MinValueValidator(0)])
    percentual = models.DecimalField(max_digits=7, decimal_places=4, default=0, validators=[MinValueValidator(0)])
    prioridade = models.PositiveIntegerField(default=0)
    bloqueado_recalculo = models.BooleanField(default=False)
    pedido = models.ForeignKey("distribuicao.PedidoVendaDistribuicao", on_delete=models.SET_NULL, null=True, blank=True, related_name="destinos")
    pedido_item = models.ForeignKey("distribuicao.PedidoVendaDistribuicaoItem", on_delete=models.SET_NULL, null=True, blank=True, related_name="destinos_distribuicao")
    status = models.CharField(max_length=4, choices=STATUS_CHOICES, default=STATUS_RASCUNHO, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["item", "loja_destino"], name="uq_distribuicao_item_loja")]
        indexes = [models.Index(fields=["distribuicao", "loja_destino"]), models.Index(fields=["status"])]
        ordering = ["loja_destino_id", "item_id"]

    def quantidade_operacional(self):
        return self.quantidade_confirmada or self.quantidade_ajustada or self.quantidade_sugerida or Decimal("0")


class PedidoVendaDistribuicao(models.Model):
    STATUS_ABERTO = "AB"
    STATUS_AGUARDANDO_FATURAMENTO = "AGF"
    STATUS_FATURADO = "FAT"
    STATUS_CANCELADO = "CANC"
    STATUS_CHOICES = [
        (STATUS_ABERTO, "Aberto"),
        (STATUS_AGUARDANDO_FATURAMENTO, "Aguardando faturamento"),
        (STATUS_FATURADO, "Faturado"),
        (STATUS_CANCELADO, "Cancelado"),
    ]

    empresa = models.ForeignKey("cadastros.Empresa", on_delete=models.PROTECT, related_name="pedidos_venda_distribuicao", db_index=True)
    distribuicao = models.ForeignKey(Distribuicao, on_delete=models.PROTECT, related_name="pedidos_venda")
    numero = models.CharField(max_length=24, db_index=True)
    unidade_origem = models.ForeignKey("cadastros.Loja", on_delete=models.PROTECT, related_name="pedidos_venda_distribuicao_origem")
    loja_destino = models.ForeignKey("cadastros.Loja", on_delete=models.PROTECT, related_name="pedidos_venda_distribuicao_destino")
    data_pedido = models.DateField(default=timezone.localdate, db_index=True)
    status = models.CharField(max_length=4, choices=STATUS_CHOICES, default=STATUS_AGUARDANDO_FATURAMENTO, db_index=True)
    quantidade_total = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    valor_total_custo = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    valor_total_venda = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    faturamento_status = models.CharField(max_length=30, default="AGUARDANDO_FATURAMENTO", db_index=True)
    nfe_numero = models.CharField(max_length=20, null=True, blank=True)
    nfe_chave = models.CharField(max_length=44, null=True, blank=True)
    nfe_status = models.CharField(max_length=30, null=True, blank=True)
    nfe_data = models.DateTimeField(null=True, blank=True)
    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["empresa", "numero"], name="uq_empresa_pedido_venda_distribuicao_numero"),
            models.UniqueConstraint(fields=["distribuicao", "loja_destino"], name="uq_distribuicao_pedido_loja"),
        ]
        indexes = [models.Index(fields=["empresa", "status"]), models.Index(fields=["loja_destino", "data_pedido"])]
        ordering = ["-data_pedido", "-id"]

    def __str__(self):
        return self.numero


class PedidoVendaDistribuicaoItem(models.Model):
    pedido = models.ForeignKey(PedidoVendaDistribuicao, on_delete=models.CASCADE, related_name="itens")
    distribuicao_destino = models.ForeignKey(DistribuicaoDestino, on_delete=models.SET_NULL, null=True, blank=True, related_name="pedido_itens")
    produto = models.ForeignKey("produto.Produto", on_delete=models.PROTECT)
    sku = models.ForeignKey("produto.ProdutoDetalhe", on_delete=models.PROTECT)
    referencia = models.CharField(max_length=30, db_index=True)
    descricao = models.CharField(max_length=140)
    cor_descricao = models.CharField(max_length=80, null=True, blank=True)
    tamanho_descricao = models.CharField(max_length=40, null=True, blank=True)
    ean13 = models.CharField(max_length=13, db_index=True)
    quantidade = models.DecimalField(max_digits=14, decimal_places=3, validators=[MinValueValidator(0)])
    custo_unitario = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    preco_unitario = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    total_custo = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_item = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    class Meta:
        indexes = [models.Index(fields=["pedido", "referencia"]), models.Index(fields=["sku"])]
        ordering = ["id"]


class MercadoriaTransito(models.Model):
    STATUS_AGUARDANDO_EXPEDICAO = "AG_EXP"
    STATUS_EM_TRANSITO = "TRANS"
    STATUS_RECEBIDA = "RECB"
    STATUS_DIVERGENTE = "DIV"
    STATUS_CHOICES = [
        (STATUS_AGUARDANDO_EXPEDICAO, "Aguardando expedição"),
        (STATUS_EM_TRANSITO, "Em trânsito"),
        (STATUS_RECEBIDA, "Recebida"),
        (STATUS_DIVERGENTE, "Divergente"),
    ]

    pedido = models.ForeignKey(PedidoVendaDistribuicao, on_delete=models.PROTECT, related_name="transitos")
    pedido_item = models.ForeignKey(PedidoVendaDistribuicaoItem, on_delete=models.PROTECT, related_name="transitos")
    distribuicao_destino = models.ForeignKey(DistribuicaoDestino, on_delete=models.PROTECT, related_name="transitos")
    unidade_origem = models.ForeignKey("cadastros.Loja", on_delete=models.PROTECT, related_name="mercadorias_transito_origem")
    loja_destino = models.ForeignKey("cadastros.Loja", on_delete=models.PROTECT, related_name="mercadorias_transito_destino")
    sku = models.ForeignKey("produto.ProdutoDetalhe", on_delete=models.PROTECT)
    ean13 = models.CharField(max_length=13, db_index=True)
    quantidade_enviada = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantidade_recebida = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantidade_divergente = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    data_envio = models.DateTimeField(null=True, blank=True)
    data_recebimento = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default=STATUS_AGUARDANDO_EXPEDICAO, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["pedido_item"], name="uq_transito_pedido_item_distribuicao")]
        indexes = [models.Index(fields=["loja_destino", "status"]), models.Index(fields=["ean13"])]
        ordering = ["-id"]
