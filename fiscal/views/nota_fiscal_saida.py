from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from django.core.exceptions import ValidationError as DjangoValidationError

from accounts.permissions import HasModuleRole
from fiscal.models import NotaFiscalSaida, NotaFiscalSaidaItem
from fiscal.serializers import NotaFiscalSaidaItemSerializer, NotaFiscalSaidaSerializer
from distribuicao.services import autorizar_nota_distribuicao


class NotaFiscalSaidaViewSet(viewsets.ModelViewSet):
    serializer_class = NotaFiscalSaidaSerializer
    permission_classes = [HasModuleRole]
    read_roles = ["Admin", "Diretor", "Gerente", "Auxiliar"]
    write_roles = ["Admin", "Diretor", "Gerente"]
    queryset = NotaFiscalSaida.objects.select_related("empresa", "loja_origem", "loja_destino", "ordem_producao").prefetch_related("itens")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        empresa = self.request.query_params.get("empresa")
        loja = self.request.query_params.get("loja")
        status = self.request.query_params.get("status")
        origem = self.request.query_params.get("origem")
        search = (self.request.query_params.get("search") or "").strip()

        if user.is_superuser:
            if empresa:
                qs = qs.filter(empresa_id=empresa)
        else:
            empresa_id = getattr(user, "empresa_id", None)
            qs = qs.filter(empresa_id=empresa_id) if empresa_id else qs.none()

        if loja:
            qs = qs.filter(loja_origem_id=loja)
        if status:
            qs = qs.filter(status=status)
        if origem:
            qs = qs.filter(tipo_operacao=origem)
        if search:
            qs = qs.filter(numero__icontains=search) | qs.filter(documento_origem__icontains=search) | qs.filter(chave_acesso__icontains=search)
        return qs.order_by("-dt_emissao", "-id")

    @action(detail=True, methods=["post"], url_path="autorizar")
    def autorizar(self, request, pk=None):
        try:
            nota = autorizar_nota_distribuicao(self.get_object(), request.user)
            return Response(self.get_serializer(nota).data)
        except DjangoValidationError as exc:
            if hasattr(exc, "message_dict"):
                return Response(exc.message_dict, status=400)
            return Response({"detail": str(exc)}, status=400)


class NotaFiscalSaidaItemViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotaFiscalSaidaItemSerializer
    permission_classes = [HasModuleRole]
    queryset = NotaFiscalSaidaItem.objects.select_related("nota", "produto", "sku")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        nota = self.request.query_params.get("nota")
        if user.is_superuser:
            pass
        else:
            empresa_id = getattr(user, "empresa_id", None)
            qs = qs.filter(nota__empresa_id=empresa_id) if empresa_id else qs.none()
        if nota:
            qs = qs.filter(nota_id=nota)
        return qs.order_by("id")
