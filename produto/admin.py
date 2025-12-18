from django.contrib import admin
from .models import (
    ConfigEan, Ncm, Grade, Tamanho, Cor, Material, Colecao, Unidade,
    Grupo, Subgrupo, Tabelapreco, Codigos, Produto, ProdutoDetalhe,
    TabelaprecoProduto, Pack, PackItem, Estoque
)

@admin.register(ConfigEan)
class ConfigEanAdmin(admin.ModelAdmin):
    list_display = ('country_prefix', 'company_prefix', 'atualizado_em')

@admin.register(Ncm)
class NcmAdmin(admin.ModelAdmin):
    list_display = ('ncm', 'descricao', 'aliquota')

@admin.register(Grade)
class GradeAdmin(admin.ModelAdmin):
    list_display = ('Idgrade', 'Descricao', 'Status', 'data_cadastro')

@admin.register(Tamanho)
class TamanhoAdmin(admin.ModelAdmin):
    list_display = ('Idtamanho', 'idgrade', 'Tamanho', 'Descricao', 'Status')

@admin.register(Cor)
class CorAdmin(admin.ModelAdmin):
    list_display = ('Idcor', 'Descricao', 'Codigo', 'Cor', 'Status')

@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ('Idmaterial', 'Descricao', 'Codigo', 'Status')

@admin.register(Colecao)
class ColecaoAdmin(admin.ModelAdmin):
    list_display = ('Idcolecao', 'Descricao', 'Codigo', 'Estacao', 'Status', 'Contador')

@admin.register(Unidade)
class UnidadeAdmin(admin.ModelAdmin):
    list_display = ('Idunidade', 'Descricao', 'Codigo')

@admin.register(Grupo)
class GrupoAdmin(admin.ModelAdmin):
    list_display = ('Idgrupo', 'Codigo', 'Descricao', 'Margem')

@admin.register(Subgrupo)
class SubgrupoAdmin(admin.ModelAdmin):
    list_display = ('Idsubgrupo', 'Idgrupo', 'Descricao', 'Margem')

@admin.register(Tabelapreco)
class TabelaprecoAdmin(admin.ModelAdmin):
    list_display = ('Idtabela', 'NomeTabela', 'DataInicio', 'DataFim', 'Promocao')

@admin.register(Codigos)
class CodigosAdmin(admin.ModelAdmin):
    list_display = ('Idcodigo', 'colecao', 'estacao', 'valor_var')

@admin.register(Produto)
class ProdutoAdmin(admin.ModelAdmin):
    list_display = ('Idproduto', 'descricao', 'tipo_produto', 'referencia', 'unidade', 'ncm', 'ativo')
    list_filter = ('tipo_produto', 'ativo', 'grupo', 'subgrupo', 'colecao')
    search_fields = ('Idproduto', 'descricao', 'referencia')

@admin.register(ProdutoDetalhe)
class ProdutoDetalheAdmin(admin.ModelAdmin):
    list_display = ('IdprodutoDetalhe', 'produto', 'idcor', 'idtamanho', 'codigo_item_ref', 'ean13', 'ativo')
    search_fields = ('ean13', 'codigo_item_ref')

@admin.register(TabelaprecoProduto)
class TabelaprecoProdutoAdmin(admin.ModelAdmin):
    list_display = ('Idprodutopreco', 'produto', 'tabela', 'preco', 'preco_promocional', 'DataInicio', 'DataFim', 'ativo')

@admin.register(Pack)
class PackAdmin(admin.ModelAdmin):
    list_display = ('id', 'nome', 'grade', 'ativo', 'data_cadastro', 'atualizado_em')

@admin.register(PackItem)
class PackItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'pack', 'tamanho', 'qtd')

@admin.register(Estoque)
class EstoqueAdmin(admin.ModelAdmin):
    list_display = ('Idestoque', 'CodigodeBarra', 'Idloja', 'Estoque', 'reserva')
    search_fields = ('CodigodeBarra',)
    list_filter = ('Idloja',)
