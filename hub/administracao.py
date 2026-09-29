from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from financeiro.models import Caixa
from hub.models import HubComandoAdministrativo, HubSincronizacaoSolicitacao, SysvarHub


def listar_caixas_loja(loja):
    return [
        {
            "id": caixa.Idcaixa,
            "codigo": caixa.codigo,
            "descricao": caixa.descricao,
            "ativo": caixa.ativo,
        }
        for caixa in Caixa.objects.filter(idloja=loja, ativo=True).order_by("codigo", "Idcaixa")
    ]


def solicitar_configurar_terminal(hub, usuario, payload):
    _validar_hub_operavel(hub)
    _validar_primeira_carga_concluida(hub)
    codigo = _texto(payload.get("codigo"), 30)
    nome = _texto(payload.get("nome"), 100)
    hostname = _texto(payload.get("hostname"), 150)
    caixa_id = payload.get("caixa_retaguarda_id") or payload.get("caixa_id")
    if not codigo:
        raise ValidationError({"codigo": "Informe o código do terminal."})
    if not nome:
        raise ValidationError({"nome": "Informe o nome do terminal."})
    if not caixa_id:
        raise ValidationError({"caixa": "Informe o caixa."})
    caixa = Caixa.objects.filter(pk=caixa_id, idloja=hub.loja, ativo=True).first()
    if not caixa:
        raise ValidationError({"caixa": "Caixa inválido para a loja do Hub."})
    comando_payload = {
        "codigo": codigo,
        "nome": nome,
        "caixa_retaguarda_id": caixa.Idcaixa,
        "hostname": hostname,
    }
    return _criar_ou_reusar_comando(
        hub,
        HubComandoAdministrativo.TIPO_CONFIGURAR_TERMINAL,
        comando_payload,
        usuario,
        chave=("codigo",),
    )


def solicitar_gerar_pareamento(hub, usuario, payload):
    _validar_hub_operavel(hub)
    terminal_uuid = _texto(payload.get("terminal_uuid"), 64)
    codigo = _texto(payload.get("codigo") or payload.get("terminal_codigo"), 30)
    terminal = _buscar_terminal_snapshot(hub, terminal_uuid=terminal_uuid, codigo=codigo)
    if not terminal:
        raise ValidationError({"terminal": "Terminal não configurado no snapshot do Hub."})
    if terminal.get("ativo") is False:
        raise ValidationError({"terminal": "Terminal inativo não pode gerar pareamento."})
    comando_payload = {
        "terminal_uuid": terminal.get("terminal_uuid"),
        "codigo": terminal.get("codigo"),
    }
    return _criar_ou_reusar_comando(
        hub,
        HubComandoAdministrativo.TIPO_GERAR_PAREAMENTO,
        comando_payload,
        usuario,
        chave=("terminal_uuid", "codigo"),
    )


def obter_comando_administrativo_para_hub(hub):
    comando = (
        HubComandoAdministrativo.objects.filter(hub=hub, status=HubComandoAdministrativo.STATUS_PENDENTE)
        .order_by("solicitado_em", "id")
        .first()
    )
    return serializar_comando_para_hub(comando) if comando else None


def atualizar_resultado_comando(hub, comando_id, data):
    status = data.get("status")
    if status not in (
        HubComandoAdministrativo.STATUS_PROCESSANDO,
        HubComandoAdministrativo.STATUS_CONCLUIDO,
        HubComandoAdministrativo.STATUS_ERRO,
    ):
        raise ValidationError({"status": "Status inválido."})
    with transaction.atomic():
        comando = HubComandoAdministrativo.objects.select_for_update().filter(pk=comando_id, hub=hub).first()
        if not comando:
            return None
        if comando.status in HubComandoAdministrativo.STATUS_TERMINAIS:
            return comando
        agora = timezone.now()
        if status == HubComandoAdministrativo.STATUS_PROCESSANDO:
            if comando.iniciado_em is None:
                comando.iniciado_em = agora
        else:
            comando.concluido_em = agora
        comando.status = status
        comando.mensagem_erro = _texto(data.get("mensagem_erro"), 500)
        comando.resultado = _sanitizar_resultado(comando.tipo, data.get("resultado") or {})
        comando.save(
            update_fields=[
                "status",
                "resultado",
                "mensagem_erro",
                "iniciado_em",
                "concluido_em",
                "atualizado_em",
            ]
        )
        return comando


def serializar_comando(comando):
    if not comando:
        return None
    return {
        "id": comando.pk,
        "hub_id": comando.hub_id,
        "tipo": comando.tipo,
        "payload": comando.payload,
        "resultado": comando.resultado,
        "status": comando.status,
        "mensagem_erro": comando.mensagem_erro,
        "solicitado_em": comando.solicitado_em,
        "iniciado_em": comando.iniciado_em,
        "concluido_em": comando.concluido_em,
        "atualizado_em": comando.atualizado_em,
    }


def serializar_comando_para_hub(comando):
    return {
        "id": comando.pk,
        "hub_id": comando.hub_id,
        "tipo": comando.tipo,
        "payload": comando.payload,
        "status": comando.status,
    }


