from rest_framework import serializers
from django.contrib.auth import get_user_model
from cadastros.models import Loja

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

    class Meta:
        model = Loja
        fields = ("Idloja", "nome_loja", "apelido_loja")

class UserSerializer(serializers.ModelSerializer):
    # leitura amigável da loja
    loja = LojaMiniSerializer(read_only=True)
    # gravação por PK
    Idloja = serializers.PrimaryKeyRelatedField(
        source="loja", queryset=Loja.objects.all(), allow_null=True, required=False
    )
    # permitir criar/alterar senha via API (write-only)
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = (
            "id", "username", "email", "first_name", "last_name",
            "type", "Idloja", "loja",
            "is_active", "is_staff", "date_joined",
            "password",
        )
        read_only_fields = ("id", "date_joined")

    def validate(self, attrs):
        tipo = attrs.get("type", getattr(self.instance, "type", User.Type.REGULAR))
        loja = attrs.get("loja", getattr(self.instance, "loja", None))
        if tipo in TIPOS_EXIGEM_LOJA and not loja:
            raise serializers.ValidationError({
                "Idloja": "Vincule este usuário a uma filial ou matriz."
            })
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        user = User(**validated_data)
        if password:
            user.set_password(password)
        else:
            # senha padrão se não for enviada (opcional)
            user.set_password(User.objects.make_random_password())
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance
