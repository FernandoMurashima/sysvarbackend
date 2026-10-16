"""Explicit isolated runner; never initializes the configured MySQL connection."""
import argparse
import os
from pathlib import Path
import sys


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--hub-root',required=True)
    parser.add_argument('labels',nargs='*',default=['hub.tests_backup_integrated'])
    args=parser.parse_args()
    root=Path(args.hub_root).resolve()
    if not (root/'integracao/services/backup_transmission.py').is_file() or not (root/'runtime/recovery.py').is_file():
        parser.error('Informe o checkout local do Hub com os Prompts 8/11.')
    os.environ['SYSVAR_BACKUP_TEST_HUB_ROOT']=str(root)
    sys.path.append(str(root))
    os.environ['DJANGO_SETTINGS_MODULE']='Varejo.settings'
    from django.conf import settings
    settings.DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':':memory:'}}
    settings.HUB_BACKUP_RECEBIMENTO_ATIVO=False
    settings.HUB_BACKUP_RECOVERY_ATIVO=False
    settings.SYSVAR_BACKUP_INTEGRATION_TEST_ONLY=True
    import django
    django.setup()
    from django.apps import apps
    # Legacy MySQL-only migrations are not applied anywhere. Test SyncDB only.
    settings.MIGRATION_MODULES={app.label:None for app in apps.get_app_configs()}
    from django.test.utils import get_runner
    return bool(get_runner(settings)(verbosity=1,interactive=False).run_tests(args.labels))


if __name__=='__main__': raise SystemExit(main())
