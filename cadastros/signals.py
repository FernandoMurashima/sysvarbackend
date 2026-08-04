from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from cadastros.models import Empresa, EmpresaContrato, EmpresaModulo, ModuloSistema


@receiver(post_save, sender=Empresa)
def ensure_empresa_contract(sender, instance, created, **kwargs):
    if not created:
        return
    contrato, _ = EmpresaContrato.objects.get_or_create(
        empresa=instance,
        defaults={
            "status": EmpresaContrato.STATUS_ATIVO if instance.ativo else EmpresaContrato.STATUS_SUSPENSO,
            "data_inicio": getattr(instance, "data_cadastro", None).date() if getattr(instance, "data_cadastro", None) else timezone.localdate(),
            "limite_usuarios": 1,
            "limite_sessoes_simultaneas": 1,
            "plano_completo": bool(instance.plano_completo or instance.licenca_master),
            "observacoes": "Contrato inicial criado automaticamente.",
        },
    )
    legacy_map = {
        "usa_vendas": "vendas",
        "usa_compras": "compras",
        "usa_estoque": "estoque",
        "usa_financeiro": "financeiro",
        "usa_fiscal": "fiscal",
        "usa_producao": "producao",
        "usa_distribuicao_producao": "distribuicao",
    }
    modules = {m.chave: m for m in ModuloSistema.objects.filter(chave__in=legacy_map.values())}
    for field, key in legacy_map.items():
        modulo = modules.get(key)
        if modulo:
            EmpresaModulo.objects.get_or_create(
                empresa=instance,
                modulo=modulo,
                defaults={"contratado": bool(getattr(instance, field, False)), "data_inicio": contrato.data_inicio},
            )
    try:
        from accounts.services.profiles import ensure_default_profiles

        ensure_default_profiles(instance)
    except Exception:
        pass
