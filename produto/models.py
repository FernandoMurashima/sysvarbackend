from django.db import models, transaction
from django.utils import timezone
from django.core.validators import RegexValidator, MinValueValidator, MaxValueValidator

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
            models.UniqueConstraint(fields=['country_prefix', 'company_prefix'], name='uq_country_company_prefix'),
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
    # Guardaremos também no Produto como CHAR(10) no formato ####.##.##
    ncm = models.CharField(
        max_length=10, null=True, blank=True,
        validators=[RegexValidator(r'^\d{4}\.\d{2}\.\d{2}$', 'NCM deve estar no formato ####.##.##')]
    )
    descricao = models.CharField(max_length=1000)
    aliquota = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    campo1 = models.CharField(max_length=25, blank=True, null=True)

    def __str__(self):
        return f'{self.ncm} - {self.descricao[:60]}'


class Grade(models.Model):  # HAD
    Idgrade = models.BigAutoField(primary_key=True)
    Descricao = models.CharField(max_length=100)
    Status = models.CharField(max_length=10, null=True, blank=True)
    data_cadastro = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.Descricao


class Tamanho(models.Model):
    Idtamanho = models.BigAutoField(primary_key=True)
    idgrade = models.ForeignKey(Grade, on_delete=models.CASCADE)
    Tamanho = models.CharField(max_length=10)  # ex.: PP, P, M, G, 38, 40...
    Descricao = models.CharField(max_length=100, default="Tamanho")
    Status = models.CharField(max_length=10, null=True, blank=True)
    data_cadastro = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.Tamanho or self.Descricao


class Cor(models.Model):
    Idcor = models.BigAutoField(primary_key=True)
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
    Descricao = models.CharField(max_length=100)
    Codigo = models.CharField(max_length=2, null=True, blank=True, help_text='Dois ultimos digitos do ano, ex.: 2026 = 26')
    Estacao = models.CharField(max_length=2, null=True, blank=True, choices=ESTACOES_CHOICES)
    Status = models.CharField(max_length=10, null=True, blank=True, choices=STATUS_CHOICES)
    Contador = models.IntegerField(null=True, blank=True, default=0)
    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['Codigo', 'Estacao'], name='uq_colecao_codigo_estacao')
        ]

    def __str__(self):
        return self.Descricao


class Unidade(models.Model):
    Idunidade = models.BigAutoField(primary_key=True)
    Descricao = models.CharField(max_length=100)
    Codigo = models.CharField(max_length=10, null=True, blank=True)
    data_cadastro = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.Descricao


class Grupo(models.Model):
    Idgrupo = models.BigAutoField(primary_key=True)
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
    Idgrupo = models.ForeignKey(Grupo, on_delete=models.CASCADE, null=True, blank=True)
    Descricao = models.CharField(max_length=100)
    Margem = models.DecimalField(max_digits=6, decimal_places=2, validators=[MinValueValidator(0)])
    data_cadastro = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.Descricao


class Tabelapreco(models.Model):
    Idtabela = models.BigAutoField(primary_key=True)
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
    colecao = models.CharField(max_length=2, null=False, blank=False, default="00")
    estacao = models.CharField(max_length=2, null=False, blank=False, default="00")
    valor_var = models.IntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['colecao', 'estacao'], name='unique_colecao_estacao')
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
    )

    Idproduto = models.BigAutoField(primary_key=True)
    tipo_produto = models.CharField(max_length=1, choices=TIPO_CHOICES, default='1')
    referencia = models.CharField(max_length=11, null=True, blank=True, unique=True,
                                  help_text='Gerada automaticamente: AA-BB-CCDDD')
    descricao = models.CharField(max_length=120)
    descricao_reduzida = models.CharField(max_length=60, null=True, blank=True)
    unidade = models.ForeignKey(Unidade, on_delete=models.PROTECT)

    # Classificação / coleção
    grupo = models.ForeignKey(Grupo, on_delete=models.SET_NULL, null=True, blank=True)
    subgrupo = models.ForeignKey(Subgrupo, on_delete=models.SET_NULL, null=True, blank=True)
    colecao = models.ForeignKey(Colecao, on_delete=models.SET_NULL, null=True, blank=True)
    material = models.ForeignKey(Material, on_delete=models.SET_NULL, null=True, blank=True)

    # Grade (HAD) – obrigatória para Revenda (validar na aplicação)
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

    def _gerar_referencia(self) -> str:
        # Regras: AA-BB-CCDDD
        if not self.colecao or not self.grupo:
            raise ValueError('Coleção e Grupo são obrigatórios para gerar a referência.')

        aa = str(self.colecao.Codigo).zfill(2)[:2]
        bb = str(self.colecao.Estacao).zfill(2)[:2]
        cc = str(self.grupo.CodigoRef).zfill(2)[:2]

        with transaction.atomic():
            cod_row, _ = Codigos.objects.select_for_update().get_or_create(
                colecao=aa, estacao=bb, defaults={'valor_var': 1}
            )
            ddd_val = cod_row.valor_var
            cod_row.valor_var = ddd_val + 1
            cod_row.save(update_fields=['valor_var'])

        ddd = f"{ddd_val:03d}"
        return f"{aa}-{bb}-{cc}{ddd}"

    def save(self, *args, **kwargs):
        if self.tipo_produto == '1' and not self.referencia:
            self.referencia = self._gerar_referencia()
        super().save(*args, **kwargs)

    class Meta:
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
    # Variante Cor × Tamanho – só existe para Revenda
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
                cfg = ConfigEan.objects.select_for_update().filter(ativo=True).order_by('id').first()
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


# ===========================
# Packs (compra por grade)
# ===========================
class Pack(models.Model):
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
    Estoque = models.IntegerField(null=True, blank=True)
    reserva = models.IntegerField(null=True, blank=True)

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
