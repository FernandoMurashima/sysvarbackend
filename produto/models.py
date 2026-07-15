from django.db import models, transaction
from django.utils import timezone
from django.core.validators import RegexValidator, MinValueValidator, MaxValueValidator
from decimal import Decimal

# ------------------------------------------------------------
# Helper: dígito verificador do EAN-13
# ------------------------------------------------------------
def ean13_check_digit(base12: str) -> str:
    s = 0
    for i, ch in enumerate(base12):
        n = int(ch)
        s += n if i % 2 == 0 else 3 * n
    return str((10 - (s % 10)) % 10)


# ===========================
# Configuração EAN (fixo 789 + empresa 4 dígitos) — com 'ativo'
# ===========================
class ConfigEan(models.Model):
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, null=True, blank=True, related_name='configs_ean', db_index=True)
    country_prefix = models.CharField(
        max_length=3,
        default='789',
        validators=[RegexValidator(r'^\d{3}$', 'Use exatamente 3 dígitos.')],
        help_text='Prefixo do país (fixo 789).'
    )
    company_prefix = models.CharField(
        max_length=4,
        validators=[RegexValidator(r'^\d{4}$', 'Use exatamente 4 dígitos.')],
        help_text='Prefixo da empresa (4 dígitos).'
    )
    # Sequência de item (00001..99999) PARA ESTE PREFIXO
    next_itemref = models.PositiveIntegerField(
        default=1,
        help_text='Sequência 00001..99999 do item para este prefixo'
    )
    ativo = models.BooleanField(default=True, help_text='Se marcado, este prefixo pode gerar novos SKUs')
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'config_ean'
        verbose_name = 'Configuração EAN'
        verbose_name_plural = 'Configuração EAN'
        constraints = [
            models.UniqueConstraint(fields=['empresa', 'country_prefix', 'company_prefix'], name='uq_empresa_country_company_prefix'),
        ]
        indexes = [
            models.Index(fields=['ativo']),
            models.Index(fields=['company_prefix']),
        ]

    def __str__(self):
        return f'{self.country_prefix}-{self.company_prefix} (ativo={self.ativo}, next={self.next_itemref})'


# ===========================
# Tabelas auxiliares (mestre)
# ===========================
class Ncm(models.Model):
    CATEGORIA_VESTUARIO = "VESTUARIO"
    CATEGORIA_TECIDO = "TECIDO"
    CATEGORIA_AVIAMENTO = "AVIAMENTO"
    CATEGORIA_EMBALAGEM = "EMBALAGEM"
    CATEGORIA_OUTROS = "OUTROS"
    CATEGORIA_CHOICES = [
        (CATEGORIA_VESTUARIO, "Vestuário"),
        (CATEGORIA_TECIDO, "Tecidos"),
        (CATEGORIA_AVIAMENTO, "Aviamentos"),
        (CATEGORIA_EMBALAGEM, "Embalagens"),
        (CATEGORIA_OUTROS, "Outros"),
    ]

    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, null=True, blank=True, related_name='ncms', db_index=True)
    # Guardaremos também no Produto como CHAR(10) no formato ####.##.##
    ncm = models.CharField(
        max_length=10, null=True, blank=True,
        validators=[RegexValidator(r'^\d{4}\.\d{2}\.\d{2}$', 'NCM deve estar no formato ####.##.##')]
    )
    descricao = models.CharField(max_length=1000)
    categoria = models.CharField(max_length=20, choices=CATEGORIA_CHOICES, default=CATEGORIA_OUTROS, db_index=True)
    aliquota = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    campo1 = models.CharField(max_length=25, blank=True, null=True)
    ativo = models.BooleanField(default=True, db_index=True)

    def __str__(self):
        return f'{self.ncm} - {self.descricao[:60]}'


class Grade(models.Model):  # HAD
    Idgrade = models.BigAutoField(primary_key=True)
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, null=True, blank=True, related_name='grades_produto', db_index=True)
    Descricao = models.CharField(max_length=100)
    Status = models.CharField(max_length=10, null=True, blank=True)
    data_cadastro = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.Descricao


class Tamanho(models.Model):
    Idtamanho = models.BigAutoField(primary_key=True)
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, null=True, blank=True, related_name='tamanhos_produto', db_index=True)
    idgrade = models.ForeignKey(Grade, on_delete=models.CASCADE)
    Tamanho = models.CharField(max_length=10)  # ex.: PP, P, M, G, 38, 40...
    Descricao = models.CharField(max_length=100, default="Tamanho")
    Status = models.CharField(max_length=10, null=True, blank=True)
    data_cadastro = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.Tamanho or self.Descricao


class Cor(models.Model):
    Idcor = models.BigAutoField(primary_key=True)
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, null=True, blank=True, related_name='cores_produto', db_index=True)
    Descricao = models.CharField(max_length=100)
    Codigo = models.CharField(max_length=12, null=True, blank=True)  # ex.: AZ, PR, BR, etc.
    Cor = models.CharField(max_length=30)  # nome completo
    Status = models.CharField(max_length=10, null=True, blank=True)
    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=['Codigo']),
        ]

    def __str__(self):
        return self.Descricao


