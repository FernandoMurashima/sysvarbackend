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


class HubEventoRecebido(models.Model):
    STATUS_RECEBIDO = "RECEBIDO"
    STATUS_PROCESSADO = "PROCESSADO"
    STATUS_DUPLICADO = "DUPLICADO"
    STATUS_ERRO = "ERRO"
    STATUS_CONFLITO = "CONFLITO"
    STATUS_CHOICES = [
        (STATUS_RECEBIDO, "Recebido"),
        (STATUS_PROCESSADO, "Processado"),
        (STATUS_DUPLICADO, "Duplicado"),
        (STATUS_ERRO, "Erro"),
        (STATUS_CONFLITO, "Conflito"),
    ]

    hub = models.ForeignKey(SysvarHub, on_delete=models.PROTECT, related_name="eventos_recebidos")
    evento_uuid = models.UUIDField()
    chave_idempotencia = models.CharField(max_length=120)
    tipo = models.CharField(max_length=40, db_index=True)
    payload_hash = models.CharField(max_length=64)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_RECEBIDO, db_index=True)
    mensagem_erro = models.CharField(max_length=255, blank=True, default="")
    recebido_em = models.DateTimeField(auto_now_add=True)
    processado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-recebido_em", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["hub", "evento_uuid"], name="uq_hub_evento_uuid"),
            models.UniqueConstraint(fields=["hub", "chave_idempotencia"], name="uq_hub_chave_idempotencia"),
        ]
        indexes = [
            models.Index(fields=["hub", "tipo"], name="ix_hub_evt_hub_tipo"),
            models.Index(fields=["hub", "status"], name="ix_hub_evt_hub_status"),
        ]


class HubClienteMapeamento(models.Model):
    hub = models.ForeignKey(SysvarHub, on_delete=models.PROTECT, related_name="clientes_mapeados")
    cliente_uuid = models.UUIDField()
    cliente = models.ForeignKey("cadastros.Cliente", on_delete=models.PROTECT, related_name="mapeamentos_hub")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["hub", "cliente_uuid"], name="uq_hub_cliente_uuid"),
        ]


class HubVendaMapeamento(models.Model):
    hub = models.ForeignKey(SysvarHub, on_delete=models.PROTECT, related_name="vendas_mapeadas")
    venda_uuid = models.UUIDField()
    venda = models.ForeignKey("fiscal.VendaPdv", on_delete=models.PROTECT, related_name="mapeamentos_hub")
    documento = models.CharField(max_length=50, db_index=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["hub", "venda_uuid"], name="uq_hub_venda_uuid"),
        ]


class HubNFCeMapeamento(models.Model):
    hub = models.ForeignKey(SysvarHub, on_delete=models.PROTECT, related_name="nfces_mapeadas")
    nfce_uuid = models.UUIDField()
    nfce = models.ForeignKey("fiscal.NFCe", on_delete=models.PROTECT, related_name="mapeamentos_hub")
    venda_uuid = models.UUIDField()
    ultima_versao_evento = models.PositiveIntegerField()
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["hub", "nfce_uuid"], name="uq_hub_nfce_uuid"),
            models.UniqueConstraint(fields=["hub", "nfce"], name="uq_hub_nfce_central"),
        ]


class HubMovimentoCaixaRecebido(models.Model):
    hub = models.ForeignKey(SysvarHub, on_delete=models.PROTECT, related_name="movimentos_caixa_recebidos")
    movimento_uuid = models.UUIDField()
    tipo = models.CharField(max_length=20, db_index=True)
    caixa = models.ForeignKey("financeiro.Caixa", on_delete=models.PROTECT, null=True, blank=True, related_name="movimentos_hub")
    operador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="movimentos_caixa_hub")
    terminal = models.CharField(max_length=80, blank=True, default="")
    valor = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    historico = models.CharField(max_length=255, blank=True, default="")
    documento = models.CharField(max_length=80, blank=True, default="")
    tipo_despesa = models.CharField(max_length=80, blank=True, default="")
    ocorrido_em = models.DateTimeField(null=True, blank=True)
    snapshot = models.JSONField(default=dict, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["hub", "movimento_uuid"], name="uq_hub_mov_caixa_uuid"),
        ]


class HubSessaoCaixaRecebida(models.Model):
    hub = models.ForeignKey(SysvarHub, on_delete=models.PROTECT, related_name="sessoes_caixa_recebidas")
    sessao_uuid = models.UUIDField()
    caixa = models.ForeignKey("financeiro.Caixa", on_delete=models.PROTECT, null=True, blank=True, related_name="sessoes_hub")
    operador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="sessoes_caixa_hub")
    terminal = models.CharField(max_length=80, blank=True, default="")
    aberto_em = models.DateTimeField(null=True, blank=True)
    fechado_em = models.DateTimeField(null=True, blank=True)
    valor_abertura = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    valor_esperado = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    valor_contado = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    diferenca = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    situacao = models.CharField(max_length=40, blank=True, default="")
    observacao = models.CharField(max_length=255, blank=True, default="")
    snapshot = models.JSONField(default=dict, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["hub", "sessao_uuid"], name="uq_hub_sessao_uuid"),
        ]


class HubFechamentoDiaRecebido(models.Model):
    hub = models.ForeignKey(SysvarHub, on_delete=models.PROTECT, related_name="fechamentos_dia_recebidos")
    fechamento_uuid = models.UUIDField()
    data_operacional = models.DateField()
    total_sistema = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_conferido = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    diferenca = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    situacao = models.CharField(max_length=40, blank=True, default="")
    formas_pagamento = models.JSONField(default=list, blank=True)
    operador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="fechamentos_dia_hub")
    terminal = models.CharField(max_length=80, blank=True, default="")
    snapshot = models.JSONField(default=dict, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["hub", "fechamento_uuid"], name="uq_hub_fech_dia_uuid"),
        ]
