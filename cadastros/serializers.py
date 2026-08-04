from rest_framework import serializers
from .models import Empresa, Loja, Cliente, Fornecedor, Funcionarios, Nat_Lancamento
from .validators import (
    cpf_validator,
    cnpj_validator,
    email_simple_validator,
    telefone_br_validator,
    cep_validator,
    only_digits,
)
from typing import Optional
from accounts.permissions import has_field_permission

# Helpers de normalização
def _norm_email(v: Optional[str]) -> Optional[str]:
    return (v or "").strip().lower() or None

def _norm_digits(v: Optional[str]) -> Optional[str]:
    d = only_digits(v or "")
    return d or None

class EmpresaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Empresa
        fields = "__all__"

    def validate_documento(self, value):
        if not value:
            return value
        cnpj_validator(value)
        return _norm_digits(value)

    def create(self, validated_data):
        empresa = super().create(validated_data)
        try:
            from accounts.services.effective_access import sync_empresa_modulos_from_legacy_flags

            sync_empresa_modulos_from_legacy_flags(empresa)
        except Exception:
            pass
        return empresa

    def update(self, instance, validated_data):
        empresa = super().update(instance, validated_data)
        try:
            from accounts.services.effective_access import sync_empresa_modulos_from_legacy_flags

            sync_empresa_modulos_from_legacy_flags(empresa)
        except Exception:
            pass
        return empresa


# ---------------------------
# Loja
# ---------------------------
class LojaSerializer(serializers.ModelSerializer):
    empresa_nome = serializers.CharField(source="empresa.nome", read_only=True)

    class Meta:
        model = Loja
        fields = (
            "id",
            "empresa",
            "empresa_nome",
            "nome_loja",
            "apelido_loja",
            "cnpj",
            "logradouro",
            "endereco",
            "numero",
            "complemento",
            "cep",
            "bairro",
            "cidade",
            "estado",
            "telefone1",
            "telefone2",
            "email",
            "EstoqueNegativo",
            "Rede",
            "DataAbertura",
            "ContaContabil",
            "DataEnceramento",
            "Matriz",
            "tipo_unidade",
            "regime_tributario",
            "ambiente_fiscal",
            "inscricao_estadual",
            "serie_nfce",
            "proximo_numero_nfce",
            "serie_nfe",
            "proximo_numero_nfe",
            "emite_nfce",
            "emite_nfe",
            "ativo",
            "data_cadastro",
        )

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
    CATEGORIAS_NORMALIZADAS = {
        "materiaprima": "MATERIA_PRIMA",
        "matériaprima": "MATERIA_PRIMA",
        "aviamento": "AVIAMENTO",
        "revenda": "REVENDA",
        "produtoderevenda": "REVENDA",
        "faccao": "FACCAO",
        "facção": "FACCAO",
        "prestador": "PRESTADOR",
        "prestadordeservico": "PRESTADOR",
        "prestadordeserviço": "PRESTADOR",
        "transportadora": "TRANSPORTADORA",
        "outros": "OUTROS",
    }

    class Meta:
        model = Fornecedor
        fields = "__all__"

    def validate_categoria(self, value):
        if not value:
            return value
        categoria = str(value).strip()
        validas = {codigo for codigo, _ in Fornecedor.CATEGORIA_CHOICES}
        if categoria in validas:
            return categoria
        normalizada = categoria.lower().replace(" ", "").replace("_", "").replace("-", "")
        codigo = self.CATEGORIAS_NORMALIZADAS.get(normalizada)
        if codigo:
            return codigo
        raise serializers.ValidationError("Categoria de fornecedor inválida.")

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
    salario_oculto = serializers.SerializerMethodField()

    class Meta:
        model = Funcionarios
        fields = "__all__"

    def _pode_ver_salario(self):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        return has_field_permission(user, "funcionario.salario", default_roles=["Admin", "Diretor"])

    def get_salario_oculto(self, obj):
        return not self._pode_ver_salario()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self._pode_ver_salario():
            data["salario"] = None
        return data

    def validate(self, attrs):
        if "salario" in attrs and not self._pode_ver_salario():
            raise serializers.ValidationError({
                "salario": "Você não tem permissão para informar ou alterar salário."
            })
        categoria = (attrs.get("categoria", getattr(self.instance, "categoria", "")) or "").strip().lower()
        empresa = attrs.get("empresa", getattr(self.instance, "empresa", None))
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
        if loja and empresa and loja.empresa_id and loja.empresa_id != empresa.id:
            raise serializers.ValidationError({
                "idloja": "A loja selecionada pertence a outra empresa."
            })
        if loja and not empresa:
            attrs["empresa"] = loja.empresa
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
from .models import Nat_Lancamento, PlanoContabil


