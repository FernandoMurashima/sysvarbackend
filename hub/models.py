import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models, transaction
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
    snapshot_operacional = models.JSONField(default=dict, blank=True)
    snapshot_operacional_em = models.DateTimeField(null=True, blank=True)
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


class HubSincronizacaoSolicitacao(models.Model):
    TIPO_COMPLETA = "COMPLETA"
    TIPO_CHOICES = [
        (TIPO_COMPLETA, "Completa"),
    ]

    STATUS_PENDENTE = "PENDENTE"
    STATUS_PROCESSANDO = "PROCESSANDO"
    STATUS_CONCLUIDA = "CONCLUIDA"
    STATUS_ERRO = "ERRO"
    STATUS_CHOICES = [
        (STATUS_PENDENTE, "Pendente"),
        (STATUS_PROCESSANDO, "Processando"),
        (STATUS_CONCLUIDA, "Concluída"),
        (STATUS_ERRO, "Erro"),
    ]
    STATUS_ATIVOS = (STATUS_PENDENTE, STATUS_PROCESSANDO)
    STATUS_TERMINAIS = (STATUS_CONCLUIDA, STATUS_ERRO)

    hub = models.ForeignKey(SysvarHub, on_delete=models.PROTECT, related_name="sincronizacoes")
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, default=TIPO_COMPLETA)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDENTE, db_index=True)
    solicitado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    solicitado_em = models.DateTimeField(auto_now_add=True)
    iniciado_em = models.DateTimeField(null=True, blank=True)
    concluido_em = models.DateTimeField(null=True, blank=True)
    etapa_atual = models.CharField(max_length=80, blank=True, default="")
    mensagem_erro = models.TextField(blank=True, default="")
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-solicitado_em", "-id"]
        indexes = [
            models.Index(fields=["hub", "status"], name="ix_hub_sync_hub_status"),
            models.Index(fields=["status", "solicitado_em"], name="ix_hub_sync_status_sol"),
        ]

    def __str__(self) -> str:
        return f"{self.hub_id} - {self.tipo} - {self.status}"


