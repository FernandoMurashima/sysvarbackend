from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator
from django.conf import settings
from cadastros.models import Loja, Fornecedor
from produto.models import Produto, Cor, Pack, PackItem

STATUS_PC = (
    ('AB', 'Aberto'),
    ('AP', 'Aprovado'),
    ('AT', 'Atendido'),
    ('CA', 'Cancelado'),
)

TIPO_PC = (
    ('', 'Não definido'),
    ('1', 'Revenda'),
    ('2', 'Uso/Consumo'),
    ('4', 'Insumo'),
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

PRIORIDADE_REQUISICAO = (
    ('NORMAL', 'Normal'),
    ('URGENTE', 'Urgente'),
    ('EMERGENCIAL', 'Emergencial'),
)

STATUS_REQUISICAO = (
    ('RASCUNHO', 'Rascunho'),
    ('SOLICITADA', 'Solicitada'),
    ('EM_ANALISE', 'Em análise'),
    ('AGUARDANDO_APROVACAO', 'Aguardando aprovação'),
    ('APROVADA', 'Aprovada'),
    ('EM_ATENDIMENTO', 'Em atendimento'),
    ('ATENDIDA_PARCIALMENTE', 'Atendida parcialmente'),
    ('EM_PROCESSO_COMPRA', 'Em processo de compra'),
    ('EM_PROCESSO_CONTRATACAO', 'Em processo de contratação'),
    ('CONCLUIDA', 'Concluída'),
    ('REJEITADA', 'Rejeitada'),
    ('CANCELADA', 'Cancelada'),
)

TIPO_ITEM_REQUISICAO = (
    ('MATERIAL', 'Material'),
    ('SERVICO', 'Serviço'),
)

ORIGEM_ITEM_REQUISICAO = (
    ('PRODUTO', 'Produto cadastrado'),
    ('LIVRE', 'Item não cadastrado'),
    ('SERVICO', 'Serviço'),
)

STATUS_ITEM_REQUISICAO = (
    ('PENDENTE', 'Pendente'),
    ('APROVADO', 'Aprovado'),
    ('REJEITADO', 'Rejeitado'),
    ('EM_SEPARACAO', 'Em separação'),
    ('ATENDIDO', 'Atendido'),
    ('ATENDIDO_PARCIALMENTE', 'Atendido parcialmente'),
    ('AGUARDANDO_COTACAO', 'Aguardando cotação'),
    ('EM_COTACAO', 'Em cotação'),
    ('PEDIDO_GERADO', 'Pedido gerado'),
    ('AGUARDANDO_RECEBIMENTO', 'Aguardando recebimento'),
    ('RECEBIDO', 'Recebido'),
    ('SERVICO_CONTRATACAO', 'Serviço em contratação'),
    ('SERVICO_CONCLUIDO', 'Serviço concluído'),
    ('CANCELADO', 'Cancelado'),
)

TIPO_SERVICO_REQUISICAO = (
    ('PREVENTIVA', 'Preventiva'),
    ('CORRETIVA', 'Corretiva'),
    ('INSTALACAO', 'Instalação'),
    ('REVISAO', 'Revisão'),
    ('REPARO', 'Reparo'),
    ('OUTRO', 'Outro'),
)

FINALIDADE_ITEM_REQUISICAO = (
    ('USO_CONSUMO', 'Uso e Consumo'),
    ('ALMOXARIFADO', 'Estoque/Almoxarifado'),
    ('IMOBILIZADO', 'Imobilizado'),
    ('OUTRO', 'Outro'),
)

ACAO_HISTORICO_REQUISICAO = (
    ('CRIACAO', 'Criação'),
    ('EDICAO', 'Edição'),
    ('ENVIO', 'Envio'),
    ('APROVACAO', 'Aprovação'),
    ('REJEICAO', 'Rejeição'),
    ('DEVOLUCAO', 'Devolução'),
    ('ATENDIMENTO', 'Atendimento'),
    ('CANCELAMENTO', 'Cancelamento'),
    ('STATUS', 'Mudança de status'),
)


class RequisicaoServicoCategoria(models.Model):
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, related_name='categorias_servico_requisicao', db_index=True)
    nome = models.CharField(max_length=80)
    ativo = models.BooleanField(default=True, db_index=True)
    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'compras_requisicao_servico_categoria'
        ordering = ['nome']
        constraints = [models.UniqueConstraint(fields=['empresa', 'nome'], name='uq_req_serv_cat_empresa_nome')]
        indexes = [models.Index(fields=['empresa', 'ativo'])]

    def __str__(self):
        return self.nome


class RequisicaoSetor(models.Model):
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, related_name='setores_requisicao', db_index=True)
    nome = models.CharField(max_length=80)
    descricao = models.TextField(blank=True, default='')
    ativo = models.BooleanField(default=True, db_index=True)
    pode_fazer_requisicao = models.BooleanField(default=True)
    recebe_requisicoes = models.BooleanField(default=True)
    controla_estoque_uso_consumo = models.BooleanField(default=False)
    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'compras_requisicao_setor'
        ordering = ['nome']
        constraints = [models.UniqueConstraint(fields=['empresa', 'nome'], name='uq_req_setor_empresa_nome')]
        indexes = [models.Index(fields=['empresa', 'ativo'])]

    def __str__(self):
        return self.nome


