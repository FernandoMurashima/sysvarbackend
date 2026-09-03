from django.contrib import admin

from .models import (
    Cfop,
    AgenteLocalSysvar,
    AtivacaoAgenteLocalSysvar,
    ConfiguracaoXmlFornecedor,
    FormaPagamentoFiscalMap,
    NFCe,
    NFeDevolucao,
    NotaFiscalEntrada,
    NotaFiscalEntradaEvento,
    NotaFiscalEntradaItem,
    VendaDevolucao,
    VendaDevolucaoItem,
    VendaPdv,
    VendaPdvItem,
    VendaPdvPagamento,
    RegraTributaria,
    Tributo,
)


@admin.register(AgenteLocalSysvar)
class AgenteLocalSysvarAdmin(admin.ModelAdmin):
    list_display = ("id", "empresa", "identificador", "nome", "ativo", "token_prefixo", "ultimo_contato", "versao", "hostname")
    list_filter = ("empresa", "ativo")
    search_fields = ("identificador", "nome", "hostname", "empresa__nome")
    readonly_fields = ("token_hash", "token_prefixo", "ultimo_contato", "criado_em", "atualizado_em")


@admin.register(AtivacaoAgenteLocalSysvar)
class AtivacaoAgenteLocalSysvarAdmin(admin.ModelAdmin):
    list_display = ("id", "empresa", "codigo_prefixo", "criado_por", "criado_em", "expira_em", "usado_em", "revogado_em", "agente")
    list_filter = ("empresa", "criado_em", "expira_em", "usado_em", "revogado_em")
    search_fields = ("codigo_prefixo", "empresa__nome", "agente__identificador", "agente__hostname")
    readonly_fields = ("codigo_hash", "codigo_prefixo", "criado_em", "usado_em", "agente")


@admin.register(Cfop)
class CfopAdmin(admin.ModelAdmin):
    list_display = ("empresa", "codigo", "descricao", "tipo_operacao", "destino", "ativo")
    list_filter = ("empresa", "tipo_operacao", "destino", "ativo")
    search_fields = ("codigo", "descricao", "empresa__nome", "empresa__nome_fantasia")


@admin.register(Tributo)
class TributoAdmin(admin.ModelAdmin):
    list_display = ("empresa", "codigo", "descricao", "esfera", "atual", "ativo")
    list_filter = ("empresa", "esfera", "atual", "ativo")
    search_fields = ("codigo", "descricao", "empresa__nome", "empresa__nome_fantasia")


@admin.register(RegraTributaria)
class RegraTributariaAdmin(admin.ModelAdmin):
    list_display = ("empresa", "nome", "tributo", "cfop", "ncm", "tipo_operacao", "regime_tributario", "aliquota", "ativo")
    list_filter = ("empresa", "tributo", "tipo_operacao", "regime_tributario", "ativo")
    search_fields = ("nome", "tributo__codigo", "cfop__codigo", "ncm__ncm")


class NotaFiscalEntradaItemInline(admin.TabularInline):
    model = NotaFiscalEntradaItem
    extra = 0


@admin.register(NotaFiscalEntrada)
class NotaFiscalEntradaAdmin(admin.ModelAdmin):
    list_display = ("id", "pedido_compra", "modelo", "serie", "numero", "status", "situacao_fiscal", "ambiente", "dt_emissao", "valor_total")
    list_filter = ("status", "situacao_fiscal", "ambiente", "modelo", "dt_emissao", "dt_entrada")
    search_fields = ("numero", "serie", "chave_acesso", "pedido_compra__id")
    inlines = [NotaFiscalEntradaItemInline]


@admin.register(NotaFiscalEntradaItem)
class NotaFiscalEntradaItemAdmin(admin.ModelAdmin):
    list_display = ("id", "nota", "pedido_item", "qtd_recebida", "preco_unit_nf", "total_item")
    search_fields = ("nota__numero", "pedido_item__id")


