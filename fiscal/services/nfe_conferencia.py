from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from auditoria.models import AuditAction, AuditCategory
from auditoria.services import AuditService
from fiscal.models import NotaFiscalEntrada, NotaFiscalEntradaDivergenciaXml, NotaFiscalEntradaItemXml
from fiscal.services.nfe_conciliacao import conversao_info


def money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def quantidade_interna_recebida(item):
    if item.quantidade_recebida is None or not item.produto_fornecedor_id:
        return None
    info = conversao_info(item)
    if info["conversao_pendente"]:
        return None
    return item.produto_fornecedor.converter_quantidade_fornecedor(item.quantidade_recebida)


def resumo_conferencia(nota):
    return nota.resumo_conferencia_xml()


@transaction.atomic
def registrar_conferencia(item, quantidade_recebida, user=None, request=None):
    item = (
        NotaFiscalEntradaItemXml.objects.select_for_update()
        .select_related("nota", "nota__fornecedor", "produto", "produto_fornecedor", "produto__unidade")
        .get(pk=item.pk)
    )
    nota = item.nota
    if nota.status != NotaFiscalEntrada.Status.ABERTA:
        raise ValidationError({"nota": "Somente notas abertas podem receber conferência física."})
    if not item.produto_id:
        raise ValidationError({"produto": "Concilie o item XML antes de registrar a conferência física."})
    try:
        quantidade = Decimal(str(quantidade_recebida))
    except Exception as exc:
        raise ValidationError({"quantidade_recebida": "Informe uma quantidade recebida válida."}) from exc
    if quantidade < 0:
        raise ValidationError({"quantidade_recebida": "Quantidade recebida não pode ser negativa."})
    if quantidade > Decimal(item.quantidade_comercial or 0):
        raise ValidationError({"quantidade_recebida": "Quantidade recebida não pode ser maior que a quantidade fiscal do XML."})

    before = {
        "quantidade_recebida": str(item.quantidade_recebida) if item.quantidade_recebida is not None else None,
        "conferido_em": item.conferido_em.isoformat() if item.conferido_em else None,
    }
    item.quantidade_recebida = quantidade
    item.conferido_por = user if getattr(user, "is_authenticated", False) else None
    item.conferido_em = timezone.now()
    item.save(update_fields=["quantidade_recebida", "conferido_por", "conferido_em"])
    divergencia = sincronizar_divergencia(item, user=user, request=request)
    if request:
        AuditService.success(
            AuditAction.OBJECT_UPDATED,
            category=AuditCategory.FISCAL,
            request=request,
            user=user,
            instance=item,
            before=before,
            after={
                "quantidade_recebida": str(item.quantidade_recebida),
                "quantidade_fiscal": str(item.quantidade_comercial),
                "quantidade_faltante": str(item.quantidade_faltante),
                "valor_divergente": str(item.valor_divergente),
            },
            metadata={"legacy_action": "conferencia_fisica_xml", "nota": nota.pk, "item_xml": item.pk, "produto": item.produto_id},
        )
    return item, divergencia


def sincronizar_divergencia(item, user=None, request=None):
    faltante = item.quantidade_faltante
    if faltante is None:
        return None
    divergencia = getattr(item, "divergencia", None)
    if faltante > 0:
        valor = money(faltante * Decimal(item.valor_unitario_comercial or 0))
        defaults = {
            "empresa_id": item.nota.empresa_id,
            "nota": item.nota,
            "fornecedor": item.nota.fornecedor,
            "produto": item.produto,
            "quantidade_fiscal": item.quantidade_comercial,
            "quantidade_recebida": item.quantidade_recebida,
            "quantidade_faltante": faltante,
            "valor_divergente": valor,
            "status": NotaFiscalEntradaDivergenciaXml.Status.PENDENTE,
            "conferido_por": user if getattr(user, "is_authenticated", False) else None,
            "resolvido_por": None,
            "resolvido_em": None,
        }
        if divergencia:
            for key, value in defaults.items():
                setattr(divergencia, key, value)
            divergencia.save()
            created = False
        else:
            divergencia = NotaFiscalEntradaDivergenciaXml.objects.create(item_xml=item, **defaults)
            created = True
        _audit_divergencia(divergencia, request, user, created)
        return divergencia
    if divergencia and divergencia.status == NotaFiscalEntradaDivergenciaXml.Status.PENDENTE:
        divergencia.status = NotaFiscalEntradaDivergenciaXml.Status.RESOLVIDA
        divergencia.quantidade_recebida = item.quantidade_recebida
        divergencia.quantidade_faltante = Decimal("0")
        divergencia.valor_divergente = Decimal("0.00")
        divergencia.resolvido_por = user if getattr(user, "is_authenticated", False) else None
        divergencia.resolvido_em = timezone.now()
        divergencia.save()
        _audit_divergencia(divergencia, request, user, created=False, resolvida=True)
    return divergencia


def resolver_divergencia(divergencia, user=None, request=None):
    if divergencia.nota.status != NotaFiscalEntrada.Status.ABERTA:
        raise ValidationError({"nota": "Somente divergências de notas abertas podem ser resolvidas nesta etapa."})
    divergencia.status = NotaFiscalEntradaDivergenciaXml.Status.RESOLVIDA
    divergencia.resolvido_por = user if getattr(user, "is_authenticated", False) else None
    divergencia.resolvido_em = timezone.now()
    divergencia.save(update_fields=["status", "resolvido_por", "resolvido_em", "atualizado_em"])
    _audit_divergencia(divergencia, request, user, created=False, resolvida=True)
    return divergencia


def _audit_divergencia(divergencia, request, user, created=False, resolvida=False):
    if not request:
        return
    action = "divergencia_xml_resolvida" if resolvida else ("divergencia_xml_criada" if created else "divergencia_xml_atualizada")
    AuditService.success(
        AuditAction.OBJECT_CREATED if created else AuditAction.OBJECT_UPDATED,
        category=AuditCategory.FISCAL,
        request=request,
        user=user,
        instance=divergencia,
        after={
            "nota": divergencia.nota_id,
            "item_xml": divergencia.item_xml_id,
            "produto": divergencia.produto_id,
            "quantidade_fiscal": str(divergencia.quantidade_fiscal),
            "quantidade_recebida": str(divergencia.quantidade_recebida),
            "quantidade_faltante": str(divergencia.quantidade_faltante),
            "valor_divergente": str(divergencia.valor_divergente),
            "status": divergencia.status,
        },
        metadata={"legacy_action": action},
    )
