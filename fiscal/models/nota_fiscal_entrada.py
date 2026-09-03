import hashlib
import secrets
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import models
from django.db.models import Q, UniqueConstraint, Index


def _money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _fmt_money(value):
    return f"R$ {_money(value):.2f}".replace(".", ",")


def _fmt_qty(value):
    text = f"{Decimal(value or 0).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP):f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") or "0"


class AgenteLocalSysvar(models.Model):
    empresa = models.ForeignKey("cadastros.Empresa", on_delete=models.PROTECT, related_name="agentes_locais_sysvar", db_index=True)
    identificador = models.CharField(max_length=120)
    nome = models.CharField(max_length=120)
    token_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)
    token_prefixo = models.CharField(max_length=12, blank=True, default="")
    ativo = models.BooleanField(default=True, db_index=True)
    ultimo_contato = models.DateTimeField(null=True, blank=True)
    versao = models.CharField(max_length=40, blank=True, default="")
    hostname = models.CharField(max_length=120, blank=True, default="")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "fiscal_agente_local_sysvar"
        ordering = ["empresa_id", "identificador"]
        constraints = [
            UniqueConstraint(fields=["empresa", "identificador"], name="uq_agente_local_sysvar_empresa_ident"),
        ]
        indexes = [
            Index(fields=["empresa", "ativo"], name="ix_agente_local_emp_ativo"),
            Index(fields=["token_hash"], name="ix_agente_local_token_hash"),
        ]

    def __str__(self) -> str:
        return f"{self.empresa_id} - {self.identificador}"

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


