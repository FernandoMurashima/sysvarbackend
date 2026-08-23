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

PRIORIDADE_COTACAO = (
    ('NORMAL', 'Normal'),
    ('URGENTE', 'Urgente'),
    ('EMERGENCIAL', 'Emergencial'),
)

STATUS_COTACAO = (
    ('EM_ELABORACAO', 'Em elaboração'),
    ('ABERTA', 'Aberta'),
    ('PROPOSTAS_RECEBIDAS', 'Propostas recebidas'),
    ('EM_ANALISE', 'Em análise'),
    ('AGUARDANDO_APROVACAO', 'Aguardando aprovação'),
    ('APROVADA', 'Aprovada'),
    ('REJEITADA', 'Rejeitada'),
    ('CANCELADA', 'Cancelada'),
    ('PEDIDO_GERADO', 'Pedido gerado'),
    ('ENCERRADA', 'Encerrada'),
)

TIPO_COMPRA_COTACAO = (
    ('REVENDA', 'Revenda'),
    ('USO_CONSUMO', 'Uso/Consumo'),
    ('INSUMO', 'Insumo'),
    ('SERVICO', 'Serviço'),
    ('OUTRO', 'Outro'),
)

ORIGEM_ITEM_COTACAO = (
    ('REQUISICAO', 'Requisição'),
    ('AVULSO', 'Avulso'),
)

STATUS_PARTICIPACAO_COTACAO = (
    ('CONVIDADO', 'Convidado'),
    ('PROPOSTA_RECEBIDA', 'Proposta recebida'),
    ('NAO_RESPONDEU', 'Não respondeu'),
    ('RECUSOU', 'Recusou'),
    ('DESCLASSIFICADO', 'Desclassificado'),
)

STATUS_REQUISICAO = (
    ('RASCUNHO', 'Não enviada'),
    ('SOLICITADA', 'Solicitada'),
    ('EM_ANALISE', 'Em análise'),
    ('AGUARDANDO_APROVACAO', 'Aguardando aprovação'),
    ('DEVOLVIDA_CORRECAO', 'Devolvida para correção'),
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


class RequisicaoMaterialCategoria(models.Model):
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, related_name='categorias_material_requisicao', db_index=True)
    nome = models.CharField(max_length=80)
    descricao = models.TextField(blank=True, default='')
    ativo = models.BooleanField(default=True, db_index=True)
    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'compras_requisicao_material_categoria'
        ordering = ['nome']
        constraints = [models.UniqueConstraint(fields=['empresa', 'nome'], name='uq_req_mat_cat_empresa_nome')]
        indexes = [models.Index(fields=['empresa', 'ativo'])]

    def __str__(self):
        return self.nome


class RequisicaoFinalidadeAquisicao(models.Model):
    USO_CONSUMO = 'USO_CONSUMO'
    ALMOXARIFADO = 'ALMOXARIFADO'
    IMOBILIZADO = 'IMOBILIZADO'
    OUTRO = 'OUTRO'
    COMPORTAMENTO_CHOICES = FINALIDADE_ITEM_REQUISICAO

    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, related_name='finalidades_aquisicao', db_index=True)
    nome = models.CharField(max_length=80)
    descricao = models.TextField(blank=True, default='')
    ativo = models.BooleanField(default=True, db_index=True)
    comportamento = models.CharField(max_length=20, choices=COMPORTAMENTO_CHOICES, db_index=True)
    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'compras_requisicao_finalidade_aquisicao'
        ordering = ['nome']
        constraints = [
            models.UniqueConstraint(fields=['empresa', 'nome'], name='uq_req_fin_aq_empresa_nome'),
        ]
        indexes = [models.Index(fields=['empresa', 'ativo']), models.Index(fields=['empresa', 'comportamento'])]

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
    categoria_material = models.ForeignKey(RequisicaoMaterialCategoria, on_delete=models.PROTECT, null=True, blank=True, related_name='itens_requisicao')
    finalidade_aquisicao = models.ForeignKey(RequisicaoFinalidadeAquisicao, on_delete=models.PROTECT, null=True, blank=True, related_name='itens_requisicao')
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


