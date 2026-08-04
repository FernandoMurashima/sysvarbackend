from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.permissions import HasEffectiveModuleAccess

from auditoria.models import AuditLog
from cadastros.models import Cliente, Empresa, Funcionarios, Loja
from financeiro.models import Caixa, ContaBancaria, MovimentacaoFinanceira, PagarItem, ReceberItem
from fiscal.models.venda_pdv import (
    VendaDevolucao,
    VendaDevolucaoItem,
    VendaPdv,
    VendaPdvItem,
    VendaPdvPagamento,
)
from produto.models import Colecao, Cor, Estoque, EstoqueMovimentacao, Grupo, Produto, ProdutoDetalhe, Subgrupo, TabelaprecoProduto, Tamanho


ZERO = Decimal("0")


def money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"))


def number(value):
    if isinstance(value, Decimal):
        return float(value)
    return value or 0


def pct(current, previous):
    current = Decimal(str(current or 0))
    previous = Decimal(str(previous or 0))
    if previous == 0:
        return 100 if current > 0 else 0
    return float(((current - previous) / previous * Decimal("100")).quantize(Decimal("0.01")))


def parse_date(value, default):
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError:
        return default


def default_period():
    today = timezone.localdate()
    return today.replace(day=1), today


def previous_period(start, end, mode):
    days = (end - start).days + 1
    if mode == "ano_anterior":
        return start.replace(year=start.year - 1), end.replace(year=end.year - 1)
    if mode == "mes_anterior":
        first_current = start.replace(day=1)
        prev_end = first_current - timedelta(days=1)
        prev_start = prev_end.replace(day=1)
        return prev_start, prev_end
    prev_end = start - timedelta(days=1)
    return prev_end - timedelta(days=days - 1), prev_end


def date_points(start, end):
    days = (end - start).days + 1
    return [start + timedelta(days=i) for i in range(max(days, 0))]


def as_date(value):
    if hasattr(value, "isoformat"):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return value


def period_datetimes(start, end):
    tz = timezone.get_current_timezone()
    return (
        timezone.make_aware(datetime.combine(start, time.min), tz),
        timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz),
    )


