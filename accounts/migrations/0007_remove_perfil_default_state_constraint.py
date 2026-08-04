from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_perfilacesso_alter_usermodulepermission_modulo_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="perfilacesso",
                    name="uq_perfil_padrao_ativo_empresa",
                ),
            ],
        ),
    ]
