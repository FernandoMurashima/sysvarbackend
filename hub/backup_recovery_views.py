from uuid import UUID

from cryptography.exceptions import InvalidTag
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.http import StreamingHttpResponse
from django.utils.cache import patch_vary_headers
from django.utils.decorators import method_decorator
from rest_framework import permissions
from rest_framework.exceptions import APIException
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import CompanyTokenAuthentication
from .backup_files import BackupIOError
from .backup_integrity import IntegrityError
from .backup_views import BackupJSONParser
from .backup_recovery import (RecoveryError, authorize_package, download_file, load_authorization,
                              metadata_chain, recovery_configuration, support_scope)
from .models import HubBackup


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class RecoveryBaseView(APIView):
    authentication_classes = [CompanyTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    renderer_classes = [JSONRenderer]
    parser_classes = [BackupJSONParser]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not request.is_secure():
            raise RecoveryError("HTTPS_REQUIRED", 403)
        recovery_configuration()

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store, max-age=0"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
        response["X-Content-Type-Options"] = "nosniff"
        patch_vary_headers(response, ["Authorization", "X-Backup-Recovery-Token"])
        return response

    def handle_exception(self, exc):
        if isinstance(exc, RecoveryError):
            code, status = exc.code, exc.status
        elif isinstance(exc, ImproperlyConfigured):
            code, status = "RECOVERY_CONFIGURATION_UNAVAILABLE", 503
        elif isinstance(exc, BackupIOError):
            code, status = exc.code, exc.status
        elif isinstance(exc, (IntegrityError, InvalidTag, ValidationError, ValueError)):
            code, status = "RECOVERY_INTEGRITY_OR_CHAIN_INVALID", 409
        elif isinstance(exc, APIException):
            return super().handle_exception(exc)
        else:
            code, status = "RECOVERY_UNAVAILABLE", 503
        return Response({"code": code, "detail": "Recuperacao nao liberada; verificar escopo, integridade e configuracao com suporte."}, status=status)


class RecoveryPointsView(RecoveryBaseView):
    def get(self, request, empresa_id, loja_id):
        user, hub = support_scope(request.user, empresa_id, loja_id)
        scope = HubBackup.objects.do_hub(hub)
        try:
            for key, field in (("instalacao_uuid", "instalacao_uuid"), ("servidor_mysql_uuid", "servidor_mysql_uuid")):
                if request.query_params.get(key):
                    scope = scope.filter(**{field: UUID(request.query_params[key])})
            offset = int(request.query_params.get("offset", "0"))
            limit = int(request.query_params.get("limit", "100"))
            if not 0 <= offset <= 1000000 or not 1 <= limit <= 200:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise RecoveryError("INVALID_POINT_FILTER", 400) from None
        result = []
        for b in scope.order_by("-gerado_em", "-sequencia", "-pk")[offset:offset+limit]:
            try:
                metadata_chain(hub, b.backup_uuid, b.instalacao_uuid, b.servidor_mysql_uuid)
                selectable = True
            except (RecoveryError, IntegrityError):
                selectable = False
            result.append(dict(ponto_uuid=str(b.backup_uuid), instalacao_uuid=str(b.instalacao_uuid),
                servidor_mysql_uuid=str(b.servidor_mysql_uuid), gerado_em=b.gerado_em.isoformat(),
                tipo=b.tipo, sequencia=b.sequencia, estado_recebimento=b.estado_recebimento,
                estado_validacao=b.estado_validacao, selecionavel=selectable, revalidacao_obrigatoria=True))
        return Response(dict(empresa_id=empresa_id, loja_id=loja_id, hub_id=hub.pk, pontos=result,
                             offset=offset, limit=limit, restauracao_homologada=False))


class RecoveryAuthorizeView(RecoveryBaseView):
    def post(self, request, empresa_id, loja_id):
        user, hub = support_scope(request.user, empresa_id, loja_id, write=True)
        return Response(authorize_package(user, hub, request.data, request), status=201)


class RecoveryFileView(RecoveryBaseView):
    def get(self, request, empresa_id, loja_id, backup_uuid):
        user, hub = support_scope(request.user, empresa_id, loja_id, write=True)
        token = request.headers.get("X-Backup-Recovery-Token", "")
        load_authorization(token, user, hub)
        if request.headers.get("Range"):
            raise RecoveryError("RANGE_NOT_SUPPORTED", 416)
        stream, item, package_id = download_file(user, hub, token, backup_uuid, request)
        try:
            response = StreamingHttpResponse(stream, content_type="application/gzip")
            response["Content-Disposition"] = 'attachment; filename="' + item["nome"] + '"'
            response["Content-Length"] = str(item["tamanho"])
            response["X-Backup-SHA256"] = item["sha256"]
            response["X-Recovery-Package-Id"] = package_id
            response["X-Accel-Buffering"] = "no"
            return response
        except Exception:
            stream.close()
            raise
