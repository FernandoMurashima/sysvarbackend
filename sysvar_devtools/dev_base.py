from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from itertools import cycle
import json
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from accounts.models import PerfilAcesso, PerfilModuloPermissao, PerfilProcessPermission, SessaoUsuario, SessionToken, UserFieldPermission, UserModulePermission
from accounts.services.effective_access import sync_legacy_license_flags
from accounts.services.profiles import ensure_default_profiles
from cadastros.models import Cargo, CentroCusto, Cliente, Empresa, EmpresaContrato, EmpresaModulo, Fornecedor, FornecedorCategoria, FornecedorContato, FornecedorEndereco, Funcionarios, FuncionarioHistorico, Loja, ModuloSistema, Nat_Lancamento, PlanoContabil
from cadastros.services import CargoInicialService, ClientePadraoService
from compras.models import Cotacao, CotacaoFornecedor, CotacaoItem, CotacaoProposta, CotacaoPropostaItem, CotacaoRequisicao, OrdemServico, OrdemServicoMaterial, PedidoCompra, PedidoCompraEntrega, PedidoCompraItem, PedidoCompraParcela, Requisicao, RequisicaoFinalidadeAquisicao, RequisicaoHistorico, RequisicaoItem, RequisicaoMaterialCategoria, RequisicaoMatrizResponsabilidade, RequisicaoServicoCategoria, RequisicaoSetor
from distribuicao.models import Distribuicao, DistribuicaoDestino, DistribuicaoItem, MercadoriaTransito, PedidoVendaDistribuicao, PedidoVendaDistribuicaoItem, PerfilDistribuicao, PerfilDistribuicaoItem
from financeiro.models import AntecipacaoRecebivel, AntecipacaoRecebivelItem, Caixa, CashbackConfig, CashbackMovimento, ConfigFinanceira, ContaBancaria, FormaPagamento, FormaPagamentoParcela, LancamentoContabil, MovimentacaoFinanceira, Pagar, PagarItem, PagarRateio, PrazoPagamento, PrazoPagamentoParcela, Receber, ReceberItem, ReceberRateio, TipoDespesaPdv, ValeTroca, ValeTrocaMovimento
from fiscal.models.cfop import Cfop
from fiscal.models.nota_fiscal_entrada import FormaPagamentoFiscalMap, NotaFiscalEntrada, NotaFiscalEntradaDivergenciaXml, NotaFiscalEntradaEvento, NotaFiscalEntradaItem, NotaFiscalEntradaItemXml
from fiscal.models.nota_fiscal_saida import NotaFiscalSaida, NotaFiscalSaidaItem
from fiscal.models.tributacao import RegraTributaria, Tributo
from fiscal.models.venda_pdv import NFCe, NFeDevolucao, VendaDevolucao, VendaDevolucaoItem, VendaPdv, VendaPdvItem, VendaPdvPagamento
from produto.models import (
    Codigos,
    Colecao,
    ConfigEan,
    Cor,
    Estoque,
    EstoqueMovimentacao,
    FichaTecnica,
    FichaTecnicaItem,
    Grade,
    Grupo,
    InventarioEstoque,
    InventarioEstoqueItem,
    Material,
    Ncm,
    OrdemProducao,
    OrdemProducaoGrade,
    OrdemProducaoItem,
    Pack,
    PackItem,
    Produto,
    ProdutoDetalhe,
    ProdutoFornecedor,
    ProdutoImagem,
    ProdutoInsumoHistorico,
    ProdutoUsoConsumoEstoque,
    ProdutoUsoConsumoHistorico,
    ProdutoUsoConsumoMovimentacao,
    ProdutoUsoConsumoSequencia,
    ProdutoVendaHistorico,
    Promocao,
    Subgrupo,
    Tabelapreco,
    TabelaprecoProduto,
    Tamanho,
    Unidade,
)


DEV_COMPANY_DOCUMENT = "11222333000181"
DEV_COMPANY_NAME = "Sysvar Desenvolvimento Moda Ltda"
DEV_PASSWORD = "Sysvar@123"
PRESERVED_SUPERUSER = "takeshi"
FIXED_DATE = timezone.datetime(2026, 1, 5).date()
DEV_USERS = {
    "admin.delegado": ("Ricardo", "Almeida", "Administrador delegado", None, "Admin"),
    "gerente.barra": ("Mariana", "Costa", "Gerente", "BARRA", "Gerente"),
    "caixa.barra": ("Juliana", "Rocha", "Caixa", "BARRA", "Caixa"),
    "vendedor1.barra": ("Camila", "Martins", "Vendedor", "BARRA", "Vendedor"),
    "vendedor2.barra": ("Rafael", "Souza", "Vendedor", "BARRA", "Vendedor"),
    "gerente.tijuca": ("Bruno", "Carvalho", "Gerente", "TIJUCA", "Gerente"),
    "caixa.tijuca": ("Fernanda", "Lima", "Caixa", "TIJUCA", "Caixa"),
    "vendedor1.tijuca": ("Lucas", "Ribeiro", "Vendedor", "TIJUCA", "Vendedor"),
    "vendedor2.tijuca": ("Beatriz", "Fernandes", "Vendedor", "TIJUCA", "Vendedor"),
    "gerente.centro": ("Patrícia", "Gomes", "Gerente", "CENTRO", "Gerente"),
    "caixa.centro": ("Daniela", "Alves", "Caixa", "CENTRO", "Caixa"),
    "vendedor1.centro": ("Gustavo", "Oliveira", "Vendedor", "CENTRO", "Vendedor"),
    "vendedor2.centro": ("Larissa", "Mendes", "Vendedor", "CENTRO", "Vendedor"),
}
ALLOWED_USERS = {PRESERVED_SUPERUSER, *DEV_USERS}
SEED_DIR = Path(__file__).resolve().parent / "seeds"
FORBIDDEN_OPERATIONAL_MODELS = [
    ("compras", "Requisicao"),
    ("compras", "OrdemServico"),
    ("compras", "Cotacao"),
    ("compras", "PedidoCompra"),
    ("distribuicao", "Distribuicao"),
    ("distribuicao", "PedidoVendaDistribuicao"),
    ("distribuicao", "MercadoriaTransito"),
    ("financeiro", "CashbackMovimento"),
    ("financeiro", "ValeTroca"),
    ("financeiro", "ValeTrocaMovimento"),
    ("financeiro", "MovimentacaoFinanceira"),
    ("financeiro", "Pagar"),
    ("financeiro", "Receber"),
    ("financeiro", "AntecipacaoRecebivel"),
    ("fiscal", "NotaFiscalEntrada"),
    ("fiscal", "NotaFiscalEntradaEvento"),
    ("fiscal", "NotaFiscalSaida"),
    ("fiscal", "VendaPdv"),
    ("fiscal", "VendaDevolucao"),
    ("fiscal", "NFCe"),
    ("produto", "EstoqueMovimentacao"),
    ("produto", "OrdemProducao"),
    ("produto", "InventarioEstoque"),
]
@dataclass
class DevBaseReport:
    created: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def set(self, key, value):
        self.created[key] = int(value)

    @property
    def valid(self):
        return not self.problems