class HubComandoAdministrativo(models.Model):
    TIPO_CONFIGURAR_TERMINAL = "CONFIGURAR_TERMINAL"
    TIPO_GERAR_PAREAMENTO = "GERAR_PAREAMENTO"
    TIPO_CHOICES = [
        (TIPO_CONFIGURAR_TERMINAL, "Configurar terminal"),
        (TIPO_GERAR_PAREAMENTO, "Gerar pareamento"),
    ]

    STATUS_PENDENTE = "PENDENTE"
    STATUS_PROCESSANDO = "PROCESSANDO"
    STATUS_CONCLUIDO = "CONCLUIDO"
    STATUS_ERRO = "ERRO"
    STATUS_CHOICES = [
        (STATUS_PENDENTE, "Pendente"),
        (STATUS_PROCESSANDO, "Processando"),
        (STATUS_CONCLUIDO, "Concluído"),
        (STATUS_ERRO, "Erro"),
    ]
    STATUS_ATIVOS = (STATUS_PENDENTE, STATUS_PROCESSANDO)
    STATUS_TERMINAIS = (STATUS_CONCLUIDO, STATUS_ERRO)

    hub = models.ForeignKey(SysvarHub, on_delete=models.PROTECT, related_name="comandos_administrativos")
    tipo = models.CharField(max_length=40, choices=TIPO_CHOICES, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    resultado = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDENTE, db_index=True)
    mensagem_erro = models.CharField(max_length=500, blank=True, default="")
    solicitado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    solicitado_em = models.DateTimeField(auto_now_add=True)
    iniciado_em = models.DateTimeField(null=True, blank=True)
    concluido_em = models.DateTimeField(null=True, blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["solicitado_em", "id"]
        indexes = [
            models.Index(fields=["hub", "status", "tipo"], name="ix_hub_cmd_hub_status_tipo"),
            models.Index(fields=["hub", "tipo", "solicitado_em"], name="ix_hub_cmd_hub_tipo_sol"),
        ]

    def __str__(self) -> str:
        return f"{self.hub_id} - {self.tipo} - {self.status}"


class AtivacaoSysvarHub(models.Model):
    ESTADO_PENDENTE = "PENDENTE"
    ESTADO_UTILIZADA = "UTILIZADA"
    ESTADO_EXPIRADA = "EXPIRADA"
    ESTADO_REVOGADA = "REVOGADA"

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

    def estado_administrativo(self, agora=None) -> str:
        agora = agora or timezone.now()
        if self.usado_em is not None:
            return self.ESTADO_UTILIZADA
        if self.revogado_em is not None:
            return self.ESTADO_REVOGADA
        if self.expira_em <= agora:
            return self.ESTADO_EXPIRADA
        return self.ESTADO_PENDENTE


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


class HubVendaFaixaNumeracao(models.Model):
    hub = models.ForeignKey(SysvarHub, on_delete=models.PROTECT, related_name="faixas_numeracao_venda")
    inicio = models.PositiveIntegerField()
    fim = models.PositiveIntegerField()
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["hub_id", "inicio"]
        constraints = [
            models.UniqueConstraint(fields=["hub", "inicio", "fim"], name="uq_hub_venda_faixa_intervalo"),
            models.CheckConstraint(check=models.Q(inicio__lte=models.F("fim")), name="ck_hub_venda_faixa_ordem"),
        ]
        indexes = [
            models.Index(fields=["hub", "inicio", "fim"], name="ix_hub_venda_faixa_hub_int"),
        ]

    def __str__(self) -> str:
        return f"{self.hub_id} - {self.inicio}-{self.fim}"


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


class HubDevolucaoMapeamento(models.Model):
    hub = models.ForeignKey(SysvarHub, on_delete=models.PROTECT, related_name="devolucoes_mapeadas")
    devolucao_uuid = models.UUIDField()
    devolucao = models.ForeignKey("fiscal.VendaDevolucao", on_delete=models.PROTECT, related_name="mapeamentos_hub")
    venda_uuid = models.UUIDField()
    documento = models.CharField(max_length=50, db_index=True)
    vale_documento = models.CharField(max_length=80, blank=True, default="")
    venda_troca_uuid = models.UUIDField(null=True, blank=True, db_index=True)
    operacao_uuid = models.UUIDField(null=True, blank=True, db_index=True)
    valor_consumido_offline = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["hub", "devolucao_uuid"], name="uq_hub_devolucao_uuid"),
            models.UniqueConstraint(fields=["hub", "devolucao"], name="uq_hub_devolucao_central"),
        ]


class HubDevolucaoFaixaNumeracao(models.Model):
    hub = models.ForeignKey(SysvarHub, on_delete=models.PROTECT, related_name="faixas_numeracao_devolucao")
    inicio = models.PositiveIntegerField()
    fim = models.PositiveIntegerField()
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["hub_id", "inicio"]
        constraints = [
            models.UniqueConstraint(fields=["hub", "inicio", "fim"], name="uq_hub_dev_faixa_intervalo"),
            models.CheckConstraint(check=models.Q(inicio__lte=models.F("fim")), name="ck_hub_dev_faixa_ordem"),
        ]
        indexes = [
            models.Index(fields=["hub", "inicio", "fim"], name="ix_hub_dev_faixa_hub_int"),
        ]

    def __str__(self) -> str:
        return f"{self.hub_id} - {self.inicio}-{self.fim}"


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


