from rest_framework import serializers

from fiscal.models import Cfop


class CfopSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cfop
        fields = "__all__"

    def validate_codigo(self, value):
        return (value or "").strip()
