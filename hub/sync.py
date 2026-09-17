import hashlib
import json
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import dateparse, timezone

from cadastros.models import Cliente, Funcionarios
from fiscal.models import VendaPdv
from fiscal.models.venda_pdv import money
from fiscal.views.venda_pdv import VendaPdvViewSet
from financeiro.models import Caixa
from produto.models import ProdutoDetalhe

from hub.models import (
    HubClienteMapeamento,
    HubEventoRecebido,
    HubFechamentoDiaRecebido,
    HubMovimentoCaixaRecebido,
    HubSessaoCaixaRecebida,
    HubVendaMapeamento,
)


TIPOS_SUPORTADOS = {
    "CLIENTE_LOCAL",
    "VENDA_FINALIZADA",
    "MOVIMENTO_CAIXA",
    "SESSAO_CAIXA_FECHADA",
    "FECHAMENTO_DIA",
}


def payload_hash(payload):
    normalizado = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(normalizado.encode("utf-8")).hexdigest()


def limpar_documento(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def parse_decimal(value):
    return money(value)


def parse_datetime(value, campo="data/hora"):
    if not value:
        return None
    parsed = dateparse.parse_datetime(str(value))
    if not parsed:
        raise HubSyncError(f"{campo} inválida.")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def parse_required_date(value, campo):
    if not value:
        raise HubSyncError(f"{campo} é obrigatória.")
    parsed = dateparse.parse_date(str(value))
    if not parsed:
        raise HubSyncError(f"{campo} inválida.")
    return parsed


def uuid_value(value, campo):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{campo} inválido.") from exc


class HubSyncError(Exception):
    pass


class HubSyncProcessor:
    def __init__(self, hub, request=None):
        self.hub = hub
        self.request = request
        self.loja = hub.loja
        self.empresa = hub.loja.empresa

    def processar_lote(self, eventos):
        if len(eventos) > 50:
            raise ValidationError("Máximo de 50 eventos por chamada.")
        return [self.processar_evento(evento or {}) for evento in eventos]

    def processar_evento(self, item):
        evento_uuid = item.get("evento_uuid")
        chave = str(item.get("chave_idempotencia") or "").strip()
        tipo = str(item.get("tipo") or "").strip().upper()
        payload = item.get("payload") or {}
        if not evento_uuid or not chave or tipo not in TIPOS_SUPORTADOS or not isinstance(payload, dict):
            return self._resultado(evento_uuid, chave, HubEventoRecebido.STATUS_ERRO, mensagem="Evento inválido.")
        try:
            evento_uuid_obj = uuid_value(evento_uuid, "evento_uuid")
        except ValidationError:
            return self._resultado(evento_uuid, chave, HubEventoRecebido.STATUS_ERRO, mensagem="evento_uuid inválido.")

        digest = payload_hash(payload)
        with transaction.atomic():
            existente = self._buscar_evento_existente(evento_uuid_obj, chave)
            if existente:
                return self._resolver_evento_existente(existente, evento_uuid_obj, chave, tipo, payload, digest)

            try:
                with transaction.atomic():
                    registro = HubEventoRecebido.objects.create(
                        hub=self.hub,
                        evento_uuid=evento_uuid_obj,
                        chave_idempotencia=chave,
                        tipo=tipo,
                        payload_hash=digest,
                        payload=payload,
                    )
            except IntegrityError:
                registro = self._buscar_evento_existente(evento_uuid_obj, chave)
                if registro:
                    return self._resolver_evento_existente(registro, evento_uuid_obj, chave, tipo, payload, digest)
                return self._resultado(evento_uuid, chave, HubEventoRecebido.STATUS_ERRO, mensagem="Conflito concorrente ao registrar evento.")

            return self._processar_registro(registro, tipo, payload, evento_uuid, chave)

    def _buscar_evento_existente(self, evento_uuid, chave):
        return (
            HubEventoRecebido.objects.select_for_update()
            .filter(hub=self.hub)
            .filter(models_q_evento(evento_uuid, chave))
            .order_by("id")
            .first()
        )

    def _resolver_evento_existente(self, registro, evento_uuid, chave, tipo, payload, digest):
        if registro.evento_uuid != evento_uuid or registro.chave_idempotencia != chave:
            return self._resultado(evento_uuid, chave, HubEventoRecebido.STATUS_CONFLITO, mensagem="Identificadores idempotentes conflitantes.")
        if registro.payload_hash != digest:
            return self._resultado(evento_uuid, chave, HubEventoRecebido.STATUS_CONFLITO, mensagem="Chave idempotente reutilizada com payload diferente.")
        if registro.status == HubEventoRecebido.STATUS_PROCESSADO:
            return self._resultado(evento_uuid, chave, HubEventoRecebido.STATUS_DUPLICADO, mapeamento=self._mapeamento_existente(tipo, payload))
        if registro.status == HubEventoRecebido.STATUS_ERRO:
            return self._processar_registro(registro, tipo, payload, evento_uuid, chave)
        return self._resultado(evento_uuid, chave, HubEventoRecebido.STATUS_DUPLICADO, mapeamento=self._mapeamento_existente(tipo, payload))

    def _processar_registro(self, registro, tipo, payload, evento_uuid, chave):
        try:
            with transaction.atomic():
                mapeamento = self._processar_payload(tipo, payload)
        except Exception as exc:
            registro.status = HubEventoRecebido.STATUS_ERRO
            registro.mensagem_erro = str(exc)[:255] or "Erro ao processar evento."
            registro.processado_em = timezone.now()
            registro.save(update_fields=["status", "mensagem_erro", "processado_em"])
            return self._resultado(evento_uuid, chave, HubEventoRecebido.STATUS_ERRO, mensagem=registro.mensagem_erro)

        registro.status = HubEventoRecebido.STATUS_PROCESSADO
        registro.mensagem_erro = ""
        registro.processado_em = timezone.now()
        registro.save(update_fields=["status", "mensagem_erro", "processado_em"])
        return self._resultado(evento_uuid, chave, HubEventoRecebido.STATUS_PROCESSADO, mapeamento=mapeamento)

    def _processar_payload(self, tipo, payload):
        if tipo == "CLIENTE_LOCAL":
            return self._cliente_local(payload)
        if tipo == "VENDA_FINALIZADA":
            return self._venda_finalizada(payload)
        if tipo == "MOVIMENTO_CAIXA":
            return self._movimento_caixa(payload)
        if tipo == "SESSAO_CAIXA_FECHADA":
            return self._sessao_caixa(payload)
        if tipo == "FECHAMENTO_DIA":
            return self._fechamento_dia(payload)
        raise HubSyncError("Tipo de evento não suportado.")

    def _cliente_local(self, payload):
        cliente_uuid = uuid_value(payload.get("cliente_uuid"), "cliente_uuid")
        mapeado = HubClienteMapeamento.objects.filter(hub=self.hub, cliente_uuid=cliente_uuid).first()
        if mapeado:
            return {"cliente_retaguarda_id": mapeado.cliente_id}

        cliente = None
        retaguarda_id = payload.get("retaguarda_id")
        if retaguarda_id:
            cliente = Cliente.objects.filter(pk=retaguarda_id, empresa=self.empresa).first()
            if not cliente:
                raise HubSyncError("Cliente retaguarda não pertence à empresa do Hub.")
        documento = limpar_documento(payload.get("documento"))
        if not cliente and documento:
            cliente = Cliente.objects.filter(empresa=self.empresa, documento=documento).first()
        if not cliente:
            tipo_pessoa = payload.get("tipo_pessoa") or ("PJ" if len(documento) == 14 else "PF")
            cliente = Cliente.objects.create(
                empresa=self.empresa,
                tipo_pessoa=tipo_pessoa,
                documento=documento or None,
                cpf=documento or None,
                nome_cliente=str(payload.get("nome") or "Cliente Hub")[:50],
                apelido=str(payload.get("apelido") or payload.get("nome") or "")[:18] or None,
                logradouro=payload.get("logradouro") or None,
                endereco=payload.get("endereco") or None,
                numero=payload.get("numero") or None,
                complemento=payload.get("complemento") or None,
                cep=limpar_documento(payload.get("cep")) or None,
                bairro=payload.get("bairro") or None,
                cidade=payload.get("cidade") or None,
                estado=(payload.get("estado") or "")[:2].upper() or None,
                telefone1=limpar_documento(payload.get("telefone1") or payload.get("telefone")) or None,
                email=(payload.get("email") or None),
            )
        HubClienteMapeamento.objects.get_or_create(hub=self.hub, cliente_uuid=cliente_uuid, defaults={"cliente": cliente})
        return {"cliente_retaguarda_id": cliente.pk}

    def _venda_finalizada(self, payload):
        venda_uuid = uuid_value(payload.get("venda_uuid"), "venda_uuid")
        existente = HubVendaMapeamento.objects.filter(hub=self.hub, venda_uuid=venda_uuid).first()
        if existente:
            return {"venda_retaguarda_id": existente.venda_id, "documento": existente.documento}

        caixa = self._caixa(payload.get("caixa_retaguarda_id"))
        cliente = self._cliente_venda(payload)
        vendedor = self._vendedor(payload.get("vendedor_retaguarda_id"))
        if not payload.get("itens"):
            raise HubSyncError("Inclua ao menos um item na venda.")
        documento = f"HUB-{self.hub.pk}-{venda_uuid.hex[:20]}"
        if VendaPdv.objects.filter(documento=documento).exists():
            venda = VendaPdv.objects.get(documento=documento)
            HubVendaMapeamento.objects.get_or_create(hub=self.hub, venda_uuid=venda_uuid, defaults={"venda": venda, "documento": documento})
            return {"venda_retaguarda_id": venda.pk, "documento": documento}

        view = VendaPdvViewSet()
        data_venda = parse_datetime(payload.get("finalizado_em") or payload.get("data_hora") or payload.get("ocorrido_em"), "data da venda") or timezone.now()
        venda = VendaPdv.objects.create(
            empresa=self.empresa,
            loja=self.loja,
            caixa=caixa,
            cliente=cliente,
            vendedor=vendedor,
            documento=documento,
            forma_pagamento="HUB",
            desconto_geral=parse_decimal(payload.get("desconto_geral")),
            valor_recebido=parse_decimal(payload.get("valor_recebido") or payload.get("total")),
            data_venda=data_venda,
            criado_por=self._operador(payload.get("operador_retaguarda_usuario_id")),
        )
        subtotal = Decimal("0")
        desconto_itens = Decimal("0")
        for item in payload.get("itens") or []:
            item_payload = self._normalizar_item_venda(item)
            venda_item = view._registrar_item(venda, item_payload)
            subtotal += money(Decimal(venda_item.quantidade) * Decimal(venda_item.preco_unitario))
            desconto_itens += money(venda_item.desconto)
        pagamentos = view._normalizar_pagamentos({"pagamentos": [self._normalizar_pagamento(p) for p in payload.get("pagamentos") or []], "valor_recebido": payload.get("valor_recebido") or payload.get("total")})
        total = money(subtotal - desconto_itens - venda.desconto_geral)
        total_pago = money(sum((pagamento["valor"] for pagamento in pagamentos), Decimal("0")))
        if total_pago < total:
            raise HubSyncError("O total pago é menor que o total da venda.")
        venda.subtotal = money(subtotal)
        venda.desconto_itens = money(desconto_itens)
        venda.total = total
        venda.valor_recebido = total_pago
        venda.troco = money(total_pago - total) if total_pago > total else Decimal("0.00")
        venda.forma_pagamento = view._forma_resumo(pagamentos)
        venda.save(update_fields=["subtotal", "desconto_itens", "total", "valor_recebido", "troco", "forma_pagamento", "atualizado_em"])
        view._registrar_pagamentos(venda, pagamentos)
        view._registrar_financeiro(venda)
        view._registrar_cmv(venda)
        view._registrar_impostos_venda(venda)
        view._registrar_comissao(venda)
        HubVendaMapeamento.objects.create(hub=self.hub, venda_uuid=venda_uuid, venda=venda, documento=documento)
        return {"venda_retaguarda_id": venda.pk, "documento": documento, "total": str(venda.total)}

    def _normalizar_item_venda(self, item):
        produto_id = item.get("produto_retaguarda_id")
        sku_id = item.get("sku_retaguarda_id")
        sku = ProdutoDetalhe.objects.select_related("produto").filter(pk=sku_id, produto_id=produto_id, produto__empresa=self.empresa).first()
        if not sku:
            raise HubSyncError("Produto/SKU não pertence à empresa do Hub.")
        if item.get("ean") and str(item.get("ean")) != str(sku.ean13):
            raise HubSyncError("EAN não corresponde ao SKU informado.")
        return {
            "ean": sku.ean13,
            "quantidade": item.get("quantidade"),
            "preco_unitario": item.get("preco_unitario"),
            "desconto": item.get("desconto"),
            "descricao": item.get("descricao") or sku.produto.descricao,
            "cor": item.get("cor") or getattr(sku.idcor, "Descricao", ""),
            "tamanho": item.get("tamanho") or getattr(sku.idtamanho, "Tamanho", ""),
        }

    def _normalizar_pagamento(self, pagamento):
        codigo = pagamento.get("codigo") or pagamento.get("forma") or pagamento.get("tipo") or "DINHEIRO"
        return {
            "forma": str(codigo).upper(),
            "descricao": pagamento.get("descricao") or str(codigo).upper(),
            "valor": pagamento.get("valor"),
            "autorizacao": pagamento.get("autorizacao") or "",
        }

    def _caixa(self, caixa_id):
        caixa = Caixa.objects.select_for_update().filter(pk=caixa_id, empresa=self.empresa, idloja=self.loja, ativo=True, tipo_caixa=Caixa.TIPO_LOJA).first()
        if not caixa:
            raise HubSyncError("Caixa não pertence à loja/empresa do Hub.")
        return caixa

    def _cliente_venda(self, payload):
        cliente_id = payload.get("cliente_retaguarda_id")
        if cliente_id:
            cliente = Cliente.objects.filter(pk=cliente_id, empresa=self.empresa).first()
            if cliente:
                return cliente
            raise HubSyncError("Cliente da venda pertence a outra empresa.")
        cliente_uuid = payload.get("cliente_uuid")
        if cliente_uuid:
            mapping = HubClienteMapeamento.objects.filter(hub=self.hub, cliente_uuid=uuid_value(cliente_uuid, "cliente_uuid")).first()
            if mapping and mapping.cliente.empresa_id == self.empresa.pk:
                return mapping.cliente
        raise HubSyncError("Cliente da venda não localizado.")

    def _vendedor(self, vendedor_id):
        vendedor = Funcionarios.objects.filter(pk=vendedor_id, empresa=self.empresa, ativo=True).first()
        if not vendedor:
            raise HubSyncError("Vendedor não pertence à empresa do Hub.")
        return vendedor

    def _operador(self, user_id):
        if not user_id:
            return None
        User = get_user_model()
        return User.objects.filter(pk=user_id, empresa=self.empresa, is_active=True).first()

    def _movimento_caixa(self, payload):
        movimento_uuid = uuid_value(payload.get("movimento_uuid") or payload.get("uuid"), "movimento_uuid")
        caixa = self._caixa(payload.get("caixa_retaguarda_id") or payload.get("caixa")) if (payload.get("caixa_retaguarda_id") or payload.get("caixa")) else None
        obj, _ = HubMovimentoCaixaRecebido.objects.get_or_create(
            hub=self.hub,
            movimento_uuid=movimento_uuid,
            defaults={
                "tipo": str(payload.get("tipo") or "")[:20],
                "caixa": caixa,
                "operador": self._operador(payload.get("operador_retaguarda_usuario_id") or payload.get("operador")),
                "terminal": str(payload.get("terminal") or "")[:80],
                "valor": parse_decimal(payload.get("valor")),
                "historico": str(payload.get("historico") or "")[:255],
                "documento": str(payload.get("documento") or "")[:80],
                "tipo_despesa": str(payload.get("tipo_despesa") or "")[:80],
                "ocorrido_em": parse_datetime(payload.get("ocorrido_em"), "ocorrido_em"),
                "snapshot": payload,
            },
        )
        return {"movimento_caixa_id": obj.pk}

    def _sessao_caixa(self, payload):
        sessao_uuid = uuid_value(payload.get("sessao_uuid") or payload.get("uuid"), "sessao_uuid")
        caixa = self._caixa(payload.get("caixa_retaguarda_id") or payload.get("caixa")) if (payload.get("caixa_retaguarda_id") or payload.get("caixa")) else None
        obj, _ = HubSessaoCaixaRecebida.objects.get_or_create(
            hub=self.hub,
            sessao_uuid=sessao_uuid,
            defaults={
                "caixa": caixa,
                "operador": self._operador(payload.get("operador_retaguarda_usuario_id") or payload.get("operador")),
                "terminal": str(payload.get("terminal") or "")[:80],
                "aberto_em": parse_datetime(payload.get("aberto_em"), "aberto_em"),
                "fechado_em": parse_datetime(payload.get("fechado_em"), "fechado_em"),
                "valor_abertura": parse_decimal(payload.get("valor_abertura")),
                "valor_esperado": parse_decimal(payload.get("valor_esperado")),
                "valor_contado": parse_decimal(payload.get("valor_contado")),
                "diferenca": parse_decimal(payload.get("diferenca")),
                "situacao": str(payload.get("situacao") or "")[:40],
                "observacao": str(payload.get("observacao") or "")[:255],
                "snapshot": payload,
            },
        )
        return {"sessao_caixa_id": obj.pk}

    def _fechamento_dia(self, payload):
        fechamento_uuid = uuid_value(payload.get("fechamento_uuid") or payload.get("uuid"), "fechamento_uuid")
        obj, _ = HubFechamentoDiaRecebido.objects.get_or_create(
            hub=self.hub,
            fechamento_uuid=fechamento_uuid,
            defaults={
                "data_operacional": parse_required_date(payload.get("data_operacional"), "data_operacional"),
                "total_sistema": parse_decimal(payload.get("total_sistema")),
                "total_conferido": parse_decimal(payload.get("total_conferido")),
                "diferenca": parse_decimal(payload.get("diferenca")),
                "situacao": str(payload.get("situacao") or "")[:40],
                "formas_pagamento": payload.get("formas_pagamento") or [],
                "operador": self._operador(payload.get("operador_retaguarda_usuario_id") or payload.get("operador")),
                "terminal": str(payload.get("terminal") or "")[:80],
                "snapshot": payload,
            },
        )
        return {"fechamento_dia_id": obj.pk}

    def _mapeamento_existente(self, tipo, payload):
        if tipo == "CLIENTE_LOCAL" and payload.get("cliente_uuid"):
            obj = HubClienteMapeamento.objects.filter(hub=self.hub, cliente_uuid=payload.get("cliente_uuid")).first()
            return {"cliente_retaguarda_id": obj.cliente_id} if obj else {}
        if tipo == "VENDA_FINALIZADA" and payload.get("venda_uuid"):
            obj = HubVendaMapeamento.objects.filter(hub=self.hub, venda_uuid=payload.get("venda_uuid")).first()
            return {"venda_retaguarda_id": obj.venda_id, "documento": obj.documento} if obj else {}
        return {}

    def _resultado(self, evento_uuid, chave, status, mapeamento=None, mensagem=""):
        result = {
            "evento_uuid": str(evento_uuid or ""),
            "chave_idempotencia": str(chave or ""),
            "status": status,
            "mapeamento": mapeamento or {},
        }
        if mensagem:
            result["mensagem"] = mensagem
        return result


def models_q_evento(evento_uuid, chave):
    from django.db.models import Q

    return Q(evento_uuid=evento_uuid) | Q(chave_idempotencia=chave)