class DashboardExecutivoView(APIView):
    permission_classes = [IsAuthenticated, HasEffectiveModuleAccess]
    required_module = "relatorios"

    def get(self, request):
        if not self._can_view(request.user):
            self._audit(request, "dashboard_executivo_negado", False)
            raise PermissionDenied("Usuário sem permissão para acessar o dashboard executivo.")

        empresa, lojas = self._scope(request)
        start_default, end_default = default_period()
        start = parse_date(request.query_params.get("inicio") or request.query_params.get("de"), start_default)
        end = parse_date(request.query_params.get("fim") or request.query_params.get("ate"), end_default)
        if end < start:
            start, end = end, start
        comp_start, comp_end = previous_period(start, end, request.query_params.get("comparacao", "periodo_anterior"))

        loja_ids = [loja.pk for loja in lojas]
        vendas = self._vendas_qs(empresa.pk, loja_ids, start, end, request)
        vendas_prev = self._vendas_qs(empresa.pk, loja_ids, comp_start, comp_end, request)
        devolucoes = self._devolucoes_qs(empresa.pk, loja_ids, start, end)
        devolucoes_prev = self._devolucoes_qs(empresa.pk, loja_ids, comp_start, comp_end)

        payload = {
            "periodo": {"inicio": start.isoformat(), "fim": end.isoformat()},
            "comparacao": {"inicio": comp_start.isoformat(), "fim": comp_end.isoformat()},
            "empresa": {"id": empresa.pk, "nome": str(empresa)},
            "filtros": self._filters_payload(request.user, empresa, lojas),
            "indicadores": self._indicators(empresa, loja_ids, vendas, vendas_prev, devolucoes, devolucoes_prev),
            "graficos": {
                "faturamento_diario": self._daily_sales(vendas, vendas_prev, start, end, comp_start),
                "pagamentos": self._payments(vendas),
                "lojas": self._sales_by_store(vendas),
            },
            "tabelas": {
                "vendedores": self._sales_by_seller(vendas),
                "produtos": self._top_products(vendas),
                "evolucao": self._evolution(vendas, vendas_prev, devolucoes, devolucoes_prev),
                "metas": self._goals(empresa, loja_ids, vendas, start, end),
            },
            "alertas": self._alerts(empresa, loja_ids, vendas, start, end),
            "atualizado_em": timezone.now().isoformat(),
        }
        self._audit(request, "dashboard_executivo_acessado", True)
        return Response(payload)

    def _can_view(self, user):
        role = (getattr(user, "type", "") or "").strip().lower()
        return bool(user.is_superuser or role in {"admin", "administrador", "diretor"})

    def _scope(self, request):
        user = request.user
        empresas = Empresa.objects.filter(ativo=True).order_by("nome")
        empresa_id = request.query_params.get("empresa")
        if user.is_superuser:
            if empresa_id:
                empresa = empresas.filter(pk=empresa_id).first()
            else:
                empresa_venda = (
                    VendaPdv.objects.filter(status=VendaPdv.Status.FINALIZADA, empresa__ativo=True)
                    .order_by("-data_venda")
                    .values_list("empresa_id", flat=True)
                    .first()
                )
                empresa = empresas.filter(pk=empresa_venda).first() if empresa_venda else None
                if not empresa:
                    empresa = getattr(user, "empresa", None)
                    if empresa and not empresa.ativo:
                        empresa = None
                empresa = empresa or empresas.first()
        else:
            empresa = user.empresa
        if not empresa:
            raise PermissionDenied("Usuário sem empresa vinculada.")

        lojas = Loja.objects.filter(empresa=empresa, ativo=True).order_by("nome_loja")
        if not user.is_superuser:
            allowed = set(user.lojas.values_list("pk", flat=True))
            if user.loja_id:
                allowed.add(user.loja_id)
            if allowed:
                lojas = lojas.filter(pk__in=allowed)

        loja_param = request.query_params.get("loja") or request.query_params.get("lojas")
        if loja_param:
            selected = [int(x) for x in loja_param.split(",") if x.strip().isdigit()]
            if selected:
                available = set(lojas.values_list("pk", flat=True))
                if not set(selected).issubset(available):
                    raise PermissionDenied("Loja fora do escopo do usuário.")
                lojas = lojas.filter(pk__in=selected)
        return empresa, list(lojas)

    def _vendas_qs(self, empresa_id, loja_ids, start, end, request):
        start_dt, end_dt = period_datetimes(start, end)
        qs = VendaPdv.objects.filter(
            empresa_id=empresa_id,
            loja_id__in=loja_ids,
            status=VendaPdv.Status.FINALIZADA,
            data_venda__gte=start_dt,
            data_venda__lt=end_dt,
        )
        vendedor = request.query_params.get("vendedor")
        canal = request.query_params.get("canal")
        if vendedor and vendedor.isdigit():
            qs = qs.filter(vendedor_id=int(vendedor))
        if canal:
            qs = qs.filter(pagamentos__forma__iexact=canal).distinct()

        item_filter = {}
        for param, field in (
            ("grupo", "produto__grupo_id"),
            ("subgrupo", "produto__subgrupo_id"),
            ("colecao", "produto__colecao_id"),
        ):
            value = request.query_params.get(param)
            if value and value.isdigit():
                item_filter[field] = int(value)
        estacao = request.query_params.get("estacao")
        if estacao:
            item_filter["produto__colecao__Estacao"] = estacao
        if item_filter:
            ids = VendaPdvItem.objects.filter(venda__in=qs, **item_filter).values_list("venda_id", flat=True)
            qs = qs.filter(pk__in=ids)
        return qs.distinct()

    def _devolucoes_qs(self, empresa_id, loja_ids, start, end):
        start_dt, end_dt = period_datetimes(start, end)
        return VendaDevolucao.objects.filter(
            empresa_id=empresa_id,
            loja_id__in=loja_ids,
            status=VendaDevolucao.Status.FINALIZADA,
            criado_em__gte=start_dt,
            criado_em__lt=end_dt,
        )

    def _sum(self, qs, field):
        return money(qs.aggregate(total=Sum(field))["total"] or ZERO)

    def _count(self, qs):
        return qs.count()

    def _sales_metrics(self, vendas, devolucoes):
        faturamento = self._sum(vendas, "total")
        descontos = money(self._sum(vendas, "desconto_itens") + self._sum(vendas, "desconto_geral"))
        devolucao_valor = self._sum(devolucoes, "credito_cliente")
        venda_ids = vendas.values_list("pk", flat=True)
        item_qs = VendaPdvItem.objects.filter(venda_id__in=venda_ids)
        devolucao_items = VendaDevolucaoItem.objects.filter(devolucao__in=devolucoes)
        qtd_itens = item_qs.aggregate(total=Sum("quantidade"))["total"] or ZERO
        qtd_dev = devolucao_items.aggregate(total=Sum("quantidade"))["total"] or ZERO
        cmv = money(self._sum(item_qs, "cmv_total") - self._sum(devolucao_items, "cmv_total"))
        receita_liquida = money(faturamento - devolucao_valor)
        lucro = money(receita_liquida - cmv)
        margem = float((lucro / receita_liquida * Decimal("100")).quantize(Decimal("0.01"))) if receita_liquida else 0
        quantidade_vendas = vendas.count()
        ticket = money(receita_liquida / quantidade_vendas) if quantidade_vendas else ZERO
        return {
            "faturamento": faturamento,
            "receita_liquida": receita_liquida,
            "descontos": descontos,
            "devolucoes": devolucao_valor,
            "quantidade_vendas": quantidade_vendas,
            "itens_vendidos": max(number(qtd_itens - qtd_dev), 0),
            "ticket_medio": ticket,
            "cmv": cmv,
            "lucro_bruto": lucro,
            "margem_bruta": margem,
        }

    def _indicators(self, empresa, loja_ids, vendas, vendas_prev, devolucoes, devolucoes_prev):
        current = self._sales_metrics(vendas, devolucoes)
        previous = self._sales_metrics(vendas_prev, devolucoes_prev)
        canceladas = VendaPdv.objects.filter(
            empresa=empresa,
            loja_id__in=loja_ids,
            status=VendaPdv.Status.CANCELADA,
        )
        caixa = Caixa.objects.filter(empresa=empresa, ativo=True, idloja_id__in=loja_ids)
        pagar_aberto = PagarItem.objects.filter(
            Idpagar__empresa=empresa,
            Idpagar__idloja_id__in=loja_ids,
            status__in=[PagarItem.STATUS_PREVISTO, PagarItem.STATUS_EFETIVO],
        )
        receber_aberto = ReceberItem.objects.filter(
            Idreceber__empresa=empresa,
            Idreceber__idloja_id__in=loja_ids,
            status__in=[ReceberItem.STATUS_PREVISTO, ReceberItem.STATUS_EFETIVO],
        )
        estoque = self._stock_value(empresa.pk, loja_ids)
        cards = [
            self._card("faturamento", "Faturamento", current["faturamento"], previous["faturamento"], "money"),
            self._card("receita_liquida", "Receita líquida", current["receita_liquida"], previous["receita_liquida"], "money"),
            self._card("ticket_medio", "Ticket médio", current["ticket_medio"], previous["ticket_medio"], "money"),
            self._card("quantidade_vendas", "Quantidade de vendas", current["quantidade_vendas"], previous["quantidade_vendas"], "number"),
            self._card("margem_bruta", "Margem bruta", current["margem_bruta"], previous["margem_bruta"], "percent"),
            self._card("lucro_bruto", "Lucro bruto", current["lucro_bruto"], previous["lucro_bruto"], "money"),
            self._card("cmv", "CMV", current["cmv"], previous["cmv"], "money", inverse=True),
            self._card("descontos", "Descontos concedidos", current["descontos"], previous["descontos"], "money", inverse=True),
            self._card("devolucoes", "Devoluções", current["devolucoes"], previous["devolucoes"], "money", inverse=True),
            self._card("cancelamentos", "Cancelamentos", self._sum(canceladas, "total"), ZERO, "money", inverse=True),
            self._card("saldo_caixa", "Saldo de caixa", self._sum(caixa, "saldo_atual"), ZERO, "money"),
            self._card("contas_pagar", "Contas a pagar", self._sum(pagar_aberto, "valor_parcela"), ZERO, "money", inverse=True),
            self._card("contas_receber", "Contas a receber", self._sum(receber_aberto, "valor_parcela"), ZERO, "money"),
            self._card("valor_estoque", "Valor do estoque", estoque["valor"], ZERO, "money"),
        ]
        return {"cards": cards, "base": {key: number(value) for key, value in current.items()}, "estoque": estoque}

    def _card(self, key, label, value, previous, kind, inverse=False):
        trend = pct(value, previous)
        return {
            "key": key,
            "label": label,
            "value": number(value),
            "previous": number(previous),
            "variation": trend,
            "kind": kind,
            "inverse": inverse,
            "positive": (trend <= 0 if inverse else trend >= 0),
        }

    def _stock_value(self, empresa_id, loja_ids):
        stocks = Estoque.objects.filter(Idloja_id__in=loja_ids)
        eans = list(stocks.values_list("CodigodeBarra", flat=True).distinct())
        skus = ProdutoDetalhe.objects.select_related("produto").filter(produto__empresa_id=empresa_id, ean13__in=eans)
        sku_by_ean = {sku.ean13: sku for sku in skus}
        prices = {
            row["produto_id"]: row["valor"] or ZERO
            for row in TabelaprecoProduto.objects.filter(produto__empresa_id=empresa_id, ativo=True)
            .values("produto_id")
            .annotate(valor=Sum("preco"))
        }
        custo_total = ZERO
        venda_total = ZERO
        itens = ZERO
        baixos = 0
        for row in stocks.values("CodigodeBarra", "Estoque"):
            qty = Decimal(row["Estoque"] or 0)
            if qty <= 0:
                continue
            sku = sku_by_ean.get(row["CodigodeBarra"])
            if not sku:
                continue
            produto = sku.produto
            custo = sku.custo_medio or sku.custo_ultima_compra or sku.custo_original or produto.custo_medio or produto.custo_ultima_compra or produto.custo_original or ZERO
            preco = prices.get(produto.pk, ZERO)
            custo_total += qty * Decimal(custo)
            venda_total += qty * Decimal(preco)
            itens += qty
            if qty <= Decimal("3"):
                baixos += 1
        return {"valor": number(money(custo_total)), "valor_venda": number(money(venda_total)), "itens": number(itens), "estoque_baixo": baixos}

    def _daily_sales(self, vendas, vendas_prev, start, end, comp_start):
        current_map = {
            as_date(row["dia"]): money(row["total"])
            for row in vendas.extra(select={"dia": "date(data_venda)"}).values("dia").annotate(total=Sum("total"))
        }
        prev_map = {
            as_date(row["dia"]): money(row["total"])
            for row in vendas_prev.extra(select={"dia": "date(data_venda)"}).values("dia").annotate(total=Sum("total"))
        }
        points = []
        for i, day in enumerate(date_points(start, end)):
            prev_day = comp_start + timedelta(days=i)
            points.append({
                "data": day.isoformat(),
                "atual": number(current_map.get(day, ZERO)),
                "anterior": number(prev_map.get(prev_day, ZERO)),
            })
        return points

    def _payments(self, vendas):
        rows = (
            VendaPdvPagamento.objects.filter(venda__in=vendas)
            .values("forma")
            .annotate(total=Sum("valor"), vendas=Count("venda", distinct=True))
            .order_by("-total")
        )
        total = sum((Decimal(row["total"] or 0) for row in rows), ZERO)
        return [
            {"forma": row["forma"] or "Não informado", "total": number(money(row["total"])), "vendas": row["vendas"], "percentual": number((Decimal(row["total"] or 0) / total * 100).quantize(Decimal("0.01"))) if total else 0}
            for row in rows
        ]

    def _sales_by_store(self, vendas):
        return [
            {"loja": row["loja__nome_loja"], "total": number(money(row["total"])), "vendas": row["vendas"]}
            for row in vendas.values("loja__nome_loja").annotate(total=Sum("total"), vendas=Count("pk")).order_by("-total")
        ]

    def _sales_by_seller(self, vendas):
        return [
            {"vendedor": row["vendedor__nomefuncionario"] or "Sem vendedor", "total": number(money(row["total"])), "vendas": row["vendas"]}
            for row in vendas.values("vendedor__nomefuncionario").annotate(total=Sum("total"), vendas=Count("pk")).order_by("-total")[:10]
        ]

    def _top_products(self, vendas):
        rows = (
            VendaPdvItem.objects.filter(venda__in=vendas)
            .values("descricao", "referencia")
            .annotate(qtd=Sum("quantidade"), total=Sum("total_item"), cmv=Sum("cmv_total"))
            .order_by("-total")[:10]
        )
        return [{"produto": row["descricao"], "referencia": row["referencia"], "qtd": number(row["qtd"]), "total": number(money(row["total"])), "cmv": number(money(row["cmv"]))} for row in rows]

    def _evolution(self, vendas, vendas_prev, devolucoes, devolucoes_prev):
        cur = self._sales_metrics(vendas, devolucoes)
        prv = self._sales_metrics(vendas_prev, devolucoes_prev)
        keys = [
            ("Faturamento", "faturamento", "money"),
            ("Receita líquida", "receita_liquida", "money"),
            ("Ticket médio", "ticket_medio", "money"),
            ("Quantidade de vendas", "quantidade_vendas", "number"),
            ("Margem bruta", "margem_bruta", "percent"),
            ("Lucro bruto", "lucro_bruto", "money"),
        ]
        return [{"indicador": label, "atual": number(cur[key]), "anterior": number(prv[key]), "diferenca": number(Decimal(str(cur[key] or 0)) - Decimal(str(prv[key] or 0))), "variacao": pct(cur[key], prv[key]), "kind": kind} for label, key, kind in keys]

    def _goals(self, empresa, loja_ids, vendas, start, end):
        meta = money(Funcionarios.objects.filter(empresa=empresa, ativo=True, idloja_id__in=loja_ids).aggregate(total=Sum("meta"))["total"] or ZERO)
        metric = self._sales_metrics(vendas, VendaDevolucao.objects.none())
        novos_clientes = Cliente.objects.filter(empresa=empresa, data_cadastro__date__gte=start, data_cadastro__date__lte=end).count()
        ticket_meta = Decimal("0")
        if metric["quantidade_vendas"]:
            ticket_meta = Decimal("500")
        rows = [
            {"meta": "Faturamento", "objetivo": number(meta), "realizado": number(metric["faturamento"])},
            {"meta": "Lucro bruto", "objetivo": number((meta * Decimal("0.35")).quantize(Decimal("0.01"))), "realizado": number(metric["lucro_bruto"])},
            {"meta": "Novos clientes", "objetivo": 50, "realizado": novos_clientes},
            {"meta": "Ticket médio", "objetivo": number(ticket_meta), "realizado": number(metric["ticket_medio"])},
        ]
        for row in rows:
            objetivo = Decimal(str(row["objetivo"] or 0))
            realizado = Decimal(str(row["realizado"] or 0))
            row["percentual"] = float((realizado / objetivo * Decimal("100")).quantize(Decimal("0.01"))) if objetivo else 0
        return rows

    def _alerts(self, empresa, loja_ids, vendas, start, end):
        today = timezone.localdate()
        pagar_vencido = PagarItem.objects.filter(
            Idpagar__empresa=empresa,
            Idpagar__idloja_id__in=loja_ids,
            status__in=[PagarItem.STATUS_PREVISTO, PagarItem.STATUS_EFETIVO],
            Data_vencimento__lt=today,
        )
        receber_vencido = ReceberItem.objects.filter(
            Idreceber__empresa=empresa,
            Idreceber__idloja_id__in=loja_ids,
            status__in=[ReceberItem.STATUS_PREVISTO, ReceberItem.STATUS_EFETIVO],
            Data_vencimento__lt=today,
        )
        stock = self._stock_value(empresa.pk, loja_ids)
        metric = self._sales_metrics(vendas, VendaDevolucao.objects.none())
        meta = money(Funcionarios.objects.filter(empresa=empresa, ativo=True, idloja_id__in=loja_ids).aggregate(total=Sum("meta"))["total"] or ZERO)
        alerts = []
        if pagar_vencido.exists():
            alerts.append({"tipo": "critico", "titulo": "Contas a pagar vencidas", "descricao": f"Total vencido R$ {money(self._sum(pagar_vencido, 'valor_parcela'))}."})
        if receber_vencido.exists():
            alerts.append({"tipo": "atencao", "titulo": "Contas a receber vencidas", "descricao": f"Total vencido R$ {money(self._sum(receber_vencido, 'valor_parcela'))}."})
        if stock["estoque_baixo"]:
            alerts.append({"tipo": "atencao", "titulo": "Estoque baixo", "descricao": f"{stock['estoque_baixo']} SKU(s) com saldo crítico."})
        if meta and Decimal(metric["faturamento"] or 0) < meta:
            falta = meta - Decimal(metric["faturamento"] or 0)
            alerts.append({"tipo": "info", "titulo": "Meta do período", "descricao": f"Faltam R$ {money(falta)} para atingir a meta."})
        if not alerts:
            alerts.append({"tipo": "ok", "titulo": "Operação sem alertas críticos", "descricao": "Nenhuma pendência crítica encontrada no período."})
        return alerts

    def _filters_payload(self, user, empresa, lojas):
        empresas = Empresa.objects.filter(ativo=True).order_by("nome") if user.is_superuser else Empresa.objects.filter(pk=empresa.pk)
        vendedores = Funcionarios.objects.filter(empresa=empresa, ativo=True, idloja_id__in=[loja.pk for loja in lojas]).order_by("nomefuncionario")
        return {
            "empresas": [{"id": e.pk, "nome": str(e)} for e in empresas],
            "lojas": [{"id": loja.pk, "nome": loja.nome_loja, "tipo": loja.tipo_unidade} for loja in lojas],
            "vendedores": [{"id": v.pk, "nome": v.nomefuncionario} for v in vendedores],
        }

    def _audit(self, request, action, allowed):
        try:
            AuditLog.objects.create(
                user=request.user if request.user.is_authenticated else None,
                action=action,
                app_label="dashboard",
                model="DashboardExecutivo",
                object_id=str(getattr(request.user, "pk", "")),
                changes={"allowed": allowed, "params": dict(request.query_params)},
                ip=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
            )
        except Exception:
            pass


