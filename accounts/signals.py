from django.db.models.signals import post_save, m2m_changed
from django.dispatch import receiver

from accounts.models import PerfilAcesso, User
from cadastros.models import EmpresaContrato


@receiver(post_save, sender=User)
def ensure_user_access_defaults(sender, instance, created, **kwargs):
    if not created or instance.is_superuser or not instance.empresa_id:
        return
    if not instance.perfil_principal_id:
        perfil = (
            PerfilAcesso.objects.filter(empresa=instance.empresa, ativo=True, nome="Administrador delegado").first()
            if instance.type in {"Admin", "Diretor"}
            else PerfilAcesso.objects.filter(empresa=instance.empresa, ativo=True, padrao=True).first()
        )
        perfil = perfil or PerfilAcesso.objects.filter(empresa=instance.empresa, ativo=True).order_by("id").first()
        if perfil:
            User.objects.filter(pk=instance.pk, perfil_principal__isnull=True).update(perfil_principal=perfil)
    if instance.type == "Admin" and instance.is_active:
        try:
            contrato = instance.empresa.contrato
        except EmpresaContrato.DoesNotExist:
            return
        if not contrato.usuario_master_id:
            contrato.usuario_master = instance
            contrato.incrementar_versao(save=False)
            contrato.save(update_fields=["usuario_master", "permissions_version", "updated_at"])
