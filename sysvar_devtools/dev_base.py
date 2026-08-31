
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
import json
from pathlib import Path
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from django.db import transaction
from django.db.models import Count
from accounts.models import PerfilAcesso, PerfilModuloPermissao, PerfilProcessPermission, SessaoUsuario, SessionToken, UserFieldPermission, UserModulePermission
from accounts.services.effective_access import sync_legacy_license_flags
from cadastros.models import *
from compras.models import *
from distribuicao.models import *
from financeiro.models import *
from fiscal.models.cfop import Cfop
from fiscal.models.nota_fiscal_entrada import FormaPagamentoFiscalMap, NotaFiscalEntrada, NotaFiscalEntradaDivergenciaXml, NotaFiscalEntradaEvento, NotaFiscalEntradaItem, NotaFiscalEntradaItemXml
from fiscal.models.nota_fiscal_saida import NotaFiscalSaida, NotaFiscalSaidaItem
from fiscal.models.tributacao import RegraTributaria, Tributo
from fiscal.models.venda_pdv import *
from produto.models import *
SEED_DIR=Path(__file__).resolve().parent/'seeds'
PRESERVED_SUPERUSER='takeshi'; DEV_PASSWORD='Sysvar@123'; DEV_COMPANY_DOCUMENT='42000001000186'; DEV_COMPANY_NAME='Sysvar Moda Comércio e Confecções Ltda'
FORBIDDEN_OPERATIONAL_MODELS=[('accounts','SessaoUsuario'),('accounts','SessionToken'),('compras','Requisicao'),('compras','RequisicaoItem'),('compras','RequisicaoHistorico'),('compras','OrdemServico'),('compras','Cotacao'),('compras','PedidoCompra'),('distribuicao','Distribuicao'),('distribuicao','PedidoVendaDistribuicao'),('distribuicao','MercadoriaTransito'),('financeiro','CashbackMovimento'),('financeiro','ValeTroca'),('financeiro','ValeTrocaMovimento'),('financeiro','MovimentacaoFinanceira'),('financeiro','Pagar'),('financeiro','PagarItem'),('financeiro','Receber'),('financeiro','ReceberItem'),('fiscal','NotaFiscalEntrada'),('fiscal','NotaFiscalSaida'),('fiscal','VendaPdv'),('fiscal','VendaDevolucao'),('fiscal','NFCe'),('produto','EstoqueMovimentacao'),('produto','ProdutoUsoConsumoMovimentacao'),('produto','InventarioEstoque'),('produto','OrdemProducao')]
@dataclass
class DevBaseReport:
    created:dict[str,int]=field(default_factory=dict); problems:list[str]=field(default_factory=list)
    def set(self,k,v): self.created[k]=int(v)
    @property
    def valid(self): return not self.problems
