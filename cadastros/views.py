from rest_framework import viewsets, permissions, filters
from rest_framework.exceptions import ValidationError
from django_filters.rest_framework import DjangoFilterBackend
from accounts.permissions import HasModuleRole

from .models import Empresa, Loja, Cliente, Fornecedor, Funcionarios, Nat_Lancamento
from .serializers import (
    EmpresaSerializer,
    LojaSerializer,
    ClienteSerializer,
    FornecedorSerializer,
    FuncionariosSerializer,
    NatLancamentoSerializer,
)


class BaseCadastroViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModuleRole]
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa", "Vendedor", "AssistenteReceber", "AssistentePagar", "Auxiliar", "Assistente", "Regular"]
    write_roles = ["Admin", "Diretor", "Gerente"]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    # Em cada ViewSet definimos filterset_fields, search_fields, ordering_fields e ordering

    def get_queryset(self):
        qs = super().get_queryset()
        model = getattr(qs, "model", None)
        user = self.request.user
        if not model or user.is_superuser or user.is_staff:
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
        model = serializer.Meta.model
        user = self.request.user
        if self._model_has_field(model, "empresa") and not getattr(user, "empresa_id", None) and not (user.is_superuser or user.is_staff):
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        if self._model_has_field(model, "empresa") and getattr(user, "empresa_id", None):
            empresa = serializer.validated_data.get("empresa")
            if empresa and empresa.id != user.empresa_id:
                raise ValidationError({"empresa": "O cadastro pertence a outra empresa."})
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
        if user.is_superuser or user.is_staff:
            return qs
        empresa_id = getattr(user, "empresa_id", None)
        if empresa_id:
            return qs.filter(pk=empresa_id)
        return qs.none()


class LojaViewSet(BaseCadastroViewSet):
    queryset = Loja.objects.select_related("empresa").all()
    serializer_class = LojaSerializer

    filterset_fields = ["ativo", "empresa", "estado", "cidade", "cnpj"]
    search_fields = ["nome_loja", "apelido_loja", "cnpj", "cidade", "email", "telefone1", "telefone2"]
    ordering_fields = ["nome_loja", "cidade", "estado", "data_cadastro"]
    ordering = ["nome_loja"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_superuser or user.is_staff:
            empresa = self.request.query_params.get("empresa")
            return qs.filter(empresa_id=empresa) if empresa else qs
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

class NatLancamentoViewSet(viewsets.ModelViewSet):
    queryset = Nat_Lancamento.objects.all().order_by("codigo")
    serializer_class = NatLancamentoSerializer
    permission_classes = [HasModuleRole]
    read_roles = ["Admin", "Diretor", "Gerente", "AssistenteReceber", "AssistentePagar"]
    write_roles = ["Admin", "Diretor"]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        "codigo", "categoria_principal", "subcategoria",
        "descricao", "tipo", "status", "tipo_natureza",
    ]
    ordering_fields = [
        "codigo", "categoria_principal", "subcategoria",
        "tipo", "status", "tipo_natureza", "idnatureza",
    ]
    ordering = ["codigo"]