class NotaFiscalEntrada(models.Model):
    class Status(models.TextChoices):
        ABERTA = "AB", "Aberta"
        FECHADA = "FE", "Fechada"
        CANCELADA = "CA", "Cancelada"

    class SituacaoFiscal(models.TextChoices):
        DESCONHECIDA = "DESCONHECIDA", "Desconhecida"
        AUTORIZADA = "AUTORIZADA", "Autorizada"
        CANCELADA = "CANCELADA", "Cancelada"
        DENEGADA = "DENEGADA", "Denegada"

    empresa = models.ForeignKey(
        "cadastros.Empresa",
        on_delete=models.PROTECT,
        related_name="notas_fiscais_entrada",
        db_index=True,
    )
    loja = models.ForeignKey(
        "cadastros.Loja",
        on_delete=models.PROTECT,
        related_name="notas_fiscais_entrada",
        db_index=True,
    )
    fornecedor = models.ForeignKey(
        "cadastros.Fornecedor",
        on_delete=models.PROTECT,
        related_name="notas_fiscais_entrada",
        db_index=True,
    )
    pedido_compra = models.ForeignKey(
        "compras.PedidoCompra",
        on_delete=models.PROTECT,
        related_name="notas_entrada",
        null=True,
        blank=True,
        db_index=True,
    )

    # dados básicos da NF
    modelo = models.CharField(max_length=2, default="55")  # 55 = NFe (MVP)
    serie = models.CharField(max_length=10, blank=True, default="")
    numero = models.CharField(max_length=20)
    chave_acesso = models.CharField(max_length=44, blank=True, null=True, default=None, unique=True, db_index=True)

    dt_emissao = models.DateField()
    dt_entrada = models.DateField()

    status = models.CharField(
        max_length=2,
        choices=Status.choices,
        default=Status.ABERTA,
        db_index=True,
    )

    # totais (MVP)
    valor_produtos = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    valor_desconto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    valor_frete = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    valor_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    observacoes = models.CharField(max_length=255, blank=True, default="")
    xml_original = models.TextField(blank=True, default="")
    xml_importado = models.BooleanField(default=False, db_index=True)
    natureza_operacao = models.CharField(max_length=120, blank=True, default="")
    emitente_documento = models.CharField(max_length=14, blank=True, default="")
    emitente_nome = models.CharField(max_length=120, blank=True, default="")
    emitente_ie = models.CharField(max_length=20, blank=True, default="")
    destinatario_documento = models.CharField(max_length=14, blank=True, default="")
    destinatario_nome = models.CharField(max_length=120, blank=True, default="")
    protocolo_autorizacao = models.CharField(max_length=30, blank=True, default="")
    situacao_fiscal = models.CharField(max_length=20, choices=SituacaoFiscal.choices, default=SituacaoFiscal.DESCONHECIDA, db_index=True)
    versao_leiaute = models.CharField(max_length=10, blank=True, default="")
    nfe_id_xml = models.CharField(max_length=47, blank=True, default="")
    codigo_uf = models.CharField(max_length=2, blank=True, default="")
    codigo_numerico = models.CharField(max_length=8, blank=True, default="")
    dh_emissao = models.DateTimeField(null=True, blank=True)
    dh_saida_entrada = models.DateTimeField(null=True, blank=True)
    tipo_operacao = models.CharField(max_length=1, blank=True, default="")
    identificador_destino = models.CharField(max_length=1, blank=True, default="")
    municipio_fato_gerador = models.CharField(max_length=7, blank=True, default="")
    tipo_impressao = models.CharField(max_length=1, blank=True, default="")
    tipo_emissao = models.CharField(max_length=1, blank=True, default="")
    digito_verificador = models.CharField(max_length=1, blank=True, default="")
    ambiente = models.CharField(max_length=1, blank=True, default="", db_index=True)
    finalidade_nfe = models.CharField(max_length=1, blank=True, default="", db_index=True)
    consumidor_final = models.CharField(max_length=1, blank=True, default="")
    presenca_comprador = models.CharField(max_length=1, blank=True, default="")
    intermediador = models.CharField(max_length=1, blank=True, default="")
    processo_emissao = models.CharField(max_length=1, blank=True, default="")
    versao_processo = models.CharField(max_length=20, blank=True, default="")
    protocolo_chave_acesso = models.CharField(max_length=44, blank=True, default="")
    protocolo_recebido_em = models.DateTimeField(null=True, blank=True)
    protocolo_cstat = models.CharField(max_length=4, blank=True, default="", db_index=True)
    protocolo_motivo = models.CharField(max_length=255, blank=True, default="")
    totais_fiscais = models.JSONField(default=dict, blank=True)
    cobranca_fiscal = models.JSONField(default=dict, blank=True)
    pagamentos_fiscais = models.JSONField(default=list, blank=True)
    documentos_referenciados = models.JSONField(default=list, blank=True)
    informacoes_complementares_fisco = models.TextField(blank=True, default="")
    informacoes_complementares_contribuinte = models.TextField(blank=True, default="")

    # auditoria básica
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="nfe_entrada_criadas",
        null=True,
        blank=True,
    )
    cancelado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="nfe_entrada_canceladas",
        null=True,
        blank=True,
    )
    cancelado_em = models.DateTimeField(null=True, blank=True)
    motivo_cancelamento = models.TextField(blank=True, default="")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "fiscal_nota_fiscal_entrada"
        indexes = [
            Index(fields=["empresa", "status"], name="ix_fiscal_nfe_empresa_status"),
            Index(fields=["pedido_compra", "status"], name="ix_fiscal_nfe_pedido_status"),
            Index(fields=["modelo", "serie", "numero"], name="ix_fiscal_nfe_num"),
            Index(fields=["empresa", "situacao_fiscal"], name="ix_fiscal_nfe_emp_sitfis"),
            Index(fields=["empresa", "ambiente"], name="ix_fiscal_nfe_emp_amb"),
        ]
        constraints = [
            UniqueConstraint(
                fields=["empresa", "fornecedor", "modelo", "serie", "numero"],
                name="uq_fiscal_nfe_emp_forn_doc",
            )
        ]

    def __str__(self) -> str:
        origem = f"Pedido {self.pedido_compra_id}" if self.pedido_compra_id else "sem pedido"
        return f"NFE {self.modelo}/{self.serie}/{self.numero} ({origem})"

    def save(self, *args, **kwargs):
        if self.pedido_compra_id:
            self.empresa_id = self.pedido_compra.empresa_id
            self.loja_id = self.pedido_compra.loja_id
            self.fornecedor_id = self.pedido_compra.fornecedor_id
        super().save(*args, **kwargs)

    def recalcular_totais(self):
        itens = list(self.itens.all())
        self.valor_produtos = _money(
            sum(Decimal(item.qtd_recebida or 0) * Decimal(item.preco_unit_nf or 0) for item in itens)
        )
        self.valor_desconto = _money(sum((item.desconto_item or 0) for item in itens))
        self.valor_total = _money((self.valor_produtos or 0) - (self.valor_desconto or 0) + (self.valor_frete or 0))
        if self.valor_total < 0:
            raise ValueError("Total da nota fiscal de entrada não pode ser negativo.")
        self.save(update_fields=["valor_produtos", "valor_desconto", "valor_total", "atualizado_em"])

    def resumo_conciliacao_xml(self):
        total = self.itens_xml.count()
        conciliados = self.itens_xml.filter(produto__isnull=False).count()
        divergencias_pedido = self.divergencias_pedido_xml()
        bloqueios_pedido = [div for div in divergencias_pedido if div.get("bloqueia")]
        return {
            "total_itens": total,
            "itens_conciliados": conciliados,
            "itens_pendentes": total - conciliados,
            "nota_conciliada": total > 0 and conciliados == total,
            "divergencias_pedido": divergencias_pedido,
            "divergencias_pedido_count": len(divergencias_pedido),
            "bloqueios_pedido_count": len(bloqueios_pedido),
            "possui_divergencia_pedido": bool(divergencias_pedido),
        }

    def divergencias_pedido_xml(self):
        if not self.xml_importado or not self.pedido_compra_id:
            return []
        divergencias = []
        for item in self.itens_xml.select_related("produto", "pedido_item", "pedido_item__produto").order_by("numero_item"):
            divergencias.extend(item.divergencias_pedido())
        return divergencias

    def resumo_conferencia_xml(self):
        itens = list(self.itens_xml.select_related("produto_fornecedor", "produto__unidade").all())
        total = len(itens)
        conferidos = sum(1 for item in itens if item.quantidade_recebida is not None)
        conversao_pendente = sum(1 for item in itens if item.produto_id and not item.conversao_pronta)
        divergencias = self.divergencias_xml.filter(status=NotaFiscalEntradaDivergenciaXml.Status.PENDENTE)
        valor_divergente = _money(sum((div.valor_divergente or 0) for div in divergencias))
        quantidade_faltante = sum((div.quantidade_faltante or 0) for div in divergencias)
        return {
            "total_itens": total,
            "itens_conferidos": conferidos,
            "itens_nao_conferidos": total - conferidos,
            "itens_com_divergencia": divergencias.count(),
            "quantidade_faltante_total": str(quantidade_faltante),
            "valor_divergente_total": str(valor_divergente),
            "possui_divergencia_pendente": divergencias.exists(),
            "conversoes_pendentes": conversao_pendente,
            "conferencia_completa": (
                total > 0
                and self.resumo_conciliacao_xml()["nota_conciliada"]
                and conferidos == total
                and conversao_pendente == 0
            ),
        }


