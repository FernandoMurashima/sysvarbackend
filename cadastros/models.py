from django.db import models
from django.utils import timezone

from cadastros.validators import (
    cpf_validator,
    cnpj_validator,
    email_simple_validator,
    telefone_br_validator,
    cep_validator,
)

class Loja(models.Model):
    nome_loja = models.CharField(max_length=50, db_index=True)
    apelido_loja = models.CharField(max_length=20, db_index=True)
    cnpj = models.CharField(max_length=18, validators=[cnpj_validator], unique=True)

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
        indexes = [
            models.Index(fields=["cnpj"]),
            models.Index(fields=["cidade", "estado"]),
            models.Index(fields=["ativo"]),
            models.Index(fields=["data_cadastro"]),
        ]

    def __str__(self):
        return self.nome_loja


class Cliente(models.Model):
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
    nome_fornecedor = models.CharField(max_length=50, db_index=True)
    apelido = models.CharField(max_length=18, null=True, blank=True, db_index=True)
    cnpj = models.CharField(max_length=18, validators=[cnpj_validator], unique=True)
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
    nomefuncionario = models.CharField(max_length=50, db_index=True)
    apelido = models.CharField(max_length=20, null=True, blank=True, db_index=True)
    cpf = models.CharField(max_length=15, null=True, blank=True, validators=[cpf_validator], db_index=True)
    inicio = models.DateField(null=True, blank=True, db_index=True)
    fim = models.DateField(null=True, blank=True, db_index=True)
    categoria = models.CharField(max_length=15, null=True, blank=True, db_index=True)
    meta = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, db_index=True)
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
    codigo = models.CharField(max_length=10)
    categoria_principal = models.CharField(max_length=50)
    subcategoria = models.CharField(max_length=50)
    descricao = models.CharField(max_length=255)
    tipo = models.CharField(max_length=20)
    status = models.CharField(max_length=10)
    tipo_natureza = models.CharField(max_length=10)

    def __str__(self):
        return f"ID: {self.idnatureza}, Código: {self.codigo}, categoria: {self.categoria_principal}"