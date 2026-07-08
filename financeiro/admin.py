from django.contrib import admin
from .models import (
    Caixa, ContaBancaria, MovimentacaoFinanceira, LancamentoContabil,
    CashbackConfig, CashbackMovimento,
    ValeTroca, ValeTrocaMovimento,
    Pagar, PagarItem, PagarRateio,
    Receber, ReceberItem, ReceberRateio,
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


class ReceberRateioInline(admin.TabularInline):
    model = ReceberRateio
    extra = 0
    fields = ("Idnatureza", "valor", "centro_custo")
    show_change_link = True


@admin.register(Caixa)
class CaixaAdmin(admin.ModelAdmin):
    list_display = ("Idcaixa", "tipo_caixa", "idloja", "codigo", "descricao", "saldo_atual", "ativo")
    list_filter = ("idloja", "ativo")
    search_fields = ("codigo", "descricao")
    readonly_fields = ("data_cadastro",)


@admin.register(ContaBancaria)
class ContaBancariaAdmin(admin.ModelAdmin):
    list_display = ("Idconta", "idloja", "banco", "agencia", "conta", "tipo_conta", "saldo_atual", "ativo")
    list_filter = ("idloja", "tipo_conta", "ativo")
    search_fields = ("descricao", "banco", "agencia", "conta")
    readonly_fields = ("data_cadastro",)


@admin.register(MovimentacaoFinanceira)
class MovimentacaoFinanceiraAdmin(admin.ModelAdmin):
    list_display = ("Idmovimentacao", "idloja", "data_movimento", "tipo", "status", "valor", "historico", "caixa", "conta_bancaria")
    list_filter = ("idloja", "tipo", "status", "origem", "data_movimento")
    search_fields = ("historico", "documento")
    date_hierarchy = "data_movimento"
    readonly_fields = ("data_cadastro",)


@admin.register(LancamentoContabil)
class LancamentoContabilAdmin(admin.ModelAdmin):
    list_display = (
        "Idlancamentocontabil", "empresa", "idloja", "data_lancamento",
        "documento", "origem", "valor", "status",
    )
    list_filter = ("empresa", "idloja", "origem", "status", "data_lancamento")
    search_fields = ("documento", "historico", "observacao")
    date_hierarchy = "data_lancamento"
    readonly_fields = ("data_cadastro",)


class ReceberItemInline(admin.TabularInline):
    model = ReceberItem
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


@admin.register(Receber)
class ReceberAdmin(admin.ModelAdmin):
    list_display = ("Idreceber", "idloja", "idcliente", "Titulo", "Data_emissao", "Valor_total", "Previsao", "FormaPagamento")
    list_filter = ("idloja", "idcliente", "Previsao", "Data_emissao", "FormaPagamento")
    search_fields = ("Idreceber", "Titulo", "Documento")
    date_hierarchy = "Data_emissao"
    readonly_fields = ("data_cadastro",)
    inlines = [ReceberItemInline]


@admin.register(ReceberItem)
class ReceberItemAdmin(admin.ModelAdmin):
    list_display = ("Idreceberitem", "Idreceber", "parcela_n", "status", "Data_vencimento", "valor_parcela", "data_baixa", "valor_baixa")
    list_filter = ("status", "Data_vencimento", "Idreceber__idloja", "Idreceber__idcliente")
    search_fields = ("Idreceberitem", "Idreceber__Idreceber")
    readonly_fields = ("data_cadastro",)
    inlines = [ReceberRateioInline]


@admin.register(ReceberRateio)
class ReceberRateioAdmin(admin.ModelAdmin):
    list_display = ("Idrateio", "Idreceberitem", "Idnatureza", "valor", "centro_custo")
    list_filter = ("Idnatureza",)
    search_fields = ("Idrateio", "Idreceberitem__Idreceberitem")

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


@admin.register(CashbackConfig)
class CashbackConfigAdmin(admin.ModelAdmin):
    list_display = ("Idcashbackconfig", "nome", "ativo", "percentual", "validade_dias", "limite_uso_percentual")
    list_filter = ("ativo", "consumidor_final_participa")
    search_fields = ("nome",)
    readonly_fields = ("criado_em", "atualizado_em")


@admin.register(CashbackMovimento)
class CashbackMovimentoAdmin(admin.ModelAdmin):
    list_display = ("Idcashbackmovimento", "cliente", "tipo", "status", "valor", "validade", "criado_em")
    list_filter = ("tipo", "status", "validade")
    search_fields = ("cliente__nome_cliente", "observacao", "venda_origem__documento", "venda_uso__documento")
    readonly_fields = ("criado_em",)


class ValeTrocaMovimentoInline(admin.TabularInline):
    model = ValeTrocaMovimento
    extra = 0
    fields = ("tipo", "valor", "saldo_apos", "venda_uso", "observacao", "criado_em")
    readonly_fields = ("criado_em",)
    show_change_link = True


@admin.register(ValeTroca)
class ValeTrocaAdmin(admin.ModelAdmin):
    list_display = ("Idvaletroca", "documento", "cliente", "loja", "valor_original", "saldo", "status", "criado_em")
    list_filter = ("status", "loja", "validade")
    search_fields = ("documento", "cliente__nome_cliente", "devolucao__documento", "devolucao__venda__documento")
    readonly_fields = ("criado_em", "atualizado_em")
    inlines = [ValeTrocaMovimentoInline]


@admin.register(ValeTrocaMovimento)
class ValeTrocaMovimentoAdmin(admin.ModelAdmin):
    list_display = ("Idvaletrocamov", "vale", "tipo", "valor", "saldo_apos", "venda_uso", "criado_em")
    list_filter = ("tipo", "criado_em")
    search_fields = ("vale__documento", "venda_uso__documento", "observacao")
    readonly_fields = ("criado_em",)