class XmlFornecedorRecebido(models.Model):
    class StatusOperacional(models.TextChoices):
        DETECTADO = "DETECTADO", "Detectado"
        AGUARDANDO_RECEBIMENTO = "AGUARDANDO_RECEBIMENTO", "Aguardando recebimento"
        EM_RECEBIMENTO = "EM_RECEBIMENTO", "Em recebimento"
        RECEBIDO = "RECEBIDO", "Recebido"
        PROCESSADO = "PROCESSADO", "Processado"
        IGNORADO = "IGNORADO", "Ignorado"

    class SituacaoFiscal(models.TextChoices):
        DESCONHECIDA = "DESCONHECIDA", "Desconhecida"
        AUTORIZADA = "AUTORIZADA", "Autorizada"
        CANCELADA = "CANCELADA", "Cancelada"
        DENEGADA = "DENEGADA", "Denegada"

    empresa = models.ForeignKey("cadastros.Empresa", on_delete=models.PROTECT, related_name="xmls_fornecedor_recebidos", db_index=True)
    loja = models.ForeignKey("cadastros.Loja", on_delete=models.PROTECT, related_name="xmls_fornecedor_recebidos", null=True, blank=True, db_index=True)
    fornecedor = models.ForeignKey("cadastros.Fornecedor", on_delete=models.PROTECT, related_name="xmls_fornecedor_recebidos", null=True, blank=True, db_index=True)
    chave_acesso = models.CharField(max_length=44, unique=True, db_index=True)
    modelo = models.CharField(max_length=2, default="55")
    serie = models.CharField(max_length=10, blank=True, default="")
    numero = models.CharField(max_length=20, blank=True, default="")
    dh_emissao = models.DateTimeField(null=True, blank=True)
    emitente_documento = models.CharField(max_length=14, blank=True, default="")
    emitente_nome = models.CharField(max_length=120, blank=True, default="")
    destinatario_documento = models.CharField(max_length=14, blank=True, default="")
    destinatario_nome = models.CharField(max_length=120, blank=True, default="")
    valor_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    situacao_fiscal = models.CharField(max_length=20, choices=SituacaoFiscal.choices, default=SituacaoFiscal.DESCONHECIDA, db_index=True)
    status_operacional = models.CharField(max_length=24, choices=StatusOperacional.choices, default=StatusOperacional.DETECTADO, db_index=True)
    caminho_origem_local = models.CharField(max_length=500, blank=True, default="")
    identificador_agente = models.CharField(max_length=120, blank=True, default="")
    detectado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    erro_processamento = models.TextField(blank=True, default="")

    class Meta:
        db_table = "fiscal_xml_fornecedor_recebido"
        ordering = ["-detectado_em", "-id"]
        indexes = [
            Index(fields=["empresa", "status_operacional"], name="ix_xml_forn_emp_status"),
            Index(fields=["empresa", "situacao_fiscal"], name="ix_xml_forn_emp_sitfis"),
            Index(fields=["emitente_documento"], name="ix_xml_forn_emit_doc"),
            Index(fields=["destinatario_documento"], name="ix_xml_forn_dest_doc"),
        ]

    def __str__(self) -> str:
        return f"XML fornecedor {self.modelo}/{self.serie}/{self.numero} - {self.chave_acesso}"