class Material(models.Model):
    Idmaterial = models.BigAutoField(primary_key=True)
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, null=True, blank=True, related_name='materiais_produto', db_index=True)
    Descricao = models.CharField(max_length=100)
    Codigo = models.CharField(max_length=10, null=True, blank=True)
    Status = models.CharField(max_length=10, null=True, blank=True)
    data_cadastro = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.Descricao


class Colecao(models.Model):
    ESTACOES_CHOICES = (
        ('01', 'Verão'),
        ('02', 'Outono'),
        ('03', 'Inverno'),
        ('04', 'Primavera'),
    )
    STATUS_CHOICES = (
        ('CR', 'Criação'),
        ('PD', 'Produção'),
        ('AT', 'Ativa'),
        ('EN', 'Encerrada'),
        ('AR', 'Arquivada'),
    )

    Idcolecao = models.BigAutoField(primary_key=True)
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, null=True, blank=True, related_name='colecoes', db_index=True)
    Descricao = models.CharField(max_length=100)
    Codigo = models.CharField(max_length=2, null=True, blank=True, help_text='Dois ultimos digitos do ano, ex.: 2026 = 26')
    Estacao = models.CharField(max_length=2, null=True, blank=True, choices=ESTACOES_CHOICES)
    Status = models.CharField(max_length=10, null=True, blank=True, choices=STATUS_CHOICES)
    Contador = models.IntegerField(null=True, blank=True, default=0)
    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['empresa', 'Codigo', 'Estacao'], name='uq_empresa_colecao_codigo_estacao')
        ]

    def __str__(self):
        return self.Descricao


class Unidade(models.Model):
    Idunidade = models.BigAutoField(primary_key=True)
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, null=True, blank=True, related_name='unidades_produto', db_index=True)
    Descricao = models.CharField(max_length=100)
    Codigo = models.CharField(max_length=10, null=True, blank=True)
    permite_decimal = models.BooleanField(default=False)
    data_cadastro = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.Descricao


class Grupo(models.Model):
    Idgrupo = models.BigAutoField(primary_key=True)
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, null=True, blank=True, related_name='grupos_produto', db_index=True)
    Codigo = models.CharField(max_length=12)
    CodigoRef = models.CharField(max_length=2, default='01', help_text='2 dígitos, ex.: 01')
    Descricao = models.CharField(max_length=100)
    Margem = models.DecimalField(max_digits=6, decimal_places=2, validators=[MinValueValidator(0)])
    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=['Codigo']),
        ]

    def __str__(self):
        return self.Descricao


class Subgrupo(models.Model):
    Idsubgrupo = models.BigAutoField(primary_key=True)
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, null=True, blank=True, related_name='subgrupos_produto', db_index=True)
    Idgrupo = models.ForeignKey(Grupo, on_delete=models.CASCADE, null=True, blank=True)
    Descricao = models.CharField(max_length=100)
    Margem = models.DecimalField(max_digits=6, decimal_places=2, validators=[MinValueValidator(0)])
    data_cadastro = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.Descricao


class Tabelapreco(models.Model):
    Idtabela = models.BigAutoField(primary_key=True)
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, null=True, blank=True, related_name='tabelas_preco', db_index=True)
    NomeTabela = models.CharField(max_length=100, default='Tabela')
    DataInicio = models.DateField()
    Promocao = models.BooleanField(default=False)
    DataFim = models.DateField(null=True, blank=True)
    data_cadastro = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f'{self.NomeTabela} ({self.Idtabela})'


class Codigos(models.Model):
    # Sequencial para gerar referência por (colecao, estacao)
    Idcodigo = models.BigAutoField(primary_key=True)
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, null=True, blank=True, related_name='sequenciais_produto', db_index=True)
    colecao = models.CharField(max_length=2, null=False, blank=False, default="00")
    estacao = models.CharField(max_length=2, null=False, blank=False, default="00")
    valor_var = models.IntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['empresa', 'colecao', 'estacao'], name='unique_empresa_colecao_estacao')
        ]

    def __str__(self):
        return f'{self.colecao}{self.estacao}: {self.valor_var}'