class Cotacao(models.Model):
    id = models.BigAutoField(primary_key=True)
    numero = models.PositiveIntegerField(db_index=True, blank=True)
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, related_name='cotacoes', db_index=True)
    loja = models.ForeignKey(Loja, on_delete=models.PROTECT, related_name='cotacoes', db_index=True)
    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='cotacoes_responsavel')
    data_abertura = models.DateField(default=timezone.localdate, db_index=True)
    data_limite_propostas = models.DateField(null=True, blank=True, db_index=True)
    prioridade = models.CharField(max_length=12, choices=PRIORIDADE_COTACAO, default='NORMAL', db_index=True)
    tipo_compra = models.CharField(max_length=20, choices=TIPO_COMPRA_COTACAO, default='OUTRO', db_index=True)
    observacao = models.TextField(blank=True, default='')
    status = models.CharField(max_length=30, choices=STATUS_COTACAO, default='EM_ELABORACAO', db_index=True)
    proposta_vencedora = models.ForeignKey('compras.CotacaoProposta', on_delete=models.PROTECT, null=True, blank=True, related_name='cotacoes_vencedoras')
    justificativa_vencedor = models.TextField(blank=True, default='')
    aprovado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='cotacoes_aprovadas')
    aprovado_em = models.DateTimeField(null=True, blank=True)
    rejeitado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='cotacoes_rejeitadas')
    rejeitado_em = models.DateTimeField(null=True, blank=True)
    motivo_rejeicao = models.TextField(blank=True, default='')
    cancelado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='cotacoes_canceladas')
    cancelado_em = models.DateTimeField(null=True, blank=True)
    motivo_cancelamento = models.TextField(blank=True, default='')
    snapshot_proposta_aprovada = models.JSONField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'compras_cotacao'
        ordering = ['-data_abertura', '-numero']
        constraints = [models.UniqueConstraint(fields=['empresa', 'numero'], name='uq_cotacao_empresa_numero')]
        indexes = [
            models.Index(fields=['empresa', 'status']),
            models.Index(fields=['empresa', 'loja']),
            models.Index(fields=['responsavel', 'data_abertura']),
        ]

    def save(self, *args, **kwargs):
        if not self.numero and self.empresa_id:
            ultimo = Cotacao.objects.filter(empresa_id=self.empresa_id).order_by('-numero').values_list('numero', flat=True).first()
            self.numero = (ultimo or 0) + 1
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.loja_id and self.empresa_id and self.loja.empresa_id != self.empresa_id:
            from django.core.exceptions import ValidationError
            raise ValidationError({'loja': 'Loja pertence a outra empresa.'})

    def __str__(self):
        return f'Cotação {self.numero}'


class CotacaoRequisicao(models.Model):
    id = models.BigAutoField(primary_key=True)
    cotacao = models.ForeignKey(Cotacao, on_delete=models.CASCADE, related_name='requisicoes_vinculadas')
    requisicao = models.ForeignKey(Requisicao, on_delete=models.PROTECT, related_name='cotacoes_vinculadas')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'compras_cotacao_requisicao'
        constraints = [models.UniqueConstraint(fields=['cotacao', 'requisicao'], name='uq_cotacao_requisicao')]
        indexes = [models.Index(fields=['cotacao']), models.Index(fields=['requisicao'])]

    def clean(self):
        super().clean()
        if self.cotacao_id and self.requisicao_id and self.cotacao.empresa_id != self.requisicao.empresa_id:
            from django.core.exceptions import ValidationError
            raise ValidationError({'requisicao': 'Requisição pertence a outra empresa.'})

    def __str__(self):
        return f'Cotação {self.cotacao_id} - Requisição {self.requisicao_id}'


