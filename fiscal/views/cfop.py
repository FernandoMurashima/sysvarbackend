from rest_framework import viewsets
from rest_framework.exceptions import ValidationError

from accounts.permissions import HasModuleRole
from fiscal.models import Cfop
from fiscal.serializers import CfopSerializer


class CfopViewSet(viewsets.ModelViewSet):
    serializer_class = CfopSerializer
    permission_classes = [HasModuleRole]
    read_roles = ["Admin", "Diretor", "Gerente", "Auxiliar"]
    write_roles = ["Admin", "Diretor", "Gerente"]
    queryset = Cfop.objects.all().order_by("codigo")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        empresa = self.request.query_params.get("empresa")
        search = (self.request.query_params.get("search") or "").strip()
        tipo = (self.request.query_params.get("tipo_operacao") or "").strip()
        destino = (self.request.query_params.get("destino") or "").strip()
        ativo = self.request.query_params.get("ativo")

        if user.is_superuser:
            if empresa:
                qs = qs.filter(empresa_id=empresa)
        else:
            empresa_id = getattr(user, "empresa_id", None)
            qs = qs.filter(empresa_id=empresa_id) if empresa_id else qs.none()

        if search:
            qs = qs.filter(codigo__icontains=search) | qs.filter(descricao__icontains=search)
        if tipo:
            qs = qs.filter(tipo_operacao=tipo)
        if destino:
            qs = qs.filter(destino=destino)
        if ativo in ("true", "false"):
            qs = qs.filter(ativo=ativo == "true")
        return qs.order_by("codigo")

    def perform_create(self, serializer):
        self._save_empresa(serializer)

    def perform_update(self, serializer):
        self._save_empresa(serializer)

    def _save_empresa(self, serializer):
        user = self.request.user
        if user.is_superuser:
            if not serializer.validated_data.get("empresa"):
                raise ValidationError({"empresa": "Informe a empresa do CFOP."})
            serializer.save()
            return
        empresa = getattr(user, "empresa", None)
        if not empresa:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        informada = serializer.validated_data.get("empresa")
        if informada and informada.id != empresa.id:
            raise ValidationError({"empresa": "O CFOP pertence a outra empresa."})
        serializer.save(empresa=empresa)
