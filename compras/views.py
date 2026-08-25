from rest_framework import viewsets, status
from rest_framework.permissions import BasePermission
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from django.db.models import Avg, Max, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from accounts.permissions import HasModuleRole
from accounts.services.effective_access import EDIT, VIEW, EffectiveAccessService

from auditoria.models import AuditAction, AuditCategory
from auditoria.services import AuditService

from .models import (
    Cotacao,
    CotacaoFornecedor,
    CotacaoItem,
    CotacaoProposta,
    CotacaoRequisicao,
    OrdemServico,
    OrdemServicoMaterial,
    PedidoCompra,
    PedidoCompraItem,
    PedidoCompraEntrega,
    PedidoCompraParcela,
    Requisicao,
    RequisicaoHistorico,
    RequisicaoItem,
    RequisicaoFinalidadeAquisicao,
    RequisicaoMaterialCategoria,
    RequisicaoMatrizResponsabilidade,
    RequisicaoServicoCategoria,
    RequisicaoSetor,
)
from .serializers import (
    CotacaoItemSerializer,
    CotacaoFornecedorSerializer,
    CotacaoPropostaSerializer,
    CotacaoSerializer,
    OrdemServicoMaterialSerializer,
    OrdemServicoSerializer,
    PedidoCompraSerializer,
    PedidoCompraItemSerializer,
    PedidoCompraEntregaSerializer,
    PedidoCompraParcelaSerializer,
    RequisicaoHistoricoSerializer,
    RequisicaoItemSerializer,
    RequisicaoSerializer,
    RequisicaoFinalidadeAquisicaoSerializer,
    RequisicaoMaterialCategoriaSerializer,
    RequisicaoMatrizResponsabilidadeSerializer,
    RequisicaoServicoCategoriaSerializer,
    RequisicaoSetorSerializer,
)
from .services_requisicao import (
    atualizar_status_material_os,
    atualizar_status_material_ordem_servico,
    estoque_disponivel_material_os,
    garantir_ordem_servico_requisicao,
    loja_almoxarifado_central,
    resolver_responsabilidade_requisicao,
    sincronizar_requisicao_com_ordem_servico,
)

# Integração Financeiro
FIN_OK = True
try:
    from financeiro.models import (
        Pagar,
        PagarItem,
        FormaPagamento,
        FormaPagamentoParcela,
        PrazoPagamento,
        PrazoPagamentoParcela,
    )
except Exception:
    FIN_OK = False
    Pagar = PagarItem = FormaPagamento = FormaPagamentoParcela = PrazoPagamento = PrazoPagamentoParcela = None

# Natureza para aprovar
from cadastros.models import Nat_Lancamento
from cadastros.models import Loja
from produto.models import ProdutoUsoConsumoEstoque, ProdutoUsoConsumoMovimentacao
from .services_necessidade import estoque_disponivel_requisicao_item, indicador_requisicao_item, loja_estoque_requisicao_item, sincronizar_requisicao_disponivel_para_atendimento


# ----------------- Auditoria robusta -----------------
def _audit(model_name: str, obj_id: str, changes: dict, request, action: str = "custom"):
    payload = {
        "action": AuditAction.OBJECT_UPDATED,
        "category": AuditCategory.PURCHASE,
        "request": request,
        "user": getattr(request, "user", None),
        "app_label": "compras",
        "model": model_name,
        "object_id": obj_id,
        "metadata": {"legacy_action": action, "changes": changes},
    }
    if transaction.get_connection().in_atomic_block:
        transaction.on_commit(lambda: AuditService.success(**payload))
    else:
        AuditService.success(**payload)


def _historico(requisicao, request, acao, status_anterior="", status_novo="", item=None, valor_anterior=None, valor_novo=None, observacao=""):
    hist = RequisicaoHistorico.objects.create(
        requisicao=requisicao,
        item=item,
        usuario=getattr(request, "user", None),
        acao=acao,
        status_anterior=status_anterior or "",
        status_novo=status_novo or "",
        valor_anterior=valor_anterior,
        valor_novo=valor_novo,
        observacao=observacao or "",
    )
    _audit(
        "requisicao",
        requisicao.pk,
        {
            "acao": acao,
            "item": getattr(item, "pk", None),
            "status": [status_anterior, status_novo],
            "valor_anterior": valor_anterior,
            "valor_novo": valor_novo,
            "observacao": observacao,
        },
        request,
        action=acao.lower(),
    )
    return hist


REQ_FAZER = "requisicoes.fazer"
REQ_APROVAR = "requisicoes.aprovar"
REQ_ATENDER = "requisicoes.atender"
COTACAO_APROVAR = "cotacao.aprovar"


def _requisicao_access(user):
    return EffectiveAccessService(user)


def _is_requisicao_admin(user):
    service = _requisicao_access(user)
    return bool(getattr(user, "is_superuser", False) or service.is_company_master())


def _can_manage_requisicao(user):
    return _requisicao_access(user).has_process_permission(REQ_ATENDER)


def _can_approve_requisicao(user):
    return _requisicao_access(user).has_process_permission(REQ_APROVAR)


def _can_request_requisicao(user):
    return _requisicao_access(user).has_process_permission(REQ_FAZER)


def _can_approve_cotacao(user):
    return bool(getattr(user, "is_superuser", False) or _requisicao_access(user).has_process_permission(COTACAO_APROVAR))


def _can_view_all_requisicao(user):
    return _is_requisicao_admin(user) or _can_manage_requisicao(user)


def _can_edit_requisicao_content(user, requisicao):
    if _is_requisicao_admin(user):
        return True
    return _can_request_requisicao(user) and requisicao.requisitante_id == user.id and requisicao.status in {"RASCUNHO", "DEVOLVIDA_CORRECAO"}


def _scope_requisicao_queryset(qs, user):
    if _is_requisicao_admin(user):
        return qs
    allowed = EffectiveAccessService(user).allowed_store_ids()
    if allowed is not None:
        qs = qs.filter(loja_id__in=allowed)
    return qs


def _cotacao_store_filter_ids(user):
    access = EffectiveAccessService(user)
    if getattr(user, "is_superuser", False) or access.is_company_master() or getattr(user, "type", "") == "Admin":
        return None
    return access.allowed_store_ids()


def _scope_cotacao_requisicao_queryset(qs, user):
    allowed = _cotacao_store_filter_ids(user)
    if allowed is not None:
        qs = qs.filter(loja_id__in=allowed)
    return qs


def _has_any_requisicao_permission(user):
    return _can_request_requisicao(user) or _can_approve_requisicao(user) or _can_manage_requisicao(user)


class HasRequisicaoProcessAccess(BasePermission):
    message = "Usuário sem autorização de requisições."

    ACTION_CODES = {
        "create": [REQ_FAZER],
        "partial_update": [REQ_FAZER],
        "update": [REQ_FAZER],
        "enviar": [REQ_FAZER],
        "salvar_enviar": [REQ_FAZER],
        "cancelar": [REQ_FAZER],
        "aprovar": [REQ_APROVAR],
        "rejeitar": [REQ_APROVAR],
        "devolver": [REQ_APROVAR],
        "atender": [REQ_ATENDER],
        "aguardar_cotacao": [REQ_ATENDER],
        "ativar": [REQ_FAZER],
        "inativar": [REQ_FAZER],
    }

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        if _is_requisicao_admin(user):
            return True
        action = getattr(view, "action", None)
        if action in {"list", "retrieve", "lojas_permitidas"}:
            if action == "lojas_permitidas" and getattr(user, "type", "") == "Admin":
                return True
            return _has_any_requisicao_permission(user)
        codes = self.ACTION_CODES.get(action, [])
        return any(_requisicao_access(user).has_process_permission(code) for code in codes)


def _ensure_default_requisicao_servico_categorias(empresa_id):
    if not empresa_id:
        return
    nomes = [
        "Ar-condicionado",
        "Eletrica",
        "Hidraulica",
        "Informatica",
        "Impressoras",
        "Moveis",
        "Equipamentos",
        "Seguranca",
        "Limpeza",
        "Pintura",
        "Outros",
    ]
    for nome in nomes:
        RequisicaoServicoCategoria.objects.get_or_create(empresa_id=empresa_id, nome=nome)


def _ensure_default_requisicao_setores(empresa_id):
    if not empresa_id:
        return
    nomes = [
        "Administrativo",
        "Financeiro",
        "TI",
        "Almoxarifado",
        "Estoque",
        "Vendas",
        "Diretoria",
        "Manutencao",
    ]
    for nome in nomes:
        defaults = {}
        if nome in {"Almoxarifado", "Estoque"}:
            defaults["controla_estoque_uso_consumo"] = True
        RequisicaoSetor.objects.get_or_create(empresa_id=empresa_id, nome=nome, defaults=defaults)


def _ensure_default_requisicao_material_categorias(empresa_id):
    if not empresa_id:
        return
    nomes = [
        "Informática",
        "Mobiliário",
        "Equipamentos",
        "Material de escritório",
        "Limpeza",
        "Segurança",
        "Comunicação",
        "Outros",
    ]
    for nome in nomes:
        RequisicaoMaterialCategoria.objects.get_or_create(empresa_id=empresa_id, nome=nome)


def _ensure_default_requisicao_finalidades(empresa_id):
    if not empresa_id:
        return
    defaults = [
        ("Uso e Consumo", "USO_CONSUMO"),
        ("Estoque/Almoxarifado", "ALMOXARIFADO"),
        ("Imobilizado", "IMOBILIZADO"),
        ("Outro", "OUTRO"),
    ]
    for nome, comportamento in defaults:
        if not RequisicaoFinalidadeAquisicao.objects.filter(empresa_id=empresa_id, comportamento=comportamento).exists():
            RequisicaoFinalidadeAquisicao.objects.create(empresa_id=empresa_id, comportamento=comportamento, nome=nome)


def _recalcular_status_requisicao(req):
    itens = list(req.itens.all())
    if not itens or req.status in {"RASCUNHO", "SOLICITADA", "AGUARDANDO_APROVACAO", "REJEITADA", "CANCELADA"}:
        return req.status
    statuses = {i.status for i in itens}
    if statuses and statuses <= {"ATENDIDO", "SERVICO_CONCLUIDO", "RECEBIDO"}:
        return "CONCLUIDA"
    if any(i.qtd_atendida and i.qtd_atendida > 0 for i in itens):
        return "ATENDIDA_PARCIALMENTE"
    if statuses & {"AGUARDANDO_COTACAO", "EM_COTACAO", "PEDIDO_GERADO", "AGUARDANDO_RECEBIMENTO"}:
        return "EM_PROCESSO_COMPRA"
    return "EM_ATENDIMENTO" if req.status == "APROVADA" else req.status


def _parcelas_configuradas(pedido: PedidoCompra):
    if not FIN_OK or not pedido.forma_pagamento:
        return None, None, []
    forma = (
        FormaPagamento.objects
        .filter(Q(empresa=pedido.empresa) | Q(empresa__isnull=True), codigo=pedido.forma_pagamento, ativo=True)
        .first()
    )
    prazo = pedido.prazo_pagamento
    if prazo:
        cfg = list(PrazoPagamentoParcela.objects.filter(prazo=prazo).order_by("ordem"))
    elif forma:
        prazo = forma.prazo_pagamento
        cfg = list(FormaPagamentoParcela.objects.filter(forma=forma).order_by("ordem"))
    else:
        cfg = []
    return forma, prazo, cfg


def _sincronizar_parcelas_planejadas(pedido: PedidoCompra, request=None, motivo="sync_total"):
    if not FIN_OK or not pedido.forma_pagamento:
        return
    forma, prazo, cfg = _parcelas_configuradas(pedido)
    if not forma or not cfg:
        raise ValidationError({"parcelas": "Não foi possível regenerar as parcelas planejadas."})

    total = Decimal(pedido.total_pedido or 0).quantize(Decimal("0.01"))
    emissao = pedido.emissao
    PedidoCompraParcela.objects.filter(pedido=pedido, status="PLAN").delete()
    restante = total
    n = len(cfg)
    vals = []
    for i, par in enumerate(cfg, start=1):
        if par.percentual is not None:
            perc = Decimal(str(par.percentual))
            if perc > 1:
                perc = perc / Decimal("100")
            val = (total * perc).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        elif i < n:
            val = (total / n).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            val = restante.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        restante -= val
        vencto = emissao + timedelta(days=int(par.dias))
        PedidoCompraParcela.objects.create(
            pedido=pedido,
            parcela_n=i,
            vencimento=vencto,
            valor=val,
            percentual=par.percentual,
            origem="FORMA",
            status="PLAN",
        )
        vals.append({"parcela_n": i, "vencimento": vencto.isoformat(), "valor": float(val)})
    if request:
        _audit("pedidocompra", pedido.pk, {"parcelas_regeneradas": vals, "motivo": motivo}, request, action="sync_parcelas")


class BaseViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModuleRole]
    required_module = "compras"
    read_roles = ["Admin", "Diretor", "Gerente", "AssistentePagar"]
    write_roles = ["Admin", "Diretor", "Gerente", "AssistentePagar"]

    def _empresa_id_usuario(self):
        user = self.request.user
        if user.is_superuser:
            return self.request.query_params.get("empresa")
        return getattr(user, "empresa_id", None)


