from decimal import Decimal
from collections import defaultdict
from random import randint
from typing import Dict, List

from django.db import models, transaction
from django.db.models import Max
from django.utils import timezone
from datetime import datetime, time, timedelta
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from accounts.permissions import HasModuleRole

from cadastros.models import Nat_Lancamento
from financeiro.models import (
    Caixa,
    CashbackConfig,
    CashbackMovimento,
    MovimentacaoFinanceira,
    Receber,
    ReceberItem,
    ValeTroca,
    ValeTrocaMovimento,
    saldo_cashback_cliente,
    saldo_vale_troca_cliente,
)
from fiscal.models import NFCe, NFeDevolucao, VendaDevolucao, VendaDevolucaoItem, VendaPdv, VendaPdvItem, VendaPdvPagamento
from fiscal.models.venda_pdv import money
from fiscal.serializers import NFCeSerializer, VendaDevolucaoSerializer, VendaPdvSerializer
from produto.models import Estoque, EstoqueMovimentacao, Produto, ProdutoDetalhe
from cadastros.models import Funcionarios


UF_CODIGO = {
    "RO": "11", "AC": "12", "AM": "13", "RR": "14", "PA": "15", "AP": "16", "TO": "17",
    "MA": "21", "PI": "22", "CE": "23", "RN": "24", "PB": "25", "PE": "26", "AL": "27",
    "SE": "28", "BA": "29", "MG": "31", "ES": "32", "RJ": "33", "SP": "35", "PR": "41",
    "SC": "42", "RS": "43", "MS": "50", "MT": "51", "GO": "52", "DF": "53",
}


def _digito_chave(base43: str) -> str:
    pesos = [2, 3, 4, 5, 6, 7, 8, 9]
    total = 0
    for idx, digit in enumerate(reversed(base43)):
        total += int(digit) * pesos[idx % len(pesos)]
    resto = total % 11
    dv = 11 - resto
    return "0" if dv >= 10 else str(dv)


