from rest_framework import serializers
from django.contrib.auth import get_user_model
from cadastros.models import Empresa, Loja

User = get_user_model()

TIPOS_EXIGEM_LOJA = {
    "Vendedor",
    "Caixa",
    "Gerente",
    "Assistente",
    "AssistenteReceber",
    "AssistentePagar",
}

class LojaMiniSerializer(serializers.ModelSerializer):
    Idloja = serializers.IntegerField(source="id", read_only=True)
    empresa = serializers.IntegerField(source="empresa_id", read_only=True)

    class Meta:
        model = Loja
        fields = ("Idloja", "empresa", "nome_loja", "apelido_loja")


class EmpresaMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Empresa
        fields = ("id", "nome", "nome_fantasia")


class UserSerializer(serializers.ModelSerializer):
    empresa = EmpresaMiniSerializer(read_only=True)
    Idempresa = serializers.PrimaryKeyRelatedField(
        source="empresa", queryset=Empresa.objects.all(), allow_null=True, required=False
    )
    # leitura amigável da loja
    loja = LojaMiniSerializer(read_only=True)
    # gravação por PK
    Idloja = serializers.PrimaryKeyRelatedField(
        source="loja", queryset=Loja.objects.all(), allow_null=True, required=False
    )
    lojas = LojaMiniSerializer(many=True, read_only=True)
    Idlojas = serializers.PrimaryKeyRelatedField(
        source="lojas", queryset=Loja.objects.all(), many=True, required=False
    )
    # permitir criar/alterar senha via API (write-only)
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated or user.is_superuser:
            return
        empresa_id = getattr(user, "empresa_id", None)
        if empresa_id:
            self.fields["Idempresa"].queryset = Empresa.objects.filter(pk=empresa_id)
            self.fields["Idloja"].queryset = Loja.objects.filter(empresa_id=empresa_id)
            self.fields["Idlojas"].queryset = Loja.objects.filter(empresa_id=empresa_id)
        else:
            self.fields["Idempresa"].queryset = Empresa.objects.none()
            self.fields["Idloja"].queryset = Loja.objects.none()
            self.fields["Idlojas"].queryset = Loja.objects.none()

    class Meta:
        model = User
        fields = (
            "id", "username", "email", "first_name", "last_name",
            "type", "Idempresa", "empresa", "Idloja", "loja", "Idlojas", "lojas",
            "is_active", "is_staff", "is_superuser", "date_joined",
            "password",
        )
        read_only_fields = ("id", "is_staff", "is_superuser", "date_joined")

    def validate(self, attrs):
        request = self.context.get("request")
        request_user = getattr(request, "user", None)
        tipo = attrs.get("type", getattr(self.instance, "type", User.Type.REGULAR))
        empresa = attrs.get("empresa", getattr(self.instance, "empresa", None))
        loja = attrs.get("loja", getattr(self.instance, "loja", None))
        lojas = attrs.get("lojas", None)
        if request_user and request_user.is_authenticated and not request_user.is_superuser:
            user_empresa = getattr(request_user, "empresa", None)
            if not user_empresa:
                raise serializers.ValidationError({
                    "Idempresa": "Seu usuário precisa estar vinculado a uma empresa para cadastrar usuários."
                })
            if empresa and empresa.id != user_empresa.id:
                raise serializers.ValidationError({
                    "Idempresa": "Você só pode cadastrar usuários na empresa vinculada ao seu usuário."
                })
            attrs["empresa"] = user_empresa
            empresa = user_empresa
        if not empresa and not getattr(self.instance, "is_superuser", False):
            raise serializers.ValidationError({
                "Idempresa": "Vincule este usuário a uma empresa."
            })
        if tipo in TIPOS_EXIGEM_LOJA and not loja:
            raise serializers.ValidationError({
                "Idloja": "Vincule este usuário a uma filial ou matriz."
            })
        if loja and empresa and loja.empresa_id and loja.empresa_id != empresa.id:
            raise serializers.ValidationError({
                "Idloja": "A loja selecionada pertence a outra empresa."
            })
        if loja and not empresa:
            attrs["empresa"] = loja.empresa
            empresa = loja.empresa
        if lojas is not None:
            if loja and loja not in lojas:
                lojas = list(lojas) + [loja]
                attrs["lojas"] = lojas
            if empresa:
                lojas_fora = [l.nome_loja for l in lojas if l.empresa_id and l.empresa_id != empresa.id]
                if lojas_fora:
                    raise serializers.ValidationError({
                        "Idlojas": "Todas as lojas permitidas devem pertencer à empresa do usuário."
                    })
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        lojas = validated_data.pop("lojas", [])
        user = User(**validated_data)
        if password:
            user.set_password(password)
        else:
            # senha padrão se não for enviada (opcional)
            user.set_password(User.objects.make_random_password())
        user.save()
        if lojas:
            user.lojas.set(lojas)
        elif user.loja_id:
            user.lojas.set([user.loja])
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        lojas = validated_data.pop("lojas", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
        if lojas is not None:
            instance.lojas.set(lojas)
        elif instance.loja_id and not instance.lojas.filter(pk=instance.loja_id).exists():
            instance.lojas.add(instance.loja)
        return instance
