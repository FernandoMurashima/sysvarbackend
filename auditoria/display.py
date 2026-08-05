def _first_filled(obj, fields):
    for field in fields:
        value = getattr(obj, field, None)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def empresa_display_name(empresa):
    if not empresa:
        return None
    return _first_filled(empresa, ("nome_fantasia", "nome")) or str(empresa).strip() or None


def loja_display_name(loja):
    if not loja:
        return None
    return _first_filled(loja, ("nome_loja", "apelido_loja")) or str(loja).strip() or None


def user_display_name(user):
    if not user:
        return None
    get_full_name = getattr(user, "get_full_name", None)
    if callable(get_full_name):
        full_name = get_full_name()
        if full_name and full_name.strip():
            return full_name.strip()
    return _first_filled(user, ("first_name", "username", "email"))
