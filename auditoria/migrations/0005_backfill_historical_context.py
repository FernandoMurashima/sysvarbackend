from django.db import migrations


def _display(obj, fields):
    if not obj:
        return None
    for field in fields:
        value = getattr(obj, field, None)
        if value is not None and str(value).strip():
            return str(value).strip()
    text = str(obj).strip()
    return text or None


def _user_name(user):
    if not user:
        return None
    full_name = " ".join(
        part for part in [getattr(user, "first_name", ""), getattr(user, "last_name", "")]
        if part and str(part).strip()
    ).strip()
    return full_name or getattr(user, "username", None) or getattr(user, "email", None)


def _instance_context(apps, log):
    if not log.app_label or not log.model or not log.object_id:
        return None
    try:
        Model = apps.get_model(log.app_label, log.model)
    except LookupError:
        return None
    try:
        return Model._base_manager.filter(pk=log.object_id).first()
    except Exception:
        return None


def _empresa_from_instance(instance):
    if not instance:
        return None
    if instance._meta.label_lower == "cadastros.empresa":
        return instance
    for attr in ("empresa", "Idempresa"):
        empresa = getattr(instance, attr, None)
        if empresa is not None:
            return empresa
    loja = _loja_from_instance(instance)
    return getattr(loja, "empresa", None) if loja else None


def _loja_from_instance(instance):
    if not instance:
        return None
    if instance._meta.label_lower == "cadastros.loja":
        return instance
    for attr in ("loja", "idloja", "Idloja"):
        loja = getattr(instance, attr, None)
        if loja is not None:
            return loja
    return None


def backfill_historical_context(apps, schema_editor):
    AuditLog = apps.get_model("auditoria", "AuditLog")
    for log in AuditLog.objects.select_related("user", "empresa", "loja").all().iterator():
        update_fields = []

        user = getattr(log, "user", None)
        if user:
            if not log.user_id_snapshot:
                log.user_id_snapshot = str(user.pk)
                update_fields.append("user_id_snapshot")
            if not log.username_snapshot and getattr(user, "username", None):
                log.username_snapshot = user.username
                update_fields.append("username_snapshot")
            if not log.user_nome_snapshot:
                log.user_nome_snapshot = _user_name(user)
                if log.user_nome_snapshot:
                    update_fields.append("user_nome_snapshot")

        instance = _instance_context(apps, log)
        empresa = getattr(log, "empresa", None) or _empresa_from_instance(instance)
        if not empresa and user and getattr(user, "empresa_id", None):
            empresa = user.empresa
        if empresa:
            if not log.empresa_id:
                log.empresa = empresa
                update_fields.append("empresa")
            if not log.empresa_id_snapshot:
                log.empresa_id_snapshot = str(empresa.pk)
                update_fields.append("empresa_id_snapshot")
            if not log.empresa_nome_snapshot:
                log.empresa_nome_snapshot = _display(empresa, ("nome_fantasia", "nome"))
                if log.empresa_nome_snapshot:
                    update_fields.append("empresa_nome_snapshot")

        loja = getattr(log, "loja", None) or _loja_from_instance(instance)
        if not loja and user and getattr(user, "loja_id", None):
            user_loja = user.loja
            if not empresa or getattr(user_loja, "empresa_id", None) == getattr(empresa, "pk", None):
                loja = user_loja
        if loja:
            if not log.loja_id:
                log.loja = loja
                update_fields.append("loja")
            if not log.loja_id_snapshot:
                log.loja_id_snapshot = str(loja.pk)
                update_fields.append("loja_id_snapshot")
            if not log.loja_nome_snapshot:
                log.loja_nome_snapshot = _display(loja, ("nome_loja", "apelido_loja"))
                if log.loja_nome_snapshot:
                    update_fields.append("loja_nome_snapshot")

        if update_fields:
            fields = sorted(set(update_fields))
            try:
                log.save(update_fields=fields, _audit_internal=True)
            except TypeError:
                log.save(update_fields=fields)


class Migration(migrations.Migration):

    dependencies = [
        ("auditoria", "0004_central_audit_phase1"),
    ]

    operations = [
        migrations.RunPython(backfill_historical_context, migrations.RunPython.noop),
    ]