class HubBackupQuerySet(models.QuerySet):
    def da_empresa(self, empresa):
        return self.filter(empresa_id=empresa.pk, loja__empresa_id=empresa.pk, hub__loja_id=models.F("loja_id"))

    def do_hub(self, hub):
        # Escopo derivado do cadastro persistido, nunca de empresa/loja do payload.
        return self.filter(hub_id=hub.pk, loja_id=models.Subquery(
            SysvarHub.objects.filter(pk=hub.pk).values("loja_id")[:1]
        ), empresa_id=models.F("loja__empresa_id"))

    def update(self, **kwargs):
        raise ValidationError("Backups exigem save() com validacao; atualizacao em lote indisponivel.")

    def bulk_create(self, objs, **kwargs):
        raise ValidationError("Backups exigem registro individual com validacao de cadeia.")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValidationError("Backups exigem save() com validacao; atualizacao em lote indisponivel.")


class HubBackup(models.Model):
    """Somente metadados. Conteudo SQL/binlog nunca e armazenado neste model."""

    FULL = "FULL"
    INCREMENTAL = "INCREMENTAL"
    PENDENTE = "PENDENTE"
    RECEBIDO = "RECEBIDO"
    FALHA = "FALHA"
    VALIDO = "VALIDO"
    INVALIDO = "INVALIDO"
    CADEIA_INTERROMPIDA = "CADEIA_INTERROMPIDA"
    FORMATO_FULL = "MYSQLDUMP_SQL_V1"
    FORMATO_INCREMENTAL = "MYSQLBINLOG_SQL_BOUNDED_V1"
    SHA256 = RegexValidator(r"\A[a-f0-9]{64}\Z", "Informe um SHA-256 hexadecimal de 64 caracteres.")
    BINLOG = RegexValidator(r"\A[A-Za-z0-9_.-]+\.[0-9]{6,}\Z", "Nome de binlog invalido.")
    METADADOS = (
        "hub_id", "empresa_id", "loja_id", "instalacao_uuid", "servidor_mysql_uuid", "backup_uuid",
        "tipo", "full_referencia_id", "sequencia", "predecessor_id", "predecessor_sha256",
        "gerado_em", "binlog_inicio_arquivo", "binlog_inicio_posicao", "binlog_fim_arquivo",
        "binlog_fim_posicao", "tamanho_bytes", "sha256", "manifesto_sha256", "formato",
        "manifesto_versao", "compressao", "chave_armazenamento",
    )

    hub = models.ForeignKey(SysvarHub, on_delete=models.PROTECT, related_name="backups")
    empresa = models.ForeignKey("cadastros.Empresa", on_delete=models.PROTECT, related_name="backups_hub", editable=False)
    loja = models.ForeignKey("cadastros.Loja", on_delete=models.PROTECT, related_name="backups_hub", editable=False)
    instalacao_uuid = models.UUIDField()
    servidor_mysql_uuid = models.UUIDField()
    backup_uuid = models.UUIDField(unique=True)
    tipo = models.CharField(max_length=11, choices=[(FULL, "Full"), (INCREMENTAL, "Incremental")])
    full_referencia = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="incrementais")
    sequencia = models.PositiveIntegerField(default=0)
    predecessor = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="sucessores")
    predecessor_sha256 = models.CharField(max_length=64, blank=True, default="", validators=[SHA256])
    gerado_em = models.DateTimeField()
    recebido_em = models.DateTimeField(null=True, blank=True)
    verificado_em = models.DateTimeField(null=True, blank=True)
    binlog_inicio_arquivo = models.CharField(max_length=255, validators=[BINLOG])
    binlog_inicio_posicao = models.PositiveBigIntegerField(validators=[MinValueValidator(4)])
    # FULL existente registra inicio do snapshot; nao inventar um fim ausente no manifesto.
    binlog_fim_arquivo = models.CharField(max_length=255, blank=True, default="", validators=[BINLOG])
    binlog_fim_posicao = models.PositiveBigIntegerField(null=True, blank=True, validators=[MinValueValidator(4)])
    tamanho_bytes = models.PositiveBigIntegerField(validators=[MinValueValidator(1)])
    sha256 = models.CharField(max_length=64, validators=[SHA256])
    manifesto_sha256 = models.CharField(max_length=64, validators=[SHA256])
    formato = models.CharField(max_length=40, choices=[(FORMATO_FULL, "SQL mysqldump"), (FORMATO_INCREMENTAL, "SQL mysqlbinlog delimitado")])
    manifesto_versao = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    compressao = models.CharField(max_length=10, choices=[("gzip", "gzip")], default="gzip")
    estado_recebimento = models.CharField(max_length=10, default=PENDENTE, choices=[(PENDENTE, "Pendente"), (RECEBIDO, "Recebido"), (FALHA, "Falha")])
    estado_validacao = models.CharField(max_length=20, default=PENDENTE, choices=[(PENDENTE, "Pendente"), (VALIDO, "Valido"), (INVALIDO, "Invalido"), (CADEIA_INTERROMPIDA, "Cadeia interrompida")])
    chave_armazenamento = models.CharField(max_length=255, unique=True, editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    objects = HubBackupQuerySet.as_manager()

    class Meta:
        ordering = ["-gerado_em", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["full_referencia", "sequencia"], name="uq_hub_backup_full_seq"),
            models.CheckConstraint(name="ck_hub_backup_tipo_cadeia", check=(
                models.Q(tipo="FULL", sequencia=0, full_referencia__isnull=True, predecessor__isnull=True, predecessor_sha256="")
                | (models.Q(tipo="INCREMENTAL", sequencia__gte=1, full_referencia__isnull=False, predecessor__isnull=False) & ~models.Q(predecessor_sha256=""))
            )),
            models.CheckConstraint(name="ck_hub_backup_tamanho_pos", check=models.Q(tamanho_bytes__gt=0, binlog_inicio_posicao__gte=4)),
            models.CheckConstraint(name="ck_hub_backup_validado", check=(~models.Q(estado_validacao="VALIDO") | models.Q(estado_recebimento="RECEBIDO", recebido_em__isnull=False, verificado_em__isnull=False))),
        ]
        indexes = [models.Index(fields=["empresa", "loja", "hub", "gerado_em"], name="ix_hub_backup_escopo_data")]

    def gerar_chave_armazenamento(self):
        if self.tipo == self.FULL:
            full_uuid = self.backup_uuid
        else:
            full = type(self).objects.filter(pk=self.full_referencia_id).first()
            if not full:
                raise ValidationError("Incremental exige FULL existente.")
            full_uuid = full.backup_uuid
        return (
            f"empresa-{self.empresa_id}/loja-{self.loja_id}/hub-{self.hub_id}/"
            f"instalacao-{self.instalacao_uuid.hex}/mysql-{self.servidor_mysql_uuid.hex}/"
            f"full-{full_uuid.hex}/{self.tipo.lower()}-{self.backup_uuid.hex}.sql.gz"
        )

    def clean(self):
        super().clean()
        hub = SysvarHub.objects.select_related("loja").filter(pk=self.hub_id).first()
        if not hub or self.loja_id != hub.loja_id or self.empresa_id != hub.loja.empresa_id:
            raise ValidationError("Empresa e loja devem corresponder ao Hub responsavel.")
        if self._state.adding and self.instalacao_uuid != hub.hub_uuid:
            raise ValidationError({"instalacao_uuid": "Instalacao divergente do Hub autenticado."})
        for name in ("backup_uuid", "instalacao_uuid", "servidor_mysql_uuid"):
            if not isinstance(getattr(self, name), uuid.UUID) or getattr(self, name).int == 0:
                raise ValidationError({name: "UUID valido e nao nulo obrigatorio."})
        if self.manifesto_versao != 1:
            raise ValidationError({"manifesto_versao": "Versao do manifesto ainda nao suportada."})
        if self.tipo == self.FULL:
            if self.full_referencia_id or self.predecessor_id or self.sequencia != 0 or self.predecessor_sha256:
                raise ValidationError("FULL nao possui predecessor, referencia FULL ou sequencia incremental.")
            if self.formato != self.FORMATO_FULL or self.binlog_fim_arquivo or self.binlog_fim_posicao is not None:
                raise ValidationError("FULL deve preservar o formato e a coordenada inicial do snapshot.")
        elif self.tipo == self.INCREMENTAL:
            full = type(self).objects.filter(pk=self.full_referencia_id).first()
            previous = type(self).objects.filter(pk=self.predecessor_id).first()
            if not full or not previous or full.tipo != self.FULL or not isinstance(self.sequencia, int) or self.sequencia < 1:
                raise ValidationError("Incremental exige FULL e predecessor existentes.")
            identity = ("hub_id", "empresa_id", "loja_id", "instalacao_uuid", "servidor_mysql_uuid")
            if any(getattr(self, key) != getattr(item, key) for item in (full, previous) for key in identity):
                raise ValidationError("Cadeia nao pode misturar Hubs, lojas, empresas, instalacoes ou servidores MySQL.")
            expected = full if self.sequencia == 1 else previous
            if previous.pk != expected.pk or (self.sequencia > 1 and (previous.tipo != self.INCREMENTAL or previous.full_referencia_id != full.pk or previous.sequencia != self.sequencia - 1)):
                raise ValidationError("Sequencia ou referencia do predecessor inconsistente.")
            if previous.backup_uuid == self.backup_uuid or self.predecessor_sha256 != previous.manifesto_sha256:
                raise ValidationError("Identificador/hash do manifesto predecessor divergente.")
            start = (self.binlog_inicio_arquivo, self.binlog_inicio_posicao)
            previous_end = (previous.binlog_inicio_arquivo, previous.binlog_inicio_posicao) if previous.tipo == self.FULL else (previous.binlog_fim_arquivo, previous.binlog_fim_posicao)
            if start != previous_end:
                raise ValidationError("Coordenada inicial apresenta lacuna ou sobreposicao na cadeia.")
            if self.formato != self.FORMATO_INCREMENTAL or not self.binlog_fim_arquivo or self.binlog_fim_posicao is None:
                raise ValidationError("Incremental exige formato delimitado e coordenada final.")
            try:
                prefix_start, number_start = self.binlog_inicio_arquivo.rsplit(".", 1)
                prefix_end, number_end = self.binlog_fim_arquivo.rsplit(".", 1)
                order_start = (int(number_start), self.binlog_inicio_posicao)
                order_end = (int(number_end), self.binlog_fim_posicao)
                if prefix_start != prefix_end or order_end <= order_start:
                    raise ValueError
            except (ValueError, TypeError):
                raise ValidationError("Intervalo final do incremental invalido.")
            if self.estado_validacao == self.VALIDO and self.situacao_cadeia() != self.VALIDO:
                raise ValidationError("Validacao da cadeia anterior obrigatoria antes de validar o incremental.")
        if self.estado_recebimento == self.RECEBIDO and self.recebido_em is None:
            raise ValidationError({"recebido_em": "Informe quando o arquivo foi recebido."})
        if self.estado_recebimento != self.RECEBIDO and self.recebido_em is not None:
            raise ValidationError("Recebimento pendente/falho nao representa arquivo recebido.")
        if self.estado_validacao in (self.VALIDO, self.INVALIDO) and (self.estado_recebimento != self.RECEBIDO or self.verificado_em is None):
            raise ValidationError("Arquivo recebido e verificacao registrada obrigatorios; recebido nao significa validado.")
        if self.verificado_em and self.recebido_em and self.verificado_em < self.recebido_em:
            raise ValidationError("Verificacao nao pode anteceder o recebimento.")
        if self.chave_armazenamento != self.gerar_chave_armazenamento():
            raise ValidationError({"chave_armazenamento": "Localizacao logica deve ser gerada exclusivamente pela Central."})

    def save(self, *args, **kwargs):
        using = kwargs.get("using") or self._state.db or "default"
        with transaction.atomic(using=using):
            hub = SysvarHub.objects.using(using).select_for_update().select_related("loja").filter(pk=self.hub_id).first()
            if not hub:
                raise ValidationError("Hub responsavel obrigatorio.")
            self.preparar_metadados(hub)
            if not self._state.adding:
                original = type(self).objects.using(using).select_for_update().get(pk=self.pk)
                if any(getattr(self, key) != getattr(original, key) for key in self.METADADOS):
                    raise ValidationError("Identidade e metadados do backup sao imutaveis; retry deve preservar o manifesto.")
            self.full_clean()
            return super().save(*args, **kwargs)

    def preparar_metadados(self, hub):
        if self.empresa_id is None:
            self.empresa_id = hub.loja.empresa_id
        if self.loja_id is None:
            self.loja_id = hub.loja_id
        for name in ("backup_uuid", "instalacao_uuid", "servidor_mysql_uuid"):
            value = self._meta.get_field(name).to_python(getattr(self, name))
            if value is None or value.int == 0:
                raise ValidationError({name: "UUID valido e nao nulo obrigatorio."})
            setattr(self, name, value)
        for name in ("sha256", "manifesto_sha256", "predecessor_sha256"):
            setattr(self, name, str(getattr(self, name) or "").lower())
        if self._state.adding and not self.chave_armazenamento:
            self.chave_armazenamento = self.gerar_chave_armazenamento()


    def situacao_cadeia(self):
        """Diagnostico dos registros, sem presumir validacao fisica de arquivos."""
        current, seen, pending = self, set(), False
        while current:
            if current.backup_uuid in seen or current.estado_validacao in (self.INVALIDO, self.CADEIA_INTERROMPIDA):
                return self.CADEIA_INTERROMPIDA
            seen.add(current.backup_uuid)
            pending = pending or current.estado_validacao != self.VALIDO
            if current.tipo == self.FULL:
                return self.PENDENTE if pending else self.VALIDO
            current = type(self).objects.filter(pk=current.predecessor_id).first()
        return self.CADEIA_INTERROMPIDA


