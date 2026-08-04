from accounts.models import PerfilAcesso, PerfilModuloPermissao
from cadastros.models import ModuloSistema


PROFILE_PERMISSIONS = {
    "Administrador delegado": {
        "operacional": "EDIT", "cadastros": "EDIT", "produtos": "EDIT", "configuracoes": "EDIT",
        "vendas": "EDIT", "compras": "EDIT", "estoque": "EDIT", "financeiro": "EDIT",
        "fiscal": "EDIT", "producao": "EDIT", "distribuicao": "EDIT", "relatorios": "VIEW",
    },
    "Gerente": {
        "operacional": "VIEW", "cadastros": "EDIT", "produtos": "EDIT", "configuracoes": "VIEW",
        "vendas": "EDIT", "compras": "VIEW", "estoque": "EDIT", "financeiro": "VIEW",
        "fiscal": "VIEW", "producao": "VIEW", "distribuicao": "EDIT", "relatorios": "VIEW",
    },
    "Vendedor": {"cadastros": "VIEW", "produtos": "VIEW", "vendas": "VIEW", "estoque": "VIEW"},
    "Caixa": {"cadastros": "VIEW", "produtos": "VIEW", "vendas": "EDIT", "financeiro": "VIEW", "estoque": "VIEW"},
    "Financeiro": {"cadastros": "VIEW", "financeiro": "EDIT", "relatorios": "VIEW"},
    "Compras": {"cadastros": "VIEW", "produtos": "VIEW", "compras": "EDIT", "fiscal": "VIEW"},
    "Estoque": {"produtos": "VIEW", "estoque": "EDIT", "distribuicao": "VIEW"},
    "Fiscal": {"cadastros": "VIEW", "fiscal": "EDIT", "financeiro": "VIEW"},
    "Regular": {"cadastros": "VIEW", "produtos": "VIEW"},
}

PROFILE_REQUIRED_MODULE = {
    "Administrador delegado": None,
    "Gerente": None,
    "Regular": None,
    "Vendedor": "vendas",
    "Caixa": "vendas",
    "Financeiro": "financeiro",
    "Compras": "compras",
    "Estoque": "estoque",
    "Fiscal": "fiscal",
}


def visible_profile_names_for_company(empresa):
    from accounts.services.effective_access import CompanyModuleService

    available = CompanyModuleService(empresa).available_module_keys()
    return {
        name
        for name, required_module in PROFILE_REQUIRED_MODULE.items()
        if required_module is None or required_module in available
    }


def hidden_profile_names_for_company(empresa):
    return set(PROFILE_REQUIRED_MODULE) - visible_profile_names_for_company(empresa)


def ensure_default_profiles(empresa):
    from accounts.services.effective_access import CompanyModuleService

    available = CompanyModuleService(empresa).available_module_keys()
    modules = {m.chave: m for m in ModuloSistema.objects.filter(chave__in={k for perms in PROFILE_PERMISSIONS.values() for k in perms})}
    profiles = {}
    for name, permissions in PROFILE_PERMISSIONS.items():
        perfil, _ = PerfilAcesso.objects.get_or_create(
            empresa=empresa,
            nome=name,
            defaults={
                "descricao": "Perfil criado automaticamente.",
                "ativo": True,
                "padrao": name == "Regular" and not PerfilAcesso.objects.filter(empresa=empresa, ativo=True, padrao=True).exists(),
            },
        )
        profiles[name] = perfil
        for key, access in permissions.items():
            modulo = modules.get(key)
            if not modulo:
                continue
            PerfilModuloPermissao.objects.update_or_create(
                perfil=perfil,
                modulo=modulo,
                defaults={"acesso": access if key in available else "NONE"},
            )
    return profiles
