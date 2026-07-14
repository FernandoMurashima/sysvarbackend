from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from accounts.permissions import HasModuleRole
from compras.models import PedidoCompraEntrega
from produto.models import Estoque, EstoqueMovimentacao, PackItem, ProdutoDetalhe

try:
    from auditoria.models import AuditLog
except Exception:
    AuditLog = None

FIN_OK = True
try:
    from financeiro.models import Pagar, PagarItem
except Exception:
    FIN_OK = False
    Pagar = PagarItem = None

from fiscal.models import NotaFiscalEntrada, NotaFiscalEntradaItem
from fiscal.serializers import NotaFiscalEntradaItemSerializer, NotaFiscalEntradaSerializer


def _q4(valor) -> Decimal:
    return Decimal(valor or 0).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _money(valor) -> Decimal:
    return Decimal(valor or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _q3(valor) -> Decimal:
    return Decimal(valor or 0).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def _audit(model_name: str, obj_id: str, changes: dict, request, action: str = "custom"):
    if not AuditLog:
        return
    try:
        AuditLog.objects.create(
            action=(action or "custom")[:20],
            app_label="fiscal",
            model=model_name,
            object_id=str(obj_id),
            changes=changes,
            user=getattr(request, "user", None),
            ip=(request.META.get("REMOTE_ADDR") or "")[:45],
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:400],
        )
    except Exception:
        pass


def _documento_nota(nota: NotaFiscalEntrada) -> str:
    partes = [nota.modelo]
    if nota.serie:
        partes.append(nota.serie)
    partes.append(nota.numero)
    return "/".join(partes)[:30]


class BaseViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModuleRole]
    read_roles = ["Admin", "Diretor", "Gerente", "AssistentePagar"]
    write_roles = ["Admin", "Diretor", "Gerente", "AssistentePagar"]