# ===========================
# Produto / SKU / Preço
# ===========================
class Produto(models.Model):
    TIPO_CHOICES = (
        ('1', 'Revenda'),
        ('2', 'Uso/Consumo'),
        ('3', 'Produto Próprio'),
        ('4', 'Insumo de Produção'),
    )

    Idproduto = models.BigAutoField(primary_key=True)
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, null=True, blank=True, related_name='produtos', db_index=True)
    tipo_produto = models.CharField(max_length=1, choices=TIPO_CHOICES, default='1')
    referencia = models.CharField(max_length=11, null=True, blank=True,
                                  help_text='Gerada automaticamente: AA-BB-CCDDD')
    descricao = models.CharField(max_length=120)
    descricao_reduzida = models.CharField(max_length=60, null=True, blank=True)
    unidade = models.ForeignKey(Unidade, on_delete=models.PROTECT)

    # Classificação / coleção
    grupo = models.ForeignKey(Grupo, on_delete=models.SET_NULL, null=True, blank=True)
    subgrupo = models.ForeignKey(Subgrupo, on_delete=models.SET_NULL, null=True, blank=True)
    colecao = models.ForeignKey(Colecao, on_delete=models.SET_NULL, null=True, blank=True)
    material = models.ForeignKey(Material, on_delete=models.SET_NULL, null=True, blank=True)

    # Grade (HAD) – obrigatória para produtos vendáveis com variação
    grade = models.ForeignKey(Grade, on_delete=models.PROTECT, null=True, blank=True)

    # Fiscal
    ncm = models.CharField(
        max_length=10, null=True, blank=True,
        validators=[RegexValidator(r'^\d{4}\.\d{2}\.\d{2}$', 'NCM deve estar no formato ####.##.##')]
    )
    origem_mercadoria = models.PositiveSmallIntegerField(null=True, blank=True, help_text='0=nacional, etc.')
    csosn_ou_cst_icms = models.CharField(max_length=3, null=True, blank=True)
    aliquota_icms = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    cfop_venda_dentro = models.CharField(max_length=4, null=True, blank=True)
    cfop_venda_fora = models.CharField(max_length=4, null=True, blank=True)
    cst_pis = models.CharField(max_length=2, null=True, blank=True)
    aliq_pis = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    cst_cofins = models.CharField(max_length=2, null=True, blank=True)
    aliq_cofins = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    ipi_situacao = models.CharField(max_length=2, null=True, blank=True)
    aliq_ipi = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    # Estado
    ativo = models.BooleanField(default=True)
    # >>> ADDED
    bloqueado_venda = models.BooleanField(
        default=False,
        help_text='Se marcado, impede operações de venda para este produto.'
    )
    # <<< ADDED
    observacoes = models.TextField(null=True, blank=True)
    data_cadastro = models.DateTimeField(default=timezone.now)
    data_inativo = models.DateTimeField(null=True, blank=True)
    custo_original = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    custo_ultima_compra = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    custo_medio = models.DecimalField(max_digits=12, decimal_places=4, default=0)

    def _gerar_referencia(self) -> str:
        # Regras: AA-BB-CCDDD
        if not self.colecao or not self.grupo:
            raise ValueError('Coleção e Grupo são obrigatórios para gerar a referência.')

        aa = str(self.colecao.Codigo).zfill(2)[:2]
        bb = str(self.colecao.Estacao).zfill(2)[:2]
        cc = str(self.grupo.CodigoRef).zfill(2)[:2]

        with transaction.atomic():
            cod_row, _ = Codigos.objects.select_for_update().get_or_create(
                empresa=self.empresa, colecao=aa, estacao=bb, defaults={'valor_var': 1}
            )
            ddd_val = cod_row.valor_var
            cod_row.valor_var = ddd_val + 1
            cod_row.save(update_fields=['valor_var'])

        ddd = f"{ddd_val:03d}"
        return f"{aa}-{bb}-{cc}{ddd}"

    def _gerar_referencia_insumo(self) -> str:
        with transaction.atomic():
            cod_row, _ = Codigos.objects.select_for_update().get_or_create(
                empresa=self.empresa, colecao='IN', estacao='00', defaults={'valor_var': 1}
            )
            while True:
                numero = cod_row.valor_var
                referencia = f"INS-{numero:03d}"
                cod_row.valor_var = numero + 1
                cod_row.save(update_fields=['valor_var'])
                if not Produto.objects.filter(empresa=self.empresa, referencia=referencia).exists():
                    return referencia

    def save(self, *args, **kwargs):
        if self.tipo_produto in ('1', '3') and not self.referencia:
            self.referencia = self._gerar_referencia()
        elif self.tipo_produto == '4' and not self.referencia:
            self.referencia = self._gerar_referencia_insumo()
        super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['empresa', 'referencia'], name='uq_empresa_produto_referencia'),
        ]
        indexes = [
            models.Index(fields=['tipo_produto']),
            models.Index(fields=['ncm']),
            # >>> ADDED
            models.Index(fields=['ativo']),
            models.Index(fields=['bloqueado_venda']),
            # <<< ADDED
        ]

    def __str__(self):
        return f'{self.Idproduto} - {self.descricao}'


