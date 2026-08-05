from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from cadastros.validators import (
    cpf_validator,
    cnpj_validator,
    email_simple_validator,
    telefone_br_validator,
    cep_validator,
)


class Empresa(models.Model):
    REGIME_SIMPLES = "SIMPLES"
    REGIME_LUCRO_PRESUMIDO = "LUCRO_PRESUMIDO"
    REGIME_LUCRO_REAL = "LUCRO_REAL"
    REGIME_TRIBUTARIO_CHOICES = [
        (REGIME_SIMPLES, "Simples Nacional"),
        (REGIME_LUCRO_PRESUMIDO, "Lucro Presumido"),
        (REGIME_LUCRO_REAL, "Lucro Real"),
    ]

    AMBIENTE_HOMOLOGACAO = "HOMOLOGACAO"
    AMBIENTE_PRODUCAO = "PRODUCAO"
    AMBIENTE_FISCAL_CHOICES = [
        (AMBIENTE_HOMOLOGACAO, "Homologação"),
        (AMBIENTE_PRODUCAO, "Produção"),
    ]

    nome = models.CharField(max_length=120, db_index=True)
    nome_fantasia = models.CharField(max_length=120, null=True, blank=True, db_index=True)
    documento = models.CharField(max_length=18, null=True, blank=True, unique=True)
    ativo = models.BooleanField(default=True, db_index=True)
    plano_completo = models.BooleanField(default=False, db_index=True)
    # Legado: este campo representava plano completo, nunca usuário master.
    licenca_master = models.BooleanField(default=False, db_index=True)
    usa_vendas = models.BooleanField(default=False, db_index=True)
    usa_compras = models.BooleanField(default=False, db_index=True)
    usa_estoque = models.BooleanField(default=False, db_index=True)
    usa_financeiro = models.BooleanField(default=False, db_index=True)
    usa_fiscal = models.BooleanField(default=False, db_index=True)
    regime_tributario = models.CharField(
        max_length=20,
        choices=REGIME_TRIBUTARIO_CHOICES,
        default=REGIME_SIMPLES,
        db_index=True,
    )
    ambiente_fiscal = models.CharField(
        max_length=12,
        choices=AMBIENTE_FISCAL_CHOICES,
        default=AMBIENTE_HOMOLOGACAO,
        db_index=True,
    )
    uf_fiscal = models.CharField(max_length=2, null=True, blank=True, db_index=True)
    inscricao_estadual = models.CharField(max_length=20, null=True, blank=True)
    serie_nfce = models.PositiveIntegerField(default=1)
    proximo_numero_nfce = models.PositiveIntegerField(default=1)
    serie_nfe = models.PositiveIntegerField(default=1)
    proximo_numero_nfe = models.PositiveIntegerField(default=1)
    usa_producao = models.BooleanField(default=False, db_index=True)
    usa_ficha_tecnica = models.BooleanField(default=False, db_index=True)
    usa_faccao = models.BooleanField(default=False, db_index=True)
    usa_distribuicao_producao = models.BooleanField(default=False, db_index=True)
    data_cadastro = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["nome"]),
            models.Index(fields=["nome_fantasia"]),
            models.Index(fields=["ativo"]),
            models.Index(fields=["usa_producao"]),
        ]
        ordering = ["nome"]

    def __str__(self):
        return self.nome_fantasia or self.nome

    def save(self, *args, **kwargs):
        if self.licenca_master and not self.plano_completo:
            self.plano_completo = True
        if self.plano_completo:
            self.usa_vendas = True
            self.usa_compras = True
            self.usa_estoque = True
            self.usa_financeiro = True
            self.usa_fiscal = True
            self.usa_producao = True
        if self.usa_producao:
            self.usa_ficha_tecnica = True
            self.usa_faccao = True
            self.usa_distribuicao_producao = True
        else:
            self.usa_ficha_tecnica = False
            self.usa_faccao = False
            self.usa_distribuicao_producao = False
        super().save(*args, **kwargs)


