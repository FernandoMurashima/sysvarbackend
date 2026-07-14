from django.contrib import admin
from .models import (
    ConfigEan, Ncm, Grade, Tamanho, Cor, Material, Colecao, Unidade,
    Grupo, Subgrupo, Tabelapreco, Codigos, Produto, ProdutoDetalhe,
    TabelaprecoProduto, FichaTecnica, FichaTecnicaItem, OrdemProducao, OrdemProducaoItem, OrdemProducaoGrade,
    Promocao, Pack, PackItem, Estoque
)

@admin.register(ConfigEan)
class ConfigEanAdmin(admin.ModelAdmin):
    list_display = ('country_prefix', 'company_prefix', 'atualizado_em')

@admin.register(Ncm)
class NcmAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'ncm', 'descricao', 'categoria', 'aliquota', 'ativo')
    list_filter = ('empresa', 'categoria', 'ativo')
    search_fields = ('ncm', 'descricao')

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
    list_display = ('Idcolecao', 'empresa', 'Descricao', 'Codigo', 'Estacao', 'Status', 'Contador')
    list_filter = ('empresa', 'Status', 'Estacao')

@admin.register(Unidade)
class UnidadeAdmin(admin.ModelAdmin):
    list_display = ('Idunidade', 'Descricao', 'Codigo')

@admin.register(Grupo)
class GrupoAdmin(admin.ModelAdmin):
    list_display = ('Idgrupo', 'empresa', 'Codigo', 'Descricao', 'Margem')
    list_filter = ('empresa',)

@admin.register(Subgrupo)
class SubgrupoAdmin(admin.ModelAdmin):
    list_display = ('Idsubgrupo', 'empresa', 'Idgrupo', 'Descricao', 'Margem')
    list_filter = ('empresa', 'Idgrupo')

@admin.register(Tabelapreco)
class TabelaprecoAdmin(admin.ModelAdmin):
    list_display = ('Idtabela', 'empresa', 'NomeTabela', 'DataInicio', 'DataFim', 'Promocao')
    list_filter = ('empresa', 'Promocao')

@admin.register(Codigos)
class CodigosAdmin(admin.ModelAdmin):
    list_display = ('Idcodigo', 'colecao', 'estacao', 'valor_var')

@admin.register(Produto)
class ProdutoAdmin(admin.ModelAdmin):
    list_display = ('Idproduto', 'empresa', 'descricao', 'tipo_produto', 'referencia', 'unidade', 'ncm', 'ativo')
    list_filter = ('empresa', 'tipo_produto', 'ativo', 'grupo', 'subgrupo', 'colecao')
    search_fields = ('Idproduto', 'descricao', 'referencia')

@admin.register(ProdutoDetalhe)
class ProdutoDetalheAdmin(admin.ModelAdmin):
    list_display = ('IdprodutoDetalhe', 'produto', 'idcor', 'idtamanho', 'codigo_item_ref', 'ean13', 'ativo')
    search_fields = ('ean13', 'codigo_item_ref')

@admin.register(TabelaprecoProduto)
class TabelaprecoProdutoAdmin(admin.ModelAdmin):
    list_display = ('Idprodutopreco', 'produto', 'tabela', 'preco', 'preco_promocional', 'DataInicio', 'DataFim', 'ativo')


@admin.register(FichaTecnica)
class FichaTecnicaAdmin(admin.ModelAdmin):
    list_display = ('id', 'empresa', 'produto_final', 'versao', 'status', 'ativa', 'data_cadastro')
    list_filter = ('empresa', 'status', 'ativa')
    search_fields = ('produto_final__descricao', 'produto_final__referencia', 'versao')


@admin.register(FichaTecnicaItem)
class FichaTecnicaItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'ficha', 'tipo', 'produto', 'fornecedor', 'quantidade', 'perda_percentual', 'custo_unitario_previsto')
    list_filter = ('tipo',)
    search_fields = ('produto__descricao', 'fornecedor__nome_fornecedor', 'descricao')


@admin.register(OrdemProducao)
class OrdemProducaoAdmin(admin.ModelAdmin):
    list_display = ('id', 'empresa', 'numero', 'produto_final', 'quantidade', 'status', 'custo_previsto', 'custo_real', 'data_emissao')
    list_filter = ('empresa', 'status', 'data_emissao')
    search_fields = ('numero', 'produto_final__descricao', 'produto_final__referencia')


@admin.register(OrdemProducaoItem)
class OrdemProducaoItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'ordem', 'tipo', 'produto', 'fornecedor', 'quantidade_necessaria', 'custo_unitario_previsto', 'custo_total_previsto')
    list_filter = ('tipo',)
    search_fields = ('ordem__numero', 'produto__descricao', 'fornecedor__nome_fornecedor', 'descricao')


@admin.register(OrdemProducaoGrade)
class OrdemProducaoGradeAdmin(admin.ModelAdmin):
    list_display = ('id', 'ordem', 'sku_final', 'quantidade')
    search_fields = ('ordem__numero', 'sku_final__ean13', 'sku_final__produto__descricao')


@admin.register(Promocao)
class PromocaoAdmin(admin.ModelAdmin):
    list_display = ('Idpromocao', 'empresa', 'nome', 'ativo', 'escopo', 'tipo', 'valor', 'data_inicio', 'data_fim', 'prioridade')
    list_filter = ('empresa', 'ativo', 'escopo', 'tipo', 'data_inicio')
    search_fields = ('nome', 'observacao')
    filter_horizontal = ('lojas', 'produtos', 'colecoes', 'grupos', 'subgrupos')

@admin.register(Pack)
class PackAdmin(admin.ModelAdmin):
    list_display = ('id', 'empresa', 'nome', 'grade', 'ativo', 'data_cadastro', 'atualizado_em')
    list_filter = ('empresa', 'ativo')

@admin.register(PackItem)
class PackItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'pack', 'tamanho', 'qtd')

@admin.register(Estoque)
class EstoqueAdmin(admin.ModelAdmin):
    list_display = ('Idestoque', 'CodigodeBarra', 'Idloja', 'Estoque', 'reserva')
    search_fields = ('CodigodeBarra',)
    list_filter = ('Idloja',)
