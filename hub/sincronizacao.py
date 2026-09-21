from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from cadastros.models import Loja
from hub.models import HubSincronizacaoSolicitacao, SysvarHub


ROLES_SINCRONIZACAO = {"Admin", "Diretor", "Gerente"}


def validar_usuario_sincronizacao(user):
    if user.is_superuser:
        return
    if getattr(user, "type", "") not in ROLES_SINCRONIZACAO:
        from rest_framework.exceptions import PermissionDenied

        raise PermissionDenied("Usuário sem permissão para comandar sincronização do Hub.")
    if not getattr(user, "empresa_id", None):
        from rest_framework.exceptions import PermissionDenied

        raise PermissionDenied("Usuário sem empresa vinculada.")


def lojas_no_escopo(user):
    qs = Loja.objects.select_related("empresa").order_by("nome_loja", "id")
    if user.is_superuser:
        return qs
    return qs.filter(empresa_id=user.empresa_id)


def hubs_no_escopo(user):
    qs = SysvarHub.objects.select_related("loja", "loja__empresa")
    if user.is_superuser:
        return qs
    return qs.filter(loja__empresa_id=user.empresa_id)


def solicitar_sincronizacao_hub(hub, usuario=None):
    with transaction.atomic():
        hub = SysvarHub.objects.select_for_update().get(pk=hub.pk)
        existente = obter_sincronizacao_ativa(hub)
        if existente:
            return existente, False
        solicitacao = HubSincronizacaoSolicitacao.objects.create(hub=hub, solicitado_por=usuario)
        return solicitacao, True


def solicitar_sincronizacao_loja(loja, usuario=None):
    hub = SysvarHub.objects.filter(loja=loja, ativo=True).first()
    if not hub:
        return None, False
    return solicitar_sincronizacao_hub(hub, usuario=usuario)


def solicitar_sincronizacao_todas(usuario):
    criadas = 0
    ja_pendentes = 0
    ignoradas_sem_hub = 0
    ignoradas_inativas = 0
    solicitacoes = []
    hubs_por_loja = {hub.loja_id: hub for hub in hubs_no_escopo(usuario)}
    for loja in lojas_no_escopo(usuario):
        hub = hubs_por_loja.get(loja.pk)
        if not hub:
            ignoradas_sem_hub += 1
            continue
        if not hub.ativo:
            ignoradas_inativas += 1
            continue
        solicitacao, criada = solicitar_sincronizacao_hub(hub, usuario=usuario)
        solicitacoes.append(solicitacao)
        if criada:
            criadas += 1
        else:
            ja_pendentes += 1
    return {
        "criadas": criadas,
        "ja_pendentes": ja_pendentes,
        "ignoradas_sem_hub": ignoradas_sem_hub,
        "ignoradas_inativas": ignoradas_inativas,
        "solicitacoes": solicitacoes,
    }


def obter_sincronizacao_ativa(hub):
    return (
        HubSincronizacaoSolicitacao.objects.filter(
            hub=hub,
            status__in=HubSincronizacaoSolicitacao.STATUS_ATIVOS,
        )
        .order_by("solicitado_em", "id")
        .first()
    )


def obter_comando_para_hub(hub):
    solicitacao = obter_sincronizacao_ativa(hub)
    if not solicitacao:
        return None
    return serializar_solicitacao(solicitacao)


def atualizar_status_sincronizacao(solicitacao, *, status, etapa_atual="", mensagem_erro=""):
    if status not in dict(HubSincronizacaoSolicitacao.STATUS_CHOICES):
        from rest_framework.exceptions import ValidationError

        raise ValidationError({"status": "Status inválido."})
    with transaction.atomic():
        solicitacao = HubSincronizacaoSolicitacao.objects.select_for_update().get(pk=solicitacao.pk)
        if solicitacao.status in HubSincronizacaoSolicitacao.STATUS_TERMINAIS:
            return solicitacao
        agora = timezone.now()
        if status == HubSincronizacaoSolicitacao.STATUS_PROCESSANDO:
            if solicitacao.iniciado_em is None:
                solicitacao.iniciado_em = agora
        elif status == HubSincronizacaoSolicitacao.STATUS_CONCLUIDA:
            solicitacao.concluido_em = agora
            solicitacao.mensagem_erro = ""
        elif status == HubSincronizacaoSolicitacao.STATUS_ERRO:
            solicitacao.concluido_em = agora
            solicitacao.mensagem_erro = str(mensagem_erro or "")[:2000]
        solicitacao.status = status
        solicitacao.etapa_atual = str(etapa_atual or "")[:80]
        solicitacao.save(
            update_fields=[
                "status",
                "iniciado_em",
                "concluido_em",
                "etapa_atual",
                "mensagem_erro",
                "atualizado_em",
            ]
        )
        return solicitacao


def montar_painel_sincronizacao(user):
    ultimas = HubSincronizacaoSolicitacao.objects.order_by("-solicitado_em", "-id")
    linhas = []
    qs = lojas_no_escopo(user).select_related("sysvar_hub").prefetch_related(
        Prefetch("sysvar_hub__sincronizacoes", queryset=ultimas)
    )
    for loja in qs:
        try:
            hub = loja.sysvar_hub
        except SysvarHub.DoesNotExist:
            hub = None
        ultima = None
        ativa = None
        if hub:
            sincronizacoes = list(hub.sincronizacoes.all())
            ultima = sincronizacoes[0] if sincronizacoes else None
            ativa = next((s for s in sincronizacoes if s.status in HubSincronizacaoSolicitacao.STATUS_ATIVOS), None)
        referencia = ativa or ultima
        status_visual = "VERMELHO"
        if ativa:
            status_visual = "AMARELO"
        elif hub and hub.ativo and ultima and ultima.status == HubSincronizacaoSolicitacao.STATUS_CONCLUIDA:
            status_visual = "VERDE"
        linhas.append(
            {
                "loja_id": loja.pk,
                "loja_nome": loja.nome_loja,
                "empresa_id": loja.empresa_id,
                "hub_id": hub.pk if hub else None,
                "hub_uuid": str(hub.hub_uuid) if hub else None,
                "hub_ativo": bool(hub and hub.ativo),
                "hostname": hub.hostname if hub else "",
                "versao": hub.versao if hub else "",
                "ultimo_ip": str(hub.ultimo_ip) if hub and hub.ultimo_ip else None,
                "ultimo_contato": hub.ultimo_contato if hub else None,
                "sincronizacao_id": referencia.pk if referencia else None,
                "sincronizacao_status": referencia.status if referencia else "",
                "solicitado_em": referencia.solicitado_em if referencia else None,
                "iniciado_em": referencia.iniciado_em if referencia else None,
                "concluido_em": referencia.concluido_em if referencia else None,
                "etapa_atual": referencia.etapa_atual if referencia else "",
                "mensagem_erro": referencia.mensagem_erro if referencia else "",
                "status_visual": status_visual,
            }
        )
    return linhas


def serializar_solicitacao(solicitacao):
    return {
        "id": solicitacao.pk,
        "hub_id": solicitacao.hub_id,
        "tipo": solicitacao.tipo,
        "status": solicitacao.status,
        "solicitado_em": solicitacao.solicitado_em,
        "iniciado_em": solicitacao.iniciado_em,
        "concluido_em": solicitacao.concluido_em,
        "etapa_atual": solicitacao.etapa_atual,
        "mensagem_erro": solicitacao.mensagem_erro,
        "atualizado_em": solicitacao.atualizado_em,
    }