@admin.register(NotaFiscalEntradaEvento)
class NotaFiscalEntradaEventoAdmin(admin.ModelAdmin):
    list_display = ("id", "nota", "tipo_evento", "sequencia", "protocolo", "cstat", "ambiente", "criado_em")
    list_filter = ("tipo_evento", "cstat", "ambiente", "origem", "situacao_processamento")
    search_fields = ("chave_acesso", "protocolo", "id_evento", "nota__numero")


@admin.register(FormaPagamentoFiscalMap)
class FormaPagamentoFiscalMapAdmin(admin.ModelAdmin):
    list_display = ("id", "empresa", "codigo_tpag", "descricao_fiscal", "forma_pagamento", "ativo")
    list_filter = ("empresa", "codigo_tpag", "ativo")
    search_fields = ("codigo_tpag", "descricao_fiscal", "forma_pagamento__codigo", "forma_pagamento__descricao")


@admin.register(ConfiguracaoXmlFornecedor)
class ConfiguracaoXmlFornecedorAdmin(admin.ModelAdmin):
    list_display = ("id", "empresa", "loja", "caminho_local", "ativo", "identificador_agente")
    list_filter = ("empresa", "loja", "ativo")
    search_fields = ("caminho_local", "identificador_agente", "empresa__nome", "loja__nome_loja")


class VendaPdvItemInline(admin.TabularInline):
    model = VendaPdvItem
    extra = 0
    readonly_fields = ("total_item",)


class VendaPdvPagamentoInline(admin.TabularInline):
    model = VendaPdvPagamento
    extra = 0


@admin.register(VendaPdv)
class VendaPdvAdmin(admin.ModelAdmin):
    list_display = ("id", "documento", "loja", "cliente", "vendedor", "status", "total", "data_venda")
    list_filter = ("status", "loja", "forma_pagamento", "data_venda")
    search_fields = ("documento", "cliente__nome_cliente", "vendedor__nomefuncionario")
    inlines = [VendaPdvItemInline, VendaPdvPagamentoInline]


@admin.register(VendaPdvItem)
class VendaPdvItemAdmin(admin.ModelAdmin):
    list_display = ("id", "venda", "descricao", "ean", "quantidade", "preco_unitario", "total_item")
    search_fields = ("venda__documento", "descricao", "ean")


@admin.register(VendaPdvPagamento)
class VendaPdvPagamentoAdmin(admin.ModelAdmin):
    list_display = ("id", "venda", "forma", "descricao", "valor", "autorizacao")
    list_filter = ("forma",)
    search_fields = ("venda__documento", "forma", "descricao", "autorizacao")


class VendaDevolucaoItemInline(admin.TabularInline):
    model = VendaDevolucaoItem
    extra = 0
    readonly_fields = ("total_item",)


@admin.register(VendaDevolucao)
class VendaDevolucaoAdmin(admin.ModelAdmin):
    list_display = ("id", "documento", "venda", "loja", "cliente", "status", "credito_cliente", "criado_em")
    list_filter = ("status", "loja", "criado_em")
    search_fields = ("documento", "venda__documento", "cliente__nome_cliente")
    inlines = [VendaDevolucaoItemInline]


@admin.register(VendaDevolucaoItem)
class VendaDevolucaoItemAdmin(admin.ModelAdmin):
    list_display = ("id", "devolucao", "descricao", "ean", "quantidade", "preco_unitario", "total_item")
    search_fields = ("devolucao__documento", "descricao", "ean")


@admin.register(NFCe)
class NFCeAdmin(admin.ModelAdmin):
    list_display = ("id", "venda", "serie", "numero", "status", "retorno_codigo", "autorizada_em")
    list_filter = ("status", "ambiente", "serie")
    search_fields = ("venda__documento", "chave_acesso", "protocolo")


@admin.register(NFeDevolucao)
class NFeDevolucaoAdmin(admin.ModelAdmin):
    list_display = ("id", "devolucao", "serie", "numero", "status", "retorno_codigo", "autorizada_em")
    list_filter = ("status", "ambiente", "serie")
    search_fields = ("devolucao__documento", "chave_acesso", "protocolo")
