import uuid
from decimal import Decimal

from django.http import FileResponse, Http404
from django.db import IntegrityError, models, transaction
from django.db.models import Prefetch
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.renderers import BaseRenderer
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.models import CredencialPdvUsuario
from cadastros.models import Cliente, Funcionarios, Loja
from financeiro.models import Caixa, CashbackConfig, CashbackMovimento, CondicaoAdquirente, FormaPagamento, FormaPagamentoCondicao, PrazoPagamento, PrazoPagamentoParcela, TipoDespesaPdv, ValeTroca
from financeiro.services import (
    DocumentoSequenciaErro,
    ValeTrocaErro,
    cancelar_reserva_vale_troca,
    cancelar_reservas_vale_troca_venda,
    consultar_reservas_vale_troca_venda,
    consultar_vale_troca_online,
    escopo_empresa,
    escopo_loja,
    listar_vales_troca_online_cliente,
    reservar_faixa_documento,
    reservar_vales_troca_venda,
)
from financeiro.models import SequenciaDocumento
from fiscal.models import FormaPagamentoFiscalMap, NFCe, VendaDevolucao, VendaPdv
from fiscal.views.venda_pdv import VendaDevolucaoViewSet, money
from fiscal.services.documentos import reservar_documento_devolucao
from hub.administracao import (
    atualizar_resultado_comando,
    listar_caixas_loja,
    obter_comando_administrativo_para_hub,
    serializar_comando,
    solicitar_configurar_terminal,
    solicitar_gerar_pareamento,
)
from hub.authentication import HubTokenAuthentication
from hub.catalogo import gerar_catalogo_hub
from hub.models import AtivacaoSysvarHub, HubComandoAdministrativo, HubDevolucaoFaixaNumeracao, HubDevolucaoMapeamento, HubSincronizacaoSolicitacao, HubVendaFaixaNumeracao, SysvarHub
from hub.operacional import normalizar_snapshot_operacional
from hub.sincronizacao import (
    atualizar_status_sincronizacao,
    hubs_no_escopo,
    lojas_no_escopo,
    montar_painel_sincronizacao,
    obter_comando_para_hub,
    serializar_solicitacao,
    solicitar_sincronizacao_hub,
    solicitar_sincronizacao_loja,
    solicitar_sincronizacao_todas,
    validar_usuario_sincronizacao,
)
from hub.sync import HubSyncProcessor
from produto.models import ProdutoImagem

ADMIN_CONFIG_ROLES = {"Admin", "Diretor"}
VENDA_HUB_TAMANHO_FAIXA_PADRAO = 100
DEVOLUCAO_HUB_TAMANHO_FAIXA_PADRAO = 100


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _texto(data, campo, default="", limite=None):
    valor = str(data.get(campo) or default).strip()
    if limite:
        return valor[:limite]
    return valor


def _decimal_string(valor, casas):
    if valor is None:
        return None
    return f"{valor:.{casas}f}"


def _validar_usuario_admin_hub(user):
    if user.is_superuser:
        return
    if getattr(user, "type", "") not in ADMIN_CONFIG_ROLES:
        raise PermissionDenied("Usuário sem permissão para administrar o Sysvar Hub.")
    if not getattr(user, "empresa_id", None):
        raise PermissionDenied("Usuário sem empresa vinculada.")


def _loja_no_escopo_usuario(user, loja_id):
    loja = Loja.objects.select_related("empresa").filter(pk=loja_id).first()
    if not loja:
        raise ValidationError({"loja": "Loja inválida."})
    user_empresa_id = getattr(user, "empresa_id", None)
    if user_empresa_id and loja.empresa_id != int(user_empresa_id):
        raise PermissionDenied("Loja fora do escopo do usuário.")
    if not user_empresa_id and not user.is_superuser:
        raise PermissionDenied("Usuário sem empresa vinculada.")
    return loja


def _hub_no_escopo_usuario(user, hub_id, for_update=False):
    qs = SysvarHub.objects.select_related("loja", "loja__empresa")
    if for_update:
        qs = qs.select_for_update()
    hub = qs.filter(pk=hub_id).first()
    if not hub:
        raise Http404
    user_empresa_id = getattr(user, "empresa_id", None)
    if user_empresa_id and hub.loja.empresa_id != int(user_empresa_id):
        raise PermissionDenied("Hub fora do escopo do usuário.")
    if not user_empresa_id and not user.is_superuser:
        raise PermissionDenied("Usuário sem empresa vinculada.")
    return hub


def _serializar_ativacao_admin(ativacao, agora=None):
    return {
        "id": ativacao.pk,
        "loja_id": ativacao.loja_id,
        "loja_nome": ativacao.loja.nome_loja,
        "empresa_id": ativacao.loja.empresa_id,
        "codigo_prefixo": ativacao.codigo_prefixo,
        "criada_em": ativacao.criado_em,
        "expira_em": ativacao.expira_em,
        "estado": ativacao.estado_administrativo(agora),
        "hub_id": ativacao.hub_id,
    }


def _revogar_ativacoes_pendentes(loja, agora=None):
    agora = agora or timezone.now()
    return AtivacaoSysvarHub.objects.filter(
        loja=loja,
        usado_em__isnull=True,
        revogado_em__isnull=True,
        expira_em__gt=agora,
    ).update(revogado_em=agora)


def _serializar_fiscal_loja(loja, empresa):
    nome_fantasia = empresa.nome_fantasia or loja.nome_loja or empresa.nome
    uf = loja.estado
    return {
        "emite_nfce": loja.emite_nfce,
        "ambiente_fiscal": loja.ambiente_fiscal,
        "regime_tributario": loja.regime_tributario,
        "inscricao_estadual": loja.inscricao_estadual,
        "serie_nfce": loja.serie_nfce,
        "proximo_numero_nfce": loja.proximo_numero_nfce,
        "razao_social": empresa.nome,
        "nome_fantasia": nome_fantasia,
        "cnpj": loja.cnpj,
        "logradouro": loja.logradouro,
        "endereco": loja.endereco,
        "numero": loja.numero,
        "complemento": loja.complemento,
        "bairro": loja.bairro,
        "cidade": loja.cidade,
        "estado": uf,
        "uf": uf,
        "cep": loja.cep,
        "codigo_municipio_ibge": loja.codigo_municipio_ibge,
    }