class PlanoContabilSerializer(serializers.ModelSerializer):
    conta_pai_codigo = serializers.CharField(source="conta_pai.codigo", read_only=True)
    conta_pai_descricao = serializers.CharField(source="conta_pai.descricao", read_only=True)

    class Meta:
        model = PlanoContabil
        fields = [
            "id",
            "empresa",
            "codigo",
            "descricao",
            "classe",
            "natureza",
            "conta_pai",
            "conta_pai_codigo",
            "conta_pai_descricao",
            "nivel",
            "analitica",
            "ativa",
            "data_cadastro",
        ]
        read_only_fields = ["data_cadastro"]

    def validate(self, attrs):
        empresa = attrs.get("empresa", getattr(self.instance, "empresa", None))
        conta_pai = attrs.get("conta_pai", getattr(self.instance, "conta_pai", None))
        if conta_pai and empresa and conta_pai.empresa_id != empresa.id:
            raise serializers.ValidationError({"conta_pai": "A conta pai deve pertencer à mesma empresa."})
        if conta_pai and self.instance and conta_pai.pk == self.instance.pk:
            raise serializers.ValidationError({"conta_pai": "A conta não pode ser pai dela mesma."})
        if conta_pai and not attrs.get("nivel"):
            attrs["nivel"] = int(conta_pai.nivel or 1) + 1
        elif not conta_pai and not attrs.get("nivel"):
            attrs["nivel"] = 1
        return attrs

class NatLancamentoSerializer(serializers.ModelSerializer):
    plano_contabil_codigo = serializers.CharField(source="plano_contabil.codigo", read_only=True)
    plano_contabil_descricao = serializers.CharField(source="plano_contabil.descricao", read_only=True)

    class Meta:
        model = Nat_Lancamento
        fields = [
            "idnatureza",
            "empresa",
            "codigo",
            "categoria_principal",
            "subcategoria",
            "descricao",
            "tipo",
            "status",
            "tipo_natureza",
            "natureza_operacao",
            "categoria_gerencial",
            "movimenta_financeiro",
            "entra_dre",
            "plano_contabil",
            "plano_contabil_codigo",
            "plano_contabil_descricao",
            "conta_contabil",
            "ativo",
        ]

    def validate(self, attrs):
        natureza_operacao = (attrs.get("natureza_operacao", getattr(self.instance, "natureza_operacao", "")) or "").upper()
        tipo_natureza = attrs.get("tipo_natureza", getattr(self.instance, "tipo_natureza", ""))
        if natureza_operacao not in {"RECEITA", "DESPESA", "TRANSFERENCIA", "AJUSTE"}:
            raise serializers.ValidationError({
                "natureza_operacao": "Use RECEITA, DESPESA, TRANSFERENCIA ou AJUSTE."
            })
        if natureza_operacao == "RECEITA" and not tipo_natureza:
            attrs["tipo_natureza"] = "CREDITO"
        if natureza_operacao == "DESPESA" and not tipo_natureza:
            attrs["tipo_natureza"] = "DEBITO"
        if natureza_operacao == "TRANSFERENCIA":
            attrs["entra_dre"] = False
        empresa = attrs.get("empresa", getattr(self.instance, "empresa", None))
        plano = attrs.get("plano_contabil", getattr(self.instance, "plano_contabil", None))
        if plano and empresa and plano.empresa_id != empresa.id:
            raise serializers.ValidationError({
                "plano_contabil": "A conta contábil deve pertencer à mesma empresa da natureza."
            })
        if plano:
            attrs["conta_contabil"] = plano.codigo
        return attrs
