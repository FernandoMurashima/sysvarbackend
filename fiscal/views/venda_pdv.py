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
    FormaPagamento,
    MovimentacaoFinanceira,
    Receber,
    ReceberItem,
    ValeTroca,
    ValeTrocaMovimento,
    saldo_cashback_cliente,
    saldo_vale_troca_cliente,
)
from financeiro.services import gerar_lancamento_contabil_movimentacao
from fiscal.models import (
    Cfop,
    NFCe,
    NFeDevolucao,
    RegraTributaria,
    VendaDevolucao,
    VendaDevolucaoItem,
    VendaPdv,
    VendaPdvItem,
    VendaPdvPagamento,
)
from fiscal.models.venda_pdv import money
from fiscal.serializers import NFCeSerializer, VendaDevolucaoSerializer, VendaPdvSerializer
from produto.models import Estoque, EstoqueMovimentacao, Ncm, Produto, ProdutoDetalhe
from cadastros.models import Cliente, Funcionarios


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


def _proximo_documento_pdv() -> str:
    numero = _proximo_numero_nfce()
    while VendaPdv.objects.filter(documento=str(numero)).exists() or NFCe.objects.filter(numero=numero).exists():
        numero += 1
    return str(numero)


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
    module_key = "vendas"
    read_roles = ["Admin", "Diretor", "Gerente", "Caixa", "Vendedor"]
    write_roles = ["Admin", "Diretor", "Gerente", "Caixa"]
    action_roles = {
        "finalizar": ["Admin", "Diretor", "Gerente", "Caixa"],
        "relatorio_vendas": ["Admin", "Diretor", "Gerente"],
        "relatorio_margem": ["Admin", "Diretor", "Gerente"],
    }
    serializer_class = VendaPdvSerializer
    queryset = (
        VendaPdv.objects.select_related("loja", "caixa", "cliente", "vendedor", "criado_por")
        .prefetch_related("itens", "pagamentos")
        .all()
    )

    def get_queryset(self):
        qs = super().get_queryset()
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
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

    def _empresa_id_usuario(self):
        user = self.request.user
        if user.is_superuser:
            return self.request.query_params.get("empresa")
        return getattr(user, "empresa_id", None)

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

    @action(detail=False, methods=["get"], url_path="relatorio-margem")
    def relatorio_margem(self, request):
        vendas = self._vendas_relatorio(request)
        produtos = defaultdict(lambda: {
            "produto": "",
            "referencia": "",
            "colecao": "",
            "grupo": "",
            "subgrupo": "",
            "quantidade": Decimal("0"),
            "receita": Decimal("0.00"),
            "cmv": Decimal("0.00"),
        })
        lojas = defaultdict(lambda: {
            "loja": "",
            "quantidade": Decimal("0"),
            "receita": Decimal("0.00"),
            "cmv": Decimal("0.00"),
        })

        for venda in vendas:
            devolvidos = self._quantidades_devolvidas_venda(venda)
            for item in venda.itens.all():
                quantidade_original = Decimal(item.quantidade or 0)
                if quantidade_original <= 0:
                    continue
                quantidade_liquida = quantidade_original - Decimal(devolvidos.get(item.id, 0))
                if quantidade_liquida <= 0:
                    continue

                fator = quantidade_liquida / quantidade_original
                receita = money(Decimal(item.total_item or 0) * fator)
                cmv = money(Decimal(item.cmv_total or 0) * fator)
                produto = item.produto
                key = produto.pk
                row = produtos[key]
                row["produto"] = produto.descricao
                row["referencia"] = produto.referencia or ""
                row["colecao"] = getattr(produto.colecao, "Descricao", "") if produto.colecao_id else ""
                row["grupo"] = getattr(produto.grupo, "Descricao", "") if produto.grupo_id else ""
                row["subgrupo"] = getattr(produto.subgrupo, "Descricao", "") if produto.subgrupo_id else ""
                row["quantidade"] += quantidade_liquida
                row["receita"] += receita
                row["cmv"] += cmv

                loja_row = lojas[venda.loja_id]
                loja_row["loja"] = venda.loja.nome_loja
                loja_row["quantidade"] += quantidade_liquida
                loja_row["receita"] += receita
                loja_row["cmv"] += cmv

        def serializar(row):
            receita = money(row["receita"])
            cmv = money(row["cmv"])
            margem = money(receita - cmv)
            margem_percentual = money((margem / receita * Decimal("100")) if receita else Decimal("0"))
            return {
                **{k: row[k] for k in row.keys() if k not in ("quantidade", "receita", "cmv")},
                "quantidade": float(row["quantidade"]),
                "receita": str(receita),
                "cmv": str(cmv),
                "margem": str(margem),
                "margem_percentual": str(margem_percentual),
            }

        produtos_payload = [serializar(row) for row in produtos.values()]
        lojas_payload = [serializar(row) for row in lojas.values()]
        produtos_payload.sort(key=lambda row: Decimal(row["margem"]), reverse=True)
        lojas_payload.sort(key=lambda row: row["loja"])

        total_receita = money(sum((Decimal(row["receita"]) for row in produtos_payload), Decimal("0.00")))
        total_cmv = money(sum((Decimal(row["cmv"]) for row in produtos_payload), Decimal("0.00")))
        total_margem = money(total_receita - total_cmv)
        total_margem_percentual = money((total_margem / total_receita * Decimal("100")) if total_receita else Decimal("0"))

        return Response({
            "resumo": {
                "receita": str(total_receita),
                "cmv": str(total_cmv),
                "margem": str(total_margem),
                "margem_percentual": str(total_margem_percentual),
                "produtos": len(produtos_payload),
                "quantidade": float(sum((row["quantidade"] for row in produtos.values()), Decimal("0"))),
            },
            "produtos": produtos_payload,
            "lojas": lojas_payload,
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
                "devolucoes__itens",
            )
            .filter(status=VendaPdv.Status.FINALIZADA)
            .order_by("-data_venda")
        )
        empresa_id = self._empresa_id_usuario()
        loja = request.query_params.get("loja")
        data_ini = request.query_params.get("data_ini") or request.query_params.get("data_inicial")
        data_fim = request.query_params.get("data_fim") or request.query_params.get("data_final")
        vendedor = request.query_params.get("vendedor")
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not request.user.is_superuser:
            return []
        if loja:
            qs = qs.filter(loja_id=loja)
        if vendedor:
            qs = qs.filter(vendedor_id=vendedor)
        if data_ini:
            qs = qs.filter(data_venda__gte=self._datetime_inicio_dia(data_ini))
        if data_fim:
            qs = qs.filter(data_venda__lte=self._datetime_fim_dia(data_fim))
        return list(qs)

    def _quantidades_devolvidas_venda(self, venda: VendaPdv) -> Dict[int, int]:
        devolvidos = defaultdict(int)
        for devolucao in venda.devolucoes.all():
            if devolucao.status == VendaDevolucao.Status.CANCELADA:
                continue
            for item in devolucao.itens.all():
                devolvidos[item.venda_item_id] += int(item.quantidade or 0)
        return devolvidos

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

        empresa_id = self._empresa_id_usuario()
        if not empresa_id and not request.user.is_superuser:
            return Response({"detail": "Usuário sem empresa vinculada."}, status=status.HTTP_400_BAD_REQUEST)
        caixa = (
            Caixa.objects.select_for_update()
            .select_related("idloja")
            .filter(pk=caixa_id, idloja_id=loja_id, ativo=True, tipo_caixa=Caixa.TIPO_LOJA)
            .first()
        )
        if not caixa:
            return Response({"detail": "O caixa informado não pertence à loja ou não está ativo."}, status=status.HTTP_400_BAD_REQUEST)
        if empresa_id and caixa.idloja.empresa_id != int(empresa_id):
            return Response({"detail": "A loja informada pertence a outra empresa."}, status=status.HTTP_400_BAD_REQUEST)
        cliente = Cliente.objects.filter(pk=cliente_id).first()
        if not cliente or (empresa_id and cliente.empresa_id and cliente.empresa_id != int(empresa_id)):
            return Response({"detail": "O cliente informado pertence a outra empresa."}, status=status.HTTP_400_BAD_REQUEST)
        vendedor = (
            Funcionarios.objects
            .filter(pk=vendedor_id, idloja_id=loja_id, ativo=True, categoria__iexact="Vendedor")
            .first()
        )
        if not vendedor:
            return Response({"detail": "O vendedor informado não está vinculado a esta loja."}, status=status.HTTP_400_BAD_REQUEST)
        if empresa_id and vendedor.empresa_id and vendedor.empresa_id != int(empresa_id):
            return Response({"detail": "O vendedor informado pertence a outra empresa."}, status=status.HTTP_400_BAD_REQUEST)

        documento = (data.get("documento") or "").strip()
        if documento:
            venda_existente = VendaPdv.objects.filter(empresa=caixa.idloja.empresa, documento=documento).first()
            if venda_existente:
                payload = VendaPdvSerializer(venda_existente, context={"request": request}).data
                payload["cupom"] = self._cupom(venda_existente, getattr(venda_existente, "nfce", None))
                return Response(payload, status=status.HTTP_200_OK)
        else:
            documento = _proximo_documento_pdv()

        venda = VendaPdv.objects.create(
            empresa=caixa.idloja.empresa,
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
        self._registrar_cmv(venda)
        self._registrar_impostos_venda(venda)
        self._registrar_comissao(venda)
        self._registrar_uso_vale_troca(venda, pagamentos_payload)
        self._registrar_cashback(venda, pagamentos_payload)
        nfce = self._autorizar_nfce(venda, self._venda_em_contingencia(data))
        payload = VendaPdvSerializer(venda, context={"request": request}).data
        payload["cupom"] = self._cupom(venda, nfce)
        return Response(payload, status=status.HTTP_201_CREATED)

    def _venda_em_contingencia(self, data) -> bool:
        documento = str(data.get("documento") or "")
        return bool(data.get("contingencia") or data.get("local_uuid") or documento.upper().startswith("LOCAL-"))

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

        config = CashbackConfig.regra_ativa(venda.empresa)
        if not config:
            return "Não existe regra de cashback ativa."
        if not self._cliente_participa_cashback(venda, config):
            return "Cashback não pode ser usado para consumidor final."

        saldo = money(saldo_cashback_cliente(venda.cliente_id, empresa=venda.empresa))
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
        saldo = money(saldo_vale_troca_cliente(venda.cliente_id, empresa=venda.empresa))
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
            .filter(empresa=venda.empresa, cliente=venda.cliente, status=ValeTroca.STATUS_ABERTO, saldo__gt=0)
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
            .filter(empresa=venda.empresa, cliente=venda.cliente, documento=documento.strip(), status=ValeTroca.STATUS_ABERTO, saldo__gt=0)
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
        config = CashbackConfig.regra_ativa(venda.empresa)
        if not config or not self._cliente_participa_cashback(venda, config):
            return

        cashback_usado = self._total_cashback_usado(pagamentos)
        if cashback_usado > 0 and not CashbackMovimento.objects.filter(venda_uso=venda, tipo=CashbackMovimento.TIPO_DEBITO).exists():
            CashbackMovimento.objects.create(
                empresa=venda.empresa,
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
            empresa=venda.empresa,
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
        if venda.empresa_id and produto.empresa_id and produto.empresa_id != venda.empresa_id:
            raise ValueError("Produto pertence a outra empresa.")
        estoque = Estoque.objects.select_for_update().get(CodigodeBarra=ean, Idloja=venda.loja)
        anterior = estoque.Estoque or 0
        posterior = anterior - quantidade
        if posterior < 0 and (venda.loja.EstoqueNegativo or "NAO").upper() != "SIM":
            raise ValueError(f"Saldo insuficiente para {produto.descricao}.")

        custo_unitario = self._custo_sku(sku)
        estoque.Estoque = posterior
        estoque.referencia = produto.referencia or estoque.referencia
        estoque.save(update_fields=["Estoque", "referencia"])
        EstoqueMovimentacao.objects.create(
            Idloja=venda.loja,
            CodigodeBarra=ean,
            referencia=produto.referencia or "",
            tipo=EstoqueMovimentacao.TIPO_SAIDA,
            quantidade=quantidade,
            custo_unitario=custo_unitario,
            custo_total=money(Decimal(quantidade) * custo_unitario),
            custo_medio_apos=custo_unitario,
            saldo_anterior=anterior,
            saldo_posterior=posterior,
            documento=venda.documento,
            observacao=f"Venda PDV {venda.documento}",
        )

        fiscal = self._calcular_fiscal_item(venda, produto, quantidade, preco, desconto)
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
            custo_unitario=custo_unitario,
            **fiscal,
        )

    def _calcular_fiscal_item(
        self,
        venda: VendaPdv,
        produto: Produto,
        quantidade: int,
        preco: Decimal,
        desconto: Decimal,
    ) -> dict:
        base = money((Decimal(quantidade or 0) * Decimal(preco or 0)) - Decimal(desconto or 0))
        loja_uf = (getattr(venda.loja, "estado", "") or "").upper()
        destino_uf = (getattr(venda.cliente, "estado", "") or loja_uf).upper()
        cfop = produto.cfop_venda_dentro if loja_uf == destino_uf else produto.cfop_venda_fora
        if not cfop:
            cfop = produto.cfop_venda_dentro or produto.cfop_venda_fora or ""

        regras = self._regras_tributarias_item(venda, produto, cfop, loja_uf, destino_uf)
        icms = self._calcular_tributo_item(regras.get("ICMS"), base, Decimal(produto.aliquota_icms or 0))
        pis = self._calcular_tributo_item(regras.get("PIS"), base, Decimal(produto.aliq_pis or 0))
        cofins = self._calcular_tributo_item(regras.get("COFINS"), base, Decimal(produto.aliq_cofins or 0))

        regra_cfop = next((regra.cfop.codigo for regra in regras.values() if regra.cfop_id), "")
        if regra_cfop:
            cfop = regra_cfop

        return {
            "ncm": produto.ncm or "",
            "cfop": cfop,
            "origem_mercadoria": produto.origem_mercadoria,
            "cst_icms": icms["cst"] or produto.csosn_ou_cst_icms or "",
            "base_icms": base,
            "aliquota_icms": icms["aliquota"],
            "valor_icms": icms["valor"],
            "cst_pis": pis["cst"] or produto.cst_pis or "",
            "base_pis": base,
            "aliquota_pis": pis["aliquota"],
            "valor_pis": pis["valor"],
            "cst_cofins": cofins["cst"] or produto.cst_cofins or "",
            "base_cofins": base,
            "aliquota_cofins": cofins["aliquota"],
            "valor_cofins": cofins["valor"],
            "total_impostos": money(icms["valor"] + pis["valor"] + cofins["valor"]),
        }

    def _regras_tributarias_item(self, venda: VendaPdv, produto: Produto, cfop_codigo: str, uf_origem: str, uf_destino: str) -> dict:
        empresa_id = venda.empresa_id or getattr(venda.loja, "empresa_id", None) or produto.empresa_id
        if not empresa_id:
            return {}

        hoje = timezone.localdate()
        regime = getattr(venda.loja, "regime_tributario", "") or RegraTributaria.REGIME_TODOS
        tipo_produto = self._tipo_produto_regra(produto)
        cfop_obj = self._cfop_regra(empresa_id, cfop_codigo)
        ncm_obj = self._ncm_regra(empresa_id, produto.ncm)

        qs = (
            RegraTributaria.objects
            .select_related("tributo", "cfop", "ncm")
            .filter(
                empresa_id=empresa_id,
                ativo=True,
                tipo_operacao__iexact="VENDA",
                vigencia_inicio__lte=hoje,
            )
            .filter(models.Q(vigencia_fim__isnull=True) | models.Q(vigencia_fim__gte=hoje))
            .filter(models.Q(regime_tributario=regime) | models.Q(regime_tributario=RegraTributaria.REGIME_TODOS))
            .filter(models.Q(tipo_produto=tipo_produto) | models.Q(tipo_produto=RegraTributaria.TIPO_PRODUTO_TODOS))
            .filter(models.Q(uf_origem__isnull=True) | models.Q(uf_origem="") | models.Q(uf_origem=uf_origem))
            .filter(models.Q(uf_destino__isnull=True) | models.Q(uf_destino="") | models.Q(uf_destino=uf_destino))
        )
        if cfop_obj:
            qs = qs.filter(models.Q(cfop__isnull=True) | models.Q(cfop=cfop_obj))
        else:
            qs = qs.filter(cfop__isnull=True)
        if ncm_obj:
            qs = qs.filter(models.Q(ncm__isnull=True) | models.Q(ncm=ncm_obj))
        else:
            qs = qs.filter(ncm__isnull=True)

        selecionadas = {}
        for regra in qs:
            codigo = (regra.tributo.codigo or "").upper()
            if codigo not in ("ICMS", "PIS", "COFINS"):
                continue
            atual = selecionadas.get(codigo)
            if not atual or self._pontuacao_regra(regra, regime, tipo_produto, cfop_obj, ncm_obj, uf_origem, uf_destino) > self._pontuacao_regra(atual, regime, tipo_produto, cfop_obj, ncm_obj, uf_origem, uf_destino):
                selecionadas[codigo] = regra
        return selecionadas

    def _calcular_tributo_item(self, regra, base: Decimal, aliquota_padrao: Decimal) -> dict:
        if not regra:
            aliquota = Decimal(aliquota_padrao or 0).quantize(Decimal("0.01"))
            return {"aliquota": aliquota, "valor": money(base * aliquota / Decimal("100")), "cst": ""}
        aliquota = Decimal(regra.aliquota or 0).quantize(Decimal("0.01"))
        reducao = Decimal(regra.reducao_base or 0)
        base_tributada = money(base * (Decimal("100") - reducao) / Decimal("100"))
        return {
            "aliquota": aliquota,
            "valor": money(base_tributada * aliquota / Decimal("100")),
            "cst": regra.cst_csosn or "",
        }

    def _tipo_produto_regra(self, produto: Produto) -> str:
        return {
            "1": RegraTributaria.TIPO_PRODUTO_REVENDA,
            "2": RegraTributaria.TIPO_PRODUTO_USO_CONSUMO,
            "3": RegraTributaria.TIPO_PRODUTO_PROPRIO,
            "4": RegraTributaria.TIPO_PRODUTO_INSUMO,
        }.get(str(produto.tipo_produto or ""), RegraTributaria.TIPO_PRODUTO_TODOS)

    def _cfop_regra(self, empresa_id, cfop_codigo: str):
        codigo = str(cfop_codigo or "").strip()
        if not codigo:
            return None
        return Cfop.objects.filter(empresa_id=empresa_id, codigo=codigo, ativo=True).first()

    def _ncm_regra(self, empresa_id, ncm_codigo: str):
        codigo = str(ncm_codigo or "").strip()
        if not codigo:
            return None
        return Ncm.objects.filter(empresa_id=empresa_id, ncm=codigo, ativo=True).first()

    def _pontuacao_regra(self, regra, regime, tipo_produto, cfop_obj, ncm_obj, uf_origem, uf_destino) -> int:
        pontos = 0
        if regra.regime_tributario == regime:
            pontos += 8
        if regra.tipo_produto == tipo_produto:
            pontos += 8
        if cfop_obj and regra.cfop_id == cfop_obj.id:
            pontos += 6
        if ncm_obj and regra.ncm_id == ncm_obj.id:
            pontos += 6
        if regra.uf_origem == uf_origem:
            pontos += 3
        if regra.uf_destino == uf_destino:
            pontos += 3
        return pontos

    def _custo_sku(self, sku: ProdutoDetalhe) -> Decimal:
        custo = Decimal(sku.custo_medio or sku.custo_ultima_compra or sku.custo_original or 0)
        if custo > 0:
            return custo
        referencia = (
            ProdutoDetalhe.objects
            .filter(produto_id=sku.produto_id, custo_medio__gt=0)
            .order_by("-custo_medio")
            .values_list("custo_medio", flat=True)
            .first()
        )
        if referencia:
            return Decimal(referencia or 0)
        referencia = (
            ProdutoDetalhe.objects
            .filter(produto_id=sku.produto_id, custo_ultima_compra__gt=0)
            .order_by("-custo_ultima_compra")
            .values_list("custo_ultima_compra", flat=True)
            .first()
        )
        return Decimal(referencia or 0)

    def _atualizar_custo_medio_retorno(self, sku: ProdutoDetalhe, saldo_anterior: int, quantidade: int, custo_retorno: Decimal) -> Decimal:
        custo_retorno = Decimal(custo_retorno or 0)
        custo_atual = Decimal(sku.custo_medio or sku.custo_ultima_compra or sku.custo_original or 0)
        if custo_retorno <= 0:
            return custo_atual
        saldo_anterior_dec = Decimal(max(int(saldo_anterior or 0), 0))
        quantidade_dec = Decimal(max(int(quantidade or 0), 0))
        saldo_posterior = saldo_anterior_dec + quantidade_dec
        if saldo_posterior <= 0:
            custo_medio = custo_retorno
        else:
            custo_medio = ((saldo_anterior_dec * custo_atual) + (quantidade_dec * custo_retorno)) / saldo_posterior
        sku.custo_medio = custo_medio.quantize(Decimal("0.0001"))
        sku.save(update_fields=["custo_medio"])
        return sku.custo_medio

    def _natureza_venda(self, empresa=None) -> Nat_Lancamento:
        natureza = (
            Nat_Lancamento.objects
            .filter(empresa=empresa, natureza_operacao="RECEITA", ativo=True)
            .order_by("codigo")
            .first()
        )
        if natureza:
            return natureza
        return Nat_Lancamento.objects.create(
            empresa=empresa,
            codigo="1.01",
            categoria_principal="Vendas",
            subcategoria="Mercadorias",
            descricao="Receita de venda de mercadorias",
            tipo="RECEITA",
            status="ATIVO",
            tipo_natureza="CREDITO",
            natureza_operacao="RECEITA",
            categoria_gerencial="Vendas",
            movimenta_financeiro=True,
            entra_dre=True,
            ativo=True,
        )

    def _natureza_cmv(self, empresa=None) -> Nat_Lancamento:
        natureza = (
            Nat_Lancamento.objects
            .filter(empresa=empresa, ativo=True, natureza_operacao="DESPESA")
            .filter(models.Q(codigo="2100") | models.Q(descricao__icontains="CMV") | models.Q(descricao__icontains="mercadoria vendida"))
            .order_by("codigo")
            .first()
        )
        if natureza:
            return natureza

        plano = None
        try:
            from cadastros.models import PlanoContabil
            plano = (
                PlanoContabil.objects
                .filter(empresa=empresa, ativa=True, classe=PlanoContabil.CLASSE_CUSTO)
                .filter(models.Q(codigo="5.1.01") | models.Q(descricao__icontains="CMV"))
                .order_by("codigo")
                .first()
            )
        except Exception:
            plano = None

        return Nat_Lancamento.objects.create(
            empresa=empresa,
            codigo="2100",
            categoria_principal="CUSTOS DAS MERCADORIAS",
            subcategoria="CMV",
            descricao="CMV - Custo da mercadoria vendida",
            tipo="DESPESA",
            status="ATIVO",
            tipo_natureza="DEBITO",
            natureza_operacao="DESPESA",
            categoria_gerencial="CMV",
            movimenta_financeiro=False,
            entra_dre=True,
            plano_contabil=plano,
            conta_contabil=plano.codigo if plano else None,
            ativo=True,
        )

    def _natureza_comissao(self, empresa=None) -> Nat_Lancamento:
        natureza = (
            Nat_Lancamento.objects
            .filter(empresa=empresa, ativo=True, natureza_operacao="DESPESA")
            .filter(models.Q(codigo="3103") | models.Q(descricao__icontains="Comiss"))
            .order_by("codigo")
            .first()
        )
        if natureza:
            return natureza

        plano = None
        try:
            from cadastros.models import PlanoContabil
            plano = (
                PlanoContabil.objects
                .filter(empresa=empresa, ativa=True, classe=PlanoContabil.CLASSE_DESPESA)
                .filter(models.Q(codigo="6.3.02") | models.Q(descricao__icontains="Comiss"))
                .order_by("codigo")
                .first()
            )
        except Exception:
            plano = None

        return Nat_Lancamento.objects.create(
            empresa=empresa,
            codigo="3103",
            categoria_principal="DESPESAS OPERACIONAIS",
            subcategoria="Vendas",
            descricao="Comissões",
            tipo="DESPESA",
            status="ATIVO",
            tipo_natureza="DEBITO",
            natureza_operacao="DESPESA",
            categoria_gerencial="Despesas com vendas",
            movimenta_financeiro=False,
            entra_dre=True,
            plano_contabil=plano,
            conta_contabil=plano.codigo if plano else None,
            ativo=True,
        )

    def _natureza_impostos_venda(self, empresa=None) -> Nat_Lancamento:
        natureza = (
            Nat_Lancamento.objects
            .filter(empresa=empresa, ativo=True, natureza_operacao="DESPESA")
            .filter(
                models.Q(codigo="2200")
                | models.Q(descricao__icontains="Impostos sobre vendas")
                | models.Q(descricao__icontains="Tributos sobre vendas")
            )
            .order_by("codigo")
            .first()
        )
        if natureza:
            return natureza

        plano = None
        try:
            from cadastros.models import PlanoContabil
            plano = (
                PlanoContabil.objects
                .filter(empresa=empresa, ativa=True)
                .filter(
                    models.Q(descricao__icontains="Imposto")
                    | models.Q(descricao__icontains="Tributo")
                    | models.Q(descricao__icontains="ICMS")
                )
                .order_by("codigo")
                .first()
            )
        except Exception:
            plano = None

        return Nat_Lancamento.objects.create(
            empresa=empresa,
            codigo="2200",
            categoria_principal="DEDUCOES DA RECEITA",
            subcategoria="Tributos",
            descricao="Impostos sobre vendas",
            tipo="DESPESA",
            status="ATIVO",
            tipo_natureza="DEBITO",
            natureza_operacao="DESPESA",
            categoria_gerencial="Tributos sobre vendas",
            movimenta_financeiro=False,
            entra_dre=True,
            plano_contabil=plano,
            conta_contabil=plano.codigo if plano else None,
            ativo=True,
        )

    def _registrar_cmv(self, venda: VendaPdv):
        if MovimentacaoFinanceira.objects.filter(
            empresa=venda.empresa,
            origem=MovimentacaoFinanceira.ORIGEM_CMV,
            documento=venda.documento,
        ).exists():
            return

        total_cmv = money(sum((Decimal(item.cmv_total or 0) for item in venda.itens.all()), Decimal("0.00")))
        if total_cmv <= 0:
            return

        movimento = MovimentacaoFinanceira.objects.create(
            empresa=venda.empresa,
            idloja=venda.loja,
            data_movimento=timezone.localdate(),
            tipo=MovimentacaoFinanceira.TIPO_SAIDA,
            status=MovimentacaoFinanceira.STATUS_EFETIVA,
            origem=MovimentacaoFinanceira.ORIGEM_CMV,
            valor=total_cmv,
            historico=f"CMV venda PDV {venda.documento}",
            documento=venda.documento,
            Idnatureza=self._natureza_cmv(venda.empresa),
            FormaPagamento="CMV",
        )
        gerar_lancamento_contabil_movimentacao(movimento)

    def _registrar_comissao(self, venda: VendaPdv):
        if MovimentacaoFinanceira.objects.filter(
            empresa=venda.empresa,
            origem=MovimentacaoFinanceira.ORIGEM_COMISSAO,
            documento=venda.documento,
        ).exists():
            return

        percentual = Decimal(getattr(venda.vendedor, "comissao_percentual", 0) or 0)
        if percentual <= 0:
            return
        valor = money(Decimal(venda.total or 0) * percentual / Decimal("100"))
        if valor <= 0:
            return

        movimento = MovimentacaoFinanceira.objects.create(
            empresa=venda.empresa,
            idloja=venda.loja,
            data_movimento=timezone.localdate(),
            tipo=MovimentacaoFinanceira.TIPO_SAIDA,
            status=MovimentacaoFinanceira.STATUS_EFETIVA,
            origem=MovimentacaoFinanceira.ORIGEM_COMISSAO,
            valor=valor,
            historico=f"Comissão venda PDV {venda.documento} - {venda.vendedor.nomefuncionario}",
            documento=venda.documento,
            Idnatureza=self._natureza_comissao(venda.empresa),
            FormaPagamento="COMISSAO",
        )
        gerar_lancamento_contabil_movimentacao(movimento)

    def _registrar_impostos_venda(self, venda: VendaPdv):
        if MovimentacaoFinanceira.objects.filter(
            empresa=venda.empresa,
            origem=MovimentacaoFinanceira.ORIGEM_MANUAL,
            documento=venda.documento,
            FormaPagamento="IMPOSTOS",
        ).exists():
            return

        total_impostos = money(sum((Decimal(item.total_impostos or 0) for item in venda.itens.all()), Decimal("0.00")))
        if total_impostos <= 0:
            return

        movimento = MovimentacaoFinanceira.objects.create(
            empresa=venda.empresa,
            idloja=venda.loja,
            data_movimento=timezone.localdate(),
            tipo=MovimentacaoFinanceira.TIPO_SAIDA,
            status=MovimentacaoFinanceira.STATUS_EFETIVA,
            origem=MovimentacaoFinanceira.ORIGEM_MANUAL,
            valor=total_impostos,
            historico=f"Impostos sobre venda PDV {venda.documento}",
            documento=venda.documento,
            Idnatureza=self._natureza_impostos_venda(venda.empresa),
            FormaPagamento="IMPOSTOS",
        )
        gerar_lancamento_contabil_movimentacao(movimento)

    def _registrar_financeiro(self, venda: VendaPdv):
        if Receber.objects.filter(pedido_venda=venda.pk).exists():
            return

        natureza = self._natureza_venda(venda.empresa)
        pagamentos = list(venda.pagamentos.all())
        valor_venda = money(venda.total)
        formas_liquidacao = {
            forma.codigo.upper(): forma
            for forma in FormaPagamento.objects.select_related("conta_liquidacao")
            .filter(empresa=venda.empresa, ativo=True, gera_recebivel_bancario=True, conta_liquidacao__isnull=False)
        }
        receber = Receber.objects.create(
            empresa=venda.empresa,
            idloja=venda.loja,
            idcliente=venda.cliente,
            Titulo=str(venda.documento),
            Documento=venda.documento,
            Data_emissao=timezone.localdate(),
            Valor_total=valor_venda,
            Previsao=False,
            FormaPagamento=venda.forma_pagamento,
            Idnatureza=natureza,
            pedido_venda=venda.pk,
        )

        saldo_financeiro = valor_venda
        parcela_n = 1
        for pagamento in pagamentos:
            if pagamento.forma in ("CASHBACK", "TROCA"):
                continue
            valor_pagamento = money(min(money(pagamento.valor), saldo_financeiro))
            if valor_pagamento <= 0:
                continue
            saldo_financeiro = money(saldo_financeiro - valor_pagamento)
            forma_config = formas_liquidacao.get(str(pagamento.forma or "").upper())
            if forma_config:
                item = ReceberItem.objects.create(
                    Idreceber=receber,
                    parcela_n=parcela_n,
                    status=ReceberItem.STATUS_EFETIVO,
                    Data_vencimento=timezone.localdate() + timedelta(days=int(forma_config.prazo_credito_dias or 0)),
                    valor_parcela=valor_pagamento,
                    FormaPagamento=pagamento.forma,
                    Previsao=True,
                    Idnatureza=natureza,
                )
                self._registrar_recebivel_bancario(venda, natureza, pagamento, forma_config, valor_pagamento, item)
            else:
                item = ReceberItem.objects.create(
                    Idreceber=receber,
                    parcela_n=parcela_n,
                    status=ReceberItem.STATUS_BAIXADO,
                    Data_vencimento=timezone.localdate(),
                    valor_parcela=valor_pagamento,
                    FormaPagamento=pagamento.forma,
                    Previsao=False,
                    Idnatureza=natureza,
                    data_baixa=timezone.localdate(),
                    valor_baixa=valor_pagamento,
                )
                self._registrar_recebimento_imediato(venda, natureza, pagamento, valor_pagamento, item)
            parcela_n += 1

        if not receber.itens.exists():
            ReceberItem.objects.create(
                Idreceber=receber,
                parcela_n=1,
                status=ReceberItem.STATUS_BAIXADO,
                Data_vencimento=timezone.localdate(),
                valor_parcela=valor_venda,
                FormaPagamento=venda.forma_pagamento,
                Previsao=False,
                Idnatureza=natureza,
                data_baixa=timezone.localdate(),
                valor_baixa=valor_venda,
            )

    def _registrar_recebimento_imediato(self, venda: VendaPdv, natureza: Nat_Lancamento, pagamento: VendaPdvPagamento, valor: Decimal, item: ReceberItem):
        caixa = venda.caixa
        if not caixa:
            return
        caixa.saldo_atual = money(caixa.saldo_atual) + valor
        caixa.save(update_fields=["saldo_atual"])
        movimento = MovimentacaoFinanceira.objects.create(
            empresa=venda.empresa,
            idloja=venda.loja,
            data_movimento=timezone.localdate(),
            tipo=MovimentacaoFinanceira.TIPO_ENTRADA,
            status=MovimentacaoFinanceira.STATUS_EFETIVA,
            origem=MovimentacaoFinanceira.ORIGEM_RECEBER,
            valor=valor,
            historico=f"Venda PDV {venda.documento} - {pagamento.descricao or pagamento.forma}",
            documento=venda.documento,
            Idnatureza=natureza,
            FormaPagamento=pagamento.forma,
            caixa=caixa,
            receber_item=item,
        )
        gerar_lancamento_contabil_movimentacao(movimento)
        self._consolidar_caixa_master(venda, natureza, valor)

    def _registrar_recebivel_bancario(self, venda: VendaPdv, natureza: Nat_Lancamento, pagamento: VendaPdvPagamento, forma: FormaPagamento, valor_bruto: Decimal, item: ReceberItem):
        taxa_percentual = Decimal(forma.taxa_percentual or 0)
        taxa_fixa = Decimal(forma.taxa_fixa or 0)
        taxa = money((money(valor_bruto) * taxa_percentual / Decimal("100")) + taxa_fixa)
        valor_liquido = money(max(Decimal("0.00"), money(valor_bruto) - taxa))
        data_prevista = timezone.localdate() + timedelta(days=int(forma.prazo_credito_dias or 0))
        historico = f"Recebivel {forma.descricao} PDV {venda.documento}"
        if forma.adquirente:
            historico = f"{historico} - {forma.adquirente}"
        if taxa > 0:
            historico = f"{historico} | bruto {money(valor_bruto)} taxa {taxa}"
        prazo = int(forma.prazo_credito_dias or 0)
        status_movimento = MovimentacaoFinanceira.STATUS_EFETIVA if prazo <= 0 else MovimentacaoFinanceira.STATUS_PREVISTA
        origem_movimento = MovimentacaoFinanceira.ORIGEM_RECEBER if prazo <= 0 else MovimentacaoFinanceira.ORIGEM_CARTAO
        if prazo <= 0 and forma.conta_liquidacao_id:
            conta = forma.conta_liquidacao
            conta.saldo_atual = money(conta.saldo_atual) + valor_liquido
            conta.save(update_fields=["saldo_atual"])

        movimento = MovimentacaoFinanceira.objects.create(
            empresa=venda.empresa,
            idloja=venda.loja,
            data_movimento=data_prevista,
            tipo=MovimentacaoFinanceira.TIPO_ENTRADA,
            status=status_movimento,
            origem=origem_movimento,
            valor=valor_liquido,
            historico=historico[:255],
            documento=venda.documento,
            Idnatureza=natureza,
            FormaPagamento=pagamento.forma,
            conta_bancaria=forma.conta_liquidacao,
            receber_item=item,
        )
        gerar_lancamento_contabil_movimentacao(movimento)

    def _consolidar_caixa_master(self, venda: VendaPdv, natureza: Nat_Lancamento, valor: Decimal):
        master = (
            Caixa.objects.select_for_update()
            .filter(empresa=venda.empresa, tipo_caixa=Caixa.TIPO_MASTER, ativo=True)
            .order_by("Idcaixa")
            .first()
        )
        if not master:
            master = Caixa.objects.create(
                empresa=venda.empresa,
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
        movimento = MovimentacaoFinanceira.objects.create(
            empresa=venda.empresa,
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
        gerar_lancamento_contabil_movimentacao(movimento)

    def _autorizar_nfce(self, venda: VendaPdv, contingencia: bool = False) -> NFCe:
        numero = int(venda.documento) if str(venda.documento or "").isdigit() else _proximo_numero_nfce()
        nfce = NFCe.objects.create(venda=venda, numero=numero, status=NFCe.Status.EMITINDO)
        nfce.chave_acesso = _gerar_chave(nfce)
        if contingencia:
            nfce.protocolo = ""
            nfce.qr_code_url = ""
            nfce.xml = f"<NFCe ambiente=\"homologacao\" chave=\"{nfce.chave_acesso}\" venda=\"{venda.documento}\" contingencia=\"true\" />"
            nfce.status = NFCe.Status.CONTINGENCIA
            nfce.retorno_codigo = "FS"
            nfce.retorno_mensagem = "NFC-e emitida em contingencia. Aguardando transmissao/autorizacao da SEFAZ."
            nfce.autorizada_em = None
        else:
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
        total_impostos = money(sum((item.total_impostos for item in venda.itens.all()), Decimal("0.00")))
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
                    "ncm": item.ncm,
                    "cfop": item.cfop,
                    "total_impostos": str(money(item.total_impostos)),
                }
                for item in venda.itens.all()
            ],
            "subtotal": str(money(venda.subtotal)),
            "desconto": str(money(venda.desconto_itens + venda.desconto_geral)),
            "total": str(money(venda.total)),
            "total_impostos": str(total_impostos),
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

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        empresa_id = self.request.query_params.get("empresa") if user.is_superuser else getattr(user, "empresa_id", None)
        status_nfce = self.request.query_params.get("status")
        loja_id = self.request.query_params.get("loja")
        if empresa_id:
            qs = qs.filter(venda__empresa_id=empresa_id)
        elif not user.is_superuser:
            return qs.none()
        if status_nfce:
            qs = qs.filter(status=status_nfce)
        if loja_id:
            qs = qs.filter(venda__loja_id=loja_id)
        return qs

    @action(detail=False, methods=["get"], url_path="contingencia-pendente")
    def contingencia_pendente(self, request):
        qs = self.filter_queryset(self.get_queryset().filter(status=NFCe.Status.CONTINGENCIA))
        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

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
        empresa_id = self._empresa_id_usuario()
        loja = self.request.query_params.get("loja")
        cliente = self.request.query_params.get("cliente")
        venda = self.request.query_params.get("venda")
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not self.request.user.is_superuser:
            return qs.none()
        if loja:
            qs = qs.filter(loja_id=loja)
        if cliente:
            qs = qs.filter(cliente_id=cliente)
        if venda:
            qs = qs.filter(venda_id=venda)
        return qs

    def _empresa_id_usuario(self):
        user = self.request.user
        if user.is_superuser:
            return self.request.query_params.get("empresa")
        return getattr(user, "empresa_id", None)

    @action(detail=False, methods=["get"], url_path="vendas-devolviveis")
    def vendas_devolviveis(self, request):
        qs = (
            VendaPdv.objects.select_related("loja", "cliente", "vendedor", "caixa")
            .prefetch_related("itens", "pagamentos", "devolucoes__itens", "cashback_creditos", "cashback_usos")
            .filter(status=VendaPdv.Status.FINALIZADA)
            .order_by("-data_venda")
        )
        empresa_id = self._empresa_id_usuario()
        loja = request.query_params.get("loja")
        cliente = request.query_params.get("cliente")
        documento = (request.query_params.get("documento") or "").strip()
        ean = (request.query_params.get("ean") or "").strip()
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not request.user.is_superuser:
            return Response([], status=status.HTTP_200_OK)
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
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        elif not request.user.is_superuser:
            return Response({"detail": "Usuário sem empresa vinculada."}, status=status.HTTP_400_BAD_REQUEST)
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
        empresa_id = self._empresa_id_usuario()
        if empresa_id:
            venda_qs = venda_qs.filter(empresa_id=empresa_id)
        elif not request.user.is_superuser:
            return Response({"detail": "Usuário sem empresa vinculada."}, status=status.HTTP_400_BAD_REQUEST)
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
            empresa=venda.empresa,
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
        self._estornar_cmv(devolucao)
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

    def _atualizar_custo_medio_retorno(self, sku: ProdutoDetalhe, saldo_anterior: int, quantidade: int, custo_retorno: Decimal) -> Decimal:
        custo_retorno = Decimal(custo_retorno or 0)
        custo_atual = Decimal(sku.custo_medio or sku.custo_ultima_compra or sku.custo_original or 0)
        if custo_retorno <= 0:
            return custo_atual
        saldo_anterior_dec = Decimal(max(int(saldo_anterior or 0), 0))
        quantidade_dec = Decimal(max(int(quantidade or 0), 0))
        saldo_posterior = saldo_anterior_dec + quantidade_dec
        if saldo_posterior <= 0:
            custo_medio = custo_retorno
        else:
            custo_medio = ((saldo_anterior_dec * custo_atual) + (quantidade_dec * custo_retorno)) / saldo_posterior
        sku.custo_medio = custo_medio.quantize(Decimal("0.0001"))
        sku.save(update_fields=["custo_medio"])
        return sku.custo_medio

    def _registrar_item_devolucao(self, devolucao: VendaDevolucao, venda_item: VendaPdvItem, quantidade: int, desconto: Decimal):
        estoque = Estoque.objects.select_for_update().get(CodigodeBarra=venda_item.ean, Idloja=devolucao.loja)
        anterior = estoque.Estoque or 0
        posterior = anterior + quantidade
        custo_unitario = Decimal(venda_item.custo_unitario or 0)
        custo_medio_apos = self._atualizar_custo_medio_retorno(venda_item.sku, anterior, quantidade, custo_unitario)
        estoque.Estoque = posterior
        estoque.referencia = venda_item.referencia or estoque.referencia
        estoque.save(update_fields=["Estoque", "referencia"])
        EstoqueMovimentacao.objects.create(
            Idloja=devolucao.loja,
            CodigodeBarra=venda_item.ean,
            referencia=venda_item.referencia or "",
            tipo=EstoqueMovimentacao.TIPO_ENTRADA,
            quantidade=quantidade,
            custo_unitario=custo_unitario,
            custo_total=money(Decimal(quantidade) * custo_unitario),
            custo_medio_apos=custo_medio_apos,
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
            custo_unitario=venda_item.custo_unitario,
        )

    def _natureza_cmv(self, empresa=None) -> Nat_Lancamento:
        natureza = (
            Nat_Lancamento.objects
            .filter(empresa=empresa, ativo=True, natureza_operacao="DESPESA")
            .filter(models.Q(codigo="2100") | models.Q(descricao__icontains="CMV") | models.Q(descricao__icontains="mercadoria vendida"))
            .order_by("codigo")
            .first()
        )
        if natureza:
            return natureza

        plano = None
        try:
            from cadastros.models import PlanoContabil
            plano = (
                PlanoContabil.objects
                .filter(empresa=empresa, ativa=True, classe=PlanoContabil.CLASSE_CUSTO)
                .filter(models.Q(codigo="5.1.01") | models.Q(descricao__icontains="CMV"))
                .order_by("codigo")
                .first()
            )
        except Exception:
            plano = None

        return Nat_Lancamento.objects.create(
            empresa=empresa,
            codigo="2100",
            categoria_principal="CUSTOS DAS MERCADORIAS",
            subcategoria="CMV",
            descricao="CMV - Custo da mercadoria vendida",
            tipo="DESPESA",
            status="ATIVO",
            tipo_natureza="DEBITO",
            natureza_operacao="DESPESA",
            categoria_gerencial="CMV",
            movimenta_financeiro=False,
            entra_dre=True,
            plano_contabil=plano,
            conta_contabil=plano.codigo if plano else None,
            ativo=True,
        )

    def _estornar_cmv(self, devolucao: VendaDevolucao):
        if MovimentacaoFinanceira.objects.filter(
            empresa=devolucao.empresa,
            origem=MovimentacaoFinanceira.ORIGEM_CMV,
            documento=devolucao.documento,
        ).exists():
            return

        total_cmv = money(sum((Decimal(item.cmv_total or 0) for item in devolucao.itens.all()), Decimal("0.00")))
        if total_cmv <= 0:
            return

        movimento = MovimentacaoFinanceira.objects.create(
            empresa=devolucao.empresa,
            idloja=devolucao.loja,
            data_movimento=timezone.localdate(),
            tipo=MovimentacaoFinanceira.TIPO_ENTRADA,
            status=MovimentacaoFinanceira.STATUS_EFETIVA,
            origem=MovimentacaoFinanceira.ORIGEM_CMV,
            valor=total_cmv,
            historico=f"Estorno CMV devolução {devolucao.documento}",
            documento=devolucao.documento,
            Idnatureza=self._natureza_cmv(devolucao.empresa),
            FormaPagamento="CMV",
        )
        gerar_lancamento_contabil_movimentacao(movimento)

    def _registrar_credito_cliente(self, devolucao: VendaDevolucao):
        vale, created = ValeTroca.objects.get_or_create(
            devolucao=devolucao,
            defaults={
                "empresa": devolucao.empresa,
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
        receber = Receber.objects.filter(empresa=devolucao.empresa, pedido_venda=devolucao.venda_id).prefetch_related("itens").first()
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
