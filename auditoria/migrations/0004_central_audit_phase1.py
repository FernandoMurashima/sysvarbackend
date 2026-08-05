import uuid

import django.core.serializers.json
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def migrate_legacy_changes(apps, schema_editor):
    AuditLog = apps.get_model("auditoria", "AuditLog")
    for log in AuditLog.objects.all().iterator():
        if not log.event_id:
            log.event_id = uuid.uuid4()
        if not log.category:
            log.category = "SECURITY" if log.app_label == "accounts" else "PRODUCT" if log.app_label == "produto" else "SYSTEM"
        if not log.result:
            log.result = "SUCCESS"
        if not log.severity:
            log.severity = "INFO"
        if not log.origin:
            log.origin = "API"
        if not log.object_repr:
            log.object_repr = f"{log.app_label}.{log.model} #{log.object_id}"[:255] if log.object_id else f"{log.app_label}.{log.model}"[:255]
        changes = log.changes
        if changes and log.before_data is None and log.after_data is None:
            action = (log.action or "").lower()
            if action in {"create", "login"}:
                log.before_data = None
                log.after_data = changes
                log.changed_fields = list(changes.keys()) if isinstance(changes, dict) else []
            elif action == "update" and isinstance(changes, dict) and all(isinstance(v, (list, tuple)) and len(v) == 2 for v in changes.values()):
                log.before_data = {k: v[0] for k, v in changes.items()}
                log.after_data = {k: v[1] for k, v in changes.items()}
                log.changed_fields = list(changes.keys())
            elif action in {"delete", "logout"}:
                log.before_data = changes
                log.after_data = None
                log.changed_fields = list(changes.keys()) if isinstance(changes, dict) else []
            else:
                log.metadata = changes
        if log.user_id:
            log.user_id_snapshot = str(log.user_id)
            log.username_snapshot = getattr(log.user, "username", None) if hasattr(log, "user") else None
        log.save(update_fields=[
            "event_id", "category", "result", "severity", "origin", "object_repr",
            "before_data", "after_data", "changed_fields", "metadata",
            "user_id_snapshot", "username_snapshot",
        ])


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("cadastros", "0019_empresacontrato_limite_sessoes_simultaneas"),
        ("auditoria", "0003_remove_auditlog_auditoria_a_action_1263d1_idx_and_more"),
    ]

    operations = [
        migrations.AddField("auditlog", "event_id", models.UUIDField(blank=True, null=True, db_index=True, editable=False)),
        migrations.AddField("auditlog", "request_id", models.UUIDField(blank=True, null=True, db_index=True)),
        migrations.AddField("auditlog", "correlation_id", models.UUIDField(blank=True, null=True, db_index=True)),
        migrations.AddField("auditlog", "empresa", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="audit_logs", to="cadastros.empresa")),
        migrations.AddField("auditlog", "empresa_id_snapshot", models.CharField(blank=True, max_length=64, null=True)),
        migrations.AddField("auditlog", "empresa_nome_snapshot", models.CharField(blank=True, max_length=160, null=True)),
        migrations.AddField("auditlog", "loja", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="audit_logs", to="cadastros.loja")),
        migrations.AddField("auditlog", "loja_id_snapshot", models.CharField(blank=True, max_length=64, null=True)),
        migrations.AddField("auditlog", "loja_nome_snapshot", models.CharField(blank=True, max_length=120, null=True)),
        migrations.AddField("auditlog", "user_id_snapshot", models.CharField(blank=True, max_length=64, null=True)),
        migrations.AddField("auditlog", "username_snapshot", models.CharField(blank=True, max_length=150, null=True)),
        migrations.AddField("auditlog", "user_nome_snapshot", models.CharField(blank=True, max_length=180, null=True)),
        migrations.AddField("auditlog", "session_id", models.UUIDField(blank=True, null=True, db_index=True)),
        migrations.AddField("auditlog", "device_id", models.CharField(blank=True, db_index=True, max_length=128, null=True)),
        migrations.AddField("auditlog", "category", models.CharField(choices=[("SECURITY", "Segurança"), ("ACCESS", "Acesso"), ("CONTRACT", "Contrato"), ("USER_MANAGEMENT", "Usuários"), ("CADASTRO", "Cadastros"), ("PRODUCT", "Produtos"), ("PURCHASE", "Compras"), ("STOCK", "Estoque"), ("SALE", "Vendas"), ("FISCAL", "Fiscal"), ("FINANCIAL", "Financeiro"), ("ACCOUNTING", "Contabilidade"), ("PRODUCTION", "Produção"), ("DISTRIBUTION", "Distribuição"), ("REPORT", "Relatórios"), ("SYSTEM", "Sistema"), ("INTEGRATION", "Integração")], db_index=True, default="SYSTEM", max_length=32)),
        migrations.AddField("auditlog", "result", models.CharField(choices=[("SUCCESS", "Sucesso"), ("FAILURE", "Falha"), ("DENIED", "Negado"), ("PENDING", "Pendente"), ("ROLLED_BACK", "Rollback")], db_index=True, default="SUCCESS", max_length=16)),
        migrations.AddField("auditlog", "severity", models.CharField(choices=[("INFO", "Informação"), ("WARNING", "Alerta"), ("ERROR", "Erro"), ("CRITICAL", "Crítico")], db_index=True, default="INFO", max_length=16)),
        migrations.AddField("auditlog", "origin", models.CharField(choices=[("API", "API"), ("WEB", "Web"), ("PDV", "PDV"), ("OFFLINE_SYNC", "Sincronização offline"), ("COMMAND", "Command"), ("IMPORT", "Importação"), ("INTEGRATION", "Integração"), ("SYSTEM", "Sistema")], db_index=True, default="API", max_length=20)),
        migrations.AddField("auditlog", "object_repr", models.CharField(blank=True, max_length=255, null=True)),
        migrations.AddField("auditlog", "before_data", models.JSONField(blank=True, encoder=django.core.serializers.json.DjangoJSONEncoder, null=True)),
        migrations.AddField("auditlog", "after_data", models.JSONField(blank=True, encoder=django.core.serializers.json.DjangoJSONEncoder, null=True)),
        migrations.AddField("auditlog", "changed_fields", models.JSONField(blank=True, encoder=django.core.serializers.json.DjangoJSONEncoder, null=True)),
        migrations.AddField("auditlog", "metadata", models.JSONField(blank=True, encoder=django.core.serializers.json.DjangoJSONEncoder, null=True)),
        migrations.AddField("auditlog", "http_method", models.CharField(blank=True, db_index=True, max_length=12, null=True)),
        migrations.AddField("auditlog", "endpoint", models.CharField(blank=True, db_index=True, max_length=255, null=True)),
        migrations.AddField("auditlog", "status_code", models.PositiveSmallIntegerField(blank=True, db_index=True, null=True)),
        migrations.AddField("auditlog", "error_code", models.CharField(blank=True, max_length=80, null=True)),
        migrations.AddField("auditlog", "error_message", models.CharField(blank=True, max_length=512, null=True)),
        migrations.AlterField("auditlog", "action", models.CharField(db_index=True, max_length=64)),
        migrations.AlterField("auditlog", "model", models.CharField(db_index=True, max_length=100)),
        migrations.RunPython(migrate_legacy_changes, migrations.RunPython.noop),
        migrations.AlterField("auditlog", "event_id", models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)),
        migrations.AddIndex("auditlog", models.Index(fields=["empresa", "created_at"], name="aud_emp_created_idx")),
        migrations.AddIndex("auditlog", models.Index(fields=["empresa", "category", "created_at"], name="aud_emp_cat_created_idx")),
        migrations.AddIndex("auditlog", models.Index(fields=["empresa", "user", "created_at"], name="aud_emp_user_created_idx")),
        migrations.AddIndex("auditlog", models.Index(fields=["empresa", "app_label", "model", "object_id"], name="aud_emp_obj_idx")),
        migrations.AddIndex("auditlog", models.Index(fields=["loja", "created_at"], name="aud_loja_created_idx")),
        migrations.AddIndex("auditlog", models.Index(fields=["result", "severity", "created_at"], name="aud_res_sev_created_idx")),
    ]
