from dataclasses import dataclass

from decimal import Decimal

from django.db.models import Sum
from rest_framework.exceptions import ValidationError

from produto.models import ProdutoUsoConsumoEstoque

from .models import OrdemServico, RequisicaoHistorico, RequisicaoMatrizResponsabilidade


TIPO_REQUISICAO_LABELS = {
    "USO_CONSUMO": "Uso e Consumo",
    "MANUTENCAO": "Manutenção",
    "TI": "TI",
}


@dataclass(frozen=True)
class ResponsabilidadeRequisicao:
    setor_atendimento: object
    setor_aquisicao: object


def resolver_responsabilidade_requisicao(empresa, tipo_requisicao):
    matriz = (
        RequisicaoMatrizResponsabilidade.objects
        .select_related("setor_atendimento", "setor_aquisicao")
        .filter(empresa=empresa, tipo_requisicao=tipo_requisicao, ativo=True)
        .first()
    )
    if not matriz:
        label = TIPO_REQUISICAO_LABELS.get(tipo_requisicao, tipo_requisicao)
        raise ValidationError({
            "tipo_requisicao": f"Não existe Central de Atendimento configurada para requisições de {label} nesta empresa."
        })
    return ResponsabilidadeRequisicao(
        setor_atendimento=matriz.setor_atendimento,
        setor_aquisicao=matriz.setor_aquisicao,
    )


def garantir_ordem_servico_requisicao(requisicao):
    if requisicao.tipo_requisicao not in {"MANUTENCAO", "TI"}:
        return None
    descricao = (requisicao.justificativa or requisicao.observacoes or "").strip()
    ordem, _ = OrdemServico.objects.get_or_create(
        requisicao=requisicao,
        defaults={
            "empresa": requisicao.empresa,
            "loja": requisicao.loja,
            "setor_solicitante": requisicao.setor,
            "setor_responsavel": requisicao.setor_responsavel,
            "tipo": requisicao.tipo_requisicao,
            "origem": "REQUISICAO",
            "descricao": descricao,
        },
    )
    return ordem


def _registrar_historico_requisicao_os(requisicao, observacao, usuario=None, status_anterior="", status_novo=""):
    if RequisicaoHistorico.objects.filter(requisicao=requisicao, observacao=observacao).exists():
        return None
    return RequisicaoHistorico.objects.create(
        requisicao=requisicao,
        usuario=usuario,
        acao="STATUS",
        status_anterior=status_anterior or "",
        status_novo=status_novo or "",
        observacao=observacao,
    )


def sincronizar_requisicao_com_ordem_servico(ordem_servico, usuario=None, registrar_inicio=False):
    requisicao = ordem_servico.requisicao
    if requisicao.tipo_requisicao not in {"MANUTENCAO", "TI"}:
        return {"requisicao": False, "itens": 0}

    if ordem_servico.status == "CANCELADA":
        return {"requisicao": False, "itens": 0}

    status_destino = "CONCLUIDA" if ordem_servico.status == "CONCLUIDA" else "EM_ATENDIMENTO"
    status_anterior = requisicao.status
    requisicao_atualizada = False
    if requisicao.status != status_destino:
        requisicao.status = status_destino
        requisicao.save(update_fields=["status", "atualizado_em"])
        requisicao_atualizada = True

    itens_atualizados = 0
    if ordem_servico.status == "CONCLUIDA":
        itens = requisicao.itens.filter(tipo="SERVICO").exclude(status__in=["SERVICO_CONCLUIDO", "CANCELADO", "REJEITADO"])
        itens_atualizados = itens.update(status="SERVICO_CONCLUIDO")
        _registrar_historico_requisicao_os(
            requisicao,
            f"Atendida pela OS nº {ordem_servico.id}.",
            usuario=usuario,
            status_anterior=status_anterior,
            status_novo="CONCLUIDA",
        )
    elif registrar_inicio:
        _registrar_historico_requisicao_os(
            requisicao,
            f"Atendimento iniciado pela OS nº {ordem_servico.id}.",
            usuario=usuario,
            status_anterior=status_anterior,
            status_novo=status_destino,
        )

    return {"requisicao": requisicao_atualizada, "itens": itens_atualizados}


def loja_almoxarifado_central(empresa):
    responsabilidade = resolver_responsabilidade_requisicao(empresa, "USO_CONSUMO")
    setor = responsabilidade.setor_atendimento
    if not setor or not setor.loja_id:
        raise ValidationError({"detail": "Não foi possível identificar o estoque do Almoxarifado responsável por esta necessidade."})
    return setor.loja


def estoque_disponivel_material_os(material):
    if not material.produto_id:
        return Decimal("0")
    loja = loja_almoxarifado_central(material.ordem_servico.empresa)
    return ProdutoUsoConsumoEstoque.objects.filter(
        empresa=material.ordem_servico.empresa,
        loja=loja,
        produto=material.produto,
    ).aggregate(total=Sum("saldo"))["total"] or Decimal("0")


def atualizar_status_material_os(material):
    if material.status in {"ATENDIDA", "CANCELADA"}:
        return material
    estoque = estoque_disponivel_material_os(material)
    material.qtd_pendente = max(Decimal(material.qtd_necessaria or 0) - Decimal(material.qtd_atendida or 0), Decimal("0"))
    if material.qtd_pendente == 0:
        material.status = "ATENDIDA"
    elif estoque >= material.qtd_pendente:
        material.status = "DISPONIVEL"
    elif material.status != "EM_COMPRA":
        material.status = "PENDENTE"
    material.save(update_fields=["qtd_pendente", "status", "atualizado_em"])
    return material


def atualizar_status_material_ordem_servico(ordem_servico):
    if ordem_servico.status in {"CONCLUIDA", "CANCELADA"}:
        return ordem_servico
    pendentes = ordem_servico.materiais.filter(status__in={"PENDENTE", "DISPONIVEL", "EM_COMPRA"}).exists()
    if pendentes and ordem_servico.status == "ABERTA":
        ordem_servico.status = "AGUARDANDO_MATERIAL"
        ordem_servico.save(update_fields=["status", "atualizado_em"])
    elif not pendentes and ordem_servico.status == "AGUARDANDO_MATERIAL":
        ordem_servico.status = "EM_ATENDIMENTO"
        ordem_servico.save(update_fields=["status", "atualizado_em"])
    sincronizar_requisicao_com_ordem_servico(ordem_servico)
    return ordem_servico
