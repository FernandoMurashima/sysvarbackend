from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import viewsets, permissions, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, DateTimeField, DecimalField, ExpressionWrapper, F, IntegerField, Max, OuterRef, ProtectedError, Q, Sum, Value, When, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from accounts.permissions import HasModuleRole
from accounts.services.effective_access import EffectiveAccessService, MasterTransferService, audit_event, increment_permissions_version, sync_legacy_license_flags
from accounts.services.sessions import ConcurrentSessionService
from accounts.serializers import EmpresaContratoDetalheSerializer
from auditoria.models import AuditAction, AuditCategory, AuditLog
from auditoria.services import AuditService, instance_snapshot

from .models import (
    Empresa,
    EmpresaContrato,
    Loja,
    Cliente,
    Fornecedor,
    FornecedorContato,
    FornecedorEndereco,
    Funcionarios,
    Nat_Lancamento,
    PlanoContabil,
)
from .serializers import (
    EmpresaSerializer,
    LojaSerializer,
    ClienteSerializer,
    FornecedorSerializer,
    FornecedorContatoSerializer,
    FornecedorEnderecoSerializer,
    FuncionariosSerializer,
    NatLancamentoSerializer,
    PlanoContabilSerializer,
)
from .services import ClientePadraoService

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
    EXCLUSAO_NEGADA_DETAIL = "Este cliente possui vendas ou outros registros vinculados e não pode ser excluído. Utilize a inativação."
    queryset = Cliente.objects.select_related("empresa", "bloqueado_por").all()
    serializer_class = ClienteSerializer

    filterset_fields = [
        "ativo", "empresa", "estado", "cidade", "categoria", "bloqueio",
        "mala_direta", "tipo_pessoa", "cliente_padrao", "documento", "email",
    ]
    search_fields = ["nome_cliente", "apelido", "documento", "cpf", "email", "cidade", "telefone1", "telefone2"]
    ordering_fields = ["nome_cliente", "cidade", "estado", "data_cadastro", "ultima_compra", "total_comprado", "quantidade_compras", "ticket_medio"]
    ordering = ["nome_cliente"]
    action_required_access = {
        "indicadores": "VIEW",
        "historico": "VIEW",
        "compras": "VIEW",
        "ativar": "EDIT",
        "inativar": "EDIT",
        "bloquear": "EDIT",
        "desbloquear": "EDIT",
    }

    def get_queryset(self):
        qs = super().get_queryset()
        qs = self._with_indicadores_compra(qs)
        com_compras = self.request.query_params.get("com_compras")
        sem_compras = self.request.query_params.get("sem_compras")
        if str(com_compras).lower() == "true":
            qs = qs.filter(quantidade_compras__gt=0)
        if str(sem_compras).lower() == "true":
            qs = qs.filter(quantidade_compras=0)
        return qs

    def _with_indicadores_compra(self, qs):
        try:
            from fiscal.models import VendaDevolucao, VendaPdv

            vendas_validas = (
                VendaPdv.objects
                .filter(
                    cliente_id=OuterRef("pk"),
                    empresa_id=OuterRef("empresa_id"),
                    status=VendaPdv.Status.FINALIZADA,
                )
                .values("cliente_id")
            )
            devolucoes_validas = (
                VendaDevolucao.objects
                .filter(
                    cliente_id=OuterRef("pk"),
                    empresa_id=OuterRef("empresa_id"),
                    status=VendaDevolucao.Status.FINALIZADA,
                )
                .values("cliente_id")
            )
            decimal_field = DecimalField(max_digits=18, decimal_places=2)
            total_bruto = Coalesce(
                Subquery(vendas_validas.annotate(total=Sum("total")).values("total")[:1], output_field=decimal_field),
                Value(Decimal("0.00")),
                output_field=decimal_field,
            )
            total_devolvido = Coalesce(
                Subquery(devolucoes_validas.annotate(total=Sum("credito_cliente")).values("total")[:1], output_field=decimal_field),
                Value(Decimal("0.00")),
                output_field=decimal_field,
            )
            quantidade = Coalesce(
                Subquery(vendas_validas.annotate(total=Count("id")).values("total")[:1], output_field=IntegerField()),
                Value(0),
                output_field=IntegerField(),
            )
            return qs.annotate(
                ultima_compra=Subquery(
                    vendas_validas.annotate(ultima=Max("data_venda")).values("ultima")[:1],
                    output_field=DateTimeField(),
                ),
                total_vendas_bruto=total_bruto,
                total_devolucoes=total_devolvido,
                total_comprado=ExpressionWrapper(total_bruto - total_devolvido, output_field=decimal_field),
                quantidade_compras=quantidade,
                ticket_medio=Case(
                    When(
                        quantidade_compras__gt=0,
                        then=ExpressionWrapper((total_bruto - total_devolvido) / F("quantidade_compras"), output_field=decimal_field),
                    ),
                    default=Value(Decimal("0.00")),
                    output_field=decimal_field,
                ),
            )
        except Exception:
            return qs.annotate(
                total_comprado=Value(0, output_field=DecimalField(max_digits=18, decimal_places=2)),
                quantidade_compras=Value(0),
                ticket_medio=Value(0, output_field=DecimalField(max_digits=18, decimal_places=2)),
            )

    def perform_create(self, serializer):
        try:
            with transaction.atomic():
                cliente = self._save_cliente(serializer)
                AuditService.required_success(
                    AuditAction.CLIENT_CREATED,
                    category=AuditCategory.CADASTRO,
                    request=self.request,
                    user=self.request.user,
                    instance=cliente,
                    after=instance_snapshot(cliente),
                    status_code=201,
                )
        except IntegrityError:
            raise ValidationError({"documento": "Já existe um cliente com este documento nesta empresa."})

    def perform_update(self, serializer):
        instance = serializer.instance
        before = instance_snapshot(instance)
        if instance.cliente_padrao:
            atual_empresa = instance.empresa_id
            atual_doc = instance.documento
            atual_tipo = instance.tipo_pessoa
            atual_padrao = instance.cliente_padrao
        try:
            with transaction.atomic():
                cliente = self._save_cliente(serializer)
                AuditService.required_success(
                    AuditAction.CLIENT_UPDATED,
                    category=AuditCategory.CADASTRO,
                    request=self.request,
                    user=self.request.user,
                    instance=cliente,
                    before=before,
                    after=instance_snapshot(cliente),
                    changed_fields=sorted(serializer.validated_data.keys()),
                    status_code=200,
                )
        except IntegrityError:
            raise ValidationError({"documento": "Já existe um cliente com este documento nesta empresa."})
        if instance.cliente_padrao and (
            instance.empresa_id != atual_empresa
            or instance.documento != atual_doc
            or instance.tipo_pessoa != atual_tipo
            or instance.cliente_padrao != atual_padrao
        ):
            raise ValidationError({"cliente_padrao": "Cliente padrão possui dados protegidos."})

    def _save_cliente(self, serializer):
        user = self.request.user
        if self.request.user.is_superuser:
            if not serializer.validated_data.get("empresa") and not getattr(serializer.instance, "empresa_id", None):
                raise ValidationError({"empresa": "Informe a empresa do cliente."})
            return serializer.save()
        if not getattr(user, "empresa_id", None):
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        empresa = serializer.validated_data.get("empresa")
        if empresa and empresa.id != user.empresa_id:
            raise ValidationError({"empresa": "Você só pode cadastrar clientes na empresa vinculada ao seu usuário."})
        return serializer.save(empresa=user.empresa)

    @action(detail=False, methods=["get"], url_path="indicadores")
    def indicadores(self, request):
        qs = self.filter_queryset(self.get_queryset())
        return Response({
            "total": qs.count(),
            "ativos": qs.filter(ativo=True).count(),
            "inativos": qs.filter(ativo=False).count(),
            "bloqueados": qs.filter(bloqueio=True).count(),
            "pessoas_fisicas": qs.filter(tipo_pessoa=Cliente.TIPO_PESSOA_FISICA).count(),
            "pessoas_juridicas": qs.filter(tipo_pessoa=Cliente.TIPO_PESSOA_JURIDICA).count(),
            "clientes_identificados": qs.filter(cliente_padrao=False).count(),
            "cliente_padrao": qs.filter(cliente_padrao=True).count(),
            "com_consentimento": qs.filter(Q(aceita_email=True) | Q(aceita_whatsapp=True) | Q(aceita_sms=True) | Q(mala_direta=True)).count(),
            "clientes_com_compras": qs.filter(quantidade_compras__gt=0, cliente_padrao=False).count(),
            "clientes_sem_compras": qs.filter(quantidade_compras=0, cliente_padrao=False).count(),
        })

    @action(detail=True, methods=["get"], url_path="historico")
    def historico(self, request, pk=None):
        cliente = self.get_object()
        qs = AuditLog.objects.filter(
            empresa_id=cliente.empresa_id,
            app_label="cadastros",
            model="cliente",
            object_id=str(cliente.pk),
        ).select_related("user").order_by("-created_at", "-id")
        page = self.paginate_queryset(qs)
        logs = page if page is not None else qs
        data = [self._historico_item(log) for log in logs]
        if page is not None:
            return self.get_paginated_response(data)
        return Response(data)

    def _historico_item(self, log):
        before = log.before_data or {}
        after = log.after_data or {}
        metadata = log.metadata or {}
        return {
            "id": log.id,
            "created_at": log.created_at,
            "acao": log.action,
            "acao_descricao": self._historico_descricao(log.action),
            "usuario": log.user_nome_snapshot or log.username_snapshot or None,
            "origem": log.origin,
            "resultado": log.result,
            "campos_alterados": log.changed_fields or [],
            "motivo": metadata.get("mensagem") or log.error_message or metadata.get("motivo") or after.get("motivo_bloqueio") or before.get("motivo_bloqueio"),
            "observacao": metadata.get("observacao") or after.get("observacao_bloqueio") or before.get("observacao_bloqueio"),
        }

    def _historico_descricao(self, action_name):
        return {
            AuditAction.CLIENT_CREATED: "Cliente criado",
            AuditAction.CLIENT_UPDATED: "Cliente atualizado",
            AuditAction.CLIENT_ACTIVATED: "Cliente ativado",
            AuditAction.CLIENT_DEACTIVATED: "Cliente inativado",
            AuditAction.CLIENT_BLOCKED: "Cliente bloqueado",
            AuditAction.CLIENT_UNBLOCKED: "Cliente desbloqueado",
            AuditAction.CLIENT_DELETED: "Cliente excluído",
            AuditAction.CLIENT_DELETE_DENIED: "Exclusão negada",
        }.get(action_name, action_name)

    @action(detail=True, methods=["get"], url_path="compras")
    def compras(self, request, pk=None):
        cliente = self.get_object()
        from fiscal.models import VendaPdv

        qs = (
            VendaPdv.objects
            .filter(cliente_id=cliente.pk, empresa_id=cliente.empresa_id)
            .select_related("loja", "vendedor", "nfce")
            .prefetch_related("itens", "pagamentos", "devolucoes")
        )
        data_inicio = request.query_params.get("data_inicio")
        data_fim = request.query_params.get("data_fim")
        status_q = request.query_params.get("status")
        loja = request.query_params.get("loja")
        if data_inicio:
            qs = qs.filter(data_venda__date__gte=data_inicio)
        if data_fim:
            qs = qs.filter(data_venda__date__lte=data_fim)
        if status_q:
            qs = qs.filter(status=status_q)
        if loja:
            qs = qs.filter(loja_id=loja)
        ordering = request.query_params.get("ordering") or "-data_venda"
        allowed_ordering = {"data_venda", "-data_venda", "total", "-total", "documento", "-documento", "status", "-status"}
        qs = qs.order_by(ordering if ordering in allowed_ordering else "-data_venda", "-id")
        page = self.paginate_queryset(qs)
        vendas = page if page is not None else qs
        data = [self._compra_item(venda, request.user) for venda in vendas]
        if page is not None:
            return self.get_paginated_response(data)
        return Response(data)

    def _compra_item(self, venda, user):
        devolucoes_finalizadas = [
            devolucao for devolucao in venda.devolucoes.all()
            if devolucao.status == devolucao.Status.FINALIZADA
        ]
        valor_devolvido = sum((Decimal(devolucao.credito_cliente or 0) for devolucao in devolucoes_finalizadas), Decimal("0.00"))
        devolvida = bool(devolucoes_finalizadas)
        total = Decimal(venda.total or 0)
        cancelada = venda.status == venda.Status.CANCELADA
        if cancelada:
            status_descricao = "Cancelada"
        elif venda.status == venda.Status.ABERTA:
            status_descricao = "Aberta"
        elif devolvida and valor_devolvido >= total:
            status_descricao = "Devolvida"
        elif devolvida:
            status_descricao = "Parcialmente devolvida"
        else:
            status_descricao = venda.get_status_display()
        pagamentos = list(venda.pagamentos.all())
        if len(pagamentos) > 1:
            forma_pagamento = "Múltiplas"
        elif pagamentos:
            forma_pagamento = pagamentos[0].descricao or pagamentos[0].forma
        else:
            forma_pagamento = venda.forma_pagamento
        nfce = getattr(venda, "nfce", None)
        return {
            "id": venda.id,
            "data_venda": venda.data_venda,
            "numero_venda": venda.documento,
            "numero_documento": str(getattr(nfce, "numero", "") or venda.documento),
            "loja_id": venda.loja_id,
            "loja_nome": getattr(venda.loja, "nome_loja", None),
            "vendedor_id": venda.vendedor_id,
            "vendedor_nome": getattr(venda.vendedor, "nomefuncionario", None),
            "quantidade_itens": sum(int(item.quantidade or 0) for item in venda.itens.all()),
            "valor_bruto": str(venda.subtotal),
            "desconto": str(Decimal(venda.desconto_itens or 0) + Decimal(venda.desconto_geral or 0)),
            "valor_final": str(venda.total),
            "valor_devolvido": str(valor_devolvido),
            "forma_pagamento": forma_pagamento,
            "status": venda.status,
            "status_descricao": status_descricao,
            "cancelada": cancelada,
            "devolvida": devolvida,
            "pode_consultar_venda": False,
            "detalhe_venda_url": None,
        }

    @action(detail=True, methods=["post"], url_path="ativar")
    def ativar(self, request, pk=None):
        scoped = self.get_object()
        with transaction.atomic():
            cliente = Cliente.objects.select_for_update().get(pk=scoped.pk)
            before = instance_snapshot(cliente)
            cliente.ativo = True
            cliente.save(update_fields=["ativo", "cpf", "documento"])
            AuditService.required_success(AuditAction.CLIENT_ACTIVATED, category=AuditCategory.CADASTRO, request=request, user=request.user, instance=cliente, before=before, after=instance_snapshot(cliente), changed_fields=["ativo"], status_code=200)
        return Response(self.get_serializer(cliente).data)

    @action(detail=True, methods=["post"], url_path="inativar")
    def inativar(self, request, pk=None):
        scoped = self.get_object()
        cliente = scoped
        if cliente.cliente_padrao:
            return self._deny_cliente_padrao(cliente, request, "inativar")
        with transaction.atomic():
            cliente = Cliente.objects.select_for_update().get(pk=scoped.pk)
            before = instance_snapshot(cliente)
            cliente.ativo = False
            cliente.save(update_fields=["ativo", "cpf", "documento"])
            AuditService.required_success(AuditAction.CLIENT_DEACTIVATED, category=AuditCategory.CADASTRO, request=request, user=request.user, instance=cliente, before=before, after=instance_snapshot(cliente), changed_fields=["ativo"], status_code=200)
        return Response(self.get_serializer(cliente).data)

    @action(detail=True, methods=["post"], url_path="bloquear")
    def bloquear(self, request, pk=None):
        scoped = self.get_object()
        cliente = scoped
        if cliente.cliente_padrao:
            return self._deny_cliente_padrao(cliente, request, "bloquear")
        motivo = (request.data.get("motivo") or request.data.get("motivo_bloqueio") or "").strip()
        if not motivo:
            raise ValidationError({"motivo": "Informe o motivo do bloqueio."})
        observacao = (request.data.get("observacao") or request.data.get("observacao_bloqueio") or "").strip() or None
        with transaction.atomic():
            cliente = Cliente.objects.select_for_update().get(pk=scoped.pk)
            before = instance_snapshot(cliente)
            cliente.bloqueio = True
            cliente.motivo_bloqueio = motivo[:80]
            cliente.observacao_bloqueio = observacao
            cliente.bloqueado_em = timezone.now()
            cliente.bloqueado_por = request.user if request.user.is_authenticated else None
            cliente.save(update_fields=["bloqueio", "motivo_bloqueio", "observacao_bloqueio", "bloqueado_em", "bloqueado_por", "cpf", "documento"])
            AuditService.required_success(AuditAction.CLIENT_BLOCKED, category=AuditCategory.CADASTRO, request=request, user=request.user, instance=cliente, before=before, after=instance_snapshot(cliente), changed_fields=["bloqueio", "motivo_bloqueio", "observacao_bloqueio", "bloqueado_em", "bloqueado_por"], metadata={"motivo": motivo[:80], "observacao": observacao}, status_code=200)
        return Response(self.get_serializer(cliente).data)

    @action(detail=True, methods=["post"], url_path="desbloquear")
    def desbloquear(self, request, pk=None):
        scoped = self.get_object()
        with transaction.atomic():
            cliente = Cliente.objects.select_for_update().get(pk=scoped.pk)
            before = instance_snapshot(cliente)
            motivo_anterior = cliente.motivo_bloqueio
            observacao_anterior = cliente.observacao_bloqueio
            cliente.bloqueio = False
            cliente.motivo_bloqueio = None
            cliente.observacao_bloqueio = None
            cliente.bloqueado_em = None
            cliente.bloqueado_por = None
            cliente.save(update_fields=["bloqueio", "motivo_bloqueio", "observacao_bloqueio", "bloqueado_em", "bloqueado_por", "cpf", "documento"])
            AuditService.required_success(AuditAction.CLIENT_UNBLOCKED, category=AuditCategory.CADASTRO, request=request, user=request.user, instance=cliente, before=before, after=instance_snapshot(cliente), changed_fields=["bloqueio", "motivo_bloqueio", "observacao_bloqueio", "bloqueado_em", "bloqueado_por"], metadata={"motivo": motivo_anterior, "observacao": observacao_anterior}, status_code=200)
        return Response(self.get_serializer(cliente).data)

    def perform_destroy(self, instance):
        if instance.cliente_padrao:
            AuditService.denied(AuditAction.CLIENT_DELETE_DENIED, category=AuditCategory.CADASTRO, request=self.request, user=self.request.user, instance=instance, metadata={"motivo": "cliente_padrao"}, status_code=400)
            raise ValidationError({"detail": "Cliente padrão não pode ser excluído."})
        impedimentos = self._impedimentos_exclusao(instance)
        if impedimentos:
            self._auditar_exclusao_negada(instance, "vinculos", impedimentos)
            raise ValidationError({"detail": self.EXCLUSAO_NEGADA_DETAIL})
        before = instance_snapshot(instance)
        object_id = instance.pk
        empresa = getattr(instance, "empresa", None)
        try:
            instance.delete()
        except ProtectedError:
            self._auditar_exclusao_negada(instance, "protected_error")
            raise ValidationError({"detail": self.EXCLUSAO_NEGADA_DETAIL})
        except IntegrityError:
            self._auditar_exclusao_negada(instance, "integrity_error")
            raise ValidationError({"detail": self.EXCLUSAO_NEGADA_DETAIL})
        AuditService.required_success(AuditAction.CLIENT_DELETED, category=AuditCategory.CADASTRO, request=self.request, user=self.request.user, empresa=empresa, app_label="cadastros", model="cliente", object_id=object_id, before=before, status_code=204)

    def _impedimentos_exclusao(self, cliente):
        checks = [
            ("vendas", "vendas_pdv"),
            ("devoluções", "devolucoes_venda"),
            ("cashback", "cashback_movimentos"),
            ("vale-troca", "vales_troca"),
            ("títulos financeiros", "receber_set"),
        ]
        impedimentos = []
        for label, related_name in checks:
            manager = getattr(cliente, related_name, None)
            if manager is not None and manager.exists():
                impedimentos.append(f"Possui {label}.")
        return impedimentos

    def _auditar_exclusao_negada(self, cliente, motivo, impedimentos=None):
        AuditService.denied(
            AuditAction.CLIENT_DELETE_DENIED,
            category=AuditCategory.CADASTRO,
            request=self.request,
            user=self.request.user,
            instance=cliente,
            metadata={
                "motivo": motivo,
                "impedimentos": impedimentos or [],
                "mensagem": self.EXCLUSAO_NEGADA_DETAIL,
            },
            error_message=self.EXCLUSAO_NEGADA_DETAIL,
            status_code=400,
        )

    def _deny_cliente_padrao(self, cliente, request, operacao):
        AuditService.denied(AuditAction.CLIENT_OPERATION_DENIED, category=AuditCategory.CADASTRO, request=request, user=request.user, instance=cliente, metadata={"operacao": operacao, "motivo": "cliente_padrao"}, status_code=400)
        return Response({"detail": "Cliente padrão não pode ser alterado por esta ação."}, status=400)


