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
        CAIXA = 'Caixa', 'Caixa'
        GERENTE = 'Gerente', 'Gerente'
        ADMIN = 'Admin', 'Admin'
        AUXILIAR = 'Auxiliar', 'Auxiliar'
        ASSISTENTE = 'Assistente', 'Assistente'

    type = models.CharField(max_length=10, choices=Type.choices, default=Type.REGULAR)
    # Loja do usuário (opcional, pode ficar vazia)
    loja = models.ForeignKey('cadastros.Loja', on_delete=models.SET_NULL, null=True, blank=True, related_name='usuarios')

    def __str__(self):
        return f"{self.username} ({self.type})"