class DashboardProdutosView(DashboardExecutivoView):
    required_modules = ["relatorios", "produtos"]
    def get(self, request):
        if not self._can_view(request.user):
            self._audit(request, "dashboard_produtos_negado", False)
            raise PermissionDenied("Usuário sem permissão para acessar o dashboard de produtos.")

        empresa, lojas = self._scope(request)
        start_default, end_default = default_period()
        start = parse_date(request.query_params.get("inicio") or request.query_params.get("de"), start_default)
        end = parse_date(request.query_params.get("fim") or request.query_params.get("ate"), end_default)
        if end < start:
            start, end = end, start
        comp_start, comp_end = previous_period(start, end, request.query_params.get("comparacao", "periodo_anterior"))

        loja_ids = [loja.pk for loja in lojas]
        vendas = self._vendas_qs(empresa.pk, loja_ids, start, end, request)
        vendas_prev = self._vendas_qs(empresa.pk, loja_ids, comp_start, comp_end, request)
        itens = self._items_qs(vendas, request)
        itens_prev = self._items_qs(vendas_prev, request)
        produtos = self._product_rows(itens, itens_prev, empresa.pk, loja_ids)
        total_faturamento = sum((Decimal(str(p["faturamento"])) for p in produtos), ZERO)
        total_qtd = sum((Decimal(str(p["qtd"])) for p in produtos), ZERO)
        total_cmv = sum((Decimal(str(p["cmv"])) for p in produtos), ZERO)
        total_prev = sum((Decimal(str(p["faturamento_anterior"])) for p in produtos), ZERO)
        qtd_prev = sum((Decimal(str(p["qtd_anterior"])) for p in produtos), ZERO)
        lucro = money(total_faturamento - total_cmv)
        margem = float((lucro / total_faturamento * Decimal("100")).quantize(Decimal("0.01"))) if total_faturamento else 0
        ticket = money(total_faturamento / vendas.count()) if vendas.count() else ZERO
        ticket_prev = money(total_prev / vendas_prev.count()) if vendas_prev.count() else ZERO

        payload = {
            "periodo": {"inicio": start.isoformat(), "fim": end.isoformat()},
            "comparacao": {"inicio": comp_start.isoformat(), "fim": comp_end.isoformat()},
            "empresa": {"id": empresa.pk, "nome": str(empresa)},
            "filtros": self._product_filters(request.user, empresa, lojas),
            "indicadores": {
                "cards": [
                    self._card("faturamento", "Faturamento total", total_faturamento, total_prev, "money"),
                    self._card("quantidade", "Quantidade vendida", total_qtd, qtd_prev, "number"),
                    self._card("ticket", "Ticket médio", ticket, ticket_prev, "money"),
                    self._card("margem", "Margem bruta média", margem, self._margin_prev(itens_prev), "percent"),
                    self._card("produtos", "Produtos vendidos", len(produtos), len([p for p in produtos if p["qtd_anterior"]]), "number"),
                ],
                "base": {
                    "faturamento": number(total_faturamento),
                    "quantidade": number(total_qtd),
                    "cmv": number(total_cmv),
                    "lucro": number(lucro),
                    "margem": margem,
                },
            },
            "graficos": {
                "categorias": self._group_rows(produtos, "categoria"),
                "colecoes": self._group_rows(produtos, "colecao"),
                "lojas": self._products_by_store(itens),
                "diario": self._daily_sales(vendas, vendas_prev, start, end, comp_start),
            },
            "tabelas": {
                "ranking": sorted(produtos, key=lambda p: p["faturamento"], reverse=True)[:10],
                "lucro": sorted(produtos, key=lambda p: p["lucro_bruto"], reverse=True)[:5],
                "quedas": self._falling_products(produtos)[:5],
                "por_loja": self._products_store_matrix(itens),
                "abc": self._abc(produtos),
                "cores": self._group_rows(produtos, "cor"),
                "tamanhos": self._group_rows(produtos, "tamanho"),
            },
            "insights": self._insights(produtos, total_faturamento),
            "atualizado_em": timezone.now().isoformat(),
        }
        self._audit(request, "dashboard_produtos_acessado", True)
        return Response(payload)

    def _items_qs(self, vendas, request):
        qs = VendaPdvItem.objects.filter(venda__in=vendas).select_related(
            "venda__loja", "produto__grupo", "produto__subgrupo", "produto__colecao", "sku__idcor", "sku__idtamanho"
        )
        filters = (
            ("grupo", "produto__grupo_id"),
            ("subgrupo", "produto__subgrupo_id"),
            ("colecao", "produto__colecao_id"),
            ("cor", "sku__idcor_id"),
            ("tamanho", "sku__idtamanho_id"),
        )
        for param, field in filters:
            value = request.query_params.get(param)
            if value and value.isdigit():
                qs = qs.filter(**{field: int(value)})
        estacao = request.query_params.get("estacao")
        if estacao:
            qs = qs.filter(produto__colecao__Estacao=estacao)
        term = (request.query_params.get("q") or request.query_params.get("busca") or "").strip()
        if term:
            qs = qs.filter(
                Q(descricao__icontains=term) |
                Q(referencia__icontains=term) |
                Q(ean__icontains=term) |
                Q(produto__descricao__icontains=term) |
                Q(produto__referencia__icontains=term)
            )
        return qs

    def _product_rows(self, itens, itens_prev, empresa_id, loja_ids):
        rows = {}
        prev = {}
        for item in itens_prev:
            key = item.produto_id or f"{item.referencia}-{item.descricao}"
            prev.setdefault(key, {"qtd": ZERO, "fat": ZERO})
            prev[key]["qtd"] += Decimal(item.quantidade or 0)
            prev[key]["fat"] += Decimal(item.total_item or 0)
        eans = set()
        for item in itens:
            key = item.produto_id or f"{item.referencia}-{item.descricao}"
            produto = item.produto
            sku = item.sku
            categoria = getattr(getattr(produto, "grupo", None), "Descricao", None) or "-"
            subcategoria = getattr(getattr(produto, "subgrupo", None), "Descricao", None) or "-"
            colecao = getattr(getattr(produto, "colecao", None), "Descricao", None) or "-"
            cor = item.cor or getattr(getattr(sku, "idcor", None), "Descricao", None) or "-"
            tamanho = item.tamanho or getattr(getattr(sku, "idtamanho", None), "Tamanho", None) or "-"
            row = rows.setdefault(key, {
                "id": produto.pk if produto else None,
                "produto": item.descricao or getattr(produto, "descricao", "-"),
                "referencia": item.referencia or getattr(produto, "referencia", "-"),
                "categoria": categoria,
                "subcategoria": subcategoria,
                "colecao": colecao,
                "cor": cor,
                "tamanho": tamanho,
                "qtd": ZERO,
                "faturamento": ZERO,
                "cmv": ZERO,
                "qtd_anterior": prev.get(key, {}).get("qtd", ZERO),
                "faturamento_anterior": prev.get(key, {}).get("fat", ZERO),
                "estoque": ZERO,
                "_eans": set(),
                "ultima_venda": None,
            })
            row["qtd"] += Decimal(item.quantidade or 0)
            row["faturamento"] += Decimal(item.total_item or 0)
            row["cmv"] += Decimal(item.cmv_total or 0)
            if item.ean:
                row["_eans"].add(item.ean)
                eans.add(item.ean)
            venda_data = item.venda.data_venda.date() if item.venda and item.venda.data_venda else None
            if venda_data and (not row["ultima_venda"] or venda_data > row["ultima_venda"]):
                row["ultima_venda"] = venda_data
        stock = {
            row["CodigodeBarra"]: Decimal(row["saldo"] or 0)
            for row in Estoque.objects.filter(Idloja_id__in=loja_ids, CodigodeBarra__in=eans)
            .values("CodigodeBarra")
            .annotate(saldo=Sum("Estoque"))
        }
        total = sum((row["faturamento"] for row in rows.values()), ZERO)
        out = []
        today = timezone.localdate()
        for row in rows.values():
            row["estoque"] = sum((stock.get(ean, ZERO) for ean in row["_eans"]), ZERO)
            lucro = money(row["faturamento"] - row["cmv"])
            margem = float((lucro / row["faturamento"] * Decimal("100")).quantize(Decimal("0.01"))) if row["faturamento"] else 0
            participacao = float((row["faturamento"] / total * Decimal("100")).quantize(Decimal("0.01"))) if total else 0
            dias = (today - row["ultima_venda"]).days if row["ultima_venda"] else None
            out.append({
                **{k: v for k, v in row.items() if not k.startswith("_")},
                "qtd": number(row["qtd"]),
                "faturamento": number(money(row["faturamento"])),
                "cmv": number(money(row["cmv"])),
                "lucro_bruto": number(lucro),
                "margem_bruta": margem,
                "participacao": participacao,
                "qtd_anterior": number(row["qtd_anterior"]),
                "faturamento_anterior": number(money(row["faturamento_anterior"])),
                "estoque": number(row["estoque"]),
                "dias_sem_venda": dias,
                "ultima_venda": row["ultima_venda"].isoformat() if row["ultima_venda"] else None,
                "giro": number(row["qtd"] / max(Decimal("1"), Decimal(str(dias or 1)))),
                "cobertura": number(row["estoque"] / row["qtd"] if row["qtd"] else ZERO),
            })
        return out

    def _margin_prev(self, itens_prev):
        fat = self._sum(itens_prev, "total_item")
        cmv = self._sum(itens_prev, "cmv_total")
        return float(((fat - cmv) / fat * Decimal("100")).quantize(Decimal("0.01"))) if fat else 0

    def _group_rows(self, produtos, key):
        rows = {}
        for produto in produtos:
            label = produto.get(key) or "-"
            rows.setdefault(label, {"nome": label, "qtd": ZERO, "faturamento": ZERO, "lucro_bruto": ZERO})
            rows[label]["qtd"] += Decimal(str(produto["qtd"]))
            rows[label]["faturamento"] += Decimal(str(produto["faturamento"]))
            rows[label]["lucro_bruto"] += Decimal(str(produto["lucro_bruto"]))
        total = sum((row["faturamento"] for row in rows.values()), ZERO)
        return [
            {
                "nome": row["nome"],
                "qtd": number(row["qtd"]),
                "faturamento": number(money(row["faturamento"])),
                "lucro_bruto": number(money(row["lucro_bruto"])),
                "percentual": number((row["faturamento"] / total * Decimal("100")).quantize(Decimal("0.01"))) if total else 0,
            }
            for row in sorted(rows.values(), key=lambda r: r["faturamento"], reverse=True)
        ]

    def _products_by_store(self, itens):
        rows = (
            itens.values("venda__loja__nome_loja")
            .annotate(total=Sum("total_item"), qtd=Sum("quantidade"))
            .order_by("-total")
        )
        return [{"loja": row["venda__loja__nome_loja"], "total": number(money(row["total"])), "qtd": number(row["qtd"])} for row in rows]

    def _products_store_matrix(self, itens):
        rows = {}
        lojas = []
        for item in itens:
            produto = item.descricao or "-"
            loja = item.venda.loja.nome_loja if item.venda and item.venda.loja else "-"
            if loja not in lojas:
                lojas.append(loja)
            row = rows.setdefault(produto, {"produto": produto, "total": ZERO, "lojas": {}})
            row["lojas"][loja] = row["lojas"].get(loja, ZERO) + Decimal(item.quantidade or 0)
            row["total"] += Decimal(item.quantidade or 0)
        return {"lojas": lojas, "produtos": [{"produto": r["produto"], "total": number(r["total"]), "lojas": {k: number(v) for k, v in r["lojas"].items()}} for r in rows.values()]}

    def _falling_products(self, produtos):
        rows = []
        for produto in produtos:
            atual = Decimal(str(produto["qtd"]))
            anterior = Decimal(str(produto["qtd_anterior"]))
            if anterior > atual:
                diff = atual - anterior
                rows.append({**produto, "diferenca": number(diff), "variacao": pct(atual, anterior)})
        return sorted(rows, key=lambda p: p["diferenca"])

    def _abc(self, produtos):
        total = sum((Decimal(str(p["faturamento"])) for p in produtos), ZERO)
        acumulado = ZERO
        rows = []
        for produto in sorted(produtos, key=lambda p: p["faturamento"], reverse=True):
            acumulado += Decimal(str(produto["faturamento"]))
            perc = (acumulado / total * Decimal("100")) if total else ZERO
            classe = "A" if perc <= 80 else "B" if perc <= 95 else "C"
            rows.append({**produto, "classe": classe, "acumulado": number(perc.quantize(Decimal("0.01"))) if total else 0})
        return rows

    def _insights(self, produtos, total):
        insights = []
        ranking = sorted(produtos, key=lambda p: p["faturamento"], reverse=True)
        if ranking:
            lider = ranking[0]
            insights.append({"tipo": "positivo", "titulo": "Produto líder", "descricao": f"{lider['produto']} lidera com {lider['participacao']}% do faturamento."})
        queda = self._falling_products(produtos)
        if queda:
            insights.append({"tipo": "atencao", "titulo": "Queda de vendas", "descricao": f"{queda[0]['produto']} caiu {abs(queda[0]['variacao'])}% contra o período anterior."})
        baixa_margem = [p for p in produtos if p["faturamento"] and p["margem_bruta"] < 20]
        if baixa_margem:
            insights.append({"tipo": "critico", "titulo": "Margem baixa", "descricao": f"{len(baixa_margem)} produto(s) abaixo de 20% de margem."})
        if not insights:
            insights.append({"tipo": "ok", "titulo": "Sem alertas críticos", "descricao": "Nenhuma queda ou margem crítica no período."})
        return insights

    def _product_filters(self, user, empresa, lojas):
        payload = self._filters_payload(user, empresa, lojas)
        payload.update({
            "grupos": [{"id": g.pk, "nome": g.Descricao} for g in Grupo.objects.filter(empresa=empresa).order_by("Descricao")],
            "subgrupos": [{"id": s.pk, "nome": s.Descricao, "grupo": s.Idgrupo_id} for s in Subgrupo.objects.filter(empresa=empresa).order_by("Descricao")],
            "colecoes": [{"id": c.pk, "nome": c.Descricao, "estacao": c.Estacao} for c in Colecao.objects.filter(empresa=empresa).order_by("Descricao")],
            "cores": [{"id": c.pk, "nome": c.Descricao} for c in Cor.objects.filter(empresa=empresa).order_by("Descricao")],
            "tamanhos": [{"id": t.pk, "nome": t.Tamanho} for t in Tamanho.objects.filter(empresa=empresa).order_by("Tamanho")],
        })
        payload["estacoes"] = sorted({c["estacao"] for c in payload["colecoes"] if c["estacao"]})
        return payload


