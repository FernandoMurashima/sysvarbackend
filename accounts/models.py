# accounts/models.py
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.core.exceptions import ValidationError
import uuid

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
    perfil_principal = models.ForeignKey(
        'accounts.PerfilAcesso',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='usuarios',
    )

    def __str__(self):
        return f"{self.username} ({self.type})"

    def clean(self):
        super().clean()
        if self.perfil_principal_id and self.empresa_id and self.perfil_principal.empresa_id != self.empresa_id:
            raise ValidationError({"perfil_principal": "Perfil principal deve pertencer à empresa do usuário."})


class UserModulePermission(models.Model):
    class Module(models.TextChoices):
        OPERACIONAL = "operacional", "Operacional"
        CADASTROS = "cadastros", "Cadastros"
        PRODUTOS = "produtos", "Produtos"
        FISCAL = "fiscal", "Fiscal"
        FISCAL_CONTABIL = "fiscal_contabil", "Fiscal e Contábil"
        ESTOQUE = "estoque", "Estoque"
        DISTRIBUICAO = "distribuicao", "Distribuição"
        VENDAS = "vendas", "Vendas"
        COMPRAS = "compras", "Compras"
        PRODUCAO = "producao", "Produção"
        FINANCEIRO = "financeiro", "Financeiro"
        RELATORIOS = "relatorios", "Relatórios"
        CONFIGURACOES = "configuracoes", "Configurações"
        AUDITORIA = "auditoria", "Auditoria"

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

    def clean(self):
        super().clean()
        if self.user_id and self.user.is_superuser:
            return
        if self.user_id and not self.user.empresa_id:
            raise ValidationError({"user": "Usuário sem empresa vinculada."})


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


class PerfilAcesso(models.Model):
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, related_name='perfis_acesso')
    nome = models.CharField(max_length=80)
    descricao = models.TextField(blank=True, default="")
    ativo = models.BooleanField(default=True, db_index=True)
    padrao = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["empresa_id", "nome"]
        constraints = [
            models.UniqueConstraint(fields=["empresa", "nome"], name="uq_perfil_acesso_empresa_nome"),
        ]
        indexes = [
            models.Index(fields=["empresa", "ativo"]),
            models.Index(fields=["padrao", "ativo"]),
        ]

    def __str__(self):
        return f"{self.empresa_id} - {self.nome}"

    def clean(self):
        super().clean()
        if self.padrao and self.ativo and self.empresa_id:
            qs = PerfilAcesso.objects.filter(empresa_id=self.empresa_id, ativo=True, padrao=True)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError({"padrao": "Já existe um perfil padrão ativo para esta empresa."})

    def delete(self, *args, **kwargs):
        if self.usuarios.exists():
            raise ValidationError("Perfil em uso não pode ser excluído; inative o perfil.")
        return super().delete(*args, **kwargs)


class PerfilModuloPermissao(models.Model):
    perfil = models.ForeignKey(PerfilAcesso, on_delete=models.CASCADE, related_name="permissoes_modulos")
    modulo = models.ForeignKey('cadastros.ModuloSistema', on_delete=models.PROTECT, related_name="permissoes_perfis")
    acesso = models.CharField(max_length=10, choices=UserModulePermission.Access.choices, default=UserModulePermission.Access.NONE, db_index=True)

    class Meta:
        ordering = ["perfil_id", "modulo__ordem"]
        constraints = [
            models.UniqueConstraint(fields=["perfil", "modulo"], name="uq_perfil_modulo_permission")
        ]

    def __str__(self):
        return f"{self.perfil_id} - {self.modulo.chave}: {self.acesso}"


class SessaoUsuario(models.Model):
    empresa = models.ForeignKey('cadastros.Empresa', on_delete=models.PROTECT, null=True, blank=True, related_name='sessoes_usuarios')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='sessoes_acesso')
    loja = models.ForeignKey('cadastros.Loja', on_delete=models.SET_NULL, null=True, blank=True, related_name='sessoes_usuarios')
    session_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    token_key_hash = models.CharField(max_length=128, unique=True, db_index=True)
    dispositivo_id = models.CharField(max_length=128, db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True, default="")
    iniciada_em = models.DateTimeField(auto_now_add=True)
    ultima_atividade_em = models.DateTimeField(db_index=True)
    encerrada_em = models.DateTimeField(null=True, blank=True)
    motivo_encerramento = models.CharField(max_length=50, blank=True, default="")
    ativa = models.BooleanField(default=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["empresa", "ativa"]),
            models.Index(fields=["usuario", "ativa"]),
            models.Index(fields=["dispositivo_id"]),
            models.Index(fields=["ultima_atividade_em"]),
            models.Index(fields=["empresa", "ativa", "ultima_atividade_em"]),
        ]


class SessionToken(models.Model):
    key_hash = models.CharField(max_length=128, unique=True, db_index=True)
    session = models.OneToOneField(SessaoUsuario, on_delete=models.CASCADE, related_name="session_token")
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True, db_index=True)