class ProdutoDetalhe(models.Model):
    # Variante Cor × Tamanho – usada por produtos vendáveis com grade
    IdprodutoDetalhe = models.BigAutoField(primary_key=True)
    produto = models.ForeignKey(Produto, on_delete=models.CASCADE, related_name='skus')
    idcor = models.ForeignKey(Cor, on_delete=models.PROTECT)
    idtamanho = models.ForeignKey(Tamanho, on_delete=models.PROTECT)

    # Prefixo GS1 usado para gerar o EAN (pode ficar em branco; save escolhe o ativo)
    config_ean = models.ForeignKey(
        ConfigEan, on_delete=models.PROTECT, related_name='skus',
        blank=True, null=True
    )

    # Código do item (5 dígitos por prefixo) + EAN-13 (único) — gerados automaticamente
    codigo_item_ref = models.CharField(
        max_length=5,
        validators=[RegexValidator(r'^\d{5}$', 'Use exatamente 5 dígitos.')],
        help_text='Sequência 00001..99999 por prefixo para compor o EAN-13.',
        blank=True
    )
    ean13 = models.CharField(
        max_length=13,
        validators=[RegexValidator(r'^\d{13}$', 'EAN-13 deve ter exatamente 13 dígitos.')],
        unique=True,
        help_text='EAN-13 (789 + empresa(4) + item(5) + DV).',
        blank=True,
        editable=False
    )

    ativo = models.BooleanField(default=True)
    custo_original = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    custo_ultima_compra = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    custo_medio = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    # >>> ADDED
    bloqueado_venda = models.BooleanField(
        default=False,
        help_text='Se marcado, impede operações de venda para este SKU.'
    )
    # <<< ADDED

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['produto', 'idcor', 'idtamanho'], name='uq_produto_cor_tamanho'),
            models.UniqueConstraint(fields=['config_ean', 'codigo_item_ref'], name='uq_prefixo_itemref'),
        ]
        indexes = [
            models.Index(fields=['ean13']),
            models.Index(fields=['codigo_item_ref']),
            models.Index(fields=['config_ean']),
            # >>> ADDED
            models.Index(fields=['ativo']),
            models.Index(fields=['bloqueado_venda']),
            # <<< ADDED
        ]

    def __str__(self):
        return f'{self.produto_id} · {self.idcor_id} · {self.idtamanho_id} · {self.ean13}'

    def _alocar_itemref_e_ean(self):
        with transaction.atomic():
            # 1) Escolher prefixo ativo se não vier
            if not self.config_ean_id:
                empresa_id = getattr(getattr(self, 'produto', None), 'empresa_id', None)
                cfg_qs = ConfigEan.objects.select_for_update().filter(ativo=True)
                if empresa_id:
                    cfg_qs = cfg_qs.filter(empresa_id=empresa_id)
                cfg = cfg_qs.order_by('id').first()
                if not cfg:
                    raise ValueError('Nenhum prefixo GS1 ativo encontrado em ConfigEan.')
                self.config_ean = cfg
            else:
                cfg = ConfigEan.objects.select_for_update().get(pk=self.config_ean_id)

            # 2) Alocar sequência (5 dígitos) se não vier
            if not self.codigo_item_ref:
                val = cfg.next_itemref or 1
                if val > 99999:
                    raise ValueError(f'Prefixo {cfg.company_prefix} esgotado (>= 100000). Cadastre/ative outro.')
                self.codigo_item_ref = f'{val:05d}'
                cfg.next_itemref = val + 1
                cfg.save(update_fields=['next_itemref'])

            # 3) Montar EAN-13 se não vier
            if not self.ean13:
                base12 = f'{cfg.country_prefix}{cfg.company_prefix}{self.codigo_item_ref}'
                dv = ean13_check_digit(base12)
                self.ean13 = base12 + dv

    def save(self, *args, **kwargs):
        creating = self.pk is None
        if creating or (not self.config_ean_id or not self.codigo_item_ref or not self.ean13):
            self._alocar_itemref_e_ean()
        super().save(*args, **kwargs)


class TabelaprecoProduto(models.Model):
    """Preço por PRODUTO (revenda)."""
    Idprodutopreco = models.BigAutoField(primary_key=True)
    produto = models.ForeignKey(Produto, on_delete=models.CASCADE, related_name='precos')
    tabela = models.ForeignKey(Tabelapreco, on_delete=models.PROTECT, related_name='precos')
    preco = models.DecimalField(max_digits=12, decimal_places=4)
    preco_promocional = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    DataInicio = models.DateField(default=timezone.now)
    DataFim = models.DateField(null=True, blank=True)
    ativo = models.BooleanField(default=True)
    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=['produto', 'tabela']),
            models.Index(fields=['DataInicio', 'DataFim']),
        ]

    def __str__(self):
        return f'{self.produto_id} · {self.tabela_id} · {self.preco}'


class FichaTecnica(models.Model):
    STATUS_RASCUNHO = 'RASCUNHO'
    STATUS_APROVADA = 'APROVADA'
    STATUS_INATIVA = 'INATIVA'
    STATUS_CHOICES = (
        (STATUS_RASCUNHO, 'Rascunho'),
        (STATUS_APROVADA, 'Aprovada'),
        (STATUS_INATIVA, 'Inativa'),
    )

    empresa = models.ForeignKey(
        'cadastros.Empresa',
        on_delete=models.PROTECT,
        related_name='fichas_tecnicas',
        db_index=True,
    )
    produto_final = models.ForeignKey(
        Produto,
        on_delete=models.PROTECT,
        related_name='fichas_tecnicas',
        limit_choices_to={'tipo_produto': '3'},
    )
    versao = models.CharField(max_length=20, default='1')
    descricao = models.CharField(max_length=120, null=True, blank=True)
    rendimento = models.DecimalField(max_digits=10, decimal_places=3, default=1)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_RASCUNHO, db_index=True)
    ativa = models.BooleanField(default=True, db_index=True)
    observacoes = models.TextField(null=True, blank=True)
    data_cadastro = models.DateTimeField(default=timezone.now, db_index=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['empresa', 'produto_final', 'versao'], name='uq_empresa_produto_ficha_versao'),
        ]
        indexes = [
            models.Index(fields=['empresa', 'produto_final']),
            models.Index(fields=['status']),
            models.Index(fields=['ativa']),
        ]

    def __str__(self):
        return f'{self.produto_final_id} · ficha {self.versao}'


