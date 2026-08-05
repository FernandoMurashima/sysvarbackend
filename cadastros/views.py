from django.contrib.auth import get_user_model
from rest_framework import viewsets, permissions, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, ProtectedError, Q
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from accounts.permissions import HasModuleRole
from accounts.services.effective_access import EffectiveAccessService, MasterTransferService, audit_event, increment_permissions_version, sync_legacy_license_flags
from accounts.services.sessions import ConcurrentSessionService
from accounts.serializers import EmpresaContratoDetalheSerializer
from auditoria.models import AuditAction, AuditCategory
from auditoria.services import AuditService, instance_snapshot

from .models import Empresa, EmpresaContrato, Loja, Cliente, Fornecedor, Funcionarios, Nat_Lancamento, PlanoContabil
from .serializers import (
    EmpresaSerializer,
    LojaSerializer,
    ClienteSerializer,
    FornecedorSerializer,
    FuncionariosSerializer,
    NatLancamentoSerializer,
    PlanoContabilSerializer,
)

User = get_user_model()


class BaseCadastroViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModuleRole]
    required_module = "cadastros"
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa", "Vendedor", "AssistenteReceber", "AssistentePagar", "Auxiliar", "Assistente", "Regular"]
    write_roles = ["Admin", "Diretor", "Gerente"]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    # Em cada ViewSet definimos filterset_fields, search_fields, ordering_fields e ordering

    def get_queryset(self):
        qs = super().get_queryset()
        model = getattr(qs, "model", None)
        user = self.request.user
        if not model or user.is_superuser:
            empresa = self.request.query_params.get("empresa")
            if empresa and self._model_has_field(model, "empresa"):
                return qs.filter(empresa_id=empresa)
            return qs
        empresa_id = getattr(user, "empresa_id", None)
        if empresa_id and self._model_has_field(model, "empresa"):
            return qs.filter(empresa_id=empresa_id)
        if self._model_has_field(model, "empresa"):
            return qs.none()
        return qs

    def perform_create(self, serializer):
        self._save_with_empresa_scope(serializer)

    def perform_update(self, serializer):
        self._save_with_empresa_scope(serializer)

    def _save_with_empresa_scope(self, serializer):
        model = serializer.Meta.model
        user = self.request.user
        if self._model_has_field(model, "empresa") and user.is_superuser:
            if not serializer.validated_data.get("empresa"):
                raise ValidationError({"empresa": "Informe a empresa do cadastro."})
            serializer.save()
            return
        if self._model_has_field(model, "empresa") and not getattr(user, "empresa_id", None) and not user.is_superuser:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        if self._model_has_field(model, "empresa") and getattr(user, "empresa_id", None):
            empresa = serializer.validated_data.get("empresa")
            if empresa and empresa.id != user.empresa_id:
                raise ValidationError({"empresa": "Você só pode cadastrar lojas e registros na empresa vinculada ao seu usuário."})
            serializer.save(empresa=user.empresa)
            return
        serializer.save()

    def _model_has_field(self, model, field_name):
        if model is None:
            return False
        try:
            model._meta.get_field(field_name)
            return True
        except Exception:
            return False