class CotacaoItem(models.Model):
    id = models.BigAutoField(primary_key=True)
    cotacao = models.ForeignKey(Cotacao, on_delete=models.CASCADE, related_name='itens')
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT, null=True, blank=True, related_name='itens_cotacao')
    descricao = models.CharField(max_length=200, blank=True, default='')
    quantidade_cotar = models.DecimalField(max_digits=14, decimal_places=3, validators=[MinValueValidator(0)])
    unidade = models.ForeignKey('produto.Unidade', on_delete=models.PROTECT, null=True, blank=True, related_name='itens_cotacao')
    especificacao_tecnica = models.TextField(blank=True, default='')
    marca_desejada = models.CharField(max_length=120, blank=True, default='')
    modelo_referencia = models.CharField(max_length=120, blank=True, default='')
    permite_alternativo = models.BooleanField(default=True)
    observacao = models.TextField(blank=True, default='')
    requisicao_item_origem = models.ForeignKey(RequisicaoItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='itens_cotacao')
    origem = models.CharField(max_length=10, choices=ORIGEM_ITEM_COTACAO, default='AVULSO', db_index=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'compras_cotacao_item'
        ordering = ['id']
        indexes = [
            models.Index(fields=['cotacao']),
            models.Index(fields=['produto']),
            models.Index(fields=['origem']),
        ]

    def clean(self):
        super().clean()
        if self.produto_id and self.cotacao_id and self.produto.empresa_id != self.cotacao.empresa_id:
            from django.core.exceptions import ValidationError
            raise ValidationError({'produto': 'Produto pertence a outra empresa.'})
        if self.unidade_id and self.cotacao_id and self.unidade.empresa_id != self.cotacao.empresa_id:
            from django.core.exceptions import ValidationError
            raise ValidationError({'unidade': 'Unidade pertence a outra empresa.'})
        if self.requisicao_item_origem_id and self.cotacao_id and self.requisicao_item_origem.requisicao.empresa_id != self.cotacao.empresa_id:
            from django.core.exceptions import ValidationError
            raise ValidationError({'requisicao_item_origem': 'Item de requisição pertence a outra empresa.'})

    def __str__(self):
        return self.descricao or f'Item cotação {self.id}'


class CotacaoFornecedor(models.Model):
    id = models.BigAutoField(primary_key=True)
    cotacao = models.ForeignKey(Cotacao, on_delete=models.CASCADE, related_name='fornecedores_participantes')
    fornecedor = models.ForeignKey(Fornecedor, on_delete=models.PROTECT, related_name='cotacoes_participantes')
    status_participacao = models.CharField(max_length=20, choices=STATUS_PARTICIPACAO_COTACAO, default='CONVIDADO', db_index=True)
    motivo_desclassificacao = models.TextField(blank=True, default='')
    observacao = models.TextField(blank=True, default='')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'compras_cotacao_fornecedor'
        constraints = [models.UniqueConstraint(fields=['cotacao', 'fornecedor'], name='uq_cotacao_fornecedor')]
        indexes = [
            models.Index(fields=['cotacao', 'status_participacao']),
            models.Index(fields=['fornecedor']),
        ]

    def clean(self):
        super().clean()
        if self.cotacao_id and self.fornecedor_id and self.cotacao.empresa_id != self.fornecedor.empresa_id:
            from django.core.exceptions import ValidationError
            raise ValidationError({'fornecedor': 'Fornecedor pertence a outra empresa.'})
        if self.fornecedor_id and not self.fornecedor.ativo:
            from django.core.exceptions import ValidationError
            raise ValidationError({'fornecedor': 'Fornecedor inativo não pode participar da cotação.'})
        if self.status_participacao == 'DESCLASSIFICADO' and not (self.motivo_desclassificacao or '').strip():
            from django.core.exceptions import ValidationError
            raise ValidationError({'motivo_desclassificacao': 'Informe o motivo da desclassificação.'})

    def __str__(self):
        return f'Cotação {self.cotacao_id} - {self.fornecedor_id}'


class CotacaoProposta(models.Model):
    id = models.BigAutoField(primary_key=True)
    cotacao = models.ForeignKey(Cotacao, on_delete=models.CASCADE, related_name='propostas')
    cotacao_fornecedor = models.ForeignKey(CotacaoFornecedor, on_delete=models.PROTECT, related_name='propostas')
    data_proposta = models.DateField(default=timezone.localdate)
    validade_proposta = models.DateField(null=True, blank=True)
    prazo_entrega = models.CharField(max_length=120, blank=True, default='')
    prazo_entrega_dias = models.PositiveIntegerField(null=True, blank=True)
    forma_pagamento = models.CharField(max_length=30, null=True, blank=True)
    condicao_pagamento = models.CharField(max_length=160, blank=True, default='')
    prazo_pagamento = models.ForeignKey(
        'financeiro.PrazoPagamento',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='cotacoes_propostas',
    )
    frete = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    outras_despesas = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    desconto_geral = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_itens = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_proposta = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    observacao = models.TextField(blank=True, default='')
    anexo = models.FileField(upload_to='cotacoes/propostas/', null=True, blank=True)
    ativa = models.BooleanField(default=True, db_index=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'compras_cotacao_proposta'
        indexes = [
            models.Index(fields=['cotacao', 'ativa']),
            models.Index(fields=['cotacao_fornecedor']),
        ]

    def recomputar_totais(self):
        itens = list(self.itens.all())
        self.total_itens = sum((i.total_item or 0) for i in itens)
        self.total_proposta = (self.total_itens or 0) - (self.desconto_geral or 0) + (self.frete or 0) + (self.outras_despesas or 0)

    def __str__(self):
        return f'Proposta {self.id} - Cotação {self.cotacao_id}'


class CotacaoPropostaItem(models.Model):
    id = models.BigAutoField(primary_key=True)
    proposta = models.ForeignKey(CotacaoProposta, on_delete=models.CASCADE, related_name='itens')
    cotacao_item = models.ForeignKey(CotacaoItem, on_delete=models.PROTECT, related_name='propostas_itens')
    quantidade_ofertada = models.DecimalField(max_digits=14, decimal_places=3)
    preco_unitario = models.DecimalField(max_digits=14, decimal_places=4)
    desconto_item = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    marca = models.CharField(max_length=120, blank=True, default='')
    modelo_referencia = models.CharField(max_length=120, blank=True, default='')
    garantia = models.CharField(max_length=120, blank=True, default='')
    prazo_entrega_item = models.CharField(max_length=120, blank=True, default='')
    observacao = models.TextField(blank=True, default='')
    total_item = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    class Meta:
        db_table = 'compras_cotacao_proposta_item'
        constraints = [models.UniqueConstraint(fields=['proposta', 'cotacao_item'], name='uq_cotacao_proposta_item')]
        indexes = [
            models.Index(fields=['proposta']),
            models.Index(fields=['cotacao_item']),
        ]

    def recalcular_total(self):
        self.total_item = (self.quantidade_ofertada or 0) * (self.preco_unitario or 0) - (self.desconto_item or 0)

    def save(self, *args, **kwargs):
        self.recalcular_total()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'Item proposta {self.id}'


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
    outras_despesas = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_pedido = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    observacoes = models.TextField(null=True, blank=True)
    cotacao_origem = models.OneToOneField(Cotacao, on_delete=models.PROTECT, null=True, blank=True, related_name='pedido_compra_gerado')
    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'compras_pedido_compra'
        ordering = ['-emissao', '-id']

    def __str__(self):
        return f'PC {self.id} - {self.get_tipo_display()} - {self.get_status_display()}'

    def recomputa_totais(self):
        itens = self.itens.all()
        self.total_itens = sum([i.total_item or 0 for i in itens])
        self.total_pedido = (self.total_itens - (self.total_desconto or 0)) + (self.frete or 0) + (self.outras_despesas or 0)
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
