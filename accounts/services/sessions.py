import hashlib
import secrets

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.signals import user_login_failed
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied, ValidationError

from accounts.models import SessaoUsuario, SessionToken
from accounts.services.effective_access import CompanyModuleService, EffectiveAccessService, audit_event
from auditoria.models import AuditAction, AuditCategory, AuditResult, AuditSeverity
from auditoria.services import AuditService
from cadastros.models import EmpresaContrato


LIMIT_CODE = "CONCURRENT_SESSION_LIMIT_REACHED"
CONTRACT_SUSPENDED_CODE = "CONTRACT_SUSPENDED"
CONTRACT_SUSPENDED_MESSAGE = "O acesso da empresa está temporariamente suspenso. Entre em contato com o suporte."


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(40)


def timeout_cutoff():
    return timezone.now() - timezone.timedelta(minutes=getattr(settings, "SESSION_IDLE_TIMEOUT_MINUTES", 30))


class ConcurrentSessionService:
    @staticmethod
    def active_sessions_qs(empresa):
        return SessaoUsuario.objects.filter(empresa=empresa, ativa=True, ultima_atividade_em__gte=timeout_cutoff())

    @staticmethod
    def close_expired(empresa=None):
        qs = SessaoUsuario.objects.filter(ativa=True, ultima_atividade_em__lt=timeout_cutoff())
        if empresa is not None:
            qs = qs.filter(empresa=empresa)
        count = 0
        for sessao in qs.select_related("session_token"):
            ConcurrentSessionService.close_session(sessao, "TIMEOUT")
            count += 1
        return count

    @staticmethod
    def close_session(sessao, motivo, actor=None, request=None, audit=True):
        if not sessao.ativa:
            return sessao
        sessao.ativa = False
        sessao.encerrada_em = timezone.now()
        sessao.motivo_encerramento = motivo
        sessao.save(update_fields=["ativa", "encerrada_em", "motivo_encerramento"])
        SessionToken.objects.filter(session=sessao, revoked_at__isnull=True).update(revoked_at=timezone.now())
        action = AuditAction.USER_LOGOUT if motivo == "LOGOUT" else AuditAction.SESSION_TIMEOUT if motivo == "TIMEOUT" else AuditAction.SESSION_REPLACED if motivo == "REPLACED" else AuditAction.SESSION_CLOSED
        if audit:
            AuditService.security(
                action=action,
                result=AuditResult.SUCCESS,
                severity=AuditSeverity.INFO,
                request=request,
                user=actor or sessao.usuario,
                session=sessao,
                empresa=sessao.empresa,
                loja=sessao.loja,
                app_label="accounts",
                model="sessao_usuario",
                object_id=sessao.pk,
                metadata={"motivo": motivo},
                status_code=200 if motivo != "TIMEOUT" else 401,
            )
        return sessao

    @staticmethod
    def login(request, username, password, dispositivo_id, loja=None):
        user = authenticate(request=request, username=username, password=password)
        if not user:
            user_login_failed.send(sender=ConcurrentSessionService, credentials={"username": username}, request=request)
            raise AuthenticationFailed("Usuário ou senha inválidos.")
        if user.is_superuser:
            raw = new_token()
            now = timezone.now()
            sessao = SessaoUsuario.objects.create(
                empresa_id=None,
                usuario=user,
                loja=None,
                token_key_hash=token_hash(raw),
                dispositivo_id=dispositivo_id or "platform",
                ip=ConcurrentSessionService.client_ip(request),
                user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:512],
                ultima_atividade_em=now,
            )
            SessionToken.objects.create(key_hash=token_hash(raw), session=sessao)
            transaction.on_commit(lambda: AuditService.security(
                action=AuditAction.USER_LOGIN,
                result=AuditResult.SUCCESS,
                severity=AuditSeverity.INFO,
                request=request,
                user=user,
                session=sessao,
                app_label="accounts",
                model="sessao_usuario",
                object_id=sessao.pk,
                metadata={"superuser": True},
                status_code=200,
            ))
            return raw, sessao, user
        if not user.is_active:
            raise AuthenticationFailed("Usuário inativo.")
        if not user.empresa_id:
            raise AuthenticationFailed("Usuário sem empresa vinculada.")
        if not dispositivo_id:
            raise ValidationError({"device_id": "Identificador do dispositivo é obrigatório."})
        state = CompanyModuleService(user.empresa).contract_state()
        if not state.active:
            if state.reason == CONTRACT_SUSPENDED_CODE:
                exc = AuthenticationFailed(CONTRACT_SUSPENDED_MESSAGE)
                exc.detail = {"detail": CONTRACT_SUSPENDED_MESSAGE, "code": CONTRACT_SUSPENDED_CODE}
                raise exc
            raise AuthenticationFailed(state.reason or "Contrato indisponível.")
        if not EffectiveAccessService(user).is_company_master() and not getattr(user, "perfil_principal_id", None):
            raise AuthenticationFailed("Usuário sem perfil de acesso.")

        with transaction.atomic():
            contrato = EmpresaContrato.objects.select_for_update().get(empresa=user.empresa)
            ConcurrentSessionService.close_expired(user.empresa)
            existing = SessaoUsuario.objects.select_for_update().filter(
                empresa=user.empresa,
                usuario=user,
                dispositivo_id=dispositivo_id,
                ativa=True,
            ).first()
            if existing:
                ConcurrentSessionService.close_session(existing, "REPLACED", user, request)
            active_count = ConcurrentSessionService.active_sessions_qs(user.empresa).select_for_update().count()
            limit = int(contrato.limite_sessoes_simultaneas or 0)
            if active_count >= limit:
                AuditService.security(
                    action=AuditAction.SESSION_LIMIT_REACHED,
                    result=AuditResult.DENIED,
                    severity=AuditSeverity.WARNING,
                    request=request,
                    user=user,
                    empresa=user.empresa,
                    app_label="accounts",
                    model="empresa",
                    object_id=user.empresa_id,
                    metadata={"limite": limit, "sessoes_ativas": active_count, "username": username},
                    status_code=403,
                )
                exc = PermissionDenied("O limite de acessos simultâneos da empresa foi atingido.")
                exc.detail = {
                    "detail": "O limite de acessos simultâneos da empresa foi atingido.",
                    "code": LIMIT_CODE,
                    "limite_sessoes_simultaneas": limit,
                    "sessoes_ativas": active_count,
                }
                raise exc
            raw = new_token()
            sessao = SessaoUsuario.objects.create(
                empresa=user.empresa,
                usuario=user,
                loja=loja,
                token_key_hash=token_hash(raw),
                dispositivo_id=dispositivo_id,
                ip=ConcurrentSessionService.client_ip(request),
                user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:512],
                ultima_atividade_em=timezone.now(),
            )
            SessionToken.objects.create(key_hash=token_hash(raw), session=sessao)
            transaction.on_commit(lambda: AuditService.security(
                action=AuditAction.USER_LOGIN,
                result=AuditResult.SUCCESS,
                severity=AuditSeverity.INFO,
                request=request,
                user=user,
                session=sessao,
                empresa=user.empresa,
                loja=loja,
                app_label="accounts",
                model="sessao_usuario",
                object_id=sessao.pk,
                status_code=200,
            ))
            return raw, sessao, user

    @staticmethod
    def touch(sessao, force=False):
        now = timezone.now()
        if force or now - sessao.ultima_atividade_em >= timezone.timedelta(minutes=2):
            sessao.ultima_atividade_em = now
            sessao.save(update_fields=["ultima_atividade_em"])
        return sessao

    @staticmethod
    def validate_session_token(raw_token):
        h = token_hash(raw_token)
        token = SessionToken.objects.select_related("session", "session__usuario", "session__empresa").filter(
            key_hash=h,
            revoked_at__isnull=True,
        ).first()
        if not token:
            raise AuthenticationFailed("Token inválido ou sessão encerrada.")
        sessao = token.session
        if not sessao.ativa:
            raise AuthenticationFailed("Sessão encerrada.")
        if sessao.ultima_atividade_em < timeout_cutoff():
            ConcurrentSessionService.close_session(sessao, "TIMEOUT")
            raise AuthenticationFailed("Sessão expirada.")
        user = sessao.usuario
        if not user.is_active:
            ConcurrentSessionService.close_session(sessao, "USER_INACTIVE")
            raise AuthenticationFailed("Usuário inativo.")
        if not user.is_superuser:
            state = CompanyModuleService(user.empresa).contract_state()
            if not state.active:
                if state.reason == CONTRACT_SUSPENDED_CODE:
                    exc = AuthenticationFailed(CONTRACT_SUSPENDED_MESSAGE)
                    exc.detail = {"detail": CONTRACT_SUSPENDED_MESSAGE, "code": CONTRACT_SUSPENDED_CODE}
                    raise exc
                raise AuthenticationFailed(state.reason or "Contrato indisponível.")
        ConcurrentSessionService.touch(sessao)
        return user, token, sessao

    @staticmethod
    def client_ip(request):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        return (forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR") or None)