class ModuloSistema(models.Model):
    CATEGORIA_BASICO = "BASICO"
    CATEGORIA_COMERCIAL = "COMERCIAL"
    CATEGORIA_INTERNO = "INTERNO"
    CATEGORIA_CHOICES = [
        (CATEGORIA_BASICO, "Básico"),
        (CATEGORIA_COMERCIAL, "Comercial"),
        (CATEGORIA_INTERNO, "Interno"),
    ]

    chave = models.CharField(max_length=40, unique=True, db_index=True)
    nome = models.CharField(max_length=80)
    descricao = models.TextField(blank=True, default="")
    categoria = models.CharField(max_length=20, choices=CATEGORIA_CHOICES, db_index=True)
    basico = models.BooleanField(default=False, db_index=True)
    ativo = models.BooleanField(default=True, db_index=True)
    ordem = models.PositiveSmallIntegerField(default=0, db_index=True)
    dependencias = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["ordem", "nome"]
        indexes = [
            models.Index(fields=["chave"]),
            models.Index(fields=["basico", "ativo"]),
        ]

    def __str__(self):
        return f"{self.chave} - {self.nome}"


class EmpresaContrato(models.Model):
    STATUS_PENDENTE = "PENDENTE"
    STATUS_ATIVO = "ATIVO"
    STATUS_SUSPENSO = "SUSPENSO"
    STATUS_VENCIDO = "VENCIDO"
    STATUS_CANCELADO = "CANCELADO"
    STATUS_CHOICES = [
        (STATUS_PENDENTE, "Pendente"),
        (STATUS_ATIVO, "Ativo"),
        (STATUS_SUSPENSO, "Suspenso"),
        (STATUS_VENCIDO, "Vencido"),
        (STATUS_CANCELADO, "Cancelado"),
    ]
    MOTIVO_INADIMPLENCIA = "INADIMPLENCIA"
    MOTIVO_SOLICITACAO_CLIENTE = "SOLICITACAO_CLIENTE"
    MOTIVO_RISCO_SEGURANCA = "RISCO_SEGURANCA"
    MOTIVO_ENCERRAMENTO_CONTRATO = "ENCERRAMENTO_CONTRATO"
    MOTIVO_BLOQUEIO_ADMINISTRATIVO = "BLOQUEIO_ADMINISTRATIVO"
    MOTIVO_OUTRO = "OUTRO"
    MOTIVO_SUSPENSAO_CHOICES = [
        (MOTIVO_INADIMPLENCIA, "Inadimplência"),
        (MOTIVO_SOLICITACAO_CLIENTE, "Solicitação do cliente"),
        (MOTIVO_RISCO_SEGURANCA, "Risco de segurança"),
        (MOTIVO_ENCERRAMENTO_CONTRATO, "Encerramento de contrato"),
        (MOTIVO_BLOQUEIO_ADMINISTRATIVO, "Bloqueio administrativo"),
        (MOTIVO_OUTRO, "Outro"),
    ]

    empresa = models.OneToOneField(Empresa, on_delete=models.PROTECT, related_name="contrato")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_PENDENTE, db_index=True)
    data_inicio = models.DateField(default=timezone.localdate, db_index=True)
    data_fim = models.DateField(null=True, blank=True, db_index=True)
    limite_sessoes_simultaneas = models.PositiveIntegerField(default=1)
    limite_usuarios = models.PositiveIntegerField(default=1)
    plano_completo = models.BooleanField(default=False, db_index=True)
    usuario_master = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="empresas_master",
    )
    motivo_suspensao = models.CharField(max_length=40, choices=MOTIVO_SUSPENSAO_CHOICES, null=True, blank=True, db_index=True)
    observacao_suspensao = models.TextField(blank=True, default="")
    suspenso_em = models.DateTimeField(null=True, blank=True)
    suspenso_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="contratos_suspensos")
    reativado_em = models.DateTimeField(null=True, blank=True)
    reativado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="contratos_reativados")
    observacoes = models.TextField(blank=True, default="")
    permissions_version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "data_inicio", "data_fim"]),
            models.Index(fields=["plano_completo"]),
        ]

    def __str__(self):
        return f"Contrato {self.empresa_id} - {self.status}"

    def clean(self):
        if self.status == self.STATUS_ATIVO and self.limite_sessoes_simultaneas < 1:
            raise ValidationError({"limite_sessoes_simultaneas": "Contrato ativo exige pelo menos uma sessão simultânea."})
        if self.data_fim and self.data_inicio and self.data_fim < self.data_inicio:
            raise ValidationError({"data_fim": "A data final não pode ser anterior à data inicial."})
        if self.status == self.STATUS_SUSPENSO and not self.motivo_suspensao:
            raise ValidationError({"motivo_suspensao": "Informe o motivo da suspensão."})
        if self.usuario_master_id:
            if self.usuario_master.is_superuser:
                raise ValidationError({"usuario_master": "Superusuário interno não pode ser master de cliente."})
            if not self.usuario_master.is_active:
                raise ValidationError({"usuario_master": "Usuário master deve estar ativo."})
            if self.usuario_master.empresa_id != self.empresa_id:
                raise ValidationError({"usuario_master": "Usuário master deve pertencer à empresa."})

    @property
    def ativo_no_periodo(self):
        hoje = timezone.localdate()
        return (
            self.empresa.ativo
            and self.status == self.STATUS_ATIVO
            and self.data_inicio <= hoje
            and (self.data_fim is None or self.data_fim >= hoje)
        )

    @property
    def usuarios_ativos(self):
        return self.empresa.usuarios.filter(is_active=True, is_superuser=False).count()

    @property
    def sessoes_ativas(self):
        cutoff = timezone.now() - timezone.timedelta(minutes=getattr(settings, "SESSION_IDLE_TIMEOUT_MINUTES", 30))
        return self.empresa.sessoes_usuarios.filter(ativa=True, ultima_atividade_em__gte=cutoff).count()

    @property
    def sessoes_disponiveis(self):
        return max(0, int(self.limite_sessoes_simultaneas or 0) - self.sessoes_ativas)

    @property
    def limite_excedido(self):
        return self.sessoes_ativas > int(self.limite_sessoes_simultaneas or 0)

    @property
    def licencas_disponiveis(self):
        return self.sessoes_disponiveis

    @property
    def excedido(self):
        return self.limite_excedido

    def incrementar_versao(self, save=True):
        self.permissions_version = int(self.permissions_version or 0) + 1
        if save:
            self.save(update_fields=["permissions_version", "updated_at"])
        return self.permissions_version


