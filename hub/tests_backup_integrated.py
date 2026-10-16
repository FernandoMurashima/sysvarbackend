"""Real cross-checkout HTTPS/AES/SQLite flow, fictitious MySQL generation/replay only."""
import gzip
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import tempfile
import threading
import time
import tracemalloc
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from wsgiref.simple_server import WSGIRequestHandler, make_server

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from datetime import datetime, timedelta, timezone as utc
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.handlers.wsgi import WSGIHandler
from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from hub import tests_backup_recebimento as fixtures
from hub.backup_files import destination
from hub.backup_integrity import auditar_backups_hub
from hub.models import HubBackup, HubBackupParte, HubEventoRecebido, HubVendaMapeamento
from hub.sync import HubSyncProcessor, payload_hash


class SecureHandler(WSGIRequestHandler):
    def get_environ(self):
        env=super().get_environ(); env['HTTPS']='on'; env['wsgi.url_scheme']='https'
        return env
    def log_message(self,*args):
        pass


class BackupIntegratedTests(TransactionTestCase):
    reset_sequences=True
    cleanup_files=fixtures.BackupRecebimentoTests.cleanup_files
    client_for=fixtures.BackupRecebimentoTests.client_for
    full_manifest=fixtures.BackupRecebimentoTests.full_manifest

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not getattr(settings,'SYSVAR_BACKUP_INTEGRATION_TEST_ONLY',False) or connection.vendor!='sqlite':
            raise RuntimeError('Use somente hub.run_backup_integration_tests com SQLite isolado.')
        from integracao.services import backup_transmission
        from runtime import recovery
        from runtime.tests_recovery import FakeMysql
        cls.transport,cls.recovery,cls.fake_mysql=backup_transmission,recovery,FakeMysql
        cls.hub_root=Path(os.environ['SYSVAR_BACKUP_TEST_HUB_ROOT'])
        cls.tls_temp=tempfile.TemporaryDirectory(prefix='sysvar-backup-e2e-tls-')
        cls.addClassCleanup(cls.tls_temp.cleanup)
        cls.cert=Path(cls.tls_temp.name)/'fictitious-cert.pem'; cls.key_path=cls.cert.with_name('fictitious-key.pem')
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')]); now=datetime.now(utc.utc)
        cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),False)
            .add_extension(x509.BasicConstraints(ca=True,path_length=None),True).sign(key,hashes.SHA256()))
        cls.cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        cls.key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))

    def setUp(self):
        fixtures.BackupRecebimentoTests.setUpTestData.__func__(type(self))
        fixtures.BackupRecebimentoTests.setUp(self)
        options=override_settings(HUB_BACKUP_PART_BYTES=65536,HUB_BACKUP_MAX_FILE_BYTES=16*1024**2,
            HUB_BACKUP_RECOVERY_ATIVO=True,HUB_BACKUP_RECOVERY_AUTH_SECONDS=900,HUB_BACKUP_RETENTION_TIME_ZONE='America/Sao_Paulo',
            SECRET_KEY='fictitious-backup-integration-signing-key-only-123456789',ALLOWED_HOSTS=['127.0.0.1','localhost'],DEBUG=False)
        options.enable(); self.addCleanup(options.disable)
        self.token=self.hub.gerar_token()
        self.support=get_user_model().objects.create_superuser(username='backup-integration-support',password=None)
        def session(key):
            if key!='FICTITIOUS-ADMIN-SESSION': raise AuthenticationFailed()
            return self.support,SimpleNamespace(),SimpleNamespace()
        auth=patch('accounts.authentication.ConcurrentSessionService.validate_session_token',side_effect=session)
        auth.start(); self.addCleanup(auth.stop)
        self.server=make_server('127.0.0.1',0,WSGIHandler(),handler_class=SecureHandler)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(self.cert,self.key_path)
        self.server.socket=context.wrap_socket(self.server.socket,server_side=True)
        self.server_thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.server_thread.start()
        self.addCleanup(self.close_server)
        self.context=ssl.create_default_context(cafile=str(self.cert))
        self.base=f'https://127.0.0.1:{self.server.server_port}'
        self.fixture_temp=tempfile.TemporaryDirectory(prefix='sysvar-backup-e2e-case-'); self.addCleanup(self.fixture_temp.cleanup)
        self.case=Path(self.fixture_temp.name)
        self.source_uuid=str(uuid4())
        self.event=dict(evento_uuid=str(uuid4()),chave_idempotencia='fictitious-restored-event',tipo='VENDA_FINALIZADA',payload={'venda_uuid':str(uuid4())})
        self.original_event=HubEventoRecebido.objects.create(hub=self.hub,evento_uuid=self.event['evento_uuid'],chave_idempotencia=self.event['chave_idempotencia'],
            tipo=self.event['tipo'],payload=self.event['payload'],payload_hash=payload_hash(self.event['payload']),status='PROCESSADO',processado_em=timezone.now())
        self.generate()

    def close_server(self):
        self.server.shutdown(); self.server.server_close(); self.server_thread.join(timeout=5)
        connections.close_all()

    def generate(self,size=1024):
        if (self.case/'fixture-plan.json').exists():
            extra=tempfile.TemporaryDirectory(prefix='sysvar-backup-e2e-case-'); self.addCleanup(extra.cleanup)
            self.case=Path(extra.name)
        identity=dict(hub_uuid=str(self.hub.hub_uuid),empresa_id=self.empresa.pk,loja_id=self.loja.pk,retaguarda_hub_id=self.hub.pk)
        (self.case/'identity.json').write_text(json.dumps(dict(test_only='FICTITIOUS_SYSVAR_BACKUP_INTEGRATION',identity=identity,
            server_uuid=self.source_uuid,payload_bytes=size,evento=self.event)),encoding='utf-8')
        env=dict(os.environ,SYSVAR_BACKUP_TEST_FIXTURE_ROOT=str(self.case))
        env.pop('PSModulePath',None)  # Let Windows PowerShell resolve its own 5.1 modules, not inherited pwsh 7 modules.
        command="Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1') -Force; $r=Invoke-Pester -Script (Join-Path $env:SYSVAR_BACKUP_TEST_HUB_ROOT 'deploy/windows/tests/backup-integrated-fixture.Tests.ps1') -PassThru -Quiet; if ($r.FailedCount -gt 0) { $r.TestResult | Where-Object { $_.Result -eq 'Failed' } | ForEach-Object { Write-Output $_.FailureMessage }; exit 1 }"
        result=subprocess.run([r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe','-NoProfile','-NonInteractive','-Command',command],env=env,capture_output=True,timeout=40)
        self.assertEqual(result.returncode,0,result.stdout.decode('utf-8',errors='replace') + result.stderr.decode('utf-8',errors='replace'))
        self.plan=json.loads((self.case/'fixture-plan.json').read_text(encoding='utf-8-sig'))
        hub=SimpleNamespace(**identity)
        self.entries=self.transport.load_entries(self.plan,hub)
        self.client=self.transport.BackupClient(self.base,self.token,bandwidth=16777216,timeout=5,finalize_timeout=30)
        self.client.context=self.context
        self.sender=self.transport.BackupSender(self.plan['backup_root'],self.client,max_seconds=120,clock=lambda:100000)
        self.source_hashes={str(e['path']):self.recovery.file_hash(e['path']) for e in self.entries}

    def request(self,method,path,data=None,*,grant=None,read_limit=None):
        client=http.client.HTTPSConnection('127.0.0.1',self.server.server_port,context=self.context,timeout=30)
        headers={'Authorization':'Token FICTITIOUS-ADMIN-SESSION'}
        if grant: headers['X-Backup-Recovery-Token']=grant['autorizacao_token']
        raw=None if data is None else json.dumps(data).encode()
        if raw is not None: headers['Content-Type']='application/json'
        try:
            client.request(method,path,raw,headers)
            response=client.getresponse(); headers=dict(response.getheaders())
            body=response.read() if read_limit is None else response.read(read_limit)
            return response.status,headers,body
        finally: client.close()

    def send(self):
        state=self.sender.run(self.entries)
        self.assertEqual(state['status'],'RECEBIDO_CENTRAL',state.get('failure_code'))
        self.assertEqual(state['pending_files'],0)
        self.assertEqual(HubBackup.objects.count(),3)
        self.assertEqual(set(HubBackup.objects.values_list('estado_validacao',flat=True)),{'PENDENTE'})
        for entry in self.entries:
            self.assertEqual(HubBackup.objects.get(backup_uuid=entry['id']).manifesto_sha256,entry['manifest_hash'])
        return state

    def audit(self):
        report=auditar_backups_hub(empresa_id=self.empresa.pk,loja_id=self.loja.pk,hub_id=self.hub.pk,registrar=True)
        self.assertTrue(all(item['integridade']=='VALIDO' for item in report['backups']),report)
        return report

    def grant(self,point=None):
        point=point or self.entries[-1]['id']
        path=f'/api/hub/backups/recuperacao/{self.empresa.pk}/{self.loja.pk}/autorizar/'
        status,_,body=self.request('POST',path,dict(ponto_uuid=point,instalacao_uuid=str(self.hub.hub_uuid),servidor_mysql_uuid=self.source_uuid,autorizar=True,motivo='fictitious integration scenario'))
        self.assertEqual(status,201,body)
        return json.loads(body)

    def export(self,grant,limit=None):
        package=self.case/'recovery-package'; package.mkdir(exist_ok=True)
        (package/'pacote.json').write_text(json.dumps(grant['manifesto']),encoding='utf-8')
        for item in grant['manifesto']['arquivos']:
            client=http.client.HTTPSConnection('127.0.0.1',self.server.server_port,context=self.context,timeout=30)
            try:
                client.request('GET',item['download_url'],headers={'Authorization':'Token FICTITIOUS-ADMIN-SESSION','X-Backup-Recovery-Token':grant['autorizacao_token']})
                response=client.getresponse(); self.assertEqual(response.status,200)
                self.assertIn('no-store',response.getheader('Cache-Control'))
                with (package/item['nome']).open('wb') as output:
                    count=0
                    while chunk:=response.read(65536 if limit is None else limit):
                        output.write(chunk); count+=len(chunk)
                        if limit is not None: break
                if limit is None:
                    self.assertEqual(count,item['tamanho'])
                    self.assertEqual(self.recovery.file_hash(package/item['nome']),item['sha256'])
            finally: client.close()
        return package

    def recovery_plan(self,grant,package):
        target=self.case/'replacement'; config=target/'config'; config.mkdir(parents=True,exist_ok=True)
        (config/'recovery-block.json').write_text('{}')
        datadir=target/'mysql'/'data'; datadir.mkdir(parents=True,exist_ok=True)
        plan=dict(version=1,package_dir=str(package),program_data=str(target),install_root=str(self.case/'install'),manifest_sha256=grant['manifesto_sha256'],
            point_uuid=grant['manifesto']['ponto_uuid'],empresa_id=self.empresa.pk,loja_id=self.loja.pk,installation_uuid=str(self.hub.hub_uuid),source_server_uuid=self.source_uuid,
            mysql_version='8.0.46',timeout_seconds=60,resume_after_support=False)
        state=dict(server_uuid=str(uuid4()),datadir=str(datadir))
        return plan,state

    def assert_preserved(self):
        self.assertEqual(self.source_hashes,{path:self.recovery.file_hash(Path(path)) for path in self.source_hashes})
        for backup in HubBackup.objects.filter(estado_recebimento='RECEBIDO'): self.assertTrue(destination(backup).is_file())

    def restore(self,plan,state,*,fail=False):
        fake=self.fake_mysql(fail=fail)
        with patch.object(self.recovery,'verify_target',return_value=state),patch.object(self.recovery,'start_mysql',return_value=fake),patch.object(self.recovery,'logical_check',return_value={'tables_checked':3,'foreign_keys_checked':0}):
            before=self.recovery.recover(plan)
            self.assertEqual(before['status'],'DRY_RUN_APROVADO')
            result=self.recovery.recover(plan,execute=True,confirmation='RESTAURAR:'+plan['point_uuid']+':'+plan['manifest_sha256'])
        return result,fake

    def test_complete_generated_chain_https_resume_encryption_export_restore_and_event_idempotency(self):
        original=self.client.request; disconnected=False; puts=[]
        def request(method,route,*args,**kwargs):
            nonlocal disconnected
            result=original(method,route,*args,**kwargs)
            if method=='PUT':
                puts.append((route,kwargs['headers']['X-Upload-Offset']))
                if not disconnected:
                    disconnected=True
                    raise self.transport.TransferError('CONNECTION')  # Real receipt committed, ACK lost.
            return result
        with patch.object(self.client,'request',side_effect=request):
            first=self.sender.run(self.entries)
            self.assertEqual(first['status'],'FALHA_REPETIR')
            self.assertEqual(HubBackupParte.objects.count(),1)
            resumed=self.sender.run(self.entries,resume=True)
        self.assertEqual(resumed['status'],'RECEBIDO_CENTRAL',resumed.get('failure_code'))
        self.assertEqual(len(puts),len(set(puts)))
        self.assertEqual(set(HubBackup.objects.values_list('estado_validacao',flat=True)),{'PENDENTE'})
        self.audit(); grant=self.grant()
        self.assertEqual([f['sequencia'] for f in grant['manifesto']['arquivos']],[0,1,2])
        ciphertext={b.pk:self.recovery.file_hash(destination(b)) for b in HubBackup.objects.all()}
        package=self.export(grant); plan,state=self.recovery_plan(grant,package)
        result,fake=self.restore(plan,state)
        self.assertEqual(result['status'],'RESTAURADO_BLOQUEADO'); self.assertFalse(result['pdv_liberado'])
        event=json.loads(re.search(rb'^-- EVENT=(.+)$',bytes(fake.captured),re.M)[1])
        self.assertEqual(event,self.event)
        processor=HubSyncProcessor(self.hub)
        with patch.object(processor,'_processar_registro',side_effect=AssertionError('confirmed event must not be applied again')):
            duplicate=processor.processar_evento(event)
        self.assertEqual(duplicate['status'],'DUPLICADO')
        self.assertEqual(HubEventoRecebido.objects.count(),1); self.assertEqual(HubVendaMapeamento.objects.count(),0)
        self.original_event.refresh_from_db(); self.assertEqual(self.original_event.payload_hash,payload_hash(self.event['payload']))
        self.assertEqual(ciphertext,{b.pk:self.recovery.file_hash(destination(b)) for b in HubBackup.objects.all()})
        self.assert_preserved()

    def test_internet_unavailable_keeps_previously_received_full_and_local_chain(self):
        received=self.sender.run(self.entries[:1])
        self.assertEqual(received['status'],'RECEBIDO_CENTRAL')
        full=HubBackup.objects.get(tipo='FULL'); checksum=self.recovery.file_hash(destination(full))
        with patch.object(self.transport.http.client.HTTPSConnection,'connect',side_effect=ConnectionRefusedError('fictitious internet outage')):
            state=self.sender.run(self.entries,resume=True)
        self.assertEqual(state['status'],'FALHA_REPETIR'); self.assertEqual(state['failure_code'],'CONNECTION')
        self.assertGreater(state['next_attempt'],state['last_attempt_utc'])
        self.assertEqual(checksum,self.recovery.file_hash(destination(full)))
        self.assertEqual(HubBackup.objects.count(),1); self.assert_preserved()

    def test_missing_full_or_incremental_in_upload_chain_is_rejected_before_http(self):
        identity=SimpleNamespace(hub_uuid=self.hub.hub_uuid,empresa_id=self.empresa.pk,loja_id=self.loja.pk,retaguarda_hub_id=self.hub.pk)
        for omitted in (0,1):
            plan=dict(self.plan,directories=[name for index,name in enumerate(self.plan['directories']) if index!=omitted])
            with self.assertRaises(self.transport.TransferError): self.transport.load_entries(plan,identity)
        self.assertEqual(HubBackup.objects.count(),0); self.assert_preserved()

    def test_wrong_archive_hash_blocks_incremental_and_preserves_received_full(self):
        self.sender.run(self.entries[:1])
        full=HubBackup.objects.get(tipo='FULL'); original=self.recovery.file_hash(destination(full))
        path=self.entries[1]['path']; preserved=path.with_name('original-fictitious-archive.preserved')
        preserved.write_bytes(path.read_bytes())
        corrupt=bytearray(path.read_bytes()); corrupt[-1]^=1; path.write_bytes(corrupt)
        state=self.sender.run(self.entries,resume=True)
        self.assertEqual(state['status'],'INTERVENCAO_SUPORTE'); self.assertEqual(state['failure_code'],'HTTP_422')
        self.assertEqual(HubBackup.objects.filter(estado_recebimento='RECEBIDO').count(),1)
        self.assertFalse(HubBackup.objects.filter(backup_uuid=self.entries[-1]['id']).exists())
        self.assertEqual(original,self.recovery.file_hash(destination(full)))
        self.assertEqual(self.recovery.file_hash(preserved),self.source_hashes[str(path)])

    def test_physical_chain_corruption_blocks_grant_and_preserves_full(self):
        self.send()
        full=HubBackup.objects.get(tipo='FULL'); first=HubBackup.objects.get(sequencia=1)
        full_hash=self.recovery.file_hash(destination(full)); path=destination(first)
        preserved=path.with_name('fictitious-encrypted-container.preserved'); preserved.write_bytes(path.read_bytes())
        with path.open('r+b') as stream:
            stream.seek(-1,2); byte=stream.read(1); stream.seek(-1,2); stream.write(bytes([byte[0]^1]))
        report=auditar_backups_hub(empresa_id=self.empresa.pk,loja_id=self.loja.pk,hub_id=self.hub.pk,registrar=True)
        by_sequence={HubBackup.objects.get(backup_uuid=item['backup_uuid']).sequencia:item for item in report['backups']}
        self.assertEqual(by_sequence[0]['integridade'],'VALIDO'); self.assertNotEqual(by_sequence[1]['integridade'],'VALIDO')
        self.assertEqual(by_sequence[2]['integridade'],'CADEIA_INTERROMPIDA')
        status,_,_=self.request('POST',f'/api/hub/backups/recuperacao/{self.empresa.pk}/{self.loja.pk}/autorizar/',
            dict(ponto_uuid=self.entries[-1]['id'],instalacao_uuid=str(self.hub.hub_uuid),servidor_mysql_uuid=self.source_uuid,autorizar=True,motivo='fictitious support'))
        self.assertEqual(status,409)
        self.assertEqual(full_hash,self.recovery.file_hash(destination(full))); self.assertTrue(preserved.exists()); self.assert_preserved()

    def test_received_is_not_validated_and_regular_user_cannot_authorize(self):
        self.send()
        path=f'/api/hub/backups/recuperacao/{self.empresa.pk}/{self.loja.pk}/autorizar/'
        payload=dict(ponto_uuid=self.entries[-1]['id'],instalacao_uuid=str(self.hub.hub_uuid),servidor_mysql_uuid=self.source_uuid,autorizar=True,motivo='fictitious support')
        self.assertEqual(self.request('POST',path,payload)[0],409)
        self.audit()
        self.support=get_user_model().objects.create_user(username='fictitious-not-support',empresa=self.empresa,type='Regular')
        self.assertEqual(self.request('POST',path,payload)[0],403); self.assert_preserved()

    def test_other_store_and_company_cannot_use_hub_or_administrative_grant(self):
        self.send(); self.audit(); grant=self.grant()
        self.client.token=self.hub_b.gerar_token()
        with self.assertRaises(self.transport.TransferError) as error:
            self.client.request('GET',str(self.recovery.UUID(self.entries[0]['id']))+'/progresso/')
        self.assertEqual(error.exception.code,'HTTP_404')
        get_user_model().objects.filter(pk=self.support.pk).update(empresa=self.empresa)
        self.support.refresh_from_db()
        path=f"/api/hub/backups/recuperacao/{self.empresa_b.pk}/{self.loja_b.pk}/arquivos/{grant['manifesto']['full_uuid']}/"
        self.assertEqual(self.request('GET',path,grant=grant)[0],403)
        self.assertFalse(HubBackup.objects.filter(hub=self.hub_b).exists()); self.assert_preserved()

    def test_incomplete_download_blocks_preflight_without_import(self):
        self.send(); self.audit(); grant=self.grant(); package=self.export(grant,limit=10)
        plan,_=self.recovery_plan(grant,package)
        with patch.object(self.recovery,'start_mysql') as mysql:
            with self.assertRaisesRegex(self.recovery.RecoveryError,'ARCHIVE_HASH_MISMATCH'): self.recovery.recover(plan)
        mysql.assert_not_called(); self.assertTrue((Path(plan['program_data'])/'config/recovery-block.json').exists())
        self.assert_preserved()

    def test_missing_full_or_incremental_export_blocks_restore_without_deleting_originals(self):
        self.send(); self.audit(); grant=self.grant(); package=self.export(grant)
        plan,_=self.recovery_plan(grant,package)
        for index in (0,1):
            original=package/grant['manifesto']['arquivos'][index]['nome']; preserved=original.with_name('temporarily-preserved-fixture.gz')
            original.rename(preserved)
            try:
                with patch.object(self.recovery,'start_mysql') as mysql:
                    with self.assertRaises(FileNotFoundError): self.recovery.recover(plan)
                mysql.assert_not_called(); self.assertTrue(preserved.exists())
            finally: preserved.rename(original)
        self.assert_preserved()

    def test_nonempty_mysql_destination_is_rejected_by_real_preflight_without_import(self):
        self.send(); self.audit(); grant=self.grant(); package=self.export(grant)
        plan,state=self.recovery_plan(grant,package)
        state.update(port=3307,version='8.0.46',account='sysvarhub_restore@127.0.0.1',log_bin=1,other_sessions=0,
            basedir=str(Path(plan['install_root'])/'mysql'),schemas=['mysql','information_schema','performance_schema','sys','sysvarhub_db'],objects=1)
        with patch.object(self.recovery,'query',return_value=json.dumps(state)),patch.object(self.recovery,'start_mysql') as mysql:
            with self.assertRaisesRegex(self.recovery.RecoveryError,'TARGET_NOT_EMPTY'): self.recovery.recover(plan)
        mysql.assert_not_called(); self.assert_preserved()

    def test_restore_interruption_keeps_services_blocked_and_does_not_feed_incrementals(self):
        self.send(); self.audit(); grant=self.grant(); package=self.export(grant); plan,state=self.recovery_plan(grant,package)
        fake=self.fake_mysql(fail=True)
        with patch.object(self.recovery,'verify_target',return_value=state),patch.object(self.recovery,'start_mysql',return_value=fake):
            with self.assertRaisesRegex(self.recovery.RecoveryError,'DESTINATION_BLOCKED'):
                self.recovery.recover(plan,execute=True,confirmation='RESTAURAR:'+plan['point_uuid']+':'+plan['manifest_sha256'])
        self.assertNotIn(b'INCREMENTAL_FICTITIOUS',fake.captured)
        diagnostic=json.loads((Path(plan['program_data'])/'config/recovery-state.json').read_text())
        self.assertEqual(diagnostic['status'],'FALHA_SUPORTE'); self.assertFalse(diagnostic['pdv_liberado'])
        from runtime.windows_service import bootstrap
        with patch.dict(os.environ,{'SYSVARHUB_PROGRAMDATA':plan['program_data']}),patch('runtime.windows_service.load_env_file') as env:
            with self.assertRaisesRegex(RuntimeError,'bloqueado'): bootstrap()
        env.assert_not_called(); self.assert_preserved()

    def test_confirmed_event_conflict_after_simulated_restore_does_not_change_history(self):
        self.send(); self.audit(); grant=self.grant(); package=self.export(grant); plan,state=self.recovery_plan(grant,package)
        result,fake=self.restore(plan,state)
        self.assertFalse(result['pdv_liberado'])
        event=json.loads(re.search(rb'^-- EVENT=(.+)$',bytes(fake.captured),re.M)[1])
        before=HubEventoRecebido.objects.values().get(pk=self.original_event.pk)
        processor=HubSyncProcessor(self.hub)
        with patch.object(processor,'_processar_registro',side_effect=AssertionError('must not apply again')):
            self.assertEqual(processor.processar_evento(event)['status'],'DUPLICADO')
            event['payload']['venda_uuid']=str(uuid4())
            self.assertEqual(processor.processar_evento(event)['status'],'CONFLITO')
        self.assertEqual(before,HubEventoRecebido.objects.values().get(pk=self.original_event.pk)); self.assert_preserved()

    def test_streaming_memory_with_generated_sizes_and_no_plaintext_sql_temporaries(self):
        measurements=[]
        for size in (1024,128*1024,2*1024*1024):
            if size!=1024: self.generate(size)
            state=self.sender.run(self.entries)
            self.assertEqual(state['status'],'RECEBIDO_CENTRAL',state.get('failure_code'))
            self.audit(); grant=self.grant()
            tracemalloc.start(); started=time.monotonic()
            try:
                package=self.export(grant); plan,target=self.recovery_plan(grant,package)
                fake=self.fake_mysql(); writer=fake.stdin.write; maximum=0; total=0
                def bounded(data):
                    nonlocal maximum,total
                    maximum=max(maximum,len(data)); total+=len(data)
                    writer(data); fake.captured.clear()  # Test observer counts writes instead of retaining the SQL.
                fake.stdin.write=bounded
                with patch.object(self.recovery,'verify_target',return_value=target),patch.object(self.recovery,'start_mysql',return_value=fake),patch.object(self.recovery,'logical_check',return_value={}):
                    result=self.recovery.recover(plan,execute=True,confirmation='RESTAURAR:'+plan['point_uuid']+':'+plan['manifest_sha256'])
                _,peak=tracemalloc.get_traced_memory()
            finally: tracemalloc.stop()
            self.assertEqual(result['status'],'RESTAURADO_BLOQUEADO'); self.assertLessEqual(maximum,65536)
            self.assertGreater(total,size); self.assertFalse(list(package.glob('*.sql')))
            self.assertLess(peak,16*1024**2)
            measurements.append(dict(fictitious_input_bytes=size,peak_python_bytes=peak,max_write=maximum,seconds=round(time.monotonic()-started,3)))
            self.assert_preserved()
        print('BACKUP_INTEGRATION_MEMORY '+json.dumps(measurements,sort_keys=True))

    def test_selected_earlier_point_excludes_later_incremental_from_package_and_replay(self):
        self.send(); self.audit(); grant=self.grant(point=self.entries[1]['id'])
        self.assertEqual([item['sequencia'] for item in grant['manifesto']['arquivos']],[0,1])
        package=self.export(grant); plan,state=self.recovery_plan(grant,package)
        result,fake=self.restore(plan,state)
        self.assertEqual(result['coordenada']['position'],333)
        self.assertIn(b'INCREMENTAL_FICTITIOUS_333',fake.captured)
        self.assertNotIn(b'INCREMENTAL_FICTITIOUS_555',fake.captured)
        self.assertFalse(result['pdv_liberado']); self.assert_preserved()