class EmpresaViewSet(BaseCadastroViewSet):
    required_module = "operacional"
    queryset = Empresa.objects.all()
    serializer_class = EmpresaSerializer
    read_roles = ["Admin", "Diretor", "Gerente"]
    write_roles = ["Admin", "Diretor"]
    filterset_fields = ["ativo", "documento"]
    search_fields = ["nome", "nome_fantasia", "documento"]
    ordering_fields = ["nome", "nome_fantasia", "data_cadastro"]
    ordering = ["nome"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_superuser:
            return qs
        empresa_id = getattr(user, "empresa_id", None)
        if empresa_id:
            return qs.filter(pk=empresa_id)
        return qs.none()

    def _exigir_superusuario(self):
        if not self.request.user.is_superuser:
            raise PermissionDenied("Somente superusuário pode cadastrar ou alterar empresas.")

    def perform_create(self, serializer):
        self._exigir_superusuario()
        serializer.save()

    def perform_update(self, serializer):
        self._exigir_superusuario()
        serializer.save()

    def perform_destroy(self, instance):
        self._exigir_superusuario()
        instance.delete()

    @action(detail=True, methods=["post"], url_path="transferir-master")
    def transferir_master(self, request, pk=None):
        empresa = self.get_object()
        user_id = request.data.get("usuario_master") or request.data.get("novo_master_id") or request.data.get("user_id")
        if not user_id:
            raise ValidationError({"usuario_master": "Informe o novo usuário master."})
        try:
            new_master = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            raise ValidationError({"usuario_master": "Usuário não encontrado."})
        contrato = MasterTransferService(request.user, empresa, new_master, request).transfer()
        return Response({"empresa": empresa.pk, "usuario_master": contrato.usuario_master_id})

    @action(detail=True, methods=["get", "put", "patch"], url_path="contrato")
    def contrato(self, request, pk=None):
        empresa = self.get_object()
        try:
            contrato = empresa.contrato
        except Exception:
            if not request.user.is_superuser:
                raise PermissionDenied("Contrato não disponível.")
            from cadastros.models import EmpresaContrato

            contrato = EmpresaContrato.objects.create(empresa=empresa)
            audit_event("contract_create", request, request.user, "contrato", contrato.pk, {"empresa": empresa.pk})

        is_master = contrato.usuario_master_id == getattr(request.user, "id", None)
        if request.method == "GET":
            if not (request.user.is_superuser or is_master):
                raise PermissionDenied("Somente superusuário ou master da empresa pode consultar este contrato.")
            return Response(EmpresaContratoDetalheSerializer(contrato, context={"request": request}).data)

        if not request.user.is_superuser:
            raise PermissionDenied("Somente superusuário pode alterar contrato.")

        old = {
            "status": contrato.status,
            "data_inicio": contrato.data_inicio.isoformat() if contrato.data_inicio else None,
            "data_fim": contrato.data_fim.isoformat() if contrato.data_fim else None,
            "limite_sessoes_simultaneas": contrato.limite_sessoes_simultaneas,
            "plano_completo": contrato.plano_completo,
            "usuario_master_id": contrato.usuario_master_id,
        }
        serializer = EmpresaContratoDetalheSerializer(
            contrato,
            data=request.data,
            partial=request.method == "PATCH",
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        contrato = serializer.save()
        contrato.incrementar_versao()
        sync_legacy_license_flags(empresa)
        new = {
            "status": contrato.status,
            "data_inicio": contrato.data_inicio.isoformat() if contrato.data_inicio else None,
            "data_fim": contrato.data_fim.isoformat() if contrato.data_fim else None,
            "limite_sessoes_simultaneas": contrato.limite_sessoes_simultaneas,
            "plano_completo": contrato.plano_completo,
            "usuario_master_id": contrato.usuario_master_id,
        }
        action_name = "contract_update"
        if old["limite_sessoes_simultaneas"] != new["limite_sessoes_simultaneas"]:
            action_name = "contract_limit_update"
        if old["limite_sessoes_simultaneas"] > new["limite_sessoes_simultaneas"] and contrato.limite_excedido:
            action_name = "contract_limit_reduced_with_excess"
        audit_event(action_name, request, request.user, "contrato", contrato.pk, {"old": old, "new": new})
        return Response(EmpresaContratoDetalheSerializer(contrato, context={"request": request}).data)

    def _confirmacao_valida(self, empresa, valor):
        esperado = {str(empresa.pk), empresa.nome, empresa.nome_fantasia, empresa.documento}
        esperado = {str(v).strip().lower() for v in esperado if v}
        return str(valor or "").strip().lower() in esperado

    @action(detail=True, methods=["post"], url_path="suspender")
    def suspender(self, request, pk=None):
        empresa = self.get_object()
        if not request.user.is_superuser:
            AuditService.denied(AuditAction.CONTRACT_SUSPENSION_DENIED, category=AuditCategory.CONTRACT, request=request, user=request.user, empresa=empresa, app_label="cadastros", model="empresa", object_id=empresa.pk, status_code=403)
            raise PermissionDenied("Somente superusuário pode suspender empresa.")
        motivo = request.data.get("motivo")
        observacao = (request.data.get("observacao") or "").strip()
        if motivo not in dict(EmpresaContrato.MOTIVO_SUSPENSAO_CHOICES):
            raise ValidationError({"motivo": "Motivo de suspensão inválido."})
        if not self._confirmacao_valida(empresa, request.data.get("confirmacao")):
            AuditService.denied(AuditAction.CONTRACT_SUSPENSION_DENIED, category=AuditCategory.CONTRACT, request=request, user=request.user, empresa=empresa, app_label="cadastros", model="empresa", object_id=empresa.pk, metadata={"motivo": motivo}, status_code=400)
            raise ValidationError({"confirmacao": "Confirmação inválida."})
        with transaction.atomic():
            contrato = EmpresaContrato.objects.select_for_update().select_related("empresa").get(empresa=empresa)
            if contrato.status == EmpresaContrato.STATUS_CANCELADO:
                raise ValidationError({"status": "Contrato cancelado não pode ser suspenso."})
            if contrato.status == EmpresaContrato.STATUS_SUSPENSO:
                raise ValidationError({"status": "Contrato já está suspenso."})
            before = {"status": contrato.status}
            sessoes = list(ConcurrentSessionService.active_sessions_queryset(empresa).select_for_update())
            for sessao in sessoes:
                ConcurrentSessionService.close_session(sessao, "CONTRACT_SUSPENDED", request.user, request)
            contrato.status = EmpresaContrato.STATUS_SUSPENSO
            contrato.motivo_suspensao = motivo
            contrato.observacao_suspensao = observacao
            contrato.suspenso_em = timezone.now()
            contrato.suspenso_por = request.user
            contrato.incrementar_versao(save=False)
            contrato.__skip_audit_signal__ = True
            contrato.save(update_fields=["status", "motivo_suspensao", "observacao_suspensao", "suspenso_em", "suspenso_por", "permissions_version", "updated_at"])
            AuditService.required_success(
                AuditAction.CONTRACT_SUSPENDED,
                category=AuditCategory.CONTRACT,
                request=request,
                user=request.user,
                instance=contrato,
                before=before,
                after={"status": contrato.status, "motivo": motivo},
                metadata={"motivo": motivo, "observacao": observacao, "sessoes_encerradas": len(sessoes)},
                status_code=200,
            )
        return Response(EmpresaContratoDetalheSerializer(contrato, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="reativar")
    def reativar(self, request, pk=None):
        empresa = self.get_object()
        if not request.user.is_superuser:
            AuditService.denied(AuditAction.CONTRACT_REACTIVATION_DENIED, category=AuditCategory.CONTRACT, request=request, user=request.user, empresa=empresa, app_label="cadastros", model="empresa", object_id=empresa.pk, status_code=403)
            raise PermissionDenied("Somente superusuário pode reativar empresa.")
        with transaction.atomic():
            contrato = EmpresaContrato.objects.select_for_update().select_related("empresa").get(empresa=empresa)
            if contrato.status != EmpresaContrato.STATUS_SUSPENSO:
                raise ValidationError({"status": "Somente contrato suspenso pode ser reativado."})
            before = {"status": contrato.status, "motivo": contrato.motivo_suspensao}
            contrato.status = EmpresaContrato.STATUS_ATIVO
            contrato.reativado_em = timezone.now()
            contrato.reativado_por = request.user
            contrato.incrementar_versao(save=False)
            contrato.__skip_audit_signal__ = True
            contrato.save(update_fields=["status", "reativado_em", "reativado_por", "permissions_version", "updated_at"])
            AuditService.required_success(
                AuditAction.CONTRACT_REACTIVATED,
                category=AuditCategory.CONTRACT,
                request=request,
                user=request.user,
                instance=contrato,
                before=before,
                after={"status": contrato.status},
                metadata={"motivo_anterior": before["motivo"]},
                status_code=200,
            )
        return Response(EmpresaContratoDetalheSerializer(contrato, context={"request": request}).data)


class LojaViewSet(BaseCadastroViewSet):
    required_module = "operacional"
    queryset = Loja.objects.select_related("empresa").all()
    serializer_class = LojaSerializer

    filterset_fields = ["ativo", "empresa", "estado", "cidade", "cnpj"]
    search_fields = ["nome_loja", "apelido_loja", "cnpj", "cidade", "email", "telefone1", "telefone2"]
    ordering_fields = ["nome_loja", "cidade", "estado", "data_cadastro"]
    ordering = ["nome_loja"]
    filterset_fields = ["ativo", "empresa", "estado", "cidade", "cnpj", "tipo_unidade", "emite_nfce", "emite_nfe"]
    action_required_access = {"usuarios": "VIEW", "indicadores": "VIEW", "ativar": "EDIT", "inativar": "EDIT", "encerrar": "EDIT", "reabrir": "EDIT"}

    def perform_create(self, serializer):
        user = self.request.user
        if user.is_superuser:
            loja = serializer.save()
        else:
            if not getattr(user, "empresa_id", None):
                raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
            empresa = serializer.validated_data.get("empresa")
            if empresa and empresa.id != user.empresa_id:
                raise ValidationError({"empresa": "Você só pode cadastrar estabelecimento na sua empresa."})
            loja = serializer.save(empresa=user.empresa)
        AuditService.required_success(AuditAction.STORE_CREATED, category=AuditCategory.CADASTRO, request=self.request, user=self.request.user, instance=loja, after=instance_snapshot(loja), status_code=201)

    def perform_update(self, serializer):
        before = instance_snapshot(serializer.instance)
        user = self.request.user
        if not user.is_superuser and serializer.instance.empresa_id != getattr(user, "empresa_id", None):
            raise PermissionDenied("Estabelecimento pertence a outra empresa.")
        loja = serializer.save()
        fiscal_fields = {"regime_tributario", "ambiente_fiscal", "inscricao_estadual", "emite_nfce", "emite_nfe"}
        number_fields = {"serie_nfce", "proximo_numero_nfce", "serie_nfe", "proximo_numero_nfe"}
        policy_fields = {"EstoqueNegativo"}
        changed = set((before or {}).keys()) & set(serializer.validated_data.keys())
        action_name = AuditAction.STORE_UPDATED
        if changed & number_fields:
            action_name = AuditAction.STORE_NUMBERING_UPDATED
        elif changed & fiscal_fields:
            action_name = AuditAction.STORE_FISCAL_CONFIG_UPDATED
        elif changed & policy_fields:
            action_name = AuditAction.STORE_NEGATIVE_STOCK_POLICY_UPDATED
        AuditService.required_success(action_name, category=AuditCategory.CADASTRO, request=self.request, user=self.request.user, instance=loja, before=before, after=instance_snapshot(loja), changed_fields=sorted(serializer.validated_data.keys()), status_code=200)

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_superuser:
            empresa = self.request.query_params.get("empresa")
            return qs.filter(empresa_id=empresa) if empresa else qs
        if not EffectiveAccessService(user).is_company_master():
            lojas_ids = list(user.lojas.values_list("id", flat=True))
            if getattr(user, "loja_id", None) and user.loja_id not in lojas_ids:
                lojas_ids.append(user.loja_id)
            return qs.filter(pk__in=lojas_ids) if lojas_ids else qs.none()
        empresa_id = getattr(user, "empresa_id", None)
        if empresa_id:
            return qs.filter(empresa_id=empresa_id)
        loja_id = getattr(user, "loja_id", None)
        if loja_id:
            return qs.filter(pk=loja_id)
        return qs.none()

    def _impedimentos_inativacao(self, loja):
        impedimentos = []
        if ConcurrentSessionService.valid_sessions_queryset().filter(loja=loja).exists():
            impedimentos.append("Existem sessões ativas no estabelecimento.")
        if loja.usuarios.filter(is_active=True).exists():
            impedimentos.append("Existem usuários com esta loja principal.")
        if loja.usuarios_permitidos.filter(is_active=True).exists():
            impedimentos.append("Existem usuários com esta loja entre as lojas permitidas.")
        return impedimentos

    @action(detail=False, methods=["get"], url_path="indicadores")
    def indicadores(self, request):
        qs = self.filter_queryset(self.get_queryset())
        return Response(qs.aggregate(
            total=Count("id"),
            ativos=Count("id", filter=Q(ativo=True)),
            inativos=Count("id", filter=Q(ativo=False)),
            lojas=Count("id", filter=Q(tipo_unidade=Loja.TIPO_LOJA)),
            matrizes=Count("id", filter=Q(tipo_unidade=Loja.TIPO_MATRIZ)),
            fabricas=Count("id", filter=Q(tipo_unidade=Loja.TIPO_FABRICA)),
        ))

    @action(detail=True, methods=["post"], url_path="ativar")
    def ativar(self, request, pk=None):
        loja = self.get_object()
        before = instance_snapshot(loja)
        loja.ativo = True
        loja.DataEnceramento = None
        loja.__skip_audit_signal__ = True
        loja.save(update_fields=["ativo", "DataEnceramento", "Matriz"])
        AuditService.required_success(AuditAction.STORE_ACTIVATED, category=AuditCategory.ACCESS, request=request, user=request.user, instance=loja, before=before, after=instance_snapshot(loja), status_code=200)
        return Response(self.get_serializer(loja).data)

    @action(detail=True, methods=["post"], url_path="inativar")
    def inativar(self, request, pk=None):
        loja = self.get_object()
        impedimentos = self._impedimentos_inativacao(loja)
        if impedimentos:
            AuditService.denied(AuditAction.STORE_OPERATION_DENIED, category=AuditCategory.ACCESS, request=request, user=request.user, instance=loja, metadata={"operacao": "inativar", "impedimentos": impedimentos}, status_code=400)
            return Response({"code": "STORE_DEACTIVATION_BLOCKED", "impedimentos": impedimentos}, status=400)
        before = instance_snapshot(loja)
        loja.ativo = False
        loja.__skip_audit_signal__ = True
        loja.save(update_fields=["ativo", "Matriz"])
        AuditService.required_success(AuditAction.STORE_DEACTIVATED, category=AuditCategory.ACCESS, request=request, user=request.user, instance=loja, before=before, after=instance_snapshot(loja), status_code=200)
        return Response(self.get_serializer(loja).data)

    @action(detail=True, methods=["post"], url_path="encerrar")
    def encerrar(self, request, pk=None):
        loja = self.get_object()
        data = request.data.get("data") or request.data.get("data_encerramento")
        motivo = (request.data.get("motivo") or "").strip()
        if not data:
            raise ValidationError({"data": "Informe a data de encerramento."})
        if not motivo:
            raise ValidationError({"motivo": "Informe o motivo do encerramento."})
        before = instance_snapshot(loja)
        loja.DataEnceramento = data
        loja.ativo = False
        loja.__skip_audit_signal__ = True
        loja.save(update_fields=["DataEnceramento", "ativo", "Matriz"])
        AuditService.required_success(AuditAction.STORE_CLOSED, category=AuditCategory.ACCESS, request=request, user=request.user, instance=loja, before=before, after=instance_snapshot(loja), metadata={"motivo": motivo}, status_code=200)
        return Response(self.get_serializer(loja).data)

    @action(detail=True, methods=["post"], url_path="reabrir")
    def reabrir(self, request, pk=None):
        loja = self.get_object()
        before = instance_snapshot(loja)
        loja.DataEnceramento = None
        loja.ativo = True
        loja.__skip_audit_signal__ = True
        loja.save(update_fields=["DataEnceramento", "ativo", "Matriz"])
        AuditService.required_success(AuditAction.STORE_REOPENED, category=AuditCategory.ACCESS, request=request, user=request.user, instance=loja, before=before, after=instance_snapshot(loja), status_code=200)
        return Response(self.get_serializer(loja).data)

    @action(detail=True, methods=["get"], url_path="usuarios")
    def usuarios(self, request, pk=None):
        loja = self.get_object()
        qs = User.objects.filter(Q(loja=loja) | Q(lojas=loja)).select_related("perfil_principal").distinct()
        if not request.user.is_superuser:
            qs = qs.filter(empresa_id=request.user.empresa_id)
        data = []
        for user in qs:
            data.append({
                "id": user.id,
                "username": user.username,
                "nome": user.get_full_name() or user.username,
                "perfil": getattr(user.perfil_principal, "nome", None),
                "loja_principal": user.loja_id == loja.id,
                "loja_permitida": user.lojas.filter(pk=loja.pk).exists(),
                "ativo": user.is_active,
                "sessao_ativa": ConcurrentSessionService.valid_sessions_queryset().filter(loja=loja, usuario=user).exists(),
            })
        return Response(data)


class ClienteViewSet(BaseCadastroViewSet):
    queryset = Cliente.objects.all()
    serializer_class = ClienteSerializer

    filterset_fields = ["ativo", "empresa", "estado", "cidade", "categoria", "bloqueio", "mala_direta"]
    search_fields = ["nome_cliente", "apelido", "cpf", "email", "cidade", "telefone1", "telefone2"]
    ordering_fields = ["nome_cliente", "cidade", "estado", "data_cadastro"]
    ordering = ["nome_cliente"]


class FornecedorViewSet(BaseCadastroViewSet):
    queryset = Fornecedor.objects.all()
    serializer_class = FornecedorSerializer

    filterset_fields = ["ativo", "empresa", "estado", "cidade", "categoria", "bloqueio", "mala_direta", "cnpj"]
    search_fields = ["nome_fornecedor", "apelido", "cnpj", "email", "cidade", "telefone1", "telefone2"]
    ordering_fields = ["nome_fornecedor", "cidade", "estado", "data_cadastro"]
    ordering = ["nome_fornecedor"]


class FuncionariosViewSet(BaseCadastroViewSet):
    # select_related para otimizar a FK de loja
    queryset = Funcionarios.objects.select_related("idloja").all()
    serializer_class = FuncionariosSerializer

    filterset_fields = ["ativo", "empresa", "categoria", "idloja"]
    search_fields = ["nomefuncionario", "apelido", "cpf"]
    ordering_fields = ["nomefuncionario", "categoria", "data_cadastro", "meta"]
    ordering = ["nomefuncionario"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if not user.is_superuser and getattr(user, "type", None) in {"Caixa", "Vendedor"}:
            lojas_ids = list(user.lojas.values_list("id", flat=True))
            if getattr(user, "loja_id", None) and user.loja_id not in lojas_ids:
                lojas_ids.append(user.loja_id)
            return qs.filter(idloja_id__in=lojas_ids) if lojas_ids else qs.none()
        return qs


class PlanoContabilViewSet(BaseCadastroViewSet):
    required_module = "fiscal"
    queryset = PlanoContabil.objects.select_related("empresa", "conta_pai").all()
    serializer_class = PlanoContabilSerializer
    read_roles = ["Admin", "Diretor", "Gerente", "AssistenteReceber", "AssistentePagar"]
    write_roles = ["Admin", "Diretor"]
    filterset_fields = ["empresa", "classe", "natureza", "analitica", "ativa", "conta_pai"]
    search_fields = ["codigo", "descricao", "classe", "natureza", "conta_pai__codigo", "conta_pai__descricao"]
    ordering_fields = ["codigo", "descricao", "classe", "natureza", "nivel", "analitica", "ativa"]
    ordering = ["codigo"]


class NatLancamentoViewSet(viewsets.ModelViewSet):
    queryset = Nat_Lancamento.objects.all().order_by("codigo")
    serializer_class = NatLancamentoSerializer
    permission_classes = [HasModuleRole]
    required_module = "financeiro"
    read_roles = ["Admin", "Diretor", "Gerente", "AssistenteReceber", "AssistentePagar"]
    write_roles = ["Admin", "Diretor"]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        "codigo", "categoria_principal", "subcategoria",
        "descricao", "tipo", "status", "tipo_natureza",
        "natureza_operacao", "categoria_gerencial", "conta_contabil",
    ]
    ordering_fields = [
        "codigo", "categoria_principal", "subcategoria",
        "tipo", "status", "tipo_natureza", "natureza_operacao",
        "categoria_gerencial", "ativo", "idnatureza",
    ]
    ordering = ["codigo"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_superuser:
            empresa = self.request.query_params.get("empresa")
            return qs.filter(empresa_id=empresa) if empresa else qs
        empresa_id = getattr(user, "empresa_id", None)
        return qs.filter(empresa_id=empresa_id) if empresa_id else qs.none()

    def perform_create(self, serializer):
        user = self.request.user
        if user.is_superuser:
            if not serializer.validated_data.get("empresa"):
                raise ValidationError({"empresa": "Informe a empresa da natureza."})
            serializer.save()
            return
        if not getattr(user, "empresa_id", None):
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        empresa = serializer.validated_data.get("empresa")
        if empresa and empresa.id != user.empresa_id:
            raise ValidationError({"empresa": "Você só pode cadastrar natureza na sua empresa."})
        serializer.save(empresa=user.empresa)

    def perform_update(self, serializer):
        self.perform_create(serializer)

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError:
            raise ValidationError({
                "detail": "Natureza já utilizada em lançamentos. Inative o cadastro em vez de excluir."
            })
