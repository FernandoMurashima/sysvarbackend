import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import Index
from django.utils import timezone


class SysvarHub(models.Model):
    loja = models.OneToOneField("cadastros.Loja", on_delete=models.PROTECT, related_name="sysvar_hub")
    hub_uuid = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    nome = models.CharField(max_length=120, default="Sysvar Hub")
    token_hash = models.CharField(max_length=64, blank=True, null=True, unique=True, default=None)
    token_prefixo = models.CharField(max_length=12, blank=True, default="")
    ativo = models.BooleanField(default=True, db_index=True)
    ultimo_contato = models.DateTimeField(null=True, blank=True)
    versao = models.CharField(max_length=40, blank=True, default="")
    hostname = models.CharField(max_length=120, blank=True, default="")
    ultimo_ip = models.GenericIPAddressField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["loja_id"]

    def __str__(self) -> str:
        return f"{self.loja_id} - {self.nome}"

    @property
    def is_authenticated(self):
        return True

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()

    def gerar_token(self) -> str:
        token = secrets.token_urlsafe(48)
        self.token_hash = self.hash_token(token)
        self.token_prefixo = token[:12]
        self.save(update_fields=["token_hash", "token_prefixo", "atualizado_em"])
        return token


class AtivacaoSysvarHub(models.Model):
    TEMPO_EXPIRACAO = timedelta(minutes=15)
    ALFABETO_CODIGO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

    loja = models.ForeignKey("cadastros.Loja", on_delete=models.PROTECT, related_name="ativacoes_sysvar_hub", db_index=True)
    codigo_hash = models.CharField(max_length=64, unique=True, db_index=True)
    codigo_prefixo = models.CharField(max_length=4, db_index=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="ativacoes_sysvar_hub")
    criado_em = models.DateTimeField(auto_now_add=True)
    expira_em = models.DateTimeField(db_index=True)
    usado_em = models.DateTimeField(null=True, blank=True)
    hub = models.ForeignKey(SysvarHub, on_delete=models.SET_NULL, null=True, blank=True, related_name="ativacoes")
    revogado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-criado_em"]
        indexes = [
            Index(fields=["loja", "codigo_prefixo"], name="ix_ativ_hub_loja_pref"),
            Index(fields=["expira_em", "usado_em"], name="ix_ativ_hub_exp_uso"),
        ]

    def __str__(self) -> str:
        return f"{self.loja_id} - {self.codigo_prefixo}"

    @staticmethod
    def normalizar_codigo(codigo: str) -> str:
        return str(codigo or "").strip().upper()

    @staticmethod
    def hash_codigo(codigo: str) -> str:
        segredo = str(settings.SECRET_KEY).encode("utf-8")
        normalizado = AtivacaoSysvarHub.normalizar_codigo(codigo).encode("utf-8")
        return hmac.new(segredo, normalizado, hashlib.sha256).hexdigest()

    @classmethod
    def gerar_codigo(cls) -> str:
        bruto = "".join(secrets.choice(cls.ALFABETO_CODIGO) for _ in range(12))
        return "-".join(bruto[i : i + 4] for i in range(0, 12, 4))

    @classmethod
    def criar(cls, *, loja, criado_por):
        codigo = cls.gerar_codigo()
        ativacao = cls.objects.create(
            loja=loja,
            codigo_hash=cls.hash_codigo(codigo),
            codigo_prefixo=codigo[:4],
            criado_por=criado_por,
            expira_em=timezone.now() + cls.TEMPO_EXPIRACAO,
        )
        return ativacao, codigo

    def esta_utilizavel(self, agora=None) -> bool:
        agora = agora or timezone.now()
        return self.usado_em is None and self.revogado_em is None and self.expira_em > agora