class ConfiguracaoXmlFornecedor(models.Model):
    empresa = models.ForeignKey("cadastros.Empresa", on_delete=models.PROTECT, related_name="configuracoes_xml_fornecedor", db_index=True)
    loja = models.ForeignKey("cadastros.Loja", on_delete=models.PROTECT, related_name="configuracoes_xml_fornecedor", null=True, blank=True, db_index=True)
    loja_escopo_unicidade = models.BigIntegerField(default=0, editable=False)
    caminho_local = models.CharField(max_length=500)
    ativo = models.BooleanField(default=True, db_index=True)
    identificador_agente = models.CharField(max_length=120, blank=True, default="")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "fiscal_configuracao_xml_fornecedor"
        ordering = ["empresa_id", "loja_id", "caminho_local"]
        constraints = [
            UniqueConstraint(
                fields=["empresa", "loja_escopo_unicidade", "caminho_local"],
                name="uq_cfg_xml_forn_emp_scope_path",
            ),
        ]
        indexes = [
            Index(fields=["empresa", "loja", "ativo"], name="ix_cfg_xml_forn_emp_loja_atv"),
            Index(fields=["identificador_agente"], name="ix_cfg_xml_forn_agente"),
        ]

    def __str__(self) -> str:
        escopo = self.loja_id or "geral"
        return f"Config XML fornecedor {self.empresa_id}/{escopo} - {self.caminho_local}"

    def save(self, *args, **kwargs):
        self.loja_escopo_unicidade = self.loja_id or 0
        super().save(*args, **kwargs)


