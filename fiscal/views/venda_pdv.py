from decimal import Decimal
from collections import defaultdict
from random import randint

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from datetime import datetime, time
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from accounts.permissions import HasModuleRole

from cadastros.models import Nat_Lancamento
from financeiro.models import Caixa, MovimentacaoFinanceira, Receber, ReceberItem
from fiscal.models import NFCe, VendaPdv, VendaPdvItem, VendaPdvPagamento
from fiscal.models.venda_pdv import money
from fiscal.serializers import NFCeSerializer, VendaPdvSerializer
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
        produtos = defaultdict(lambda: {"produto": "", "referencia": "", "quantidade": 0, "total": Decimal("0")})
        colecoes = defaultdict(lambda: {"colecao": "Sem coleção", "quantidade": 0, "total": Decimal("0")})
        total_vendas = Decimal("0")
        total_itens = 0

        for venda in vendas:
            total_vendas += money(venda.total)
            vendedor = vendedores[venda.vendedor_id]
            vendedor["vendedor"] = venda.vendedor.nomefuncionario
            vendedor["comissao_percentual"] = Decimal(getattr(venda.vendedor, "comissao_percentual", 0) or 0)
            vendedor["vendas"] += 1
            vendedor["total"] += money(venda.total)

            for item in venda.itens.all():
                quantidade = int(item.quantidade or 0)
                total_item = money(item.total_item)
                total_itens += quantidade
                vendedor["itens"] += quantidade

                produto = produtos[item.produto_id]
                produto["produto"] = item.descricao
                produto["referencia"] = item.referencia
                produto["quantidade"] += quantidade
                produto["total"] += total_item

                colecao_nome = getattr(getattr(item.produto, "colecao", None), "Descricao", None) or "Sem coleção"
                colecao = colecoes[colecao_nome]
                colecao["colecao"] = colecao_nome
                colecao["quantidade"] += quantidade
                colecao["total"] += total_item

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

        produtos_payload = [
            {**row, "total": str(money(row["total"]))}
            for row in produtos.values()
        ]
        colecoes_payload = [
            {**row, "total": str(money(row["total"]))}
            for row in colecoes.values()
        ]

        vendedores_payload.sort(key=lambda item: Decimal(item["total"]), reverse=True)
        produtos_payload.sort(key=lambda item: Decimal(item["total"]), reverse=True)
        colecoes_payload.sort(key=lambda item: Decimal(item["total"]), reverse=True)
        comissao_total = sum(Decimal(row["comissao"]) for row in vendedores_payload)

        return Response({
            "resumo": {
                "vendas": len(vendas),
                "itens": total_itens,
                "total": str(money(total_vendas)),
                "ticket_medio": str(money(total_vendas / len(vendas) if vendas else 0)),
                "comissao_total": str(money(comissao_total)),
            },
            "vendedores": vendedores_payload,
            "produtos": produtos_payload[:30],
            "colecoes": colecoes_payload,
        })

    def _vendas_relatorio(self, request):
        qs = (
            VendaPdv.objects.select_related("loja", "vendedor")
            .prefetch_related("itens__produto__colecao")
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

        venda.subtotal = money(subtotal)
        venda.desconto_itens = money(desconto_itens)
        venda.total = total
        venda.valor_recebido = total_pago
        venda.troco = money(total_pago - total) if total_pago > total else Decimal("0.00")
        venda.forma_pagamento = self._forma_resumo(pagamentos_payload)
        venda.save(update_fields=["subtotal", "desconto_itens", "total", "valor_recebido", "troco", "forma_pagamento", "atualizado_em"])
        self._registrar_pagamentos(venda, pagamentos_payload)

        self._registrar_financeiro(venda)
        nfce = self._autorizar_nfce(venda)
        payload = VendaPdvSerializer(venda, context={"request": request}).data
        payload["cupom"] = self._cupom(venda, nfce)
        return Response(payload, status=status.HTTP_201_CREATED)

    def _normalizar_pagamentos(self, data) -> list[dict]:
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

    def _forma_resumo(self, pagamentos: list[dict]) -> str:
        if len(pagamentos) == 1:
            return pagamentos[0]["forma"]
        return "MULTIPLO"

    def _registrar_pagamentos(self, venda: VendaPdv, pagamentos: list[dict]):
        for pagamento in pagamentos:
            VendaPdvPagamento.objects.create(venda=venda, **pagamento)

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

    def _registrar_financeiro(self, venda: VendaPdv):
        natureza = Nat_Lancamento.objects.filter(codigo__startswith="1.").order_by("codigo").first()
        if not natureza:
            return

        pagamentos = list(venda.pagamentos.all())
        total_pago = money(sum((money(pagamento.valor) for pagamento in pagamentos), Decimal("0")))
        valor_receber = money(money(venda.total) - total_pago)

        saldo_para_caixa = money(venda.total)
        for pagamento in pagamentos:
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
        if valor_receber <= 0:
            return

        receber = Receber.objects.create(
            idloja=venda.loja,
            idcliente=venda.cliente,
            Titulo=f"Venda PDV {venda.documento}",
            Documento=venda.documento,
            Data_emissao=timezone.localdate(),
            Valor_total=valor_receber,
            Previsao=False,
            FormaPagamento=venda.forma_pagamento,
            Idnatureza=natureza,
            pedido_venda=venda.pk,
        )
        ReceberItem.objects.create(
            Idreceber=receber,
            parcela_n=1,
            status=ReceberItem.STATUS_EFETIVO,
            Data_vencimento=timezone.localdate(),
            valor_parcela=valor_receber,
            FormaPagamento=venda.forma_pagamento,
            Previsao=False,
            Idnatureza=natureza,
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