def _limpar_numero(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _proximo_numero_nfce() -> int:
    atual = NFCe.objects.aggregate(max_numero=Max("numero")).get("max_numero") or 0
    return atual + 1


def _proximo_numero_nfe_devolucao() -> int:
    atual = NFeDevolucao.objects.aggregate(max_numero=Max("numero")).get("max_numero") or 0
    return atual + 1


def _gerar_chave(nfce: NFCe) -> str:
    loja = nfce.venda.loja
    cuf = UF_CODIGO.get((loja.estado or "SP").upper(), "35")
    aamm = timezone.localdate().strftime("%y%m")
    cnpj = _limpar_numero(loja.cnpj).zfill(14)[-14:]
    modelo = nfce.modelo
    serie = str(nfce.serie).zfill(3)
    numero = str(nfce.numero).zfill(9)
    tp_emis = "1"
    codigo = str(randint(1, 99999999)).zfill(8)
    base = f"{cuf}{aamm}{cnpj}{modelo}{serie}{numero}{tp_emis}{codigo}"
    return f"{base}{_digito_chave(base)}"


class VendaPdvViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModuleRole]
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa", "Vendedor"]
    write_roles = ["Admin", "Diretor", "Gerente", "Caixa"]
    action_roles = {"relatorio_vendas": ["Admin", "Diretor", "Gerente"]}
    serializer_class = VendaPdvSerializer
    queryset = (
        VendaPdv.objects.select_related("loja", "caixa", "cliente", "vendedor", "criado_por")
        .prefetch_related("itens", "pagamentos")
        .all()
    )

    def get_queryset(self):
        qs = super().get_queryset()
        loja = self.request.query_params.get("loja")
        cliente = self.request.query_params.get("cliente")
        status_q = self.request.query_params.get("status")
        if loja:
            qs = qs.filter(loja_id=loja)
        if cliente:
            qs = qs.filter(cliente_id=cliente)
        if status_q:
            qs = qs.filter(status=status_q)
        return qs

    @action(detail=False, methods=["get"], url_path="relatorio-vendas")
    def relatorio_vendas(self, request):
        vendas = self._vendas_relatorio(request)
        vendedores = defaultdict(lambda: {
            "vendedor": "",
            "comissao_percentual": Decimal("0"),
            "vendas": 0,
            "itens": 0,
            "total": Decimal("0"),
        })
        lojas = defaultdict(lambda: {"loja": "", "vendas": 0, "itens": 0, "total": Decimal("0")})
        pagamentos = defaultdict(lambda: {"forma": "", "descricao": "", "vendas": 0, "total": Decimal("0")})
        produtos = defaultdict(lambda: {"produto": "", "referencia": "", "quantidade": 0, "total": Decimal("0")})
        colecoes = defaultdict(lambda: {"colecao": "Sem coleção", "quantidade": 0, "total": Decimal("0")})
        grupos = defaultdict(lambda: {"grupo": "Sem grupo", "quantidade": 0, "total": Decimal("0")})
        subgrupos = defaultdict(lambda: {"subgrupo": "Sem subgrupo", "quantidade": 0, "total": Decimal("0")})
        total_vendas = Decimal("0")
        total_subtotal = Decimal("0")
        total_descontos = Decimal("0")
        total_itens = 0
        cashback_gerado = Decimal("0")
        cashback_usado = Decimal("0")

        for venda in vendas:
            venda_total = money(venda.total)
            total_vendas += venda_total
            total_subtotal += money(venda.subtotal)
            total_descontos += money(venda.desconto_itens + venda.desconto_geral)
            cashback_gerado += money(sum((mov.valor for mov in venda.cashback_creditos.all()), Decimal("0.00")))
            cashback_usado += money(sum((mov.valor for mov in venda.cashback_usos.all()), Decimal("0.00")))

            loja = lojas[venda.loja_id]
            loja["loja"] = venda.loja.nome_loja
            loja["vendas"] += 1
            loja["total"] += venda_total

            vendedor = vendedores[venda.vendedor_id]
            vendedor["vendedor"] = venda.vendedor.nomefuncionario
            vendedor["comissao_percentual"] = Decimal(getattr(venda.vendedor, "comissao_percentual", 0) or 0)
            vendedor["vendas"] += 1
            vendedor["total"] += venda_total

            formas_venda = set()
            for pagamento in venda.pagamentos.all():
                forma = pagamentos[pagamento.forma]
                forma["forma"] = pagamento.forma
                forma["descricao"] = pagamento.descricao or pagamento.forma
                forma["total"] += money(pagamento.valor)
                formas_venda.add(pagamento.forma)
            for forma_codigo in formas_venda:
                pagamentos[forma_codigo]["vendas"] += 1

            for item in venda.itens.all():
                quantidade = int(item.quantidade or 0)
                total_item = money(item.total_item)
                total_itens += quantidade
                vendedor["itens"] += quantidade
                loja["itens"] += quantidade

                colecao_nome = getattr(getattr(item.produto, "colecao", None), "Descricao", None) or "Sem coleção"
                grupo_nome = getattr(getattr(item.produto, "grupo", None), "Descricao", None) or "Sem grupo"
                subgrupo_nome = getattr(getattr(item.produto, "subgrupo", None), "Descricao", None) or "Sem subgrupo"

                produto = produtos[item.produto_id]
                produto["produto"] = item.descricao
                produto["referencia"] = item.referencia
                produto["colecao"] = colecao_nome
                produto["grupo"] = grupo_nome
                produto["subgrupo"] = subgrupo_nome
                produto["quantidade"] += quantidade
                produto["total"] += total_item

                colecao = colecoes[colecao_nome]
                colecao["colecao"] = colecao_nome
                colecao["quantidade"] += quantidade
                colecao["total"] += total_item

                grupo = grupos[grupo_nome]
                grupo["grupo"] = grupo_nome
                grupo["quantidade"] += quantidade
                grupo["total"] += total_item

                subgrupo = subgrupos[f"{grupo_nome}::{subgrupo_nome}"]
                subgrupo["grupo"] = grupo_nome
                subgrupo["subgrupo"] = subgrupo_nome
                subgrupo["quantidade"] += quantidade
                subgrupo["total"] += total_item

        vendedores_payload = []
        for row in vendedores.values():
            row_total = money(row["total"])
            percentual = Decimal(row["comissao_percentual"] or 0)
            vendedores_payload.append({
                "vendedor": row["vendedor"],
                "vendas": row["vendas"],
                "itens": row["itens"],
                "total": str(row_total),
                "ticket_medio": str(money(row_total / row["vendas"] if row["vendas"] else 0)),
                "comissao_percentual": str(percentual),
                "comissao": str(money(row_total * percentual / Decimal("100"))),
            })

        lojas_payload = [
            {**row, "total": str(money(row["total"])), "ticket_medio": str(money(row["total"] / row["vendas"] if row["vendas"] else 0))}
            for row in lojas.values()
        ]
        pagamentos_payload = [
            {**row, "total": str(money(row["total"]))}
            for row in pagamentos.values()
        ]
        produtos_payload = [
            {**row, "total": str(money(row["total"]))}
            for row in produtos.values()
        ]
        colecoes_payload = [
            {**row, "total": str(money(row["total"]))}
            for row in colecoes.values()
        ]
        grupos_payload = [
            {**row, "total": str(money(row["total"]))}
            for row in grupos.values()
        ]
        subgrupos_payload = [
            {**row, "total": str(money(row["total"]))}
            for row in subgrupos.values()
        ]

        lojas_payload.sort(key=lambda item: Decimal(item["total"]), reverse=True)
        vendedores_payload.sort(key=lambda item: Decimal(item["total"]), reverse=True)
        pagamentos_payload.sort(key=lambda item: Decimal(item["total"]), reverse=True)
        produtos_payload.sort(key=lambda item: Decimal(item["total"]), reverse=True)
        colecoes_payload.sort(key=lambda item: Decimal(item["total"]), reverse=True)
        grupos_payload.sort(key=lambda item: Decimal(item["total"]), reverse=True)
        subgrupos_payload.sort(key=lambda item: Decimal(item["total"]), reverse=True)
        comissao_total = sum(Decimal(row["comissao"]) for row in vendedores_payload)

        return Response({
            "resumo": {
                "vendas": len(vendas),
                "itens": total_itens,
                "subtotal": str(money(total_subtotal)),
                "descontos": str(money(total_descontos)),
                "total": str(money(total_vendas)),
                "ticket_medio": str(money(total_vendas / len(vendas) if vendas else 0)),
                "comissao_total": str(money(comissao_total)),
                "cashback_gerado": str(money(cashback_gerado)),
                "cashback_usado": str(money(cashback_usado)),
            },
            "lojas": lojas_payload,
            "vendedores": vendedores_payload,
            "pagamentos": pagamentos_payload,
            "produtos": produtos_payload[:30],
            "colecoes": colecoes_payload,
            "grupos": grupos_payload,
            "subgrupos": subgrupos_payload,
        })

    def _vendas_relatorio(self, request):
        qs = (
            VendaPdv.objects.select_related("loja", "vendedor")
            .prefetch_related(
                "pagamentos",
                "cashback_creditos",
                "cashback_usos",
                "itens__produto__colecao",
                "itens__produto__grupo",
                "itens__produto__subgrupo",
            )
            .filter(status=VendaPdv.Status.FINALIZADA)
            .order_by("-data_venda")
        )
        loja = request.query_params.get("loja")
        data_ini = request.query_params.get("data_ini")
        data_fim = request.query_params.get("data_fim")
        vendedor = request.query_params.get("vendedor")
        if loja:
            qs = qs.filter(loja_id=loja)
        if vendedor:
            qs = qs.filter(vendedor_id=vendedor)
        if data_ini:
            qs = qs.filter(data_venda__gte=self._datetime_inicio_dia(data_ini))
        if data_fim:
            qs = qs.filter(data_venda__lte=self._datetime_fim_dia(data_fim))
        return list(qs)

    def _datetime_inicio_dia(self, date_text: str):
        dt = datetime.combine(datetime.strptime(date_text, "%Y-%m-%d").date(), time.min)
        return timezone.make_aware(dt, timezone.get_current_timezone()) if timezone.is_naive(dt) else dt

    def _datetime_fim_dia(self, date_text: str):
        dt = datetime.combine(datetime.strptime(date_text, "%Y-%m-%d").date(), time.max)
        return timezone.make_aware(dt, timezone.get_current_timezone()) if timezone.is_naive(dt) else dt

    @action(detail=False, methods=["post"], url_path="finalizar")
    @transaction.atomic
    def finalizar(self, request):
        data = request.data
        itens = data.get("itens") or []
        if not itens:
            return Response({"detail": "Inclua ao menos um item na venda."}, status=status.HTTP_400_BAD_REQUEST)

        loja_id = data.get("loja")
        caixa_id = data.get("caixa")
        cliente_id = data.get("cliente")
        vendedor_id = data.get("vendedor")
        forma_pagamento = data.get("forma_pagamento") or "AV"
        desconto_geral = money(data.get("desconto_geral"))
        valor_recebido = money(data.get("valor_recebido"))
        pagamentos_payload = self._normalizar_pagamentos(data)

        if not loja_id or not caixa_id or not cliente_id or not vendedor_id:
            return Response({"detail": "Informe loja, caixa, cliente e vendedor."}, status=status.HTTP_400_BAD_REQUEST)

        caixa = (
            Caixa.objects.select_for_update()
            .filter(pk=caixa_id, idloja_id=loja_id, ativo=True, tipo_caixa=Caixa.TIPO_LOJA)
            .first()
        )
        if not caixa:
            return Response({"detail": "O caixa informado não pertence à loja ou não está ativo."}, status=status.HTTP_400_BAD_REQUEST)
        vendedor = (
            Funcionarios.objects
            .filter(pk=vendedor_id, idloja_id=loja_id, ativo=True, categoria__iexact="Vendedor")
            .first()
        )
        if not vendedor:
            return Response({"detail": "O vendedor informado não está vinculado a esta loja."}, status=status.HTTP_400_BAD_REQUEST)

        documento = data.get("documento") or f"PDV-{timezone.now().strftime('%Y%m%d%H%M%S%f')}"
        venda = VendaPdv.objects.create(
            loja_id=loja_id,
            caixa=caixa,
            cliente_id=cliente_id,
            vendedor=vendedor,
            documento=documento,
            forma_pagamento=forma_pagamento,
            desconto_geral=desconto_geral,
            valor_recebido=valor_recebido,
            criado_por=request.user if request.user.is_authenticated else None,
        )

        subtotal = Decimal("0")
        desconto_itens = Decimal("0")
        try:
            for item in itens:
                venda_item = self._registrar_item(venda, item)
                subtotal += money(Decimal(venda_item.quantidade) * Decimal(venda_item.preco_unitario))
                desconto_itens += money(venda_item.desconto)
        except (ProdutoDetalhe.DoesNotExist, Estoque.DoesNotExist):
            transaction.set_rollback(True)
            return Response({"detail": "Produto sem SKU ou saldo de estoque para a loja."}, status=status.HTTP_400_BAD_REQUEST)
        except ValueError as exc:
            transaction.set_rollback(True)
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        total = money(subtotal - desconto_itens - desconto_geral)
        total_pago = money(sum((pagamento["valor"] for pagamento in pagamentos_payload), Decimal("0")))
        if total_pago <= 0:
            transaction.set_rollback(True)
            return Response({"detail": "Informe o valor pago pelo cliente."}, status=status.HTTP_400_BAD_REQUEST)
        if total_pago < total:
            transaction.set_rollback(True)
            return Response({"detail": "O total pago é menor que o total da venda."}, status=status.HTTP_400_BAD_REQUEST)
        cashback_erro = self._validar_cashback(venda, pagamentos_payload, total)
        if cashback_erro:
            transaction.set_rollback(True)
            return Response({"detail": cashback_erro}, status=status.HTTP_400_BAD_REQUEST)
        troca_erro = self._validar_vale_troca(venda, pagamentos_payload, total)
        if troca_erro:
            transaction.set_rollback(True)
            return Response({"detail": troca_erro}, status=status.HTTP_400_BAD_REQUEST)

        venda.subtotal = money(subtotal)
        venda.desconto_itens = money(desconto_itens)
        venda.total = total
        venda.valor_recebido = total_pago
        venda.troco = money(total_pago - total) if total_pago > total else Decimal("0.00")
        venda.forma_pagamento = self._forma_resumo(pagamentos_payload)
        venda.save(update_fields=["subtotal", "desconto_itens", "total", "valor_recebido", "troco", "forma_pagamento", "atualizado_em"])
        self._registrar_pagamentos(venda, pagamentos_payload)

        self._registrar_financeiro(venda)
        self._registrar_uso_vale_troca(venda, pagamentos_payload)
        self._registrar_cashback(venda, pagamentos_payload)
        nfce = self._autorizar_nfce(venda)
        payload = VendaPdvSerializer(venda, context={"request": request}).data
        payload["cupom"] = self._cupom(venda, nfce)
        return Response(payload, status=status.HTTP_201_CREATED)

    def _normalizar_pagamentos(self, data) -> List[Dict]:
        pagamentos = data.get("pagamentos") or []
        if not pagamentos:
            valor = money(data.get("valor_recebido"))
            forma = data.get("forma_pagamento") or "DINHEIRO"
            pagamentos = [{"forma": forma, "valor": valor}]

        normalizados = []
        for pagamento in pagamentos:
            forma = str(pagamento.get("forma") or pagamento.get("codigo") or "").strip().upper()
            valor = money(pagamento.get("valor"))
            if not forma or valor <= 0:
                continue
            normalizados.append({
                "forma": forma,
                "descricao": str(pagamento.get("descricao") or forma).strip()[:80],
                "valor": valor,
                "autorizacao": str(pagamento.get("autorizacao") or "").strip()[:60],
            })
        return normalizados

    def _forma_resumo(self, pagamentos: List[Dict]) -> str:
        if len(pagamentos) == 1:
            return pagamentos[0]["forma"]
        return "MULTIPLO"

    def _registrar_pagamentos(self, venda: VendaPdv, pagamentos: List[Dict]):
        for pagamento in pagamentos:
            VendaPdvPagamento.objects.create(venda=venda, **pagamento)

    def _total_cashback_usado(self, pagamentos: List[Dict]) -> Decimal:
        return money(sum((pagamento["valor"] for pagamento in pagamentos if pagamento["forma"] == "CASHBACK"), Decimal("0")))

    def _total_vale_troca_usado(self, pagamentos: List[Dict]) -> Decimal:
        return money(sum((pagamento["valor"] for pagamento in pagamentos if pagamento["forma"] == "TROCA"), Decimal("0")))

    def _cliente_padrao(self, venda: VendaPdv) -> bool:
        cliente = venda.cliente
        cpf = _limpar_numero(getattr(cliente, "cpf", ""))
        nome = (getattr(cliente, "nome_cliente", "") or "").lower()
        return cpf == "00000000000" or "consumidor final" in nome

    def _cliente_participa_cashback(self, venda: VendaPdv, config: CashbackConfig) -> bool:
        return config.consumidor_final_participa or not self._cliente_padrao(venda)

    def _validar_cashback(self, venda: VendaPdv, pagamentos: List[Dict], total: Decimal) -> str:
        cashback_usado = self._total_cashback_usado(pagamentos)
        if cashback_usado <= 0:
            return ""

        config = CashbackConfig.regra_ativa()
        if not config:
            return "Não existe regra de cashback ativa."
        if not self._cliente_participa_cashback(venda, config):
            return "Cashback não pode ser usado para consumidor final."

        saldo = money(saldo_cashback_cliente(venda.cliente_id))
        if cashback_usado > saldo:
            return "O cashback informado é maior que o saldo disponível do cliente."
        if cashback_usado > total:
            return "O cashback não pode ser maior que o total da venda."
        valor_outros = money(sum((pagamento["valor"] for pagamento in pagamentos if pagamento["forma"] != "CASHBACK"), Decimal("0")))
        if cashback_usado > money(max(Decimal("0.00"), total - valor_outros)):
            return "Cashback não pode gerar troco; use apenas o saldo pendente da venda."
        if money(config.valor_minimo_uso) > 0 and cashback_usado < money(config.valor_minimo_uso):
            return "O valor de cashback usado é menor que o mínimo configurado."

        limite = money(total * Decimal(config.limite_uso_percentual or 0) / Decimal("100"))
        if cashback_usado > limite:
            return "O cashback usado ultrapassa o limite permitido para a venda."
        return ""

    def _validar_vale_troca(self, venda: VendaPdv, pagamentos: List[Dict], total: Decimal) -> str:
        vale_usado = self._total_vale_troca_usado(pagamentos)
        if vale_usado <= 0:
            return ""
        if self._cliente_padrao(venda):
            return "Troca exige cliente identificado."
        saldo = money(saldo_vale_troca_cliente(venda.cliente_id))
        if vale_usado > saldo:
            return "O valor de troca informado é maior que o saldo disponível do cliente."
        for pagamento in pagamentos:
            if pagamento["forma"] != "TROCA":
                continue
            if not pagamento.get("autorizacao"):
                return "Selecione um cupom de troca válido para o pagamento."
            vale = self._vale_troca_por_documento(venda, pagamento["autorizacao"])
            if not vale:
                return "Cupom de troca inválido para este cliente."
            if money(pagamento["valor"]) > money(vale.saldo):
                return "O valor informado é maior que o saldo do cupom de troca selecionado."
        if vale_usado > total:
            return "Troca não pode gerar troco; use apenas o saldo pendente da venda."
        valor_outros = money(sum((pagamento["valor"] for pagamento in pagamentos if pagamento["forma"] != "TROCA"), Decimal("0")))
        if vale_usado > money(max(Decimal("0.00"), total - valor_outros)):
            return "Troca não pode gerar troco; use apenas o saldo pendente da venda."
        return ""

    def _registrar_uso_vale_troca(self, venda: VendaPdv, pagamentos: List[Dict]):
        pagamentos_troca = [pagamento for pagamento in pagamentos if pagamento["forma"] == "TROCA"]
        if not pagamentos_troca:
            return
        hoje = timezone.localdate()
        vales_base = (
            ValeTroca.objects.select_for_update()
            .filter(cliente=venda.cliente, status=ValeTroca.STATUS_ABERTO, saldo__gt=0)
            .filter(models.Q(validade__isnull=True) | models.Q(validade__gte=hoje))
        )
        for pagamento in pagamentos_troca:
            restante = money(pagamento["valor"])
            documento = (pagamento.get("autorizacao") or "").strip()
            vales = vales_base.filter(documento=documento) if documento else vales_base.order_by("criado_em", "Idvaletroca")
            self._consumir_vales_troca(venda, vales, restante)

    def _vale_troca_por_documento(self, venda: VendaPdv, documento: str):
        hoje = timezone.localdate()
        return (
            ValeTroca.objects
            .filter(cliente=venda.cliente, documento=documento.strip(), status=ValeTroca.STATUS_ABERTO, saldo__gt=0)
            .filter(models.Q(validade__isnull=True) | models.Q(validade__gte=hoje))
            .first()
        )

    def _consumir_vales_troca(self, venda: VendaPdv, vales, valor: Decimal):
        restante = money(valor)
        for vale in vales:
            if restante <= 0:
                break
            saldo_atual = money(vale.saldo)
            uso = money(min(saldo_atual, restante))
            vale.saldo = money(saldo_atual - uso)
            if vale.saldo <= 0:
                vale.status = ValeTroca.STATUS_USADO
            vale.save(update_fields=["saldo", "status", "atualizado_em"])
            ValeTrocaMovimento.objects.create(
                vale=vale,
                venda_uso=venda,
                tipo=ValeTrocaMovimento.TIPO_USO,
                valor=uso,
                saldo_apos=vale.saldo,
                observacao=f"Uso na venda PDV {venda.documento}",
                criado_por=venda.criado_por,
            )
            restante = money(restante - uso)
        if restante > 0:
            raise ValueError("Saldo de troca insuficiente para concluir a venda.")

    def _registrar_cashback(self, venda: VendaPdv, pagamentos: List[Dict]):
        config = CashbackConfig.regra_ativa()
        if not config or not self._cliente_participa_cashback(venda, config):
            return

        cashback_usado = self._total_cashback_usado(pagamentos)
        if cashback_usado > 0 and not CashbackMovimento.objects.filter(venda_uso=venda, tipo=CashbackMovimento.TIPO_DEBITO).exists():
            CashbackMovimento.objects.create(
                cliente=venda.cliente,
                venda_uso=venda,
                tipo=CashbackMovimento.TIPO_DEBITO,
                valor=cashback_usado,
                observacao=f"Uso na venda PDV {venda.documento}",
                criado_por=venda.criado_por,
            )

        base_credito = money(venda.total - cashback_usado)
        if base_credito < money(config.valor_minimo_geracao):
            return

        credito = money(base_credito * Decimal(config.percentual or 0) / Decimal("100"))
        if credito <= 0:
            return

        if CashbackMovimento.objects.filter(venda_origem=venda, tipo=CashbackMovimento.TIPO_CREDITO).exists():
            return

        CashbackMovimento.objects.create(
            cliente=venda.cliente,
            venda_origem=venda,
            tipo=CashbackMovimento.TIPO_CREDITO,
            valor=credito,
            validade=timezone.localdate() + timedelta(days=int(config.validade_dias or 0)),
            observacao=f"Crédito gerado pela venda PDV {venda.documento}",
            criado_por=venda.criado_por,
        )

    def _registrar_item(self, venda: VendaPdv, item: dict) -> VendaPdvItem:
        ean = item.get("ean") or item.get("CodigodeBarra")
        quantidade = int(item.get("quantidade") or item.get("qtd") or 0)
        preco = money(item.get("preco_unitario") or item.get("preco"))
        desconto = money(item.get("desconto"))
        if not ean or quantidade <= 0 or preco < 0 or desconto < 0:
            raise ValueError("Item de venda inválido.")

        sku = ProdutoDetalhe.objects.select_related("produto").get(ean13=ean)
        produto = sku.produto
        estoque = Estoque.objects.select_for_update().get(CodigodeBarra=ean, Idloja=venda.loja)
        anterior = estoque.Estoque or 0
        posterior = anterior - quantidade
        if posterior < 0 and (venda.loja.EstoqueNegativo or "NAO").upper() != "SIM":
            raise ValueError(f"Saldo insuficiente para {produto.descricao}.")

        estoque.Estoque = posterior
        estoque.referencia = produto.referencia or estoque.referencia
        estoque.save(update_fields=["Estoque", "referencia"])
        EstoqueMovimentacao.objects.create(
            Idloja=venda.loja,
            CodigodeBarra=ean,
            referencia=produto.referencia or "",
            tipo=EstoqueMovimentacao.TIPO_SAIDA,
            quantidade=quantidade,
            saldo_anterior=anterior,
            saldo_posterior=posterior,
            documento=venda.documento,
            observacao=f"Venda PDV {venda.documento}",
        )

        return VendaPdvItem.objects.create(
            venda=venda,
            produto=produto,
            sku=sku,
            ean=ean,
            referencia=produto.referencia or "",
            descricao=item.get("descricao") or produto.descricao,
            cor=item.get("cor") or "",
            tamanho=item.get("tamanho") or "",
            quantidade=quantidade,
            preco_unitario=preco,
            desconto=desconto,
        )

    def _natureza_venda(self) -> Nat_Lancamento:
        natureza = Nat_Lancamento.objects.filter(codigo__startswith="1.").order_by("codigo").first()
        if natureza:
            return natureza
        return Nat_Lancamento.objects.create(
            codigo="1.01",
            categoria_principal="Vendas",
            subcategoria="Mercadorias",
            descricao="Receita de venda de mercadorias",
            tipo="RECEITA",
            status="ATIVO",
            tipo_natureza="CREDITO",
        )

    def _registrar_financeiro(self, venda: VendaPdv):
        if Receber.objects.filter(pedido_venda=venda.pk).exists():
            return

        natureza = self._natureza_venda()
        pagamentos = list(venda.pagamentos.all())
        total_pago = money(sum((money(pagamento.valor) for pagamento in pagamentos), Decimal("0")))
        valor_venda = money(venda.total)
        valor_baixado = money(min(total_pago, valor_venda))
        valor_receber = money(valor_venda - valor_baixado)

        saldo_para_caixa = money(venda.total)
        for pagamento in pagamentos:
            if pagamento.forma in ("CASHBACK", "TROCA"):
                continue
            valor_caixa = money(min(money(pagamento.valor), saldo_para_caixa))
            if valor_caixa <= 0:
                continue
            saldo_para_caixa = money(saldo_para_caixa - valor_caixa)
            caixa = venda.caixa
            if not caixa:
                continue
            caixa.saldo_atual = money(caixa.saldo_atual) + valor_caixa
            caixa.save(update_fields=["saldo_atual"])
            MovimentacaoFinanceira.objects.create(
                idloja=venda.loja,
                data_movimento=timezone.localdate(),
                tipo=MovimentacaoFinanceira.TIPO_ENTRADA,
                status=MovimentacaoFinanceira.STATUS_EFETIVA,
                origem=MovimentacaoFinanceira.ORIGEM_MANUAL,
                valor=valor_caixa,
                historico=f"Venda PDV {venda.documento} - {pagamento.descricao or pagamento.forma}",
                documento=venda.documento,
                Idnatureza=natureza,
                FormaPagamento=pagamento.forma,
                caixa=caixa,
            )
            self._consolidar_caixa_master(venda, natureza, valor_caixa)
        receber = Receber.objects.create(
            idloja=venda.loja,
            idcliente=venda.cliente,
            Titulo=f"Venda PDV {venda.documento}",
            Documento=venda.documento,
            Data_emissao=timezone.localdate(),
            Valor_total=valor_venda,
            Previsao=False,
            FormaPagamento=venda.forma_pagamento,
            Idnatureza=natureza,
            pedido_venda=venda.pk,
        )
        parcela_status = ReceberItem.STATUS_BAIXADO if valor_receber <= 0 else ReceberItem.STATUS_EFETIVO
        baixa_fields = {}
        if valor_baixado > 0:
            baixa_fields = {
                "data_baixa": timezone.localdate(),
                "valor_baixa": valor_baixado,
            }

        ReceberItem.objects.create(
            Idreceber=receber,
            parcela_n=1,
            status=parcela_status,
            Data_vencimento=timezone.localdate(),
            valor_parcela=valor_venda,
            FormaPagamento=venda.forma_pagamento,
            Previsao=False,
            Idnatureza=natureza,
            **baixa_fields,
        )

    def _consolidar_caixa_master(self, venda: VendaPdv, natureza: Nat_Lancamento, valor: Decimal):
        master = (
            Caixa.objects.select_for_update()
            .filter(tipo_caixa=Caixa.TIPO_MASTER, ativo=True)
            .order_by("Idcaixa")
            .first()
        )
        if not master:
            master = Caixa.objects.create(
                idloja=None,
                tipo_caixa=Caixa.TIPO_MASTER,
                codigo="MASTER",
                descricao="Caixa Master do Grupo",
                saldo_inicial=0,
                saldo_atual=0,
                ativo=True,
            )

        master.saldo_atual = money(master.saldo_atual) + money(valor)
        master.save(update_fields=["saldo_atual"])
        MovimentacaoFinanceira.objects.create(
            idloja=venda.loja,
            data_movimento=timezone.localdate(),
            tipo=MovimentacaoFinanceira.TIPO_ENTRADA,
            status=MovimentacaoFinanceira.STATUS_EFETIVA,
            origem=MovimentacaoFinanceira.ORIGEM_MANUAL,
            valor=valor,
            historico=f"Consolidacao master PDV {venda.documento}",
            documento=venda.documento,
            Idnatureza=natureza,
            FormaPagamento=venda.forma_pagamento,
            caixa=master,
        )

    def _autorizar_nfce(self, venda: VendaPdv) -> NFCe:
        nfce = NFCe.objects.create(venda=venda, numero=_proximo_numero_nfce(), status=NFCe.Status.EMITINDO)
        nfce.chave_acesso = _gerar_chave(nfce)
        nfce.protocolo = f"135{timezone.now().strftime('%y%m%d%H%M%S')}"
        nfce.qr_code_url = f"https://homologacao.nfce.sysvar.local/consulta?p={nfce.chave_acesso}"
        nfce.xml = f"<NFCe ambiente=\"homologacao\" chave=\"{nfce.chave_acesso}\" venda=\"{venda.documento}\" />"
        nfce.status = NFCe.Status.AUTORIZADA
        nfce.retorno_codigo = "100"
        nfce.retorno_mensagem = "Autorizado o uso da NFC-e em ambiente de homologacao."
        nfce.autorizada_em = timezone.now()
        nfce.save()
        return nfce

    def _cupom(self, venda: VendaPdv, nfce: NFCe) -> dict:
        cashback_gerado = money(sum((mov.valor for mov in venda.cashback_creditos.all()), Decimal("0.00")))
        cashback_usado = money(sum((mov.valor for mov in venda.cashback_usos.all()), Decimal("0.00")))
        return {
            "empresa": venda.loja.nome_loja,
            "cnpj": venda.loja.cnpj,
            "endereco": ", ".join(filter(None, [venda.loja.endereco, venda.loja.numero, venda.loja.cidade, venda.loja.estado])),
            "documento": venda.documento,
            "data": timezone.localtime(venda.data_venda).strftime("%d/%m/%Y %H:%M"),
            "cliente": venda.cliente.nome_cliente,
            "vendedor": venda.vendedor.nomefuncionario,
            "itens": [
                {
                    "descricao": item.descricao,
                    "ean": item.ean,
                    "quantidade": item.quantidade,
                    "preco_unitario": str(money(item.preco_unitario)),
                    "desconto": str(money(item.desconto)),
                    "total_item": str(money(item.total_item)),
                }
                for item in venda.itens.all()
            ],
            "subtotal": str(money(venda.subtotal)),
            "desconto": str(money(venda.desconto_itens + venda.desconto_geral)),
            "total": str(money(venda.total)),
            "forma_pagamento": venda.forma_pagamento,
            "valor_recebido": str(money(venda.valor_recebido)),
            "troco": str(money(venda.troco)),
            "cashback_gerado": str(cashback_gerado),
            "cashback_usado": str(cashback_usado),
            "pagamentos": [
                {
                    "forma": pagamento.forma,
                    "descricao": pagamento.descricao,
                    "valor": str(money(pagamento.valor)),
                    "autorizacao": pagamento.autorizacao,
                }
                for pagamento in venda.pagamentos.all()
            ],
            "nfce": NFCeSerializer(nfce).data,
        }


class NFCeViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModuleRole]
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa"]
    write_roles = ["Admin", "Diretor", "Gerente"]
    serializer_class = NFCeSerializer
    queryset = NFCe.objects.select_related("venda", "venda__loja", "venda__cliente", "venda__vendedor").all()

    @action(detail=True, methods=["get"], url_path="cupom")
    def cupom(self, request, pk=None):
        nfce = self.get_object()
        payload = VendaPdvViewSet()._cupom(nfce.venda, nfce)
        return Response(payload, status=status.HTTP_200_OK)


class VendaDevolucaoViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModuleRole]
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa"]
    write_roles = ["Admin", "Diretor", "Gerente", "Caixa"]
    serializer_class = VendaDevolucaoSerializer
    queryset = (
        VendaDevolucao.objects.select_related("venda", "loja", "cliente", "criado_por", "nfe_devolucao")
        .prefetch_related("itens")
        .all()
    )

    def get_queryset(self):
        qs = super().get_queryset()
        loja = self.request.query_params.get("loja")
        cliente = self.request.query_params.get("cliente")
        venda = self.request.query_params.get("venda")
        if loja:
            qs = qs.filter(loja_id=loja)
        if cliente:
            qs = qs.filter(cliente_id=cliente)
        if venda:
            qs = qs.filter(venda_id=venda)
        return qs

    @action(detail=False, methods=["get"], url_path="vendas-devolviveis")
    def vendas_devolviveis(self, request):
        qs = (
            VendaPdv.objects.select_related("loja", "cliente", "vendedor", "caixa")
            .prefetch_related("itens", "pagamentos", "devolucoes__itens", "cashback_creditos", "cashback_usos")
            .filter(status=VendaPdv.Status.FINALIZADA)
            .order_by("-data_venda")
        )
        loja = request.query_params.get("loja")
        cliente = request.query_params.get("cliente")
        documento = (request.query_params.get("documento") or "").strip()
        ean = (request.query_params.get("ean") or "").strip()
        if loja:
            qs = qs.filter(loja_id=loja)
        if cliente:
            qs = qs.filter(cliente_id=cliente)
        if documento:
            qs = qs.filter(documento__icontains=documento)
        if ean:
            qs = qs.filter(itens__ean=ean).distinct()

        payload = []
        for venda in qs:
            row = self._venda_devolucao_payload(venda)
            itens_disponiveis = [
                item for item in row["itens"]
                if int(item["quantidade_disponivel"] or 0) > 0 and (not ean or item["ean"] == ean)
            ]
            if itens_disponiveis:
                payload.append(row)
            if len(payload) >= 100:
                break
        return Response(payload, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="buscar-venda")
    def buscar_venda(self, request):
        documento = (request.query_params.get("documento") or "").strip()
        venda_id = request.query_params.get("venda")
        if not documento and not venda_id:
            return Response({"detail": "Informe o documento ou o código da venda."}, status=status.HTTP_400_BAD_REQUEST)

        qs = (
            VendaPdv.objects.select_related("loja", "cliente", "vendedor", "caixa")
            .prefetch_related("itens", "pagamentos", "devolucoes__itens")
            .filter(status=VendaPdv.Status.FINALIZADA)
        )
        venda = qs.filter(pk=venda_id).first() if venda_id else qs.filter(documento=documento).first()
        if not venda:
            return Response({"detail": "Venda finalizada não encontrada."}, status=status.HTTP_404_NOT_FOUND)

        return Response(self._venda_devolucao_payload(venda), status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="finalizar")
    @transaction.atomic
    def finalizar(self, request):
        venda_id = request.data.get("venda")
        documento = (request.data.get("documento_venda") or request.data.get("documento") or "").strip()
        motivo = str(request.data.get("motivo") or "").strip()[:255]
        itens_payload = request.data.get("itens") or []
        if not itens_payload:
            return Response({"detail": "Informe ao menos um item para devolução."}, status=status.HTTP_400_BAD_REQUEST)

        venda_qs = (
            VendaPdv.objects.select_for_update()
            .select_related("loja", "cliente", "caixa")
            .prefetch_related("itens", "devolucoes__itens", "pagamentos")
            .filter(status=VendaPdv.Status.FINALIZADA)
        )
        venda = venda_qs.filter(pk=venda_id).first() if venda_id else venda_qs.filter(documento=documento).first()
        if not venda:
            return Response({"detail": "Venda finalizada não encontrada."}, status=status.HTTP_404_NOT_FOUND)

        itens_por_id = {item.id: item for item in venda.itens.all()}
        devolvidos = self._quantidades_devolvidas(venda)
        selecionados = []
        total = Decimal("0.00")
        for row in itens_payload:
            venda_item_id = int(row.get("venda_item") or row.get("id") or 0)
            quantidade = int(row.get("quantidade") or 0)
            venda_item = itens_por_id.get(venda_item_id)
            if not venda_item or quantidade <= 0:
                transaction.set_rollback(True)
                return Response({"detail": "Item de devolução inválido."}, status=status.HTTP_400_BAD_REQUEST)
            disponivel = int(venda_item.quantidade or 0) - int(devolvidos.get(venda_item.id, 0))
            if quantidade > disponivel:
                transaction.set_rollback(True)
                return Response({"detail": f"Quantidade maior que o saldo para devolver em {venda_item.descricao}."}, status=status.HTTP_400_BAD_REQUEST)
            desconto_unitario = money(Decimal(venda_item.desconto or 0) / Decimal(venda_item.quantidade or 1))
            total_item = money((Decimal(venda_item.preco_unitario or 0) - desconto_unitario) * Decimal(quantidade))
            total += total_item
            selecionados.append((venda_item, quantidade, money(desconto_unitario * Decimal(quantidade))))

        if total <= 0:
            return Response({"detail": "Valor da devolução inválido."}, status=status.HTTP_400_BAD_REQUEST)

        devolucao = VendaDevolucao.objects.create(
            venda=venda,
            loja=venda.loja,
            cliente=venda.cliente,
            documento=f"DEV-{timezone.now().strftime('%Y%m%d%H%M%S%f')}",
            motivo=motivo,
            subtotal=money(total),
            credito_cliente=money(total),
            criado_por=request.user if request.user.is_authenticated else None,
        )

        for venda_item, quantidade, desconto in selecionados:
            self._registrar_item_devolucao(devolucao, venda_item, quantidade, desconto)

        self._registrar_credito_cliente(devolucao)
        self._estornar_financeiro(devolucao)
        self._registrar_nfe_devolucao(devolucao)

        payload = VendaDevolucaoSerializer(devolucao, context={"request": request}).data
        payload["venda_origem"] = self._venda_devolucao_payload(venda)
        return Response(payload, status=status.HTTP_201_CREATED)

    def _venda_devolucao_payload(self, venda: VendaPdv) -> dict:
        devolvidos = self._quantidades_devolvidas(venda)
        cashback_gerado = money(sum((mov.valor for mov in venda.cashback_creditos.all()), Decimal("0.00"))) if hasattr(venda, "cashback_creditos") else Decimal("0.00")
        cashback_usado = money(sum((mov.valor for mov in venda.cashback_usos.all()), Decimal("0.00"))) if hasattr(venda, "cashback_usos") else Decimal("0.00")
        return {
            "id": venda.id,
            "documento": venda.documento,
            "data_venda": venda.data_venda,
            "loja": venda.loja_id,
            "loja_nome": venda.loja.nome_loja,
            "cliente": venda.cliente_id,
            "cliente_nome": venda.cliente.nome_cliente,
            "vendedor_nome": venda.vendedor.nomefuncionario,
            "total": str(money(venda.total)),
            "cashback_gerado": str(cashback_gerado),
            "cashback_usado": str(cashback_usado),
            "nfce": NFCeSerializer(getattr(venda, "nfce", None)).data if hasattr(venda, "nfce") else None,
            "itens": [
                {
                    "id": item.id,
                    "produto": item.produto_id,
                    "sku": item.sku_id,
                    "ean": item.ean,
                    "referencia": item.referencia,
                    "descricao": item.descricao,
                    "cor": item.cor,
                    "tamanho": item.tamanho,
                    "quantidade": item.quantidade,
                    "quantidade_devolvida": devolvidos.get(item.id, 0),
                    "quantidade_disponivel": max(0, int(item.quantidade or 0) - int(devolvidos.get(item.id, 0))),
                    "preco_unitario": str(money(item.preco_unitario)),
                    "desconto": str(money(item.desconto)),
                    "total_item": str(money(item.total_item)),
                }
                for item in venda.itens.all()
            ],
        }

    def _quantidades_devolvidas(self, venda: VendaPdv) -> Dict[int, int]:
        devolvidos = defaultdict(int)
        for devolucao in venda.devolucoes.all():
            if devolucao.status == VendaDevolucao.Status.CANCELADA:
                continue
            for item in devolucao.itens.all():
                devolvidos[item.venda_item_id] += int(item.quantidade or 0)
        return devolvidos

    def _registrar_item_devolucao(self, devolucao: VendaDevolucao, venda_item: VendaPdvItem, quantidade: int, desconto: Decimal):
        estoque = Estoque.objects.select_for_update().get(CodigodeBarra=venda_item.ean, Idloja=devolucao.loja)
        anterior = estoque.Estoque or 0
        posterior = anterior + quantidade
        estoque.Estoque = posterior
        estoque.referencia = venda_item.referencia or estoque.referencia
        estoque.save(update_fields=["Estoque", "referencia"])
        EstoqueMovimentacao.objects.create(
            Idloja=devolucao.loja,
            CodigodeBarra=venda_item.ean,
            referencia=venda_item.referencia or "",
            tipo=EstoqueMovimentacao.TIPO_ENTRADA,
            quantidade=quantidade,
            saldo_anterior=anterior,
            saldo_posterior=posterior,
            documento=devolucao.documento,
            observacao=f"Devolução da venda {devolucao.venda.documento}",
        )
        VendaDevolucaoItem.objects.create(
            devolucao=devolucao,
            venda_item=venda_item,
            produto=venda_item.produto,
            sku=venda_item.sku,
            ean=venda_item.ean,
            referencia=venda_item.referencia,
            descricao=venda_item.descricao,
            cor=venda_item.cor,
            tamanho=venda_item.tamanho,
            quantidade=quantidade,
            preco_unitario=venda_item.preco_unitario,
            desconto=desconto,
        )

    def _registrar_credito_cliente(self, devolucao: VendaDevolucao):
        vale, created = ValeTroca.objects.get_or_create(
            devolucao=devolucao,
            defaults={
                "cliente": devolucao.cliente,
                "loja": devolucao.loja,
                "documento": f"VT-{devolucao.documento}",
                "valor_original": devolucao.credito_cliente,
                "saldo": devolucao.credito_cliente,
                "status": ValeTroca.STATUS_ABERTO,
                "observacao": f"Vale-troca gerado pela devolução {devolucao.documento}",
                "criado_por": devolucao.criado_por,
            },
        )
        if not created:
            return
        ValeTrocaMovimento.objects.create(
            vale=vale,
            tipo=ValeTrocaMovimento.TIPO_CREDITO,
            valor=devolucao.credito_cliente,
            saldo_apos=devolucao.credito_cliente,
            observacao=f"Crédito por devolução {devolucao.documento} da venda {devolucao.venda.documento}",
            criado_por=devolucao.criado_por,
        )
    def _estornar_financeiro(self, devolucao: VendaDevolucao):
        valor = money(devolucao.credito_cliente)
        receber = Receber.objects.filter(pedido_venda=devolucao.venda_id).prefetch_related("itens").first()
        if not receber:
            return
        restante = valor
        receber.Valor_total = money(max(Decimal("0.00"), money(receber.Valor_total) - valor))
        receber.save(update_fields=["Valor_total"])
        for item in receber.itens.all():
            if restante <= 0:
                break
            parcela = money(item.valor_parcela)
            abatimento = money(min(parcela, restante))
            item.valor_parcela = money(parcela - abatimento)
            if item.valor_baixa is not None:
                item.valor_baixa = money(max(Decimal("0.00"), money(item.valor_baixa) - abatimento))
            if item.valor_parcela <= 0:
                item.status = ReceberItem.STATUS_CANCELADO
                item.data_baixa = timezone.localdate()
                item.valor_baixa = Decimal("0.00")
            item.save(update_fields=["valor_parcela", "valor_baixa", "status", "data_baixa"])
            restante = money(restante - abatimento)

    def _registrar_nfe_devolucao(self, devolucao: VendaDevolucao):
        nfce_origem = getattr(devolucao.venda, "nfce", None)
        nfe = NFeDevolucao.objects.create(
            devolucao=devolucao,
            nfce_origem=nfce_origem,
            numero=_proximo_numero_nfe_devolucao(),
            status=NFeDevolucao.Status.DIGITADA,
        )
        nfe.xml = (
            f"<NFeDevolucao ambiente=\"homologacao\" devolucao=\"{devolucao.documento}\" "
            f"venda_origem=\"{devolucao.venda.documento}\" nfce_origem=\"{getattr(nfce_origem, 'chave_acesso', '')}\" />"
        )
        nfe.retorno_codigo = "000"
        nfe.retorno_mensagem = "NF-e de devolução digitada. Aguardando integração fiscal para autorização."
        nfe.save(update_fields=["xml", "retorno_codigo", "retorno_mensagem", "atualizado_em"])