class NotaFiscalEntradaItem(models.Model):
    nota = models.ForeignKey(
        "fiscal.NotaFiscalEntrada",
        on_delete=models.CASCADE,
        related_name="itens",
        db_index=True,
    )

    # link direto ao item do pedido (MVP)
    pedido_item = models.ForeignKey(
        "compras.PedidoCompraItem",
        on_delete=models.PROTECT,
        related_name="itens_nf_entrada",
        db_index=True,
    )

    qtd_recebida = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    preco_unit_nf = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    desconto_item = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_item = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "fiscal_nota_fiscal_entrada_item"
        constraints = [
            UniqueConstraint(
                fields=["nota", "pedido_item"],
                name="uq_fiscal_nfe_item_nota_pedido_item",
            )
        ]
        indexes = [
            Index(fields=["nota"], name="ix_fiscal_nfe_item_nota"),
            Index(fields=["pedido_item"], name="ix_fiscal_nfe_item_pedido_item"),
        ]

    def __str__(self) -> str:
        return f"Item NF {self.nota_id} / PedidoItem {self.pedido_item_id}"


class NotaFiscalEntradaItemXml(models.Model):
    class OrigemConciliacao(models.TextChoices):
        VINCULO = "VINCULO", "Vínculo existente"
        PEDIDO = "PEDIDO", "Pedido"
        GTIN = "GTIN", "GTIN/EAN"
        MANUAL = "MANUAL", "Manual"

    nota = models.ForeignKey(
        "fiscal.NotaFiscalEntrada",
        on_delete=models.CASCADE,
        related_name="itens_xml",
        db_index=True,
    )
    numero_item = models.PositiveIntegerField()
    codigo_produto_fornecedor = models.CharField(max_length=80, blank=True, default="")
    descricao_produto = models.CharField(max_length=255, blank=True, default="")
    gtin_ean = models.CharField(max_length=14, blank=True, default="")
    ncm = models.CharField(max_length=10, blank=True, default="")
    cfop = models.CharField(max_length=4, blank=True, default="")
    unidade_comercial = models.CharField(max_length=20, blank=True, default="")
    quantidade_comercial = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    quantidade_recebida = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    valor_unitario_comercial = models.DecimalField(max_digits=18, decimal_places=10, default=0)
    valor_produto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    valor_desconto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    informacoes_adicionais = models.TextField(blank=True, default="")
    impostos_fiscais = models.JSONField(default=dict, blank=True)
    produto = models.ForeignKey(
        "produto.Produto",
        on_delete=models.PROTECT,
        related_name="itens_xml_nfe",
        null=True,
        blank=True,
        db_index=True,
    )
    produto_fornecedor = models.ForeignKey(
        "produto.ProdutoFornecedor",
        on_delete=models.PROTECT,
        related_name="itens_xml_nfe",
        null=True,
        blank=True,
        db_index=True,
    )
    pedido_item = models.ForeignKey(
        "compras.PedidoCompraItem",
        on_delete=models.PROTECT,
        related_name="itens_xml_nfe",
        null=True,
        blank=True,
        db_index=True,
    )
    origem_conciliacao = models.CharField(max_length=10, choices=OrigemConciliacao.choices, blank=True, default="")
    conciliado_em = models.DateTimeField(null=True, blank=True)
    conciliado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="itens_xml_nfe_conciliados",
        null=True,
        blank=True,
    )
    conferido_em = models.DateTimeField(null=True, blank=True)
    conferido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="itens_xml_nfe_conferidos",
        null=True,
        blank=True,
    )
    unidade_fornecedor_efetivada = models.CharField(max_length=20, blank=True, default="")
    fator_conversao_efetivado = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    quantidade_interna_efetivada = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    efetivado_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fiscal_nota_fiscal_entrada_item_xml"
        constraints = [
            UniqueConstraint(fields=["nota", "numero_item"], name="uq_fiscal_nfe_xml_item_numero"),
        ]
        indexes = [
            Index(fields=["nota", "codigo_produto_fornecedor"], name="ix_fiscal_nfe_xml_cod"),
            Index(fields=["gtin_ean"], name="ix_fiscal_nfe_xml_gtin"),
            Index(fields=["nota", "produto"], name="ix_fiscal_nfe_xml_prod"),
        ]

    def __str__(self) -> str:
        return f"Item XML NF {self.nota_id} #{self.numero_item}"

    @property
    def conciliado(self):
        return self.produto_id is not None

    def divergencias_pedido(self):
        if not self.nota_id or not self.nota.pedido_compra_id:
            return []
        if not self.pedido_item_id or self.pedido_item.pedido_id != self.nota.pedido_compra_id:
            return [
                self._divergencia_pedido(
                    "SEM_VINCULO_PEDIDO",
                    "Sem vínculo seguro com item do Pedido",
                    "Item XML sem vínculo seguro com item aprovado do Pedido de Compra.",
                )
            ]

        divergencias = []
        preco_xml = Decimal(self.valor_unitario_comercial or 0)
        preco_pedido = Decimal(self.pedido_item.preco_unit or 0)
        if preco_xml > preco_pedido:
            divergencias.append(
                self._divergencia_pedido(
                    "PRECO_ACIMA_PEDIDO",
                    "Preço NF acima do Pedido",
                    f"Preço unitário da NF-e ({_fmt_money(preco_xml)}) maior que o preço aprovado do Pedido ({_fmt_money(preco_pedido)}).",
                    preco_nf=preco_xml,
                    preco_pedido=preco_pedido,
                )
            )

        quantidade = self.quantidade_interna_fiscal
        saldo = self.saldo_pedido_disponivel
        if quantidade is not None and quantidade > saldo:
            divergencias.append(
                self._divergencia_pedido(
                    "QUANTIDADE_ACIMA_SALDO",
                    "Quantidade NF acima do saldo",
                    f"Quantidade do XML ({_fmt_qty(quantidade)}) maior que o saldo disponível do Pedido ({_fmt_qty(saldo)}).",
                    quantidade_nf=quantidade,
                    saldo_pedido=saldo,
                )
            )
        return divergencias

    @property
    def quantidade_interna_fiscal(self):
        if not self.produto_fornecedor_id:
            return None
        return Decimal(self.produto_fornecedor.converter_quantidade_fornecedor(self.quantidade_comercial or 0))

    @property
    def quantidade_interna_recebida(self):
        if self.quantidade_recebida is None or not self.produto_fornecedor_id:
            return None
        return Decimal(self.produto_fornecedor.converter_quantidade_fornecedor(self.quantidade_recebida))

    @property
    def saldo_pedido_disponivel(self):
        if not self.pedido_item_id:
            return Decimal("0")
        itens = NotaFiscalEntradaItem.objects.filter(
            pedido_item=self.pedido_item,
            nota__pedido_compra_id=self.pedido_item.pedido_id,
            nota__status=NotaFiscalEntrada.Status.FECHADA,
        ).exclude(nota=self.nota)
        total_legado = sum(Decimal(item.qtd_recebida or 0) for item in itens)
        itens_xml = NotaFiscalEntradaItemXml.objects.filter(
            pedido_item=self.pedido_item,
            nota__pedido_compra_id=self.pedido_item.pedido_id,
        ).exclude(nota__status=NotaFiscalEntrada.Status.CANCELADA).exclude(nota=self.nota)
        total_xml = sum(
            Decimal(item.quantidade_interna_efetivada or 0)
            for item in itens_xml.filter(Q(nota__status=NotaFiscalEntrada.Status.FECHADA) | Q(quantidade_interna_efetivada__isnull=False))
        )
        return Decimal(self.pedido_item.qtd or 0) - total_legado - total_xml

    def _divergencia_pedido(self, tipo, titulo, mensagem, **valores):
        produto = self.produto or getattr(self.pedido_item, "produto", None)
        data = {
            "item_xml": self.pk,
            "numero_item": self.numero_item,
            "pedido_item": self.pedido_item_id,
            "produto": getattr(produto, "pk", None),
            "produto_descricao": getattr(produto, "descricao", None) or self.descricao_produto,
            "tipo": tipo,
            "titulo": titulo,
            "mensagem": mensagem,
            "bloqueia": True,
        }
        data.update({key: str(value) for key, value in valores.items()})
        return data

    @property
    def conferido(self):
        return self.quantidade_recebida is not None

    @property
    def quantidade_faltante(self):
        if self.quantidade_recebida is None:
            return None
        return Decimal(self.quantidade_comercial or 0) - Decimal(self.quantidade_recebida or 0)

    @property
    def valor_divergente(self):
        if self.quantidade_faltante is None:
            return None
        return _money(Decimal(self.quantidade_faltante or 0) * Decimal(self.valor_unitario_comercial or 0))

    @property
    def conversao_pronta(self):
        if not self.produto_id or not self.produto_fornecedor_id:
            return False
        unidade_xml = str(self.unidade_comercial or "").strip().upper()
        unidade_vinculo = str(self.produto_fornecedor.unidade_fornecedor or "").strip().upper()
        return bool(unidade_vinculo and unidade_xml == unidade_vinculo)


