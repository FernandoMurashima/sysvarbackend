from rest_framework import serializers
from .models import Loja, Cliente, Fornecedor, Funcionarios, Nat_Lancamento
from .validators import (
    cpf_validator,
    cnpj_validator,
    email_simple_validator,
    telefone_br_validator,
    cep_validator,
    only_digits,
)
from typing import Optional

# Helpers de normalização
def _norm_email(v: Optional[str]) -> Optional[str]:
    return (v or "").strip().lower() or None

def _norm_digits(v: Optional[str]) -> Optional[str]:
    d = only_digits(v or "")
    return d or None

# ---------------------------
# Loja
# ---------------------------
class LojaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Loja
        fields = "__all__"

    # Validações field-level com normalização
    def validate_cnpj(self, value):
        if not value:
            return value
        cnpj_validator(value)  # lança erro se inválido
        return _norm_digits(value)

    def validate_email(self, value):
        if not value:
            return value
        email_simple_validator(value)
        return _norm_email(value)

    def validate_telefone1(self, value):
        if not value:
            return value
        telefone_br_validator(value)
        return _norm_digits(value)

    def validate_telefone2(self, value):
        if not value:
            return value
        telefone_br_validator(value)
        return _norm_digits(value)

    def validate_cep(self, value):
        if not value:
            return value
        cep_validator(value)
        return _norm_digits(value)

# ---------------------------
# Cliente
# ---------------------------
class ClienteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cliente
        fields = "__all__"

    def validate_cpf(self, value):
        if not value:
            return value
        # Cliente padrão sem identificação
        if only_digits(value) == "00000000000":
            return "00000000000"
        cpf_validator(value)  # lança erro se inválido
        return _norm_digits(value)

    def validate_email(self, value):
        if not value:
            return value
        email_simple_validator(value)
        return _norm_email(value)

    def validate_telefone1(self, value):
        if not value:
            return value
        telefone_br_validator(value)
        return _norm_digits(value)

    def validate_telefone2(self, value):
        if not value:
            return value
        telefone_br_validator(value)
        return _norm_digits(value)

    def validate_cep(self, value):
        if not value:
            return value
        cep_validator(value)
        return _norm_digits(value)

# ---------------------------
# Fornecedor
# ---------------------------
class FornecedorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Fornecedor
        fields = "__all__"

    def validate_cnpj(self, value):
        if not value:
            return value
        cnpj_validator(value)
        return _norm_digits(value)

    def validate_email(self, value):
        if not value:
            return value
        email_simple_validator(value)
        return _norm_email(value)

    def validate_telefone1(self, value):
        if not value:
            return value
        telefone_br_validator(value)
        return _norm_digits(value)

    def validate_telefone2(self, value):
        if not value:
            return value
        telefone_br_validator(value)
        return _norm_digits(value)

    def validate_cep(self, value):
        if not value:
            return value
        cep_validator(value)
        return _norm_digits(value)

# ---------------------------
# Funcionários
# ---------------------------
class FuncionariosSerializer(serializers.ModelSerializer):
    class Meta:
        model = Funcionarios
        fields = "__all__"

    def validate(self, attrs):
        categoria = (attrs.get("categoria", getattr(self.instance, "categoria", "")) or "").strip().lower()
        loja = attrs.get("idloja", getattr(self.instance, "idloja", None))
        categorias_exigem_loja = {
            "vendedor",
            "caixa",
            "gerente",
            "assistente",
            "assistente receber",
            "assistente pagar",
            "assistentecontasareceber",
            "assistentecontasapagar",
        }
        categoria_normalizada = categoria.replace(" ", "").replace("_", "").replace("-", "")
        if (categoria in categorias_exigem_loja or categoria_normalizada in categorias_exigem_loja) and not loja:
            raise serializers.ValidationError({
                "idloja": "Vincule este funcionário a uma filial ou matriz."
            })
        return attrs

    def validate_cpf(self, value):
        if not value:
            return value
        # Permitimos funcionário sem CPF? Normalmente não; mas se vier, valida.
        if only_digits(value) == "00000000000":
            # Para funcionário, manteremos regra estrita: não aceitar CPF padrão.
            raise serializers.ValidationError("CPF padrão (000.000.000-00) não é permitido para funcionários.")
        cpf_validator(value)
        return _norm_digits(value)

from rest_framework import serializers
from .models import Nat_Lancamento

class NatLancamentoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Nat_Lancamento
        fields = [
            "idnatureza",
            "codigo",
            "categoria_principal",
            "subcategoria",
            "descricao",
            "tipo",
            "status",
            "tipo_natureza",
        ]
