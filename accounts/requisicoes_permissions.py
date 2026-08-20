REQUISICOES_BINARY_ACCESS = {
    "requisicoes": "EDIT",
    "requisicoes_analise": "EDIT",
    "requisicoes_atendimento": "EDIT",
    "requisicoes_todas": "VIEW",
}


def normalize_requisicoes_access(module_key, access):
    if module_key not in REQUISICOES_BINARY_ACCESS:
        return access
    if access == "NONE":
        return "NONE"
    return REQUISICOES_BINARY_ACCESS[module_key]
