from rest_framework import viewsets, permissions, filters
from django_filters.rest_framework import DjangoFilterBackend
from accounts.permissions import HasModuleRole

from .models import Loja, Cliente, Fornecedor, Funcionarios, Nat_Lancamento
from .serializers import (
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


class LojaViewSet(BaseCadastroViewSet):
    queryset = Loja.objects.all()
    serializer_class = LojaSerializer

    filterset_fields = ["ativo", "estado", "cidade", "cnpj"]
    search_fields = ["nome_loja", "apelido_loja", "cnpj", "cidade", "email", "telefone1", "telefone2"]
    ordering_fields = ["nome_loja", "cidade", "estado", "data_cadastro"]
    ordering = ["nome_loja"]


class ClienteViewSet(BaseCadastroViewSet):
    queryset = Cliente.objects.all()
    serializer_class = ClienteSerializer

    filterset_fields = ["ativo", "estado", "cidade", "categoria", "bloqueio", "mala_direta"]
    search_fields = ["nome_cliente", "apelido", "cpf", "email", "cidade", "telefone1", "telefone2"]
    ordering_fields = ["nome_cliente", "cidade", "estado", "data_cadastro"]
    ordering = ["nome_cliente"]


class FornecedorViewSet(BaseCadastroViewSet):
    queryset = Fornecedor.objects.all()
    serializer_class = FornecedorSerializer

    filterset_fields = ["ativo", "estado", "cidade", "categoria", "bloqueio", "mala_direta", "cnpj"]
    search_fields = ["nome_fornecedor", "apelido", "cnpj", "email", "cidade", "telefone1", "telefone2"]
    ordering_fields = ["nome_fornecedor", "cidade", "estado", "data_cadastro"]
    ordering = ["nome_fornecedor"]


class FuncionariosViewSet(BaseCadastroViewSet):
    # select_related para otimizar a FK de loja
    queryset = Funcionarios.objects.select_related("idloja").all()
    serializer_class = FuncionariosSerializer

    filterset_fields = ["ativo", "categoria", "idloja"]
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
