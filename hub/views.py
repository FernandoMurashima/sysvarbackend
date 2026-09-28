import uuid

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
from financeiro.models import Caixa, CashbackConfig, CashbackMovimento, CondicaoAdquirente, FormaPagamento, PrazoPagamentoParcela, TipoDespesaPdv, ValeTroca
from fiscal.models import FormaPagamentoFiscalMap
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
from hub.models import AtivacaoSysvarHub, HubComandoAdministrativo, HubSincronizacaoSolicitacao, SysvarHub
from hub.operacional import normalizar_snapshot_operacional
from hub.sincronizacao import (
    atualizar_status_sincronizacao,
    hubs_no_escopo,
    lojas_no_escopo,
    montar_painel_sincronizacao,
    obter_comando_para_hub,
    serializar_solicitacao,
    solicitar_sincronizacao_loja,
    solicitar_sincronizacao_todas,
    validar_usuario_sincronizacao,
)
from hub.sync import HubSyncProcessor
from produto.models import ProdutoImagem

ADMIN_CONFIG_ROLES = {"Admin", "Diretor"}


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
        formas = (
            FormaPagamento.objects
            .filter(empresa=empresa)
            .select_related("prazo_pagamento")
            .prefetch_related(Prefetch("prazo_pagamento__parcelas", queryset=parcelas_ordenadas))
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
            formas_pagamento.append({
                "id": forma.pk,
                "codigo": forma.codigo,
                "descricao": forma.descricao,
                "tipo": forma.tipo,
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
