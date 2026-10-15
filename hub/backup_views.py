import logging

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.utils.decorators import method_decorator
from rest_framework import permissions
from rest_framework.parsers import BaseParser
from rest_framework.response import Response
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView
from django.conf import settings

from .authentication import HubTokenAuthentication
from .backup_files import BackupIOError
from .backup_manifest import json_sem_duplicatas
from . import backup_uploads

logger = logging.getLogger(__name__)


class BackupJSONParser(BaseParser):
    media_type = "application/json"

    def parse(self, stream, media_type=None, parser_context=None):
        limit = min(settings.HUB_BACKUP_MAX_MANIFEST_BYTES, 1024**2) * 2 + 4096
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise backup_uploads.RecebimentoErro("REQUEST_TOO_LARGE", 413)
        try:
            data = json_sem_duplicatas(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError
            return data
        except (ValueError, UnicodeError, TypeError, RecursionError):
            raise backup_uploads.RecebimentoErro("INVALID_JSON", 400) from None


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class BackupBaseView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [BackupJSONParser]
    renderer_classes = [JSONRenderer]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    def get_authenticate_header(self, request):
        return "Hub"

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not request.is_secure():
            raise backup_uploads.RecebimentoErro("HTTPS_REQUIRED", 403)
        # Antes de ler payloads e consultar tabelas novas: implantacao explicita.
        backup_uploads.configuracao_recebimento()

    def handle_exception(self, exc):
        if isinstance(exc, BackupIOError):
            return Response({"code": exc.code, "detail": "Recebimento nao concluido; consultar progresso e repetir ou acionar suporte."}, status=exc.status)
        if isinstance(exc, ValidationError):
            return Response({"code": "MANIFEST_INVALID", "detail": "Manifesto ou identidade do backup divergente."}, status=400)
        if isinstance(exc, ImproperlyConfigured):
            return Response({"code": "RECEIVING_UNAVAILABLE", "detail": "Recebimento de backup indisponivel; configuracao do suporte necessaria."}, status=503)
        if isinstance(exc, (TimeoutError, ConnectionError)):
            return Response({"code": "REQUEST_TIMEOUT", "detail": "Requisicao interrompida; repetir a mesma operacao."}, status=408)
        if isinstance(exc, OSError):
            return Response({"code": "STORAGE_UNAVAILABLE", "detail": "Armazenamento temporariamente indisponivel."}, status=503)
        from rest_framework.exceptions import APIException
        if isinstance(exc, APIException):
            return super().handle_exception(exc)
        logger.error("Hub backup request failed code=INTERNAL_ERROR")
        return Response({"code": "RECEIVING_UNAVAILABLE", "detail": "Recebimento indisponivel; acionar suporte."}, status=503)


class BackupIniciarView(BackupBaseView):
    def post(self, request):
        if set(request.data) != {"manifesto"}:
            raise backup_uploads.RecebimentoErro("MANIFEST_REQUIRED", 400)
        return Response(backup_uploads.iniciar(request.sysvar_hub, request.data["manifesto"]))


class BackupProgressoView(BackupBaseView):
    def get(self, request, backup_uuid):
        return Response(backup_uploads.progresso(request.sysvar_hub, backup_uuid))


class BackupParteView(BackupBaseView):
    parser_classes = []

    def put(self, request, backup_uuid, indice):
        if request.content_type != "application/octet-stream":
            raise backup_uploads.RecebimentoErro("OCTET_STREAM_REQUIRED", 415)
        try:
            offset = int(request.headers.get("X-Upload-Offset", ""))
            length = int(request.META.get("CONTENT_LENGTH", ""))
        except (ValueError, TypeError):
            raise backup_uploads.RecebimentoErro("PART_HEADERS_REQUIRED", 400) from None
        return Response(backup_uploads.enviar_parte(
            request.sysvar_hub, backup_uuid, request.headers.get("X-Upload-Id"), indice, offset,
            request.headers.get("X-Chunk-SHA256"), request.stream, length,
        ))


class BackupFinalizarView(BackupBaseView):
    def post(self, request, backup_uuid):
        if set(request.data) != {"upload_uuid"}:
            raise backup_uploads.RecebimentoErro("UPLOAD_ID_REQUIRED", 400)
        return Response(backup_uploads.finalizar(request.sysvar_hub, backup_uuid, request.data["upload_uuid"]))