class Requisicao(models.Model):
    id = models.BigAutoField(primary_key=True)
    numero = models.PositiveIntegerField(db_index=True)
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, related_name='requisicoes_compra', db_index=True)
    loja = models.ForeignKey(Loja, on_delete=models.PROTECT, related_name='requisicoes_compra', db_index=True)
    setor = models.ForeignKey(RequisicaoSetor, on_delete=models.PROTECT, related_name='requisicoes', db_index=True)
    requisitante = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='requisicoes_solicitadas')
    data_requisicao = models.DateField(default=timezone.localdate, db_index=True)
    data_necessaria = models.DateField(null=True, blank=True, db_index=True)
    prioridade = models.CharField(max_length=12, choices=PRIORIDADE_REQUISICAO, default='NORMAL', db_index=True)
    justificativa = models.TextField(blank=True, default='')
    observacoes = models.TextField(blank=True, default='')
    status = models.CharField(max_length=30, choices=STATUS_REQUISICAO, default='RASCUNHO', db_index=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='requisicoes_criadas')
    aprovado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='requisicoes_aprovadas')
    aprovado_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'compras_requisicao'
        ordering = ['-data_requisicao', '-numero']
        constraints = [models.UniqueConstraint(fields=['empresa', 'numero'], name='uq_requisicao_empresa_numero')]
        indexes = [
            models.Index(fields=['empresa', 'status']),
            models.Index(fields=['empresa', 'loja']),
            models.Index(fields=['requisitante', 'data_requisicao']),
        ]

    def __str__(self):
        return f'Requisição {self.numero}'


class RequisicaoItem(models.Model):
    id = models.BigAutoField(primary_key=True)
    requisicao = models.ForeignKey(Requisicao, on_delete=models.CASCADE, related_name='itens')
    tipo = models.CharField(max_length=10, choices=TIPO_ITEM_REQUISICAO, db_index=True)
    origem = models.CharField(max_length=10, choices=ORIGEM_ITEM_REQUISICAO, db_index=True)
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT, null=True, blank=True, related_name='itens_requisicao')
    descricao = models.CharField(max_length=200, blank=True, default='')
    categoria = models.CharField(max_length=80, blank=True, default='')
    finalidade = models.CharField(max_length=20, choices=FINALIDADE_ITEM_REQUISICAO, blank=True, default='')
    unidade = models.ForeignKey('produto.Unidade', on_delete=models.PROTECT, null=True, blank=True, related_name='itens_requisicao')
    especificacao_tecnica = models.TextField(blank=True, default='')
    titulo_servico = models.CharField(max_length=160, blank=True, default='')
    descricao_servico = models.TextField(blank=True, default='')
    categoria_servico = models.ForeignKey(RequisicaoServicoCategoria, on_delete=models.PROTECT, null=True, blank=True, related_name='itens_requisicao')
    tipo_servico = models.CharField(max_length=20, choices=TIPO_SERVICO_REQUISICAO, blank=True, default='')
    qtd_solicitada = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=[MinValueValidator(0)])
    qtd_atendida = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=[MinValueValidator(0)])
    qtd_pendente = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=[MinValueValidator(0)])
    status = models.CharField(max_length=30, choices=STATUS_ITEM_REQUISICAO, default='PENDENTE', db_index=True)
    observacoes = models.TextField(blank=True, default='')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'compras_requisicao_item'
        ordering = ['id']
        indexes = [
            models.Index(fields=['requisicao', 'status']),
            models.Index(fields=['produto']),
            models.Index(fields=['tipo', 'origem']),
        ]

    def __str__(self):
        return self.descricao or self.titulo_servico or f'Item {self.id}'


class RequisicaoHistorico(models.Model):
    id = models.BigAutoField(primary_key=True)
    requisicao = models.ForeignKey(Requisicao, on_delete=models.CASCADE, related_name='historico')
    item = models.ForeignKey(RequisicaoItem, on_delete=models.CASCADE, null=True, blank=True, related_name='historico')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='historicos_requisicao')
    data_hora = models.DateTimeField(default=timezone.now, db_index=True)
    acao = models.CharField(max_length=20, choices=ACAO_HISTORICO_REQUISICAO, db_index=True)
    status_anterior = models.CharField(max_length=30, blank=True, default='')
    status_novo = models.CharField(max_length=30, blank=True, default='')
    valor_anterior = models.JSONField(null=True, blank=True)
    valor_novo = models.JSONField(null=True, blank=True)
    observacao = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'compras_requisicao_historico'
        ordering = ['-data_hora', '-id']
        indexes = [
            models.Index(fields=['requisicao', '-data_hora']),
            models.Index(fields=['acao', 'data_hora']),
        ]

class PedidoCompra(models.Model):
    id = models.BigAutoField(primary_key=True)

    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, null=True, blank=True, related_name='pedidos_compra', db_index=True)
    tipo = models.CharField(max_length=1, choices=TIPO_PC, blank=True, default='')
    loja = models.ForeignKey(Loja, on_delete=models.PROTECT)
    fornecedor = models.ForeignKey(Fornecedor, on_delete=models.PROTECT)

    emissao = models.DateField(default=timezone.localdate)
    previsao_entrega = models.DateField(null=True, blank=True)

    # snapshot textual do código da forma (ex.: 'AV', '30/60')
    forma_pagamento = models.CharField(max_length=30, null=True, blank=True)
    prazo_pagamento = models.ForeignKey(
        'financeiro.PrazoPagamento',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='pedidos_compra',
    )

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
        if self.total_pedido < 0:
            raise ValueError('Total do pedido não pode ser negativo.')


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
