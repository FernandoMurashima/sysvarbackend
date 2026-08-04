from django.contrib.auth import get_user_model
from rest_framework import viewsets, permissions, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from django.db.models import ProtectedError
from django_filters.rest_framework import DjangoFilterBackend
from accounts.permissions import HasModuleRole
from accounts.services.effective_access import MasterTransferService, audit_event, increment_permissions_version, sync_legacy_license_flags
from accounts.serializers import EmpresaContratoDetalheSerializer

from .models import Empresa, Loja, Cliente, Fornecedor, Funcionarios, Nat_Lancamento, PlanoContabil
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


class LojaViewSet(BaseCadastroViewSet):
    required_module = "operacional"
    queryset = Loja.objects.select_related("empresa").all()
    serializer_class = LojaSerializer

    filterset_fields = ["ativo", "empresa", "estado", "cidade", "cnpj"]
    search_fields = ["nome_loja", "apelido_loja", "cnpj", "cidade", "email", "telefone1", "telefone2"]
    ordering_fields = ["nome_loja", "cidade", "estado", "data_cadastro"]
    ordering = ["nome_loja"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_superuser:
            empresa = self.request.query_params.get("empresa")
            return qs.filter(empresa_id=empresa) if empresa else qs
        if getattr(user, "type", None) in {"Caixa", "Vendedor"}:
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