class FichaTecnicaItem(models.Model):
    TIPO_INSUMO = 'INSUMO'
    TIPO_AVIAMENTO = 'AVIAMENTO'
    TIPO_SERVICO = 'SERVICO'
    TIPO_CHOICES = (
        (TIPO_INSUMO, 'Insumo'),
        (TIPO_AVIAMENTO, 'Aviamento'),
        (TIPO_SERVICO, 'Serviço/Facção'),
    )

    ficha = models.ForeignKey(FichaTecnica, on_delete=models.CASCADE, related_name='itens')
    tipo = models.CharField(max_length=15, choices=TIPO_CHOICES, default=TIPO_INSUMO, db_index=True)
    produto = models.ForeignKey(
        Produto,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='itens_ficha_tecnica',
    )
    fornecedor = models.ForeignKey(
        'cadastros.Fornecedor',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='itens_ficha_tecnica',
    )
    descricao = models.CharField(max_length=120, null=True, blank=True)
    unidade = models.ForeignKey(Unidade, on_delete=models.PROTECT, null=True, blank=True)
    quantidade = models.DecimalField(max_digits=12, decimal_places=4)
    perda_percentual = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    custo_unitario_previsto = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    observacoes = models.CharField(max_length=200, null=True, blank=True)
    ordem = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['ordem', 'id']
        indexes = [
            models.Index(fields=['ficha', 'tipo']),
            models.Index(fields=['produto']),
            models.Index(fields=['fornecedor']),
        ]

    @property
    def quantidade_com_perda(self):
        perda = Decimal(self.perda_percentual or 0) / Decimal('100')
        return Decimal(self.quantidade or 0) * (Decimal('1') + perda)

    @property
    def custo_medio_produto(self):
        if not self.produto_id:
            return Decimal('0')
        return Decimal(
            self.produto.custo_medio
            or self.produto.custo_ultima_compra
            or self.produto.custo_original
            or 0
        )

    @property
    def custo_unitario_usado(self):
        custo_informado = Decimal(self.custo_unitario_previsto or 0)
        if custo_informado > 0:
            return custo_informado
        return self.custo_medio_produto

    @property
    def custo_total_previsto(self):
        return self.quantidade_com_perda * self.custo_unitario_usado

    def __str__(self):
        return self.descricao or str(self.produto or self.fornecedor or self.pk)


class OrdemProducao(models.Model):
    STATUS_ABERTA = 'ABERTA'
    STATUS_APROVADA = 'APROVADA'
    STATUS_EM_PRODUCAO = 'EM_PRODUCAO'
    STATUS_FINALIZADA = 'FINALIZADA'
    STATUS_CANCELADA = 'CANCELADA'
    STATUS_CHOICES = (
        (STATUS_ABERTA, 'Aberta'),
        (STATUS_APROVADA, 'Aprovada'),
        (STATUS_EM_PRODUCAO, 'Em produção'),
        (STATUS_FINALIZADA, 'Finalizada'),
        (STATUS_CANCELADA, 'Cancelada'),
    )

    empresa = models.ForeignKey(
        'cadastros.Empresa',
        on_delete=models.PROTECT,
        related_name='ordens_producao',
        db_index=True,
    )
    numero = models.CharField(max_length=30, db_index=True)
    ficha_tecnica = models.ForeignKey(FichaTecnica, on_delete=models.PROTECT, related_name='ordens_producao')
    produto_final = models.ForeignKey(Produto, on_delete=models.PROTECT, related_name='ordens_producao')
    sku_final = models.ForeignKey(
        ProdutoDetalhe,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='ordens_producao',
        help_text='SKU acabado produzido pela OP. A entrada ocorre no estoque central da empresa.',
    )
    quantidade = models.DecimalField(max_digits=12, decimal_places=3)
    rendimento = models.DecimalField(max_digits=10, decimal_places=3, default=1)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_ABERTA, db_index=True)
    custo_previsto = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    custo_real = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    observacoes = models.TextField(null=True, blank=True)
    data_emissao = models.DateField(default=timezone.localdate, db_index=True)
    data_inicio = models.DateTimeField(null=True, blank=True)
    data_finalizacao = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(default=timezone.now)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-data_emissao', '-id']
        constraints = [
            models.UniqueConstraint(fields=['empresa', 'numero'], name='uq_empresa_ordem_producao_numero'),
        ]
        indexes = [
            models.Index(fields=['empresa', 'status']),
            models.Index(fields=['produto_final']),
            models.Index(fields=['ficha_tecnica']),
            models.Index(fields=['sku_final']),
        ]

    def save(self, *args, **kwargs):
        if self.ficha_tecnica_id and not self.produto_final_id:
            self.produto_final = self.ficha_tecnica.produto_final
        if self.ficha_tecnica_id and not self.rendimento:
            self.rendimento = self.ficha_tecnica.rendimento
        if not self.numero:
            ano = timezone.now().year
            prefixo = f"OP{ano}"
            ultima = (
                OrdemProducao.objects
                .filter(empresa=self.empresa, numero__startswith=prefixo)
                .order_by('-numero')
                .values_list('numero', flat=True)
                .first()
            )
            sequencia = 1
            if ultima:
                try:
                    sequencia = int(str(ultima).replace(prefixo, '') or 0) + 1
                except ValueError:
                    sequencia = 1
            self.numero = f"{prefixo}{sequencia:05d}"
        super().save(*args, **kwargs)

    def recalcular_totais(self):
        total_previsto = Decimal('0')
        total_real = Decimal('0')
        for item in self.itens.all():
            total_previsto += Decimal(item.custo_total_previsto or 0)
            total_real += Decimal(item.custo_total_real or 0)
        self.custo_previsto = total_previsto.quantize(Decimal('0.01'))
        self.custo_real = total_real.quantize(Decimal('0.01'))
        self.save(update_fields=['custo_previsto', 'custo_real', 'atualizado_em'])

    def __str__(self):
        return f'{self.numero} - {self.produto_final}'