class EmpresaModulo(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, related_name="modulos_contratados")
    modulo = models.ForeignKey(ModuloSistema, on_delete=models.PROTECT, related_name="empresas_modulo")
    contratado = models.BooleanField(default=False, db_index=True)
    data_inicio = models.DateField(null=True, blank=True, db_index=True)
    data_fim = models.DateField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["empresa", "modulo"], name="uq_empresa_modulo")
        ]
        indexes = [
            models.Index(fields=["empresa", "contratado"]),
            models.Index(fields=["data_inicio", "data_fim"]),
        ]

    def __str__(self):
        return f"{self.empresa_id} - {self.modulo.chave}: {self.contratado}"

    def clean(self):
        if self.modulo.basico and not self.contratado:
            return
        if self.data_fim and self.data_inicio and self.data_fim < self.data_inicio:
            raise ValidationError({"data_fim": "A data final não pode ser anterior à data inicial."})


class Loja(models.Model):
    TIPO_LOJA = "LOJA"
    TIPO_MATRIZ = "MATRIZ"
    TIPO_FABRICA = "FABRICA"
    TIPO_UNIDADE_CHOICES = [
        (TIPO_LOJA, "Loja"),
        (TIPO_MATRIZ, "Matriz / Estoque central"),
        (TIPO_FABRICA, "Fábrica / Produção"),
    ]

    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.PROTECT,
        null=False,
        blank=False,
        related_name="lojas",
        db_index=True,
    )
    nome_loja = models.CharField(max_length=50, db_index=True)
    apelido_loja = models.CharField(max_length=20, db_index=True)
    cnpj = models.CharField(max_length=18, validators=[cnpj_validator])

    logradouro = models.CharField(max_length=50, null=True, blank=True)
    endereco = models.CharField(max_length=50, null=True, blank=True)
    numero = models.CharField(max_length=10, null=True, blank=True)
    complemento = models.CharField(max_length=100, null=True, blank=True)
    cep = models.CharField(max_length=10, null=True, blank=True, validators=[cep_validator])
    bairro = models.CharField(max_length=30, null=True, blank=True)
    cidade = models.CharField(max_length=50, null=True, blank=True, db_index=True)
    estado = models.CharField(max_length=2, null=True, blank=True, db_index=True)

    telefone1 = models.CharField(max_length=15, null=True, blank=True, validators=[telefone_br_validator])
    telefone2 = models.CharField(max_length=15, null=True, blank=True, validators=[telefone_br_validator])
    email = models.CharField(max_length=50, null=True, blank=True, validators=[email_simple_validator])

    # NOVOS CAMPOS
    EstoqueNegativo = models.CharField(max_length=3, null=True, blank=True, default="NAO")
    Rede = models.CharField(max_length=3, null=True, blank=True, default="NAO")
    DataAbertura = models.DateField(null=True, blank=True, default=None)
    ContaContabil = models.CharField(max_length=50, null=True, blank=True, default="")
    DataEnceramento = models.DateField(null=True, blank=True, default=None)
    Matriz = models.CharField(max_length=3, null=True, blank=True, default="NAO")
    tipo_unidade = models.CharField(
        max_length=10,
        choices=TIPO_UNIDADE_CHOICES,
        default=TIPO_LOJA,
        db_index=True,
    )
    regime_tributario = models.CharField(
        max_length=20,
        choices=Empresa.REGIME_TRIBUTARIO_CHOICES,
        default=Empresa.REGIME_SIMPLES,
        db_index=True,
    )
    ambiente_fiscal = models.CharField(
        max_length=12,
        choices=Empresa.AMBIENTE_FISCAL_CHOICES,
        default=Empresa.AMBIENTE_HOMOLOGACAO,
        db_index=True,
    )
    inscricao_estadual = models.CharField(max_length=20, null=True, blank=True)
    serie_nfce = models.PositiveIntegerField(default=1)
    proximo_numero_nfce = models.PositiveIntegerField(default=1)
    serie_nfe = models.PositiveIntegerField(default=1)
    proximo_numero_nfe = models.PositiveIntegerField(default=1)
    emite_nfce = models.BooleanField(default=True)
    emite_nfe = models.BooleanField(default=True)

    ativo = models.BooleanField(default=True, db_index=True)
    data_cadastro = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["empresa", "cnpj"], name="uq_empresa_loja_cnpj")
        ]
        indexes = [
            models.Index(fields=["cnpj"]),
            models.Index(fields=["cidade", "estado"]),
            models.Index(fields=["ativo"]),
            models.Index(fields=["tipo_unidade"]),
            models.Index(fields=["data_cadastro"]),
        ]

    def __str__(self):
        return self.nome_loja

    def clean(self):
        super().clean()
        if not self.empresa_id:
            raise ValidationError({"empresa": "Estabelecimento deve pertencer a uma empresa."})
        if self.tipo_unidade not in {self.TIPO_LOJA, self.TIPO_MATRIZ, self.TIPO_FABRICA}:
            raise ValidationError({"tipo_unidade": "Tipo de unidade inválido."})
        self.Matriz = "SIM" if self.tipo_unidade == self.TIPO_MATRIZ else "NAO"
        if self.DataAbertura and self.DataEnceramento and self.DataEnceramento < self.DataAbertura:
            raise ValidationError({"DataEnceramento": "Data de encerramento não pode ser anterior à abertura."})
        if self.DataEnceramento and self.ativo:
            raise ValidationError({"ativo": "Estabelecimento encerrado não pode permanecer ativo."})
        for field in ("serie_nfce", "proximo_numero_nfce", "serie_nfe", "proximo_numero_nfe"):
            if int(getattr(self, field, 0) or 0) <= 0:
                raise ValidationError({field: "Informe valor maior que zero."})
        if self.estado and len(self.estado.strip()) != 2:
            raise ValidationError({"estado": "Informe a UF com duas letras."})
        if self.estado:
            self.estado = self.estado.strip().upper()

    def save(self, *args, **kwargs):
        self.Matriz = "SIM" if self.tipo_unidade == self.TIPO_MATRIZ else "NAO"
        self.full_clean(exclude=["cnpj"])
        return super().save(*args, **kwargs)