def _serializar_natureza_despesa_pdv(natureza):
    return {
        "id": natureza.pk,
        "codigo": natureza.codigo,
        "descricao": natureza.descricao,
        "categoria_principal": natureza.categoria_principal,
        "subcategoria": natureza.subcategoria,
        "tipo": natureza.tipo,
        "status": natureza.status,
        "tipo_natureza": natureza.tipo_natureza,
        "natureza_operacao": natureza.natureza_operacao,
        "categoria_gerencial": natureza.categoria_gerencial,
        "movimenta_financeiro": natureza.movimenta_financeiro,
        "entra_dre": natureza.entra_dre,
    }


def _hub_vendas_base(hub):
    return (
        VendaPdv.objects.select_related("loja", "cliente", "vendedor", "caixa")
        .prefetch_related("itens", "pagamentos", "devolucoes__itens", "cashback_creditos", "cashback_usos")
        .filter(empresa_id=hub.loja.empresa_id)
    )


def _serializar_venda_devolucao_online(venda):
    view = VendaDevolucaoViewSet()
    payload = view._venda_devolucao_payload(venda)
    payload["uuid"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"central-venda:{venda.pk}"))
    payload["loja_origem"] = {"id": venda.loja_id, "nome": venda.loja.nome_loja}
    payload["cliente"] = {
        "id": venda.cliente_id,
        "nome": venda.cliente.nome_cliente,
        "documento": venda.cliente.documento or venda.cliente.cpf or "",
    }
    payload["situacao"] = venda.status
    payload["quantidade_itens"] = sum(int(item.quantidade or 0) for item in venda.itens.all())
    for item in payload["itens"]:
        item["item_uuid"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"central-venda-item:{item['id']}"))
        quantidade = Decimal(item["quantidade"] or 0)
        desconto = Decimal(item["desconto"] or 0)
        preco = Decimal(item["preco_unitario"] or 0)
        disponivel = Decimal(item["quantidade_disponivel"] or 0)
        desconto_unitario = money(desconto / quantidade) if quantidade else Decimal("0.00")
        item["valor_liquido_disponivel"] = str(money((preco - desconto_unitario) * disponivel))
    return payload


def _buscar_venda_online(hub, termo=None, venda_id=None):
    qs = _hub_vendas_base(hub)
    if venda_id:
        return qs.filter(pk=venda_id).first()
    termo = str(termo or "").strip()
    if not termo:
        return None
    filtros = models.Q(documento__iexact=termo)
    try:
        filtros |= models.Q(pk=int(termo))
    except (TypeError, ValueError):
        pass
    nfce_ids = NFCe.objects.filter(
        models.Q(chave_acesso=termo) | models.Q(protocolo=termo) | models.Q(numero=int(termo) if termo.isdecimal() else -1),
        venda__empresa_id=hub.loja.empresa_id,
    ).values_list("venda_id", flat=True)
    filtros |= models.Q(pk__in=nfce_ids)
    return qs.filter(filtros).order_by("-data_venda", "-id").first()


class HubDevolucaoVendaView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        venda = _buscar_venda_online(request.sysvar_hub, termo=request.query_params.get("documento"))
        if not venda:
            return Response({"detail": "Venda/cupom não encontrado na Central."}, status=status.HTTP_404_NOT_FOUND)
        if venda.status != VendaPdv.Status.FINALIZADA:
            return Response({"detail": "Somente vendas finalizadas podem ser devolvidas."}, status=status.HTTP_409_CONFLICT)
        return Response({"venda": _serializar_venda_devolucao_online(venda)}, status=status.HTTP_200_OK)


class HubDevolucaoVendaDetalheView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, venda_id):
        venda = _buscar_venda_online(request.sysvar_hub, venda_id=venda_id)
        if not venda:
            return Response({"detail": "Venda não encontrada na Central."}, status=status.HTTP_404_NOT_FOUND)
        if venda.status != VendaPdv.Status.FINALIZADA:
            return Response({"detail": "Somente vendas finalizadas podem ser devolvidas."}, status=status.HTTP_409_CONFLICT)
        return Response({"venda": _serializar_venda_devolucao_online(venda)}, status=status.HTTP_200_OK)


class HubDevolucaoClientesView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        termo = str(request.query_params.get("q") or "").strip()
        documento = "".join(ch for ch in str(request.query_params.get("documento") or termo) if ch.isdigit())
        nome = str(request.query_params.get("nome") or ("" if documento else termo)).strip()
        qs = Cliente.objects.filter(empresa_id=request.sysvar_hub.loja.empresa_id).order_by("nome_cliente", "id")
        if documento:
            qs = qs.filter(models.Q(documento__icontains=documento) | models.Q(cpf__icontains=documento))
        elif nome:
            qs = qs.filter(nome_cliente__icontains=nome)
        else:
            return Response({"clientes": []}, status=status.HTTP_200_OK)
        return Response(
            {
                "clientes": [
                    {
                        "id": cliente.pk,
                        "nome": cliente.nome_cliente,
                        "documento": cliente.documento or cliente.cpf or "",
                    }
                    for cliente in qs[:20]
                ]
            },
            status=status.HTTP_200_OK,
        )


class HubDevolucaoClienteVendasView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, cliente_id):
        cliente = Cliente.objects.filter(pk=cliente_id, empresa_id=request.sysvar_hub.loja.empresa_id).first()
        if not cliente:
            return Response({"detail": "Cliente não encontrado na Central."}, status=status.HTTP_404_NOT_FOUND)
        vendas = _hub_vendas_base(request.sysvar_hub).filter(cliente=cliente, status=VendaPdv.Status.FINALIZADA).order_by("-data_venda", "-id")[:50]
        payload = []
        for venda in vendas:
            nfce = getattr(venda, "nfce", None)
            payload.append(
                {
                    "id": venda.pk,
                    "documento": venda.documento,
                    "data_venda": venda.data_venda,
                    "loja": {"id": venda.loja_id, "nome": venda.loja.nome_loja},
                    "total": str(money(venda.total)),
                    "quantidade_itens": sum(int(item.quantidade or 0) for item in venda.itens.all()),
                    "nfce": {"numero": nfce.numero, "chave_acesso": nfce.chave_acesso, "status": nfce.status} if nfce else None,
                }
            )
        return Response({"cliente": {"id": cliente.pk, "nome": cliente.nome_cliente, "documento": cliente.documento or cliente.cpf or ""}, "vendas": payload}, status=status.HTTP_200_OK)


def _vale_troca_error_response(exc):
    return Response({"detail": str(exc)}, status=getattr(exc, "status_code", status.HTTP_409_CONFLICT))


class HubValeTrocaConsultarView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            vale = consultar_vale_troca_online(request.sysvar_hub, request.query_params.get("documento"))
        except ValeTrocaErro as exc:
            return _vale_troca_error_response(exc)
        return Response({"vale_troca": vale}, status=status.HTTP_200_OK)