class OrdemProducaoItem(models.Model):
    STATUS_FACCAO_PENDENTE = 'PENDENTE'
    STATUS_FACCAO_ENVIADO = 'ENVIADO'
    STATUS_FACCAO_RETORNADO = 'RETORNADO'
    STATUS_FACCAO_CHOICES = (
        (STATUS_FACCAO_PENDENTE, 'Pendente'),
        (STATUS_FACCAO_ENVIADO, 'Enviado'),
        (STATUS_FACCAO_RETORNADO, 'Retornado'),
    )

    ordem = models.ForeignKey(OrdemProducao, on_delete=models.CASCADE, related_name='itens')
    ficha_item = models.ForeignKey(FichaTecnicaItem, on_delete=models.PROTECT, related_name='itens_ordem')
    tipo = models.CharField(max_length=15, choices=FichaTecnicaItem.TIPO_CHOICES)
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT, null=True, blank=True, related_name='itens_ordem_producao')
    fornecedor = models.ForeignKey(
        'cadastros.Fornecedor',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='itens_ordem_producao',
    )
    descricao = models.CharField(max_length=120, null=True, blank=True)
    unidade = models.ForeignKey(Unidade, on_delete=models.PROTECT, null=True, blank=True)
    quantidade_base = models.DecimalField(max_digits=12, decimal_places=4)
    perda_percentual = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    quantidade_necessaria = models.DecimalField(max_digits=14, decimal_places=4)
    custo_unitario_previsto = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    custo_unitario_real = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    custo_total_previsto = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    custo_total_real = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    observacoes = models.CharField(max_length=200, null=True, blank=True)
    ordem_linha = models.PositiveIntegerField(default=1)
    status_faccao = models.CharField(max_length=12, choices=STATUS_FACCAO_CHOICES, default=STATUS_FACCAO_PENDENTE, db_index=True)
    documento_faccao = models.CharField(max_length=50, null=True, blank=True)
    data_envio_faccao = models.DateField(null=True, blank=True)
    data_retorno_faccao = models.DateField(null=True, blank=True)
    quantidade_enviada_faccao = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    quantidade_retornada_faccao = models.DecimalField(max_digits=14, decimal_places=4, default=0)

    class Meta:
        ordering = ['ordem_linha', 'id']
        indexes = [
            models.Index(fields=['ordem', 'tipo']),
            models.Index(fields=['produto']),
            models.Index(fields=['status_faccao']),
        ]

    def __str__(self):
        return self.descricao or str(self.produto or self.fornecedor or self.pk)


class OrdemProducaoGrade(models.Model):
    ordem = models.ForeignKey(OrdemProducao, on_delete=models.CASCADE, related_name='grade_producao')
    sku_final = models.ForeignKey(
        ProdutoDetalhe,
        on_delete=models.PROTECT,
        related_name='grades_ordem_producao',
        help_text='SKU acabado produzido nesta linha da OP.',
    )
    quantidade = models.DecimalField(max_digits=12, decimal_places=3)

    class Meta:
        ordering = ['sku_final__idcor__Descricao', 'sku_final__idtamanho__Tamanho', 'id']
        constraints = [
            models.UniqueConstraint(fields=['ordem', 'sku_final'], name='uq_op_grade_sku'),
        ]
        indexes = [
            models.Index(fields=['ordem']),
            models.Index(fields=['sku_final']),
        ]

    def __str__(self):
        return f'{self.ordem.numero} - {self.sku_final} - {self.quantidade}'


