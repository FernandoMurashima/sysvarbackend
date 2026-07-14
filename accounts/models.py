# accounts/models.py
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings

# Obs.: FK para 'cadastros.Loja' — o app 'cadastros' terá o modelo Loja.
# Se ainda não existir a migração de Loja, tudo bem: a migration do accounts
# criará dependência automática para a de 'cadastros'.

class User(AbstractUser):
    class Type(models.TextChoices):
        REGULAR = 'Regular', 'Regular'
        VENDEDOR = 'Vendedor', 'Vendedor'
        CAIXA = 'Caixa', 'Caixa'
        GERENTE = 'Gerente', 'Gerente'
        DIRETOR = 'Diretor', 'Diretor'
        ADMIN = 'Admin', 'Admin'
        AUXILIAR = 'Auxiliar', 'Auxiliar'
        ASSISTENTE = 'Assistente', 'Assistente'
        ASSISTENTE_RECEBER = 'AssistenteReceber', 'Assistente contas a receber'
        ASSISTENTE_PAGAR = 'AssistentePagar', 'Assistente contas a pagar'

    type = models.CharField(max_length=20, choices=Type.choices, default=Type.REGULAR)
    # Empresa contratante/tenant do usuário no modelo SaaS.
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.SET_NULL, null=True, blank=True, related_name='usuarios')
    # Loja do usuário (opcional, pode ficar vazia)
    loja = models.ForeignKey('cadastros.Loja', on_delete=models.SET_NULL, null=True, blank=True, related_name='usuarios')
    lojas = models.ManyToManyField('cadastros.Loja', blank=True, related_name='usuarios_permitidos')

    def __str__(self):
        return f"{self.username} ({self.type})"


class UserModulePermission(models.Model):
    class Module(models.TextChoices):
        CADASTROS = "cadastros", "Cadastros"
        PRODUTOS = "produtos", "Produtos"
        FISCAL = "fiscal", "Fiscal"
        ESTOQUE = "estoque", "Estoque"
        VENDAS = "vendas", "Vendas"
        COMPRAS = "compras", "Compras"
        PRODUCAO = "producao", "Produção"
        FINANCEIRO = "financeiro", "Financeiro"
        RELATORIOS = "relatorios", "Relatórios"
        CONFIGURACOES = "configuracoes", "Configurações"

    class Access(models.TextChoices):
        NONE = "NONE", "Sem acesso"
        VIEW = "VIEW", "Somente consulta"
        EDIT = "EDIT", "Consulta e edição"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="module_permissions")
    modulo = models.CharField(max_length=30, choices=Module.choices, db_index=True)
    acesso = models.CharField(max_length=10, choices=Access.choices, default=Access.NONE, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "modulo"], name="uq_user_module_permission")
        ]
        ordering = ["user_id", "modulo"]

    def __str__(self):
        return f"{self.user_id} - {self.modulo}: {self.acesso}"


class UserFieldPermission(models.Model):
    class Field(models.TextChoices):
        FUNCIONARIO_SALARIO = "funcionario.salario", "Funcionário - salário"
        PRODUTO_CUSTO = "produto.custo", "Produto - custos e margens"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="field_permissions")
    campo = models.CharField(max_length=60, choices=Field.choices, db_index=True)
    pode_ver = models.BooleanField(default=False, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "campo"], name="uq_user_field_permission")
        ]
        ordering = ["user_id", "campo"]

    def __str__(self):
        return f"{self.user_id} - {self.campo}: {self.pode_ver}"