class HubValeTrocaDisponiveisView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            vales = listar_vales_troca_online_cliente(request.sysvar_hub, request.query_params.get("cliente_id"))
        except ValeTrocaErro as exc:
            return _vale_troca_error_response(exc)
        return Response({"vales_troca": vales}, status=status.HTTP_200_OK)


class HubValeTrocaReservarVendaView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            reservas = reservar_vales_troca_venda(
                request.sysvar_hub,
                request.data.get("venda_uuid"),
                request.data.get("cliente_id") or request.data.get("cliente_retaguarda_id"),
                request.data.get("pagamentos") or [],
            )
        except ValeTrocaErro as exc:
            return _vale_troca_error_response(exc)
        return Response({"reservas": reservas}, status=status.HTTP_200_OK)


class HubValeTrocaReservasVendaView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, venda_uuid):
        try:
            reservas = consultar_reservas_vale_troca_venda(request.sysvar_hub, venda_uuid)
        except ValeTrocaErro as exc:
            return _vale_troca_error_response(exc)
        return Response({"reservas": reservas}, status=status.HTTP_200_OK)


class HubValeTrocaCancelarReservaView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            reserva = cancelar_reserva_vale_troca(
                request.sysvar_hub,
                reserva_id=request.data.get("reserva_id"),
                venda_uuid=request.data.get("venda_uuid"),
                operacao_uuid=request.data.get("operacao_uuid"),
            )
        except ValeTrocaErro as exc:
            return _vale_troca_error_response(exc)
        return Response({"reserva": reserva}, status=status.HTTP_200_OK)


class HubValeTrocaCancelarVendaView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            reservas = cancelar_reservas_vale_troca_venda(request.sysvar_hub, request.data.get("venda_uuid"))
        except ValeTrocaErro as exc:
            return _vale_troca_error_response(exc)
        return Response({"reservas": reservas}, status=status.HTTP_200_OK)


class HubDevolucaoFinalizarOnlineView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        hub = request.sysvar_hub
        devolucao_uuid = request.data.get("devolucao_uuid") or request.data.get("idempotency_key")
        try:
            devolucao_uuid = uuid.UUID(str(devolucao_uuid))
        except (TypeError, ValueError, AttributeError):
            return Response({"detail": "Identificador idempotente da devolução inválido."}, status=status.HTTP_400_BAD_REQUEST)
        existente = HubDevolucaoMapeamento.objects.select_related("devolucao").filter(hub=hub, devolucao_uuid=devolucao_uuid).first()
        if existente:
            return Response(_serializar_devolucao_online_resultado(existente.devolucao, existente), status=status.HTTP_200_OK)

        venda = _buscar_venda_online(hub, venda_id=request.data.get("venda_id"), termo=request.data.get("documento_venda"))
        if not venda:
            return Response({"detail": "Venda finalizada não encontrada na Central."}, status=status.HTTP_404_NOT_FOUND)
        if venda.status != VendaPdv.Status.FINALIZADA:
            return Response({"detail": "Somente vendas finalizadas podem ser devolvidas."}, status=status.HTTP_409_CONFLICT)
        if venda.cliente.documento == Cliente.DOCUMENTO_CONSUMIDOR_FINAL:
            return Response({"detail": "Vale-Troca exige cliente identificada. Identifique uma cliente válida antes de finalizar."}, status=status.HTTP_409_CONFLICT)

        view = VendaDevolucaoViewSet()
        itens_por_id = {item.id: item for item in venda.itens.all()}
        devolvidos = view._quantidades_devolvidas(venda)
        selecionados = []
        total = Decimal("0.00")
        for row in request.data.get("itens") or []:
            venda_item_id = int(row.get("venda_item") or row.get("id") or 0)
            quantidade = int(row.get("quantidade") or 0)
            venda_item = itens_por_id.get(venda_item_id)
            if not venda_item or quantidade <= 0:
                return Response({"detail": "Item de devolução inválido."}, status=status.HTTP_400_BAD_REQUEST)
            disponivel = int(venda_item.quantidade or 0) - int(devolvidos.get(venda_item.id, 0))
            if quantidade > disponivel:
                return Response({"detail": f"Quantidade maior que o saldo para devolver em {venda_item.descricao}."}, status=status.HTTP_409_CONFLICT)
            desconto_unitario = money(Decimal(venda_item.desconto or 0) / Decimal(venda_item.quantidade or 1))
            total += money((Decimal(venda_item.preco_unitario or 0) - desconto_unitario) * Decimal(quantidade))
            selecionados.append((venda_item, quantidade, money(desconto_unitario * Decimal(quantidade))))
        if total <= 0:
            return Response({"detail": "Valor da devolução inválido."}, status=status.HTTP_400_BAD_REQUEST)

        devolucao = VendaDevolucao.objects.create(
            empresa=venda.empresa,
            venda=venda,
            loja=hub.loja,
            cliente=venda.cliente,
            documento=reservar_documento_devolucao(venda.empresa),
            motivo=str(request.data.get("motivo") or "")[:255],
            subtotal=money(total),
            credito_cliente=money(total),
            criado_por=None,
        )
        for venda_item, quantidade, desconto in selecionados:
            view._registrar_item_devolucao(devolucao, venda_item, quantidade, desconto)
        view._registrar_credito_cliente(devolucao)
        view._estornar_financeiro(devolucao)
        view._estornar_cmv(devolucao)
        view._registrar_nfe_devolucao(devolucao)
        vale = getattr(devolucao, "vale_troca", None)
        mapeamento = HubDevolucaoMapeamento.objects.create(
            hub=hub,
            devolucao_uuid=devolucao_uuid,
            devolucao=devolucao,
            venda_uuid=uuid.uuid5(uuid.NAMESPACE_URL, f"central-venda:{venda.pk}"),
            documento=devolucao.documento,
            vale_documento=vale.documento if vale else "",
        )
        return Response(_serializar_devolucao_online_resultado(devolucao, mapeamento), status=status.HTTP_201_CREATED)