class FornecedorViewSet(BaseCadastroViewSet):
    EXCLUSAO_NEGADA_DETAIL = "Este fornecedor possui compras ou outros registros vinculados e não pode ser excluído. Utilize a inativação."
    queryset = Fornecedor.objects.select_related("empresa", "bloqueado_por", "natureza_padrao").prefetch_related("categorias_rel", "contatos", "enderecos").all()
    serializer_class = FornecedorSerializer

    filterset_fields = ["ativo", "empresa", "estado", "cidade", "categoria", "bloqueio", "mala_direta", "cnpj", "documento", "tipo_pessoa"]
    search_fields = ["nome_fornecedor", "apelido", "documento", "cnpj", "email", "cidade", "telefone1", "telefone2"]
    ordering_fields = ["nome_fornecedor", "cidade", "estado", "data_cadastro", "ultima_compra", "total_comprado", "quantidade_compras", "ticket_medio", "saldo_a_pagar"]
    ordering = ["nome_fornecedor"]
    action_required_access = {
        "indicadores": "VIEW",
        "historico": "VIEW",
        "compras": "VIEW",
        "financeiro": "VIEW",
        "possiveis_duplicados": "VIEW",
        "ativar": "EDIT",
        "inativar": "EDIT",
        "bloquear": "EDIT",
        "desbloquear": "EDIT",
        "contatos": "EDIT",
        "contato": "EDIT",
        "contato_inativar": "EDIT",
        "contato_reativar": "EDIT",
        "enderecos": "EDIT",
        "endereco": "EDIT",
        "endereco_inativar": "EDIT",
        "endereco_reativar": "EDIT",
    }

    def get_queryset(self):
        qs = super().get_queryset()
        categoria = self.request.query_params.get("categoria")
        if categoria:
            qs = qs.filter(Q(categoria=categoria) | Q(categorias_rel__categoria=categoria)).distinct()
        somente_utilizaveis = str(self.request.query_params.get("utilizavel") or "").lower() == "true"
        if somente_utilizaveis:
            qs = qs.filter(ativo=True, bloqueio=False)
        return self._with_indicadores(qs)

    def _with_indicadores(self, qs):
        try:
            from compras.models import PedidoCompra
            from financeiro.models import PagarItem

            pedidos_validos = (
                PedidoCompra.objects
                .filter(
                    fornecedor_id=OuterRef("pk"),
                    empresa_id=OuterRef("empresa_id"),
                    status__in=["AP", "AT"],
                )
                .values("fornecedor_id")
            )
            itens_abertos = (
                PagarItem.objects
                .filter(
                    Idpagar__idfornecedor_id=OuterRef("pk"),
                    Idpagar__empresa_id=OuterRef("empresa_id"),
                )
                .exclude(status__in=[PagarItem.STATUS_BAIXADO, PagarItem.STATUS_CANCELADO])
                .values("Idpagar__idfornecedor_id")
            )
            decimal_field = DecimalField(max_digits=18, decimal_places=2)
            total_comprado = Coalesce(
                Subquery(pedidos_validos.annotate(total=Sum("total_pedido")).values("total")[:1], output_field=decimal_field),
                Value(Decimal("0.00")),
                output_field=decimal_field,
            )
            quantidade = Coalesce(
                Subquery(pedidos_validos.annotate(total=Count("id")).values("total")[:1], output_field=IntegerField()),
                Value(0),
                output_field=IntegerField(),
            )
            saldo = Coalesce(
                Subquery(itens_abertos.annotate(total=Sum("valor_parcela")).values("total")[:1], output_field=decimal_field),
                Value(Decimal("0.00")),
                output_field=decimal_field,
            )
            return qs.annotate(
                ultima_compra=Subquery(pedidos_validos.annotate(ultima=Max("emissao")).values("ultima")[:1]),
                total_comprado=total_comprado,
                quantidade_compras=quantidade,
                ticket_medio=Case(
                    When(
                        quantidade_compras__gt=0,
                        then=ExpressionWrapper(total_comprado / F("quantidade_compras"), output_field=decimal_field),
                    ),
                    default=Value(Decimal("0.00")),
                    output_field=decimal_field,
                ),
                saldo_a_pagar=saldo,
            )
        except Exception:
            return qs.annotate(
                total_comprado=Value(0, output_field=DecimalField(max_digits=18, decimal_places=2)),
                quantidade_compras=Value(0),
                ticket_medio=Value(0, output_field=DecimalField(max_digits=18, decimal_places=2)),
                saldo_a_pagar=Value(0, output_field=DecimalField(max_digits=18, decimal_places=2)),
            )

    def perform_create(self, serializer):
        try:
            with transaction.atomic():
                fornecedor = self._save_fornecedor(serializer)
                AuditService.required_success(AuditAction.SUPPLIER_CREATED, category=AuditCategory.CADASTRO, request=self.request, user=self.request.user, instance=fornecedor, after=instance_snapshot(fornecedor), status_code=201)
        except IntegrityError:
            raise ValidationError({"documento": "Já existe um fornecedor com este documento nesta empresa."})

    def perform_update(self, serializer):
        before = instance_snapshot(serializer.instance)
        try:
            with transaction.atomic():
                fornecedor = self._save_fornecedor(serializer)
                AuditService.required_success(AuditAction.SUPPLIER_UPDATED, category=AuditCategory.CADASTRO, request=self.request, user=self.request.user, instance=fornecedor, before=before, after=instance_snapshot(fornecedor), changed_fields=sorted(serializer.validated_data.keys()), status_code=200)
        except IntegrityError:
            raise ValidationError({"documento": "Já existe um fornecedor com este documento nesta empresa."})

    def _save_fornecedor(self, serializer):
        user = self.request.user
        if user.is_superuser:
            if not serializer.validated_data.get("empresa") and not getattr(serializer.instance, "empresa_id", None):
                raise ValidationError({"empresa": "Informe a empresa do fornecedor."})
            return serializer.save()
        if not getattr(user, "empresa_id", None):
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        empresa = serializer.validated_data.get("empresa")
        if empresa and empresa.id != user.empresa_id:
            raise ValidationError({"empresa": "Você só pode cadastrar fornecedores na empresa vinculada ao seu usuário."})
        return serializer.save(empresa=user.empresa)

    @action(detail=False, methods=["get"], url_path="indicadores")
    def indicadores(self, request):
        qs = self.filter_queryset(self.get_queryset())
        return Response({
            "total": qs.count(),
            "ativos": qs.filter(ativo=True).count(),
            "inativos": qs.filter(ativo=False).count(),
            "bloqueados": qs.filter(bloqueio=True).count(),
            "pessoas_fisicas": qs.filter(tipo_pessoa=Fornecedor.TIPO_PESSOA_FISICA).count(),
            "pessoas_juridicas": qs.filter(tipo_pessoa=Fornecedor.TIPO_PESSOA_JURIDICA).count(),
            "sem_documento": qs.filter(documento__isnull=True).count(),
            "com_compras": qs.filter(quantidade_compras__gt=0).count(),
            "saldo_a_pagar": qs.aggregate(total=Coalesce(Sum("saldo_a_pagar"), Value(Decimal("0.00")), output_field=DecimalField(max_digits=18, decimal_places=2)))["total"],
        })

    @action(detail=False, methods=["get"], url_path="possiveis-duplicados")
    def possiveis_duplicados(self, request):
        nome = self._normalizar_nome(request.query_params.get("nome") or "")
        if not nome:
            return Response([])
        qs = self.get_queryset()
        atual = request.query_params.get("id")
        if atual:
            qs = qs.exclude(pk=atual)
        data = []
        for fornecedor in qs.only("id", "nome_fornecedor", "apelido", "documento")[:500]:
            candidatos = [fornecedor.nome_fornecedor, fornecedor.apelido]
            if any(nome and (nome == self._normalizar_nome(c) or nome in self._normalizar_nome(c) or self._normalizar_nome(c) in nome) for c in candidatos if c):
                data.append({"id": fornecedor.pk, "nome_fornecedor": fornecedor.nome_fornecedor, "apelido": fornecedor.apelido, "documento": fornecedor.documento})
        return Response(data[:10])

    def _normalizar_nome(self, value):
        import unicodedata

        normalized = unicodedata.normalize("NFD", str(value or "").strip().lower())
        normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
        return " ".join(normalized.split())

    @action(detail=True, methods=["get"], url_path="historico")
    def historico(self, request, pk=None):
        fornecedor = self.get_object()
        qs = AuditLog.objects.filter(
            empresa_id=fornecedor.empresa_id,
            app_label="cadastros",
            model__in=["fornecedor", "fornecedorcontato", "fornecedorendereco"],
        ).filter(Q(object_id=str(fornecedor.pk)) | Q(metadata__fornecedor_id=fornecedor.pk)).select_related("user").order_by("-created_at", "-id")
        page = self.paginate_queryset(qs)
        logs = page if page is not None else qs
        data = [self._historico_item(log) for log in logs]
        if page is not None:
            return self.get_paginated_response(data)
        return Response(data)

    def _historico_item(self, log):
        metadata = log.metadata or {}
        return {
            "id": log.id,
            "created_at": log.created_at,
            "acao": log.action,
            "acao_descricao": self._historico_descricao(log.action),
            "usuario": log.user_nome_snapshot or log.username_snapshot or None,
            "origem": log.origin,
            "resultado": log.result,
            "campos_alterados": log.changed_fields or [],
            "motivo": metadata.get("mensagem") or log.error_message or metadata.get("motivo"),
            "observacao": metadata.get("observacao"),
        }

    def _historico_descricao(self, action_name):
        return {
            AuditAction.SUPPLIER_CREATED: "Fornecedor criado",
            AuditAction.SUPPLIER_UPDATED: "Fornecedor atualizado",
            AuditAction.SUPPLIER_ACTIVATED: "Fornecedor ativado",
            AuditAction.SUPPLIER_DEACTIVATED: "Fornecedor inativado",
            AuditAction.SUPPLIER_BLOCKED: "Fornecedor bloqueado",
            AuditAction.SUPPLIER_UNBLOCKED: "Fornecedor desbloqueado",
            AuditAction.SUPPLIER_DELETED: "Fornecedor excluído",
            AuditAction.SUPPLIER_DELETE_DENIED: "Exclusão negada",
            AuditAction.SUPPLIER_CONTACT_CREATED: "Contato criado",
            AuditAction.SUPPLIER_CONTACT_UPDATED: "Contato atualizado",
            AuditAction.SUPPLIER_CONTACT_DEACTIVATED: "Contato inativado",
            AuditAction.SUPPLIER_CONTACT_REACTIVATED: "Contato reativado",
            AuditAction.SUPPLIER_ADDRESS_CREATED: "Endereço criado",
            AuditAction.SUPPLIER_ADDRESS_UPDATED: "Endereço atualizado",
            AuditAction.SUPPLIER_ADDRESS_DEACTIVATED: "Endereço inativado",
            AuditAction.SUPPLIER_ADDRESS_REACTIVATED: "Endereço reativado",
        }.get(action_name, action_name)

    @action(detail=True, methods=["get"], url_path="compras")
    def compras(self, request, pk=None):
        fornecedor = self.get_object()
        from compras.models import PedidoCompra

        qs = PedidoCompra.objects.filter(fornecedor=fornecedor, empresa_id=fornecedor.empresa_id).select_related("loja").order_by("-emissao", "-id")
        status_q = request.query_params.get("status")
        loja = request.query_params.get("loja")
        if status_q:
            qs = qs.filter(status=status_q)
        if loja:
            qs = qs.filter(loja_id=loja)
        page = self.paginate_queryset(qs)
        pedidos = page if page is not None else qs
        data = [{
            "id": pedido.id,
            "data": pedido.emissao,
            "tipo": pedido.tipo,
            "tipo_descricao": pedido.get_tipo_display(),
            "loja_id": pedido.loja_id,
            "loja_nome": getattr(pedido.loja, "nome_loja", None),
            "status": pedido.status,
            "status_descricao": pedido.get_status_display(),
            "total": str(pedido.total_pedido),
        } for pedido in pedidos]
        if page is not None:
            return self.get_paginated_response(data)
        return Response(data)

    @action(detail=True, methods=["get"], url_path="financeiro")
    def financeiro(self, request, pk=None):
        fornecedor = self.get_object()
        from financeiro.models import PagarItem

        qs = PagarItem.objects.filter(Idpagar__idfornecedor=fornecedor, Idpagar__empresa_id=fornecedor.empresa_id).select_related("Idpagar", "Idpagar__idloja", "Idnatureza").order_by("Data_vencimento", "Idpagaritem")
        page = self.paginate_queryset(qs)
        itens = page if page is not None else qs
        data = [{
            "id": item.Idpagaritem,
            "titulo_id": item.Idpagar_id,
            "titulo": item.Idpagar.Titulo,
            "documento": item.Idpagar.Documento,
            "loja_nome": getattr(item.Idpagar.idloja, "nome_loja", None),
            "parcela_n": item.parcela_n,
            "status": item.status,
            "vencimento": item.Data_vencimento,
            "valor": str(item.valor_parcela),
            "valor_baixa": str(item.valor_baixa or Decimal("0.00")),
            "natureza": getattr(item.Idnatureza, "descricao", None),
        } for item in itens]
        if page is not None:
            return self.get_paginated_response(data)
        return Response(data)

    @action(detail=True, methods=["post"], url_path="ativar")
    def ativar(self, request, pk=None):
        scoped = self.get_object()
        with transaction.atomic():
            fornecedor = Fornecedor.objects.select_for_update().get(pk=scoped.pk)
            before = instance_snapshot(fornecedor)
            fornecedor.ativo = True
            fornecedor.save(update_fields=["ativo", "documento", "cnpj"])
            AuditService.required_success(AuditAction.SUPPLIER_ACTIVATED, category=AuditCategory.CADASTRO, request=request, user=request.user, instance=fornecedor, before=before, after=instance_snapshot(fornecedor), changed_fields=["ativo"], status_code=200)
        return Response(self.get_serializer(fornecedor).data)

    @action(detail=True, methods=["post"], url_path="inativar")
    def inativar(self, request, pk=None):
        scoped = self.get_object()
        with transaction.atomic():
            fornecedor = Fornecedor.objects.select_for_update().get(pk=scoped.pk)
            before = instance_snapshot(fornecedor)
            fornecedor.ativo = False
            fornecedor.save(update_fields=["ativo", "documento", "cnpj"])
            AuditService.required_success(AuditAction.SUPPLIER_DEACTIVATED, category=AuditCategory.CADASTRO, request=request, user=request.user, instance=fornecedor, before=before, after=instance_snapshot(fornecedor), changed_fields=["ativo"], status_code=200)
        return Response(self.get_serializer(fornecedor).data)

    @action(detail=True, methods=["post"], url_path="bloquear")
    def bloquear(self, request, pk=None):
        scoped = self.get_object()
        motivo = (request.data.get("motivo") or request.data.get("motivo_bloqueio") or "").strip()
        if not motivo:
            raise ValidationError({"motivo": "Informe o motivo do bloqueio."})
        observacao = (request.data.get("observacao") or request.data.get("observacao_bloqueio") or "").strip() or None
        with transaction.atomic():
            fornecedor = Fornecedor.objects.select_for_update().get(pk=scoped.pk)
            before = instance_snapshot(fornecedor)
            fornecedor.bloqueio = True
            fornecedor.motivo_bloqueio = motivo[:80]
            fornecedor.observacao_bloqueio = observacao
            fornecedor.bloqueado_em = timezone.now()
            fornecedor.bloqueado_por = request.user if request.user.is_authenticated else None
            fornecedor.save(update_fields=["bloqueio", "motivo_bloqueio", "observacao_bloqueio", "bloqueado_em", "bloqueado_por", "documento", "cnpj"])
            AuditService.required_success(AuditAction.SUPPLIER_BLOCKED, category=AuditCategory.CADASTRO, request=request, user=request.user, instance=fornecedor, before=before, after=instance_snapshot(fornecedor), changed_fields=["bloqueio", "motivo_bloqueio", "observacao_bloqueio", "bloqueado_em", "bloqueado_por"], metadata={"motivo": motivo[:80], "observacao": observacao}, status_code=200)
        return Response(self.get_serializer(fornecedor).data)

    @action(detail=True, methods=["post"], url_path="desbloquear")
    def desbloquear(self, request, pk=None):
        scoped = self.get_object()
        with transaction.atomic():
            fornecedor = Fornecedor.objects.select_for_update().get(pk=scoped.pk)
            before = instance_snapshot(fornecedor)
            motivo_anterior = fornecedor.motivo_bloqueio
            observacao_anterior = fornecedor.observacao_bloqueio
            fornecedor.bloqueio = False
            fornecedor.motivo_bloqueio = None
            fornecedor.observacao_bloqueio = None
            fornecedor.bloqueado_em = None
            fornecedor.bloqueado_por = None
            fornecedor.save(update_fields=["bloqueio", "motivo_bloqueio", "observacao_bloqueio", "bloqueado_em", "bloqueado_por", "documento", "cnpj"])
            AuditService.required_success(AuditAction.SUPPLIER_UNBLOCKED, category=AuditCategory.CADASTRO, request=request, user=request.user, instance=fornecedor, before=before, after=instance_snapshot(fornecedor), changed_fields=["bloqueio", "motivo_bloqueio", "observacao_bloqueio", "bloqueado_em", "bloqueado_por"], metadata={"motivo": motivo_anterior, "observacao": observacao_anterior}, status_code=200)
        return Response(self.get_serializer(fornecedor).data)

    @action(detail=True, methods=["get", "post"], url_path="contatos")
    def contatos(self, request, pk=None):
        fornecedor = self.get_object()
        if request.method == "GET":
            return Response(FornecedorContatoSerializer(fornecedor.contatos.order_by("-principal", "tipo", "nome"), many=True).data)
        serializer = FornecedorContatoSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        contato = serializer.save(fornecedor=fornecedor, empresa=fornecedor.empresa)
        AuditService.required_success(AuditAction.SUPPLIER_CONTACT_CREATED, category=AuditCategory.CADASTRO, request=request, user=request.user, instance=contato, after=instance_snapshot(contato), metadata={"fornecedor_id": fornecedor.pk}, status_code=201)
        return Response(FornecedorContatoSerializer(contato).data, status=201)

    @action(detail=True, methods=["patch"], url_path=r"contatos/(?P<contato_id>[^/.]+)")
    def contato(self, request, pk=None, contato_id=None):
        fornecedor = self.get_object()
        contato = fornecedor.contatos.get(pk=contato_id, empresa=fornecedor.empresa)
        before = instance_snapshot(contato)
        serializer = FornecedorContatoSerializer(contato, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        contato = serializer.save()
        AuditService.required_success(AuditAction.SUPPLIER_CONTACT_UPDATED, category=AuditCategory.CADASTRO, request=request, user=request.user, instance=contato, before=before, after=instance_snapshot(contato), changed_fields=sorted(serializer.validated_data.keys()), metadata={"fornecedor_id": fornecedor.pk}, status_code=200)
        return Response(FornecedorContatoSerializer(contato).data)

    @action(detail=True, methods=["post"], url_path=r"contatos/(?P<contato_id>[^/.]+)/inativar")
    def contato_inativar(self, request, pk=None, contato_id=None):
        return self._toggle_contato(request, contato_id, False, AuditAction.SUPPLIER_CONTACT_DEACTIVATED)

    @action(detail=True, methods=["post"], url_path=r"contatos/(?P<contato_id>[^/.]+)/reativar")
    def contato_reativar(self, request, pk=None, contato_id=None):
        return self._toggle_contato(request, contato_id, True, AuditAction.SUPPLIER_CONTACT_REACTIVATED)

    def _toggle_contato(self, request, contato_id, ativo, action_name):
        fornecedor = self.get_object()
        contato = fornecedor.contatos.get(pk=contato_id, empresa=fornecedor.empresa)
        before = instance_snapshot(contato)
        contato.ativo = ativo
        contato.save(update_fields=["ativo", "data_atualizacao"])
        AuditService.required_success(action_name, category=AuditCategory.CADASTRO, request=request, user=request.user, instance=contato, before=before, after=instance_snapshot(contato), changed_fields=["ativo"], metadata={"fornecedor_id": fornecedor.pk}, status_code=200)
        return Response(FornecedorContatoSerializer(contato).data)

    @action(detail=True, methods=["get", "post"], url_path="enderecos")
    def enderecos(self, request, pk=None):
        fornecedor = self.get_object()
        if request.method == "GET":
            return Response(FornecedorEnderecoSerializer(fornecedor.enderecos.order_by("-principal", "tipo", "endereco"), many=True).data)
        serializer = FornecedorEnderecoSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        endereco = serializer.save(fornecedor=fornecedor, empresa=fornecedor.empresa)
        AuditService.required_success(AuditAction.SUPPLIER_ADDRESS_CREATED, category=AuditCategory.CADASTRO, request=request, user=request.user, instance=endereco, after=instance_snapshot(endereco), metadata={"fornecedor_id": fornecedor.pk}, status_code=201)
        return Response(FornecedorEnderecoSerializer(endereco).data, status=201)

    @action(detail=True, methods=["patch"], url_path=r"enderecos/(?P<endereco_id>[^/.]+)")
    def endereco(self, request, pk=None, endereco_id=None):
        fornecedor = self.get_object()
        endereco = fornecedor.enderecos.get(pk=endereco_id, empresa=fornecedor.empresa)
        before = instance_snapshot(endereco)
        serializer = FornecedorEnderecoSerializer(endereco, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        endereco = serializer.save()
        AuditService.required_success(AuditAction.SUPPLIER_ADDRESS_UPDATED, category=AuditCategory.CADASTRO, request=request, user=request.user, instance=endereco, before=before, after=instance_snapshot(endereco), changed_fields=sorted(serializer.validated_data.keys()), metadata={"fornecedor_id": fornecedor.pk}, status_code=200)
        return Response(FornecedorEnderecoSerializer(endereco).data)

    @action(detail=True, methods=["post"], url_path=r"enderecos/(?P<endereco_id>[^/.]+)/inativar")
    def endereco_inativar(self, request, pk=None, endereco_id=None):
        return self._toggle_endereco(request, endereco_id, False, AuditAction.SUPPLIER_ADDRESS_DEACTIVATED)

    @action(detail=True, methods=["post"], url_path=r"enderecos/(?P<endereco_id>[^/.]+)/reativar")
    def endereco_reativar(self, request, pk=None, endereco_id=None):
        return self._toggle_endereco(request, endereco_id, True, AuditAction.SUPPLIER_ADDRESS_REACTIVATED)

    def _toggle_endereco(self, request, endereco_id, ativo, action_name):
        fornecedor = self.get_object()
        endereco = fornecedor.enderecos.get(pk=endereco_id, empresa=fornecedor.empresa)
        before = instance_snapshot(endereco)
        endereco.ativo = ativo
        endereco.save(update_fields=["ativo", "data_atualizacao"])
        AuditService.required_success(action_name, category=AuditCategory.CADASTRO, request=request, user=request.user, instance=endereco, before=before, after=instance_snapshot(endereco), changed_fields=["ativo"], metadata={"fornecedor_id": fornecedor.pk}, status_code=200)
        return Response(FornecedorEnderecoSerializer(endereco).data)

    def perform_destroy(self, instance):
        impedimentos = self._impedimentos_exclusao(instance)
        if impedimentos:
            self._auditar_exclusao_negada(instance, "vinculos", impedimentos)
            raise ValidationError({"detail": self.EXCLUSAO_NEGADA_DETAIL})
        before = instance_snapshot(instance)
        object_id = instance.pk
        empresa = getattr(instance, "empresa", None)
        try:
            instance.delete()
        except ProtectedError:
            self._auditar_exclusao_negada(instance, "protected_error")
            raise ValidationError({"detail": self.EXCLUSAO_NEGADA_DETAIL})
        except IntegrityError:
            self._auditar_exclusao_negada(instance, "integrity_error")
            raise ValidationError({"detail": self.EXCLUSAO_NEGADA_DETAIL})
        AuditService.required_success(AuditAction.SUPPLIER_DELETED, category=AuditCategory.CADASTRO, request=self.request, user=self.request.user, empresa=empresa, app_label="cadastros", model="fornecedor", object_id=object_id, before=before, status_code=204)

    def _impedimentos_exclusao(self, fornecedor):
        checks = [
            ("compras", "pedidos_compra"),
            ("títulos financeiros", "pagar_set"),
            ("itens de ficha técnica", "itens_ficha_tecnica"),
            ("itens de ordem de produção", "itens_ordem_producao"),
        ]
        impedimentos = []
        for label, related_name in checks:
            manager = getattr(fornecedor, related_name, None)
            if manager is not None and manager.exists():
                impedimentos.append(f"Possui {label}.")
        return impedimentos

    def _auditar_exclusao_negada(self, fornecedor, motivo, impedimentos=None):
        AuditService.denied(
            AuditAction.SUPPLIER_DELETE_DENIED,
            category=AuditCategory.CADASTRO,
            request=self.request,
            user=self.request.user,
            instance=fornecedor,
            metadata={"motivo": motivo, "impedimentos": impedimentos or [], "mensagem": self.EXCLUSAO_NEGADA_DETAIL},
            error_message=self.EXCLUSAO_NEGADA_DETAIL,
            status_code=400,
        )


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
