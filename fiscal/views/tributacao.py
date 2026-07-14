from django.db.models import Q
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError

from accounts.permissions import HasModuleRole
from fiscal.models import RegraTributaria, Tributo
from fiscal.serializers import RegraTributariaSerializer, TributoSerializer


class EmpresaScopedFiscalViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModuleRole]
    read_roles = ["Admin", "Diretor", "Gerente", "Auxiliar"]
    write_roles = ["Admin", "Diretor", "Gerente"]

    def _empresa_queryset(self, qs):
        user = self.request.user
        empresa = self.request.query_params.get("empresa")
        if user.is_superuser:
            return qs.filter(empresa_id=empresa) if empresa else qs
        empresa_id = getattr(user, "empresa_id", None)
        return qs.filter(empresa_id=empresa_id) if empresa_id else qs.none()

    def perform_create(self, serializer):
        self._save_empresa(serializer)

    def perform_update(self, serializer):
        self._save_empresa(serializer)

    def _save_empresa(self, serializer):
        user = self.request.user
        if user.is_superuser:
            if not serializer.validated_data.get("empresa"):
                raise ValidationError({"empresa": "Informe a empresa."})
            serializer.save()
            return
        empresa = getattr(user, "empresa", None)
        if not empresa:
            raise ValidationError({"empresa": "Usuário sem empresa vinculada."})
        informada = serializer.validated_data.get("empresa")
        if informada and informada.id != empresa.id:
            raise ValidationError({"empresa": "O cadastro pertence a outra empresa."})
        serializer.save(empresa=empresa)


class TributoViewSet(EmpresaScopedFiscalViewSet):
    serializer_class = TributoSerializer
    queryset = Tributo.objects.all().order_by("codigo")

    def get_queryset(self):
        qs = self._empresa_queryset(super().get_queryset())
        search = (self.request.query_params.get("search") or "").strip()
        ativo = self.request.query_params.get("ativo")
        if search:
            qs = qs.filter(Q(codigo__icontains=search) | Q(descricao__icontains=search))
        if ativo in ("true", "false"):
            qs = qs.filter(ativo=ativo == "true")
        return qs.order_by("codigo")


class RegraTributariaViewSet(EmpresaScopedFiscalViewSet):
    serializer_class = RegraTributariaSerializer
    queryset = RegraTributaria.objects.select_related("tributo", "cfop", "ncm", "empresa").all()

    def get_queryset(self):
        qs = self._empresa_queryset(super().get_queryset())
        search = (self.request.query_params.get("search") or "").strip()
        ativo = self.request.query_params.get("ativo")
        tipo_operacao = (self.request.query_params.get("tipo_operacao") or "").strip()
        tributo = self.request.query_params.get("tributo")
        if search:
            qs = qs.filter(
                Q(nome__icontains=search)
                | Q(tributo__codigo__icontains=search)
                | Q(tributo__descricao__icontains=search)
                | Q(cfop__codigo__icontains=search)
                | Q(ncm__ncm__icontains=search)
            )
        if ativo in ("true", "false"):
            qs = qs.filter(ativo=ativo == "true")
        if tipo_operacao:
            qs = qs.filter(tipo_operacao=tipo_operacao)
        if tributo:
            qs = qs.filter(tributo_id=tributo)
        return qs.order_by("nome", "tributo__codigo")