# ----------------- Pedido -----------------
class CotacaoViewSet(BaseViewSet):
    queryset = Cotacao.objects.select_related("empresa", "loja", "responsavel").prefetch_related("itens", "fornecedores_participantes").all()
    serializer_class = CotacaoSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        loja = self.request.query_params.get("loja")
        status_q = self.request.query_params.get("status")
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        allowed = _cotacao_store_filter_ids(self.request.user)
        if allowed is not None:
            qs = qs.filter(loja_id__in=allowed)
        if loja:
            qs = qs.filter(loja_id=loja)
        if status_q:
            qs = qs.filter(status=status_q)
        return qs.order_by("-data_abertura", "-numero")

    def _validate_loja(self, loja):
        empresa_id = self._empresa_id_usuario()
        if not loja:
            raise ValidationError({"loja": "Informe a loja."})
        if empresa_id and loja.empresa_id != int(empresa_id):
            raise ValidationError({"loja": "A loja informada pertence a outra empresa."})
        if not EffectiveAccessService(self.request.user).can_access_store(loja):
            raise ValidationError({"loja": "Loja fora do escopo permitido."})

    def perform_create(self, serializer):
        loja = serializer.validated_data.get("loja")
        self._validate_loja(loja)
        serializer.save(empresa=loja.empresa, responsavel=self.request.user, status="EM_ELABORACAO")

    def perform_update(self, serializer):
        if serializer.instance.status != "EM_ELABORACAO":
            raise ValidationError({"status": "Somente cotações em elaboração podem ser editadas."})
        loja = serializer.validated_data.get("loja") or serializer.instance.loja
        self._validate_loja(loja)
        serializer.save(empresa=loja.empresa)

    def _requisicoes_disponiveis_qs(self):
        qs = Requisicao.objects.select_related("loja", "setor", "requisitante").prefetch_related("itens").filter(
            status__in=["APROVADA", "EM_PROCESSO_COMPRA", "EM_PROCESSO_CONTRATACAO"],
            itens__qtd_pendente__gt=0,
        )
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        qs = _scope_cotacao_requisicao_queryset(qs, self.request.user)
        return qs.distinct().order_by("-data_requisicao", "-numero")

    def _copiar_item_requisicao(self, cotacao, req_item):
        descricao = req_item.produto.descricao if req_item.produto_id else (req_item.descricao or req_item.titulo_servico or "")
        return CotacaoItem.objects.create(
            cotacao=cotacao,
            produto=req_item.produto,
            descricao=descricao,
            quantidade_cotar=req_item.qtd_pendente or req_item.qtd_solicitada,
            unidade=req_item.unidade,
            especificacao_tecnica=req_item.especificacao_tecnica,
            observacao=req_item.observacoes,
            requisicao_item_origem=req_item,
            origem="REQUISICAO",
        )

    def _copiar_material_os(self, cotacao, material, quantidade):
        return CotacaoItem.objects.create(
            cotacao=cotacao,
            produto=material.produto,
            descricao=material.produto.descricao if material.produto_id else material.descricao,
            quantidade_cotar=quantidade,
            unidade=material.unidade,
            observacao=material.observacoes,
            ordem_servico_material_origem=material,
            origem="OS",
        )

    def _cobertura_compra_origem(self, tipo, origem_id):
        filtros = {
            "cotacao__status__in": ["EM_ELABORACAO", "ABERTA", "PROPOSTAS_RECEBIDAS", "EM_ANALISE", "AGUARDANDO_APROVACAO", "APROVADA", "PEDIDO_GERADO"],
        }
        if tipo == "REQ":
            filtros.update({"origem": "REQUISICAO", "requisicao_item_origem_id": origem_id})
        else:
            filtros.update({"origem": "OS", "ordem_servico_material_origem_id": origem_id})
        return CotacaoItem.objects.filter(**filtros).aggregate(total=Sum("quantidade_cotar"))["total"] or Decimal("0")

    def _estoque_central_origem(self, origem):
        if not origem.get("produto"):
            return Decimal("0")
        try:
            return origem["resolver_estoque"]()
        except ValidationError:
            return Decimal("0")

    def _origens_necessidade_compra(self, request):
        categoria = request.query_params.get("categoria")
        loja = request.query_params.get("loja")
        setor = request.query_params.get("setor")
        search = (request.query_params.get("search") or "").strip()
        rows = []
        itens = RequisicaoItem.objects.select_related(
            "requisicao", "requisicao__loja", "requisicao__setor", "produto", "unidade", "categoria_material"
        ).filter(requisicao__in=self._requisicoes_disponiveis_qs(), qtd_pendente__gt=0)
        itens = itens.exclude(
            requisicao__tipo_requisicao__in=["MANUTENCAO", "TI"],
            requisicao__ordem_servico__isnull=False,
        )
        if categoria:
            itens = itens.filter(categoria_material_id=categoria)
        if loja:
            itens = itens.filter(requisicao__loja_id=loja)
        if setor:
            itens = itens.filter(requisicao__setor_id=setor)
        if search:
            itens = itens.filter(Q(produto__descricao__icontains=search) | Q(descricao__icontains=search) | Q(titulo_servico__icontains=search))
        for item in itens.order_by("produto_id", "id"):
            pendente = Decimal(item.qtd_pendente or 0)
            em_compra = self._cobertura_compra_origem("REQ", item.id)
            row = {
                "tipo_origem": "REQ",
                "origem_id": item.id,
                "documento_origem": f"REQ {item.requisicao.numero}",
                "documento_id": item.requisicao_id,
                "requisicao": item.requisicao_id,
                "produto": item.produto_id,
                "nome": item.produto.descricao if item.produto_id else (item.descricao or item.titulo_servico or f"Item {item.id}"),
                "unidade": item.unidade_id,
                "unidade_nome": getattr(item.unidade, "Descricao", None),
                "quantidade_total_solicitada": Decimal(item.qtd_solicitada or 0),
                "quantidade_pendente": pendente,
                "quantidade_em_compra": em_compra,
                "loja_nome": item.requisicao.loja.nome_loja,
                "setor_nome": item.requisicao.setor.nome,
                "numero": item.requisicao.numero,
                "resolver_estoque": lambda item=item: estoque_disponivel_requisicao_item(item),
            }
            row["estoque_central"] = self._estoque_central_origem(row)
            row.pop("resolver_estoque", None)
            rows.append(row)

        materiais = OrdemServicoMaterial.objects.select_related(
            "ordem_servico", "ordem_servico__loja", "ordem_servico__setor_solicitante", "produto", "unidade"
        ).filter(
            ordem_servico__status__in=["ABERTA", "EM_TRIAGEM", "EM_ATENDIMENTO", "AGUARDANDO_MATERIAL", "AGUARDANDO_TERCEIRO"],
            status__in=["PENDENTE", "DISPONIVEL", "EM_COMPRA"],
            qtd_pendente__gt=0,
        )
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            materiais = materiais.filter(ordem_servico__empresa_id=empresa_id)
        elif not request.user.is_superuser:
            materiais = materiais.none()
        allowed = _cotacao_store_filter_ids(request.user)
        if allowed is not None:
            materiais = materiais.filter(ordem_servico__loja_id__in=allowed)
        if loja:
            materiais = materiais.filter(ordem_servico__loja_id=loja)
        if setor:
            materiais = materiais.filter(ordem_servico__setor_solicitante_id=setor)
        if search:
            materiais = materiais.filter(Q(produto__descricao__icontains=search) | Q(descricao__icontains=search))
        for material in materiais.order_by("produto_id", "id"):
            pendente = Decimal(material.qtd_pendente or 0)
            em_compra = self._cobertura_compra_origem("OS", material.id)
            row = {
                "tipo_origem": "OS",
                "origem_id": material.id,
                "documento_origem": f"OS {material.ordem_servico_id}",
                "documento_id": material.ordem_servico_id,
                "produto": material.produto_id,
                "nome": material.produto.descricao if material.produto_id else material.descricao,
                "unidade": material.unidade_id,
                "unidade_nome": getattr(material.unidade, "Descricao", None),
                "quantidade_total_solicitada": Decimal(material.qtd_necessaria or 0),
                "quantidade_pendente": pendente,
                "quantidade_em_compra": em_compra,
                "loja_nome": material.ordem_servico.loja.nome_loja,
                "setor_nome": material.ordem_servico.setor_solicitante.nome,
                "numero": material.ordem_servico_id,
                "resolver_estoque": lambda material=material: estoque_disponivel_material_os(material),
            }
            row["estoque_central"] = self._estoque_central_origem(row)
            row.pop("resolver_estoque", None)
            rows.append(row)
        return rows

    @action(detail=False, methods=["get"], url_path="requisicoes-disponiveis")
    def requisicoes_disponiveis(self, request):
        rows = []
        for req in self._requisicoes_disponiveis_qs():
            itens = list(req.itens.all())
            rows.append({
                "id": req.id,
                "numero": req.numero,
                "loja": req.loja_id,
                "loja_nome": req.loja.nome_loja,
                "setor_nome": req.setor.nome,
                "requisitante_nome": req.requisitante.username,
                "quantidade_itens": len(itens),
                "data_requisicao": req.data_requisicao,
                "prioridade": req.prioridade,
                "itens": RequisicaoItemSerializer(itens, many=True).data,
            })
        return Response(rows)

    @action(detail=False, methods=["get"], url_path="necessidades")
    def necessidades(self, request):
        grupos = {}
        for origem in self._origens_necessidade_compra(request):
            if origem["produto"]:
                key = f"produto:{origem['produto']}"
                nome = origem["nome"]
            else:
                key = f"{origem['tipo_origem'].lower()}:{origem['origem_id']}"
                nome = origem["nome"]
            grupo = grupos.setdefault(key, {
                "key": key,
                "produto": origem["produto"],
                "nome": nome,
                "quantidade_total_solicitada": Decimal("0"),
                "quantidade_pendente": Decimal("0"),
                "estoque_central": Decimal("0"),
                "quantidade_em_compra": Decimal("0"),
                "requisicoes_ids": set(),
                "lojas": set(),
                "setores": set(),
                "origens": [],
            })
            grupo["quantidade_total_solicitada"] += origem["quantidade_total_solicitada"]
            grupo["quantidade_pendente"] += origem["quantidade_pendente"]
            grupo["estoque_central"] = max(grupo["estoque_central"], origem["estoque_central"])
            grupo["quantidade_em_compra"] += origem["quantidade_em_compra"]
            if origem["tipo_origem"] == "REQ":
                grupo["requisicoes_ids"].add(origem["documento_id"])
            grupo["lojas"].add(origem["loja_nome"])
            grupo["setores"].add(origem["setor_nome"])
            grupo["origens"].append({
                **origem,
                "key": f"{origem['tipo_origem']}:{origem['origem_id']}",
            })

        rows = []
        for grupo in grupos.values():
            requisicoes_ids = sorted(grupo["requisicoes_ids"])
            sem_cobertura = max(grupo["quantidade_pendente"] - grupo["estoque_central"] - grupo["quantidade_em_compra"], Decimal("0"))
            if sem_cobertura <= 0:
                continue
            rows.append({
                "key": grupo["key"],
                "produto": grupo["produto"],
                "nome": grupo["nome"],
                "quantidade_total_solicitada": grupo["quantidade_total_solicitada"],
                "quantidade_pendente": grupo["quantidade_pendente"],
                "estoque_central": grupo["estoque_central"],
                "quantidade_em_compra": grupo["quantidade_em_compra"],
                "quantidade_sem_cobertura": sem_cobertura,
                "numero_requisicoes": len(requisicoes_ids),
                "requisicoes_ids": requisicoes_ids,
                "lojas": sorted(grupo["lojas"]),
                "setores": sorted(grupo["setores"]),
                "origens": grupo["origens"],
            })
        return Response(rows)

    @transaction.atomic
    @action(detail=True, methods=["post"], url_path="adicionar-requisicoes")
    def adicionar_requisicoes(self, request, pk=None):
        cotacao = self.get_object()
        if cotacao.status != "EM_ELABORACAO":
            raise ValidationError({"cotacao": "Somente cotações em elaboração podem vincular requisições."})
        ids = request.data.get("requisicoes") or request.data.get("ids") or []
        if request.data.get("requisicao"):
            ids = [request.data.get("requisicao")]
        ids = [int(i) for i in ids]
        disponiveis = self._requisicoes_disponiveis_qs().filter(id__in=ids)
        encontrados = {r.id: r for r in disponiveis}
        if set(ids) != set(encontrados):
            raise ValidationError({"requisicoes": "Uma ou mais requisições não estão disponíveis para cotação."})
        existentes = set(CotacaoRequisicao.objects.filter(cotacao=cotacao, requisicao_id__in=ids).values_list("requisicao_id", flat=True))
        if existentes:
            raise ValidationError({"requisicoes": "Requisição já vinculada à cotação."})
        for req_id in ids:
            req = encontrados[req_id]
            CotacaoRequisicao.objects.create(cotacao=cotacao, requisicao=req)
            for item in req.itens.all():
                self._copiar_item_requisicao(cotacao, item)
        return Response(self.get_serializer(cotacao).data)

    @transaction.atomic
    @action(detail=True, methods=["post"], url_path="adicionar-necessidades")
    def adicionar_necessidades(self, request, pk=None):
        cotacao = self.get_object()
        if cotacao.status != "EM_ELABORACAO":
            raise ValidationError({"cotacao": "Somente cotações em elaboração podem receber necessidades."})
        keys = request.data.get("necessidades") or request.data.get("origens") or []
        if not keys:
            raise ValidationError({"necessidades": "Informe as necessidades a incluir."})
        selecionadas = set(str(k) for k in keys)
        origens = [o for o in self._origens_necessidade_compra(request) if f"{o['tipo_origem']}:{o['origem_id']}" in selecionadas or (o["produto"] and f"produto:{o['produto']}" in selecionadas)]
        encontrados = {f"{o['tipo_origem']}:{o['origem_id']}" for o in origens}
        atomicas = {k for k in selecionadas if not k.startswith("produto:")}
        if not atomicas.issubset(encontrados):
            raise ValidationError({"necessidades": "Uma ou mais necessidades não estão disponíveis para cotação."})

        por_produto = {}
        for origem in origens:
            key = f"produto:{origem['produto']}" if origem["produto"] else f"{origem['tipo_origem']}:{origem['origem_id']}"
            por_produto.setdefault(key, []).append(origem)

        criados = 0
        for grupo in por_produto.values():
            estoque_restante = max(max((o["estoque_central"] for o in grupo), default=Decimal("0")), Decimal("0"))
            for origem in sorted(grupo, key=lambda o: (o["tipo_origem"], o["origem_id"])):
                pendente_compra = max(origem["quantidade_pendente"] - origem["quantidade_em_compra"], Decimal("0"))
                coberto_estoque = min(estoque_restante, pendente_compra)
                estoque_restante -= coberto_estoque
                quantidade_cotar = pendente_compra - coberto_estoque
                if quantidade_cotar <= 0:
                    continue
                if origem["tipo_origem"] == "REQ":
                    item = RequisicaoItem.objects.select_related("requisicao", "produto", "unidade").get(pk=origem["origem_id"])
                    cot_item = self._copiar_item_requisicao(cotacao, item)
                    cot_item.quantidade_cotar = quantidade_cotar
                    cot_item.save(update_fields=["quantidade_cotar", "atualizado_em"])
                    if item.status in {"PENDENTE", "APROVADO", "AGUARDANDO_COTACAO"}:
                        item.status = "EM_COTACAO"
                        item.save(update_fields=["status", "atualizado_em"])
                else:
                    material = OrdemServicoMaterial.objects.select_related("ordem_servico", "produto", "unidade").get(pk=origem["origem_id"])
                    self._copiar_material_os(cotacao, material, quantidade_cotar)
                    if material.status in {"PENDENTE", "DISPONIVEL"}:
                        material.status = "EM_COMPRA"
                        material.save(update_fields=["status", "atualizado_em"])
                criados += 1
        if not criados:
            raise ValidationError({"necessidades": "Necessidades já cobertas por estoque ou compra em andamento."})
        return Response(self.get_serializer(cotacao).data)

    @transaction.atomic
    @action(detail=True, methods=["post"], url_path="remover-requisicao")
    def remover_requisicao(self, request, pk=None):
        cotacao = self.get_object()
        if cotacao.status != "EM_ELABORACAO":
            raise ValidationError({"cotacao": "Somente cotações em elaboração podem remover requisições."})
        req_id = request.data.get("requisicao")
        vinculo = get_object_or_404(CotacaoRequisicao, cotacao=cotacao, requisicao_id=req_id)
        CotacaoItem.objects.filter(cotacao=cotacao, origem="REQUISICAO", requisicao_item_origem__requisicao_id=req_id).delete()
        vinculo.delete()
        return Response(self.get_serializer(cotacao).data)

    def _propostas_ativas(self, cotacao):
        return CotacaoProposta.objects.filter(cotacao=cotacao, ativa=True)

    def _justificativa_obrigatoria(self, cotacao, proposta):
        propostas = list(self._propostas_ativas(cotacao))
        if len(propostas) <= 1:
            return True
        menor_total = min(p.total_proposta for p in propostas)
        return proposta.total_proposta != menor_total

    def _snapshot_proposta(self, proposta):
        forma = None
        if FIN_OK and proposta.forma_pagamento:
            forma = (
                FormaPagamento.objects
                .filter(Q(empresa=proposta.cotacao.empresa) | Q(empresa__isnull=True), codigo=proposta.forma_pagamento, ativo=True)
                .first()
            )
        return {
            "proposta": proposta.id,
            "fornecedor": proposta.cotacao_fornecedor.fornecedor_id,
            "fornecedor_nome": proposta.cotacao_fornecedor.fornecedor.nome_fornecedor,
            "frete": str(proposta.frete),
            "outras_despesas": str(proposta.outras_despesas),
            "desconto_geral": str(proposta.desconto_geral),
            "forma_pagamento": proposta.forma_pagamento or "",
            "forma_pagamento_legivel": getattr(forma, "descricao", "") or "",
            "condicao_pagamento": proposta.condicao_pagamento,
            "prazo_pagamento": proposta.prazo_pagamento_id,
            "prazo_pagamento_legivel": getattr(proposta.prazo_pagamento, "descricao", "") or proposta.condicao_pagamento,
            "condicao_pagamento_legivel": getattr(proposta.prazo_pagamento, "descricao", "") or proposta.condicao_pagamento,
            "prazo_entrega": proposta.prazo_entrega,
            "prazo_entrega_dias": proposta.prazo_entrega_dias,
            "validade_proposta": proposta.validade_proposta.isoformat() if proposta.validade_proposta else None,
            "total_final": str(proposta.total_proposta),
            "justificativa_vencedor": proposta.cotacao.justificativa_vencedor,
            "itens": [
                {
                    "cotacao_item": item.cotacao_item_id,
                    "descricao": item.cotacao_item.descricao,
                    "quantidade_ofertada": str(item.quantidade_ofertada),
                    "preco_unitario": str(item.preco_unitario),
                    "desconto_item": str(item.desconto_item),
                    "marca": item.marca,
                    "modelo_referencia": item.modelo_referencia,
                    "garantia": item.garantia,
                    "prazo_entrega_item": item.prazo_entrega_item,
                    "unidade": item.cotacao_item.unidade_id,
                    "total_item": str(item.total_item),
                }
                for item in proposta.itens.select_related("cotacao_item").all()
            ],
        }

    def _tipo_pedido_cotacao(self, tipo_compra):
        return {"REVENDA": "1", "USO_CONSUMO": "2", "INSUMO": "4"}.get(tipo_compra, "2")

    def _cotacao_tem_material_interno(self, cotacao):
        return cotacao.itens.filter(
            Q(origem="OS", ordem_servico_material_origem__produto__tipo_produto="2")
            | Q(origem="REQUISICAO", requisicao_item_origem__requisicao__tipo_requisicao="USO_CONSUMO", requisicao_item_origem__produto__tipo_produto="2")
        ).exists()

    def _loja_destino_pedido_cotacao(self, cotacao):
        if self._cotacao_tem_material_interno(cotacao):
            return loja_almoxarifado_central(cotacao.empresa)
        return cotacao.loja

    def _gerar_pedido_da_cotacao(self, cotacao, request):
        existente = getattr(cotacao, "pedido_compra_gerado", None)
        if existente:
            return existente
        snapshot = cotacao.snapshot_proposta_aprovada or {}
        proposta = CotacaoProposta.objects.select_related("cotacao_fornecedor", "cotacao_fornecedor__fornecedor", "prazo_pagamento").get(pk=cotacao.proposta_vencedora_id)
        forma_pagamento = snapshot.get("forma_pagamento") or proposta.forma_pagamento
        prazo_pagamento = snapshot.get("prazo_pagamento") or proposta.prazo_pagamento_id
        if not forma_pagamento:
            raise ValidationError({"forma_pagamento": "Informe a forma de pagamento da proposta vencedora."})
        if not prazo_pagamento:
            raise ValidationError({"prazo_pagamento": "Informe o prazo de pagamento da proposta vencedora."})
        prazo_dias = snapshot.get("prazo_entrega_dias")
        if prazo_dias is None:
            try:
                prazo_dias = int(snapshot.get("prazo_entrega") or 0)
            except (TypeError, ValueError):
                prazo_dias = None
        previsao_entrega = timezone.localdate() + timedelta(days=int(prazo_dias)) if prazo_dias is not None else None
        loja_destino = self._loja_destino_pedido_cotacao(cotacao)
        pedido = PedidoCompra.objects.create(
            empresa=cotacao.empresa,
            loja=loja_destino,
            fornecedor=proposta.cotacao_fornecedor.fornecedor,
            tipo=self._tipo_pedido_cotacao(cotacao.tipo_compra),
            emissao=timezone.localdate(),
            previsao_entrega=previsao_entrega,
            forma_pagamento=forma_pagamento,
            prazo_pagamento_id=prazo_pagamento,
            frete=Decimal(str(snapshot.get("frete") or 0)),
            outras_despesas=Decimal(str(snapshot.get("outras_despesas") or 0)),
            total_desconto=Decimal(str(snapshot.get("desconto_geral") or 0)),
            observacoes=f"Origem: Cotação {cotacao.numero}; destino recebimento: {loja_destino.nome_loja}",
            cotacao_origem=cotacao,
        )
        for item in snapshot.get("itens", []):
            cot_item = CotacaoItem.objects.filter(pk=item.get("cotacao_item"), cotacao=cotacao).first()
            pedido_item = PedidoCompraItem.objects.create(
                pedido=pedido,
                produto=getattr(cot_item, "produto", None),
                descricao_livre=(getattr(cot_item, "descricao", "") if not getattr(cot_item, "produto_id", None) else ""),
                unidade_id=item.get("unidade") or getattr(cot_item, "unidade_id", None),
                qtd=Decimal(str(item.get("quantidade_ofertada") or 0)),
                preco_unit=Decimal(str(item.get("preco_unitario") or 0)).quantize(Decimal("0.01")),
                desconto_valor=Decimal(str(item.get("desconto_item") or 0)),
                observacoes=(item.get("observacao") or "")[:180],
            )
            if cot_item and cot_item.origem in {"REQUISICAO", "OS"}:
                origem_txt = f"REQ_ITEM:{cot_item.requisicao_item_origem_id}" if cot_item.origem == "REQUISICAO" else f"OS_MATERIAL:{cot_item.ordem_servico_material_origem_id}"
                pedido_item.observacoes = (f"{pedido_item.observacoes or ''} {origem_txt}").strip()[:255]
            pedido_item.recalcular_totais()
            pedido_item.save(update_fields=["qtd", "preco_unit", "desconto_valor", "total_item", "observacoes", "unidade"])
        pedido.recomputa_totais()
        pedido.save(update_fields=["total_itens", "total_desconto", "frete", "outras_despesas", "total_pedido"])
        _sincronizar_parcelas_planejadas(pedido, request, motivo="pedido_gerado_por_cotacao")
        _audit("pedidocompra", pedido.pk, {"acao": "gerado_por_cotacao", "cotacao": cotacao.pk, "usuario": request.user.pk}, request, action="pedido_gerado_por_cotacao")
        return pedido

    def _cancelar_pedido_vinculado_se_permitido(self, cotacao, request):
        pedido = getattr(cotacao, "pedido_compra_gerado", None)
        if not pedido:
            return None
        if PedidoCompraEntrega.objects.filter(item__pedido=pedido, qtd_recebida__gt=0).exists():
            raise ValidationError({"pedido": "Pedido vinculado já possui recebimento/execução e não pode ser cancelado com segurança."})
        if (pedido.status or "").upper() != "AB":
            raise ValidationError({"pedido": "Pedido vinculado não está em aberto e não pode ser cancelado pela Cotação."})
        antes = pedido.status
        pedido.status = "CA"
        pedido.save(update_fields=["status"])
        _audit("pedidocompra", pedido.pk, {"acao": "cancelado_por_cancelamento_cotacao", "cotacao": cotacao.pk, "status": [antes, "CA"]}, request, action="cancelar_por_cotacao")
        return pedido

    @action(detail=True, methods=["post"], url_path="selecionar-vencedor")
    def selecionar_vencedor(self, request, pk=None):
        cotacao = self.get_object()
        if cotacao.status not in {"EM_ELABORACAO", "ABERTA", "PROPOSTAS_RECEBIDAS", "EM_ANALISE"}:
            raise ValidationError({"cotacao": "Cotação não permite selecionar vencedor neste status."})
        proposta_id = request.data.get("proposta")
        justificativa = (request.data.get("justificativa") or "").strip()
        proposta = get_object_or_404(CotacaoProposta, pk=proposta_id, cotacao=cotacao, ativa=True)
        if self._justificativa_obrigatoria(cotacao, proposta) and not justificativa:
            raise ValidationError({"justificativa": "Informe a justificativa da escolha."})
        anterior = cotacao.proposta_vencedora_id
        cotacao.proposta_vencedora = proposta
        cotacao.justificativa_vencedor = justificativa
        cotacao.save(update_fields=["proposta_vencedora", "justificativa_vencedor", "atualizado_em"])
        _audit("cotacao", cotacao.pk, {"acao": "selecionar_vencedor", "anterior": anterior, "novo": proposta.id, "justificativa": justificativa}, request, action="cotacao_selecionar_vencedor")
        return Response(self.get_serializer(cotacao).data)

    @action(detail=True, methods=["post"], url_path="enviar-aprovacao")
    def enviar_aprovacao(self, request, pk=None):
        cotacao = self.get_object()
        if cotacao.status not in {"EM_ELABORACAO", "ABERTA", "PROPOSTAS_RECEBIDAS", "EM_ANALISE"}:
            raise ValidationError({"cotacao": "Cotação não permite envio para aprovação neste status."})
        if not cotacao.itens.exists():
            raise ValidationError({"itens": "Cotação deve possuir itens."})
        if not self._propostas_ativas(cotacao).exists():
            raise ValidationError({"propostas": "Cotação deve possuir pelo menos uma proposta."})
        if not cotacao.proposta_vencedora_id:
            raise ValidationError({"proposta_vencedora": "Selecione uma proposta vencedora."})
        if self._justificativa_obrigatoria(cotacao, cotacao.proposta_vencedora) and not cotacao.justificativa_vencedor.strip():
            raise ValidationError({"justificativa": "Informe a justificativa da escolha."})
        cotacao.status = "AGUARDANDO_APROVACAO"
        cotacao.save(update_fields=["status", "atualizado_em"])
        _audit("cotacao", cotacao.pk, {"acao": "enviar_aprovacao", "proposta_vencedora": cotacao.proposta_vencedora_id, "justificativa": cotacao.justificativa_vencedor}, request, action="cotacao_enviar_aprovacao")
        return Response(self.get_serializer(cotacao).data)

    @action(detail=True, methods=["post"], url_path="aprovar")
    @transaction.atomic
    def aprovar(self, request, pk=None):
        if not _can_approve_cotacao(request.user):
            raise PermissionDenied("Usuário sem autorização para aprovar cotação.")
        cotacao = self.get_object()
        if cotacao.status != "AGUARDANDO_APROVACAO":
            raise ValidationError({"status": "Somente cotações aguardando aprovação podem ser aprovadas."})
        if not cotacao.proposta_vencedora_id:
            raise ValidationError({"proposta_vencedora": "Selecione uma proposta vencedora."})
        proposta = CotacaoProposta.objects.select_related("cotacao_fornecedor", "cotacao_fornecedor__fornecedor", "prazo_pagamento").get(pk=cotacao.proposta_vencedora_id)
        if not proposta.forma_pagamento:
            raise ValidationError({"forma_pagamento": "Informe a forma de pagamento da proposta vencedora."})
        if not proposta.prazo_pagamento_id:
            raise ValidationError({"prazo_pagamento": "Informe o prazo de pagamento da proposta vencedora."})
        cotacao.status = "APROVADA"
        cotacao.aprovado_por = request.user
        cotacao.aprovado_em = timezone.now()
        cotacao.snapshot_proposta_aprovada = self._snapshot_proposta(proposta)
        cotacao.save(update_fields=["status", "aprovado_por", "aprovado_em", "snapshot_proposta_aprovada", "atualizado_em"])
        pedido = self._gerar_pedido_da_cotacao(cotacao, request)
        cotacao.status = "PEDIDO_GERADO"
        cotacao.save(update_fields=["status", "atualizado_em"])
        _audit("cotacao", cotacao.pk, {"acao": "aprovar", "proposta_vencedora": proposta.id, "snapshot": True, "pedido": pedido.pk}, request, action="cotacao_aprovar")
        return Response(self.get_serializer(cotacao).data)

    @action(detail=True, methods=["post"], url_path="rejeitar")
    def rejeitar(self, request, pk=None):
        if not _can_approve_cotacao(request.user):
            raise PermissionDenied("Usuário sem autorização para rejeitar cotação.")
        motivo = (request.data.get("motivo") or "").strip()
        if not motivo:
            raise ValidationError({"motivo": "Informe o motivo da rejeição."})
        cotacao = self.get_object()
        if cotacao.status != "AGUARDANDO_APROVACAO":
            raise ValidationError({"status": "Somente cotações aguardando aprovação podem ser rejeitadas."})
        cotacao.status = "REJEITADA"
        cotacao.rejeitado_por = request.user
        cotacao.rejeitado_em = timezone.now()
        cotacao.motivo_rejeicao = motivo
        cotacao.save(update_fields=["status", "rejeitado_por", "rejeitado_em", "motivo_rejeicao", "atualizado_em"])
        _audit("cotacao", cotacao.pk, {"acao": "rejeitar", "motivo": motivo}, request, action="cotacao_rejeitar")
        return Response(self.get_serializer(cotacao).data)

    @transaction.atomic
    @action(detail=True, methods=["post"], url_path="cancelar")
    def cancelar(self, request, pk=None):
        cotacao = self.get_object()
        motivo = (request.data.get("motivo") or "").strip()
        if not motivo:
            raise ValidationError({"motivo": "Informe o motivo do cancelamento."})
        if cotacao.status in {"CANCELADA", "ENCERRADA"}:
            raise ValidationError({"status": "Cotação já está cancelada ou encerrada."})
        pedido = None
        if cotacao.status == "PEDIDO_GERADO":
            pedido = self._cancelar_pedido_vinculado_se_permitido(cotacao, request)
        cotacao.status = "CANCELADA"
        cotacao.cancelado_por = request.user
        cotacao.cancelado_em = timezone.now()
        cotacao.motivo_cancelamento = motivo
        cotacao.save(update_fields=["status", "cancelado_por", "cancelado_em", "motivo_cancelamento", "atualizado_em"])
        _audit("cotacao", cotacao.pk, {"acao": "cancelar", "motivo": motivo, "pedido": getattr(pedido, "pk", None)}, request, action="cotacao_cancelar")
        return Response(self.get_serializer(cotacao).data)

    @action(detail=True, methods=["get"], url_path="comparativo")
    def comparativo(self, request, pk=None):
        cotacao = self.get_object()
        itens = list(cotacao.itens.select_related("produto", "unidade").order_by("id"))
        propostas = list(
            CotacaoProposta.objects.select_related("cotacao_fornecedor", "cotacao_fornecedor__fornecedor", "prazo_pagamento")
            .prefetch_related("itens", "itens__cotacao_item")
            .filter(cotacao=cotacao, ativa=True)
            .order_by("cotacao_fornecedor__fornecedor__nome_fornecedor", "id")
        )
        itens_por_proposta = {p.id: {i.cotacao_item_id: i for i in p.itens.all()} for p in propostas}
        menores_preco = {}
        menores_custo = {}
        for item in itens:
            ofertas = [itens_por_proposta[p.id].get(item.id) for p in propostas]
            ofertas = [oferta for oferta in ofertas if oferta]
            if ofertas:
                menores_preco[item.id] = min(oferta.preco_unitario for oferta in ofertas)
                menores_custo[item.id] = min(oferta.total_item for oferta in ofertas)
        totais = [p.total_proposta for p in propostas]
        menor_total = min(totais) if totais else None
        maior_total = max(totais) if totais else None
        prazos = []
        for proposta in propostas:
            try:
                prazos.append((int(proposta.prazo_entrega), proposta.id))
            except (TypeError, ValueError):
                continue
        melhor_prazo = min(prazos)[0] if prazos else None
        rows = []
        for proposta in propostas:
            forma = None
            if FIN_OK and proposta.forma_pagamento:
                forma = (
                    FormaPagamento.objects
                    .filter(Q(empresa=proposta.cotacao.empresa) | Q(empresa__isnull=True), codigo=proposta.forma_pagamento, ativo=True)
                    .first()
                )
            total = proposta.total_proposta or Decimal("0")
            rows.append({
                "proposta": proposta.id,
                "cotacao_fornecedor": proposta.cotacao_fornecedor_id,
                "fornecedor": proposta.cotacao_fornecedor.fornecedor_id,
                "fornecedor_nome": proposta.cotacao_fornecedor.fornecedor.nome_fornecedor,
                "total_itens": proposta.total_itens,
                "desconto_geral": proposta.desconto_geral,
                "frete": proposta.frete,
                "outras_despesas": proposta.outras_despesas,
                "total_geral": total,
                "menor_total_geral": menor_total is not None and total == menor_total,
                "diferenca_percentual": Decimal("0") if menor_total in (None, 0) else ((total - menor_total) / menor_total * Decimal("100")).quantize(Decimal("0.01")),
                "economia_vs_mais_cara": Decimal("0") if maior_total is None else (maior_total - total),
                "prazo_entrega": proposta.prazo_entrega,
                "prazo_entrega_dias": proposta.prazo_entrega_dias,
                "melhor_prazo": melhor_prazo is not None and str(proposta.prazo_entrega).isdigit() and int(proposta.prazo_entrega) == melhor_prazo,
                "forma_pagamento": proposta.forma_pagamento or "",
                "forma_pagamento_legivel": getattr(forma, "descricao", "") or "",
                "condicao_pagamento": proposta.condicao_pagamento,
                "prazo_pagamento": proposta.prazo_pagamento_id,
                "prazo_pagamento_legivel": getattr(proposta.prazo_pagamento, "descricao", "") or proposta.condicao_pagamento,
                "condicao_pagamento_legivel": getattr(proposta.prazo_pagamento, "descricao", "") or proposta.condicao_pagamento,
                "validade_proposta": proposta.validade_proposta,
                "itens": [{
                    "cotacao_item": item.id,
                    "descricao": item.produto.descricao if item.produto_id else item.descricao,
                    "quantidade_cotar": item.quantidade_cotar,
                    "sem_oferta": itens_por_proposta[proposta.id].get(item.id) is None,
                    "quantidade_ofertada": getattr(itens_por_proposta[proposta.id].get(item.id), "quantidade_ofertada", None),
                    "preco_unitario": getattr(itens_por_proposta[proposta.id].get(item.id), "preco_unitario", None),
                    "desconto_item": getattr(itens_por_proposta[proposta.id].get(item.id), "desconto_item", None),
                    "custo_final_item": getattr(itens_por_proposta[proposta.id].get(item.id), "total_item", None),
                    "menor_preco_unitario": itens_por_proposta[proposta.id].get(item.id) is not None and itens_por_proposta[proposta.id][item.id].preco_unitario == menores_preco.get(item.id),
                    "menor_custo_final": itens_por_proposta[proposta.id].get(item.id) is not None and itens_por_proposta[proposta.id][item.id].total_item == menores_custo.get(item.id),
                    "marca": getattr(itens_por_proposta[proposta.id].get(item.id), "marca", ""),
                    "modelo_referencia": getattr(itens_por_proposta[proposta.id].get(item.id), "modelo_referencia", ""),
                    "garantia": getattr(itens_por_proposta[proposta.id].get(item.id), "garantia", ""),
                    "prazo_entrega_item": getattr(itens_por_proposta[proposta.id].get(item.id), "prazo_entrega_item", ""),
                } for item in itens],
            })
        return Response({"cotacao": cotacao.id, "itens": [{"id": i.id, "descricao": i.produto.descricao if i.produto_id else i.descricao, "quantidade_cotar": i.quantidade_cotar} for i in itens], "propostas": rows})


