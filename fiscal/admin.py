from django.contrib import admin

from .models import NFCe, NotaFiscalEntrada, NotaFiscalEntradaItem, VendaPdv, VendaPdvItem


class NotaFiscalEntradaItemInline(admin.TabularInline):
    model = NotaFiscalEntradaItem
    extra = 0


@admin.register(NotaFiscalEntrada)
class NotaFiscalEntradaAdmin(admin.ModelAdmin):
    list_display = ("id", "pedido_compra", "modelo", "serie", "numero", "status", "dt_emissao", "valor_total")
    list_filter = ("status", "modelo", "dt_emissao", "dt_entrada")
    search_fields = ("numero", "serie", "chave_acesso", "pedido_compra__id")
    inlines = [NotaFiscalEntradaItemInline]


@admin.register(NotaFiscalEntradaItem)
class NotaFiscalEntradaItemAdmin(admin.ModelAdmin):
    list_display = ("id", "nota", "pedido_item", "qtd_recebida", "preco_unit_nf", "total_item")
    search_fields = ("nota__numero", "pedido_item__id")


class VendaPdvItemInline(admin.TabularInline):
    model = VendaPdvItem
    extra = 0
    readonly_fields = ("total_item",)


@admin.register(VendaPdv)
class VendaPdvAdmin(admin.ModelAdmin):
    list_display = ("id", "documento", "loja", "cliente", "vendedor", "status", "total", "data_venda")
    list_filter = ("status", "loja", "forma_pagamento", "data_venda")
    search_fields = ("documento", "cliente__nome_cliente", "vendedor__nomefuncionario")
    inlines = [VendaPdvItemInline]


@admin.register(VendaPdvItem)
class VendaPdvItemAdmin(admin.ModelAdmin):
    list_display = ("id", "venda", "descricao", "ean", "quantidade", "preco_unitario", "total_item")
    search_fields = ("venda__documento", "descricao", "ean")


@admin.register(NFCe)
class NFCeAdmin(admin.ModelAdmin):
    list_display = ("id", "venda", "serie", "numero", "status", "retorno_codigo", "autorizada_em")
    list_filter = ("status", "ambiente", "serie")
    search_fields = ("venda__documento", "chave_acesso", "protocolo")