class Promocao(models.Model):
    TIPO_DESCONTO_PERCENTUAL = 'DESCONTO_PERCENTUAL'
    TIPO_DESCONTO_VALOR = 'DESCONTO_VALOR'
    TIPO_PRECO_FIXO = 'PRECO_FIXO'
    TIPO_CHOICES = [
        (TIPO_DESCONTO_PERCENTUAL, 'Desconto percentual'),
        (TIPO_DESCONTO_VALOR, 'Desconto em valor'),
        (TIPO_PRECO_FIXO, 'Preço fixo'),
    ]

    ESCOPO_TODOS = 'TODOS'
    ESCOPO_PRODUTO = 'PRODUTO'
    ESCOPO_COLECAO = 'COLECAO'
    ESCOPO_GRUPO = 'GRUPO'
    ESCOPO_SUBGRUPO = 'SUBGRUPO'
    ESCOPO_CHOICES = [
        (ESCOPO_TODOS, 'Todos os produtos'),
        (ESCOPO_PRODUTO, 'Produto'),
        (ESCOPO_COLECAO, 'Coleção'),
        (ESCOPO_GRUPO, 'Grupo'),
        (ESCOPO_SUBGRUPO, 'Subgrupo'),
    ]

    Idpromocao = models.BigAutoField(primary_key=True)
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, null=True, blank=True, related_name='promocoes_produto', db_index=True)
    nome = models.CharField(max_length=100)
    ativo = models.BooleanField(default=True, db_index=True)
    data_inicio = models.DateField(db_index=True)
    data_fim = models.DateField(null=True, blank=True, db_index=True)
    tipo = models.CharField(max_length=25, choices=TIPO_CHOICES)
    valor = models.DecimalField(max_digits=18, decimal_places=4)
    escopo = models.CharField(max_length=15, choices=ESCOPO_CHOICES, default=ESCOPO_TODOS)
    prioridade = models.PositiveIntegerField(default=10)
    acumula_cashback = models.BooleanField(default=True)
    observacao = models.CharField(max_length=255, blank=True, default='')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    lojas = models.ManyToManyField('cadastros.Loja', blank=True, related_name='promocoes')
    produtos = models.ManyToManyField(Produto, blank=True, related_name='promocoes')
    colecoes = models.ManyToManyField(Colecao, blank=True, related_name='promocoes')
    grupos = models.ManyToManyField(Grupo, blank=True, related_name='promocoes')
    subgrupos = models.ManyToManyField(Subgrupo, blank=True, related_name='promocoes')

    class Meta:
        db_table = 'produto_promocao'
        ordering = ['-ativo', '-data_inicio', 'prioridade', 'nome']
        indexes = [
            models.Index(fields=['ativo', 'data_inicio', 'data_fim']),
            models.Index(fields=['escopo', 'prioridade']),
        ]

    def __str__(self):
        return self.nome

    def aplica_produto(self, produto: Produto) -> bool:
        if self.escopo == self.ESCOPO_TODOS:
            return True
        if self.escopo == self.ESCOPO_PRODUTO:
            return self.produtos.filter(pk=produto.pk).exists()
        if self.escopo == self.ESCOPO_COLECAO:
            return bool(produto.colecao_id and self.colecoes.filter(pk=produto.colecao_id).exists())
        if self.escopo == self.ESCOPO_GRUPO:
            return bool(produto.grupo_id and self.grupos.filter(pk=produto.grupo_id).exists())
        if self.escopo == self.ESCOPO_SUBGRUPO:
            return bool(produto.subgrupo_id and self.subgrupos.filter(pk=produto.subgrupo_id).exists())
        return False

    def preco_promocional(self, preco_base):
        base = Decimal(preco_base or 0)
        valor = Decimal(self.valor or 0)
        if self.tipo == self.TIPO_PRECO_FIXO:
            return max(Decimal('0.00'), valor)
        if self.tipo == self.TIPO_DESCONTO_PERCENTUAL:
            return max(Decimal('0.00'), base - (base * valor / Decimal('100')))
        if self.tipo == self.TIPO_DESCONTO_VALOR:
            return max(Decimal('0.00'), base - valor)
        return base


# ===========================
# Packs (compra por grade)
# ===========================
class Pack(models.Model):
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, null=True, blank=True, related_name='packs_produto', db_index=True)
    nome = models.CharField(max_length=80, null=True, blank=True)
    grade = models.ForeignKey(Grade, on_delete=models.PROTECT, related_name='packs')
    ativo = models.BooleanField(default=True)

    data_cadastro = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['grade', 'nome'], name='uq_pack_grade_nome')
        ]
        indexes = [
            models.Index(fields=['grade'], name='ix_pack_grade'),
            models.Index(fields=['ativo'], name='ix_pack_ativo'),
        ]

    def __str__(self):
        return self.nome or f'Pack #{self.pk}'


class PackItem(models.Model):
    pack = models.ForeignKey(Pack, on_delete=models.CASCADE, related_name='itens')
    tamanho = models.ForeignKey(Tamanho, on_delete=models.PROTECT, related_name='packs_itens')
    qtd = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['pack', 'tamanho'], name='uq_packitem_pack_tamanho')
        ]
        indexes = [
            models.Index(fields=['pack'], name='ix_packitem_pack'),
            models.Index(fields=['tamanho'], name='ix_packitem_tamanho'),
        ]

    def __str__(self):
        return f'{self.pack_id} · {self.tamanho_id} · {self.qtd}'


