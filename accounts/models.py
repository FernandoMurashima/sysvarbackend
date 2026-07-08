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