class DashboardVendasView(DashboardProdutosView):
    required_modules = ["relatorios", "vendas"]
    def get(self, request):
        if not self._can_view(request.user) and not request.user.has_perm("dashboard.visualizar_vendas"):
            raise PermissionDenied("Usuário sem permissão para acessar este dashboard.")
        start, end = default_period()
        comp_start, comp_end = previous_period(start, end, request.query_params.get("comparacao"))
        empresa, lojas = self._scope(request)
        loja_ids = [loja.pk for loja in lojas]

        vendas = self._vendas_qs(empresa.pk, loja_ids, start, end, request)
        vendas_prev = self._vendas_qs(empresa.pk, loja_ids, comp_start, comp_end, request)
        devolucoes = self._devolucoes_qs(empresa.pk, loja_ids, start, end)
        devolucoes_prev = self._devolucoes_qs(empresa.pk, loja_ids, comp_start, comp_end)
        metric = self._sales_metrics(vendas, devolucoes)
        prev = self._sales_metrics(vendas_prev, devolucoes_prev)
        itens = self._items_qs(vendas, request)
        itens_prev = self._items_qs(vendas_prev, request)
        produtos = self._product_rows(itens, itens_prev, empresa.pk, loja_ids)

        payload = {
            "periodo": {"inicio": start.isoformat(), "fim": end.isoformat()},
            "comparacao": {"inicio": comp_start.isoformat(), "fim": comp_end.isoformat()},
            "empresa": {"id": empresa.pk, "nome": str(empresa)},
            "filtros": self._sales_filters(request.user, empresa, lojas),
            "indicadores": {
                "cards": [
                    self._card("faturamento", "Faturamento", metric["faturamento"], prev["faturamento"], "money"),
                    self._card("vendas", "Quantidade de vendas", metric["quantidade_vendas"], prev["quantidade_vendas"], "number"),
                    self._card("ticket", "Ticket médio", metric["ticket_medio"], prev["ticket_medio"], "money"),
                    self._card("itens", "Itens vendidos", metric["itens_vendidos"], prev["itens_vendidos"], "number"),
                    self._card("margem", "Margem bruta", metric["margem_bruta"], prev["margem_bruta"], "percent"),
                    self._card("desconto", "Desconto médio", self._discount_rate(metric), self._discount_rate(prev), "percent", True),
                ],
                "base": metric,
            },
            "graficos": {
                "diario": self._daily_sales(vendas, vendas_prev, start, end, comp_start),
                "categorias": self._group_rows(produtos, "categoria"),
                "pagamentos": self._payments(vendas),
                "lojas": self._sales_by_store(vendas),
                "horas": self._sales_by_hour(vendas),
                "canais": self._sales_by_channel(vendas),
                "semana": self._sales_by_weekday(vendas),
            },
            "tabelas": {
                "produtos": produtos[:10],
                "vendedores": self._sales_by_seller(vendas)[:10],
                "cancelamentos_devolucoes": self._returns_and_cancellations(empresa.pk, loja_ids, start, end, devolucoes, metric),
            },
            "alertas": self._alerts(empresa, loja_ids, vendas, start, end),
            "atualizado_em": timezone.now().isoformat(),
        }
        self._audit(request, "dashboard_vendas_acessado", True)
        return Response(payload)

    def _discount_rate(self, metric):
        faturamento = Decimal(str(metric.get("faturamento") or 0))
        descontos = Decimal(str(metric.get("descontos") or 0))
        return number((descontos / faturamento * Decimal("100")).quantize(Decimal("0.01"))) if faturamento else 0

    def _sales_filters(self, user, empresa, lojas):
        payload = self._product_filters(user, empresa, lojas)
        payload.update({
            "clientes": [{"id": c.pk, "nome": c.nome_cliente} for c in Cliente.objects.filter(empresa=empresa).order_by("nome_cliente")[:300]],
            "formas": [{"id": f, "nome": f.title()} for f in VendaPdvPagamento.objects.filter(venda__empresa=empresa).values_list("forma", flat=True).distinct().order_by("forma") if f],
            "status": [{"id": item[0], "nome": item[1]} for item in VendaPdv.Status.choices],
            "canais": [{"id": "loja_fisica", "nome": "Loja física"}],
        })
        return payload

    def _sales_by_hour(self, vendas):
        week = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
        hours = list(range(8, 23, 2))
        rows = {day: {str(hour): ZERO for hour in hours} for day in week}
        for venda in vendas:
            if not venda.data_venda:
                continue
            day = week[venda.data_venda.weekday()]
            hour = max([h for h in hours if h <= venda.data_venda.hour], default=8)
            rows[day][str(hour)] += Decimal(venda.total or 0)
        max_value = max([value for day in rows.values() for value in day.values()] or [ZERO])
        return {
            "horas": hours,
            "linhas": [{"dia": day, "valores": [{"hora": h, "valor": number(money(rows[day][str(h)])), "intensidade": number((rows[day][str(h)] / max_value * Decimal("100")).quantize(Decimal("0.01"))) if max_value else 0} for h in hours]} for day in week],
        }

    def _sales_by_channel(self, vendas):
        total = self._sum(vendas, "total")
        count = vendas.count()
        return [{"canal": "Loja física", "faturamento": number(money(total)), "vendas": count, "percentual": 100 if total else 0, "evolucao": 0}]

    def _sales_by_weekday(self, vendas):
        week = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
        rows = {day: {"dia": day, "total": ZERO, "vendas": 0} for day in week}
        for venda in vendas:
            if not venda.data_venda:
                continue
            row = rows[week[venda.data_venda.weekday()]]
            row["total"] += Decimal(venda.total or 0)
            row["vendas"] += 1
        return [{"dia": row["dia"], "total": number(money(row["total"])), "vendas": row["vendas"]} for row in rows.values()]

    def _returns_and_cancellations(self, empresa_id, loja_ids, start, end, devolucoes, metric):
        start_dt, end_dt = period_datetimes(start, end)
        canceladas = VendaPdv.objects.filter(empresa_id=empresa_id, loja_id__in=loja_ids, data_venda__gte=start_dt, data_venda__lt=end_dt, status=VendaPdv.Status.CANCELADA)
        cancel_valor = self._sum(canceladas, "total")
        devol_valor = Decimal(str(metric.get("devolucoes") or 0))
        faturamento = Decimal(str(metric.get("faturamento") or 0))
        devol_ids = list(devolucoes.values_list("pk", flat=True))
        devol_qtd = VendaDevolucaoItem.objects.filter(devolucao_id__in=devol_ids).aggregate(total=Sum("quantidade"))["total"] or ZERO
        return {
            "cancelamentos": {"quantidade": canceladas.count(), "valor": number(money(cancel_valor)), "percentual": number((cancel_valor / faturamento * Decimal("100")).quantize(Decimal("0.01"))) if faturamento else 0},
            "devolucoes": {"quantidade": devolucoes.count(), "itens": number(devol_qtd), "valor": number(money(devol_valor)), "percentual": number((devol_valor / faturamento * Decimal("100")).quantize(Decimal("0.01"))) if faturamento else 0},
        }