def _serializar_devolucao_online_resultado(devolucao, mapeamento):
    vale = getattr(devolucao, "vale_troca", None)
    nfe = getattr(devolucao, "nfe_devolucao", None)
    return {
        "devolucao": {
            "id": devolucao.pk,
            "uuid": str(mapeamento.devolucao_uuid),
            "documento": devolucao.documento,
            "valor_total": str(money(devolucao.credito_cliente)),
            "status": devolucao.status,
            "venda_origem": _serializar_venda_devolucao_online(devolucao.venda),
            "loja_recebimento": {"id": devolucao.loja_id, "nome": devolucao.loja.nome_loja},
            "cliente": {"id": devolucao.cliente_id, "nome": devolucao.cliente.nome_cliente, "documento": devolucao.cliente.documento or devolucao.cliente.cpf or ""},
            "vale_troca": {
                "id": vale.pk,
                "documento": vale.documento,
                "valor_original": str(money(vale.valor_original)),
                "saldo": str(money(vale.saldo)),
                "status": vale.status,
                "validade": vale.validade,
            } if vale else None,
            "fiscal": {
                "status": nfe.status,
                "modelo": nfe.modelo,
                "serie": nfe.serie,
                "numero": nfe.numero,
                "chave_acesso": nfe.chave_acesso,
                "mensagem": nfe.retorno_mensagem,
            } if nfe else None,
            "itens": [
                {
                    "venda_item": item.venda_item_id,
                    "produto": item.produto_id,
                    "sku": item.sku_id,
                    "ean": item.ean,
                    "descricao": item.descricao,
                    "quantidade": item.quantidade,
                    "preco_unitario": str(item.preco_unitario),
                    "desconto": str(item.desconto),
                    "total_item": str(item.total_item),
                }
                for item in devolucao.itens.all()
            ],
        }
    }


class HubAtivacaoAdminView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        _validar_usuario_admin_hub(request.user)
        agora = timezone.now()
        ativacoes = (
            AtivacaoSysvarHub.objects.select_related("loja", "loja__empresa", "hub")
            .filter(loja__in=lojas_no_escopo(request.user))
            .order_by("-criado_em", "-id")
        )
        return Response([_serializar_ativacao_admin(ativacao, agora) for ativacao in ativacoes], status=status.HTTP_200_OK)

    def post(self, request):
        _validar_usuario_admin_hub(request.user)
        loja_id = request.data.get("loja") or request.data.get("loja_id")
        if not loja_id:
            raise ValidationError({"loja": "Informe a loja."})

        loja = _loja_no_escopo_usuario(request.user, loja_id)
        with transaction.atomic():
            _revogar_ativacoes_pendentes(loja)
            ativacao, codigo = AtivacaoSysvarHub.criar(loja=loja, criado_por=request.user)
        return Response(
            {
                "id": ativacao.pk,
                "codigo": codigo,
                "codigo_prefixo": ativacao.codigo_prefixo,
                "expira_em": ativacao.expira_em,
                "loja_id": loja.pk,
                "loja_nome": loja.nome_loja,
                "empresa_id": loja.empresa_id,
                "empresa_nome": loja.empresa.nome,
            },
            status=status.HTTP_201_CREATED,
        )


class HubAtivacaoRevogarView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, ativacao_id):
        _validar_usuario_admin_hub(request.user)
        ativacao = (
            AtivacaoSysvarHub.objects.select_for_update()
            .select_related("loja", "loja__empresa", "hub")
            .filter(pk=ativacao_id)
            .first()
        )
        if not ativacao:
            raise Http404
        _loja_no_escopo_usuario(request.user, ativacao.loja_id)
        if ativacao.usado_em is None and ativacao.revogado_em is None and ativacao.expira_em > timezone.now():
            ativacao.revogado_em = timezone.now()
            ativacao.save(update_fields=["revogado_em"])
        return Response(_serializar_ativacao_admin(ativacao), status=status.HTTP_200_OK)


class HubAdministracaoView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        _validar_usuario_admin_hub(request.user)
        linhas = montar_painel_sincronizacao(request.user)
        agora = timezone.now()
        pendentes = {}
        ativacoes = (
            AtivacaoSysvarHub.objects.select_related("loja")
            .filter(loja__in=lojas_no_escopo(request.user), usado_em__isnull=True, revogado_em__isnull=True, expira_em__gt=agora)
            .order_by("loja_id", "-criado_em", "-id")
        )
        for ativacao in ativacoes:
            pendentes.setdefault(ativacao.loja_id, ativacao)
        hubs = {hub.pk: hub for hub in hubs_no_escopo(request.user)}
        for linha in linhas:
            hub = hubs.get(linha["hub_id"])
            ativacao = pendentes.get(linha["loja_id"])
            linha["hub_nome"] = hub.nome if hub else ""
            linha["ativacao_pendente"] = _serializar_ativacao_admin(ativacao, agora) if ativacao else None
            linha["caixas"] = listar_caixas_loja(hub.loja if hub else Loja.objects.get(pk=linha["loja_id"]))
        return Response(linhas, status=status.HTTP_200_OK)


class HubAdministracaoAcaoView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    acao = None

    @transaction.atomic
    def post(self, request, hub_id):
        _validar_usuario_admin_hub(request.user)
        hub = _hub_no_escopo_usuario(request.user, hub_id, for_update=True)
        if self.acao == "desativar":
            hub.ativo = False
            hub.save(update_fields=["ativo", "atualizado_em"])
        elif self.acao == "reativar":
            if not hub.token_hash:
                raise ValidationError({"hub": "Hub sem credencial ativa. Gere uma nova ativação."})
            hub.ativo = True
            hub.save(update_fields=["ativo", "atualizado_em"])
        elif self.acao == "desvincular":
            hub.ativo = False
            hub.token_hash = None
            hub.token_prefixo = ""
            hub.save(update_fields=["ativo", "token_hash", "token_prefixo", "atualizado_em"])
            _revogar_ativacoes_pendentes(hub.loja)
        else:
            raise ValidationError({"acao": "Ação administrativa inválida."})
        return Response(
            {
                "hub_id": hub.pk,
                "loja_id": hub.loja_id,
                "empresa_id": hub.loja.empresa_id,
                "ativo": hub.ativo,
                "possui_credencial": bool(hub.token_hash),
            },
            status=status.HTTP_200_OK,
        )


class HubAdministracaoConfigurarTerminalView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, hub_id):
        _validar_usuario_admin_hub(request.user)
        hub = _hub_no_escopo_usuario(request.user, hub_id)
        comando, criada = solicitar_configurar_terminal(hub, request.user, request.data)
        return Response(serializar_comando(comando), status=status.HTTP_201_CREATED if criada else status.HTTP_200_OK)


class HubAdministracaoPareamentoView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, hub_id):
        _validar_usuario_admin_hub(request.user)
        hub = _hub_no_escopo_usuario(request.user, hub_id)
        comando, criada = solicitar_gerar_pareamento(hub, request.user, request.data)
        return Response(serializar_comando(comando), status=status.HTTP_201_CREATED if criada else status.HTTP_200_OK)