class CotacaoFornecedorViewSet(BaseViewSet):
    queryset = CotacaoFornecedor.objects.select_related("cotacao", "cotacao__empresa", "cotacao__loja", "fornecedor").all()
    serializer_class = CotacaoFornecedorSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        cotacao = self.request.query_params.get("cotacao")
        if empresa_id:
            qs = qs.filter(cotacao__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        allowed = _cotacao_store_filter_ids(self.request.user)
        if allowed is not None:
            qs = qs.filter(cotacao__loja_id__in=allowed)
        if cotacao:
            qs = qs.filter(cotacao_id=cotacao)
        return qs.order_by("fornecedor__nome_fornecedor", "id")

    def perform_destroy(self, instance):
        if instance.cotacao.status not in {"EM_ELABORACAO", "ABERTA", "PROPOSTAS_RECEBIDAS", "EM_ANALISE"}:
            raise ValidationError({"cotacao": "Cotação não permite remover fornecedores neste status."})
        instance.delete()


class CotacaoPropostaViewSet(BaseViewSet):
    queryset = CotacaoProposta.objects.select_related(
        "cotacao", "cotacao__empresa", "cotacao__loja", "cotacao_fornecedor", "cotacao_fornecedor__fornecedor"
    ).prefetch_related("itens", "itens__cotacao_item").all()
    serializer_class = CotacaoPropostaSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        cotacao = self.request.query_params.get("cotacao")
        cotacao_fornecedor = self.request.query_params.get("cotacao_fornecedor")
        if empresa_id:
            qs = qs.filter(cotacao__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        allowed = _cotacao_store_filter_ids(self.request.user)
        if allowed is not None:
            qs = qs.filter(cotacao__loja_id__in=allowed)
        if cotacao:
            qs = qs.filter(cotacao_id=cotacao)
        if cotacao_fornecedor:
            qs = qs.filter(cotacao_fornecedor_id=cotacao_fornecedor)
        return qs.order_by("-data_proposta", "-id")

    def perform_destroy(self, instance):
        if instance.cotacao.status not in {"EM_ELABORACAO", "ABERTA", "PROPOSTAS_RECEBIDAS", "EM_ANALISE"}:
            raise ValidationError({"cotacao": "Cotação em status final não permite alterar propostas."})
        instance.ativa = False
        instance.save(update_fields=["ativa", "atualizado_em"])


class CotacaoItemViewSet(BaseViewSet):
    queryset = CotacaoItem.objects.select_related("cotacao", "cotacao__empresa", "produto", "unidade", "requisicao_item_origem", "requisicao_item_origem__requisicao").all()
    serializer_class = CotacaoItemSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        cotacao = self.request.query_params.get("cotacao")
        if empresa_id:
            qs = qs.filter(cotacao__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        allowed = _cotacao_store_filter_ids(self.request.user)
        if allowed is not None:
            qs = qs.filter(cotacao__loja_id__in=allowed)
        if cotacao:
            qs = qs.filter(cotacao_id=cotacao)
        return qs.order_by("id")

    def perform_destroy(self, instance):
        if instance.cotacao.status != "EM_ELABORACAO":
            raise ValidationError({"cotacao": "Somente cotações em elaboração podem excluir itens."})
        instance.delete()

    def _allowed_store_ids_for_empresa(self):
        allowed = _cotacao_store_filter_ids(self.request.user)
        return allowed

    @action(detail=True, methods=["get"], url_path="apoio-decisao")
    def apoio_decisao(self, request, pk=None):
        item = self.get_object()
        produto = item.produto
        if not produto:
            return Response({
                "cotacao_item": item.id,
                "produto": None,
                "necessidade_aberta": None,
                "estoque_atual": None,
                "pedidos_pendentes": None,
                "ultimas_compras": [],
                "media_quantidades_ultimas_compras": None,
                "ultimo_preco": None,
                "preco_medio": None,
                "quantidade_cotar": item.quantidade_cotar,
            })

        empresa_id = item.cotacao.empresa_id
        allowed = self._allowed_store_ids_for_empresa()
        req_itens = RequisicaoItem.objects.filter(
            requisicao__empresa_id=empresa_id,
            requisicao__status__in=["APROVADA", "EM_PROCESSO_COMPRA", "EM_PROCESSO_CONTRATACAO"],
            produto=produto,
            qtd_pendente__gt=0,
        )
        estoques = ProdutoUsoConsumoEstoque.objects.filter(empresa_id=empresa_id, produto=produto)
        pedidos_itens = PedidoCompraItem.objects.select_related("pedido", "pedido__fornecedor").prefetch_related("entregas").filter(
            pedido__empresa_id=empresa_id,
            pedido__status__in=["AB", "AP"],
            produto=produto,
        )
        historico_itens = PedidoCompraItem.objects.select_related("pedido", "pedido__fornecedor").filter(
            pedido__empresa_id=empresa_id,
            produto=produto,
            entregas__qtd_recebida__gt=0,
        )
        if allowed is not None:
            req_itens = req_itens.filter(requisicao__loja_id__in=allowed)
            estoques = estoques.filter(loja_id__in=allowed)
            pedidos_itens = pedidos_itens.filter(pedido__loja_id__in=allowed)
            historico_itens = historico_itens.filter(pedido__loja_id__in=allowed)
        historico_itens = historico_itens.distinct().order_by("-pedido__emissao", "-id")[:3]

        necessidade = req_itens.aggregate(total=Sum("qtd_pendente"))["total"] or Decimal("0")
        estoque = estoques.aggregate(total=Sum("saldo"))["total"] or Decimal("0")
        pendente = Decimal("0")
        for pedido_item in pedidos_itens:
            recebido = pedido_item.entregas.aggregate(total=Sum("qtd_recebida"))["total"] or Decimal("0")
            saldo = Decimal(pedido_item.qtd or 0) - Decimal(recebido or 0)
            if saldo > 0:
                pendente += saldo

        ultimas = []
        for pedido_item in historico_itens:
            recebido = pedido_item.entregas.aggregate(total=Sum("qtd_recebida"))["total"] or Decimal("0")
            data_recebida = pedido_item.entregas.filter(qtd_recebida__gt=0).aggregate(data=Max("data_recebida"))["data"]
            ultimas.append({
                "data": data_recebida or pedido_item.pedido.emissao,
                "quantidade": recebido,
                "preco_unitario": pedido_item.preco_unit,
                "fornecedor": getattr(pedido_item.pedido.fornecedor, "nome_fornecedor", "") or getattr(pedido_item.pedido.fornecedor, "RazaoSocial", ""),
            })
        media_qtd = (sum((Decimal(c["quantidade"] or 0) for c in ultimas), Decimal("0")) / Decimal(len(ultimas))) if ultimas else None
        preco_medio = (sum((Decimal(c["preco_unitario"] or 0) for c in ultimas), Decimal("0")) / Decimal(len(ultimas))) if ultimas else None
        return Response({
            "cotacao_item": item.id,
            "produto": produto.pk,
            "necessidade_aberta": necessidade,
            "estoque_atual": estoque,
            "pedidos_pendentes": pendente,
            "ultimas_compras": ultimas,
            "media_quantidades_ultimas_compras": media_qtd,
            "ultimo_preco": ultimas[0]["preco_unitario"] if ultimas else None,
            "preco_medio": preco_medio,
            "quantidade_cotar": item.quantidade_cotar,
        })


class PedidoCompraViewSet(BaseViewSet):
    queryset = PedidoCompra.objects.all().order_by("-emissao", "-id")
    serializer_class = PedidoCompraSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        tipo = self.request.query_params.get("tipo")
        status_q = self.request.query_params.get("status")
        loja = self.request.query_params.get("loja")
        fornecedor = self.request.query_params.get("fornecedor")
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        if tipo in ("1", "2", "4"):
            qs = qs.filter(tipo=tipo)
        if status_q:
            qs = qs.filter(status=status_q)
        if loja:
            qs = qs.filter(loja_id=loja)
        if fornecedor:
            qs = qs.filter(fornecedor_id=fornecedor)
        return qs

    def perform_create(self, serializer):
        loja = serializer.validated_data.get("loja")
        fornecedor = serializer.validated_data.get("fornecedor")
        empresa_id = self._empresa_id_usuario()
        if not empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        if empresa_id and loja.empresa_id != int(empresa_id):
            raise ValidationError({"loja": "A loja informada pertence a outra empresa."})
        if loja.empresa_id and fornecedor.empresa_id and loja.empresa_id != fornecedor.empresa_id:
            raise ValidationError({"fornecedor": "O fornecedor informado pertence a outra empresa."})
        if fornecedor.ativo is False:
            raise ValidationError({"fornecedor": "Fornecedor inativo não pode ser utilizado em novo pedido."})
        if fornecedor.bloqueio:
            raise ValidationError({"fornecedor": "Fornecedor bloqueado não pode ser utilizado em novo pedido."})
        serializer.save(empresa=loja.empresa)

    def perform_update(self, serializer):
        if serializer.instance.cotacao_origem_id:
            raise ValidationError({"detail": "Pedido originado de cotação aprovada não permite alteração comercial."})
        loja = serializer.validated_data.get("loja") or serializer.instance.loja
        fornecedor = serializer.validated_data.get("fornecedor") or serializer.instance.fornecedor
        empresa_id = serializer.instance.empresa_id or getattr(loja, "empresa_id", None)
        user_empresa_id = self._empresa_id_usuario()
        if not user_empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        if user_empresa_id and empresa_id and int(user_empresa_id) != empresa_id:
            raise ValidationError({"empresa": "Pedido pertence a outra empresa."})
        if loja.empresa_id and fornecedor.empresa_id and loja.empresa_id != fornecedor.empresa_id:
            raise ValidationError({"fornecedor": "O fornecedor informado pertence a outra empresa."})
        if serializer.instance.status == "AB" and fornecedor.ativo is False:
            raise ValidationError({"fornecedor": "Fornecedor inativo não pode ser utilizado em novo pedido."})
        if serializer.instance.status == "AB" and fornecedor.bloqueio:
            raise ValidationError({"fornecedor": "Fornecedor bloqueado não pode ser utilizado em novo pedido."})
        obj = serializer.save(empresa=loja.empresa)
        obj.recomputa_totais()
        obj.save(update_fields=["total_itens", "total_desconto", "frete", "outras_despesas", "total_pedido"])
        _sincronizar_parcelas_planejadas(obj, self.request, motivo="alteracao_cabecalho")

    def perform_destroy(self, instance):
        if instance.status != "AB":
            raise ValidationError({"detail": "Somente pedidos em aberto (AB) podem ser excluídos."})
        if instance.cotacao_origem_id:
            raise ValidationError({"detail": "Pedido originado de cotação aprovada não pode ser excluído por edição comercial."})
        instance.delete()

    @action(detail=True, methods=["post"], url_path="set-forma-pagamento")
    @transaction.atomic
    def set_forma_pagamento(self, request, pk=None):
        """
        Seta forma de pagamento (por id ou codigo) e RECRIA as parcelas planejadas (PLAN)
        em compras_pedido_compra_parcela com base em FormaPagamentoParcela.
        Body: {"id_forma": 2} ou {"codigo_forma":"30/60", "id_prazo": 1}.
        """
        obj: PedidoCompra = self.get_object()
        if obj.cotacao_origem_id:
            return Response({"detail": "Pedido originado de cotação aprovada não permite alterar condição comercial."}, status=status.HTTP_400_BAD_REQUEST)
        if obj.status != "AB":
            return Response({"detail": "Somente AB"}, status=status.HTTP_400_BAD_REQUEST)
        if not FIN_OK:
            return Response({"detail": "Financeiro indisponível"}, status=status.HTTP_400_BAD_REQUEST)

        id_forma = request.data.get("id_forma")
        codigo = (request.data.get("codigo_forma") or "").strip()
        id_prazo = request.data.get("id_prazo") or request.data.get("prazo_pagamento")
        codigo_prazo = (request.data.get("codigo_prazo") or "").strip()

        try:
            if id_forma:
                forma = FormaPagamento.objects.filter(Q(empresa=obj.empresa) | Q(empresa__isnull=True), pk=id_forma, ativo=True).get()
            elif codigo:
                forma = FormaPagamento.objects.filter(Q(empresa=obj.empresa) | Q(empresa__isnull=True), codigo=codigo, ativo=True).get()
            else:
                return Response({"detail": "Informe id_forma ou codigo_forma"}, status=status.HTTP_400_BAD_REQUEST)
        except FormaPagamento.DoesNotExist:
            return Response({"detail": "Forma não encontrada/inativa"}, status=status.HTTP_400_BAD_REQUEST)

        prazo = None
        if id_prazo or codigo_prazo:
            try:
                if id_prazo:
                    prazo = PrazoPagamento.objects.filter(Q(empresa=obj.empresa) | Q(empresa__isnull=True), pk=id_prazo, ativo=True).get()
                else:
                    prazo = PrazoPagamento.objects.filter(Q(empresa=obj.empresa) | Q(empresa__isnull=True), codigo=codigo_prazo, ativo=True).get()
            except PrazoPagamento.DoesNotExist:
                return Response({"detail": "Prazo não encontrado/inativo"}, status=status.HTTP_400_BAD_REQUEST)
        else:
            prazo = forma.prazo_pagamento

        if prazo:
            cfg = list(PrazoPagamentoParcela.objects.filter(prazo=prazo).order_by("ordem"))
        else:
            cfg = list(FormaPagamentoParcela.objects.filter(forma=forma).order_by("ordem"))
        if not cfg:
            return Response({"detail": "Prazo sem parcelas configuradas"}, status=status.HTTP_400_BAD_REQUEST)

        obj.recomputa_totais()
        obj.save(update_fields=["total_itens", "total_desconto", "frete", "outras_despesas", "total_pedido"])

        before = obj.forma_pagamento
        before_prazo = obj.prazo_pagamento_id
        obj.forma_pagamento = forma.codigo
        obj.prazo_pagamento = prazo
        obj.save(update_fields=["forma_pagamento", "prazo_pagamento"])
        _sincronizar_parcelas_planejadas(obj, request, motivo="set_forma_pagamento")

        _audit(
            "pedidocompra",
            obj.pk,
            {"set_forma": {"before": before, "after": forma.codigo}, "set_prazo": {"before": before_prazo, "after": getattr(prazo, "pk", None)}},
            request,
            action="set_forma_pagto",
        )
        return Response(self.get_serializer(obj).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="aprovar")
    @transaction.atomic
    def aprovar(self, request, pk=None):
        """
        Aprova pedido (AB->AP) e gera títulos PREVISTO no Financeiro a partir das parcelas PLAN.

        Body esperado:
            {"idnatureza": <ID obrigatório>}
        """
        obj: PedidoCompra = self.get_object()

        # 1) Status precisa ser AB
        if (obj.status or "").upper() != "AB":
            return Response(
                {"detail": "Só é possível aprovar pedidos em aberto (AB)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 2) Financeiro disponível
        if not FIN_OK:
            return Response(
                {"detail": "Financeiro indisponível para aprovação."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not obj.itens.exists():
            return Response({"detail": "Inclua ao menos um item antes de aprovar."}, status=status.HTTP_400_BAD_REQUEST)
        tipos_itens = set(obj.itens.values_list("produto__tipo_produto", flat=True))
        if len(tipos_itens) != 1 or obj.tipo not in tipos_itens or obj.tipo not in ("1", "2", "4"):
            return Response({"detail": "Pedido possui tipo indefinido ou itens de tipos diferentes."}, status=status.HTTP_400_BAD_REQUEST)

        # 3) Forma de pagamento precisa estar definida
        if not obj.forma_pagamento:
            return Response(
                {"detail": "Defina a forma de pagamento / gere parcelas antes de aprovar."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 4) Natureza (idnatureza) obrigatória e válida
        raw_idnat = request.data.get("idnatureza")
        try:
            idnat = int(raw_idnat)
        except (TypeError, ValueError):
            return Response(
                {"detail": 'Informe "idnatureza" numérico no corpo da requisição.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if idnat <= 0:
            return Response(
                {"detail": '"idnatureza" deve ser maior que zero.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            natureza = Nat_Lancamento.objects.get(pk=idnat)
        except Nat_Lancamento.DoesNotExist:
            return Response(
                {"detail": f"Natureza de lançamento {idnat} não encontrada."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if natureza.empresa_id and obj.empresa_id and natureza.empresa_id != obj.empresa_id:
            return Response(
                {"detail": "A natureza informada pertence a outra empresa."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 5) Recalcula totais para garantir consistência
        obj.recomputa_totais()
        obj.save(update_fields=["total_itens", "total_desconto", "frete", "total_pedido"])
        total = Decimal(obj.total_pedido or 0).quantize(Decimal("0.01"))
        if total <= 0:
            return Response({"detail": "Total do pedido deve ser maior que zero."}, status=status.HTTP_400_BAD_REQUEST)

        # 6) Parcelas PLAN obrigatórias
        parcelas_plan = list(
            PedidoCompraParcela.objects.filter(pedido=obj, status="PLAN").order_by("parcela_n")
        )
        if not parcelas_plan:
            return Response(
                {"detail": "Defina a forma de pagamento / parcelas antes de aprovar."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        soma = sum(Decimal(p.valor).quantize(Decimal("0.01")) for p in parcelas_plan)

        # tolerância de 1 centavo para arredondamento
        if (soma - total).copy_abs() > Decimal("0.01"):
            return Response(
                {"detail": f"Soma das parcelas ({soma}) difere do total do pedido ({total})."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 7) Cria Pagar (PREVISTO)
        pagar = Pagar.objects.create(
            empresa=obj.empresa,
            idloja=obj.loja,
            idfornecedor=obj.fornecedor,
            Titulo=f"PC {obj.id}",
            Documento=None,
            Data_emissao=obj.emissao,
            Valor_total=total,
            Previsao=True,
            FormaPagamento=obj.forma_pagamento,
            Idnatureza=natureza,
            conta_contabil=None,
            pedido_compra=obj.id,
            nfe_id=None,
        )

        # 8) Cria itens PagarItem a partir das parcelas
        for p in parcelas_plan:
            pi = PagarItem.objects.create(
                Idpagar=pagar,
                parcela_n=p.parcela_n,
                status=getattr(PagarItem, "STATUS_PREVISTO", "PREVISTO"),
                Data_vencimento=p.vencimento,
                valor_parcela=p.valor,
                FormaPagamento=obj.forma_pagamento,
                idconta=None,
                juros=0,
                desconto=0,
                data_baixa=None,
                valor_baixa=None,
                Previsao=True,
                Idnatureza=natureza,
            )
            p.status = "GERADA"
            # vínculo pelo campo inteiro pagar_item_id (já existente no model/tabela)
            p.pagar_item_id = getattr(pi, "Idpagaritem", pi.pk)
            p.save(update_fields=["status", "pagar_item_id"])

        # 9) Atualiza status do pedido para AP
        before = obj.status
        obj.status = "AP"
        obj.save(update_fields=["status"])

        _audit(
            "pedidocompra",
            obj.pk,
            {"status": [before, "AP"], "pagar_id": getattr(pagar, "Idpagar", pagar.pk)},
            request,
            action="aprovar",
        )

        return Response(self.get_serializer(obj).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="alterar-natureza")
    @transaction.atomic
    def alterar_natureza(self, request, pk=None):
        """
        Altera a natureza do contas a pagar gerado pelo pedido.
        Parcelas já baixadas não são alteradas para preservar o histórico financeiro.
        """
        obj: PedidoCompra = self.get_object()
        if not FIN_OK:
            return Response(
                {"detail": "Financeiro indisponível."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if (obj.status or "").upper() not in ("AP", "AT"):
            return Response(
                {"detail": "A natureza só pode ser alterada em pedido aprovado ou atendido."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_idnat = request.data.get("idnatureza")
        try:
            idnat = int(raw_idnat)
        except (TypeError, ValueError):
            return Response(
                {"detail": 'Informe "idnatureza" numérico no corpo da requisição.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            natureza = Nat_Lancamento.objects.get(pk=idnat)
        except Nat_Lancamento.DoesNotExist:
            return Response(
                {"detail": f"Natureza de lançamento {idnat} não encontrada."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if natureza.empresa_id and obj.empresa_id and natureza.empresa_id != obj.empresa_id:
            return Response(
                {"detail": "A natureza informada pertence a outra empresa."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pagar = (
            Pagar.objects
            .select_for_update()
            .filter(empresa=obj.empresa, pedido_compra=obj.id)
            .order_by("-Idpagar")
            .first()
        )
        if not pagar:
            return Response(
                {"detail": "Não existe contas a pagar gerado para este pedido."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        anterior = pagar.Idnatureza_id
        pagar.Idnatureza = natureza
        pagar.save(update_fields=["Idnatureza"])

        itens_editaveis = pagar.itens.exclude(status=getattr(PagarItem, "STATUS_BAIXADO", "BAIXADO"))
        alteradas = itens_editaveis.update(Idnatureza=natureza)
        baixadas = pagar.itens.filter(status=getattr(PagarItem, "STATUS_BAIXADO", "BAIXADO")).count()

        _audit(
            "pedidocompra",
            obj.pk,
            {
                "natureza": [anterior, natureza.pk],
                "pagar_id": getattr(pagar, "Idpagar", pagar.pk),
                "parcelas_alteradas": alteradas,
                "parcelas_baixadas_preservadas": baixadas,
            },
            request,
            action="alt_natureza",
        )

        return Response(self.get_serializer(obj).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='cancelar')
    @transaction.atomic
    def cancelar(self, request, pk=None):
        """
        Cancela um pedido de compra EM ABERTO (status = 'AB').

        - Não mexe em financeiro porque só permitimos cancelar pedidos em AB.
        """
        pedido: PedidoCompra = self.get_object()

        # Só permite cancelar se estiver em aberto
        if (pedido.status or '').upper() != 'AB':
            return Response(
                {'detail': 'Só é possível cancelar pedidos em aberto (AB).'},
                status=status.HTTP_400_BAD_REQUEST
            )

        antes = pedido.status
        pedido.status = 'CA'  # código de cancelado
        pedido.save(update_fields=['status'])

        _audit(
            'pedidocompra',
            pedido.pk,
            {'status': [antes, pedido.status]},
            request,
            action='cancelar',
        )

        ser = PedidoCompraSerializer(pedido)
        return Response(ser.data, status=status.HTTP_200_OK)


# ----------------- Itens -----------------
class PedidoCompraItemViewSet(BaseViewSet):
    queryset = PedidoCompraItem.objects.all().order_by("pedido_id", "id")
    serializer_class = PedidoCompraItemSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        pedido = self.request.query_params.get("pedido")
        if empresa_id:
            qs = qs.filter(pedido__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        if pedido:
            qs = qs.filter(pedido_id=pedido)
        return qs

    def perform_create(self, serializer):
        self._validar_item_empresa(serializer.validated_data)
        item = serializer.save()
        _sincronizar_parcelas_planejadas(item.pedido, self.request, motivo="alteracao_item")

    def perform_update(self, serializer):
        data = {**serializer.validated_data}
        data.setdefault("pedido", serializer.instance.pedido)
        data.setdefault("produto", serializer.validated_data.get("produto", serializer.instance.produto))
        data.setdefault("pack", serializer.validated_data.get("pack", serializer.instance.pack))
        self._validar_item_empresa(data)
        item = serializer.save()
        _sincronizar_parcelas_planejadas(item.pedido, self.request, motivo="alteracao_item")

    @transaction.atomic
    def perform_destroy(self, instance):
        pedido = instance.pedido
        if pedido.status != "AB":
            raise ValidationError({"pedido": "Somente pedidos em aberto (AB) permitem exclusão de itens."})
        if pedido.cotacao_origem_id:
            raise ValidationError({"pedido": "Pedido originado de cotação aprovada não permite alteração comercial."})
        instance.delete()
        pedido.recomputa_totais()
        update_fields = ["total_itens", "total_desconto", "frete", "outras_despesas", "total_pedido"]
        if not pedido.itens.exists() and pedido.tipo:
            pedido.tipo = ""
            update_fields.append("tipo")
        pedido.save(update_fields=update_fields)
        _sincronizar_parcelas_planejadas(pedido, self.request, motivo="exclusao_item")

    def _validar_item_empresa(self, data):
        pedido = data.get("pedido")
        produto = data.get("produto")
        pack = data.get("pack")
        empresa_id = pedido.empresa_id if pedido else None
        user_empresa_id = self._empresa_id_usuario()
        if not user_empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        if user_empresa_id and empresa_id and int(user_empresa_id) != empresa_id:
            raise ValidationError({"pedido": "Pedido pertence a outra empresa."})
        if produto and produto.empresa_id and empresa_id and produto.empresa_id != empresa_id:
            raise ValidationError({"produto": "Produto pertence a outra empresa."})
        if pack and pack.empresa_id and empresa_id and pack.empresa_id != empresa_id:
            raise ValidationError({"pack": "Pack pertence a outra empresa."})
        if produto and pack and produto.grade_id and pack.grade_id != produto.grade_id:
            raise ValidationError({"pack": "Pack incompatível com a grade do produto."})
        if produto and produto.tipo_produto == "3":
            raise ValidationError({"produto": "Produto de fabricação própria não participa de Compras."})
        if pedido and pedido.status != "AB":
            raise ValidationError({"pedido": "Somente pedidos em aberto (AB) permitem alteração de itens."})
        if pedido and pedido.cotacao_origem_id:
            raise ValidationError({"pedido": "Pedido originado de cotação aprovada não permite alteração comercial."})


# ----------------- Entregas -----------------
class PedidoCompraEntregaViewSet(BaseViewSet):
    queryset = PedidoCompraEntrega.objects.all().order_by("item_id", "id")
    serializer_class = PedidoCompraEntregaSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        pedido = self.request.query_params.get("pedido")
        if empresa_id:
            qs = qs.filter(item__pedido__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        if pedido:
            qs = qs.filter(item__pedido_id=pedido)
        return qs


# ----------------- Parcelas (planejamento) -----------------
class PedidoCompraParcelaViewSet(BaseViewSet):
    queryset = PedidoCompraParcela.objects.all().order_by("pedido_id", "parcela_n")
    serializer_class = PedidoCompraParcelaSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        pedido = self.request.query_params.get("pedido")
        if empresa_id:
            qs = qs.filter(pedido__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        if pedido:
            qs = qs.filter(pedido_id=pedido)
        status_q = self.request.query_params.get("status")
        if status_q:
            qs = qs.filter(status=status_q)
        return qs


class RequisicaoServicoCategoriaViewSet(BaseViewSet):
    queryset = RequisicaoServicoCategoria.objects.all().order_by("nome")
    serializer_class = RequisicaoServicoCategoriaSerializer
    permission_classes = [HasRequisicaoProcessAccess]

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            _ensure_default_requisicao_servico_categorias(empresa_id)
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        ativo = self.request.query_params.get("ativo")
        if ativo is not None:
            qs = qs.filter(ativo=str(ativo).lower() in {"1", "true", "sim"})
        return qs

    def perform_create(self, serializer):
        empresa_id = self._empresa_id_usuario()
        if not empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        empresa = self.request.user.empresa if empresa_id else serializer.validated_data.get("empresa")
        obj = serializer.save(empresa=empresa)
        _audit("requisicaoservicocategoria", obj.pk, {"created": True}, self.request, action="create")


class RequisicaoSetorViewSet(BaseViewSet):
    queryset = RequisicaoSetor.objects.all().order_by("nome")
    serializer_class = RequisicaoSetorSerializer
    permission_classes = [HasRequisicaoProcessAccess]

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            _ensure_default_requisicao_setores(empresa_id)
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        search = self.request.query_params.get("search")
        empresa = self.request.query_params.get("empresa")
        if self.request.user.is_superuser and empresa:
            qs = qs.filter(empresa_id=empresa)
        if search:
            qs = qs.filter(Q(nome__icontains=search) | Q(descricao__icontains=search))
        ativo = self.request.query_params.get("ativo")
        if ativo is not None:
            qs = qs.filter(ativo=str(ativo).lower() in {"1", "true", "sim"})
        loja = self.request.query_params.get("loja")
        if loja:
            qs = qs.filter(loja_id=loja)
        pode_fazer = self.request.query_params.get("pode_fazer_requisicao")
        if pode_fazer is not None:
            qs = qs.filter(pode_fazer_requisicao=str(pode_fazer).lower() in {"1", "true", "sim"})
        for field in ("recebe_requisicoes", "central_uso_consumo", "central_manutencao", "central_ti", "responsavel_compras"):
            value = self.request.query_params.get(field)
            if value is not None:
                qs = qs.filter(**{field: str(value).lower() in {"1", "true", "sim"}})
        return qs

    def perform_create(self, serializer):
        empresa_id = self._empresa_id_usuario()
        if not empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        empresa_payload = self.request.data.get("empresa")
        if empresa_id and empresa_payload and int(empresa_payload) != int(empresa_id):
            raise ValidationError({"empresa": "Usuário não pode criar setor para outra empresa."})
        empresa = self.request.user.empresa if empresa_id else serializer.validated_data.get("empresa")
        obj = serializer.save(empresa=empresa)
        _audit("requisicaosetor", obj.pk, {"created": True}, self.request, action="create")

    def perform_update(self, serializer):
        empresa_id = self._empresa_id_usuario()
        obj = serializer.instance
        if empresa_id and obj.empresa_id != int(empresa_id):
            raise ValidationError({"empresa": "Setor pertence a outra empresa."})
        updated = serializer.save(empresa=obj.empresa)
        _audit("requisicaosetor", updated.pk, {"updated": True}, self.request, action="update")

    def destroy(self, request, *args, **kwargs):
        return Response({"detail": "Exclusão física de setor não é permitida. Utilize inativação."}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    @action(detail=True, methods=["post"], url_path="ativar")
    def ativar(self, request, pk=None):
        obj = self.get_object()
        before = obj.ativo
        obj.ativo = True
        obj.save(update_fields=["ativo"])
        _audit("requisicaosetor", obj.pk, {"ativo": [before, True]}, request, action="ativar")
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"], url_path="inativar")
    def inativar(self, request, pk=None):
        obj = self.get_object()
        before = obj.ativo
        obj.ativo = False
        obj.save(update_fields=["ativo"])
        _audit("requisicaosetor", obj.pk, {"ativo": [before, False]}, request, action="inativar")
        return Response(self.get_serializer(obj).data)


class RequisicaoMatrizResponsabilidadeViewSet(BaseViewSet):
    queryset = RequisicaoMatrizResponsabilidade.objects.select_related("empresa", "setor_atendimento", "setor_aquisicao").all().order_by("tipo_requisicao")
    serializer_class = RequisicaoMatrizResponsabilidadeSerializer
    permission_classes = [HasRequisicaoProcessAccess]

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        empresa = self.request.query_params.get("empresa")
        if self.request.user.is_superuser and empresa:
            qs = qs.filter(empresa_id=empresa)
        tipo = self.request.query_params.get("tipo_requisicao")
        if tipo:
            qs = qs.filter(tipo_requisicao=tipo)
        ativo = self.request.query_params.get("ativo")
        if ativo is not None:
            qs = qs.filter(ativo=str(ativo).lower() in {"1", "true", "sim"})
        return qs

    def perform_create(self, serializer):
        empresa_id = self._empresa_id_usuario()
        if not empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        empresa_payload = self.request.data.get("empresa")
        if empresa_id and empresa_payload and int(empresa_payload) != int(empresa_id):
            raise ValidationError({"empresa": "Usuário não pode criar matriz para outra empresa."})
        empresa = self.request.user.empresa if empresa_id else serializer.validated_data.get("empresa")
        obj = serializer.save(empresa=empresa)
        _audit("requisicaomatrizresponsabilidade", obj.pk, {"created": True}, self.request, action="create")

    def perform_update(self, serializer):
        empresa_id = self._empresa_id_usuario()
        obj = serializer.instance
        if empresa_id and obj.empresa_id != int(empresa_id):
            raise ValidationError({"empresa": "Matriz pertence a outra empresa."})
        updated = serializer.save(empresa=obj.empresa)
        _audit("requisicaomatrizresponsabilidade", updated.pk, {"updated": True}, self.request, action="update")

    def destroy(self, request, *args, **kwargs):
        return Response({"detail": "Exclusão física de matriz não é permitida. Utilize inativação."}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    @action(detail=False, methods=["get"], url_path="resolver")
    def resolver(self, request):
        empresa_id = self._empresa_id_usuario()
        if not empresa_id and not request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        empresa = request.user.empresa if empresa_id else None
        if request.user.is_superuser and request.query_params.get("empresa"):
            from cadastros.models import Empresa
            empresa = get_object_or_404(Empresa, pk=request.query_params.get("empresa"))
        tipo = request.query_params.get("tipo_requisicao")
        resp = resolver_responsabilidade_requisicao(empresa, tipo)
        return Response({
            "tipo_requisicao": tipo,
            "setor_atendimento": resp.setor_atendimento.id,
            "setor_atendimento_nome": resp.setor_atendimento.nome,
            "setor_aquisicao": resp.setor_aquisicao.id,
            "setor_aquisicao_nome": resp.setor_aquisicao.nome,
        })

    @action(detail=True, methods=["post"], url_path="ativar")
    def ativar(self, request, pk=None):
        obj = self.get_object()
        before = obj.ativo
        obj.ativo = True
        obj.save(update_fields=["ativo", "atualizado_em"])
        _audit("requisicaomatrizresponsabilidade", obj.pk, {"ativo": [before, True]}, request, action="ativar")
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"], url_path="inativar")
    def inativar(self, request, pk=None):
        obj = self.get_object()
        before = obj.ativo
        obj.ativo = False
        obj.save(update_fields=["ativo", "atualizado_em"])
        _audit("requisicaomatrizresponsabilidade", obj.pk, {"ativo": [before, False]}, request, action="inativar")
        return Response(self.get_serializer(obj).data)


class RequisicaoMaterialCategoriaViewSet(BaseViewSet):
    queryset = RequisicaoMaterialCategoria.objects.all().order_by("nome")
    serializer_class = RequisicaoMaterialCategoriaSerializer
    permission_classes = [HasRequisicaoProcessAccess]

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            _ensure_default_requisicao_material_categorias(empresa_id)
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        empresa = self.request.query_params.get("empresa")
        if self.request.user.is_superuser and empresa:
            qs = qs.filter(empresa_id=empresa)
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(Q(nome__icontains=search) | Q(descricao__icontains=search))
        ativo = self.request.query_params.get("ativo")
        if ativo is not None:
            qs = qs.filter(ativo=str(ativo).lower() in {"1", "true", "sim"})
        return qs

    def perform_create(self, serializer):
        empresa_id = self._empresa_id_usuario()
        if not empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        empresa_payload = self.request.data.get("empresa")
        if empresa_id and empresa_payload and int(empresa_payload) != int(empresa_id):
            raise ValidationError({"empresa": "Usuário não pode criar categoria para outra empresa."})
        empresa = self.request.user.empresa if empresa_id else serializer.validated_data.get("empresa")
        obj = serializer.save(empresa=empresa)
        _audit("requisicaomaterialcategoria", obj.pk, {"created": True}, self.request, action="create")

    def perform_update(self, serializer):
        empresa_id = self._empresa_id_usuario()
        obj = serializer.instance
        if empresa_id and obj.empresa_id != int(empresa_id):
            raise ValidationError({"empresa": "Categoria pertence a outra empresa."})
        updated = serializer.save(empresa=obj.empresa)
        _audit("requisicaomaterialcategoria", updated.pk, {"updated": True}, self.request, action="update")

    def destroy(self, request, *args, **kwargs):
        return Response({"detail": "Exclusão física de categoria de material não é permitida. Utilize inativação."}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    @action(detail=True, methods=["post"], url_path="ativar")
    def ativar(self, request, pk=None):
        obj = self.get_object()
        before = obj.ativo
        obj.ativo = True
        obj.save(update_fields=["ativo"])
        _audit("requisicaomaterialcategoria", obj.pk, {"ativo": [before, True]}, request, action="ativar")
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"], url_path="inativar")
    def inativar(self, request, pk=None):
        obj = self.get_object()
        before = obj.ativo
        obj.ativo = False
        obj.save(update_fields=["ativo"])
        _audit("requisicaomaterialcategoria", obj.pk, {"ativo": [before, False]}, request, action="inativar")
        return Response(self.get_serializer(obj).data)


class RequisicaoFinalidadeAquisicaoViewSet(BaseViewSet):
    queryset = RequisicaoFinalidadeAquisicao.objects.all().order_by("nome")
    serializer_class = RequisicaoFinalidadeAquisicaoSerializer
    permission_classes = [HasRequisicaoProcessAccess]

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            _ensure_default_requisicao_finalidades(empresa_id)
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        empresa = self.request.query_params.get("empresa")
        if self.request.user.is_superuser and empresa:
            qs = qs.filter(empresa_id=empresa)
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(Q(nome__icontains=search) | Q(descricao__icontains=search) | Q(comportamento__icontains=search))
        ativo = self.request.query_params.get("ativo")
        if ativo is not None:
            qs = qs.filter(ativo=str(ativo).lower() in {"1", "true", "sim"})
        return qs

    def perform_create(self, serializer):
        empresa_id = self._empresa_id_usuario()
        if not empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        empresa_payload = self.request.data.get("empresa")
        if empresa_id and empresa_payload and int(empresa_payload) != int(empresa_id):
            raise ValidationError({"empresa": "Usuário não pode criar finalidade para outra empresa."})
        empresa = self.request.user.empresa if empresa_id else serializer.validated_data.get("empresa")
        obj = serializer.save(empresa=empresa)
        _audit("requisicaofinalidadeaquisicao", obj.pk, {"created": True}, self.request, action="create")

    def perform_update(self, serializer):
        empresa_id = self._empresa_id_usuario()
        obj = serializer.instance
        if empresa_id and obj.empresa_id != int(empresa_id):
            raise ValidationError({"empresa": "Finalidade pertence a outra empresa."})
        updated = serializer.save(empresa=obj.empresa)
        _audit("requisicaofinalidadeaquisicao", updated.pk, {"updated": True}, self.request, action="update")

    def destroy(self, request, *args, **kwargs):
        return Response({"detail": "Exclusão física de finalidade não é permitida. Utilize inativação."}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    @action(detail=True, methods=["post"], url_path="ativar")
    def ativar(self, request, pk=None):
        obj = self.get_object()
        before = obj.ativo
        obj.ativo = True
        obj.save(update_fields=["ativo"])
        _audit("requisicaofinalidadeaquisicao", obj.pk, {"ativo": [before, True]}, request, action="ativar")
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"], url_path="inativar")
    def inativar(self, request, pk=None):
        obj = self.get_object()
        before = obj.ativo
        obj.ativo = False
        obj.save(update_fields=["ativo"])
        _audit("requisicaofinalidadeaquisicao", obj.pk, {"ativo": [before, False]}, request, action="inativar")
        return Response(self.get_serializer(obj).data)


class RequisicaoViewSet(BaseViewSet):
    queryset = Requisicao.objects.select_related("empresa", "loja", "setor", "setor_responsavel", "requisitante", "criado_por").prefetch_related("itens", "historico").all()
    serializer_class = RequisicaoSerializer
    permission_classes = [HasRequisicaoProcessAccess]

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        qs = _scope_requisicao_queryset(qs, self.request.user)
        status_q = self.request.query_params.get("status")
        loja = self.request.query_params.get("loja")
        prioridade = self.request.query_params.get("prioridade")
        search = self.request.query_params.get("search")
        if status_q:
            qs = qs.filter(status=status_q)
        if loja:
            qs = qs.filter(loja_id=loja)
        if prioridade:
            qs = qs.filter(prioridade=prioridade)
        tipo_requisicao = self.request.query_params.get("tipo_requisicao")
        if tipo_requisicao:
            qs = qs.filter(tipo_requisicao=tipo_requisicao)
        if search:
            f = Q(setor__nome__icontains=search) | Q(justificativa__icontains=search) | Q(requisitante__username__icontains=search)
            if str(search).isdigit():
                f |= Q(numero=int(search))
            qs = qs.filter(f)
        visao = self.request.query_params.get("visao") or self.request.query_params.get("view")
        if visao == "minhas":
            qs = qs.filter(requisitante=self.request.user)
        elif visao == "para_analisar":
            if not _can_approve_requisicao(self.request.user):
                return qs.none()
            qs = qs.filter(status__in=["AGUARDANDO_APROVACAO", "SOLICITADA", "EM_ANALISE"])
        elif visao == "para_atender":
            if not _can_manage_requisicao(self.request.user):
                return qs.none()
            qs = qs.filter(status__in=["APROVADA", "EM_ATENDIMENTO", "ATENDIDA_PARCIALMENTE"])
        elif visao == "todas":
            if not _can_view_all_requisicao(self.request.user):
                return qs.none()
        elif not self.request.user.is_superuser:
            allowed = Q()
            if _can_request_requisicao(self.request.user):
                allowed |= Q(requisitante=self.request.user)
            if _can_approve_requisicao(self.request.user):
                allowed |= Q(status__in=["AGUARDANDO_APROVACAO", "SOLICITADA", "EM_ANALISE"])
            if _can_manage_requisicao(self.request.user):
                allowed |= Q(status__in=["APROVADA", "EM_ATENDIMENTO", "ATENDIDA_PARCIALMENTE"])
            if not allowed:
                return qs.none()
            qs = qs.filter(allowed)
        return qs

    @transaction.atomic
    def retrieve(self, request, *args, **kwargs):
        obj = Requisicao.objects.select_for_update().get(pk=self.get_object().pk)
        ordem = getattr(obj, "ordem_servico", None)
        if ordem:
            sincronizar_requisicao_com_ordem_servico(ordem, usuario=request.user)
            obj.refresh_from_db()
        sincronizar_requisicao_disponivel_para_atendimento(obj)
        obj.refresh_from_db()
        serializer = self.get_serializer(obj)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], url_path="lojas-permitidas")
    def lojas_permitidas(self, request):
        empresa_id = self._empresa_id_usuario()
        qs = Loja.objects.all().order_by("nome_loja")
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not request.user.is_superuser:
            return Response([])
        access = EffectiveAccessService(request.user)
        acesso_total_empresa = bool(request.user.is_superuser or access.is_company_master() or getattr(request.user, "type", "") == "Admin")
        allowed = access.allowed_store_ids()
        if allowed is not None and not acesso_total_empresa:
            qs = qs.filter(id__in=allowed)
        return Response([
            {
                "id": loja.id,
                "Idloja": loja.id,
                "empresa": loja.empresa_id,
                "nome_loja": loja.nome_loja,
                "apelido_loja": loja.apelido_loja,
            }
            for loja in qs
        ])

    @transaction.atomic
    def perform_create(self, serializer):
        loja = serializer.validated_data.get("loja")
        if not _can_request_requisicao(self.request.user):
            raise ValidationError({"detail": "Usuário sem permissão para criar requisições."})
        empresa_id = self._empresa_id_usuario()
        if not empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        if empresa_id and loja.empresa_id != int(empresa_id):
            raise ValidationError({"loja": "A loja informada pertence a outra empresa."})
        if not EffectiveAccessService(self.request.user).can_access_store(loja):
            raise ValidationError({"loja": "Usuário sem acesso à loja informada."})
        empresa = loja.empresa
        _ensure_default_requisicao_servico_categorias(empresa.pk)
        _ensure_default_requisicao_setores(empresa.pk)
        _ensure_default_requisicao_material_categorias(empresa.pk)
        _ensure_default_requisicao_finalidades(empresa.pk)
        tipo = serializer.validated_data.get("tipo_requisicao", "USO_CONSUMO")
        responsabilidade = resolver_responsabilidade_requisicao(empresa, tipo)
        proximo = (Requisicao.objects.select_for_update().filter(empresa=empresa).aggregate(max_num=Max("numero"))["max_num"] or 0) + 1
        obj = serializer.save(empresa=empresa, numero=proximo, requisitante=self.request.user, criado_por=self.request.user, setor_responsavel=responsabilidade.setor_atendimento)
        _historico(obj, self.request, "CRIACAO", "", obj.status, observacao="Requisição criada.")

    @transaction.atomic
    def perform_update(self, serializer):
        obj = serializer.instance
        if not _can_edit_requisicao_content(self.request.user, obj):
            raise ValidationError({"status": "Somente o requisitante original pode editar requisições não enviadas ou devolvidas para correção."})
        loja = serializer.validated_data.get("loja", obj.loja)
        if loja.empresa_id != obj.empresa_id:
            raise ValidationError({"loja": "A loja informada pertence a outra empresa."})
        if not EffectiveAccessService(self.request.user).can_access_store(loja):
            raise ValidationError({"loja": "Usuário sem acesso à loja informada."})
        tipo = serializer.validated_data.get("tipo_requisicao", obj.tipo_requisicao)
        responsabilidade = resolver_responsabilidade_requisicao(obj.empresa, tipo)
        before = {"setor": obj.setor_id, "loja": obj.loja_id, "prioridade": obj.prioridade, "tipo_requisicao": obj.tipo_requisicao, "setor_responsavel": obj.setor_responsavel_id}
        updated = serializer.save(empresa=obj.empresa, setor_responsavel=responsabilidade.setor_atendimento)
        after = {"setor": updated.setor_id, "loja": updated.loja_id, "prioridade": updated.prioridade, "tipo_requisicao": updated.tipo_requisicao, "setor_responsavel": updated.setor_responsavel_id}
        _historico(updated, self.request, "EDICAO", updated.status, updated.status, valor_anterior=before, valor_novo=after, observacao="Requisição editada.")

    def destroy(self, request, *args, **kwargs):
        return Response({"detail": "Exclusão física de requisição não é permitida. Utilize cancelamento."}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    @action(detail=True, methods=["post"], url_path="enviar")
    @transaction.atomic
    def enviar(self, request, pk=None):
        obj = self.get_object()
        if not _can_edit_requisicao_content(request.user, obj):
            return Response({"detail": "Somente o requisitante original pode enviar requisições não enviadas ou devolvidas para correção."}, status=status.HTTP_403_FORBIDDEN)
        if obj.setor.loja_id != obj.loja_id:
            return Response({"setor": ["Setor deve estar vinculado à mesma loja da requisição."]}, status=status.HTTP_400_BAD_REQUEST)
        if not obj.itens.exists():
            return Response({"detail": "Inclua ao menos um item antes de enviar."}, status=status.HTTP_400_BAD_REQUEST)
        before = obj.status
        obj.status = "AGUARDANDO_APROVACAO"
        obj.save(update_fields=["status", "atualizado_em"])
        _historico(obj, request, "ENVIO", before, obj.status, observacao=request.data.get("observacao", ""))
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"], url_path="salvar-enviar")
    @transaction.atomic
    def salvar_enviar(self, request, pk=None):
        obj = Requisicao.objects.select_for_update().get(pk=self.get_object().pk)
        if not _can_edit_requisicao_content(request.user, obj):
            return Response({"detail": "Somente o requisitante original pode enviar requisições não enviadas ou devolvidas para correção."}, status=status.HTTP_403_FORBIDDEN)
        dados = request.data.get("requisicao")
        if isinstance(dados, dict):
            serializer = self.get_serializer(obj, data=dados, partial=True)
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
            obj = serializer.instance
        if obj.setor.loja_id != obj.loja_id:
            return Response({"setor": ["Setor deve estar vinculado à mesma loja da requisição."]}, status=status.HTTP_400_BAD_REQUEST)
        if not obj.itens.exists():
            return Response({"itens": ["Inclua ao menos um item antes de enviar."]}, status=status.HTTP_400_BAD_REQUEST)
        before = obj.status
        obj.status = "AGUARDANDO_APROVACAO"
        obj.save(update_fields=["status", "atualizado_em"])
        _historico(obj, request, "ENVIO", before, obj.status, observacao=request.data.get("observacao", ""))
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"], url_path="aprovar")
    @transaction.atomic
    def aprovar(self, request, pk=None):
        if not _can_approve_requisicao(request.user):
            return Response({"detail": "Usuário sem permissão para aprovar requisição."}, status=status.HTTP_403_FORBIDDEN)
        obj = self.get_object()
        if obj.status not in {"AGUARDANDO_APROVACAO", "SOLICITADA", "EM_ANALISE"}:
            return Response({"detail": "Requisição não está aguardando aprovação."}, status=status.HTTP_400_BAD_REQUEST)
        before = obj.status
        obj.status = "APROVADA"
        obj.aprovado_por = request.user
        obj.aprovado_em = timezone.now()
        obj.save(update_fields=["status", "aprovado_por", "aprovado_em", "atualizado_em"])
        obj.itens.filter(status="PENDENTE").update(status="APROVADO")
        _historico(obj, request, "APROVACAO", before, obj.status, observacao=request.data.get("observacao", ""))
        ordem = garantir_ordem_servico_requisicao(obj)
        if ordem:
            sincronizar_requisicao_com_ordem_servico(ordem, usuario=request.user, registrar_inicio=True)
            obj.refresh_from_db()
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"], url_path="rejeitar")
    @transaction.atomic
    def rejeitar(self, request, pk=None):
        if not _can_approve_requisicao(request.user):
            return Response({"detail": "Usuário sem permissão para rejeitar requisição."}, status=status.HTTP_403_FORBIDDEN)
        obj = self.get_object()
        if obj.status not in {"AGUARDANDO_APROVACAO", "SOLICITADA", "EM_ANALISE"}:
            return Response({"detail": "Requisição não pode ser rejeitada neste status."}, status=status.HTTP_400_BAD_REQUEST)
        before = obj.status
        obj.status = "REJEITADA"
        obj.save(update_fields=["status", "atualizado_em"])
        obj.itens.exclude(status__in=["ATENDIDO", "CANCELADO"]).update(status="REJEITADO")
        _historico(obj, request, "REJEICAO", before, obj.status, observacao=request.data.get("motivo", ""))
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"], url_path="devolver")
    @transaction.atomic
    def devolver(self, request, pk=None):
        if not _can_approve_requisicao(request.user):
            return Response({"detail": "Usuário sem permissão para devolver requisição."}, status=status.HTTP_403_FORBIDDEN)
        obj = self.get_object()
        if obj.status not in {"AGUARDANDO_APROVACAO", "SOLICITADA", "EM_ANALISE"}:
            return Response({"detail": "Requisição não pode ser devolvida neste status."}, status=status.HTTP_400_BAD_REQUEST)
        before = obj.status
        obj.status = "DEVOLVIDA_CORRECAO"
        obj.save(update_fields=["status", "atualizado_em"])
        _historico(obj, request, "DEVOLUCAO", before, obj.status, observacao=request.data.get("motivo", ""))
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"], url_path="cancelar")
    @transaction.atomic
    def cancelar(self, request, pk=None):
        obj = self.get_object()
        if not _can_edit_requisicao_content(request.user, obj):
            return Response({"detail": "Somente o requisitante original pode cancelar requisições editáveis."}, status=status.HTTP_403_FORBIDDEN)
        if obj.status in {"CONCLUIDA", "CANCELADA"}:
            return Response({"detail": "Requisição não pode ser cancelada neste status."}, status=status.HTTP_400_BAD_REQUEST)
        before = obj.status
        obj.status = "CANCELADA"
        obj.save(update_fields=["status", "atualizado_em"])
        obj.itens.exclude(status__in=["ATENDIDO", "SERVICO_CONCLUIDO"]).update(status="CANCELADO")
        _historico(obj, request, "CANCELAMENTO", before, obj.status, observacao=request.data.get("motivo", ""))
        return Response(self.get_serializer(obj).data)


class RequisicaoItemViewSet(BaseViewSet):
    queryset = RequisicaoItem.objects.select_related("requisicao", "produto", "unidade", "categoria_servico", "categoria_material", "finalidade_aquisicao").all()
    serializer_class = RequisicaoItemSerializer
    permission_classes = [HasRequisicaoProcessAccess]

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(requisicao__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        if not _is_requisicao_admin(self.request.user):
            allowed = EffectiveAccessService(self.request.user).allowed_store_ids()
            if allowed is not None:
                qs = qs.filter(requisicao__loja_id__in=allowed)
            visible = Q()
            if _can_request_requisicao(self.request.user):
                visible |= Q(requisicao__requisitante=self.request.user)
            if _can_approve_requisicao(self.request.user):
                visible |= Q(requisicao__status__in=["AGUARDANDO_APROVACAO", "SOLICITADA", "EM_ANALISE"])
            if _can_manage_requisicao(self.request.user):
                visible |= Q(requisicao__status__in=["APROVADA", "EM_ATENDIMENTO", "ATENDIDA_PARCIALMENTE"])
            if not visible:
                return qs.none()
            qs = qs.filter(visible)
        requisicao = self.request.query_params.get("requisicao")
        if requisicao:
            qs = qs.filter(requisicao_id=requisicao)
        return qs

    def _validar_empresa(self, data):
        req = data.get("requisicao")
        user_empresa_id = self._empresa_id_usuario()
        if not user_empresa_id and not self.request.user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        if user_empresa_id and req and req.empresa_id != int(user_empresa_id):
            raise ValidationError({"requisicao": "Requisição pertence a outra empresa."})
        if req and not EffectiveAccessService(self.request.user).can_access_store(req.loja):
            raise ValidationError({"requisicao": "Usuário sem acesso à loja da requisição."})

    def perform_create(self, serializer):
        self._validar_empresa(serializer.validated_data)
        req = serializer.validated_data.get("requisicao")
        if req and not _can_edit_requisicao_content(self.request.user, req):
            raise ValidationError({"requisicao": "Somente o requisitante original pode alterar itens de requisições não enviadas ou devolvidas para correção."})
        item = serializer.save()
        _historico(item.requisicao, self.request, "EDICAO", item.requisicao.status, item.requisicao.status, item=item, valor_novo={"item": item.pk}, observacao="Item incluído.")

    def perform_update(self, serializer):
        data = {**serializer.validated_data}
        data.setdefault("requisicao", serializer.instance.requisicao)
        self._validar_empresa(data)
        req = data.get("requisicao")
        if req and not _can_edit_requisicao_content(self.request.user, req):
            raise ValidationError({"requisicao": "Somente o requisitante original pode alterar itens de requisições não enviadas ou devolvidas para correção."})
        item = serializer.save()
        _historico(item.requisicao, self.request, "EDICAO", item.requisicao.status, item.requisicao.status, item=item, observacao="Item editado.")

    def perform_destroy(self, instance):
        if not _can_edit_requisicao_content(self.request.user, instance.requisicao):
            raise ValidationError({"requisicao": "Somente o requisitante original pode excluir itens de requisições não enviadas ou devolvidas para correção."})
        req = instance.requisicao
        item_id = instance.pk
        instance.delete()
        _historico(req, self.request, "EDICAO", req.status, req.status, valor_anterior={"item": item_id}, observacao="Item removido.")

    @action(detail=True, methods=["post"], url_path="aguardar-cotacao")
    @transaction.atomic
    def aguardar_cotacao(self, request, pk=None):
        if not _can_manage_requisicao(request.user):
            return Response({"detail": "Usuário sem permissão para encaminhar requisição."}, status=status.HTTP_403_FORBIDDEN)
        item = get_object_or_404(self.get_queryset().select_for_update(), pk=pk)
        self.check_object_permissions(request, item)
        req = get_object_or_404(Requisicao.objects.select_for_update(), pk=item.requisicao_id)
        if req.status not in {"APROVADA", "EM_ATENDIMENTO", "ATENDIDA_PARCIALMENTE", "EM_PROCESSO_COMPRA"}:
            return Response({"detail": "A requisição precisa estar aprovada para encaminhar item."}, status=status.HTTP_400_BAD_REQUEST)
        if item.status in {"ATENDIDO", "CANCELADO", "REJEITADO", "SERVICO_CONCLUIDO"}:
            return Response({"detail": "Item não pode ser encaminhado neste status."}, status=status.HTTP_400_BAD_REQUEST)
        before_item = item.status
        item.status = "AGUARDANDO_COTACAO"
        item.save(update_fields=["status", "atualizado_em"])
        before_req = req.status
        req.status = _recalcular_status_requisicao(req)
        req.save(update_fields=["status", "atualizado_em"])
        _historico(req, request, "STATUS", before_req, req.status, item=item, valor_anterior={"item_status": before_item}, valor_novo={"item_status": item.status}, observacao="Item marcado aguardando cotação.")
        return Response(self.get_serializer(item).data)

    @action(detail=True, methods=["post"], url_path="atender")
    @transaction.atomic
    def atender(self, request, pk=None):
        if not _can_manage_requisicao(request.user):
            return Response({"detail": "Usuário sem permissão para atender requisição."}, status=status.HTTP_403_FORBIDDEN)
        item = get_object_or_404(self.get_queryset().select_for_update(), pk=pk)
        self.check_object_permissions(request, item)
        req = get_object_or_404(Requisicao.objects.select_for_update(), pk=item.requisicao_id)
        if req.status not in {"APROVADA", "EM_ATENDIMENTO", "ATENDIDA_PARCIALMENTE", "EM_PROCESSO_COMPRA"}:
            return Response({"detail": "A requisição precisa estar aprovada para atendimento."}, status=status.HTTP_400_BAD_REQUEST)
        indicador = indicador_requisicao_item(item)
        bloqueados = {"ATENDIDO", "CANCELADO", "REJEITADO", "SERVICO_CONCLUIDO"}
        if item.status in {"AGUARDANDO_COTACAO", "EM_COTACAO", "PEDIDO_GERADO", "AGUARDANDO_RECEBIMENTO"} and indicador.get("codigo") != "DISPONIVEL":
            bloqueados.add(item.status)
        if item.status in bloqueados:
            return Response({"detail": "Item não pode ser atendido neste status."}, status=status.HTTP_400_BAD_REQUEST)
        if item.tipo != "MATERIAL" or item.origem != "PRODUTO" or not item.produto_id:
            return Response({"detail": "Somente material cadastrado pode ser atendido pelo estoque nesta etapa."}, status=status.HTTP_400_BAD_REQUEST)
        qtd = Decimal(request.data.get("quantidade") or 0)
        if qtd <= 0:
            return Response({"quantidade": "Informe uma quantidade maior que zero."}, status=status.HTTP_400_BAD_REQUEST)
        saldo = Decimal(item.qtd_pendente or 0)
        if qtd > saldo:
            return Response({"quantidade": "Quantidade não pode ultrapassar o saldo pendente."}, status=status.HTTP_400_BAD_REQUEST)
        loja_estoque = loja_estoque_requisicao_item(item)
        estoque = ProdutoUsoConsumoEstoque.objects.select_for_update().filter(empresa=req.empresa, produto=item.produto, loja=loja_estoque).first()
        disponivel = Decimal(getattr(estoque, "saldo", 0) or 0)
        if qtd > disponivel:
            return Response({"quantidade": "Estoque insuficiente para a quantidade informada.", "disponivel": str(disponivel)}, status=status.HTTP_400_BAD_REQUEST)
        before_item = item.status
        before_req = req.status
        anterior = disponivel
        posterior = disponivel - qtd
        estoque.saldo = posterior
        estoque.save(update_fields=["saldo", "atualizado_em"])
        ProdutoUsoConsumoMovimentacao.objects.create(
            empresa=req.empresa,
            produto=item.produto,
            loja=loja_estoque,
            tipo=ProdutoUsoConsumoMovimentacao.TIPO_CONSUMO_INTERNO,
            quantidade=qtd,
            saldo_anterior=anterior,
            saldo_posterior=posterior,
            usuario=request.user,
            motivo="Atendimento de requisição",
            destino=f"{req.setor.nome} / {req.loja.nome_loja}",
            documento=f"REQ {req.numero}",
            origem=f"REQUISICAO:{req.id};ALMOXARIFADO:{getattr(req.setor_responsavel, 'nome', '')}",
        )
        item.qtd_atendida = Decimal(item.qtd_atendida or 0) + qtd
        item.qtd_pendente = max(Decimal(item.qtd_solicitada or 0) - item.qtd_atendida, Decimal("0"))
        item.status = "ATENDIDO" if item.qtd_pendente == 0 else "ATENDIDO_PARCIALMENTE"
        item.save(update_fields=["qtd_atendida", "qtd_pendente", "status", "atualizado_em"])
        req.status = _recalcular_status_requisicao(req)
        req.save(update_fields=["status", "atualizado_em"])
        _historico(req, request, "ATENDIMENTO", before_req, req.status, item=item, valor_anterior={"item_status": before_item, "saldo_estoque": str(anterior)}, valor_novo={"item_status": item.status, "qtd_atendida": str(item.qtd_atendida), "saldo_estoque": str(posterior)}, observacao=request.data.get("observacao", ""))
        return Response(self.get_serializer(item).data)


class OrdemServicoViewSet(BaseViewSet):
    queryset = OrdemServico.objects.select_related("empresa", "loja", "setor_solicitante", "setor_responsavel", "requisicao", "responsavel").all()
    serializer_class = OrdemServicoSerializer
    permission_classes = [HasRequisicaoProcessAccess]

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        access = EffectiveAccessService(self.request.user)
        allowed = access.allowed_store_ids()
        if allowed is not None and not (self.request.user.is_superuser or access.is_company_master()):
            qs = qs.filter(loja_id__in=allowed)
        if not _can_manage_requisicao(self.request.user) and not _is_requisicao_admin(self.request.user):
            if _can_request_requisicao(self.request.user):
                qs = qs.filter(requisicao__requisitante=self.request.user)
            else:
                return qs.none()
        status_q = self.request.query_params.get("status")
        tipo = self.request.query_params.get("tipo") or self.request.query_params.get("tipo_requisicao")
        loja = self.request.query_params.get("loja")
        responsavel = self.request.query_params.get("responsavel")
        if status_q:
            qs = qs.filter(status=status_q)
        if tipo:
            qs = qs.filter(tipo=tipo)
        if loja:
            qs = qs.filter(loja_id=loja)
        if responsavel:
            qs = qs.filter(responsavel_id=responsavel)
        return qs

    @transaction.atomic
    def retrieve(self, request, *args, **kwargs):
        obj = OrdemServico.objects.select_for_update().get(pk=self.get_object().pk)
        for material in obj.materiais.select_for_update().select_related("ordem_servico", "produto").all():
            atualizar_status_material_os(material)
        atualizar_status_material_ordem_servico(obj)
        sincronizar_requisicao_com_ordem_servico(obj, usuario=request.user)
        obj.refresh_from_db()
        return Response(self.get_serializer(obj).data)

    def perform_create(self, serializer):
        raise ValidationError({"detail": "Ordem de Serviço é gerada a partir da Requisição."})

    def perform_update(self, serializer):
        obj = serializer.instance
        before = {"status": obj.status, "responsavel": obj.responsavel_id}
        if serializer.validated_data.get("status") == "CONCLUIDA":
            pendente = obj.materiais.filter(qtd_pendente__gt=0).exclude(status="CANCELADA").exists()
            status_pendente = obj.materiais.exclude(status__in=["ATENDIDA", "CANCELADA"]).exists()
            if pendente or status_pendente:
                raise ValidationError({"detail": "Não é possível concluir a OS enquanto houver material pendente."})
        updated = serializer.save()
        sincronizar_requisicao_com_ordem_servico(updated, usuario=self.request.user)
        after = {"status": updated.status, "responsavel": updated.responsavel_id}
        _audit("ordemservico", updated.pk, {"updated": True, "before": before, "after": after}, self.request, action="update")

    def destroy(self, request, *args, **kwargs):
        return Response({"detail": "Exclusão física de Ordem de Serviço não é permitida."}, status=status.HTTP_405_METHOD_NOT_ALLOWED)


class OrdemServicoMaterialViewSet(BaseViewSet):
    queryset = OrdemServicoMaterial.objects.select_related(
        "ordem_servico",
        "ordem_servico__empresa",
        "ordem_servico__loja",
        "ordem_servico__setor_solicitante",
        "ordem_servico__setor_responsavel",
        "produto",
        "unidade",
    ).all()
    serializer_class = OrdemServicoMaterialSerializer
    permission_classes = [HasRequisicaoProcessAccess]

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(ordem_servico__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        access = EffectiveAccessService(self.request.user)
        allowed = access.allowed_store_ids()
        if allowed is not None and not (self.request.user.is_superuser or access.is_company_master()):
            qs = qs.filter(ordem_servico__loja_id__in=allowed)
        if not _can_manage_requisicao(self.request.user) and not _is_requisicao_admin(self.request.user):
            if _can_request_requisicao(self.request.user):
                qs = qs.filter(ordem_servico__requisicao__requisitante=self.request.user)
            else:
                return qs.none()
        ordem_servico = self.request.query_params.get("ordem_servico")
        if ordem_servico:
            qs = qs.filter(ordem_servico_id=ordem_servico)
        return qs

    @transaction.atomic
    def retrieve(self, request, *args, **kwargs):
        material = OrdemServicoMaterial.objects.select_for_update().select_related("ordem_servico", "produto").get(pk=self.get_object().pk)
        atualizar_status_material_os(material)
        atualizar_status_material_ordem_servico(material.ordem_servico)
        material.refresh_from_db()
        return Response(self.get_serializer(material).data)

    def perform_create(self, serializer):
        if not _can_manage_requisicao(self.request.user) and not _is_requisicao_admin(self.request.user):
            raise PermissionDenied("Usuário sem permissão para registrar material da OS.")
        material = serializer.save()
        atualizar_status_material_os(material)
        atualizar_status_material_ordem_servico(material.ordem_servico)
        _audit("ordemservicomaterial", material.pk, {"created": True, "ordem_servico": material.ordem_servico_id}, self.request, action="create")

    def perform_update(self, serializer):
        if not _can_manage_requisicao(self.request.user) and not _is_requisicao_admin(self.request.user):
            raise PermissionDenied("Usuário sem permissão para alterar material da OS.")
        before = {"status": serializer.instance.status, "qtd_necessaria": str(serializer.instance.qtd_necessaria)}
        material = serializer.save()
        atualizar_status_material_os(material)
        atualizar_status_material_ordem_servico(material.ordem_servico)
        after = {"status": material.status, "qtd_necessaria": str(material.qtd_necessaria)}
        _audit("ordemservicomaterial", material.pk, {"updated": True, "before": before, "after": after}, self.request, action="update")

    def destroy(self, request, *args, **kwargs):
        material = self.get_object()
        if Decimal(material.qtd_atendida or 0) > 0:
            return Response({"detail": "Material já atendido não pode ser removido."}, status=status.HTTP_400_BAD_REQUEST)
        _audit("ordemservicomaterial", material.pk, {"deleted": True, "ordem_servico": material.ordem_servico_id}, request, action="delete")
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def atender(self, request, pk=None):
        if not _can_manage_requisicao(request.user) and not _is_requisicao_admin(request.user):
            raise PermissionDenied("Usuário sem permissão para atender material da OS.")
        with transaction.atomic():
            material = OrdemServicoMaterial.objects.select_for_update().select_related("ordem_servico", "produto").get(pk=self.get_object().pk)
            if material.status == "CANCELADA":
                return Response({"detail": "Material cancelado não pode ser atendido."}, status=status.HTTP_400_BAD_REQUEST)
            if not material.produto_id:
                return Response({"produto": "Informe um produto para atender pelo Almoxarifado."}, status=status.HTTP_400_BAD_REQUEST)
            qtd = Decimal(str(request.data.get("quantidade") or "0"))
            if qtd <= 0:
                return Response({"quantidade": "Informe uma quantidade positiva."}, status=status.HTTP_400_BAD_REQUEST)
            pendente = Decimal(material.qtd_pendente or 0)
            if qtd > pendente:
                return Response({"quantidade": "Quantidade não pode ultrapassar o pendente."}, status=status.HTTP_400_BAD_REQUEST)

            loja_estoque = loja_almoxarifado_central(material.ordem_servico.empresa)
            estoque = ProdutoUsoConsumoEstoque.objects.select_for_update().filter(
                empresa=material.ordem_servico.empresa,
                produto=material.produto,
                loja=loja_estoque,
            ).first()
            disponivel = Decimal(getattr(estoque, "saldo", 0) or 0)
            if qtd > disponivel:
                return Response({"quantidade": "Estoque insuficiente no Almoxarifado para a quantidade informada.", "disponivel": str(disponivel)}, status=status.HTTP_400_BAD_REQUEST)

            anterior = disponivel
            posterior = disponivel - qtd
            estoque.saldo = posterior
            estoque.save(update_fields=["saldo", "atualizado_em"])
            os_obj = material.ordem_servico
            ProdutoUsoConsumoMovimentacao.objects.create(
                empresa=os_obj.empresa,
                produto=material.produto,
                loja=loja_estoque,
                tipo=ProdutoUsoConsumoMovimentacao.TIPO_CONSUMO_INTERNO,
                quantidade=qtd,
                saldo_anterior=anterior,
                saldo_posterior=posterior,
                usuario=request.user,
                motivo="Atendimento de material da OS",
                destino=f"{os_obj.setor_solicitante.nome} / {os_obj.loja.nome_loja}",
                documento=f"OS {os_obj.id}",
                origem=f"ORDEM_SERVICO:{os_obj.id};MATERIAL:{material.id}",
            )
            material.qtd_atendida = Decimal(material.qtd_atendida or 0) + qtd
            material.save(update_fields=["qtd_atendida", "qtd_pendente", "status", "atualizado_em"])
            atualizar_status_material_os(material)
            atualizar_status_material_ordem_servico(os_obj)
            _audit("ordemservicomaterial", material.pk, {"attended": True, "qtd": str(qtd), "saldo_anterior": str(anterior), "saldo_posterior": str(posterior)}, request, action="atender")
        return Response(self.get_serializer(material).data)


class RequisicaoHistoricoViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = RequisicaoHistorico.objects.select_related("requisicao", "item", "usuario").all()
    serializer_class = RequisicaoHistoricoSerializer
    permission_classes = [HasRequisicaoProcessAccess]
    read_roles = ['Admin', 'Diretor', 'Gerente', 'AssistentePagar']

    def _empresa_id_usuario(self):
        usuario = self.request.user
        if getattr(usuario, "empresa_id", None):
            return usuario.empresa_id
        return None

    def get_queryset(self):
        qs = self.queryset
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(requisicao__empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        if not _is_requisicao_admin(self.request.user):
            allowed = EffectiveAccessService(self.request.user).allowed_store_ids()
            if allowed is not None:
                qs = qs.filter(requisicao__loja_id__in=allowed)
            visible = Q()
            if _can_request_requisicao(self.request.user):
                visible |= Q(requisicao__requisitante=self.request.user)
            if _can_approve_requisicao(self.request.user):
                visible |= Q(requisicao__status__in=["AGUARDANDO_APROVACAO", "SOLICITADA", "EM_ANALISE"])
            if _can_manage_requisicao(self.request.user):
                visible |= Q(requisicao__status__in=["APROVADA", "EM_ATENDIMENTO", "ATENDIDA_PARCIALMENTE"])
            if not visible:
                return qs.none()
            qs = qs.filter(visible)
        requisicao = self.request.query_params.get("requisicao")
        if requisicao:
            qs = qs.filter(requisicao_id=requisicao)
        return qs
