from copy import deepcopy
from pathlib import Path
from tempfile import gettempdir
from unittest.mock import patch
from uuid import uuid4

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import IntegrityError, connection, models, transaction
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from cadastros.models import Empresa, Loja
from hub.backup_storage import caminho_backup_privado, raiz_backup_privada
from hub.backups import registrar_metadados_backup
from hub.authentication import HubTokenAuthentication
from rest_framework.test import APIRequestFactory
from hub.checks import check_hub_backup_storage
from hub.models import HubBackup, SysvarHub


class HubBackupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nome="Backup A", documento="11222333000181")
        cls.outra_empresa = Empresa.objects.create(nome="Backup B", documento="21222333000181")
        cls.loja = Loja.objects.create(empresa=cls.empresa, nome_loja="Loja A", apelido_loja="BA", cnpj="11222333000181", estado="SP")
        cls.outra_loja = Loja.objects.create(empresa=cls.empresa, nome_loja="Loja A2", apelido_loja="BA2", cnpj="31222333000181", estado="SP")
        cls.loja_b = Loja.objects.create(empresa=cls.outra_empresa, nome_loja="Loja B", apelido_loja="BB", cnpj="21222333000181", estado="SP")
        cls.hub = SysvarHub.objects.create(loja=cls.loja)
        cls.outro_hub = SysvarHub.objects.create(loja=cls.outra_loja)
        cls.hub_b = SysvarHub.objects.create(loja=cls.loja_b)
        cls.mysql_uuid = uuid4()

    def metadados_full(self, hub=None):
        hub = hub or self.hub
        return dict(
            backup_uuid=uuid4(), instalacao_uuid=hub.hub_uuid, servidor_mysql_uuid=self.mysql_uuid,
            tipo=HubBackup.FULL, gerado_em=timezone.now(), binlog_inicio_arquivo="sysvarhub-bin.000001",
            binlog_inicio_posicao=100, tamanho_bytes=1234, sha256="a" * 64,
            manifesto_sha256="b" * 64, formato=HubBackup.FORMATO_FULL,
        )

    def criar_full(self, hub=None):
        return registrar_metadados_backup(hub=hub or self.hub, **self.metadados_full(hub))[0]

    def metadados_incremental(self, full, predecessor=None):
        previous = predecessor or full
        start_file = previous.binlog_inicio_arquivo if previous.tipo == HubBackup.FULL else previous.binlog_fim_arquivo
        start_position = previous.binlog_inicio_posicao if previous.tipo == HubBackup.FULL else previous.binlog_fim_posicao
        return dict(
            backup_uuid=uuid4(), instalacao_uuid=full.instalacao_uuid, servidor_mysql_uuid=full.servidor_mysql_uuid,
            tipo=HubBackup.INCREMENTAL, full_referencia_id=full.pk, predecessor_id=previous.pk,
            predecessor_sha256=previous.manifesto_sha256, sequencia=previous.sequencia + 1,
            gerado_em=timezone.now(), binlog_inicio_arquivo=start_file, binlog_inicio_posicao=start_position,
            binlog_fim_arquivo=start_file, binlog_fim_posicao=start_position + 100, tamanho_bytes=234,
            sha256="c" * 64, manifesto_sha256="d" * 64, formato=HubBackup.FORMATO_INCREMENTAL,
        )

    def registrar_incremental(self, full, predecessor=None, **overrides):
        data = self.metadados_incremental(full, predecessor)
        data.update(overrides)
        return registrar_metadados_backup(hub=self.hub, **data)[0]

    def test_associacao_hub_loja_empresa_e_chave_privada(self):
        backup = self.criar_full()
        self.assertEqual((backup.hub_id, backup.loja_id, backup.empresa_id), (self.hub.pk, self.loja.pk, self.empresa.pk))
        self.assertEqual(backup.instalacao_uuid, self.hub.hub_uuid)
        self.assertTrue(backup.chave_armazenamento.startswith(f"empresa-{self.empresa.pk}/loja-{self.loja.pk}/hub-{self.hub.pk}/"))
        self.assertIn(f"full-{backup.backup_uuid.hex}/full-{backup.backup_uuid.hex}.sql.gz", backup.chave_armazenamento)
        self.assertIsNone(backup.binlog_fim_posicao)

    def test_usa_o_hub_resolvido_pela_autenticacao_existente(self):
        token = self.hub.gerar_token()
        request = APIRequestFactory().post("/", {}, HTTP_AUTHORIZATION=f"Hub {token}")
        HubTokenAuthentication().authenticate(request)
        backup, created = registrar_metadados_backup(hub=request.sysvar_hub, **self.metadados_full())
        self.assertTrue(created)
        self.assertEqual(backup.hub_id, self.hub.pk)

    def test_escopo_e_derivado_do_cadastro_e_nao_da_instancia_desatualizada(self):
        stale = SysvarHub(pk=self.hub.pk, loja=self.loja_b, hub_uuid=uuid4())
        backup, _ = registrar_metadados_backup(hub=stale, **self.metadados_full())
        self.assertEqual((backup.loja_id, backup.empresa_id), (self.loja.pk, self.empresa.pk))

    def test_escopo_separa_empresas_e_lojas(self):
        a, a2, b = self.criar_full(), self.criar_full(self.outro_hub), self.criar_full(self.hub_b)
        self.assertCountEqual(HubBackup.objects.da_empresa(self.empresa), [a, a2])
        self.assertCountEqual(HubBackup.objects.da_empresa(self.outra_empresa), [b])
        self.assertCountEqual(HubBackup.objects.do_hub(self.hub), [a])

    def test_nao_confia_em_empresa_loja_caminho_ou_estado_do_payload(self):
        for extra in (dict(empresa_id=self.outra_empresa.pk), dict(loja_id=self.outra_loja.pk), dict(chave_armazenamento="../../publico"), dict(estado_validacao="VALIDO")):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                registrar_metadados_backup(hub=self.hub, **self.metadados_full(), **extra)
        self.assertEqual(HubBackup.objects.count(), 0)

    def test_model_rejeita_empresa_ou_loja_inconsistente(self):
        for company, store in ((self.outra_empresa, self.loja), (self.empresa, self.outra_loja)):
            with self.subTest(company=company.pk, store=store.pk), self.assertRaises(ValidationError):
                HubBackup.objects.create(hub=self.hub, empresa=company, loja=store, **self.metadados_full())

    def test_instalacao_divergente_e_uuid_nulo_rejeitados(self):
        for field, value in (("instalacao_uuid", uuid4()), ("servidor_mysql_uuid", "00000000-0000-0000-0000-000000000000"), ("backup_uuid", "invalido")):
            data = self.metadados_full(); data[field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                registrar_metadados_backup(hub=self.hub, **data)

    def test_identificador_unico_e_retry_idempotente(self):
        data = self.metadados_full()
        first, created = registrar_metadados_backup(hub=self.hub, **data)
        again, repeated = registrar_metadados_backup(hub=self.hub, **data)
        self.assertTrue(created)
        self.assertFalse(repeated)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(HubBackup.objects.count(), 1)

    def test_retry_normaliza_uuid_data_e_numeros_sem_duplicar(self):
        data = self.metadados_full(); first, _ = registrar_metadados_backup(hub=self.hub, **data)
        repeated = dict(data)
        for name in ("backup_uuid", "instalacao_uuid", "servidor_mysql_uuid"):
            repeated[name] = str(repeated[name])
        repeated["gerado_em"] = repeated["gerado_em"].isoformat()
        repeated["tamanho_bytes"] = str(repeated["tamanho_bytes"])
        again, created = registrar_metadados_backup(hub=self.hub, **repeated)
        self.assertFalse(created)
        self.assertEqual(again.pk, first.pk)

    def test_retry_com_hash_ou_metadado_divergente_rejeitado(self):
        data = self.metadados_full(); first, _ = registrar_metadados_backup(hub=self.hub, **data)
        for field, value in (("manifesto_sha256", "e" * 64), ("sha256", "f" * 64), ("tamanho_bytes", 999)):
            changed = dict(data); changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                registrar_metadados_backup(hub=self.hub, **changed)
        first.refresh_from_db()
        self.assertEqual(first.tamanho_bytes, 1234)

    def test_identificador_nao_pode_ser_reutilizado_por_outro_hub(self):
        data = self.metadados_full(); registrar_metadados_backup(hub=self.hub, **data)
        other = self.metadados_full(self.hub_b); other["backup_uuid"] = data["backup_uuid"]
        with self.assertRaises(ValidationError):
            registrar_metadados_backup(hub=self.hub_b, **other)
        self.assertEqual(HubBackup.objects.count(), 1)

    def test_unicidade_tambem_existe_no_banco(self):
        first = self.criar_full()
        duplicate = deepcopy(first); duplicate.pk = None; duplicate._state.adding = True
        duplicate.chave_armazenamento += ".outro"
        with self.assertRaises(IntegrityError), transaction.atomic():
            models.QuerySet.bulk_create(HubBackup.objects.all(), [duplicate])

    def test_full_e_incrementais_sucessivos(self):
        full = self.criar_full(); first = self.registrar_incremental(full); second = self.registrar_incremental(full, first)
        self.assertEqual(first.full_referencia_id, full.pk)
        self.assertEqual(second.predecessor_id, first.pk)
        self.assertEqual(second.sequencia, 2)
        self.assertEqual(second.binlog_inicio_posicao, first.binlog_fim_posicao)
        self.assertEqual(second.predecessor_sha256, first.manifesto_sha256)

    def test_retry_incremental_nao_duplica_sequencia(self):
        full = self.criar_full(); data = self.metadados_incremental(full)
        first, _ = registrar_metadados_backup(hub=self.hub, **data)
        again, created = registrar_metadados_backup(hub=self.hub, **data)
        self.assertFalse(created)
        self.assertEqual(first.pk, again.pk)
        with self.assertRaises(ValidationError):
            self.registrar_incremental(full)
        self.assertEqual(full.incrementais.count(), 1)

    def test_rejeita_salto_de_sequencia_predecessor_e_hash_divergente(self):
        full = self.criar_full()
        for changes in (dict(sequencia=2), dict(predecessor_sha256="e" * 64), dict(predecessor_id=999999), dict(full_referencia_id=999999)):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.registrar_incremental(full, **changes)
        self.assertEqual(HubBackup.objects.count(), 1)

    def test_rejeita_outro_full_como_predecessor(self):
        full = self.criar_full(); other = self.criar_full()
        with self.assertRaises(ValidationError):
            self.registrar_incremental(full, predecessor_id=other.pk)

    def test_rejeita_referencia_full_que_e_incremental(self):
        full = self.criar_full(); first = self.registrar_incremental(full)
        with self.assertRaises(ValidationError):
            self.registrar_incremental(full, first, full_referencia_id=first.pk)

    def test_rejeita_mistura_de_hub_loja_empresa_mysql_ou_instalacao(self):
        full = self.criar_full()
        for other_hub in (self.outro_hub, self.hub_b):
            data = self.metadados_incremental(full); data["instalacao_uuid"] = other_hub.hub_uuid
            with self.subTest(hub=other_hub.pk), self.assertRaises(ValidationError):
                registrar_metadados_backup(hub=other_hub, **data)
        with self.assertRaises(ValidationError):
            self.registrar_incremental(full, servidor_mysql_uuid=uuid4())
        self.hub.hub_uuid = uuid4(); self.hub.save(update_fields=["hub_uuid"])
        with self.assertRaises(ValidationError):
            self.registrar_incremental(full, instalacao_uuid=self.hub.hub_uuid)

    def test_rejeita_lacuna_sobreposicao_e_intervalo_regressivo(self):
        full = self.criar_full()
        for changes in (dict(binlog_inicio_posicao=99), dict(binlog_inicio_posicao=101), dict(binlog_fim_posicao=100), dict(binlog_fim_arquivo="outro-bin.000002"), dict(binlog_fim_posicao=None)):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.registrar_incremental(full, **changes)

    def test_rotacao_preserva_coordenada_final_para_proximo_incremental(self):
        full = self.criar_full()
        first = self.registrar_incremental(full, binlog_fim_arquivo="sysvarhub-bin.000002", binlog_fim_posicao=120)
        second = self.registrar_incremental(full, first)
        self.assertEqual((second.binlog_inicio_arquivo, second.binlog_inicio_posicao), ("sysvarhub-bin.000002", 120))

    def test_full_nao_admite_vinculo_incremental_ou_fim_inventado(self):
        for changes in (dict(sequencia=1), dict(binlog_fim_arquivo="sysvarhub-bin.000002", binlog_fim_posicao=100), dict(formato=HubBackup.FORMATO_INCREMENTAL)):
            data = self.metadados_full(); data.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                registrar_metadados_backup(hub=self.hub, **data)

    def test_tamanho_hash_versao_formato_e_binlog_invalidos(self):
        for field, value in (("tamanho_bytes", 0), ("sha256", "invalido"), ("manifesto_versao", 2), ("binlog_inicio_arquivo", "../arquivo"), ("binlog_inicio_posicao", 3), ("compressao", "zip")):
            data = self.metadados_full(); data[field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                registrar_metadados_backup(hub=self.hub, **data)

    def test_estados_distinguem_arquivo_recebido_de_validado(self):
        backup = self.criar_full()
        self.assertEqual((backup.estado_recebimento, backup.estado_validacao), ("PENDENTE", "PENDENTE"))
        backup.estado_recebimento = "RECEBIDO"
        with self.assertRaises(ValidationError):
            backup.save()
        backup.recebido_em = timezone.now(); backup.save()
        self.assertEqual(backup.estado_validacao, "PENDENTE")
        backup.estado_validacao = "VALIDO"
        with self.assertRaises(ValidationError):
            backup.save()
        backup.verificado_em = timezone.now(); backup.save()
        self.assertEqual(HubBackup.objects.get(pk=backup.pk).estado_validacao, "VALIDO")

    def test_incremental_nao_valida_antes_dos_ancestrais(self):
        full = self.criar_full(); inc = self.registrar_incremental(full)
        inc.estado_recebimento = "RECEBIDO"; inc.recebido_em = timezone.now(); inc.estado_validacao = "VALIDO"; inc.verificado_em = timezone.now()
        with self.assertRaises(ValidationError):
            inc.save()
        full.estado_recebimento = "RECEBIDO"; full.recebido_em = timezone.now(); full.estado_validacao = "VALIDO"; full.verificado_em = timezone.now(); full.save()
        inc.save()
        self.assertEqual(inc.estado_validacao, "VALIDO")

    def test_retry_preserva_estados_existentes(self):
        data = self.metadados_full(); backup, _ = registrar_metadados_backup(hub=self.hub, **data)
        backup.estado_recebimento = "RECEBIDO"; backup.recebido_em = timezone.now(); backup.save()
        repeated, created = registrar_metadados_backup(hub=self.hub, **data)
        self.assertFalse(created)
        self.assertEqual(repeated.estado_recebimento, "RECEBIDO")
        self.assertEqual(repeated.recebido_em, backup.recebido_em)

    def test_cadeia_interrompida_pode_ser_identificada_sem_apagar_historico(self):
        full = self.criar_full(); inc = self.registrar_incremental(full)
        inc.estado_validacao = HubBackup.CADEIA_INTERROMPIDA; inc.save()
        self.assertTrue(HubBackup.objects.filter(full_referencia=full, estado_validacao=HubBackup.CADEIA_INTERROMPIDA).exists())
        self.assertEqual(HubBackup.objects.count(), 2)

    def test_metadados_e_hub_sao_imutaveis_mesmo_sem_sucessor(self):
        for field, value in (("hub_id", self.outro_hub.pk), ("tamanho_bytes", 999), ("chave_armazenamento", "../publico")):
            backup = self.criar_full(); setattr(backup, field, value)
            with self.subTest(field=field), self.assertRaises(ValidationError):
                backup.save()

    def test_lotes_nao_contornam_validacao(self):
        full = self.criar_full()
        for operation in (lambda: HubBackup.objects.filter(pk=full.pk).update(hub=self.hub_b), lambda: HubBackup.objects.bulk_create([full]), lambda: HubBackup.objects.bulk_update([full], ["sha256"])):
            with self.assertRaises(ValidationError):
                operation()

    def test_historico_continua_salvavel_apos_reinstalacao(self):
        full = self.criar_full(); old_uuid = full.instalacao_uuid
        self.hub.hub_uuid = uuid4(); self.hub.save(update_fields=["hub_uuid"])
        full.estado_validacao = HubBackup.CADEIA_INTERROMPIDA; full.save()
        full.refresh_from_db(); self.assertEqual(full.instalacao_uuid, old_uuid)

    def test_hub_desativado_nao_registra_novos_metadados(self):
        self.hub.ativo = False; self.hub.save(update_fields=["ativo"])
        with self.assertRaises(ValidationError):
            registrar_metadados_backup(hub=self.hub, **self.metadados_full())

    def test_caminho_fisico_derivado_sem_arquivo_ou_diretorio(self):
        backup = self.criar_full()
        root = Path(gettempdir()) / ("sysvar-private-test-" + uuid4().hex)
        with override_settings(HUB_BACKUP_ROOT=root, HUB_BACKUP_PUBLIC_ROOTS=[]):
            path = caminho_backup_privado(backup)
            self.assertTrue(path.is_relative_to(root.resolve()))
            self.assertFalse(root.exists())
            backup.chave_armazenamento = "../arquivo-externo"
            with self.assertRaises(ValidationError):
                caminho_backup_privado(backup)

    def test_cadeia_interrompida_em_ancestral_impede_validar_novo_incremental(self):
        full = self.criar_full(); one = self.registrar_incremental(full); two = self.registrar_incremental(full, one)
        for item in (full, one, two):
            item.estado_recebimento = HubBackup.RECEBIDO; item.recebido_em = timezone.now()
            item.estado_validacao = HubBackup.VALIDO; item.verificado_em = timezone.now(); item.save()
        one.estado_validacao = HubBackup.CADEIA_INTERROMPIDA; one.save()
        self.assertEqual(two.situacao_cadeia(), HubBackup.CADEIA_INTERROMPIDA)
        three = self.registrar_incremental(full, two)
        three.estado_recebimento = HubBackup.RECEBIDO; three.recebido_em = timezone.now()
        three.estado_validacao = HubBackup.VALIDO; three.verificado_em = timezone.now()
        with self.assertRaises(ValidationError):
            three.save()

    def test_identificador_ausente_e_sequencia_nula_retornam_erro_funcional(self):
        data = self.metadados_full(); data["backup_uuid"] = None
        with self.assertRaises(ValidationError):
            registrar_metadados_backup(hub=self.hub, **data)
        with self.assertRaises(ValidationError):
            self.registrar_incremental(self.criar_full(), sequencia=None)

    def test_model_nao_armazena_conteudo_nem_storage_publico(self):
        self.assertFalse(any(isinstance(field, (models.FileField, models.BinaryField, models.JSONField)) for field in HubBackup._meta.fields))


class HubBackupStorageTests(SimpleTestCase):
    def fixture_root(self):
        # Apenas nomes ficticios: nao cria nem precisa remover diretorios Windows.
        return Path(gettempdir()) / ("sysvar-test-" + uuid4().hex)

    def test_configuracao_privada_sem_criar_diretorios(self):
        base = self.fixture_root(); root = base / "private" / "hub-backups"
        with override_settings(HUB_BACKUP_ROOT=root, BASE_DIR=base / "app", MEDIA_ROOT=base / "media", STATIC_ROOT=base / "static", STATICFILES_DIRS=[], HUB_BACKUP_PUBLIC_ROOTS=[]), patch.object(Path, "mkdir", side_effect=AssertionError("mkdir proibido")):
            self.assertEqual(raiz_backup_privada(), root.resolve())
            self.assertEqual(check_hub_backup_storage(None), [])
            self.assertFalse(root.exists())

    def test_bloqueia_media_static_projeto_nginx_e_seus_ancestrais(self):
        base = self.fixture_root()
        public = [base / "app", base / "media", base / "static", base / "nginx"]
        for root in [*public, *(path / "backups" for path in public), base]:
            with self.subTest(root=root), override_settings(HUB_BACKUP_ROOT=root, BASE_DIR=public[0], MEDIA_ROOT=public[1], STATIC_ROOT=public[2], STATICFILES_DIRS=[], HUB_BACKUP_PUBLIC_ROOTS=[public[3]]):
                with self.assertRaises(ImproperlyConfigured):
                    raiz_backup_privada()
                self.assertEqual(check_hub_backup_storage(None)[0].id, "hub.E001")

    def test_bloqueia_diretorio_static_adicional(self):
        base = self.fixture_root(); extra = base / "assets"
        with override_settings(HUB_BACKUP_ROOT=extra / "backups", STATICFILES_DIRS=[extra]):
            with self.assertRaises(ImproperlyConfigured):
                raiz_backup_privada()

    @override_settings(HUB_BACKUP_ROOT="pasta-relativa")
    def test_rejeita_diretorio_relativo(self):
        with self.assertRaises(ImproperlyConfigured):
            raiz_backup_privada()


class HubBackupMigrationTests(TransactionTestCase):
    def test_migration_nova_reversivel_em_banco_isolado(self):
        # O bootstrap dos testes SQLite usa modelos atuais porque a migration fiscal
        # legada 0016 tem SQL exclusivo MySQL. Aplicar/reverter a NOVA migration aqui.
        loader = MigrationLoader(connection)
        targets = [node for node in loader.graph.leaf_nodes() if node[0] != "hub"]
        targets.append(("hub", "0011_hubdevolucaomapeamento_conciliacao"))
        before = loader.project_state(targets)
        migration = loader.disk_migrations[("hub", "0012_hub_backup_control")]
        before13 = loader.project_state([node for node in targets if node[0] != "hub"] + [("hub", "0012_hub_backup_control")])
        migration13 = loader.disk_migrations[("hub", "0013_backup_recebimento_seguro")]
        with connection.schema_editor() as editor:
            migration13.unapply(before13, editor)
        self.assertTrue(all(operation.__class__.__name__ in {"CreateModel", "AddConstraint"} for operation in migration.operations))
        with connection.schema_editor() as editor:
            migration.unapply(before, editor)
        self.assertNotIn(HubBackup._meta.db_table, connection.introspection.table_names())
        with connection.schema_editor() as editor:
            migration.apply(before, editor)
        with connection.schema_editor() as editor:
            migration13.apply(before13, editor)
        self.assertIn(HubBackup._meta.db_table, connection.introspection.table_names())
        self.assertEqual(HubBackup.objects.count(), 0)

    def test_migration_0013_preserva_chaves_e_bloqueia_truncamento(self):
        from importlib import import_module
        from hub.backups import registrar_metadados_backup
        empresa = Empresa.objects.create(nome="Migration backup", documento="91222333000181")
        loja = Loja.objects.create(empresa=empresa, nome_loja="Migration", apelido_loja="M", cnpj="91222333000181", estado="SP")
        hub = SysvarHub.objects.create(loja=loja)
        backup, _ = registrar_metadados_backup(hub=hub, backup_uuid=uuid4(), instalacao_uuid=hub.hub_uuid,
            servidor_mysql_uuid=uuid4(), tipo="FULL", gerado_em=timezone.now(), binlog_inicio_arquivo="bin.000001",
            binlog_inicio_posicao=100, tamanho_bytes=100, sha256="a"*64, manifesto_sha256="b"*64, formato=HubBackup.FORMATO_FULL)
        loader = MigrationLoader(connection)
        targets = [node for node in loader.graph.leaf_nodes() if node[0] != "hub"] + [("hub", "0012_hub_backup_control")]
        before = loader.project_state(targets)
        migration = loader.disk_migrations[("hub", "0013_backup_recebimento_seguro")]
        with connection.schema_editor() as editor:
            migration.unapply(before, editor)
        HistoricalBackup = before.apps.get_model("hub", "HubBackup")
        HistoricalBackup.objects.filter(pk=backup.pk).update(chave_armazenamento="x"*300)
        module = import_module("hub.migrations.0013_backup_recebimento_seguro")
        with connection.schema_editor() as editor:
            with self.assertRaises(RuntimeError):
                module.validar_tamanho_chaves(before.apps, editor)
        self.assertEqual(HistoricalBackup.objects.get(pk=backup.pk).chave_armazenamento, "x"*300)
        HistoricalBackup.objects.filter(pk=backup.pk).update(chave_armazenamento="x"*249)
        with connection.schema_editor() as editor:
            migration.apply(before, editor)
        backup.refresh_from_db()
        self.assertEqual(backup.chave_armazenamento, "x"*249)