def estados_administrativos(linha):
    hub_id = linha.get("hub_id")
    hub_ativo = linha.get("hub_ativo")
    possui_credencial = linha.get("possui_credencial")
    snapshot = linha.get("snapshot_operacional") or {}
    terminais = snapshot.get("terminais") if isinstance(snapshot, dict) else []
    if not isinstance(terminais, list):
        terminais = []
    terminais_ativos = [terminal for terminal in terminais if terminal.get("ativo")]
    total = len(terminais_ativos)
    ativos = sum(1 for terminal in terminais if terminal.get("ativo"))
    pareados = sum(1 for terminal in terminais_ativos if terminal.get("pareado"))
    configuracao = "CONFIGURADO" if total else "NAO_CONFIGURADO"
    pareamento = "PAREADO" if total and pareados == total else "NAO_PAREADO"
    if not hub_id or not hub_ativo or not possui_credencial:
        configuracao = "NAO_DISPONIVEL"
        pareamento = "NAO_DISPONIVEL"
    elif not linha.get("primeira_sincronizacao_concluida"):
        configuracao = "NAO_DISPONIVEL"
        pareamento = "NAO_DISPONIVEL"
    elif not total:
        pareamento = "NAO_DISPONIVEL"
    return {
        "hub_estado": _hub_estado(linha),
        "configuracao_estado": configuracao,
        "pareamento_estado": pareamento,
        "total_terminais": total,
        "terminais_ativos": ativos,
        "terminais_pareados": pareados,
    }


def _hub_estado(linha):
    if not linha.get("hub_id"):
        return "SEM_HUB"
    if not linha.get("possui_credencial"):
        return "DESVINCULADO"
    if not linha.get("hub_ativo"):
        return "DESATIVADO"
    return "ATIVADO"


def _validar_hub_operavel(hub):
    if not hub:
        raise ValidationError({"hub": "Hub não encontrado."})
    if not hub.ativo:
        raise ValidationError({"hub": "Hub desativado."})
    if not hub.token_hash:
        raise ValidationError({"hub": "Hub sem credencial ativa."})


def _validar_primeira_carga_concluida(hub):
    if not HubSincronizacaoSolicitacao.objects.filter(
        hub=hub,
        status=HubSincronizacaoSolicitacao.STATUS_CONCLUIDA,
    ).exists():
        raise ValidationError(
            {
                "sincronizacao": (
                    "Aguarde a primeira sincronização do Hub ser concluída "
                    "antes de configurar terminais."
                )
            }
        )


def _criar_ou_reusar_comando(hub, tipo, payload, usuario, *, chave):
    with transaction.atomic():
        qs = HubComandoAdministrativo.objects.select_for_update().filter(
            hub=hub,
            tipo=tipo,
            status__in=HubComandoAdministrativo.STATUS_ATIVOS,
        )
        for comando in qs:
            if all(str(comando.payload.get(campo) or "") == str(payload.get(campo) or "") for campo in chave):
                return comando, False
        return HubComandoAdministrativo.objects.create(hub=hub, tipo=tipo, payload=payload, solicitado_por=usuario), True


def _buscar_terminal_snapshot(hub, *, terminal_uuid="", codigo=""):
    snapshot = hub.snapshot_operacional or {}
    for terminal in snapshot.get("terminais") or []:
        if terminal_uuid and terminal.get("terminal_uuid") == terminal_uuid:
            return terminal
        if codigo and terminal.get("codigo") == codigo:
            return terminal
    return None


def _sanitizar_resultado(tipo, resultado):
    if not isinstance(resultado, dict):
        return {}
    if tipo == HubComandoAdministrativo.TIPO_CONFIGURAR_TERMINAL:
        terminal = resultado.get("terminal") if isinstance(resultado.get("terminal"), dict) else resultado
        return {
            "terminal_uuid": _texto(terminal.get("terminal_uuid"), 64),
            "codigo": _texto(terminal.get("codigo"), 30),
            "nome": _texto(terminal.get("nome"), 100),
            "hostname": _texto(terminal.get("hostname"), 150),
            "ativo": bool(terminal.get("ativo")),
            "caixa": _sanitizar_caixa(terminal.get("caixa")),
        }
    if tipo == HubComandoAdministrativo.TIPO_GERAR_PAREAMENTO:
        return {
            "terminal": _sanitizar_terminal(resultado.get("terminal")),
            "codigo": _texto(resultado.get("codigo"), 20),
            "expira_em": _texto(resultado.get("expira_em"), 40),
            "estado": _texto(resultado.get("estado") or "CODIGO_DISPONIVEL", 30),
        }
    return {}


def _sanitizar_caixa(caixa):
    if not isinstance(caixa, dict):
        return None
    return {
        "id": _inteiro(caixa.get("id")),
        "codigo": _texto(caixa.get("codigo"), 30),
        "descricao": _texto(caixa.get("descricao"), 150),
        "ativo": bool(caixa.get("ativo")),
    }


def _sanitizar_terminal(terminal):
    if not isinstance(terminal, dict):
        return None
    return {
        "terminal_uuid": _texto(terminal.get("terminal_uuid"), 64),
        "codigo": _texto(terminal.get("codigo"), 30),
        "nome": _texto(terminal.get("nome"), 100),
    }


def _texto(valor, limite):
    if valor is None:
        return ""
    return str(valor).strip()[:limite]


def _inteiro(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None
