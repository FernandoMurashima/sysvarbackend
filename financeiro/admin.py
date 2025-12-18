from django.contrib import admin
from .models import (
    Pagar, PagarItem, PagarRateio,
    FormaPagamento, FormaPagamentoParcela
)

class PagarRateioInline(admin.TabularInline):
    model = PagarRateio
    extra = 0
    fields = ("Idnatureza", "valor", "centro_custo")
    show_change_link = True

class PagarItemInline(admin.TabularInline):
    model = PagarItem
    extra = 0
    fields = (
        "parcela_n", "status",
        "Data_vencimento", "valor_parcela",
        "FormaPagamento", "idconta",
        "juros", "desconto",
        "data_baixa", "valor_baixa",
        "Previsao", "Idnatureza",
    )
    show_change_link = True

@admin.register(Pagar)
class PagarAdmin(admin.ModelAdmin):
    list_display = ("Idpagar", "idloja", "idfornecedor", "Titulo", "Data_emissao", "Valor_total", "Previsao", "FormaPagamento")
    list_filter = ("idloja", "idfornecedor", "Previsao", "Data_emissao", "FormaPagamento")
    search_fields = ("Idpagar", "Titulo", "Documento")
    date_hierarchy = "Data_emissao"
    readonly_fields = ("data_cadastro",)
    inlines = [PagarItemInline]
    fieldsets = (
        ("Identificação", {"fields": ("idloja", "idfornecedor", "Titulo", "Documento")}),
        ("Datas & Valores", {"fields": ("Data_emissao", "Valor_total")}),
        ("Pagamento", {"fields": ("Previsao", "FormaPagamento")}),
        ("Classificação", {"fields": ("Idnatureza", "conta_contabil")}),
        ("Vínculos", {"fields": ("pedido_compra", "nfe_id")}),
        ("Auditoria", {"fields": ("data_cadastro",)}),
    )

@admin.register(PagarItem)
class PagarItemAdmin(admin.ModelAdmin):
    list_display = ("Idpagaritem", "Idpagar", "parcela_n", "status", "Data_vencimento", "valor_parcela", "data_baixa", "valor_baixa")
    list_filter = ("status", "Data_vencimento", "Idpagar__idloja", "Idpagar__idfornecedor")
    search_fields = ("Idpagaritem", "Idpagar__Idpagar")
    readonly_fields = ("data_cadastro",)
    inlines = [PagarRateioInline]
    fieldsets = (
        ("Vínculo", {"fields": ("Idpagar", "parcela_n", "status")}),
        ("Vencimento", {"fields": ("Data_vencimento", "valor_parcela")}),
        ("Pagamento", {"fields": ("FormaPagamento", "idconta", "juros", "desconto")}),
        ("Baixa", {"fields": ("data_baixa", "valor_baixa")}),
        ("Classificação", {"fields": ("Previsao", "Idnatureza")}),
        ("Auditoria", {"fields": ("data_cadastro",)}),
    )

@admin.register(PagarRateio)
class PagarRateioAdmin(admin.ModelAdmin):
    list_display = ("Idrateio", "Idpagaritem", "Idnatureza", "valor", "centro_custo")
    list_filter = ("Idnatureza",)
    search_fields = ("Idrateio", "Idpagaritem__Idpagaritem")

class FormaPagamentoParcelaInline(admin.TabularInline):
    model = FormaPagamentoParcela
    extra = 0
    fields = ("ordem", "dias", "percentual", "valor_fixo", "data_cadastro")
    readonly_fields = ("data_cadastro",)
    show_change_link = True

@admin.register(FormaPagamento)
class FormaPagamentoAdmin(admin.ModelAdmin):
    list_display = ("Idformapagamento", "codigo", "descricao", "num_parcelas", "ativo", "data_cadastro")
    list_filter = ("ativo",)
    search_fields = ("codigo", "descricao")
    readonly_fields = ("data_cadastro",)
    inlines = [FormaPagamentoParcelaInline]

@admin.register(FormaPagamentoParcela)
class FormaPagamentoParcelaAdmin(admin.ModelAdmin):
    list_display = ("Idformapagparcela", "forma", "ordem", "dias", "percentual", "valor_fixo", "data_cadastro")
    list_filter = ("forma",)
    search_fields = ("forma__codigo", "forma__descricao")
    readonly_fields = ("data_cadastro",)
