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
    nome = models.CharField(max_length=120, db_index=True)
    nome_fantasia = models.CharField(max_length=120, null=True, blank=True, db_index=True)
    documento = models.CharField(max_length=18, null=True, blank=True, unique=True)
    ativo = models.BooleanField(default=True, db_index=True)
    data_cadastro = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["nome"]),
            models.Index(fields=["nome_fantasia"]),
            models.Index(fields=["ativo"]),
        ]
        ordering = ["nome"]

    def __str__(self):
        return self.nome_fantasia or self.nome


class Loja(models.Model):
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
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
            models.Index(fields=["data_cadastro"]),
        ]

    def __str__(self):
        return self.nome_loja


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
    categoria = models.CharField(max_length=15, null=True, blank=True, db_index=True)
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