class HubAdministracaoComandoView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, comando_id):
        _validar_usuario_admin_hub(request.user)
        comando = HubComandoAdministrativo.objects.select_related("hub", "hub__loja", "hub__loja__empresa").filter(pk=comando_id).first()
        if not comando:
            raise Http404
        _hub_no_escopo_usuario(request.user, comando.hub_id)
        return Response(serializar_comando(comando), status=status.HTTP_200_OK)


class HubAtivarView(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "hub_ativacao"

    @transaction.atomic
    def post(self, request):
        codigo = AtivacaoSysvarHub.normalizar_codigo(request.data.get("codigo"))
        hub_uuid_raw = _texto(request.data, "hub_uuid")
        nome = _texto(request.data, "nome", "Sysvar Hub", 120) or "Sysvar Hub"
        hostname = _texto(request.data, "hostname", "", 120)
        versao = _texto(request.data, "versao", "", 40)

        if not codigo:
            raise ValidationError({"codigo": "Código inválido."})
        try:
            hub_uuid = uuid.UUID(hub_uuid_raw)
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValidationError({"hub_uuid": "UUID do Hub inválido."}) from exc

        codigo_hash = AtivacaoSysvarHub.hash_codigo(codigo)
        ativacao = (
            AtivacaoSysvarHub.objects.select_for_update()
            .select_related("loja", "loja__empresa")
            .filter(codigo_hash=codigo_hash)
            .first()
        )
        if not ativacao or not ativacao.esta_utilizavel():
            raise ValidationError({"codigo": "Código inválido."})

        conflito_uuid = SysvarHub.objects.select_for_update().filter(hub_uuid=hub_uuid).exclude(loja=ativacao.loja).first()
        if conflito_uuid:
            raise ValidationError({"hub_uuid": "UUID do Hub já está vinculado a outra loja."})

        hub = SysvarHub.objects.select_for_update().filter(loja=ativacao.loja).first()
        agora = timezone.now()
        if not hub:
            try:
                hub = SysvarHub.objects.create(
                    loja=ativacao.loja,
                    hub_uuid=hub_uuid,
                    nome=nome,
                    hostname=hostname,
                    versao=versao,
                    ultimo_ip=_client_ip(request),
                    ultimo_contato=agora,
                    ativo=True,
                )
            except IntegrityError as exc:
                raise ValidationError({"hub_uuid": "UUID do Hub já está vinculado a outra loja."}) from exc
        else:
            hub.hub_uuid = hub_uuid
            hub.nome = nome
            hub.hostname = hostname
            hub.versao = versao
            hub.ultimo_ip = _client_ip(request)
            hub.ultimo_contato = agora
            hub.ativo = True
            hub.save(update_fields=["hub_uuid", "nome", "hostname", "versao", "ultimo_ip", "ultimo_contato", "ativo", "atualizado_em"])

        token = hub.gerar_token()
        ativacao.usado_em = agora
        ativacao.hub = hub
        ativacao.save(update_fields=["usado_em", "hub"])
        solicitar_sincronizacao_hub(hub)

        loja = ativacao.loja
        return Response(
            {
                "token": token,
                "hub_uuid": str(hub.hub_uuid),
                "hub_id": hub.pk,
                "loja_id": loja.pk,
                "loja_nome": loja.nome_loja,
                "empresa_id": loja.empresa_id,
                "empresa_nome": loja.empresa.nome,
            },
            status=status.HTTP_200_OK,
        )


class HubHeartbeatView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        hub = request.sysvar_hub
        hub.ultimo_contato = timezone.now()
        hub.hostname = _texto(request.data, "hostname", "", 120)
        hub.versao = _texto(request.data, "versao", "", 40)
        hub.ultimo_ip = _client_ip(request)
        update_fields = ["ultimo_contato", "hostname", "versao", "ultimo_ip", "atualizado_em"]
        if "snapshot_operacional" in request.data:
            hub.snapshot_operacional = normalizar_snapshot_operacional(request.data.get("snapshot_operacional"))
            hub.snapshot_operacional_em = hub.ultimo_contato
            update_fields.extend(["snapshot_operacional", "snapshot_operacional_em"])
        hub.save(update_fields=update_fields)
        return Response(
            {
                "status": "ok",
                "hub_uuid": str(hub.hub_uuid),
                "loja_id": hub.loja_id,
                "empresa_id": hub.loja.empresa_id,
                "servidor_em": timezone.now(),
                "comando_sincronizacao": obter_comando_para_hub(hub),
                "comando_administrativo": obter_comando_administrativo_para_hub(hub),
            },
            status=status.HTTP_200_OK,
        )


class HubComandoAdministrativoResultadoView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, comando_id):
        comando = atualizar_resultado_comando(request.sysvar_hub, comando_id, request.data)
        if not comando:
            raise Http404
        return Response(serializar_comando(comando), status=status.HTTP_200_OK)


class HubSincronizacaoPainelView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        validar_usuario_sincronizacao(request.user)
        return Response(montar_painel_sincronizacao(request.user), status=status.HTTP_200_OK)


class HubSincronizacaoSolicitarView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        validar_usuario_sincronizacao(request.user)
        loja_id = request.data.get("loja_id")
        if not loja_id:
            raise ValidationError({"loja_id": "Informe a loja."})
        loja = Loja.objects.filter(pk=loja_id).first()
        if not loja:
            raise ValidationError({"loja_id": "Loja inválida."})
        user_empresa_id = getattr(request.user, "empresa_id", None)
        if user_empresa_id and loja.empresa_id != int(user_empresa_id):
            raise PermissionDenied("Loja fora do escopo do usuário.")
        if not user_empresa_id and not request.user.is_superuser:
            raise PermissionDenied("Usuário sem empresa vinculada.")
        solicitacao, criada = solicitar_sincronizacao_loja(loja, usuario=request.user)
        if not solicitacao:
            raise ValidationError({"loja_id": "Loja sem Hub ativo."})
        return Response(serializar_solicitacao(solicitacao), status=status.HTTP_201_CREATED if criada else status.HTTP_200_OK)


class HubSincronizacaoTodasView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        validar_usuario_sincronizacao(request.user)
        resultado = solicitar_sincronizacao_todas(request.user)
        return Response(
            {
                "criadas": resultado["criadas"],
                "ja_pendentes": resultado["ja_pendentes"],
                "ignoradas_sem_hub": resultado["ignoradas_sem_hub"],
                "ignoradas_inativas": resultado["ignoradas_inativas"],
                "solicitacoes": [serializar_solicitacao(s) for s in resultado["solicitacoes"]],
            },
            status=status.HTTP_200_OK,
        )