# ===========================
# Estoque (por Loja × EAN-13)
# ===========================
class Estoque(models.Model):
    Idestoque = models.BigAutoField(primary_key=True)
    CodigodeBarra = models.CharField(
        max_length=13,
        validators=[RegexValidator(r'^\d{13}$', 'EAN-13 deve ter exatamente 13 dígitos.')]
    )
    referencia = models.CharField(max_length=30, default='')
    Idloja = models.ForeignKey('cadastros.Loja', on_delete=models.CASCADE)
    Estoque = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    reserva = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['CodigodeBarra', 'Idloja'], name='uq_estoque_codigodebarra_loja'),
        ]
        indexes = [
            models.Index(fields=['CodigodeBarra']),
            models.Index(fields=['Idloja']),
        ]

    def __str__(self):
        return f'{self.CodigodeBarra} - {self.Estoque}'


class EstoqueMovimentacao(models.Model):
    TIPO_ENTRADA = 'ENTRADA'
    TIPO_SAIDA = 'SAIDA'
    TIPO_AJUSTE = 'AJUSTE'
    TIPO_RESERVA = 'RESERVA'
    TIPO_CHOICES = [
        (TIPO_ENTRADA, 'Entrada'),
        (TIPO_SAIDA, 'Saída'),
        (TIPO_AJUSTE, 'Ajuste'),
        (TIPO_RESERVA, 'Reserva'),
    ]

    Idmovimento = models.BigAutoField(primary_key=True)
    Idloja = models.ForeignKey('cadastros.Loja', on_delete=models.PROTECT, db_index=True)
    CodigodeBarra = models.CharField(
        max_length=13,
        validators=[RegexValidator(r'^\d{13}$', 'EAN-13 deve ter exatamente 13 dígitos.')],
        db_index=True,
    )
    referencia = models.CharField(max_length=30, default='', db_index=True)
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, db_index=True)
    quantidade = models.DecimalField(max_digits=14, decimal_places=3)
    custo_unitario = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    custo_total = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    custo_medio_apos = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    saldo_anterior = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    saldo_posterior = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    documento = models.CharField(max_length=50, null=True, blank=True)
    observacao = models.CharField(max_length=255, null=True, blank=True)
    data_movimento = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = 'produto_estoque_movimentacao'
        indexes = [
            models.Index(fields=['Idloja', 'CodigodeBarra']),
            models.Index(fields=['referencia']),
            models.Index(fields=['tipo', 'data_movimento']),
        ]
        ordering = ['-data_movimento', '-Idmovimento']

    def __str__(self):
        return f'{self.Idmovimento} - {self.tipo} - {self.CodigodeBarra}'


class InventarioEstoque(models.Model):
    STATUS_ABERTO = 'ABERTO'
    STATUS_VALIDADO = 'VALIDADO'
    STATUS_FECHADO = 'FECHADO'
    STATUS_CANCELADO = 'CANCELADO'
    STATUS_CHOICES = [
        (STATUS_ABERTO, 'Aberto'),
        (STATUS_VALIDADO, 'Validado'),
        (STATUS_FECHADO, 'Fechado'),
        (STATUS_CANCELADO, 'Cancelado'),
    ]

    Idinventario = models.BigAutoField(primary_key=True)
    Idloja = models.ForeignKey('cadastros.Loja', on_delete=models.PROTECT, db_index=True)
    descricao = models.CharField(max_length=120)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ABERTO, db_index=True)
    data_abertura = models.DateField(default=timezone.now)
    data_fechamento = models.DateField(null=True, blank=True)
    observacao = models.CharField(max_length=255, null=True, blank=True)
    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'produto_inventario_estoque'
        ordering = ['-data_abertura', '-Idinventario']

    def __str__(self):
        return f'{self.Idinventario} - {self.descricao}'


class InventarioEstoqueItem(models.Model):
    Idinventarioitem = models.BigAutoField(primary_key=True)
    inventario = models.ForeignKey(InventarioEstoque, on_delete=models.CASCADE, related_name='itens')
    CodigodeBarra = models.CharField(
        max_length=13,
        validators=[RegexValidator(r'^\d{13}$', 'EAN-13 deve ter exatamente 13 dígitos.')],
    )
    referencia = models.CharField(max_length=30, default='')
    saldo_sistema = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    saldo_contado = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    diferenca = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    contado = models.BooleanField(default=False, db_index=True)
    observacao = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = 'produto_inventario_estoque_item'
        constraints = [
            models.UniqueConstraint(fields=['inventario', 'CodigodeBarra'], name='uq_inventario_item_ean')
        ]
        indexes = [
            models.Index(fields=['inventario']),
            models.Index(fields=['CodigodeBarra']),
            models.Index(fields=['referencia']),
        ]

    def save(self, *args, **kwargs):
        self.diferenca = (self.saldo_contado or 0) - (self.saldo_sistema or 0)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.inventario_id} - {self.CodigodeBarra}'
