import uuid

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from cadastros.models import Loja
from financeiro.models import Caixa
from hub.authentication import HubTokenAuthentication
from hub.catalogo import gerar_catalogo_hub
from hub.models import AtivacaoSysvarHub, SysvarHub

ADMIN_CONFIG_ROLES = {"Admin", "Diretor"}


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _texto(data, campo, default="", limite=None):
    valor = str(data.get(campo) or default).strip()
    if limite:
        return valor[:limite]
    return valor


class HubAtivacaoAdminView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _validar_usuario_admin(self, request):
        user = request.user
        if not user.is_superuser and getattr(user, "type", "") not in ADMIN_CONFIG_ROLES:
            raise PermissionDenied("Usuário sem permissão para administrar ativações do Sysvar Hub.")

    def post(self, request):
        self._validar_usuario_admin(request)
        loja_id = request.data.get("loja") or request.data.get("loja_id")
        if not loja_id:
            raise ValidationError({"loja": "Informe a loja."})

        loja = Loja.objects.select_related("empresa").filter(pk=loja_id).first()
        if not loja:
            raise ValidationError({"loja": "Loja inválida."})

        user_empresa_id = getattr(request.user, "empresa_id", None)
        if user_empresa_id and loja.empresa_id != int(user_empresa_id):
            raise PermissionDenied("Loja fora do escopo do usuário.")
        if not user_empresa_id and not request.user.is_superuser:
            raise PermissionDenied("Usuário sem empresa vinculada.")

        ativacao, codigo = AtivacaoSysvarHub.criar(loja=loja, criado_por=request.user)
        return Response(
            {
                "id": ativacao.pk,
                "codigo": codigo,
                "codigo_prefixo": ativacao.codigo_prefixo,
                "expira_em": ativacao.expira_em,
                "loja_id": loja.pk,
                "loja_nome": loja.nome_loja,
                "empresa_id": loja.empresa_id,
                "empresa_nome": loja.empresa.nome,
            },
            status=status.HTTP_201_CREATED,
        )


class HubAtivarView(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "hub_ativacao"

    @transaction.atomic
    def post(self, request):
        codigo = AtivacaoSysvarHub.normalizar_codigo(request.data.get("codigo"))
        hub_uuid_raw = _texto(request.data, "hub_uuid")
        nome = _texto(request.data, "nome", "Sysvar Hub", 120) or "Sysvar Hub"
        hostname = _texto(request.data, "hostname", "", 120)
        versao = _texto(request.data, "versao", "", 40)

        if not codigo:
            raise ValidationError({"codigo": "Código inválido."})
        try:
            hub_uuid = uuid.UUID(hub_uuid_raw)
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValidationError({"hub_uuid": "UUID do Hub inválido."}) from exc

        codigo_hash = AtivacaoSysvarHub.hash_codigo(codigo)
        ativacao = (
            AtivacaoSysvarHub.objects.select_for_update()
            .select_related("loja", "loja__empresa")
            .filter(codigo_hash=codigo_hash)
            .first()
        )
        if not ativacao or not ativacao.esta_utilizavel():
            raise ValidationError({"codigo": "Código inválido."})

        conflito_uuid = SysvarHub.objects.select_for_update().filter(hub_uuid=hub_uuid).exclude(loja=ativacao.loja).first()
        if conflito_uuid:
            raise ValidationError({"hub_uuid": "UUID do Hub já está vinculado a outra loja."})

        hub = SysvarHub.objects.select_for_update().filter(loja=ativacao.loja).first()
        agora = timezone.now()
        if not hub:
            try:
                hub = SysvarHub.objects.create(
                    loja=ativacao.loja,
                    hub_uuid=hub_uuid,
                    nome=nome,
                    hostname=hostname,
                    versao=versao,
                    ultimo_ip=_client_ip(request),
                    ultimo_contato=agora,
                    ativo=True,
                )
            except IntegrityError as exc:
                raise ValidationError({"hub_uuid": "UUID do Hub já está vinculado a outra loja."}) from exc
        else:
            hub.hub_uuid = hub_uuid
            hub.nome = nome
            hub.hostname = hostname
            hub.versao = versao
            hub.ultimo_ip = _client_ip(request)
            hub.ultimo_contato = agora
            hub.ativo = True
            hub.save(update_fields=["hub_uuid", "nome", "hostname", "versao", "ultimo_ip", "ultimo_contato", "ativo", "atualizado_em"])

        token = hub.gerar_token()
        ativacao.usado_em = agora
        ativacao.hub = hub
        ativacao.save(update_fields=["usado_em", "hub"])

        loja = ativacao.loja
        return Response(
            {
                "token": token,
                "hub_uuid": str(hub.hub_uuid),
                "hub_id": hub.pk,
                "loja_id": loja.pk,
                "loja_nome": loja.nome_loja,
                "empresa_id": loja.empresa_id,
                "empresa_nome": loja.empresa.nome,
            },
            status=status.HTTP_200_OK,
        )


class HubHeartbeatView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        hub = request.sysvar_hub
        hub.ultimo_contato = timezone.now()
        hub.hostname = _texto(request.data, "hostname", "", 120)
        hub.versao = _texto(request.data, "versao", "", 40)
        hub.ultimo_ip = _client_ip(request)
        hub.save(update_fields=["ultimo_contato", "hostname", "versao", "ultimo_ip", "atualizado_em"])
        return Response(
            {
                "status": "ok",
                "hub_uuid": str(hub.hub_uuid),
                "loja_id": hub.loja_id,
                "empresa_id": hub.loja.empresa_id,
                "servidor_em": timezone.now(),
            },
            status=status.HTTP_200_OK,
        )


class HubBootstrapView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        hub = request.sysvar_hub
        loja = hub.loja
        empresa = loja.empresa
        caixas = (
            Caixa.objects.filter(idloja=loja, ativo=True, tipo_caixa=Caixa.TIPO_LOJA)
            .order_by("codigo", "Idcaixa")
            .values("Idcaixa", "codigo", "descricao", "ativo")
        )

        return Response(
            {
                "bootstrap_versao": 1,
                "servidor_em": timezone.now(),
                "hub": {
                    "id": hub.pk,
                    "hub_uuid": str(hub.hub_uuid),
                    "versao": hub.versao,
                },
                "empresa": {
                    "id": empresa.pk,
                    "nome": empresa.nome,
                },
                "loja": {
                    "id": loja.pk,
                    "nome_loja": loja.nome_loja,
                    "apelido_loja": loja.apelido_loja,
                    "cnpj": loja.cnpj,
                    "estado": loja.estado,
                },
                "caixas": [
                    {
                        "id": caixa["Idcaixa"],
                        "codigo": caixa["codigo"],
                        "descricao": caixa["descricao"],
                        "ativo": caixa["ativo"],
                    }
                    for caixa in caixas
                ],
            },
            status=status.HTTP_200_OK,
        )


class HubCatalogoView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(gerar_catalogo_hub(request.sysvar_hub), status=status.HTTP_200_OK)