class NotaFiscalEntradaViewSet(BaseViewSet):
    queryset = (
        NotaFiscalEntrada.objects.select_related("pedido_compra", "criado_por")
        .prefetch_related("itens")
        .all()
        .order_by("-dt_entrada", "-id")
    )
    serializer_class = NotaFiscalEntradaSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        pedido = self.request.query_params.get("pedido") or self.request.query_params.get("pedido_compra")
        status_q = self.request.query_params.get("status")
        numero = self.request.query_params.get("numero")
        chave = self.request.query_params.get("chave_acesso")

        if pedido:
            qs = qs.filter(pedido_compra_id=pedido)
        if status_q:
            qs = qs.filter(status=status_q)
        if numero:
            qs = qs.filter(numero__icontains=numero)
        if chave:
            qs = qs.filter(chave_acesso__icontains=chave)
        return qs

    @action(detail=True, methods=["post"], url_path="fechar")
    @transaction.atomic
    def fechar(self, request, pk=None):
        nota = self.get_object()
        if nota.status != NotaFiscalEntrada.Status.ABERTA:
            return Response({"detail": "Somente notas abertas podem ser fechadas."}, status=status.HTTP_400_BAD_REQUEST)
        if not nota.itens.exists():
            return Response({"detail": "Inclua ao menos um item antes de fechar a nota."}, status=status.HTTP_400_BAD_REQUEST)

        nota.recalcular_totais()
        before = nota.status
        nota.status = NotaFiscalEntrada.Status.FECHADA
        nota.save(update_fields=["status", "atualizado_em"])
        custos_produtos = self._atualizar_custos_produtos_nao_revenda(nota)

        try:
            estoque = self._movimentar_estoque_entrada(nota)
        except ValueError as exc:
            transaction.set_rollback(True)
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        financeiro = self._vincular_financeiro(nota)
        recebimento = self._atualizar_recebimento_pedido(nota)
        _audit("notafiscalentrada", nota.pk, {"status": [before, nota.status]}, request, action="fechar")
        data = self.get_serializer(nota).data
        data["financeiro"] = financeiro
        data["estoque"] = estoque
        data["custos_produtos"] = custos_produtos
        data["recebimento_pedido"] = recebimento
        return Response(data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="cancelar")
    @transaction.atomic
    def cancelar(self, request, pk=None):
        nota = self.get_object()
        if nota.status == NotaFiscalEntrada.Status.CANCELADA:
            return Response(self.get_serializer(nota).data, status=status.HTTP_200_OK)

        before = nota.status
        estoque = {"disponivel": True, "movimentos": 0}
        if nota.status == NotaFiscalEntrada.Status.FECHADA:
            try:
                estoque = self._movimentar_estoque_cancelamento(nota)
            except ValueError as exc:
                transaction.set_rollback(True)
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        nota.status = NotaFiscalEntrada.Status.CANCELADA
        nota.save(update_fields=["status", "atualizado_em"])
        recebimento = self._atualizar_recebimento_pedido(nota)
        _audit("notafiscalentrada", nota.pk, {"status": [before, nota.status]}, request, action="cancelar")
        data = self.get_serializer(nota).data
        data["estoque"] = estoque
        data["recebimento_pedido"] = recebimento
        return Response(data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="itens-pedido")
    def itens_pedido(self, request, pk=None):
        nota = self.get_object()
        itens_nota = {item.pedido_item_id: item for item in nota.itens.all()}
        payload = []

        for pedido_item in nota.pedido_compra.itens.all().order_by("id"):
            qtd_outras_notas = sum(
                Decimal(item.qtd_recebida or 0)
                for item in NotaFiscalEntradaItem.objects.filter(
                    pedido_item=pedido_item,
                    nota__pedido_compra_id=nota.pedido_compra_id,
                )
                .exclude(nota_id=nota.pk)
                .exclude(nota__status=NotaFiscalEntrada.Status.CANCELADA)
            )
            item_nota = itens_nota.get(pedido_item.pk)
            qtd_na_nota = Decimal(item_nota.qtd_recebida or 0) if item_nota else Decimal("0")
            qtd_pedido = Decimal(pedido_item.qtd or 0)
            saldo_total = qtd_pedido - qtd_outras_notas
            saldo_pendente = saldo_total - qtd_na_nota
            pack_itens = list(PackItem.objects.filter(pack_id=pedido_item.pack_id)) if pedido_item.pack_id else []
            qtd_pack = sum(int(item.qtd or 0) for item in pack_itens)
            quantidades_validas = []
            if qtd_pack and pedido_item.n_packs:
                quantidades_validas = [
                    str(Decimal(qtd_pack * pack_num))
                    for pack_num in range(1, int(pedido_item.n_packs or 0) + 1)
                    if Decimal(qtd_pack * pack_num) <= saldo_total
                ]

            payload.append(
                {
                    "pedido_item": pedido_item.pk,
                    "nota_item": getattr(item_nota, "pk", None),
                    "produto": getattr(pedido_item, "produto_id", None),
                    "produto_descricao": getattr(getattr(pedido_item, "produto", None), "descricao", None),
                    "produto_referencia": getattr(getattr(pedido_item, "produto", None), "referencia", None),
                    "cor": getattr(pedido_item, "cor_id", None),
                    "cor_nome": getattr(getattr(pedido_item, "cor", None), "Descricao", None),
                    "pack": getattr(pedido_item, "pack_id", None),
                    "pack_nome": getattr(getattr(pedido_item, "pack", None), "nome", None),
                    "descricao_livre": pedido_item.descricao_livre,
                    "qtd_pedido": str(qtd_pedido),
                    "qtd_recebida_outras_notas": str(qtd_outras_notas),
                    "qtd_na_nota": str(qtd_na_nota),
                    "saldo_total_recebivel": str(saldo_total),
                    "saldo_pendente": str(saldo_pendente),
                    "preco_unit_pedido": str(pedido_item.preco_unit),
                    "qtd_pack": str(qtd_pack or ""),
                    "n_packs": pedido_item.n_packs or 0,
                    "quantidades_validas": quantidades_validas,
                }
            )

        return Response(payload, status=status.HTTP_200_OK)

    def _documento_estoque(self, nota):
        return str(nota.numero or nota.pk).strip()

    def _codigo_estoque_produto(self, produto):
        referencia_numerica = ''.join(ch for ch in str(produto.referencia or '') if ch.isdigit())
        if len(referencia_numerica) == 13:
            return referencia_numerica
        return f"29{int(produto.pk) % 100000000000:011d}"

    def _qtd_recebida_item(self, pedido_item):
        itens = NotaFiscalEntradaItem.objects.filter(
            pedido_item=pedido_item,
            nota__pedido_compra_id=pedido_item.pedido_id,
            nota__status=NotaFiscalEntrada.Status.FECHADA,
        )
        return sum(Decimal(item.qtd_recebida or 0) for item in itens)

    def _atualizar_recebimento_pedido(self, nota):
        pedido = nota.pedido_compra
        itens = list(pedido.itens.all().order_by("id"))
        if not itens:
            return {"status_pedido": pedido.status, "itens_atualizados": 0}

        atendidos = 0
        parciais = 0
        atualizados = 0
        for item in itens:
            prevista = Decimal(item.qtd or 0)
            recebida = self._qtd_recebida_item(item)
            entrega = item.entregas.order_by("id").first()
            if not entrega:
                entrega = PedidoCompraEntrega(item=item, qtd_prevista=prevista, data_prevista=pedido.previsao_entrega)

            entrega.qtd_prevista = prevista
            entrega.qtd_recebida = recebida
            if prevista > 0 and recebida >= prevista:
                entrega.status = "RECB"
                entrega.data_recebida = nota.dt_entrada
                atendidos += 1
            elif recebida > 0:
                entrega.status = "PARC"
                entrega.data_recebida = None
                parciais += 1
            else:
                entrega.status = "PREV"
                entrega.data_recebida = None
            entrega.save()
            atualizados += 1

        novo_status = "AT" if atendidos == len(itens) else "AP"
        if pedido.status != novo_status and pedido.status != "CA":
            pedido.status = novo_status
            pedido.save(update_fields=["status"])

        return {
            "status_pedido": pedido.status,
            "itens_atualizados": atualizados,
            "itens_atendidos": atendidos,
            "itens_parciais": parciais,
        }

    def _movimentar_estoque_entrada(self, nota):
        documento = self._documento_estoque(nota)
        if EstoqueMovimentacao.objects.filter(documento=documento, tipo=EstoqueMovimentacao.TIPO_ENTRADA).exists():
            return {"disponivel": True, "movimentos": 0, "ja_movimentada": True}

        movimentos = 0
        for item_nf in nota.itens.select_related("pedido_item", "pedido_item__produto", "pedido_item__cor", "pedido_item__pack"):
            if nota.pedido_compra.tipo == "1":
                movimentos += self._movimentar_item_estoque(
                    nota=nota,
                    item_nf=item_nf,
                    tipo=EstoqueMovimentacao.TIPO_ENTRADA,
                    documento=documento,
                    sinal=1,
                )
            else:
                movimentos += self._movimentar_item_estoque_nao_revenda(
                    nota=nota,
                    item_nf=item_nf,
                    tipo=EstoqueMovimentacao.TIPO_ENTRADA,
                    documento=documento,
                    sinal=1,
                )

        return {"disponivel": True, "movimentos": movimentos}

    def _movimentar_estoque_cancelamento(self, nota):
        documento = f"{self._documento_estoque(nota)}:CANCEL"
        if EstoqueMovimentacao.objects.filter(documento=documento, tipo=EstoqueMovimentacao.TIPO_SAIDA).exists():
            return {"disponivel": True, "movimentos": 0, "ja_movimentada": True}

        movimentos = 0
        for item_nf in nota.itens.select_related("pedido_item", "pedido_item__produto", "pedido_item__cor", "pedido_item__pack"):
            if nota.pedido_compra.tipo == "1":
                movimentos += self._movimentar_item_estoque(
                    nota=nota,
                    item_nf=item_nf,
                    tipo=EstoqueMovimentacao.TIPO_SAIDA,
                    documento=documento,
                    sinal=-1,
                )
            else:
                movimentos += self._movimentar_item_estoque_nao_revenda(
                    nota=nota,
                    item_nf=item_nf,
                    tipo=EstoqueMovimentacao.TIPO_SAIDA,
                    documento=documento,
                    sinal=-1,
                )

        return {"disponivel": True, "movimentos": movimentos}

    def _movimentar_item_estoque_nao_revenda(self, nota, item_nf, tipo, documento, sinal):
        pedido_item = item_nf.pedido_item
        produto = pedido_item.produto if pedido_item else None
        if not produto or produto.tipo_produto not in ("2", "4"):
            return 0

        qtd = _q3(item_nf.qtd_recebida or 0)
        if qtd <= 0:
            return 0

        codigo = self._codigo_estoque_produto(produto)
        custo_movimento = _q4(
            item_nf.preco_unit_nf
            or produto.custo_medio
            or produto.custo_ultima_compra
            or produto.custo_original
            or 0
        )
        estoque, _ = Estoque.objects.select_for_update().get_or_create(
            CodigodeBarra=codigo,
            Idloja=nota.pedido_compra.loja,
            defaults={"referencia": produto.referencia or "", "Estoque": 0, "reserva": 0},
        )
        anterior = Decimal(estoque.Estoque or 0)
        posterior = anterior + (qtd * Decimal(sinal))
        if posterior < 0 and (nota.pedido_compra.loja.EstoqueNegativo or "NAO").upper() != "SIM":
            raise ValueError(
                f"Saldo insuficiente do produto {produto.descricao} para cancelar/movimentar a nota."
            )

        estoque.referencia = produto.referencia or estoque.referencia
        estoque.Estoque = posterior
        estoque.reserva = estoque.reserva or 0
        estoque.save(update_fields=["referencia", "Estoque", "reserva"])

        EstoqueMovimentacao.objects.create(
            Idloja=nota.pedido_compra.loja,
            CodigodeBarra=codigo,
            referencia=produto.referencia or "",
            tipo=tipo,
            quantidade=qtd,
            custo_unitario=custo_movimento,
            custo_total=_money(qtd * custo_movimento),
            custo_medio_apos=_q4(produto.custo_medio or produto.custo_ultima_compra or produto.custo_original or custo_movimento),
            saldo_anterior=anterior,
            saldo_posterior=posterior,
            documento=documento,
            observacao=f"Nota fiscal de entrada {nota.numero}",
        )
        return 1

    def _atualizar_custos_produtos_nao_revenda(self, nota):
        atualizados = 0
        for item_nf in nota.itens.select_related("pedido_item", "pedido_item__produto"):
            pedido_item = item_nf.pedido_item
            produto = pedido_item.produto if pedido_item else None
            if not produto or produto.tipo_produto not in ("2", "4"):
                continue

            qtd_recebida = Decimal(item_nf.qtd_recebida or 0)
            if qtd_recebida <= 0:
                continue

            total_liquido = Decimal(item_nf.total_item or 0)
            custo_entrada = _q4((total_liquido / qtd_recebida) if total_liquido > 0 else item_nf.preco_unit_nf)
            if custo_entrada <= 0:
                continue

            if not Decimal(produto.custo_original or 0):
                produto.custo_original = custo_entrada
            produto.custo_ultima_compra = custo_entrada
            produto.custo_medio = custo_entrada
            produto.save(update_fields=["custo_original", "custo_ultima_compra", "custo_medio"])
            atualizados += 1

        return {"atualizados": atualizados}

    def _movimentar_item_estoque(self, nota, item_nf, tipo, documento, sinal):
        pedido_item = item_nf.pedido_item
        if not pedido_item.produto_id or not pedido_item.cor_id or not pedido_item.pack_id:
            raise ValueError("Item de revenda sem produto, cor ou pack para movimentar estoque.")

        qtd_recebida = Decimal(item_nf.qtd_recebida or 0)
        qtd_pedido = Decimal(pedido_item.qtd or 0)
        if qtd_recebida <= 0:
            return 0
        if qtd_pedido <= 0:
            raise ValueError("Item do pedido sem quantidade calculada para movimentar estoque.")

        fator_recebido = qtd_recebida / qtd_pedido
        pack_itens = list(PackItem.objects.select_related("tamanho").filter(pack_id=pedido_item.pack_id))
        if not pack_itens:
            raise ValueError("Pack do item não possui tamanhos configurados.")

        custo_entrada = _q4(item_nf.preco_unit_nf or 0)
        movimentos = 0
        for pack_item in pack_itens:
            qtd_decimal = Decimal(pack_item.qtd or 0) * Decimal(pedido_item.n_packs or 0) * fator_recebido
            if qtd_decimal != qtd_decimal.to_integral_value():
                qtd_pack = sum(int(item.qtd or 0) for item in pack_itens)
                validas = [
                    str(qtd_pack * pack_num)
                    for pack_num in range(1, int(pedido_item.n_packs or 0) + 1)
                ] if qtd_pack and pedido_item.n_packs else []
                produto = getattr(pedido_item.produto, "descricao", f"produto {pedido_item.produto_id}")
                complemento = f" Quantidades válidas para este item: {', '.join(validas)}." if validas else ""
                raise ValueError(
                    f"Item {pedido_item.pk} ({produto}): a quantidade recebida {qtd_recebida} não fecha com a composição do pack."
                    f" O pedido tem {qtd_pedido} peça(s) neste item.{complemento}"
                )

            qtd = int(qtd_decimal)
            if qtd <= 0:
                continue

            sku = ProdutoDetalhe.objects.select_related("produto").filter(
                produto_id=pedido_item.produto_id,
                idcor_id=pedido_item.cor_id,
                idtamanho_id=pack_item.tamanho_id,
            ).first()
            if not sku:
                raise ValueError(
                    f"SKU não encontrado para produto {pedido_item.produto_id}, cor {pedido_item.cor_id}, tamanho {pack_item.tamanho_id}."
                )
            estoque, _ = Estoque.objects.select_for_update().get_or_create(
                CodigodeBarra=sku.ean13,
                Idloja=nota.pedido_compra.loja,
                defaults={"referencia": sku.produto.referencia or "", "Estoque": 0, "reserva": 0},
            )
            anterior = estoque.Estoque or 0
            posterior = anterior + (qtd * sinal)
            custo_movimento = self._custo_movimento_sku(sku, custo_entrada)
            custo_medio_apos = _q4(sku.custo_medio or sku.custo_ultima_compra or sku.custo_original or custo_movimento)
            if sinal > 0:
                custo_medio_apos = self._atualizar_custo_medio_sku(sku, anterior, qtd, custo_entrada)
            estoque.referencia = sku.produto.referencia or estoque.referencia
            estoque.Estoque = posterior
            estoque.reserva = estoque.reserva or 0
            estoque.save(update_fields=["referencia", "Estoque", "reserva"])

            EstoqueMovimentacao.objects.create(
                Idloja=nota.pedido_compra.loja,
                CodigodeBarra=sku.ean13,
                referencia=sku.produto.referencia or "",
                tipo=tipo,
                quantidade=qtd,
                custo_unitario=custo_movimento,
                custo_total=_money(Decimal(qtd) * custo_movimento),
                custo_medio_apos=custo_medio_apos,
                saldo_anterior=anterior,
                saldo_posterior=posterior,
                documento=documento,
                observacao=f"Nota fiscal de entrada {nota.numero}",
            )
            movimentos += 1

        return movimentos

    def _custo_movimento_sku(self, sku, custo_entrada: Decimal) -> Decimal:
        if custo_entrada > 0:
            return custo_entrada
        return _q4(sku.custo_medio or sku.custo_ultima_compra or sku.custo_original or 0)

    def _atualizar_custo_medio_sku(self, sku, saldo_anterior: int, quantidade: int, custo_entrada: Decimal) -> Decimal:
        custo_entrada = _q4(custo_entrada)
        custo_atual = _q4(sku.custo_medio or sku.custo_ultima_compra or sku.custo_original or 0)
        if custo_entrada <= 0:
            return custo_atual

        saldo_anterior_dec = Decimal(max(int(saldo_anterior or 0), 0))
        quantidade_dec = Decimal(max(int(quantidade or 0), 0))
        saldo_posterior = saldo_anterior_dec + quantidade_dec
        if saldo_posterior <= 0:
            custo_medio = custo_entrada
        else:
            custo_medio = ((saldo_anterior_dec * custo_atual) + (quantidade_dec * custo_entrada)) / saldo_posterior

        sku.custo_ultima_compra = custo_entrada
        if not Decimal(sku.custo_original or 0):
            sku.custo_original = custo_entrada
        sku.custo_medio = _q4(custo_medio)
        sku.save(update_fields=["custo_original", "custo_ultima_compra", "custo_medio"])
        return sku.custo_medio

    def _vincular_financeiro(self, nota):
        if not FIN_OK:
            return {"disponivel": False, "titulos_atualizados": 0, "parcelas_efetivadas": 0}

        titulos = Pagar.objects.filter(pedido_compra=nota.pedido_compra_id).filter(nfe_id__isnull=True, Previsao=True)
        documento = _documento_nota(nota)
        valor_nota = _money(nota.valor_total or 0)
        titulos_atualizados = 0
        titulos_criados = 0
        parcelas_efetivadas = 0
        previsoes_ajustadas = 0

        for titulo in titulos.order_by("Idpagar"):
            parcelas_previstas = list(
                PagarItem.objects.filter(Idpagar=titulo)
                .exclude(status=PagarItem.STATUS_CANCELADO)
                .order_by("parcela_n")
            )
            valor_previsao = _money(sum(Decimal(p.valor_parcela or 0) for p in parcelas_previstas))
            if valor_previsao <= 0 or valor_nota <= 0:
                continue

            if valor_nota >= valor_previsao - Decimal("0.01"):
                self._redistribuir_parcelas(parcelas_previstas, valor_nota)
                titulo.nfe_id = nota.pk
                titulo.Titulo = str(nota.numero)[:60]
                titulo.Documento = documento
                titulo.Data_emissao = nota.dt_emissao
                titulo.Valor_total = valor_nota
                titulo.Previsao = False
                titulo.save(update_fields=["nfe_id", "Titulo", "Documento", "Data_emissao", "Valor_total", "Previsao"])
                parcelas_efetivadas += PagarItem.objects.filter(Idpagar=titulo).exclude(
                    status=PagarItem.STATUS_CANCELADO
                ).update(status=PagarItem.STATUS_EFETIVO, Previsao=False)
                titulos_atualizados += 1
                valor_nota = Decimal("0.00")
                break

            titulo_nf = Pagar.objects.create(
                empresa=titulo.empresa,
                idloja=titulo.idloja,
                idfornecedor=titulo.idfornecedor,
                Titulo=str(nota.numero)[:60],
                Documento=documento,
                Data_emissao=nota.dt_emissao,
                Valor_total=valor_nota,
                Previsao=False,
                FormaPagamento=titulo.FormaPagamento,
                Idnatureza=titulo.Idnatureza,
                conta_contabil=titulo.conta_contabil,
                pedido_compra=nota.pedido_compra_id,
                nfe_id=nota.pk,
            )
            self._criar_parcelas_proporcionais(titulo_nf, parcelas_previstas, valor_previsao, valor_nota, efetivo=True)
            titulos_criados += 1
            parcelas_efetivadas += len(parcelas_previstas)

            saldo_previsao = _money(valor_previsao - valor_nota)
            self._redistribuir_parcelas(parcelas_previstas, saldo_previsao)
            titulo.Valor_total = saldo_previsao
            titulo.Titulo = f"PC {nota.pedido_compra_id} - saldo"[:60]
            titulo.save(update_fields=["Valor_total", "Titulo"])
            previsoes_ajustadas += 1
            valor_nota = Decimal("0.00")
            break

        return {
            "disponivel": True,
            "titulos_atualizados": titulos_atualizados,
            "titulos_criados": titulos_criados,
            "parcelas_efetivadas": parcelas_efetivadas,
            "previsoes_ajustadas": previsoes_ajustadas,
        }

    def _criar_parcelas_proporcionais(self, titulo, parcelas_base, total_base, total_destino, efetivo=False):
        restante = _money(total_destino)
        total_base = _money(total_base)
        total_parcelas = len(parcelas_base)
        for idx, base in enumerate(parcelas_base, start=1):
            if idx == total_parcelas:
                valor = restante
            else:
                proporcao = Decimal(base.valor_parcela or 0) / total_base if total_base else Decimal("0")
                valor = _money(total_destino * proporcao)
                restante = _money(restante - valor)
            PagarItem.objects.create(
                Idpagar=titulo,
                parcela_n=base.parcela_n,
                status=PagarItem.STATUS_EFETIVO if efetivo else PagarItem.STATUS_PREVISTO,
                Data_vencimento=base.Data_vencimento,
                valor_parcela=valor,
                FormaPagamento=base.FormaPagamento,
                idconta=base.idconta,
                juros=0,
                multa=0,
                tarifa=0,
                desconto=0,
                data_baixa=None,
                valor_baixa=None,
                Previsao=not efetivo,
                Idnatureza=base.Idnatureza,
            )

    def _redistribuir_parcelas(self, parcelas, total_destino):
        total_atual = _money(sum(Decimal(p.valor_parcela or 0) for p in parcelas))
        restante = _money(total_destino)
        total_parcelas = len(parcelas)
        for idx, parcela in enumerate(parcelas, start=1):
            if idx == total_parcelas:
                valor = restante
            else:
                proporcao = Decimal(parcela.valor_parcela or 0) / total_atual if total_atual else Decimal("0")
                valor = _money(total_destino * proporcao)
                restante = _money(restante - valor)
            parcela.valor_parcela = valor
            parcela.save(update_fields=["valor_parcela"])


class NotaFiscalEntradaItemViewSet(BaseViewSet):
    queryset = NotaFiscalEntradaItem.objects.select_related("nota", "pedido_item").all().order_by("nota_id", "id")
    serializer_class = NotaFiscalEntradaItemSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        nota = self.request.query_params.get("nota")
        pedido = self.request.query_params.get("pedido") or self.request.query_params.get("pedido_compra")
        pedido_item = self.request.query_params.get("pedido_item")

        if nota:
            qs = qs.filter(nota_id=nota)
        if pedido:
            qs = qs.filter(nota__pedido_compra_id=pedido)
        if pedido_item:
            qs = qs.filter(pedido_item_id=pedido_item)
        return qs

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        nota = instance.nota
        if nota.status != NotaFiscalEntrada.Status.ABERTA:
            return Response({"detail": "Somente notas abertas podem receber alterações."}, status=status.HTTP_400_BAD_REQUEST)

        response = super().destroy(request, *args, **kwargs)
        nota.recalcular_totais()
        return response
