from rest_framework import serializers

from fiscal.models import RegraTributaria, Tributo


class TributoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tributo
        fields = "__all__"

    def validate_codigo(self, value):
        return (value or "").strip().upper()


class RegraTributariaSerializer(serializers.ModelSerializer):
    tributo_codigo = serializers.CharField(source="tributo.codigo", read_only=True)
    tributo_descricao = serializers.CharField(source="tributo.descricao", read_only=True)
    cfop_codigo = serializers.CharField(source="cfop.codigo", read_only=True)
    ncm_codigo = serializers.CharField(source="ncm.ncm", read_only=True)

    class Meta:
        model = RegraTributaria
        fields = "__all__"

    def validate(self, attrs):
        empresa = attrs.get("empresa", getattr(self.instance, "empresa", None))
        for field in ("tributo", "cfop", "ncm"):
            obj = attrs.get(field, getattr(self.instance, field, None))
            if obj and empresa and getattr(obj, "empresa_id", None) and obj.empresa_id != empresa.id:
                raise serializers.ValidationError({field: "O cadastro selecionado pertence a outra empresa."})
        return attrs