class Cliente(models.Model):
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="clientes",
        db_index=True,
    )
    nome_cliente = models.CharField(max_length=50, db_index=True)
    apelido = models.CharField(max_length=18, null=True, blank=True, db_index=True)
    cpf = models.CharField(max_length=15, null=True, blank=True, validators=[cpf_validator], db_index=True)
    logradouro = models.CharField(max_length=50, null=True, blank=True)
    endereco = models.CharField(max_length=50, null=True, blank=True)
    numero = models.CharField(max_length=10, null=True, blank=True)
    complemento = models.CharField(max_length=100, null=True, blank=True)
    cep = models.CharField(max_length=10, null=True, blank=True, validators=[cep_validator])
    bairro = models.CharField(max_length=30, null=True, blank=True)
    cidade = models.CharField(max_length=50, null=True, blank=True, db_index=True)
    estado = models.CharField(max_length=2, null=True, blank=True, db_index=True)
    telefone1 = models.CharField(max_length=15, null=True, blank=True, validators=[telefone_br_validator])
    telefone2 = models.CharField(max_length=15, null=True, blank=True, validators=[telefone_br_validator])
    email = models.CharField(max_length=50, null=True, blank=True, validators=[email_simple_validator])
    categoria = models.CharField(max_length=15, null=True, blank=True, db_index=True)
    bloqueio = models.BooleanField(default=False, db_index=True)
    aniversario = models.DateField(null=True, blank=True, db_index=True)
    mala_direta = models.BooleanField(default=False, db_index=True)
    conta_contabil = models.CharField(max_length=50, null=True, blank=True)
    ativo = models.BooleanField(default=True, db_index=True)
    data_cadastro = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["cpf"]),
            models.Index(fields=["cidade", "estado"]),
            models.Index(fields=["categoria"]),
            models.Index(fields=["bloqueio"]),
            models.Index(fields=["mala_direta"]),
            models.Index(fields=["ativo"]),
            models.Index(fields=["data_cadastro"]),
        ]

    def __str__(self):
        return self.nome_cliente


