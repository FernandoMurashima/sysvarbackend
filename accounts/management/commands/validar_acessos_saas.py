from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import PerfilAcesso, UserModulePermission
from accounts.services.effective_access import CompanyModuleService
from cadastros.models import Empresa, EmpresaContrato, EmpresaModulo, ModuloSistema


LEGACY_MODULE_FLAGS = {
    "vendas": "usa_vendas",
    "compras": "usa_compras",
    "estoque": "usa_estoque",
    "financeiro": "usa_financeiro",
    "fiscal": "usa_fiscal",
    "producao": "usa_producao",
    "distribuicao": "usa_distribuicao_producao",
}


class Command(BaseCommand):
    help = "Valida contratos, módulos, masters, perfis e permissões efetivas do modelo SaaS."

    def add_arguments(self, parser):
        parser.add_argument("--corrigir-seguro", action="store_true", help="Aplica somente correções determinísticas.")

    def handle(self, *args, **options):
        corrigir = options["corrigir_seguro"]
        issues = []
        fixes = []
        today = timezone.localdate()

        with transaction.atomic():
            for empresa in Empresa.objects.select_related("contrato").prefetch_related("usuarios", "perfis_acesso"):
                contrato = getattr(empresa, "contrato", None)
                if not contrato:
                    issues.append(f"empresa:{empresa.pk}:sem_contrato")
                    if corrigir:
                        contrato = EmpresaContrato.objects.create(
                            empresa=empresa,
                            status=EmpresaContrato.STATUS_ATIVO,
                            data_inicio=today,
                            limite_usuarios=max(1, empresa.usuarios.filter(is_active=True, is_superuser=False).count()),
                            plano_completo=empresa.plano_completo or empresa.licenca_master,
                        )
                        fixes.append(f"empresa:{empresa.pk}:contrato_criado")
                if not contrato:
                    continue

                if empresa.licenca_master != empresa.plano_completo:
                    issues.append(f"empresa:{empresa.pk}:legado_licenca_master_diverge_plano_completo")
                    if corrigir:
                        empresa.plano_completo = bool(empresa.licenca_master)
                        empresa.save(update_fields=["plano_completo"])
                        contrato.plano_completo = empresa.plano_completo
                        contrato.save(update_fields=["plano_completo", "updated_at"])
                        fixes.append(f"empresa:{empresa.pk}:plano_completo_sincronizado")

                if contrato.usuario_master_id:
                    master = contrato.usuario_master
                    if master.is_superuser or master.empresa_id != empresa.pk or not master.is_active:
                        issues.append(f"empresa:{empresa.pk}:master_invalido:{master.pk}")
                else:
                    issues.append(f"empresa:{empresa.pk}:sem_master")
                    candidatos = list(empresa.usuarios.filter(is_active=True, type="Admin", is_superuser=False).order_by("id")[:2])
                    if corrigir and len(candidatos) == 1:
                        contrato.usuario_master = candidatos[0]
                        contrato.save(update_fields=["usuario_master", "updated_at"])
                        fixes.append(f"empresa:{empresa.pk}:master_definido:{candidatos[0].pk}")

                active_users = empresa.usuarios.filter(is_active=True, is_superuser=False).count()
                if active_users > int(contrato.limite_usuarios or 0):
                    issues.append(f"empresa:{empresa.pk}:limite_usuarios_excedido:{active_users}/{contrato.limite_usuarios}")

                defaults = list(PerfilAcesso.objects.filter(empresa=empresa, ativo=True, padrao=True).order_by("id"))
                if len(defaults) > 1:
                    issues.append(f"empresa:{empresa.pk}:perfis_padrao_duplicados:{','.join(str(p.pk) for p in defaults)}")
                    if corrigir:
                        PerfilAcesso.objects.filter(pk__in=[p.pk for p in defaults[1:]]).update(padrao=False)
                        fixes.append(f"empresa:{empresa.pk}:perfis_padrao_duplicados_corrigidos")

                default_profile = defaults[0] if defaults else None
                for user in empresa.usuarios.filter(is_active=True, is_superuser=False).select_related("perfil_principal"):
                    is_master = contrato.usuario_master_id == user.pk
                    if user.perfil_principal_id and user.perfil_principal.empresa_id != empresa.pk:
                        issues.append(f"user:{user.pk}:perfil_outra_empresa:{user.perfil_principal_id}")
                    if not is_master and not user.perfil_principal_id:
                        issues.append(f"user:{user.pk}:sem_perfil_principal")
                        if corrigir and default_profile:
                            user.perfil_principal = default_profile
                            user.save(update_fields=["perfil_principal"])
                            fixes.append(f"user:{user.pk}:perfil_padrao_atribuido:{default_profile.pk}")
                    wrong_store_ids = list(user.lojas.exclude(empresa=empresa).values_list("id", flat=True))
                    if wrong_store_ids:
                        issues.append(f"user:{user.pk}:lojas_outra_empresa:{','.join(map(str, wrong_store_ids))}")

                available = CompanyModuleService(empresa).available_module_keys()
                for perm in UserModulePermission.objects.filter(user__empresa=empresa).exclude(acesso=UserModulePermission.Access.NONE):
                    if perm.modulo not in available:
                        issues.append(f"user:{perm.user_id}:permissao_modulo_nao_contratado:{perm.modulo}")

                for chave, flag in LEGACY_MODULE_FLAGS.items():
                    if getattr(empresa, flag, False):
                        if chave not in available and not contrato.plano_completo:
                            issues.append(f"empresa:{empresa.pk}:flag_legado_sem_empresa_modulo:{flag}")
                            if corrigir:
                                modulo = ModuloSistema.objects.filter(chave=chave).first()
                                if modulo:
                                    EmpresaModulo.objects.update_or_create(
                                        empresa=empresa,
                                        modulo=modulo,
                                        defaults={"contratado": True},
                                    )
                                    fixes.append(f"empresa:{empresa.pk}:empresa_modulo_criado:{chave}")

        for issue in issues:
            self.stdout.write(issue)
        for fix in fixes:
            self.stdout.write(self.style.SUCCESS(f"corrigido:{fix}"))
        self.stdout.write(self.style.SUCCESS(f"total_inconsistencias={len(issues)} total_correcoes={len(fixes)}"))
