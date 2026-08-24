from dataclasses import dataclass

from rest_framework.exceptions import ValidationError

from .models import OrdemServico, RequisicaoMatrizResponsabilidade


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