class HubSincronizacaoStatusView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, sincronizacao_id):
        status_payload = request.data.get("status")
        status_permitidos = {
            HubSincronizacaoSolicitacao.STATUS_PROCESSANDO,
            HubSincronizacaoSolicitacao.STATUS_CONCLUIDA,
            HubSincronizacaoSolicitacao.STATUS_ERRO,
        }
        if status_payload not in status_permitidos:
            raise ValidationError({"status": "Status inválido para atualização pelo Hub."})
        solicitacao = HubSincronizacaoSolicitacao.objects.filter(pk=sincronizacao_id, hub=request.sysvar_hub).first()
        if not solicitacao:
            raise Http404
        solicitacao = atualizar_status_sincronizacao(
            solicitacao,
            status=status_payload,
            etapa_atual=_texto(request.data, "etapa_atual", "", 80),
            mensagem_erro=str(request.data.get("mensagem_erro") or ""),
        )
        return Response(serializar_solicitacao(solicitacao), status=status.HTTP_200_OK)


class HubSyncPushView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if request.data.get("versao") != 1:
            raise ValidationError({"versao": "Versão de sincronização inválida."})
        eventos = request.data.get("eventos") or []
        if not isinstance(eventos, list):
            raise ValidationError({"eventos": "Informe uma lista de eventos."})
        resultados = HubSyncProcessor(request.sysvar_hub, request=request).processar_lote(eventos)
        return Response({"resultados": resultados}, status=status.HTTP_200_OK)