class NotaFiscalEntradaDivergenciaXml(models.Model):
    class Status(models.TextChoices):
        PENDENTE = "PENDENTE", "Pendente"
        RESOLVIDA = "RESOLVIDA", "Resolvida"
        CANCELADA = "CANCELADA", "Cancelada"

    empresa = models.ForeignKey("cadastros.Empresa", on_delete=models.PROTECT, related_name="divergencias_nfe_xml", db_index=True)
    nota = models.ForeignKey("fiscal.NotaFiscalEntrada", on_delete=models.CASCADE, related_name="divergencias_xml", db_index=True)
    item_xml = models.OneToOneField("fiscal.NotaFiscalEntradaItemXml", on_delete=models.CASCADE, related_name="divergencia", db_index=True)
    fornecedor = models.ForeignKey("cadastros.Fornecedor", on_delete=models.PROTECT, related_name="divergencias_nfe_xml", db_index=True)
    produto = models.ForeignKey("produto.Produto", on_delete=models.PROTECT, related_name="divergencias_nfe_xml", db_index=True)
    quantidade_fiscal = models.DecimalField(max_digits=18, decimal_places=6)
    quantidade_recebida = models.DecimalField(max_digits=18, decimal_places=6)
    quantidade_faltante = models.DecimalField(max_digits=18, decimal_places=6)
    valor_divergente = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDENTE, db_index=True)
    conferido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="divergencias_nfe_xml_conferidas",
        null=True,
        blank=True,
    )
    resolvido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="divergencias_nfe_xml_resolvidas",
        null=True,
        blank=True,
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    resolvido_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "fiscal_nota_fiscal_entrada_divergencia_xml"
        indexes = [
            Index(fields=["nota", "status"], name="ix_fiscal_nfe_div_xml_st"),
            Index(fields=["empresa", "status"], name="ix_fiscal_nfe_div_emp_st"),
        ]

    def __str__(self) -> str:
        return f"Divergência XML NF {self.nota_id} item {self.item_xml_id}"