class SysvarDevBaseService:
    def __init__(self):
        self.report = DevBaseReport()

    def assert_not_production(self, destructive=False):
        if not destructive:
            return
        if getattr(settings, "DEBUG", False):
            return
        db_name = str(settings.DATABASES["default"].get("NAME", "")).lower()
        allowed = {"test", "teste", "dev", "development", "varejo_db"}
        if not any(token in db_name for token in allowed):
            raise CommandError("Operação destrutiva bloqueada: ambiente não parece ser desenvolvimento.")

    def reset(self):
        self.assert_not_production(destructive=True)
        self._flush_database()
        self._seed_global_structures()
        self._recreate_takeshi()
        self._count_global_report()
        return self.report

    @transaction.atomic
    def create(self, seed_globals=True):
        if seed_globals:
            self._seed_global_structures()
            self._recreate_takeshi()
        empresa = self._empresa()
        self._seed_plano_contabil(empresa)
        self._seed_naturezas(empresa)
        lojas = self._lojas(empresa)
        self._licenciamento(empresa)
        perfis = self._seed_profiles_and_permissions(empresa)
        setores = self._setores_centros(empresa, lojas)
        usuarios = self._usuarios_funcionarios(empresa, lojas, perfis)
        self._seed_profiles_and_permissions(empresa)
        fornecedores = self._fornecedores(empresa)
        clientes = self._clientes(empresa)
        financeiro = self._financeiro(empresa, lojas)
        base = self._base_produto(empresa)
        produtos, insumos = self._produtos(empresa, base, fornecedores)
        self._estoque(lojas, produtos)
        self._count_report(empresa)
        return self.report

    def rebuild(self):
        self.assert_not_production(destructive=True)
        self._flush_database()
        self._seed_global_structures()
        self._recreate_takeshi()
        self.create(seed_globals=False)
        return self.validate()

    def validate(self):
        empresa = self.dev_company()
        r = DevBaseReport()
        if not empresa:
            r.problems.append("Empresa de desenvolvimento não encontrada.")
            return r
        lojas = Loja.objects.filter(empresa=empresa, ativo=True)
        User = get_user_model()
        users = User.objects.all()
        dev_users = users.filter(username__in=DEV_USERS)
        takeshi = users.filter(username=PRESERVED_SUPERUSER).first()
        residual_users = set(users.values_list("username", flat=True)) - ALLOWED_USERS
        skus = ProdutoDetalhe.objects.filter(produto__empresa=empresa, produto__tipo_produto__in=["1", "3"])
        checks = [
            (Empresa.objects.filter(documento=DEV_COMPANY_DOCUMENT).count() == 1, "Deve existir exatamente 1 empresa de desenvolvimento."),
            (Empresa.objects.count() == 1, "Não deve existir empresa antiga remanescente."),
            (lojas.filter(tipo_unidade=Loja.TIPO_LOJA).count() == 3, "Devem existir 3 lojas."),
            (lojas.filter(tipo_unidade=Loja.TIPO_FABRICA).count() == 1, "Deve existir 1 fábrica."),
            (users.count() == 14, "Devem existir exatamente 14 usuários no total."),
            (users.filter(is_superuser=True).count() == 1, "Deve existir exatamente 1 superusuário."),
            (users.filter(username=PRESERVED_SUPERUSER, email="takeshi@sysvar.test", is_superuser=True, is_staff=True, is_active=True, empresa__isnull=True).count() == 1, "O superusuário takeshi deve ser recriado sem empresa vinculada."),
            (bool(takeshi and takeshi.check_password(DEV_PASSWORD)), "O superusuário takeshi deve aceitar a senha padrão."),
            (users.filter(empresa=empresa, is_superuser=False).count() == 13, "Devem existir 13 usuários operacionais vinculados à empresa."),
            (not residual_users, f"Usuários residuais encontrados: {', '.join(sorted(residual_users))}."),
            (dev_users.count() == 13, "Lista oficial de usuários da base está incompleta."),
            (all(u.first_name and u.last_name for u in dev_users), "Todos os usuários da base devem possuir nome e sobrenome."),
            (all(u.check_password(DEV_PASSWORD) for u in dev_users), "Todos os usuários da base devem aceitar a senha padrão."),
            (ModuloSistema.objects.filter(chave__in=self._seed_modulo_chaves()).count() == self._seed_count("modulos_sistema.json"), "Módulos oficiais esperados ausentes."),
            (self._profiles_are_valid(empresa), "Perfis de acesso dos usuários da base estão incorretos."),
            (PlanoContabil.objects.filter(empresa=empresa).count() == len(self._seed_plano_items()), "Plano contábil oficial incompleto."),
            (Nat_Lancamento.objects.filter(empresa=empresa).count() == self._seed_count("naturezas_lancamento.json"), "Naturezas de lançamento oficiais incompletas."),
            (PerfilAcesso.objects.filter(empresa=empresa).count() == len(self._seed_perfil_items()), "Perfis oficiais incompletos."),
            (PerfilModuloPermissao.objects.filter(perfil__empresa=empresa).count() == len(self._seed_perfil_modulo_items()), f"Permissões por módulo oficiais incompletas: {PerfilModuloPermissao.objects.filter(perfil__empresa=empresa).count()} de {len(self._seed_perfil_modulo_items())}."),
            (PerfilProcessPermission.objects.filter(perfil__empresa=empresa).count() == len(self._seed_perfil_processo_items()), "Permissões por processo oficiais incompletas."),
            (Unidade.objects.filter(empresa=empresa, Codigo__in=self._seed_unidade_codigos()).count() == len(self._seed_unidade_codigos()), "Unidades de medida oficiais incompletas."),
            (CentroCusto.objects.filter(empresa=empresa).count() == lojas.count() + len(self._seed_setor_items()), "Centros de custo esperados ausentes."),
            (RequisicaoSetor.objects.filter(empresa=empresa).count() == lojas.count() + len(self._seed_setor_items()), "Setores oficiais esperados ausentes."),
            (RequisicaoSetor.objects.filter(empresa=empresa, centro_custo__isnull=True).count() == 0, "Há setores sem centro de custo."),
            (Fornecedor.objects.filter(empresa=empresa).count() >= 40, "Devem existir pelo menos 40 fornecedores."),
            (Cliente.objects.filter(empresa=empresa).count() == 11, "Devem existir 10 clientes mais cliente padrão."),
            (FormaPagamento.objects.filter(empresa=empresa).count() == 5, "Formas de pagamento esperadas ausentes."),
            (PrazoPagamento.objects.filter(empresa=empresa).count() == 12, "Prazos esperados ausentes."),
            (ContaBancaria.objects.filter(empresa=empresa).count() == 4, "Devem existir 4 contas bancárias."),
            (ContaBancaria.objects.get(empresa=empresa, idloja__tipo_unidade=Loja.TIPO_FABRICA).saldo_atual == Decimal("5000000.00"), "Saldo central deve ser 5.000.000,00."),
            (Colecao.objects.filter(empresa=empresa, Descricao="Verão 2027").count() == 1, "Coleção Verão 2027 ausente."),
            (Grupo.objects.filter(empresa=empresa).count() == 7, "Grupos esperados ausentes."),
            (Cor.objects.filter(empresa=empresa).count() >= 5, "Cores esperadas ausentes."),
            (Grade.objects.filter(empresa=empresa).count() == 3, "Grades esperadas ausentes."),
            (Pack.objects.filter(empresa=empresa).count() == 3, "Packs esperados ausentes."),
            (Produto.objects.filter(empresa=empresa, tipo_produto__in=["1", "3"]).count() == 200, "Devem existir 200 referências vendáveis."),
            (Produto.objects.filter(empresa=empresa, tipo_produto="1").count() == 100, "Devem existir 100 referências de revenda."),
            (Produto.objects.filter(empresa=empresa, tipo_produto="3").count() == 100, "Devem existir 100 referências próprias."),
            (FichaTecnica.objects.filter(empresa=empresa, produto_final__tipo_produto="3").count() == 100, "Devem existir 100 fichas técnicas."),
            (skus.values("ean13").annotate(c=Count("ean13")).filter(c__gt=1).count() == 0, "Há EAN duplicado."),
            (not self._forbidden_operational_labels(), f"Movimentos operacionais proibidos encontrados: {', '.join(self._forbidden_operational_labels())}."),
        ]
        for ok, problem in checks:
            if not ok:
                r.problems.append(problem)
        for loja in lojas:
            expected = Decimal("100.000") if loja.tipo_unidade == Loja.TIPO_FABRICA else Decimal("50.000")
            wrong = Estoque.objects.filter(Idloja=loja).exclude(Estoque=expected).count()
            if wrong or Estoque.objects.filter(Idloja=loja).count() != skus.count():
                r.problems.append(f"Estoque inválido em {loja.nome_loja}.")
        self._count_report(empresa, r)
        return r

    def dev_company(self):
        return Empresa.objects.filter(documento=DEV_COMPANY_DOCUMENT).first()

    def _load_seed(self, filename):
        with (SEED_DIR / filename).open(encoding="utf-8") as handle:
            return json.load(handle)

    def _seed_count(self, filename):
        return len(self._load_seed(filename))

    def _seed_modulo_chaves(self):
        return [item["fields"]["chave"] for item in self._load_seed("modulos_sistema.json")]

    def _seed_unidade_items(self):
        return [item for item in self._load_seed("unidades_medida.json") if item["fields"].get("empresa") == 1]

    def _seed_unidade_codigos(self):
        return [item["fields"]["Codigo"] for item in self._seed_unidade_items()]

    def _seed_setor_items(self):
        return [item for item in self._load_seed("setores.json") if item["fields"].get("empresa") == 4]

    def _seed_perfil_items(self):
        return [item for item in self._load_seed("perfis_acesso.json") if item["fields"].get("empresa") == 1]

    def _seed_perfil_pks(self):
        return {item["pk"] for item in self._seed_perfil_items()}

    def _seed_perfil_modulo_items(self):
        perfil_pks = self._seed_perfil_pks()
        return [item for item in self._load_seed("perfil_modulo_permissoes.json") if item["fields"]["perfil"] in perfil_pks]

    def _seed_perfil_processo_items(self):
        perfil_pks = self._seed_perfil_pks()
        return [item for item in self._load_seed("perfil_processo_permissoes.json") if item["fields"]["perfil"] in perfil_pks]

    def _seed_plano_items(self):
        return [item for item in self._load_seed("plano_contabil.json") if item["fields"].get("empresa") == 1]

    def _flush_database(self):
        call_command("flush", interactive=False, verbosity=0)

    def _seed_global_structures(self):
        for item in self._load_seed("modulos_sistema.json"):
            fields = item["fields"]
            deps = fields.get("dependencias") or []
            modulo, _ = ModuloSistema.objects.update_or_create(
                chave=fields["chave"],
                defaults={
                    "nome": fields["nome"],
                    "descricao": fields.get("descricao", ""),
                    "categoria": fields["categoria"],
                    "basico": fields.get("basico", False),
                    "ordem": fields.get("ordem", 0),
                    "ativo": fields.get("ativo", True),
                    "dependencias": deps,
                },
            )

    def _recreate_takeshi(self):
        User = get_user_model()
        user, _ = User.objects.update_or_create(
            username=PRESERVED_SUPERUSER,
            defaults={
                "email": "takeshi@sysvar.test",
                "first_name": "Takeshi",
                "last_name": "Sysvar",
                "is_superuser": True,
                "is_staff": True,
                "is_active": True,
                "empresa": None,
                "loja": None,
                "perfil_principal": None,
            },
        )
        user.set_password(DEV_PASSWORD)
        user.save()
        user.lojas.clear()

    def _delete_explicit_reset_layers(self):
        # Camada 1 - filhos operacionais.
        self._delete_models([
            SessionToken,
            NotaFiscalEntradaDivergenciaXml,
            NotaFiscalEntradaEvento,
            NotaFiscalEntradaItem,
            NotaFiscalEntradaItemXml,
            NotaFiscalSaidaItem,
            NFeDevolucao,
            NFCe,
            VendaDevolucaoItem,
            ValeTrocaMovimento,
            CashbackMovimento,
            VendaPdvPagamento,
            VendaPdvItem,
            AntecipacaoRecebivelItem,
            LancamentoContabil,
            PagarRateio,
            ReceberRateio,
            PagarItem,
            ReceberItem,
            PedidoCompraEntrega,
            PedidoCompraParcela,
            PedidoCompraItem,
            CotacaoPropostaItem,
            CotacaoProposta,
            CotacaoRequisicao,
            CotacaoItem,
            CotacaoFornecedor,
            OrdemServicoMaterial,
            RequisicaoHistorico,
            RequisicaoItem,
            MercadoriaTransito,
            PedidoVendaDistribuicaoItem,
            DistribuicaoDestino,
            DistribuicaoItem,
            OrdemProducaoGrade,
            OrdemProducaoItem,
            InventarioEstoqueItem,
        ])

        # Camada 2 - documentos operacionais.
        self._delete_models([
            ValeTroca,
            VendaDevolucao,
            VendaPdv,
            NotaFiscalSaida,
            NotaFiscalEntrada,
            AntecipacaoRecebivel,
            MovimentacaoFinanceira,
            Pagar,
            Receber,
            PedidoVendaDistribuicao,
            Distribuicao,
            OrdemServico,
            Cotacao,
            PedidoCompra,
            Requisicao,
            OrdemProducao,
            InventarioEstoque,
        ])

        # Camada 3 - vínculos e configurações operacionais.
        self._delete_models([
            SessaoUsuario,
            FuncionarioHistorico,
            Funcionarios,
            PerfilModuloPermissao,
            PerfilProcessPermission,
            PerfilAcesso,
            UserModulePermission,
            UserFieldPermission,
            EmpresaModulo,
            EmpresaContrato,
            ConfigFinanceira,
            TipoDespesaPdv,
            CashbackConfig,
            FormaPagamentoFiscalMap,
            RegraTributaria,
            Tributo,
            Cfop,
            PerfilDistribuicaoItem,
            PerfilDistribuicao,
        ])

        # Camada 4 - cadastros dependentes.
        self._delete_models([
            EstoqueMovimentacao,
            Estoque,
            ProdutoUsoConsumoMovimentacao,
            ProdutoUsoConsumoEstoque,
            ProdutoVendaHistorico,
            ProdutoUsoConsumoHistorico,
            ProdutoInsumoHistorico,
            ProdutoImagem,
            ProdutoFornecedor,
            TabelaprecoProduto,
            FichaTecnicaItem,
            FichaTecnica,
            ProdutoDetalhe,
            PackItem,
            Pack,
            Promocao,
            Produto,
            ProdutoUsoConsumoSequencia,
            Codigos,
            Tabelapreco,
            Subgrupo,
            Grupo,
            Colecao,
            Material,
            Cor,
            Tamanho,
            Grade,
            Unidade,
            Ncm,
            ConfigEan,
            FormaPagamentoParcela,
            FormaPagamento,
            PrazoPagamentoParcela,
            PrazoPagamento,
            ContaBancaria,
            Caixa,
            FornecedorContato,
            FornecedorEndereco,
            FornecedorCategoria,
            Fornecedor,
            Cliente,
            RequisicaoMatrizResponsabilidade,
            RequisicaoFinalidadeAquisicao,
            RequisicaoMaterialCategoria,
            RequisicaoServicoCategoria,
            RequisicaoSetor,
            CentroCusto,
            Cargo,
        ])

        # Camada 5 - financeiro/contábil estrutural.
        self._delete_models([Nat_Lancamento])
        self._delete_plano_contabil()

        # Camada 6 - lojas e empresas.
        self._delete_models([Loja, Empresa])

    def _delete_models(self, models):
        for model in models:
            try:
                model.objects.all().delete()
            except Exception as exc:
                raise CommandError(f"Limpeza bloqueada em {model._meta.label}: {exc}") from exc

    def _delete_plano_contabil(self):
        try:
            while PlanoContabil.objects.exists():
                folhas = PlanoContabil.objects.filter(subcontas__isnull=True)
                if not folhas.exists():
                    raise CommandError("PlanoContabil possui ciclo ou hierarquia inválida em conta_pai.")
                folhas.delete()
        except Exception as exc:
            raise CommandError(f"Limpeza bloqueada em cadastros.PlanoContabil: {exc}") from exc

    def _empresa(self):
        empresa, _ = Empresa.objects.update_or_create(
            documento=DEV_COMPANY_DOCUMENT,
            defaults={
                "nome": DEV_COMPANY_NAME,
                "nome_fantasia": "Sysvar Dev Moda",
                "ativo": True,
                "plano_completo": True,
                "licenca_master": True,
                "regime_tributario": Empresa.REGIME_LUCRO_REAL,
                "ambiente_fiscal": Empresa.AMBIENTE_HOMOLOGACAO,
                "uf_fiscal": "RJ",
                "inscricao_estadual": "85000019",
                "serie_nfce": 1,
                "proximo_numero_nfce": 1,
                "serie_nfe": 1,
                "proximo_numero_nfe": 1,
            },
        )
        return empresa

    def _seed_plano_contabil(self, empresa):
        seed = self._seed_plano_items()
        parent_by_pk = {item["pk"]: item["fields"].get("conta_pai") for item in seed}
        code_by_pk = {item["pk"]: item["fields"]["codigo"] for item in seed}
        contas = {}
        pending = list(seed)
        while pending:
            progressed = False
            for item in pending[:]:
                fields = item["fields"]
                parent_pk = parent_by_pk[item["pk"]]
                parent_codigo = code_by_pk.get(parent_pk)
                if parent_codigo and parent_codigo not in contas:
                    continue
                conta, _ = PlanoContabil.objects.update_or_create(
                    empresa=empresa,
                    codigo=fields["codigo"],
                    defaults={
                        "descricao": fields["descricao"],
                        "classe": fields["classe"],
                        "natureza": fields["natureza"],
                        "conta_pai": contas.get(parent_codigo),
                        "nivel": fields.get("nivel") or fields["codigo"].count(".") + 1,
                        "analitica": fields.get("analitica", True),
                        "ativa": fields.get("ativa", True),
                    },
                )
                contas[fields["codigo"]] = conta
                pending.remove(item)
                progressed = True
            if not progressed:
                raise CommandError("Plano Contábil oficial possui conta_pai sem correspondência por código.")
        return contas

    def _seed_naturezas(self, empresa):
        seed = self._load_seed("naturezas_lancamento.json")
        plano_code_by_pk = {item["pk"]: item["fields"]["codigo"] for item in self._seed_plano_items()}
        planos = {plano.codigo: plano for plano in PlanoContabil.objects.filter(empresa=empresa)}
        for item in seed:
            fields = item["fields"]
            plano_codigo = plano_code_by_pk.get(fields.get("plano_contabil"))
            plano = planos.get(plano_codigo)
            Nat_Lancamento.objects.update_or_create(
                empresa=empresa,
                codigo=fields["codigo"],
                defaults={
                    "categoria_principal": fields["categoria_principal"],
                    "subcategoria": fields["subcategoria"],
                    "descricao": fields["descricao"],
                    "tipo": fields["tipo"],
                    "status": fields["status"],
                    "tipo_natureza": fields["tipo_natureza"],
                    "natureza_operacao": fields.get("natureza_operacao", "DESPESA"),
                    "categoria_gerencial": fields.get("categoria_gerencial"),
                    "movimenta_financeiro": fields.get("movimenta_financeiro", True),
                    "entra_dre": fields.get("entra_dre", True),
                    "plano_contabil": plano,
                    "conta_contabil": plano.codigo if plano else fields.get("conta_contabil"),
                    "ativo": fields.get("ativo", True),
                },
            )

    def _seed_profiles_and_permissions(self, empresa):
        perfil_seed = self._seed_perfil_items()
        modulo_seed = self._load_seed("modulos_sistema.json")
        perfil_name_by_pk = {}
        perfis = {}
        for item in perfil_seed:
            fields = item["fields"]
            perfil, _ = PerfilAcesso.objects.update_or_create(
                empresa=empresa,
                nome=fields["nome"],
                defaults={
                    "descricao": fields.get("descricao", ""),
                    "ativo": fields.get("ativo", True),
                    "padrao": fields.get("padrao", False),
                },
            )
            perfil_name_by_pk[item["pk"]] = perfil.nome
            perfis[perfil.nome] = perfil

        PerfilModuloPermissao.objects.filter(perfil__in=perfis.values()).delete()
        PerfilProcessPermission.objects.filter(perfil__in=perfis.values()).delete()
        modulo_chave_by_pk = {item["pk"]: item["fields"]["chave"] for item in modulo_seed}
        modulos = {modulo.chave: modulo for modulo in ModuloSistema.objects.all()}
        for item in self._seed_perfil_modulo_items():
            fields = item["fields"]
            perfil = perfis[perfil_name_by_pk[fields["perfil"]]]
            modulo = modulos[modulo_chave_by_pk[fields["modulo"]]]
            PerfilModuloPermissao.objects.update_or_create(
                perfil=perfil,
                modulo=modulo,
                defaults={"acesso": fields.get("acesso", UserModulePermission.Access.NONE), "pode_excluir": fields.get("pode_excluir", False)},
            )
        for item in self._seed_perfil_processo_items():
            fields = item["fields"]
            perfil = perfis[perfil_name_by_pk[fields["perfil"]]]
            PerfilProcessPermission.objects.update_or_create(
                perfil=perfil,
                codigo=fields["codigo"],
                defaults={"permitido": fields.get("permitido", False)},
            )
        return perfis

    def _licenciamento(self, empresa):
        modules = ModuloSistema.objects.filter(ativo=True)
        today = FIXED_DATE
        EmpresaContrato.objects.update_or_create(
            empresa=empresa,
            defaults={"status": EmpresaContrato.STATUS_ATIVO, "data_inicio": today, "limite_usuarios": 50, "limite_sessoes_simultaneas": 50, "plano_completo": True},
        )
        for modulo in modules:
            EmpresaModulo.objects.update_or_create(empresa=empresa, modulo=modulo, defaults={"contratado": True, "data_inicio": today})
        sync_legacy_license_flags(empresa)

    def _lojas(self, empresa):
        specs = [("Loja Barra", "BARRA", Loja.TIPO_LOJA), ("Loja Tijuca", "TIJUCA", Loja.TIPO_LOJA), ("Loja Centro", "CENTRO", Loja.TIPO_LOJA), ("Fábrica", "FABRICA", Loja.TIPO_FABRICA)]
        lojas = []
        for i, (nome, apelido, tipo) in enumerate(specs, 1):
            lojas.append(Loja.objects.create(empresa=empresa, nome_loja=nome, apelido_loja=apelido, cnpj=self._cnpj(5000 + i), estado="RJ", cidade="Rio de Janeiro", EstoqueNegativo="SIM", Rede="SIM", tipo_unidade=tipo, regime_tributario=Empresa.REGIME_LUCRO_REAL, ambiente_fiscal=Empresa.AMBIENTE_HOMOLOGACAO, inscricao_estadual="85000019", DataAbertura=FIXED_DATE, ativo=True))
        return lojas

    def _setores_centros(self, empresa, lojas):
        for i, loja in enumerate(lojas, 1):
            cc, _ = CentroCusto.objects.get_or_create(empresa=empresa, codigo=f"UN{i:02d}", defaults={"descricao": loja.nome_loja})
            RequisicaoSetor.objects.get_or_create(empresa=empresa, nome=loja.nome_loja, defaults={"loja": loja, "centro_custo": cc})
        setores = []
        for i, item in enumerate(self._seed_setor_items(), 1):
            fields = item["fields"]
            nome = fields["nome"]
            cc, _ = CentroCusto.objects.get_or_create(empresa=empresa, codigo=f"SET{i:02d}", defaults={"descricao": nome})
            setor, _ = RequisicaoSetor.objects.get_or_create(
                empresa=empresa,
                nome=nome,
                defaults={
                    "centro_custo": cc,
                    "descricao": fields.get("descricao", ""),
                    "ativo": fields.get("ativo", True),
                    "pode_fazer_requisicao": fields.get("pode_fazer_requisicao", True),
                    "recebe_requisicoes": fields.get("recebe_requisicoes", True),
                    "central_uso_consumo": fields.get("central_uso_consumo", False),
                    "central_manutencao": fields.get("central_manutencao", False),
                    "central_ti": fields.get("central_ti", False),
                    "responsavel_compras": fields.get("responsavel_compras", False),
                    "controla_estoque_uso_consumo": fields.get("controla_estoque_uso_consumo", False),
                },
            )
            if not setor.centro_custo_id:
                setor.centro_custo = cc
                setor.save(update_fields=["centro_custo"])
            setores.append(setor)
        return setores

    def _usuarios_funcionarios(self, empresa, lojas, perfis):
        CargoInicialService.garantir_basicos(empresa)
        User = get_user_model()
        users = []
        lojas_by_slug = {loja.apelido_loja: loja for loja in lojas}
        cargos = {c.codigo: c for c in empresa.cargos.all()}
        user_types = {"Admin": User.Type.ADMIN, "Gerente": User.Type.GERENTE, "Caixa": User.Type.CAIXA, "Vendedor": User.Type.VENDEDOR}
        for i, (username, (first_name, last_name, perfil_nome, loja_slug, type_name)) in enumerate(DEV_USERS.items(), 1):
            loja = lojas_by_slug.get(loja_slug) if loja_slug else None
            user = User(username=username, email=f"{username}@sysvar.test", empresa=empresa, loja=loja, type=user_types[type_name], perfil_principal=perfis.get(perfil_nome), is_active=True, first_name=first_name, last_name=last_name)
            user.set_password(DEV_PASSWORD)
            user.save()
            user.lojas.set(lojas if loja is None else [loja])
            cargo_key = "GERENTE" if perfil_nome == "Gerente" else "CAIXA" if perfil_nome == "Caixa" else "VENDEDOR" if perfil_nome == "Vendedor" else "ASSADM"
            categoria_legada = "Admin" if perfil_nome == "Administrador delegado" else perfil_nome
            Funcionarios.objects.create(empresa=empresa, nomefuncionario=f"{first_name} {last_name}", apelido=first_name, cpf=self._cpf(7000 + i), inicio=FIXED_DATE, cargo=cargos.get(cargo_key), categoria=categoria_legada, idloja=loja, usuario=user, email=user.email, ativo=True)
            users.append(user)
        contrato = empresa.contrato
        contrato.usuario_master = users[0]
        contrato.save(update_fields=["usuario_master", "updated_at"])
        return users

    def _fornecedores(self, empresa):
        cats = [("tecidos", "MATERIA_PRIMA"), ("roupas para revenda", "REVENDA"), ("aviamentos", "AVIAMENTO"), ("embalagens", "OUTROS"), ("material de limpeza", "OUTROS"), ("material de escritório", "OUTROS"), ("informática", "OUTROS"), ("faccionistas", "FACCAO")]
        prefixes = ["Aurora", "Bela Trama", "Costa Azul", "Nova Serra", "Rio Sul"]
        data = {}
        seq = 1
        for label, categoria in cats:
            data[label] = []
            for prefix in prefixes:
                f = Fornecedor.objects.create(empresa=empresa, tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA, documento=self._cnpj(8000 + seq), cnpj=self._cnpj(8000 + seq), nome_fornecedor=f"{prefix} {label.title()} Ltda"[:50], apelido=f"{prefix} {label}"[:18], categoria=categoria, cidade="Rio de Janeiro", estado="RJ", ativo=True)
                data[label].append(f)
                seq += 1
        return data

    def _clientes(self, empresa):
        ClientePadraoService.obter_ou_criar(empresa)
        nomes = ["Marina Costa", "Clara Azevedo", "Helena Duarte", "Bianca Ribeiro", "Laura Mendes", "Sofia Castro", "Livia Rocha", "Isabela Lima", "Renata Prado", "Camila Torres"]
        for i, nome in enumerate(nomes, 1):
            Cliente.objects.create(empresa=empresa, nome_cliente=nome, apelido=nome.split()[0], documento=self._cpf(9000 + i), cpf=self._cpf(9000 + i), cidade="Rio de Janeiro", estado="RJ", ativo=True)

    def _financeiro(self, empresa, lojas):
        bancos = ["Bradesco", "Itaú", "Bradesco", "Itaú"]
        for i, loja in enumerate(lojas):
            saldo = Decimal("5000000.00") if loja.tipo_unidade == Loja.TIPO_FABRICA else Decimal("0.00")
            ContaBancaria.objects.create(empresa=empresa, idloja=loja, banco=bancos[i], descricao=f"Conta {bancos[i]} {loja.apelido_loja}", agencia=f"{1000+i}", conta=f"000{i+1}-0", tipo_conta="CORRENTE", saldo_inicial=saldo, saldo_atual=saldo, ativo=True)
            Caixa.objects.create(empresa=empresa, idloja=loja, codigo=f"{loja.apelido_loja[:4]}01", descricao=f"Caixa {loja.nome_loja}", tipo_caixa=Caixa.TIPO_LOJA, saldo_inicial=0, saldo_atual=0, ativo=True)
        prazos = [("AV", "À vista", [0]), ("7D", "7 dias", [7]), ("14D", "14 dias", [14]), ("21D", "21 dias", [21]), ("28D", "28 dias", [28]), ("30D", "30 dias", [30]), ("60D", "60 dias", [60]), ("90D", "90 dias", [90]), ("120D", "120 dias", [120]), ("30-60", "30/60", [30, 60]), ("30-60-90", "30/60/90", [30, 60, 90]), ("30-60-90-120", "30/60/90/120", [30, 60, 90, 120])]
        prazo_objs = {}
        for codigo, desc, dias in prazos:
            prazo = PrazoPagamento.objects.create(empresa=empresa, codigo=codigo, descricao=desc, num_parcelas=len(dias), intervalo_dias=dias[0] if dias else 0, ativo=True)
            for ordem, dia in enumerate(dias, 1):
                PrazoPagamentoParcela.objects.create(prazo=prazo, ordem=ordem, dias=dia, percentual=Decimal("100.000000") / len(dias))
            prazo_objs[codigo] = prazo
        formas = [("PIX", "PIX", "PIX", [0], "AV"), ("CCR", "Crédito à vista / rotativo", "CREDITO_ROTATIVO", [30], "30D"), ("CC2", "Crédito parcelado 2x", "CREDITO_PARCELADO", [30, 60], "30-60"), ("CC3", "Crédito parcelado 3x", "CREDITO_PARCELADO", [30, 60, 90], "30-60-90"), ("CC4", "Crédito parcelado 4x", "CREDITO_PARCELADO", [30, 60, 90, 120], "30-60-90-120")]
        conta = ContaBancaria.objects.filter(empresa=empresa, idloja__tipo_unidade=Loja.TIPO_FABRICA).first()
        for codigo, desc, tipo, dias, prazo_codigo in formas:
            forma = FormaPagamento.objects.create(empresa=empresa, codigo=codigo, descricao=desc, tipo=tipo, num_parcelas=len(dias), prazo_pagamento=prazo_objs[prazo_codigo], conta_liquidacao=conta, gera_recebivel_bancario=True, ativo=True)
            for ordem, dia in enumerate(dias, 1):
                FormaPagamentoParcela.objects.create(forma=forma, ordem=ordem, dias=dia, percentual=Decimal("100.000000") / len(dias))

    def _base_produto(self, empresa):
        unidades = {}
        for item in self._seed_unidade_items():
            fields = item["fields"]
            unidade, _ = Unidade.objects.update_or_create(
                empresa=empresa,
                Codigo=fields["Codigo"],
                defaults={"Descricao": fields["Descricao"], "permite_decimal": fields.get("permite_decimal", False)},
            )
            unidades[fields["Codigo"].upper()] = unidade
        unidade_pc = unidades["PC"]
        unidade_m = unidades["M"]
        Ncm.objects.create(empresa=empresa, ncm="6204.42.00", descricao="Vestuário feminino", categoria=Ncm.CATEGORIA_VESTUARIO, ativo=True)
        ConfigEan.objects.create(empresa=empresa, country_prefix="789", company_prefix="4321", next_itemref=1, ativo=True)
        cores = [Cor.objects.create(empresa=empresa, Descricao=n, Codigo=n[:2].upper(), Cor=n, Status="ATIVO") for n in ["Branco", "Preto", "Vermelho", "Azul", "Verde", "Jeans Claro", "Jeans Escuro"]]
        grades = []
        for desc, sizes in [("Grade PP ao GG", ["PP", "P", "M", "G", "GG"]), ("Grade P ao G", ["P", "M", "G"]), ("Grade Numérica 36 ao 48", ["36", "38", "40", "42", "44", "46", "48"])]:
            grade = Grade.objects.create(empresa=empresa, Descricao=desc, Status="ATIVO")
            grades.append(grade)
            qtd = 50 // len(sizes)
            resto = 50 - qtd * len(sizes)
            pack = Pack.objects.create(empresa=empresa, nome=f"Pack {desc}", grade=grade, ativo=True)
            for idx, size in enumerate(sizes):
                tam = Tamanho.objects.create(empresa=empresa, idgrade=grade, Tamanho=size, Descricao=size, Status="ATIVO")
                PackItem.objects.create(pack=pack, tamanho=tam, qtd=qtd + (resto if idx == 0 else 0))
        grupos = {}
        for i, nome in enumerate(["Calça", "Saia", "Blusa", "Vestido", "Top", "Calçado", "Bijuteria"], 1):
            grupos[nome] = Grupo.objects.create(empresa=empresa, Codigo=nome.upper()[:8], CodigoRef=f"{i:02d}", Descricao=nome, Margem=Decimal("250.00"))
        subgrupos = {}
        for grupo_nome, nomes in {"Calça": ["Liso", "Estampado", "Jeans"], "Vestido": ["Liso", "Estampado", "Festa", "Renda", "Malha"], "Saia": ["Liso", "Estampado"], "Blusa": ["Liso", "Estampado"], "Top": ["Liso", "Estampado"], "Calçado": ["Liso"], "Bijuteria": ["Liso"]}.items():
            for nome in nomes:
                subgrupos[(grupo_nome, nome)] = Subgrupo.objects.create(empresa=empresa, Idgrupo=grupos[grupo_nome], Descricao=nome, Margem=Decimal("250.00"))
        colecao = Colecao.objects.create(empresa=empresa, Descricao="Verão 2027", Codigo="27", Estacao="01", Status="AT")
        tabela = Tabelapreco.objects.create(empresa=empresa, NomeTabela="Tabela Padrão", DataInicio=FIXED_DATE)
        materiais = {n: Material.objects.create(empresa=empresa, Descricao=n, Codigo=n[:6].upper(), Status="ATIVO") for n in ["Algodão", "Viscose", "Jeans", "Malha", "Renda"]}
        return {"unidade_pc": unidade_pc, "unidade_m": unidade_m, "cores": cores, "grades": grades, "grupos": grupos, "subgrupos": subgrupos, "colecao": colecao, "tabela": tabela, "materiais": materiais}

    def _produtos(self, empresa, base, fornecedores):
        insumos = self._insumos(empresa, base, fornecedores)
        nomes = ["Blusa Feminina Lisa Manga Curta", "Vestido Midi Estampado", "Calça Alfaiataria Feminina", "Saia Midi Lisa", "Top Cropped Canelado", "Calça Jeans Skinny", "Vestido Festa Midi Renda"]
        grupos = list(base["grupos"].values())
        cores = base["cores"]
        grades = base["grades"]
        vendaveis = []
        fornecedor_cycle = cycle(fornecedores["roupas para revenda"])
        for kind, total in [("1", 100), ("3", 100)]:
            for i in range(total):
                grupo = grupos[i % len(grupos)]
                sub = Subgrupo.objects.filter(empresa=empresa, Idgrupo=grupo).order_by("Idsubgrupo")[i % Subgrupo.objects.filter(empresa=empresa, Idgrupo=grupo).count()]
                grade = grades[2] if grupo.Descricao in ["Calça", "Calçado"] else grades[i % 2]
                custo = Decimal("35.00") + Decimal(i % 37) * Decimal("2.15")
                produto = Produto.objects.create(empresa=empresa, tipo_produto=kind, descricao=f"{nomes[i % len(nomes)]} {['Solar','Praia','Urbano','Leve'][i % 4]}", descricao_reduzida=f"{grupo.Descricao} {i+1}", unidade=base["unidade_pc"], grupo=grupo, subgrupo=sub, colecao=base["colecao"], material=list(base["materiais"].values())[i % len(base["materiais"])], grade=grade, ncm="6204.42.00", origem_mercadoria=0, custo_original=custo, custo_ultima_compra=custo, custo_medio=custo)
                TabelaprecoProduto.objects.create(produto=produto, tabela=base["tabela"], preco=(custo * Decimal("3.5")).quantize(Decimal("0.01")), ativo=True)
                if kind == "1":
                    forn = next(fornecedor_cycle)
                    ProdutoFornecedor.objects.create(empresa=empresa, fornecedor=forn, produto=produto, codigo_produto_fornecedor=f"REV-{produto.referencia}", descricao_fornecedor=produto.descricao, unidade_fornecedor="PC")
                use_cores = cores[5:7] if "Jeans" in sub.Descricao else [cores[i % 5], cores[(i + 2) % 5]]
                for cor in use_cores:
                    for tam in Tamanho.objects.filter(empresa=empresa, idgrade=grade):
                        ProdutoDetalhe.objects.create(produto=produto, idcor=cor, idtamanho=tam, custo_original=custo, custo_ultima_compra=custo, custo_medio=custo)
                vendaveis.append(produto)
                if kind == "3":
                    self._ficha(empresa, produto, insumos, fornecedores["faccionistas"][i % 5], i)
        return vendaveis, insumos

    def _insumos(self, empresa, base, fornecedores):
        result = []
        specs = [("Tecido Viscose", "INSUMO", "tecidos", Decimal("18.50")), ("Tecido Algodão", "INSUMO", "tecidos", Decimal("22.00")), ("Tecido Jeans", "INSUMO", "tecidos", Decimal("28.00")), ("Botão Simples", "AVIAMENTO", "aviamentos", Decimal("0.35")), ("Botão Decorativo", "AVIAMENTO", "aviamentos", Decimal("0.90")), ("Zíper Metálico", "AVIAMENTO", "aviamentos", Decimal("3.40")), ("Zíper Flexível", "AVIAMENTO", "aviamentos", Decimal("2.20")), ("Renda", "AVIAMENTO", "aviamentos", Decimal("6.80"))]
        for i, (nome, _tipo, cat, custo) in enumerate(specs, 1):
            produto = Produto.objects.create(empresa=empresa, tipo_produto="4", descricao=nome, descricao_reduzida=nome[:60], unidade=base["unidade_m"] if "Tecido" in nome or nome == "Renda" else base["unidade_pc"], material=list(base["materiais"].values())[i % len(base["materiais"])], custo_original=custo, custo_ultima_compra=custo, custo_medio=custo, ncm="6204.42.00")
            ProdutoFornecedor.objects.create(empresa=empresa, fornecedor=fornecedores[cat][i % 5], produto=produto, codigo_produto_fornecedor=f"INS-{i:03d}", descricao_fornecedor=nome, unidade_fornecedor=produto.unidade.Codigo)
            result.append(produto)
        return result

    def _ficha(self, empresa, produto, insumos, faccionista, idx):
        ficha = FichaTecnica.objects.create(empresa=empresa, produto_final=produto, versao="1", descricao=f"Ficha {produto.referencia}", rendimento=1, status=FichaTecnica.STATUS_APROVADA, ativa=True)
        tecido = insumos[idx % 3]
        FichaTecnicaItem.objects.create(ficha=ficha, tipo=FichaTecnicaItem.TIPO_INSUMO, produto=tecido, unidade=tecido.unidade, quantidade=Decimal("1.2000") + Decimal(idx % 4) / Decimal("10"), perda_percentual=Decimal("3.00"), custo_unitario_previsto=tecido.custo_medio, ordem=1)
        if produto.grupo and produto.grupo.Descricao in ["Calça", "Vestido"]:
            aviamento = insumos[3 + (idx % 5)]
            FichaTecnicaItem.objects.create(ficha=ficha, tipo=FichaTecnicaItem.TIPO_AVIAMENTO, produto=aviamento, unidade=aviamento.unidade, quantidade=Decimal("1.0000"), custo_unitario_previsto=aviamento.custo_medio, ordem=2)
        FichaTecnicaItem.objects.create(ficha=ficha, tipo=FichaTecnicaItem.TIPO_SERVICO, fornecedor=faccionista, descricao="Mão de obra de facção", quantidade=Decimal("1.0000"), custo_unitario_previsto=Decimal("18.00") + Decimal(idx % 9), ordem=9)

    def _estoque(self, lojas, produtos):
        for produto in produtos:
            for sku in produto.skus.all():
                for loja in lojas:
                    saldo = Decimal("100.000") if loja.tipo_unidade == Loja.TIPO_FABRICA else Decimal("50.000")
                    Estoque.objects.create(Idloja=loja, CodigodeBarra=sku.ean13, referencia=produto.referencia or "", Estoque=saldo, reserva=0)

    def _count_report(self, empresa, report=None):
        r = report or self.report
        skus = ProdutoDetalhe.objects.filter(produto__empresa=empresa)
        mapping = {"empresas": 1, "unidades": Loja.objects.filter(empresa=empresa).count(), "usuários": get_user_model().objects.filter(empresa=empresa).count(), "setores": RequisicaoSetor.objects.filter(empresa=empresa).count(), "centros de custo": CentroCusto.objects.filter(empresa=empresa).count(), "fornecedores": Fornecedor.objects.filter(empresa=empresa).count(), "clientes": Cliente.objects.filter(empresa=empresa).count(), "contas": ContaBancaria.objects.filter(empresa=empresa).count(), "formas de pagamento": FormaPagamento.objects.filter(empresa=empresa).count(), "prazos": PrazoPagamento.objects.filter(empresa=empresa).count(), "coleções": Colecao.objects.filter(empresa=empresa).count(), "grupos": Grupo.objects.filter(empresa=empresa).count(), "subgrupos": Subgrupo.objects.filter(empresa=empresa).count(), "cores": Cor.objects.filter(empresa=empresa).count(), "tamanhos": Tamanho.objects.filter(empresa=empresa).count(), "grades": Grade.objects.filter(empresa=empresa).count(), "packs": Pack.objects.filter(empresa=empresa).count(), "referências": Produto.objects.filter(empresa=empresa, tipo_produto__in=["1", "3"]).count(), "SKUs": skus.count(), "EANs": skus.exclude(ean13="").count(), "produtos próprios": Produto.objects.filter(empresa=empresa, tipo_produto="3").count(), "produtos de revenda": Produto.objects.filter(empresa=empresa, tipo_produto="1").count(), "insumos": Produto.objects.filter(empresa=empresa, tipo_produto="4").count(), "fichas técnicas": FichaTecnica.objects.filter(empresa=empresa).count(), "vínculos produto-fornecedor": ProdutoFornecedor.objects.filter(empresa=empresa).count(), "registros/saldos de estoque": Estoque.objects.filter(Idloja__empresa=empresa).count(), "usuários da base": get_user_model().objects.filter(username__in=DEV_USERS).count(), "superusuários recriados": get_user_model().objects.filter(username=PRESERVED_SUPERUSER, is_superuser=True).count(), "usuários residuais": max(0, get_user_model().objects.exclude(username__in=ALLOWED_USERS).count())}
        for key, value in mapping.items():
            r.set(key, value)

    def _count_global_report(self):
        User = get_user_model()
        self.report.set("empresas", Empresa.objects.count())
        self.report.set("usuários", User.objects.count())
        self.report.set("superusuários recriados", User.objects.filter(username=PRESERVED_SUPERUSER, is_superuser=True).count())
        self.report.set("usuários residuais", User.objects.exclude(username__in=ALLOWED_USERS).count())

    def _profiles_are_valid(self, empresa):
        User = get_user_model()
        for username, (_first, _last, perfil_nome, _loja_slug, _type_name) in DEV_USERS.items():
            user = User.objects.filter(username=username, empresa=empresa).select_related("perfil_principal").first()
            if not user or not user.perfil_principal or user.perfil_principal.nome != perfil_nome:
                return False
        admin = User.objects.filter(username="admin.delegado", empresa=empresa).select_related("perfil_principal").first()
        if not admin or not admin.perfil_principal:
            return False
        fiscal = admin.perfil_principal.permissoes_modulos.filter(modulo__chave="fiscal", acesso=UserModulePermission.Access.EDIT).exists()
        return fiscal

    def _forbidden_operational_labels(self):
        labels = []
        for app_label, model_name in FORBIDDEN_OPERATIONAL_MODELS:
            model = apps.get_model(app_label, model_name)
            if model.objects.exists():
                labels.append(model._meta.label)
        return labels

    def _cpf(self, seed):
        base = f"{seed:09d}"[-9:]
        d1 = self._cpf_dv(base)
        d2 = self._cpf_dv(base + str(d1))
        return f"{base}{d1}{d2}"

    def _cpf_dv(self, nums):
        resto = (sum(int(n) * w for n, w in zip(nums, range(len(nums) + 1, 1, -1))) * 10) % 11
        return 0 if resto == 10 else resto

    def _cnpj(self, seed):
        base = f"{seed:08d}0001"[-12:]
        d1 = self._cnpj_dv(base, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
        d2 = self._cnpj_dv(base + str(d1), [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
        return f"{base}{d1}{d2}"

    def _cnpj_dv(self, nums, pesos):
        resto = sum(int(n) * p for n, p in zip(nums, pesos)) % 11
        return 0 if resto < 2 else 11 - resto