class HubBackupUpload(models.Model):
    """Tentativas preservadas; nenhum conteudo ou chave criptografica em texto puro."""
    backup = models.ForeignKey(HubBackup, on_delete=models.PROTECT, related_name="uploads")
    upload_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    tentativa = models.PositiveIntegerField()
    estado = models.CharField(max_length=12, choices=[("ABERTO", "Aberto"), ("RECEBIDO", "Recebido"), ("ERRO", "Erro")], default="ABERTO")
    chave_id = models.CharField(max_length=40)
    sal = models.CharField(max_length=44)
    criptografia = models.CharField(max_length=30, default="AES256_GCM_CHUNKS_V1", editable=False)
    tamanho_parte = models.PositiveIntegerField()
    manifesto_tamanho = models.PositiveIntegerField()
    bytes_recebidos = models.PositiveBigIntegerField(default=0)
    partes_recebidas = models.PositiveIntegerField(default=0)
    offset_arquivo = models.PositiveBigIntegerField(default=0)
    codigo_erro = models.CharField(max_length=40, blank=True, default="")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    expira_em = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["backup", "tentativa"], name="uq_hub_backup_upload_tentativa")]
        ordering = ["-tentativa"]


class HubBackupParte(models.Model):
    upload = models.ForeignKey(HubBackupUpload, on_delete=models.PROTECT, related_name="partes")
    indice = models.PositiveIntegerField()
    offset = models.PositiveBigIntegerField()
    tamanho = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64, validators=[HubBackup.SHA256])
    offset_arquivo = models.PositiveBigIntegerField()
    fim_arquivo = models.PositiveBigIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["upload", "indice"], name="uq_hub_backup_upload_parte")]
        ordering = ["indice"]