class SysvarDevBaseService:
    def __init__(self): self.report=DevBaseReport(); self.i={}; self.seed_files=sorted(p.name for p in SEED_DIR.glob('*.json'))
    def assert_not_production(self,destructive=False):
        if destructive and not getattr(settings,'DEBUG',False):
            n=str(settings.DATABASES['default'].get('NAME','')).lower()
            if not any(t in n for t in {'test','teste','dev','development','varejo_db'}): raise CommandError('Operação destrutiva bloqueada: ambiente não parece ser desenvolvimento.')
    def reset(self): return self.rebuild()
    def create(self,seed_globals=True):
        with transaction.atomic(): self.load_all()
        return self.validate()
    def rebuild(self):
        self.assert_not_production(True)
        with transaction.atomic(): self.delete_all(); self.load_all()
        return self.validate()
    def seed(self,f):
        with (SEED_DIR/f).open(encoding='utf-8') as h: return json.load(h)
    def fields(self,m): return {f.name for f in m._meta.fields if not f.primary_key and not getattr(f,'auto_now',False) and not getattr(f,'auto_now_add',False)}
    def clean(self,m,d): return {k:v for k,v in d.items() if k in self.fields(m)}
    def emp(self,c='EMP-DEV'):
        if 'emp' in self.i and c in self.i['emp']: return self.i['emp'][c]
        if c == 'EMP-DEV':
            return Empresa.objects.get(documento=DEV_COMPANY_DOCUMENT)
        raise KeyError(c)
    def load_all(self):
        self.modulos(); self.empresas(); self.lojas(); self.perfis(); self.usuarios(); self.contrato(); self.cargos(); self.plano(); self.naturezas(); self.centros_setores(); self.funcionarios(); self.fornecedores(); self.clientes(); self.financeiro(); self.prod_base(); self.produtos(); self.prod_deps(); self.estoque_estrutural(); self.fiscal(); self.requisicoes(); self.dist(); self.count()
    def modulos(self):
        self.i['mod']={}
        for it in self.seed('modulos_sistema.json'):
            f=it.get('fields',it); obj,_=ModuloSistema.objects.update_or_create(chave=f['chave'],defaults={k:f.get(k) for k in ['nome','descricao','categoria','basico','ordem','ativo','dependencias']}); self.i['mod'][obj.chave]=obj
    def empresas(self):
        self.i['emp']={}
        for f in self.seed('empresas.json'):
            d=dict(f); c=d.pop('codigo'); obj,_=Empresa.objects.update_or_create(documento=d['documento'],defaults=self.clean(Empresa,d)); self.i['emp'][c]=obj
    def lojas(self):
        self.i['loja']={}
        for f in self.seed('lojas.json'):
            d=dict(f); c=d.pop('codigo'); d['empresa']=self.emp(d.pop('empresa_codigo')); obj,_=Loja.objects.update_or_create(empresa=d['empresa'],apelido_loja=d['apelido_loja'],defaults=self.clean(Loja,d)); self.i['loja'][c]=obj
    def perfis(self):
        self.i['perfil_pk']={}; self.i['perfil']={}; mods={x['pk']:(x.get('fields',x))['chave'] for x in self.seed('modulos_sistema.json')}; emp=self.emp()
        for it in self.seed('perfis_acesso.json'):
            f=it['fields']; obj,_=PerfilAcesso.objects.update_or_create(empresa=emp,nome=f['nome'],defaults={'descricao':f.get('descricao',''),'ativo':f.get('ativo',True),'padrao':f.get('padrao',False)}); self.i['perfil_pk'][it['pk']]=obj; self.i['perfil'][obj.nome]=obj
        for it in self.seed('perfil_modulo_permissoes.json'):
            f=it['fields']; p=self.i['perfil_pk'].get(f['perfil']); m=self.i['mod'].get(mods[f['modulo']])
            if p and m: PerfilModuloPermissao.objects.update_or_create(perfil=p,modulo=m,defaults={'acesso':f.get('acesso','NONE'),'pode_excluir':f.get('pode_excluir',False)})
        for it in self.seed('perfil_processo_permissoes.json'):
            f=it['fields']; p=self.i['perfil_pk'].get(f['perfil'])
            if p: PerfilProcessPermission.objects.update_or_create(perfil=p,codigo=f['codigo'],defaults={'permitido':f.get('permitido',False)})
    def usuarios(self):
        User=get_user_model(); self.i['user']={}
        for f in self.seed('usuarios.json'):
            emp=self.i['emp'].get(f.get('empresa_codigo')); loja=self.i['loja'].get(f.get('loja_codigo')); perfil=self.i['perfil'].get(f.get('perfil_principal'))
            u,_=User.objects.update_or_create(username=f['username'],defaults={'first_name':f.get('first_name',''),'last_name':f.get('last_name',''),'email':f.get('email',''),'type':f.get('type','Regular'),'empresa':emp,'loja':loja,'perfil_principal':perfil,'is_staff':f.get('is_staff',False),'is_superuser':f.get('is_superuser',False),'is_active':f.get('is_active',True),'deve_trocar_senha':f.get('deve_trocar_senha',False)})
            u.set_password(f.get('password') or DEV_PASSWORD); u.save(); u.lojas.set([self.i['loja'][c] for c in f.get('lojas_permitidas',[])]); self.i['user'][u.username]=u
    def contrato(self):
        for f in self.seed('empresa_contrato.json'):
            d=dict(f); emp=self.emp(d.pop('empresa_codigo')); d['usuario_master']=self.i['user'].get(d.pop('usuario_master_username',None)); EmpresaContrato.objects.update_or_create(empresa=emp,defaults=self.clean(EmpresaContrato,d))
        for f in self.seed('empresa_modulos.json'):
            emp=self.emp(f['empresa_codigo'])
            if f.get('contratar_todos_ativos'):
                for m in ModuloSistema.objects.filter(ativo=True): EmpresaModulo.objects.update_or_create(empresa=emp,modulo=m,defaults={'contratado':f.get('contratado',True),'data_inicio':f.get('data_inicio'),'data_fim':f.get('data_fim')})
            sync_legacy_license_flags(emp)
    def cargos(self):
        self.i['cargo']={}
        for f in self.seed('cargos.json'):
            d=dict(f); d['empresa']=self.emp(d.pop('empresa_codigo')); obj,_=Cargo.objects.update_or_create(empresa=d['empresa'],codigo=d['codigo'],defaults=self.clean(Cargo,d)); self.i['cargo'][d['codigo']]=obj
    def plano(self):
        self.i['plano']={}; pk={}; pending=[dict(pk=x['pk'],**x['fields']) for x in self.seed('plano_contabil.json')]
        while pending:
            ok=False
            for d in pending[:]:
                pai=d.get('conta_pai')
                if pai and pai not in pk: continue
                emp=self.emp(); data={k:v for k,v in d.items() if k not in {'pk','empresa','conta_pai','data_cadastro'}}; data['empresa']=emp; data['conta_pai']=pk.get(pai)
                obj,_=PlanoContabil.objects.update_or_create(empresa=emp,codigo=data['codigo'],defaults=self.clean(PlanoContabil,data)); pk[d['pk']]=obj; self.i['plano'][obj.codigo]=obj; pending.remove(d); ok=True
            if not ok: raise CommandError('Plano Contábil oficial possui conta_pai sem correspondência.')
    def naturezas(self):
        self.i['nat']={}; pks={x['pk']:x['fields']['codigo'] for x in self.seed('plano_contabil.json')}; emp=self.emp()
        for it in self.seed('naturezas_lancamento.json'):
            d=dict(it['fields']); plano=self.i['plano'].get(pks.get(d.get('plano_contabil'))); d={k:v for k,v in d.items() if k not in {'empresa','plano_contabil'}}; d['empresa']=emp; d['plano_contabil']=plano
            obj,_=Nat_Lancamento.objects.update_or_create(empresa=emp,codigo=d['codigo'],defaults=self.clean(Nat_Lancamento,d)); self.i['nat'][obj.codigo]=obj
    def centros_setores(self):
        self.i['cc']={}; self.i['setor']={}; emp=self.emp()
        for f in self.seed('centros_custo.json'):
            d=dict(f); d['empresa']=self.emp(d.pop('empresa_codigo')); ordem=d.pop('setor_seed_ordem',None); d.pop('loja_codigo',None); d.pop('origem',None); d.pop('descricao_origem',None)
            if not d.get('descricao') and ordem: d['descricao']=self.seed('setores.json')[int(ordem)-1]['fields']['nome']
            obj,_=CentroCusto.objects.update_or_create(empresa=d['empresa'],codigo=d['codigo'],defaults=self.clean(CentroCusto,d)); self.i['cc'][obj.codigo]=obj
        ccs=list(self.i['cc'].values()); lojas=list(self.i['loja'].values())
        for it in self.seed('setores.json'):
            f=dict(it['fields']); loja=lojas[int(f['loja'])-1] if f.get('loja') else None; cc=ccs[int(f['centro_custo'])-1] if f.get('centro_custo') else None; d={k:v for k,v in f.items() if k not in {'empresa','loja','centro_custo','data_cadastro'}}; d.update({'empresa':emp,'loja':loja,'centro_custo':cc})
            obj,_=RequisicaoSetor.objects.update_or_create(empresa=emp,nome=d['nome'],defaults=self.clean(RequisicaoSetor,d)); self.i['setor'][obj.nome]=obj
    def funcionarios(self):
        self.i['func']={}
        for f in self.seed('funcionarios.json'):
            d=dict(f); d['empresa']=self.emp(d.pop('empresa_codigo')); d['idloja']=self.i['loja'].get(d.pop('loja_codigo',None)); d['cargo']=self.i['cargo'].get(d.pop('cargo_codigo',None)); d['usuario']=self.i['user'].get(d.pop('usuario_username',None)); lojas=d.pop('lojas_supervisionadas',[])
            obj,_=Funcionarios.objects.update_or_create(empresa=d['empresa'],cpf=d.get('cpf'),defaults=self.clean(Funcionarios,d)); obj.lojas_supervisionadas.set([self.i['loja'][c] for c in lojas]); self.i['func'][obj.cpf]=obj
    def fornecedores(self):
        self.i['forn']={}; emp=self.emp()
        for f in self.seed('fornecedores.json'):
            d=dict(f); d['empresa']=emp; d.pop('tipo',None); obj,_=Fornecedor.objects.update_or_create(empresa=emp,documento=d['documento'],defaults=self.clean(Fornecedor,d)); self.i['forn'][obj.documento]=obj
        for model,file,keys in [(FornecedorCategoria,'fornecedores_categorias.json',('fornecedor','categoria')),(FornecedorContato,'fornecedores_contatos.json',('fornecedor','tipo','nome')),(FornecedorEndereco,'fornecedores_enderecos.json',('fornecedor','tipo','endereco'))]:
            for f in self.seed(file):
                d=dict(f); d['empresa']=self.emp(d.pop('empresa_codigo')); d['fornecedor']=self.i['forn'][d.pop('fornecedor_documento')]; lookup={k:d[k] for k in keys}; model.objects.update_or_create(**lookup,defaults=self.clean(model,d))
    def clientes(self):
        emp=self.emp()
        for f in self.seed('clientes.json'):
            d=dict(f); d['empresa']=emp; Cliente.objects.update_or_create(empresa=emp,documento=d.get('documento'),defaults=self.clean(Cliente,d))
    def financeiro(self):
        emp=self.emp(); self.i['prazo']={}; self.i['forma']={}; self.i['conta']={}
        for f in self.seed('prazos_pagamento.json'):
            d=dict(f); d['empresa']=emp; obj,_=PrazoPagamento.objects.update_or_create(empresa=emp,codigo=d['codigo'],defaults=self.clean(PrazoPagamento,d)); self.i['prazo'][obj.codigo]=obj
        for f in self.seed('prazos_pagamento_parcelas.json'):
            d=dict(f); d['prazo']=self.i['prazo'][d.pop('prazo_codigo')]; PrazoPagamentoParcela.objects.update_or_create(prazo=d['prazo'],ordem=d['ordem'],defaults=self.clean(PrazoPagamentoParcela,d))
        for f in self.seed('contas_bancarias.json'):
            d=dict(f); key=d.pop('codigo_seed'); d['empresa']=self.emp(d.pop('empresa_codigo')); d['idloja']=self.i['loja'][d.pop('loja_codigo')]; obj,_=ContaBancaria.objects.update_or_create(empresa=d['empresa'],idloja=d['idloja'],descricao=d['descricao'],defaults=self.clean(ContaBancaria,d)); self.i['conta'][key]=obj
        for f in self.seed('formas_pagamento.json'):
            d=dict(f); d['empresa']=emp; d['prazo_pagamento']=self.i['prazo'].get(d.pop('prazo_codigo',None)); obj,_=FormaPagamento.objects.update_or_create(empresa=emp,codigo=d['codigo'],defaults=self.clean(FormaPagamento,d)); self.i['forma'][obj.codigo]=obj
        for f in self.seed('formas_pagamento_parcelas.json'):
            d=dict(f); d['forma']=self.i['forma'][d.pop('forma_codigo')]; FormaPagamentoParcela.objects.update_or_create(forma=d['forma'],ordem=d['ordem'],defaults=self.clean(FormaPagamentoParcela,d))
        for f in self.seed('caixas.json'):
            d=dict(f); d['empresa']=self.emp(d.pop('empresa_codigo')); d['idloja']=self.i['loja'][d.pop('loja_codigo')]; Caixa.objects.update_or_create(empresa=d['empresa'],codigo=d['codigo'],defaults=self.clean(Caixa,d))
        for f in self.seed('tipos_despesa_pdv.json'):
            d=dict(f); d['empresa']=self.emp(d.pop('empresa_codigo')); d['Idnatureza']=self.i['nat'][d.pop('natureza_codigo')]; TipoDespesaPdv.objects.update_or_create(empresa=d['empresa'],codigo=d['codigo'],defaults=self.clean(TipoDespesaPdv,d))
    def prod_base(self):
        emp=self.emp(); self.i.update({'un':{},'grade':{},'tam':{},'cor':{},'mat':{},'grupo':{},'sub':{},'col':{},'tab':{},'ncm':{}})
        for f in self.seed('unidades_medida.json'):
            d=dict(f['fields'] if 'fields' in f else f); d['empresa']=emp; obj,_=Unidade.objects.update_or_create(empresa=emp,Codigo=d['Codigo'].upper(),defaults=self.clean(Unidade,{**d,'Codigo':d['Codigo'].upper()})); self.i['un'][obj.Codigo.upper()]=obj
        if 'M' in self.i['un']: self.i['un']['MT']=self.i['un']['M']
        for f in self.seed('ncm.json'):
            d=dict(f); d['empresa']=emp; obj,_=Ncm.objects.update_or_create(empresa=emp,ncm=d['ncm'],defaults=self.clean(Ncm,d)); self.i['ncm'][obj.ncm]=obj
        for f in self.seed('grupos.json'):
            d=dict(f); d['empresa']=emp; obj,_=Grupo.objects.update_or_create(empresa=emp,Codigo=d['Codigo'],defaults=self.clean(Grupo,d)); self.i['grupo'][obj.Codigo]=obj
        for f in self.seed('subgrupos.json'):
            d=dict(f); code=d.pop('Codigo'); d['empresa']=emp; d['Idgrupo']=self.i['grupo'][d.pop('grupo_codigo')]; d.setdefault('Margem',d['Idgrupo'].Margem); obj,_=Subgrupo.objects.update_or_create(empresa=emp,Idgrupo=d['Idgrupo'],Descricao=d['Descricao'],defaults=self.clean(Subgrupo,d)); self.i['sub'][code]=obj
        for f in self.seed('colecoes.json'):
            d=dict(f); d['empresa']=self.emp(d.pop('empresa_codigo')); obj,_=Colecao.objects.update_or_create(empresa=d['empresa'],Codigo=d['Codigo'],Estacao=d['Estacao'],defaults=self.clean(Colecao,d)); self.i['col'][(obj.Codigo,obj.Estacao)]=obj
        for f in self.seed('materiais.json'):
            d=dict(f); d['empresa']=emp; obj,_=Material.objects.update_or_create(empresa=emp,Codigo=d['Codigo'],defaults=self.clean(Material,d)); self.i['mat'][obj.Codigo]=obj
        for f in self.seed('grades.json'):
            d=dict(f); c=d.pop('codigo'); d['empresa']=emp; obj,_=Grade.objects.update_or_create(empresa=emp,Descricao=d['Descricao'],defaults=self.clean(Grade,d)); self.i['grade'][c]=obj
        for f in self.seed('tamanhos.json'):
            d=dict(f); c=d.pop('grade_codigo'); d['empresa']=emp; d['idgrade']=self.i['grade'][c]; obj,_=Tamanho.objects.update_or_create(empresa=emp,idgrade=d['idgrade'],Tamanho=str(d['Tamanho']),defaults=self.clean(Tamanho,d)); self.i['tam'][(c,str(obj.Tamanho))]=obj
        for f in self.seed('cores.json'):
            d=dict(f); d['empresa']=self.emp(d.pop('empresa_codigo')); obj,_=Cor.objects.update_or_create(empresa=d['empresa'],Codigo=d['Codigo'],defaults=self.clean(Cor,d)); self.i['cor'][obj.Codigo]=obj
        for f in self.seed('tabelas_preco.json'):
            d=dict(f); key=d.pop('codigo_seed'); d['empresa']=self.emp(d.pop('empresa_codigo')); obj,_=Tabelapreco.objects.update_or_create(empresa=d['empresa'],NomeTabela=d['NomeTabela'],defaults=self.clean(Tabelapreco,d)); self.i['tab'][key]=obj
        for f in self.seed('config_ean.json'):
            d=dict(f); d['empresa']=self.emp(d.pop('empresa_codigo')); ConfigEan.objects.update_or_create(empresa=d['empresa'],country_prefix=d['country_prefix'],company_prefix=d['company_prefix'],defaults=self.clean(ConfigEan,d))
        for f in self.seed('codigos.json'):
            d=dict(f); d['empresa']=self.emp(d.pop('empresa_codigo')); Codigos.objects.update_or_create(empresa=d['empresa'],colecao=d['colecao'],estacao=d['estacao'],defaults=self.clean(Codigos,d))
        for f in self.seed('produto_uso_consumo_sequencia.json'):
            d=dict(f); d['empresa']=self.emp(d.pop('empresa_codigo')); ProdutoUsoConsumoSequencia.objects.update_or_create(empresa=d['empresa'],defaults=self.clean(ProdutoUsoConsumoSequencia,d))
    def prod_data(self,f):
        d=dict(f); d['empresa']=self.emp(); d['unidade']=self.i['un'][d.pop('unidade_codigo')]; d['grupo']=self.i['grupo'].get(d.pop('grupo_codigo',None)); d['subgrupo']=self.i['sub'].get(d.pop('subgrupo_codigo',None)); d['colecao']=self.i['col'].get((d.pop('colecao_codigo',None),d.pop('colecao_estacao',None))); d['material']=self.i['mat'].get(d.pop('material_codigo',None)); d['grade']=self.i['grade'].get(d.pop('grade_codigo',None)); return d
    def produtos(self):
        self.i['prod']={}
        for file in ['produtos.json','produtos_uso_consumo.json','insumos_producao.json']:
            for f in self.seed(file):
                d=self.prod_data(f); obj,_=Produto.objects.update_or_create(empresa=d['empresa'],descricao=d['descricao'],defaults=self.clean(Produto,d)); self.i['prod'][obj.descricao]=obj
    def prod_deps(self):
        self.i['pack']={}; self.i['ficha']={}
        for f in self.seed('produto_detalhes.json'):
            p=self.i['prod'][f['produto_descricao']]; ProdutoDetalhe.objects.get_or_create(produto=p,idcor=self.i['cor'][f['cor_codigo']],idtamanho=self.i['tam'][(f['grade_codigo'],str(f['tamanho']))],defaults={'custo_original':p.custo_original,'custo_ultima_compra':p.custo_ultima_compra,'custo_medio':p.custo_medio})
        for cfg in ConfigEan.objects.all():
            itemrefs=[int(v) for v in ProdutoDetalhe.objects.filter(config_ean=cfg).exclude(codigo_item_ref='').values_list('codigo_item_ref', flat=True)]
            next_itemref=(max(itemrefs) + 1) if itemrefs else 1
            if cfg.next_itemref != next_itemref:
                cfg.next_itemref = next_itemref
                cfg.save(update_fields=['next_itemref'])
        for f in self.seed('packs.json'):
            d=dict(f); key=d.pop('codigo_seed'); d['empresa']=self.emp(d.pop('empresa_codigo')); d['grade']=self.i['grade'][d.pop('grade_codigo')]; obj,_=Pack.objects.update_or_create(empresa=d['empresa'],nome=d['nome'],defaults=self.clean(Pack,d)); self.i['pack'][key]=obj
        for f in self.seed('pack_itens.json'):
            d=dict(f); p=self.i['pack'][d.pop('pack_codigo_seed')]; t=self.i['tam'][(d.pop('grade_codigo'),str(d.pop('tamanho')))]; PackItem.objects.update_or_create(pack=p,tamanho=t,defaults={'qtd':d['qtd']})
        for f in self.seed('produtos_fornecedores.json'):
            d=dict(f); d['empresa']=self.emp(d.pop('empresa_codigo')); d['fornecedor']=self.i['forn'][d.pop('fornecedor_documento')]; d['produto']=self.i['prod'][d.pop('produto_descricao')]; ProdutoFornecedor.objects.update_or_create(empresa=d['empresa'],fornecedor=d['fornecedor'],codigo_produto_fornecedor=d['codigo_produto_fornecedor'],defaults=self.clean(ProdutoFornecedor,d))
        for f in self.seed('tabela_preco_produto.json'):
            d=dict(f); d.pop('empresa_codigo',None); d['produto']=self.i['prod'][d.pop('produto_descricao')]; d['tabela']=self.i['tab'][d.pop('tabela_codigo_seed')]; TabelaprecoProduto.objects.update_or_create(produto=d['produto'],tabela=d['tabela'],defaults=self.clean(TabelaprecoProduto,d))
        for f in self.seed('fichas_tecnicas.json'):
            d=dict(f); d['empresa']=self.emp(d.pop('empresa_codigo')); d['produto_final']=self.i['prod'][d.pop('produto_final_descricao')]; obj,_=FichaTecnica.objects.update_or_create(empresa=d['empresa'],produto_final=d['produto_final'],versao=d['versao'],defaults=self.clean(FichaTecnica,d)); self.i['ficha'][(obj.produto_final.descricao,obj.versao)]=obj
        FichaTecnicaItem.objects.all().delete()
        for f in self.seed('fichas_tecnicas_itens.json'):
            d=dict(f); d.pop('empresa_codigo',None); d['ficha']=self.i['ficha'][(d.pop('ficha_produto_final_descricao'),d.pop('ficha_versao'))]; d['produto']=self.i['prod'].get(d.pop('produto_descricao',None)); d['fornecedor']=self.i['forn'].get(d.pop('fornecedor_documento',None)); d['unidade']=self.i['un'].get(d.pop('unidade_codigo',None)); FichaTecnicaItem.objects.create(**self.clean(FichaTecnicaItem,d))
    def estoque_estrutural(self):
        emp=self.emp()
        lojas=list(Loja.objects.filter(empresa=emp).order_by('id'))
        skus=list(ProdutoDetalhe.objects.select_related('produto').filter(produto__empresa=emp).order_by('IdprodutoDetalhe'))
        existentes={(e.CodigodeBarra,e.Idloja_id) for e in Estoque.objects.filter(Idloja__empresa=emp).only('CodigodeBarra','Idloja_id')}
        novos=[]
        for sku in skus:
            for loja in lojas:
                key=(sku.ean13,loja.id)
                if key in existentes: continue
                novos.append(Estoque(CodigodeBarra=sku.ean13, Idloja=loja, referencia=sku.produto.referencia or '', Estoque=Decimal('0'), reserva=Decimal('0')))
        if novos: Estoque.objects.bulk_create(novos, batch_size=1000, ignore_conflicts=True)
        uso=list(Produto.objects.filter(empresa=emp,tipo_produto='2').order_by('Idproduto'))
        existentes_uso={(e.produto_id,e.loja_id) for e in ProdutoUsoConsumoEstoque.objects.filter(empresa=emp).only('produto_id','loja_id')}
        novos_uso=[]
        for produto in uso:
            for loja in lojas:
                key=(produto.pk,loja.id)
                if key in existentes_uso: continue
                novos_uso.append(ProdutoUsoConsumoEstoque(empresa=emp, produto=produto, loja=loja, saldo=Decimal('0')))
        if novos_uso: ProdutoUsoConsumoEstoque.objects.bulk_create(novos_uso, batch_size=1000, ignore_conflicts=True)
    def fiscal(self):
        emp=self.emp(); self.i['cfop']={}; self.i['trib']={}
        for f in self.seed('cfops.json'):
            d=dict(f); d['empresa']=emp; obj,_=Cfop.objects.update_or_create(empresa=emp,codigo=d['codigo'],defaults=self.clean(Cfop,d)); self.i['cfop'][obj.codigo]=obj
        for f in self.seed('tributos.json'):
            d=dict(f); d['empresa']=emp; obj,_=Tributo.objects.update_or_create(empresa=emp,codigo=d['codigo'],defaults=self.clean(Tributo,d)); self.i['trib'][obj.codigo]=obj
        for f in self.seed('regras_tributarias.json'):
            d=dict(f); d['empresa']=emp; d['tributo']=self.i['trib'][d.pop('tributo_codigo')]; d['cfop']=self.i['cfop'].get(d.pop('cfop_codigo',None)); d['ncm']=self.i['ncm'].get(d.get('ncm')); RegraTributaria.objects.update_or_create(empresa=emp,nome=d['nome'],tributo=d['tributo'],defaults=self.clean(RegraTributaria,d))
    def requisicoes(self):
        emp=self.emp()
        for m,file in [(RequisicaoServicoCategoria,'requisicao_servico_categorias.json'),(RequisicaoMaterialCategoria,'requisicao_material_categorias.json'),(RequisicaoFinalidadeAquisicao,'requisicao_finalidades_aquisicao.json')]:
            for f in self.seed(file):
                d=dict(f); d['empresa']=emp; d.pop('empresa_codigo',None); m.objects.update_or_create(empresa=emp,nome=d['nome'],defaults=self.clean(m,d))
        for f in self.seed('requisicao_matriz_responsabilidade.json'):
            d=dict(f); d['empresa']=emp; d.pop('empresa_codigo',None); d['setor_atendimento']=self.i['setor'][d.pop('setor_atendimento_nome')]; d['setor_aquisicao']=self.i['setor'][d.pop('setor_aquisicao_nome')]; RequisicaoMatrizResponsabilidade.objects.update_or_create(empresa=emp,tipo_requisicao=d['tipo_requisicao'],setor_atendimento=d['setor_atendimento'],setor_aquisicao=d['setor_aquisicao'],defaults=self.clean(RequisicaoMatrizResponsabilidade,d))
    def dist(self):
        emp=self.emp(); self.i['pd']={}
        for f in self.seed('perfis_distribuicao.json'):
            d=dict(f); d['empresa']=emp; d.pop('empresa_codigo',None); obj,_=PerfilDistribuicao.objects.update_or_create(empresa=emp,codigo=d['codigo'],defaults=self.clean(PerfilDistribuicao,d)); self.i['pd'][obj.codigo]=obj
        for f in self.seed('perfis_distribuicao_itens.json'):
            d=dict(f); d['perfil']=self.i['pd'][d.pop('perfil_codigo')]; d['loja']=self.i['loja'][d.pop('loja_codigo')]; d.pop('empresa_codigo',None); PerfilDistribuicaoItem.objects.update_or_create(perfil=d['perfil'],loja=d['loja'],defaults=self.clean(PerfilDistribuicaoItem,d))
    def validate(self):
        r=DevBaseReport(); self.count(r); e={p.name[:-5]:len(self.seed(p.name)) for p in SEED_DIR.glob('*.json')}
        emp=self.emp(); lojas_count=Loja.objects.filter(empresa=emp).count(); skus_count=ProdutoDetalhe.objects.filter(produto__empresa=emp).count(); uso_count=Produto.objects.filter(empresa=emp,tipo_produto='2').count()
        estoque_qs=Estoque.objects.filter(Idloja__empresa=emp); uso_estoque_qs=ProdutoUsoConsumoEstoque.objects.filter(empresa=emp)
        sku_counts=estoque_qs.values('CodigodeBarra').annotate(c=Count('Idloja', distinct=True)).filter(c=lojas_count).count()
        estoque_dup=estoque_qs.values('CodigodeBarra','Idloja').annotate(c=Count('Idestoque')).filter(c__gt=1).exists()
        uso_dup=uso_estoque_qs.values('produto','loja').annotate(c=Count('id')).filter(c__gt=1).exists()
        sku_refs={sku.ean13:(sku.produto.referencia or '') for sku in ProdutoDetalhe.objects.select_related('produto').filter(produto__empresa=emp)}
        eans_validos=set(sku_refs)
        estoque_refs_ok=all((e.referencia or '') == sku_refs.get(e.CodigodeBarra, '') for e in estoque_qs.only('CodigodeBarra','referencia'))
        checks=[(Empresa.objects.count()==e['empresas'],'Empresas divergem dos JSONs.'),(Loja.objects.count()==e['lojas'],'Lojas divergem dos JSONs.'),(get_user_model().objects.count()==e['usuarios'],'Usuários divergem dos JSONs.'),(Fornecedor.objects.count()==e['fornecedores'],'Fornecedores divergem.'),(FornecedorCategoria.objects.count()==e['fornecedores_categorias'],'Categorias de fornecedores divergem.'),(FornecedorContato.objects.count()==e['fornecedores_contatos'],'Contatos divergem.'),(FornecedorEndereco.objects.count()==e['fornecedores_enderecos'],'Endereços divergem.'),(Produto.objects.count()==e['produtos']+e['produtos_uso_consumo']+e['insumos_producao'],'Produtos divergem.'),(ProdutoDetalhe.objects.count()==e['produto_detalhes'],'SKUs divergem.'),(ProdutoFornecedor.objects.count()==e['produtos_fornecedores'],'ProdutoFornecedor diverge.'),(FichaTecnica.objects.count()==e['fichas_tecnicas'],'Fichas divergem.'),(FichaTecnicaItem.objects.count()==e['fichas_tecnicas_itens'],'Itens de fichas divergem.'),(Promocao.objects.count()==e['promocoes'],'Promoções devem respeitar JSON vazio.'),(ProdutoDetalhe.objects.exclude(ean13='').count()==ProdutoDetalhe.objects.count(),'Há SKU sem EAN.'),(ProdutoDetalhe.objects.values('ean13').annotate(c=Count('ean13')).filter(c__gt=1).count()==0,'Há EAN duplicado.'),(ConfigEan.objects.first() and ConfigEan.objects.first().next_itemref==ProdutoDetalhe.objects.count()+1,'Sequência EAN não avançou corretamente.'),(estoque_qs.count()==skus_count*lojas_count,'Estoque estrutural SKU × loja diverge.'),(sku_counts==skus_count,'Nem todo SKU possui uma linha por loja.'),(not estoque_dup,'Há estoque duplicado por EAN × loja.'),(not estoque_qs.exclude(CodigodeBarra__in=eans_validos).exists(),'Há estoque com EAN inexistente em ProdutoDetalhe.'),(estoque_refs_ok,'Há estoque com referência diferente do produto do SKU.'),(not estoque_qs.exclude(Estoque=0).exists(),'Há estoque estrutural com saldo diferente de zero.'),(not estoque_qs.exclude(reserva=0).exists(),'Há estoque estrutural com reserva diferente de zero.'),(uso_estoque_qs.count()==uso_count*lojas_count,'ProdutoUsoConsumoEstoque produto × loja diverge.'),(not uso_dup,'Há ProdutoUsoConsumoEstoque duplicado por produto × loja.'),(not uso_estoque_qs.exclude(produto__tipo_produto='2').exists(),'ProdutoUsoConsumoEstoque contém produto que não é Uso/Consumo.'),(not uso_estoque_qs.exclude(saldo=0).exists(),'Há ProdutoUsoConsumoEstoque com saldo diferente de zero.'),(not self.forbidden(),f"Movimentos operacionais proibidos encontrados: {', '.join(self.forbidden())}.")]
        for ok,msg in checks:
            if not ok: r.problems.append(msg)
        return r
    def count(self,r=None):
        r=r or self.report
        m={'seeds processados':len(self.seed_files),'empresas':Empresa.objects.count(),'lojas':Loja.objects.count(),'usuários':get_user_model().objects.count(),'fornecedores':Fornecedor.objects.count(),'clientes':Cliente.objects.count(),'centros de custo':CentroCusto.objects.count(),'setores':RequisicaoSetor.objects.count(),'produtos':Produto.objects.count(),'SKUs':ProdutoDetalhe.objects.count(),'EANs':ProdutoDetalhe.objects.exclude(ean13='').count(),'estoque estrutural SKU × loja':Estoque.objects.count(),'estoque uso/consumo produto × loja':ProdutoUsoConsumoEstoque.objects.count(),'produtos-fornecedores':ProdutoFornecedor.objects.count(),'fichas técnicas':FichaTecnica.objects.count(),'itens ficha técnica':FichaTecnicaItem.objects.count(),'formas pagamento':FormaPagamento.objects.count(),'parcelas formas':FormaPagamentoParcela.objects.count(),'prazos':PrazoPagamento.objects.count(),'parcelas prazos':PrazoPagamentoParcela.objects.count(),'perfis distribuição':PerfilDistribuicao.objects.count(),'itens perfis distribuição':PerfilDistribuicaoItem.objects.count(),'promoções':Promocao.objects.count(),'tabelas operacionais com dados':len(self.forbidden())}
        for k,v in m.items(): r.set(k,v)
    def forbidden(self):
        labels=[]
        for app,model in FORBIDDEN_OPERATIONAL_MODELS:
            try:
                m=apps.get_model(app,model)
                if m.objects.exists(): labels.append(m._meta.label)
            except LookupError: pass
        return labels
    def delete_all(self):
        self.delm([SessionToken, NotaFiscalEntradaDivergenciaXml, NotaFiscalEntradaEvento, NotaFiscalEntradaItem, NotaFiscalEntradaItemXml, NotaFiscalSaidaItem, NFeDevolucao, NFCe, VendaDevolucaoItem, ValeTrocaMovimento, CashbackMovimento, VendaPdvPagamento, VendaPdvItem, AntecipacaoRecebivelItem, LancamentoContabil, PagarRateio, ReceberRateio, PagarItem, ReceberItem, PedidoCompraEntrega, PedidoCompraParcela, PedidoCompraItem, CotacaoPropostaItem, CotacaoProposta, CotacaoRequisicao, CotacaoItem, CotacaoFornecedor, OrdemServicoMaterial, RequisicaoHistorico, RequisicaoItem, MercadoriaTransito, PedidoVendaDistribuicaoItem, DistribuicaoDestino, DistribuicaoItem, OrdemProducaoGrade, OrdemProducaoItem, InventarioEstoqueItem])
        self.delm([ValeTroca, VendaDevolucao, VendaPdv, NotaFiscalSaida, NotaFiscalEntrada, AntecipacaoRecebivel, MovimentacaoFinanceira, Pagar, Receber, PedidoVendaDistribuicao, Distribuicao, OrdemServico, Cotacao, PedidoCompra, Requisicao, OrdemProducao, InventarioEstoque])
        self.delm([SessaoUsuario, FuncionarioHistorico, Funcionarios, PerfilModuloPermissao, PerfilProcessPermission, UserModulePermission, UserFieldPermission, EmpresaModulo, EmpresaContrato, ConfigFinanceira, TipoDespesaPdv, CashbackConfig, FormaPagamentoFiscalMap, RegraTributaria, Tributo, Cfop, PerfilDistribuicaoItem, PerfilDistribuicao])
        get_user_model().objects.all().delete(); self.delm([EstoqueMovimentacao, Estoque, ProdutoUsoConsumoMovimentacao, ProdutoUsoConsumoEstoque, ProdutoVendaHistorico, ProdutoUsoConsumoHistorico, ProdutoInsumoHistorico, ProdutoImagem, ProdutoFornecedor, TabelaprecoProduto, FichaTecnicaItem, FichaTecnica, ProdutoDetalhe, PackItem, Pack, Promocao, Produto, ProdutoUsoConsumoSequencia, Codigos, Tabelapreco, Subgrupo, Grupo, Colecao, Material, Cor, Tamanho, Grade, Unidade, Ncm, ConfigEan, FormaPagamentoParcela, FormaPagamento, PrazoPagamentoParcela, PrazoPagamento, ContaBancaria, Caixa, FornecedorContato, FornecedorEndereco, FornecedorCategoria, Fornecedor, Cliente, RequisicaoMatrizResponsabilidade, RequisicaoFinalidadeAquisicao, RequisicaoMaterialCategoria, RequisicaoServicoCategoria, RequisicaoSetor, CentroCusto, Cargo, PerfilAcesso, Nat_Lancamento])
        while PlanoContabil.objects.exists():
            folhas=PlanoContabil.objects.filter(subcontas__isnull=True)
            if not folhas.exists(): raise CommandError('PlanoContabil possui ciclo ou hierarquia inválida em conta_pai.')
            folhas.delete()
        get_user_model().objects.all().delete(); self.delm([Loja,Empresa])
    def delm(self,models):
        for m in models: m.objects.all().delete()