class DashboardEstoqueView(DashboardProdutosView):
    required_modules = ["relatorios", "estoque"]
    def get(self, request):
        if not self._can_view(request.user) and not request.user.has_perm("dashboard.visualizar_estoque"):
            raise PermissionDenied("Usuário sem permissão para acessar este dashboard.")

        start_default, end_default = default_period()
        start = parse_date(request.query_params.get("inicio") or request.query_params.get("de"), start_default)
        end = parse_date(request.query_params.get("fim") or request.query_params.get("ate"), end_default)
        if end < start:
            start, end = end, start
        comp_start, comp_end = previous_period(start, end, request.query_params.get("comparacao", "periodo_anterior"))

        empresa, lojas = self._scope(request)
        loja_ids = [loja.pk for loja in lojas]
        estoque = self._stock_rows(request, empresa.pk, loja_ids)
        vendas = self._vendas_qs(empresa.pk, loja_ids, start, end, request)
        vendas_prev = self._vendas_qs(empresa.pk, loja_ids, comp_start, comp_end, request)
        itens = self._items_qs(vendas, request)
        itens_prev = self._items_qs(vendas_prev, request)
        total_valor = sum((Decimal(str(row["valor"])) for row in estoque), ZERO)
        total_qtd = sum((Decimal(str(row["saldo"])) for row in estoque), ZERO)
        ativos = Produto.objects.filter(empresa_id=empresa.pk, ativo=True).count()
        prev_valor = self._stock_value_at(empresa.pk, loja_ids, comp_end)
        sem_venda = self._sem_venda(estoque, end)
        giro = self._giro_medio(estoque, itens)
        cobertura = self._cobertura_media(estoque, itens)

        payload = {
            "periodo": {"inicio": start.isoformat(), "fim": end.isoformat()},
            "comparacao": {"inicio": comp_start.isoformat(), "fim": comp_end.isoformat()},
            "empresa": {"id": empresa.pk, "nome": str(empresa)},
            "filtros": self._product_filters(request.user, empresa, lojas),
            "indicadores": {
                "cards": [
                    self._card("valor", "Valor total do estoque", total_valor, prev_valor, "money"),
                    self._card("quantidade", "Quantidade de itens", total_qtd, self._stock_qty_at(empresa.pk, loja_ids, comp_end), "number"),
                    self._card("ativos", "Produtos ativos", ativos, ativos, "number"),
                    self._card("giro", "Giro médio", giro, self._giro_medio(estoque, itens_prev), "number"),
                    self._card("cobertura", "Cobertura (dias)", cobertura, cobertura, "number"),
                    self._card("sem_venda", "Produtos sem venda", len(sem_venda), 0, "number", True),
                ],
                "base": {
                    "valor_estoque": number(money(total_valor)),
                    "quantidade": number(total_qtd),
                    "produtos_ativos": ativos,
                    "giro": number(giro),
                    "cobertura": number(cobertura),
                    "sem_venda": len(sem_venda),
                },
            },
            "graficos": {
                "evolucao": self._stock_evolution(empresa.pk, loja_ids, start, end),
                "categorias": self._stock_group_rows(estoque, "categoria"),
                "distribuicao": self._distribution_rows(estoque),
                "lojas": self._stock_group_rows(estoque, "loja"),
                "giro_mensal": self._monthly_turnover(empresa.pk, loja_ids, end),
            },
            "tabelas": {
                "abc": self._stock_abc(estoque),
                "maior_cobertura": sorted(estoque, key=lambda row: row["cobertura"], reverse=True)[:8],
                "sem_venda": sem_venda[:8],
                "maior_giro": sorted(estoque, key=lambda row: row["giro"], reverse=True)[:8],
                "baixo_giro": sorted([row for row in estoque if row["saldo"] > 0], key=lambda row: row["giro"])[:8],
                "rupturas": [row for row in estoque if row["saldo"] <= 0][:8],
                "excessos": sorted(estoque, key=lambda row: row["valor"], reverse=True)[:8],
                "cor_tamanho": self._stock_group_rows(estoque, "cor_tamanho"),
                "colecao_estacao": self._stock_group_rows(estoque, "colecao_estacao"),
                "movimentacoes": self._movement_rows(empresa.pk, loja_ids, start, end),
            },
            "alertas": self._stock_alerts(estoque, sem_venda),
            "insights": self._stock_insights(estoque),
            "atualizado_em": timezone.now().isoformat(),
        }
        self._audit(request, "dashboard_estoque_acessado", True)
        return Response(payload)

    def _stock_rows(self, request, empresa_id, loja_ids):
        qs = Estoque.objects.filter(Idloja_id__in=loja_ids)
        termo = (request.query_params.get("q") or "").strip()
        eans = list(qs.values_list("CodigodeBarra", flat=True).distinct())
        skus = {
            sku.ean13: sku
            for sku in ProdutoDetalhe.objects.select_related("produto", "produto__grupo", "produto__subgrupo", "produto__colecao", "idcor", "idtamanho")
            .filter(produto__empresa_id=empresa_id, ean13__in=eans)
        }
        grupo = request.query_params.get("grupo")
        colecao = request.query_params.get("colecao")
        estacao = request.query_params.get("estacao")
        status = request.query_params.get("status")
        rows = []
        vendas = self._last_sales(empresa_id, loja_ids, eans)
        for item in qs.select_related("Idloja"):
            sku = skus.get(item.CodigodeBarra)
            produto = sku.produto if sku else None
            if grupo and produto and str(produto.grupo_id or "") != str(grupo):
                continue
            if colecao and produto and str(produto.colecao_id or "") != str(colecao):
                continue
            if estacao and produto and getattr(produto.colecao, "Estacao", None) != estacao:
                continue
            saldo = Decimal(item.Estoque or 0)
            if status == "zerado" and saldo != 0:
                continue
            if status == "positivo" and saldo <= 0:
                continue
            if status == "negativo" and saldo >= 0:
                continue
            payload = self._stock_row_payload(item, sku, produto, vendas.get(item.CodigodeBarra, {}))
            if termo:
                termo_lower = termo.lower()
                campos = [
                    payload["produto"], payload["referencia"], payload["ean"], payload["categoria"],
                    payload["subcategoria"], payload["colecao"], payload["estacao"], payload["cor"],
                    payload["tamanho"], payload["loja"],
                ]
                if not any(termo_lower in str(campo or "").lower() for campo in campos):
                    continue
            rows.append(payload)
        return rows

    def _stock_row_payload(self, item, sku, produto, venda):
        saldo = Decimal(item.Estoque or 0)
        reserva = Decimal(getattr(item, "reserva", 0) or 0)
        custo = Decimal(getattr(sku, "custo_medio", 0) or getattr(produto, "custo_medio", 0) or getattr(sku, "custo_ultima_compra", 0) or getattr(produto, "custo_ultima_compra", 0) or 0)
        qtd_vendida = Decimal(venda.get("qtd") or 0)
        giro = qtd_vendida
        cobertura = (saldo / (qtd_vendida / Decimal("30"))).quantize(Decimal("0.01")) if qtd_vendida else ZERO
        ultima_venda = venda.get("ultima_venda")
        hoje = timezone.localdate()
        dias_sem_venda = (hoje - ultima_venda).days if ultima_venda else None
        categoria = produto.grupo.Descricao if produto and produto.grupo else "-"
        subcategoria = produto.subgrupo.Descricao if produto and produto.subgrupo else "-"
        colecao = produto.colecao.Descricao if produto and produto.colecao else "-"
        estacao = produto.colecao.Estacao if produto and produto.colecao else "-"
        cor = sku.idcor.Descricao if sku and sku.idcor else "-"
        tamanho = sku.idtamanho.Tamanho if sku and sku.idtamanho else "-"
        return {
            "produto": produto.descricao if produto else item.referencia,
            "referencia": produto.referencia if produto else item.referencia,
            "ean": item.CodigodeBarra,
            "categoria": categoria,
            "subcategoria": subcategoria,
            "colecao": colecao,
            "estacao": estacao,
            "cor": cor,
            "tamanho": tamanho,
            "cor_tamanho": f"{cor} / {tamanho}",
            "colecao_estacao": f"{colecao} / {estacao}" if estacao else colecao,
            "loja": item.Idloja.nome_loja if item.Idloja else "-",
            "saldo": number(saldo),
            "reservado": number(reserva),
            "disponivel": number(saldo - reserva),
            "custo": number(money(custo)),
            "valor": number(money(saldo * custo)),
            "giro": number(giro),
            "cobertura": number(cobertura),
            "ultima_venda": ultima_venda.isoformat() if ultima_venda else None,
            "dias_sem_venda": dias_sem_venda,
            "ativo": bool(getattr(produto, "ativo", True)),
            "bloqueado": bool(getattr(produto, "bloqueado_venda", False) or getattr(sku, "bloqueado_venda", False)),
        }

    def _last_sales(self, empresa_id, loja_ids, eans):
        rows = {}
        itens = VendaPdvItem.objects.select_related("venda").filter(
            venda__empresa_id=empresa_id,
            venda__loja_id__in=loja_ids,
            venda__status=VendaPdv.Status.FINALIZADA,
            ean__in=eans,
        )
        for item in itens:
            row = rows.setdefault(item.ean, {"qtd": ZERO, "ultima_venda": None})
            row["qtd"] += Decimal(item.quantidade or 0)
            venda_data = item.venda.data_venda.date() if item.venda and item.venda.data_venda else None
            if venda_data and (not row["ultima_venda"] or venda_data > row["ultima_venda"]):
                row["ultima_venda"] = venda_data
        return rows

    def _stock_value_at(self, empresa_id, loja_ids, end):
        total = sum((Decimal(str(row["valor"])) for row in self._stock_rows(type("Req", (), {"query_params": {}})(), empresa_id, loja_ids)), ZERO)
        _, end_dt = period_datetimes(end + timedelta(days=1), end + timedelta(days=1))
        for mov in EstoqueMovimentacao.objects.filter(Idloja_id__in=loja_ids, data_movimento__gte=end_dt):
            value = Decimal(mov.quantidade or 0) * Decimal(mov.custo_unitario or 0)
            total -= value if mov.tipo == "ENTRADA" else -value
        return money(total)

    def _stock_qty_at(self, empresa_id, loja_ids, end):
        total = sum((Decimal(str(row["saldo"])) for row in self._stock_rows(type("Req", (), {"query_params": {}})(), empresa_id, loja_ids)), ZERO)
        _, end_dt = period_datetimes(end + timedelta(days=1), end + timedelta(days=1))
        for mov in EstoqueMovimentacao.objects.filter(Idloja_id__in=loja_ids, data_movimento__gte=end_dt):
            qtd = Decimal(mov.quantidade or 0)
            total -= qtd if mov.tipo == "ENTRADA" else -qtd
        return total

    def _giro_medio(self, estoque, itens):
        vendido = itens.aggregate(total=Sum("quantidade"))["total"] or ZERO
        saldo = sum((Decimal(str(row["saldo"])) for row in estoque), ZERO)
        return (Decimal(vendido) / saldo).quantize(Decimal("0.01")) if saldo else ZERO

    def _cobertura_media(self, estoque, itens):
        vendido = Decimal(itens.aggregate(total=Sum("quantidade"))["total"] or 0)
        saldo = sum((Decimal(str(row["saldo"])) for row in estoque), ZERO)
        media_dia = vendido / Decimal("30") if vendido else ZERO
        return (saldo / media_dia).quantize(Decimal("0.01")) if media_dia else ZERO

    def _sem_venda(self, estoque, end):
        return [row for row in estoque if not row["ultima_venda"] or (end - date.fromisoformat(row["ultima_venda"])).days > 90]

    def _stock_group_rows(self, estoque, key):
        rows = {}
        for item in estoque:
            label = item.get(key) or "-"
            row = rows.setdefault(label, {"nome": label, "qtd": ZERO, "valor": ZERO, "itens": 0})
            row["qtd"] += Decimal(str(item["saldo"]))
            row["valor"] += Decimal(str(item["valor"]))
            row["itens"] += 1
        total = sum((row["valor"] for row in rows.values()), ZERO)
        return [
            {"nome": row["nome"], "qtd": number(row["qtd"]), "valor": number(money(row["valor"])), "itens": row["itens"], "percentual": number((row["valor"] / total * Decimal("100")).quantize(Decimal("0.01"))) if total else 0}
            for row in sorted(rows.values(), key=lambda r: r["valor"], reverse=True)
        ]

    def _distribution_rows(self, estoque):
        total = sum((Decimal(str(row["valor"])) for row in estoque), ZERO)
        buckets = {
            "Estoque disponível": sum((Decimal(str(row["disponivel"])) * Decimal(str(row["custo"])) for row in estoque), ZERO),
            "Estoque reservado": sum((Decimal(str(row["reservado"])) * Decimal(str(row["custo"])) for row in estoque), ZERO),
            "Estoque zerado": sum((Decimal(str(row["valor"])) for row in estoque if Decimal(str(row["saldo"])) == 0), ZERO),
            "Estoque bloqueado": sum((Decimal(str(row["valor"])) for row in estoque if row["bloqueado"]), ZERO),
        }
        return [{"nome": k, "valor": number(money(v)), "percentual": number((v / total * Decimal("100")).quantize(Decimal("0.01"))) if total else 0} for k, v in buckets.items()]

    def _stock_abc(self, estoque):
        total = sum((Decimal(str(row["valor"])) for row in estoque), ZERO)
        classes = {"A": {"itens": 0, "valor": ZERO}, "B": {"itens": 0, "valor": ZERO}, "C": {"itens": 0, "valor": ZERO}}
        acumulado = ZERO
        for row in sorted(estoque, key=lambda r: r["valor"], reverse=True):
            valor = Decimal(str(row["valor"]))
            acumulado += valor
            perc = acumulado / total * Decimal("100") if total else ZERO
            classe = "A" if perc <= 80 else "B" if perc <= 95 else "C"
            classes[classe]["itens"] += 1
            classes[classe]["valor"] += valor
        return [{"classe": k, "itens": v["itens"], "valor": number(money(v["valor"])), "percentual": number((v["valor"] / total * Decimal("100")).quantize(Decimal("0.01"))) if total else 0} for k, v in classes.items()]

    def _stock_evolution(self, empresa_id, loja_ids, start, end):
        points = []
        for point in date_points(start, end):
            valor = self._stock_value_at(empresa_id, loja_ids, point)
            points.append({"data": point.isoformat(), "valor": number(valor), "media": number(valor)})
        return points

    def _monthly_turnover(self, empresa_id, loja_ids, end):
        rows = []
        for i in range(11, -1, -1):
            month_end = (end.replace(day=1) - timedelta(days=1)).replace(day=1) if i else end.replace(day=1)
            base = end.replace(day=1)
            start = (base - timedelta(days=31 * i)).replace(day=1)
            next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            finish = min(next_month - timedelta(days=1), end)
            vendas = self._vendas_qs(empresa_id, loja_ids, start, finish, type("Req", (), {"query_params": {}})())
            itens = self._items_qs(vendas, type("Req", (), {"query_params": {}})())
            estoque = self._stock_rows(type("Req", (), {"query_params": {}})(), empresa_id, loja_ids)
            rows.append({"mes": start.strftime("%m/%Y"), "giro": number(self._giro_medio(estoque, itens))})
        return rows

    def _movement_rows(self, empresa_id, loja_ids, start, end):
        start_dt, end_dt = period_datetimes(start, end)
        rows = (
            EstoqueMovimentacao.objects.filter(Idloja_id__in=loja_ids, data_movimento__gte=start_dt, data_movimento__lt=end_dt)
            .values("tipo")
            .annotate(qtd=Sum("quantidade"), valor=Sum("custo_total"), movimentos=Count("Idmovimento"))
            .order_by("tipo")
        )
        return [{"tipo": row["tipo"], "qtd": number(row["qtd"]), "valor": number(money(row["valor"])), "movimentos": row["movimentos"]} for row in rows]

    def _stock_alerts(self, estoque, sem_venda):
        zerado = len([row for row in estoque if row["saldo"] <= 0])
        baixo = len([row for row in estoque if 0 < row["saldo"] <= 3])
        bloqueado = len([row for row in estoque if row["bloqueado"]])
        return [
            {"tipo": "critico", "titulo": "Produtos sem venda > 90 dias", "descricao": f"{len(sem_venda)} produto(s) precisam de atenção."},
            {"tipo": "atencao", "titulo": "Estoque baixo", "descricao": f"{baixo} SKU(s) com saldo igual ou abaixo de 3."},
            {"tipo": "critico", "titulo": "Estoque zerado", "descricao": f"{zerado} SKU(s) sem saldo."},
            {"tipo": "info", "titulo": "Produtos bloqueados", "descricao": f"{bloqueado} SKU(s) bloqueados para venda."},
        ]

    def _stock_insights(self, estoque):
        if not estoque:
            return [{"tipo": "info", "titulo": "Sem estoque", "descricao": "Nenhum saldo encontrado para os filtros."}]
        maior = max(estoque, key=lambda row: row["valor"])
        giro = max(estoque, key=lambda row: row["giro"])
        return [
            {"tipo": "positivo", "titulo": "Maior valor em estoque", "descricao": f"{maior['produto']} concentra {money(maior['valor'])} em estoque."},
            {"tipo": "info", "titulo": "Maior giro", "descricao": f"{giro['produto']} tem o maior giro no período."},
        ]