class Fornecedor(models.Model):
    CATEGORIA_CHOICES = (
        ("MATERIA_PRIMA", "Matéria-prima"),
        ("AVIAMENTO", "Aviamento"),
        ("REVENDA", "Produto de revenda"),
        ("FACCAO", "Facção"),
        ("PRESTADOR", "Prestador de serviço"),
        ("TRANSPORTADORA", "Transportadora"),
        ("OUTROS", "Outros"),
    )

    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fornecedores",
        db_index=True,
    )
    nome_fornecedor = models.CharField(max_length=50, db_index=True)
    apelido = models.CharField(max_length=18, null=True, blank=True, db_index=True)
    cnpj = models.CharField(max_length=18, validators=[cnpj_validator])
    logradouro = models.CharField(max_length=50, null=True, blank=True)
    endereco = models.CharField(max_length=50, null=True, blank=True)
    numero = models.CharField(max_length=10, null=True, blank=True)
    complemento = models.CharField(max_length=100, null=True, blank=True)
    cep = models.CharField(max_length=10, null=True, blank=True, validators=[cep_validator])
    bairro = models.CharField(max_length=30, null=True, blank=True)
    cidade = models.CharField(max_length=50, null=True, blank=True, db_index=True)
    estado = models.CharField(max_length=2, null=True, blank=True, db_index=True)
    telefone1 = models.CharField(max_length=15, null=True, blank=True, validators=[telefone_br_validator])
    telefone2 = models.CharField(max_length=15, null=True, blank=True, validators=[telefone_br_validator])
    email = models.CharField(max_length=50, null=True, blank=True, validators=[email_simple_validator])
    categoria = models.CharField(max_length=15, choices=CATEGORIA_CHOICES, null=True, blank=True, db_index=True)
    bloqueio = models.BooleanField(default=False, db_index=True)
    mala_direta = models.BooleanField(default=False, db_index=True)
    conta_contabil = models.CharField(max_length=50, null=True, blank=True)
    ativo = models.BooleanField(default=True, db_index=True)
    data_cadastro = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["empresa", "cnpj"], name="uq_empresa_fornecedor_cnpj")
        ]
        indexes = [
            models.Index(fields=["cnpj"]),
            models.Index(fields=["cidade", "estado"]),
            models.Index(fields=["categoria"]),
            models.Index(fields=["bloqueio"]),
            models.Index(fields=["mala_direta"]),
            models.Index(fields=["ativo"]),
            models.Index(fields=["data_cadastro"]),
        ]

    def __str__(self):
        return self.nome_fornecedor