class HubVendaFaixaNumeracaoView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        hub = request.sysvar_hub
        loja = hub.loja
        empresa = loja.empresa
        if loja.pk <= 0 or loja.pk > 999:
            return Response({"detail": "Loja invalida para numeracao de venda do Hub."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            inicio, fim = reservar_faixa_documento(
                empresa,
                SequenciaDocumento.TIPO_VENDA,
                escopo_loja(loja.pk),
                VENDA_HUB_TAMANHO_FAIXA_PADRAO,
            )
        except DocumentoSequenciaErro as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        HubVendaFaixaNumeracao.objects.create(hub=hub, inicio=inicio, fim=fim)
        return Response(
            {
                "loja_id": loja.pk,
                "inicio": inicio,
                "fim": fim,
            },
            status=status.HTTP_201_CREATED,
        )


class HubDevolucaoFaixaNumeracaoView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        hub = request.sysvar_hub
        empresa = hub.loja.empresa
        try:
            inicio, fim = reservar_faixa_documento(
                empresa,
                SequenciaDocumento.TIPO_DEVOLUCAO,
                escopo_empresa(),
                DEVOLUCAO_HUB_TAMANHO_FAIXA_PADRAO,
            )
        except DocumentoSequenciaErro as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        HubDevolucaoFaixaNumeracao.objects.create(hub=hub, inicio=inicio, fim=fim)
        return Response(
            {
                "empresa_id": empresa.pk,
                "inicio": inicio,
                "fim": fim,
            },
            status=status.HTTP_201_CREATED,
        )


class HubBootstrapView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        hub = request.sysvar_hub
        loja = hub.loja
        empresa = loja.empresa
        caixas = (
            Caixa.objects.filter(idloja=loja, ativo=True, tipo_caixa=Caixa.TIPO_LOJA)
            .order_by("codigo", "Idcaixa")
            .values("Idcaixa", "codigo", "descricao", "ativo")
        )

        return Response(
            {
                "bootstrap_versao": 1,
                "servidor_em": timezone.now(),
                "hub": {
                    "id": hub.pk,
                    "hub_uuid": str(hub.hub_uuid),
                    "versao": hub.versao,
                },
                "empresa": {
                    "id": empresa.pk,
                    "nome": empresa.nome,
                },
                "loja": {
                    "id": loja.pk,
                    "nome_loja": loja.nome_loja,
                    "apelido_loja": loja.apelido_loja,
                    "cnpj": loja.cnpj,
                    "estado": loja.estado,
                    "fiscal": _serializar_fiscal_loja(loja, empresa),
                },
                "caixas": [
                    {
                        "id": caixa["Idcaixa"],
                        "codigo": caixa["codigo"],
                        "descricao": caixa["descricao"],
                        "ativo": caixa["ativo"],
                    }
                    for caixa in caixas
                ],
                "cashback_config": _serializar_cashback_config(CashbackConfig.regra_ativa(empresa)),
            },
            status=status.HTTP_200_OK,
        )


class HubCatalogoView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(gerar_catalogo_hub(request.sysvar_hub), status=status.HTTP_200_OK)


class HubImagemRenderer(BaseRenderer):
    media_type = "image/*"
    format = "image"
    charset = None
    render_style = "binary"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


class HubCatalogoImagemView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    renderer_classes = [HubImagemRenderer]

    def get(self, request, imagem_id):
        imagem = (
            ProdutoImagem.objects.select_related("produto", "produto__empresa")
            .filter(pk=imagem_id, produto__empresa=request.sysvar_hub.loja.empresa)
            .first()
        )
        if not imagem:
            raise Http404
        arquivo = imagem.imagem_reduzida or imagem.imagem
        if not arquivo:
            raise Http404
        try:
            return FileResponse(arquivo.open("rb"), content_type=_content_type_imagem(arquivo.name))
        except FileNotFoundError as exc:
            raise Http404 from exc


def _content_type_imagem(nome):
    extensao = (nome.rsplit(".", 1)[-1] if "." in nome else "").lower()
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "gif": "image/gif",
    }.get(extensao, "application/octet-stream")


class HubFormasPagamentoView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        hub = request.sysvar_hub
        loja = hub.loja
        empresa = loja.empresa
        parcelas_ordenadas = PrazoPagamentoParcela.objects.order_by("ordem", "Idprazoparcela")
        prazos = (
            PrazoPagamento.objects
            .filter(empresa=empresa, ativo=True)
            .prefetch_related(Prefetch("parcelas", queryset=parcelas_ordenadas))
            .order_by("num_parcelas", "codigo", "Idprazo")
        )
        formas = (
            FormaPagamento.objects
            .filter(empresa=empresa)
            .select_related("prazo_pagamento")
            .prefetch_related(
                Prefetch("prazo_pagamento__parcelas", queryset=parcelas_ordenadas),
                Prefetch(
                    "condicoes_parcelamento",
                    queryset=(
                        FormaPagamentoCondicao.objects
                        .filter(empresa=empresa, ativo=True, prazo_pagamento__empresa=empresa)
                        .select_related("prazo_pagamento")
                        .prefetch_related(Prefetch("prazo_pagamento__parcelas", queryset=parcelas_ordenadas))
                        .order_by("prazo_pagamento__num_parcelas", "prazo_pagamento__codigo", "Idformapagamentocondicao")
                    ),
                    to_attr="condicoes_parcelamento_ativas",
                ),
            )
            .order_by("codigo", "Idformapagamento")
        )
        condicoes = {
            (cond.forma_pagamento_id, cond.prazo_pagamento_id): cond
            for cond in CondicaoAdquirente.objects
            .filter(empresa=empresa, ativo=True)
            .select_related("adquirente")
            .order_by("forma_pagamento_id", "prazo_pagamento_id", "Idcondicaoadquirente")
        }
        mapas_fiscais = (
            FormaPagamentoFiscalMap.objects
            .filter(empresa=empresa, ativo=True, forma_pagamento__empresa=empresa)
            .order_by("forma_pagamento_id", "codigo_tpag", "id")
            .values("forma_pagamento_id", "codigo_tpag", "descricao_fiscal")
        )

        formas_pagamento = []
        for forma in formas:
            prazo = forma.prazo_pagamento
            condicao = condicoes.get((forma.pk, forma.prazo_pagamento_id))
            condicoes_parcelamento = []
            for condicao_parcelamento in getattr(forma, "condicoes_parcelamento_ativas", []):
                prazo_condicao = condicao_parcelamento.prazo_pagamento
                condicoes_parcelamento.append({
                    "id": condicao_parcelamento.pk,
                    "prazo_pagamento_id": prazo_condicao.pk,
                    "prazo_codigo": prazo_condicao.codigo,
                    "prazo_descricao": prazo_condicao.descricao,
                    "prazo_num_parcelas": prazo_condicao.num_parcelas,
                    "prazo_intervalo_dias": prazo_condicao.intervalo_dias,
                    "taxa_percentual": _decimal_string(condicao_parcelamento.taxa_percentual, 4),
                    "taxa_fixa": _decimal_string(condicao_parcelamento.taxa_fixa, 2),
                    "parcelas": [
                        {
                            "ordem": parcela.ordem,
                            "dias": parcela.dias,
                            "percentual": _decimal_string(parcela.percentual, 6),
                        }
                        for parcela in prazo_condicao.parcelas.all()
                    ],
                })
            formas_pagamento.append({
                "id": forma.pk,
                "codigo": forma.codigo,
                "descricao": forma.descricao,
                "tipo": forma.tipo,
                "permite_parcelamento": forma.permite_parcelamento,
                "condicoes_parcelamento": condicoes_parcelamento,
                "num_parcelas": prazo.num_parcelas if prazo else None,
                "ativo": forma.ativo,
                "prazo_pagamento": {
                    "id": prazo.pk,
                    "codigo": prazo.codigo,
                    "descricao": prazo.descricao,
                    "num_parcelas": prazo.num_parcelas,
                    "intervalo_dias": prazo.intervalo_dias,
                } if prazo else None,
                "adquirente": condicao.adquirente.descricao if condicao else None,
                "adquirente_id": condicao.adquirente_id if condicao else None,
                "condicao_adquirente_id": condicao.pk if condicao else None,
                "conta_liquidacao_id": forma.conta_liquidacao_id,
                "gera_recebivel_bancario": forma.gera_recebivel_bancario,
                "prazo_credito_dias": forma.prazo_credito_dias,
                "taxa_percentual": _decimal_string(condicao.taxa_percentual if condicao else 0, 4),
                "taxa_fixa": _decimal_string(condicao.taxa_fixa if condicao else 0, 2),
                "tef_habilitado": forma.tef_habilitado,
                "tef_modalidade": forma.tef_modalidade,
                "tef_adquirente_codigo": forma.tef_adquirente_codigo,
                "tef_terminal_logico": forma.tef_terminal_logico,
                "parcelas": [
                    {
                        "ordem": parcela.ordem,
                        "dias": parcela.dias,
                        "percentual": _decimal_string(parcela.percentual, 6),
                    }
                    for parcela in (prazo.parcelas.all() if prazo else [])
                ],
            })
        prazos_pagamento = [
            {
                "id": prazo.pk,
                "codigo": prazo.codigo,
                "descricao": prazo.descricao,
                "num_parcelas": prazo.num_parcelas,
                "intervalo_dias": prazo.intervalo_dias,
                "ativo": prazo.ativo,
                "parcelas": [
                    {
                        "ordem": parcela.ordem,
                        "dias": parcela.dias,
                        "percentual": _decimal_string(parcela.percentual, 6),
                    }
                    for parcela in prazo.parcelas.all()
                ],
            }
            for prazo in prazos
        ]

        return Response(
            {
                "formas_pagamento_versao": 1,
                "gerado_em": timezone.now(),
                "hub": {
                    "id": hub.pk,
                    "hub_uuid": str(hub.hub_uuid),
                },
                "empresa": {
                    "id": empresa.pk,
                },
                "loja": {
                    "id": loja.pk,
                },
                "formas_pagamento": formas_pagamento,
                "prazos_pagamento": prazos_pagamento,
                "mapas_fiscais": list(mapas_fiscais),
            },
            status=status.HTTP_200_OK,
        )


class HubTiposDespesaPdvView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        hub = request.sysvar_hub
        loja = hub.loja
        empresa = loja.empresa
        tipos = (
            TipoDespesaPdv.objects
            .filter(empresa=empresa, ativo=True)
            .select_related("Idnatureza")
            .order_by("descricao", "codigo", "Idtipodespesapdv")
        )

        return Response(
            {
                "tipos_despesa_pdv_versao": 1,
                "gerado_em": timezone.now(),
                "hub": {
                    "id": hub.pk,
                    "hub_uuid": str(hub.hub_uuid),
                },
                "empresa": {
                    "id": empresa.pk,
                },
                "loja": {
                    "id": loja.pk,
                },
                "tipos_despesa_pdv": [
                    {
                        "id": tipo.pk,
                        "codigo": tipo.codigo,
                        "descricao": tipo.descricao,
                        "exige_documento": tipo.exige_documento,
                        "ativo": tipo.ativo,
                        "natureza": _serializar_natureza_despesa_pdv(tipo.Idnatureza),
                    }
                    for tipo in tipos
                ],
            },
            status=status.HTTP_200_OK,
        )


class HubClientesView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not hasattr(request, "sysvar_hub"):
            raise PermissionDenied("Autenticação Hub obrigatória.")
        hub = request.sysvar_hub
        loja = hub.loja
        empresa = loja.empresa
        clientes = (
            Cliente.objects
            .filter(empresa=empresa)
            .order_by("nome_cliente", "id")
            .values(
                "id",
                "tipo_pessoa",
                "documento",
                "cliente_padrao",
                "nome_cliente",
                "apelido",
                "endereco",
                "numero",
                "complemento",
                "cep",
                "bairro",
                "cidade",
                "estado",
                "telefone1",
                "telefone2",
                "email",
                "categoria",
                "bloqueio",
                "motivo_bloqueio",
                "aniversario",
                "mala_direta",
                "aceita_email",
                "aceita_whatsapp",
                "aceita_sms",
                "consentimento_em",
                "origem_consentimento",
                "ativo",
            )
        )
        vales_por_cliente = {}
        for vale in (
            ValeTroca.objects.filter(empresa=empresa, status=ValeTroca.STATUS_ABERTO, saldo__gt=0)
            .filter(models.Q(validade__isnull=True) | models.Q(validade__gte=timezone.localdate()))
            .values("Idvaletroca", "cliente_id", "documento", "saldo", "valor_original", "validade", "status")
        ):
            vales_por_cliente.setdefault(vale["cliente_id"], []).append(
                {
                    "id": vale["Idvaletroca"],
                    "documento": vale["documento"],
                    "saldo": vale["saldo"],
                    "valor_original": vale["valor_original"],
                    "validade": vale["validade"].isoformat() if vale["validade"] else None,
                    "status": vale["status"],
                }
            )
        cashback_por_cliente = {}
        for saldo in (
            CashbackMovimento.objects.filter(empresa=empresa, status=CashbackMovimento.STATUS_ATIVO)
            .filter(models.Q(validade__isnull=True) | models.Q(validade__gte=timezone.localdate()))
            .values("cliente_id")
            .annotate(
                saldo=models.Sum(
                    models.Case(
                        models.When(tipo=CashbackMovimento.TIPO_CREDITO, then="valor"),
                        models.When(tipo=CashbackMovimento.TIPO_ESTORNO, then="valor"),
                        default=models.Value(0) - models.F("valor"),
                        output_field=models.DecimalField(max_digits=18, decimal_places=2),
                    )
                )
            )
        ):
            cashback_por_cliente[saldo["cliente_id"]] = saldo["saldo"] or 0
        clientes_payload = []
        for cliente in clientes:
            item = dict(cliente)
            item["vales_troca"] = vales_por_cliente.get(cliente["id"], [])
            item["cashback_saldo_retaguarda"] = cashback_por_cliente.get(cliente["id"], 0)
            clientes_payload.append(item)

        return Response(
            {
                "clientes_versao": 1,
                "gerado_em": timezone.now(),
                "hub": {
                    "id": hub.pk,
                    "hub_uuid": str(hub.hub_uuid),
                },
                "empresa": {
                    "id": empresa.pk,
                },
                "loja": {
                    "id": loja.pk,
                },
                "clientes": clientes_payload,
            },
            status=status.HTTP_200_OK,
        )


def _serializar_cashback_config(config):
    if not config:
        return None
    return {
        "retaguarda_id": config.pk,
        "nome": config.nome,
        "ativo": config.ativo,
        "percentual": config.percentual,
        "validade_dias": config.validade_dias,
        "valor_minimo_geracao": config.valor_minimo_geracao,
        "valor_minimo_uso": config.valor_minimo_uso,
        "limite_uso_percentual": config.limite_uso_percentual,
        "consumidor_final_participa": config.consumidor_final_participa,
    }


class HubOperadoresView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        hub = request.sysvar_hub
        loja = hub.loja
        empresa = loja.empresa
        credenciais = (
            CredencialPdvUsuario.objects
            .filter(
                habilitado=True,
                usuario__is_active=True,
                usuario__empresa=empresa,
            )
            .filter(
                models.Q(usuario__loja=loja) | models.Q(usuario__lojas=loja)
            )
            .select_related("usuario", "usuario__perfil_principal")
            .order_by("usuario_id")
            .distinct()
        )

        operadores = []
        for credencial in credenciais:
            usuario = credencial.usuario
            perfil = usuario.perfil_principal
            operadores.append({
                "usuario_id": usuario.pk,
                "codigo": usuario.username,
                "nome": (usuario.get_full_name() or usuario.username).strip(),
                "tipo": usuario.type,
                "perfil": {"id": perfil.pk, "nome": perfil.nome} if perfil else None,
                "credencial_hash": credencial.senha_hash,
                "ativo": usuario.is_active,
            })

        return Response(
            {
                "operadores_versao": 1,
                "gerado_em": timezone.now(),
                "hub": {
                    "id": hub.pk,
                    "hub_uuid": str(hub.hub_uuid),
                },
                "empresa": {
                    "id": empresa.pk,
                },
                "loja": {
                    "id": loja.pk,
                },
                "operadores": operadores,
            },
            status=status.HTTP_200_OK,
        )


class HubVendedoresView(APIView):
    authentication_classes = [HubTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        hub = request.sysvar_hub
        loja = hub.loja
        empresa = loja.empresa
        vendedores_queryset = (
            Funcionarios.objects
            .filter(
                empresa=empresa,
                idloja=loja,
                ativo=True,
                situacao=Funcionarios.SITUACAO_ATIVO,
                participa_vendas=True,
            )
            .select_related("cargo", "idloja", "empresa")
            .order_by("nomefuncionario", "id")
        )

        vendedores = []
        for vendedor in vendedores_queryset:
            cargo = vendedor.cargo
            vendedores.append({
                "id": vendedor.pk,
                "matricula": vendedor.matricula,
                "nome": vendedor.nomefuncionario,
                "apelido": vendedor.apelido,
                "cargo": {
                    "id": cargo.pk,
                    "codigo": cargo.codigo,
                    "descricao": cargo.descricao,
                } if cargo else None,
                "comissionado": vendedor.comissionado,
                "comissao_percentual": _decimal_string(vendedor.comissao_percentual, 2),
                "ativo": vendedor.ativo,
                "situacao": vendedor.situacao,
                "participa_vendas": vendedor.participa_vendas,
            })

        return Response(
            {
                "vendedores_versao": 1,
                "gerado_em": timezone.now(),
                "hub": {
                    "id": hub.pk,
                    "hub_uuid": str(hub.hub_uuid),
                },
                "empresa": {
                    "id": empresa.pk,
                },
                "loja": {
                    "id": loja.pk,
                },
                "vendedores": vendedores,
            },
            status=status.HTTP_200_OK,
        )