class DashboardFinanceiroView(DashboardExecutivoView):
    required_modules = ["relatorios", "financeiro"]
    def get(self, request):
        if not self._can_view(request.user):
            self._audit(request, "dashboard_financeiro_negado", False)
            raise PermissionDenied("Usuário sem permissão para consultar o dashboard financeiro.")

        empresa, lojas = self._scope(request)
        loja_ids = [loja.pk for loja in lojas]
        start_default, end_default = default_period()
        start = parse_date(request.query_params.get("inicio"), start_default)
        end = parse_date(request.query_params.get("fim"), end_default)
        prev_start, prev_end = previous_period(start, end, request.query_params.get("comparacao", "periodo_anterior"))

        movimentos = self._movs_qs(request, empresa.pk, loja_ids, start, end)
        movimentos_prev = self._movs_qs(request, empresa.pk, loja_ids, prev_start, prev_end)
        entradas = self._sum_movs(movimentos, MovimentacaoFinanceira.TIPO_ENTRADA)
        saidas = self._sum_movs(movimentos, MovimentacaoFinanceira.TIPO_SAIDA)
        entradas_prev = self._sum_movs(movimentos_prev, MovimentacaoFinanceira.TIPO_ENTRADA)
        saidas_prev = self._sum_movs(movimentos_prev, MovimentacaoFinanceira.TIPO_SAIDA)

        saldo = self._saldo_disponivel(empresa.pk, loja_ids)
        saldo_prev = self._saldo_atual_por_data(empresa.pk, loja_ids, prev_end)
        pagar = self._pagar_aberto(empresa.pk, loja_ids)
        receber = self._receber_aberto(empresa.pk, loja_ids)
        pagar_prev = self._pagar_aberto(empresa.pk, loja_ids, prev_end)
        receber_prev = self._receber_aberto(empresa.pk, loja_ids, prev_end)

        payload = {
            "periodo": {"inicio": start.isoformat(), "fim": end.isoformat()},
            "comparacao": {"inicio": prev_start.isoformat(), "fim": prev_end.isoformat()},
            "empresa": {"id": empresa.pk, "nome": empresa.nome},
            "filtros": self._finance_filters(empresa, lojas),
            "indicadores": {
                "cards": [
                    self._card("saldo", "Saldo disponível", saldo, saldo_prev, "money"),
                    self._card("entradas", "Entradas", entradas, entradas_prev, "money"),
                    self._card("saidas", "Saídas", saidas, saidas_prev, "money", True),
                    self._card("resultado", "Resultado do período", entradas - saidas, entradas_prev - saidas_prev, "money"),
                    self._card("pagar", "Contas a pagar", pagar["total"], pagar_prev["total"], "money", True),
                    self._card("receber", "Contas a receber", receber["total"], receber_prev["total"], "money"),
                ],
                "base": {
                    "entradas": number(entradas),
                    "saidas": number(saidas),
                    "resultado": number(entradas - saidas),
                    "saldo": number(saldo),
                    "pagar": number(pagar["total"]),
                    "receber": number(receber["total"]),
                    "pagar_vencido": number(pagar["vencido"]),
                    "receber_vencido": number(receber["vencido"]),
                },
            },
            "graficos": {
                "fluxo_caixa": self._fluxo_caixa(movimentos, start, end, saldo - entradas + saidas),
                "saldo_contas": self._saldo_contas(empresa.pk, loja_ids),
                "recebiveis": self._aging_rows(self._receber_qs(empresa.pk, loja_ids), "receber"),
                "evolucao_saldo": self._evolucao_saldo(empresa.pk, loja_ids, end),
            },
            "tabelas": {
                "pagar_resumo": self._aging_rows(self._pagar_qs(empresa.pk, loja_ids), "pagar"),
                "receber_resumo": self._aging_rows(self._receber_qs(empresa.pk, loja_ids), "receber"),
                "maiores_pagamentos": self._maiores_pagamentos(empresa.pk, loja_ids, start, end),
                "maiores_recebimentos": self._maiores_recebimentos(empresa.pk, loja_ids, start, end),
                "indicadores": self._indicadores_financeiros(saldo, receber["total"], pagar["total"], entradas - saidas, entradas),
            },
            "alertas": self._alertas_financeiros(pagar, receber, entradas - saidas),
            "atualizado_em": timezone.localtime().isoformat(),
        }
        self._audit(request, "dashboard_financeiro", True)
        return Response(payload)

    def _movs_qs(self, request, empresa_id, loja_ids, start, end):
        qs = MovimentacaoFinanceira.objects.filter(
            empresa_id=empresa_id,
            idloja_id__in=loja_ids,
            data_movimento__gte=start,
            data_movimento__lte=end,
        ).exclude(status=MovimentacaoFinanceira.STATUS_CANCELADA)
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(documento__icontains=q) | Q(historico__icontains=q) | Q(Idnatureza__descricao__icontains=q))
        status = request.query_params.get("status")
        if status:
            qs = qs.filter(status=status)
        conta = request.query_params.get("conta")
        if conta:
            tipo, _, pk = conta.partition(":")
            if tipo == "caixa":
                qs = qs.filter(caixa_id=pk)
            elif tipo == "banco":
                qs = qs.filter(conta_bancaria_id=pk)
        natureza = request.query_params.get("natureza")
        if natureza:
            qs = qs.filter(Idnatureza_id=natureza)
        return qs

    def _sum_movs(self, qs, tipo):
        return money(qs.filter(tipo=tipo).aggregate(total=Sum("valor"))["total"])

    def _pagar_qs(self, empresa_id, loja_ids, base_date=None):
        qs = PagarItem.objects.filter(Idpagar__empresa_id=empresa_id, Idpagar__idloja_id__in=loja_ids).exclude(status__in=["CANCELADO", "BAIXADO"])
        if base_date:
            qs = qs.filter(Data_vencimento__lte=base_date)
        return qs

    def _receber_qs(self, empresa_id, loja_ids, base_date=None):
        qs = ReceberItem.objects.filter(Idreceber__empresa_id=empresa_id, Idreceber__idloja_id__in=loja_ids).exclude(status__in=["CANCELADO", "BAIXADO", "ANTECIPADO"])
        if base_date:
            qs = qs.filter(Data_vencimento__lte=base_date)
        return qs

    def _pagar_aberto(self, empresa_id, loja_ids, base_date=None):
        qs = self._pagar_qs(empresa_id, loja_ids, base_date)
        today = timezone.localdate()
        return {"total": money(qs.aggregate(total=Sum("valor_parcela"))["total"]), "vencido": money(qs.filter(Data_vencimento__lt=today).aggregate(total=Sum("valor_parcela"))["total"]), "qtd": qs.count()}

    def _receber_aberto(self, empresa_id, loja_ids, base_date=None):
        qs = self._receber_qs(empresa_id, loja_ids, base_date)
        today = timezone.localdate()
        return {"total": money(qs.aggregate(total=Sum("valor_parcela"))["total"]), "vencido": money(qs.filter(Data_vencimento__lt=today).aggregate(total=Sum("valor_parcela"))["total"]), "qtd": qs.count()}

    def _saldo_disponivel(self, empresa_id, loja_ids):
        caixas = money(Caixa.objects.filter(empresa_id=empresa_id, idloja_id__in=loja_ids, ativo=True).aggregate(total=Sum("saldo_atual"))["total"])
        bancos = money(ContaBancaria.objects.filter(empresa_id=empresa_id, idloja_id__in=loja_ids, ativo=True).aggregate(total=Sum("saldo_atual"))["total"])
        return caixas + bancos

    def _saldo_atual_por_data(self, empresa_id, loja_ids, base_date):
        atual = self._saldo_disponivel(empresa_id, loja_ids)
        posteriores = MovimentacaoFinanceira.objects.filter(empresa_id=empresa_id, idloja_id__in=loja_ids, data_movimento__gt=base_date).exclude(status=MovimentacaoFinanceira.STATUS_CANCELADA)
        return atual - self._sum_movs(posteriores, MovimentacaoFinanceira.TIPO_ENTRADA) + self._sum_movs(posteriores, MovimentacaoFinanceira.TIPO_SAIDA)

    def _finance_filters(self, empresa, lojas):
        caixas = Caixa.objects.filter(empresa=empresa, idloja__in=lojas, ativo=True).order_by("codigo")
        bancos = ContaBancaria.objects.filter(empresa=empresa, idloja__in=lojas, ativo=True).order_by("descricao")
        naturezas = (
            MovimentacaoFinanceira.objects.filter(empresa=empresa, Idnatureza__isnull=False)
            .values("Idnatureza_id", "Idnatureza__codigo", "Idnatureza__descricao")
            .distinct()
            .order_by("Idnatureza__codigo")
        )
        return {
            "lojas": [{"id": loja.pk, "nome": loja.nome_loja, "tipo": loja.tipo_unidade} for loja in lojas],
            "contas": [{"id": f"caixa:{c.pk}", "nome": c.descricao, "tipo": "Caixa"} for c in caixas] + [{"id": f"banco:{b.pk}", "nome": b.descricao, "tipo": "Banco"} for b in bancos],
            "naturezas": [{"id": n["Idnatureza_id"], "codigo": n["Idnatureza__codigo"], "descricao": n["Idnatureza__descricao"]} for n in naturezas],
            "status": ["PREVISTA", "EFETIVA", "BAIXADO", "ANTECIPADA"],
        }

    def _fluxo_caixa(self, qs, start, end, saldo_inicial):
        entradas_by_day = {row["data_movimento"]: money(row["total"]) for row in qs.filter(tipo=MovimentacaoFinanceira.TIPO_ENTRADA).values("data_movimento").annotate(total=Sum("valor"))}
        saidas_by_day = {row["data_movimento"]: money(row["total"]) for row in qs.filter(tipo=MovimentacaoFinanceira.TIPO_SAIDA).values("data_movimento").annotate(total=Sum("valor"))}
        saldo = money(saldo_inicial)
        rows = []
        for day in date_points(start, end):
            entrada = entradas_by_day.get(day, ZERO)
            saida = saidas_by_day.get(day, ZERO)
            saldo += entrada - saida
            rows.append({"data": day.isoformat(), "entradas": number(entrada), "saidas": number(saida), "saldo": number(saldo)})
        return rows

    def _saldo_contas(self, empresa_id, loja_ids):
        rows = []
        for c in Caixa.objects.filter(empresa_id=empresa_id, idloja_id__in=loja_ids, ativo=True):
            rows.append({"nome": c.descricao, "tipo": "Caixa", "valor": money(c.saldo_atual)})
        for b in ContaBancaria.objects.filter(empresa_id=empresa_id, idloja_id__in=loja_ids, ativo=True):
            rows.append({"nome": b.descricao, "tipo": "Banco", "valor": money(b.saldo_atual)})
        total = sum((row["valor"] for row in rows), ZERO)
        return [{"nome": row["nome"], "tipo": row["tipo"], "valor": number(row["valor"]), "percentual": number((row["valor"] / total * Decimal("100")).quantize(Decimal("0.01"))) if total else 0} for row in rows]

    def _aging_rows(self, qs, kind):
        today = timezone.localdate()
        buckets = [
            ("Vencidas > 90 dias", lambda d: d < today - timedelta(days=90)),
            ("Vencidas 61 a 90 dias", lambda d: today - timedelta(days=90) <= d < today - timedelta(days=60)),
            ("Vencidas 31 a 60 dias", lambda d: today - timedelta(days=60) <= d < today - timedelta(days=30)),
            ("A vencer até 30 dias", lambda d: today <= d <= today + timedelta(days=30)),
            ("A vencer > 30 dias", lambda d: d > today + timedelta(days=30)),
        ]
        values = []
        for label, check in buckets:
            filtered = [item for item in qs if item.Data_vencimento and check(item.Data_vencimento)]
            total = sum((money(item.valor_parcela) for item in filtered), ZERO)
            values.append({"faixa": label, "quantidade": len(filtered), "valor": total})
        total_all = sum((row["valor"] for row in values), ZERO)
        return [{"faixa": row["faixa"], "quantidade": row["quantidade"], "valor": number(row["valor"]), "percentual": number((row["valor"] / total_all * Decimal("100")).quantize(Decimal("0.01"))) if total_all else 0} for row in values]

    def _maiores_pagamentos(self, empresa_id, loja_ids, start, end):
        qs = PagarItem.objects.select_related("Idpagar", "Idpagar__idfornecedor", "Idnatureza").filter(Idpagar__empresa_id=empresa_id, Idpagar__idloja_id__in=loja_ids, data_baixa__gte=start, data_baixa__lte=end).order_by("-valor_baixa")[:5]
        return [{"fornecedor": str(i.Idpagar.idfornecedor), "natureza": str(i.Idnatureza or i.Idpagar.Idnatureza or "-"), "data": i.data_baixa.isoformat() if i.data_baixa else "", "valor": number(money(i.valor_baixa or i.valor_parcela)), "status": i.status} for i in qs]

    def _maiores_recebimentos(self, empresa_id, loja_ids, start, end):
        qs = ReceberItem.objects.select_related("Idreceber", "Idreceber__idcliente").filter(Idreceber__empresa_id=empresa_id, Idreceber__idloja_id__in=loja_ids, data_baixa__gte=start, data_baixa__lte=end).order_by("-valor_baixa")[:5]
        return [{"cliente": str(i.Idreceber.idcliente), "data": i.data_baixa.isoformat() if i.data_baixa else "", "valor": number(money(i.valor_baixa or i.valor_parcela)), "forma": i.FormaPagamento or "-", "status": i.status} for i in qs]

    def _evolucao_saldo(self, empresa_id, loja_ids, end):
        rows = []
        saldo = self._saldo_disponivel(empresa_id, loja_ids)
        for i in range(11, -1, -1):
            start = (end.replace(day=1) - timedelta(days=31 * i)).replace(day=1)
            rows.append({"mes": start.strftime("%m/%Y"), "saldo": number(saldo), "media": number(saldo)})
        return rows

    def _indicadores_financeiros(self, saldo, receber, pagar, resultado, entradas):
        return [
            {"indicador": "Liquidez imediata", "valor": number((saldo / pagar).quantize(Decimal("0.01"))) if pagar else 0, "variacao": 0},
            {"indicador": "Liquidez corrente", "valor": number(((saldo + receber) / pagar).quantize(Decimal("0.01"))) if pagar else 0, "variacao": 0},
            {"indicador": "Índice de endividamento", "valor": number((pagar / (saldo + receber)).quantize(Decimal("0.01"))) if (saldo + receber) else 0, "variacao": 0},
            {"indicador": "Rentabilidade do período", "valor": number((resultado / entradas * Decimal("100")).quantize(Decimal("0.01"))) if entradas else 0, "variacao": 0},
        ]

    def _alertas_financeiros(self, pagar, receber, resultado):
        return [
            {"tipo": "critico", "titulo": "Contas a pagar vencidas", "descricao": f"Valor vencido: R$ {money(pagar['vencido'])}."},
            {"tipo": "atencao", "titulo": "Contas a receber vencidas", "descricao": f"Valor vencido: R$ {money(receber['vencido'])}."},
            {"tipo": "critico" if resultado < 0 else "positivo", "titulo": "Resultado do período", "descricao": f"Resultado financeiro: R$ {money(resultado)}."},
            {"tipo": "info", "titulo": "Títulos em aberto", "descricao": f"{pagar['qtd']} a pagar e {receber['qtd']} a receber."},
        ]
