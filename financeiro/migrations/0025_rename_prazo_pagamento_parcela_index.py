from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("financeiro", "0024_prazos_pagamento_formas_meios"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="prazopagamentoparcela",
            new_name="financeiro__prazo_i_9ecf94_idx",
            old_name="financeiro__prazo__76b8e4_idx",
        ),
    ]
