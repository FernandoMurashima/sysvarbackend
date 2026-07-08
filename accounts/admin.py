# accounts/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from .models import User

@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    # mostra colunas úteis
    list_display = ('username', 'email', 'first_name', 'last_name', 'type', 'empresa', 'loja', 'lojas_permitidas_count', 'is_staff')
    list_filter = ('type', 'empresa', 'loja', 'is_staff', 'is_superuser', 'is_active', 'groups')
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Informações pessoais', {'fields': ('first_name', 'last_name', 'email')}),
        ('Permissões', {'fields': ('type', 'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Datas importantes', {'fields': ('last_login', 'date_joined')}),
        ('Vínculos', {'fields': ('empresa', 'loja', 'lojas')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'password1', 'password2', 'type', 'email', 'first_name', 'last_name', 'empresa', 'loja'),
        }),
    )
    search_fields = ('username', 'first_name', 'last_name', 'email')
    ordering = ('username',)
    filter_horizontal = ('groups', 'user_permissions', 'lojas')

    def lojas_permitidas_count(self, obj):
        return obj.lojas.count()
    lojas_permitidas_count.short_description = 'Lojas'