class NotaFiscalEntradaEvento(models.Model):
    class Origem(models.TextChoices):
        IMPORTACAO_MANUAL = "IMPORTACAO_MANUAL", "Importação manual"
        CONSULTA_SEFAZ = "CONSULTA_SEFAZ", "Consulta SEFAZ"
        TRANSMISSAO_FUTURA = "TRANSMISSAO_FUTURA", "Transmissão futura"

    class SituacaoProcessamento(models.TextChoices):
        REGISTRADO = "REGISTRADO", "Registrado"
        PROCESSADO = "PROCESSADO", "Processado"
        REJEITADO = "REJEITADO", "Rejeitado"

    empresa = models.ForeignKey("cadastros.Empresa", on_delete=models.PROTECT, related_name="eventos_nfe_entrada", db_index=True)
    nota = models.ForeignKey("fiscal.NotaFiscalEntrada", on_delete=models.CASCADE, related_name="eventos_fiscais", db_index=True)
    chave_acesso = models.CharField(max_length=44, db_index=True)
    id_evento = models.CharField(max_length=80, blank=True, default="")
    tipo_evento = models.CharField(max_length=10, db_index=True)
    tipo_evento_descricao = models.CharField(max_length=120, blank=True, default="")
    sequencia = models.PositiveIntegerField(default=1)
    data_hora_evento = models.DateTimeField(null=True, blank=True)
    protocolo = models.CharField(max_length=30, blank=True, default="")
    cstat = models.CharField(max_length=4, blank=True, default="", db_index=True)
    xmotivo = models.CharField(max_length=255, blank=True, default="")
    ambiente = models.CharField(max_length=1, blank=True, default="", db_index=True)
    origem = models.CharField(max_length=30, choices=Origem.choices, default=Origem.IMPORTACAO_MANUAL)
    situacao_processamento = models.CharField(max_length=20, choices=SituacaoProcessamento.choices, default=SituacaoProcessamento.REGISTRADO)
    xml_original = models.TextField(blank=True, default="")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fiscal_nota_fiscal_entrada_evento"
        constraints = [
            UniqueConstraint(fields=["empresa", "chave_acesso", "tipo_evento", "sequencia", "protocolo"], name="uq_fiscal_nfe_evento_idem"),
        ]
        indexes = [
            Index(fields=["nota", "tipo_evento", "sequencia"], name="ix_fiscal_nfe_evt_tipo_seq"),
            Index(fields=["empresa", "ambiente", "tipo_evento"], name="ix_fiscal_nfe_evt_emp_amb"),
        ]

    def __str__(self) -> str:
        return f"Evento NF-e {self.chave_acesso} {self.tipo_evento}/{self.sequencia}"


class FormaPagamentoFiscalMap(models.Model):
    empresa = models.ForeignKey("cadastros.Empresa", on_delete=models.PROTECT, related_name="formas_pagamento_fiscais", db_index=True)
    codigo_tpag = models.CharField(max_length=2, db_index=True)
    descricao_fiscal = models.CharField(max_length=80, blank=True, default="")
    forma_pagamento = models.ForeignKey("financeiro.FormaPagamento", on_delete=models.PROTECT, related_name="mapas_fiscais_nfe")
    ativo = models.BooleanField(default=True, db_index=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="formas_pagamento_fiscais_criadas", null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "fiscal_forma_pagamento_fiscal_map"
        constraints = [
            UniqueConstraint(fields=["empresa", "codigo_tpag"], name="uq_fiscal_tpag_empresa"),
        ]
        indexes = [
            Index(fields=["empresa", "codigo_tpag", "ativo"], name="ix_fiscal_tpag_emp_cod_atv"),
        ]

    def __str__(self) -> str:
        return f"{self.empresa_id} tPag {self.codigo_tpag} -> {self.forma_pagamento}"