class Funcionarios(models.Model):
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="funcionarios",
        db_index=True,
    )
    nomefuncionario = models.CharField(max_length=50, db_index=True)
    apelido = models.CharField(max_length=20, null=True, blank=True, db_index=True)
    cpf = models.CharField(max_length=15, null=True, blank=True, validators=[cpf_validator], db_index=True)
    inicio = models.DateField(null=True, blank=True, db_index=True)
    fim = models.DateField(null=True, blank=True, db_index=True)
    categoria = models.CharField(max_length=15, null=True, blank=True, db_index=True)
    meta = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, db_index=True)
    comissao_percentual = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    salario = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    idloja = models.ForeignKey(Loja, on_delete=models.CASCADE, null=True, blank=True, related_name='funcionarios', db_index=True)
    ativo = models.BooleanField(default=True, db_index=True)
    data_cadastro = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["idloja"]),
            models.Index(fields=["categoria"]),
            models.Index(fields=["ativo"]),
            models.Index(fields=["data_cadastro"]),
        ]

    def __str__(self):
        return self.nomefuncionario

class Nat_Lancamento(models.Model):
    idnatureza = models.BigAutoField(primary_key=True)
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="naturezas_lancamento",
        db_index=True,
    )
    codigo = models.CharField(max_length=10)
    categoria_principal = models.CharField(max_length=50)
    subcategoria = models.CharField(max_length=50)
    descricao = models.CharField(max_length=255)
    tipo = models.CharField(max_length=20)
    status = models.CharField(max_length=10)
    tipo_natureza = models.CharField(max_length=10)
    natureza_operacao = models.CharField(max_length=20, default="DESPESA", db_index=True)
    categoria_gerencial = models.CharField(max_length=50, null=True, blank=True, db_index=True)
    movimenta_financeiro = models.BooleanField(default=True, db_index=True)
    entra_dre = models.BooleanField(default=True, db_index=True)
    plano_contabil = models.ForeignKey(
        "cadastros.PlanoContabil",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="naturezas_lancamento",
    )
    conta_contabil = models.CharField(max_length=50, null=True, blank=True)
    ativo = models.BooleanField(default=True, db_index=True)

    def __str__(self):
        return f"ID: {self.idnatureza}, Código: {self.codigo}, categoria: {self.categoria_principal}"


class PlanoContabil(models.Model):
    CLASSE_ATIVO = "ATIVO"
    CLASSE_PASSIVO = "PASSIVO"
    CLASSE_PATRIMONIO = "PATRIMONIO"
    CLASSE_RECEITA = "RECEITA"
    CLASSE_CUSTO = "CUSTO"
    CLASSE_DESPESA = "DESPESA"
    CLASSE_RESULTADO = "RESULTADO"
    CLASSE_CHOICES = [
        (CLASSE_ATIVO, "Ativo"),
        (CLASSE_PASSIVO, "Passivo"),
        (CLASSE_PATRIMONIO, "Patrimônio líquido"),
        (CLASSE_RECEITA, "Receita"),
        (CLASSE_CUSTO, "Custo"),
        (CLASSE_DESPESA, "Despesa"),
        (CLASSE_RESULTADO, "Resultado"),
    ]

    NATUREZA_DEBITO = "DEBITO"
    NATUREZA_CREDITO = "CREDITO"
    NATUREZA_CHOICES = [
        (NATUREZA_DEBITO, "Débito"),
        (NATUREZA_CREDITO, "Crédito"),
    ]

    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.PROTECT,
        related_name="plano_contabil",
        db_index=True,
    )
    codigo = models.CharField(max_length=30)
    descricao = models.CharField(max_length=160)
    classe = models.CharField(max_length=20, choices=CLASSE_CHOICES, db_index=True)
    natureza = models.CharField(max_length=10, choices=NATUREZA_CHOICES)
    conta_pai = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="subcontas",
    )
    nivel = models.PositiveSmallIntegerField(default=1)
    analitica = models.BooleanField(default=True, db_index=True)
    ativa = models.BooleanField(default=True, db_index=True)
    data_cadastro = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["codigo"]
        constraints = [
            models.UniqueConstraint(fields=["empresa", "codigo"], name="uq_empresa_plano_contabil_codigo")
        ]
        indexes = [
            models.Index(fields=["empresa", "codigo"]),
            models.Index(fields=["empresa", "classe"]),
            models.Index(fields=["empresa", "ativa"]),
        ]

    def __str__(self):
        return f"{self.codigo} - {self.descricao}"
