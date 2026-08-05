from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.db import transaction
from cadastros.models import Empresa, EmpresaContrato, EmpresaModulo, Loja, ModuloSistema
from accounts.services.effective_access import EffectiveAccessService, LicenseService, increment_permissions_version
from auditoria.models import AuditAction, AuditCategory
from auditoria.services import AuditService
from .models import PerfilAcesso, PerfilModuloPermissao, SessaoUsuario, UserModulePermission, UserFieldPermission

User = get_user_model()

TIPOS_EXIGEM_LOJA = {
    "Vendedor",
    "Caixa",
    "Gerente",
    "Assistente",
    "AssistenteReceber",
    "AssistentePagar",
}

class LojaMiniSerializer(serializers.ModelSerializer):
    Idloja = serializers.IntegerField(source="id", read_only=True)
    empresa = serializers.IntegerField(source="empresa_id", read_only=True)

    class Meta:
        model = Loja
        fields = ("Idloja", "empresa", "nome_loja", "apelido_loja")


class EmpresaMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Empresa
        fields = (
            "id", "nome", "nome_fantasia",
            "licenca_master", "usa_vendas", "usa_compras", "usa_estoque", "usa_financeiro",
            "usa_fiscal", "usa_producao", "usa_ficha_tecnica", "usa_faccao", "usa_distribuicao_producao",
            "plano_completo",
        )


class UserModulePermissionSerializer(serializers.ModelSerializer):
    acesso = serializers.ChoiceField(choices=[("HERDAR", "Herdar"), *UserModulePermission.Access.choices])

    class Meta:
        model = UserModulePermission
        fields = ("modulo", "acesso")


class UserFieldPermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserFieldPermission
        fields = ("campo", "pode_ver")


class PerfilMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = PerfilAcesso
        fields = ("id", "nome", "descricao", "ativo", "padrao")


class UserSerializer(serializers.ModelSerializer):
    empresa = EmpresaMiniSerializer(read_only=True)
    Idempresa = serializers.PrimaryKeyRelatedField(
        source="empresa", queryset=Empresa.objects.all(), allow_null=True, required=False
    )
    # leitura amigável da loja
    loja = LojaMiniSerializer(read_only=True)
    # gravação por PK
    Idloja = serializers.PrimaryKeyRelatedField(
        source="loja", queryset=Loja.objects.all(), allow_null=True, required=False
    )
    lojas = LojaMiniSerializer(many=True, read_only=True)
    Idlojas = serializers.PrimaryKeyRelatedField(
        source="lojas", queryset=Loja.objects.all(), many=True, required=False
    )
    # permitir criar/alterar senha via API (write-only)
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    permissoes_modulos = UserModulePermissionSerializer(source="module_permissions", many=True, required=False)
    permissoes_campos = UserFieldPermissionSerializer(source="field_permissions", many=True, required=False)
    perfil_principal = PerfilMiniSerializer(read_only=True)
    perfil_principal_id = serializers.PrimaryKeyRelatedField(
        source="perfil_principal", queryset=PerfilAcesso.objects.filter(ativo=True), allow_null=True, required=False
    )
    permissoes_efetivas_detalhadas = serializers.SerializerMethodField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated or user.is_superuser:
            return
        empresa_id = getattr(user, "empresa_id", None)
        if empresa_id:
            self.fields["Idempresa"].queryset = Empresa.objects.filter(pk=empresa_id)
            self.fields["Idloja"].queryset = Loja.objects.filter(empresa_id=empresa_id)
            self.fields["Idlojas"].queryset = Loja.objects.filter(empresa_id=empresa_id)
            self.fields["perfil_principal_id"].queryset = PerfilAcesso.objects.filter(empresa_id=empresa_id, ativo=True)
        else:
            self.fields["Idempresa"].queryset = Empresa.objects.none()
            self.fields["Idloja"].queryset = Loja.objects.none()
            self.fields["Idlojas"].queryset = Loja.objects.none()
            self.fields["perfil_principal_id"].queryset = PerfilAcesso.objects.none()

    class Meta:
        model = User
        fields = (
            "id", "username", "email", "first_name", "last_name",
            "type", "Idempresa", "empresa", "Idloja", "loja", "Idlojas", "lojas",
            "perfil_principal", "perfil_principal_id",
            "permissoes_modulos", "permissoes_campos",
            "permissoes_efetivas_detalhadas",
            "is_active", "is_staff", "is_superuser", "deve_trocar_senha", "date_joined",
            "password",
        )
        read_only_fields = ("id", "is_staff", "is_superuser", "deve_trocar_senha", "date_joined")

    def validate(self, attrs):
        request = self.context.get("request")
        request_user = getattr(request, "user", None)
        forbidden = {"is_staff", "is_superuser", "groups", "user_permissions", "token", "session_id", "session_token", "deve_trocar_senha"}
        sent_forbidden = sorted(forbidden & set(getattr(self, "initial_data", {}) or {}))
        if sent_forbidden and request_user and request_user.is_authenticated and not request_user.is_superuser:
            raise serializers.ValidationError({field: "Campo protegido." for field in sent_forbidden})
        tipo = attrs.get("type", getattr(self.instance, "type", User.Type.REGULAR))
        empresa = attrs.get("empresa", getattr(self.instance, "empresa", None))
        loja = attrs.get("loja", getattr(self.instance, "loja", None))
        lojas = attrs.get("lojas", None)
        perfil = attrs.get("perfil_principal", getattr(self.instance, "perfil_principal", None))
        if request_user and request_user.is_authenticated and not request_user.is_superuser:
            user_empresa = getattr(request_user, "empresa", None)
            if not user_empresa:
                raise serializers.ValidationError({
                    "Idempresa": "Seu usuário precisa estar vinculado a uma empresa para cadastrar usuários."
                })
            if empresa and empresa.id != user_empresa.id:
                raise serializers.ValidationError({
                    "Idempresa": "Você só pode cadastrar usuários na empresa vinculada ao seu usuário."
                })
            attrs["empresa"] = user_empresa
            empresa = user_empresa
            if "is_staff" in self.initial_data or "is_superuser" in self.initial_data:
                raise serializers.ValidationError("Usuário cliente não pode alterar campos internos.")
        if not empresa and not getattr(self.instance, "is_superuser", False):
            raise serializers.ValidationError({
                "Idempresa": "Vincule este usuário a uma empresa."
            })
        if tipo in TIPOS_EXIGEM_LOJA and not loja:
            raise serializers.ValidationError({
                "Idloja": "Vincule este usuário a uma filial ou matriz."
            })
        if loja and empresa and loja.empresa_id and loja.empresa_id != empresa.id:
            raise serializers.ValidationError({
                "Idloja": "A loja selecionada pertence a outra empresa."
            })
        if loja and not empresa:
            attrs["empresa"] = loja.empresa
            empresa = loja.empresa
        if lojas is not None:
            if loja and loja not in lojas:
                lojas = list(lojas) + [loja]
                attrs["lojas"] = lojas
            if empresa:
                lojas_fora = [l.nome_loja for l in lojas if l.empresa_id and l.empresa_id != empresa.id]
                if lojas_fora:
                    raise serializers.ValidationError({
                        "Idlojas": "Todas as lojas permitidas devem pertencer à empresa do usuário."
                    })
        if perfil:
            if not empresa or perfil.empresa_id != empresa.id:
                raise serializers.ValidationError({"perfil_principal_id": "Perfil pertence a outra empresa."})
            if not perfil.ativo:
                raise serializers.ValidationError({"perfil_principal_id": "Perfil inativo não pode ser atribuído."})
        if self.instance and EffectiveAccessService(self.instance).is_company_master() and request_user and not request_user.is_superuser:
            protected = {"empresa", "perfil_principal", "is_active", "module_permissions"}
            if any(key in attrs for key in protected) or "is_active" in self.initial_data:
                raise serializers.ValidationError("Transfira o master antes de alterar empresa, perfil, permissões ou status deste usuário.")
        if not getattr(self.instance, "is_superuser", False) and tipo != User.Type.ADMIN:
            if request_user and request_user.is_authenticated and not EffectiveAccessService(request_user).is_company_master() and perfil is None:
                raise serializers.ValidationError({"perfil_principal_id": "Usuário comum deve possuir perfil principal."})
        return attrs

    def get_permissoes_efetivas_detalhadas(self, obj):
        if not obj.empresa_id:
            return []
        available = sorted(EffectiveAccessService(obj).available_modules())
        profile_perms = {}
        if obj.perfil_principal_id:
            profile_perms = {
                p.modulo.chave: p.acesso
                for p in obj.perfil_principal.permissoes_modulos.select_related("modulo").all()
            }
        overrides = {p.modulo: p.acesso for p in obj.module_permissions.all()}
        effective = EffectiveAccessService(obj)
        return [
            {
                "modulo": key,
                "perfil": profile_perms.get(key, UserModulePermission.Access.NONE),
                "override": overrides.get(key),
                "efetivo": effective.module_access(key),
            }
            for key in available
        ]

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        if not password:
            raise serializers.ValidationError({"password": "Senha inicial é obrigatória."})
        lojas = validated_data.pop("lojas", [])
        permissoes_modulos = validated_data.pop("module_permissions", [])
        permissoes_campos = validated_data.pop("field_permissions", [])
        with transaction.atomic():
            user = User(**validated_data)
            user.set_password(password)
            user.save()
            if lojas:
                user.lojas.set(lojas)
            elif user.loja_id:
                user.lojas.set([user.loja])
            self._salvar_permissoes(user, permissoes_modulos, permissoes_campos)
            if user.empresa_id:
                increment_permissions_version(user.empresa)
            request = self.context.get("request")
            transaction.on_commit(lambda: AuditService.success(AuditAction.USER_CREATED, category=AuditCategory.USER_MANAGEMENT, request=request, user=getattr(request, "user", None), instance=user, after={"username": user.username, "is_active": user.is_active, "type": user.type}))
        return user

    def update(self, instance, validated_data):
        if instance.empresa_id:
            service = EffectiveAccessService(self.context.get("request").user) if self.context.get("request") else None
            if service and service.is_company_master() and instance.id == self.context["request"].user.id and "perfil_principal" in validated_data:
                raise serializers.ValidationError("Usuário não pode alterar seu próprio perfil.")
        password = validated_data.pop("password", None)
        lojas = validated_data.pop("lojas", None)
        permissoes_modulos = validated_data.pop("module_permissions", None)
        permissoes_campos = validated_data.pop("field_permissions", None)
        was_active = instance.is_active
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        with transaction.atomic():
            if password:
                instance.set_password(password)
            instance.save()
            if lojas is not None:
                instance.lojas.set(lojas)
            elif instance.loja_id and not instance.lojas.filter(pk=instance.loja_id).exists():
                instance.lojas.add(instance.loja)
            self._salvar_permissoes(instance, permissoes_modulos, permissoes_campos)
            if instance.empresa_id:
                increment_permissions_version(instance.empresa)
            request = self.context.get("request")
            transaction.on_commit(lambda: AuditService.success(AuditAction.USER_UPDATED, category=AuditCategory.USER_MANAGEMENT, request=request, user=getattr(request, "user", None), instance=instance, metadata={"campos": list(validated_data.keys())}))
        return instance

    def _salvar_permissoes(self, user, permissoes_modulos, permissoes_campos):
        available = EffectiveAccessService(user).available_modules() if user.empresa_id else set()
        if permissoes_modulos is not None:
            recebidos = {item["modulo"]: item.get("acesso") or UserModulePermission.Access.NONE for item in permissoes_modulos if item.get("acesso") != "HERDAR"}
            if not user.is_superuser:
                recebidos = {modulo: acesso for modulo, acesso in recebidos.items() if modulo in available}
            UserModulePermission.objects.filter(user=user).exclude(modulo__in=recebidos.keys()).delete()
            for modulo, acesso in recebidos.items():
                UserModulePermission.objects.update_or_create(
                    user=user,
                    modulo=modulo,
                    defaults={"acesso": acesso},
                )
            request = self.context.get("request")
            # Obrigatório: override de módulo muda a autorização efetiva do usuário.
            AuditService.required_success(AuditAction.PERMISSION_UPDATED, category=AuditCategory.ACCESS, request=request, user=getattr(request, "user", None), instance=user, metadata={"tipo": "user_override", "permissoes": recebidos})
        if permissoes_campos is not None:
            recebidos = {item["campo"]: bool(item.get("pode_ver")) for item in permissoes_campos}
            UserFieldPermission.objects.filter(user=user).exclude(campo__in=recebidos.keys()).delete()
            for campo, pode_ver in recebidos.items():
                UserFieldPermission.objects.update_or_create(
                    user=user,
                    campo=campo,
                    defaults={"pode_ver": pode_ver},
                )


class ModuloSistemaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModuloSistema
        fields = ("id", "chave", "nome", "descricao", "categoria", "basico", "ativo", "ordem", "dependencias")
        read_only_fields = fields


class EmpresaContratoSerializer(serializers.ModelSerializer):
    usuarios_ativos = serializers.IntegerField(read_only=True)
    licencas_disponiveis = serializers.IntegerField(read_only=True)
    excedido = serializers.BooleanField(read_only=True)
    sessoes_ativas = serializers.IntegerField(read_only=True)
    sessoes_disponiveis = serializers.IntegerField(read_only=True)
    limite_excedido = serializers.BooleanField(read_only=True)

    class Meta:
        model = EmpresaContrato
        fields = (
            "id", "empresa", "status", "data_inicio", "data_fim", "limite_usuarios",
            "limite_sessoes_simultaneas", "sessoes_ativas", "sessoes_disponiveis", "limite_excedido",
            "plano_completo", "usuario_master", "observacoes", "permissions_version",
            "motivo_suspensao", "observacao_suspensao", "suspenso_em", "suspenso_por",
            "reativado_em", "reativado_por",
            "usuarios_ativos", "licencas_disponiveis", "excedido", "created_at", "updated_at",
        )
        read_only_fields = ("permissions_version", "motivo_suspensao", "observacao_suspensao", "suspenso_em", "suspenso_por", "reativado_em", "reativado_por", "created_at", "updated_at")

    def validate(self, attrs):
        data_inicio = attrs.get("data_inicio", getattr(self.instance, "data_inicio", None))
        data_fim = attrs.get("data_fim", getattr(self.instance, "data_fim", None))
        if data_inicio and data_fim and data_fim < data_inicio:
            raise serializers.ValidationError({"data_fim": "A data final não pode ser anterior à inicial."})
        if attrs.get("status", getattr(self.instance, "status", None)) == EmpresaContrato.STATUS_ATIVO and int(attrs.get("limite_sessoes_simultaneas", getattr(self.instance, "limite_sessoes_simultaneas", 0)) or 0) < 1:
            raise serializers.ValidationError({"limite_sessoes_simultaneas": "Contrato ativo exige pelo menos uma sessão simultânea."})
        return attrs


class EmpresaModuloSerializer(serializers.ModelSerializer):
    modulo_chave = serializers.CharField(source="modulo.chave", read_only=True)
    modulo_nome = serializers.CharField(source="modulo.nome", read_only=True)

    class Meta:
        model = EmpresaModulo
        fields = ("id", "empresa", "modulo", "modulo_chave", "modulo_nome", "contratado", "data_inicio", "data_fim", "created_at", "updated_at")
        read_only_fields = ("created_at", "updated_at")


class UsuarioMasterMiniSerializer(serializers.ModelSerializer):
    nome = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "username", "nome", "email", "is_active")
        read_only_fields = fields

    def get_nome(self, obj):
        return (obj.get_full_name() or obj.username).strip()


class EmpresaContratoDetalheSerializer(EmpresaContratoSerializer):
    empresa_id = serializers.IntegerField(read_only=True)
    usuario_master = UsuarioMasterMiniSerializer(read_only=True)
    usuario_master_id = serializers.PrimaryKeyRelatedField(
        source="usuario_master",
        queryset=User.objects.filter(is_superuser=False, is_active=True),
        allow_null=True,
        required=False,
        write_only=True,
    )
    modulos_contratados = serializers.SerializerMethodField()
    warning = serializers.SerializerMethodField()
    excedente = serializers.SerializerMethodField()

    class Meta(EmpresaContratoSerializer.Meta):
        fields = (
            "id", "empresa", "empresa_id", "status", "data_inicio", "data_fim",
            "limite_usuarios", "usuarios_ativos", "licencas_disponiveis", "excedido",
            "limite_sessoes_simultaneas", "sessoes_ativas", "sessoes_disponiveis", "limite_excedido",
            "excedente", "plano_completo", "usuario_master", "usuario_master_id",
            "permissions_version", "observacoes", "modulos_contratados", "warning",
            "motivo_suspensao", "observacao_suspensao", "suspenso_em", "suspenso_por",
            "reativado_em", "reativado_por",
            "created_at", "updated_at",
        )
        read_only_fields = ("permissions_version", "motivo_suspensao", "observacao_suspensao", "suspenso_em", "suspenso_por", "reativado_em", "reativado_por", "created_at", "updated_at", "empresa", "empresa_id")

    def get_excedente(self, obj):
        return max(0, int(obj.sessoes_ativas or 0) - int(obj.limite_sessoes_simultaneas or 0))

    def get_warning(self, obj):
        excedente = self.get_excedente(obj)
        if excedente > 0:
            return f"A empresa ficará com {excedente} sessão(ões) acima do limite contratado."
        return ""

    def get_modulos_contratados(self, obj):
        return EmpresaModuloSerializer(
            obj.empresa.modulos_contratados.select_related("modulo").all().order_by("modulo__ordem", "modulo__nome"),
            many=True,
        ).data

    def validate(self, attrs):
        attrs = super().validate(attrs)
        limite = attrs.get("limite_sessoes_simultaneas", getattr(self.instance, "limite_sessoes_simultaneas", 0))
        if limite is not None and int(limite) < 0:
            raise serializers.ValidationError({"limite_sessoes_simultaneas": "Limite de sessões simultâneas não pode ser negativo."})
        master = attrs.get("usuario_master", getattr(self.instance, "usuario_master", None))
        empresa = getattr(self.instance, "empresa", None) or attrs.get("empresa")
        if master and empresa and master.empresa_id != empresa.id:
            raise serializers.ValidationError({"usuario_master_id": "Master deve pertencer à empresa do contrato."})
        return attrs


class SessaoUsuarioSerializer(serializers.ModelSerializer):
    usuario_username = serializers.CharField(source="usuario.username", read_only=True)
    usuario_nome = serializers.SerializerMethodField()
    usuario_perfil = serializers.CharField(source="usuario.perfil_principal.nome", read_only=True, allow_null=True)
    loja_nome = serializers.CharField(source="loja.nome_loja", read_only=True, allow_null=True)
    empresa_nome = serializers.CharField(source="empresa.nome", read_only=True, allow_null=True)
    status = serializers.SerializerMethodField()
    navegador = serializers.SerializerMethodField()
    sistema_operacional = serializers.SerializerMethodField()
    heartbeat = serializers.DateTimeField(source="ultima_atividade_em", read_only=True)
    token_valido = serializers.SerializerMethodField()
    token_revogado = serializers.SerializerMethodField()
    validade_motivo = serializers.SerializerMethodField()
    tempo_conectado_segundos = serializers.SerializerMethodField()
    origem = serializers.SerializerMethodField()

    class Meta:
        model = SessaoUsuario
        fields = (
            "id", "empresa", "empresa_nome", "usuario", "usuario_username", "usuario_nome", "usuario_perfil",
            "loja", "loja_nome", "session_id", "dispositivo_id", "ip", "user_agent",
            "iniciada_em", "ultima_atividade_em", "encerrada_em", "motivo_encerramento", "ativa",
            "status", "navegador", "sistema_operacional", "heartbeat", "token_valido", "token_revogado",
            "validade_motivo", "tempo_conectado_segundos", "origem",
        )
        read_only_fields = fields

    def get_usuario_nome(self, obj):
        return obj.usuario.get_full_name() or obj.usuario.username

    def _token(self, obj):
        try:
            return obj.session_token
        except Exception:
            return None

    def get_token_revogado(self, obj):
        token = self._token(obj)
        return bool(token and token.revoked_at)

    def get_token_valido(self, obj):
        from accounts.services.sessions import ConcurrentSessionService

        return ConcurrentSessionService.is_session_valid(obj)

    def get_validade_motivo(self, obj):
        from accounts.services.sessions import ConcurrentSessionService

        return ConcurrentSessionService.session_validity(obj)[1]

    def get_status(self, obj):
        valido = self.get_token_valido(obj)
        motivo = self.get_validade_motivo(obj)
        if valido:
            return "ATIVA"
        if motivo == "TIMEOUT":
            return "EXPIRADA"
        if self.get_token_revogado(obj):
            return "REVOGADA"
        return "ENCERRADA"

    def get_tempo_conectado_segundos(self, obj):
        fim = obj.encerrada_em or obj.ultima_atividade_em
        if not fim or not obj.iniciada_em:
            return 0
        return max(0, int((fim - obj.iniciada_em).total_seconds()))

    def get_origem(self, obj):
        return "Plataforma" if getattr(obj.usuario, "is_superuser", False) else "Empresa"

    def get_navegador(self, obj):
        ua = (obj.user_agent or "").lower()
        if "edg/" in ua:
            return "Edge"
        if "chrome/" in ua and "chromium" not in ua:
            return "Chrome"
        if "firefox/" in ua:
            return "Firefox"
        if "safari/" in ua:
            return "Safari"
        return "-"

    def get_sistema_operacional(self, obj):
        ua = (obj.user_agent or "").lower()
        if "windows" in ua:
            return "Windows"
        if "android" in ua:
            return "Android"
        if "iphone" in ua or "ipad" in ua:
            return "iOS"
        if "mac os" in ua or "macintosh" in ua:
            return "macOS"
        if "linux" in ua:
            return "Linux"
        return "-"


class PerfilModuloPermissaoSerializer(serializers.ModelSerializer):
    modulo_chave = serializers.CharField(source="modulo.chave", read_only=True)
    modulo_nome = serializers.CharField(source="modulo.nome", read_only=True)

    class Meta:
        model = PerfilModuloPermissao
        fields = ("id", "modulo", "modulo_chave", "modulo_nome", "acesso")


class PerfilAcessoSerializer(serializers.ModelSerializer):
    permissoes_modulos = PerfilModuloPermissaoSerializer(many=True, required=False)
    usuarios_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = PerfilAcesso
        fields = ("id", "empresa", "nome", "descricao", "ativo", "padrao", "usuarios_count", "permissoes_modulos", "created_at", "updated_at")
        read_only_fields = ("created_at", "updated_at")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user and user.is_authenticated and not user.is_superuser and getattr(user, "empresa_id", None):
            self.fields["empresa"].queryset = Empresa.objects.filter(pk=user.empresa_id)

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user and user.is_authenticated and not user.is_superuser:
            attrs["empresa"] = user.empresa
        empresa = attrs.get("empresa", getattr(self.instance, "empresa", None))
        padrao = attrs.get("padrao", getattr(self.instance, "padrao", False))
        ativo = attrs.get("ativo", getattr(self.instance, "ativo", True))
        if empresa and padrao and ativo:
            qs = PerfilAcesso.objects.filter(empresa=empresa, padrao=True, ativo=True)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({"padrao": "Já existe um perfil padrão ativo para esta empresa. Use definir-padrao."})
        return attrs

    def _save_perms(self, perfil, perms):
        if perms is None:
            return
        available = CompanyModuleKeys(perfil.empresa)
        received = {item["modulo"].id: item for item in perms}
        access_by_key = {
            item["modulo"].chave: item.get("acesso") or UserModulePermission.Access.NONE
            for item in perms
        }
        PerfilModuloPermissao.objects.filter(perfil=perfil).exclude(modulo_id__in=received.keys()).delete()
        for modulo_id, item in received.items():
            modulo = item["modulo"]
            acesso = item.get("acesso") or UserModulePermission.Access.NONE
            if modulo.chave not in available and acesso != UserModulePermission.Access.NONE:
                raise serializers.ValidationError({"permissoes_modulos": f"Módulo não contratado: {modulo.chave}"})
            if acesso != UserModulePermission.Access.NONE:
                missing = [
                    dep for dep in (modulo.dependencias or [])
                    if access_by_key.get(dep, UserModulePermission.Access.NONE) == UserModulePermission.Access.NONE
                ]
                if missing:
                    raise serializers.ValidationError({"permissoes_modulos": f"Módulo {modulo.chave} exige dependências ativas: {', '.join(missing)}"})
            PerfilModuloPermissao.objects.update_or_create(perfil=perfil, modulo=modulo, defaults={"acesso": acesso})
        increment_permissions_version(perfil.empresa)
        request = self.context.get("request")
        # Obrigatório: permissões de perfil compõem a autorização efetiva dos usuários.
        AuditService.required_success(AuditAction.PERMISSION_UPDATED, category=AuditCategory.ACCESS, request=request, user=getattr(request, "user", None), instance=perfil, metadata={"tipo": "perfil", "permissoes": {item["modulo"].id: item.get("acesso") for item in perms}})

    def create(self, validated_data):
        perms = validated_data.pop("permissoes_modulos", None)
        with transaction.atomic():
            perfil = PerfilAcesso.objects.create(**validated_data)
            self._save_perms(perfil, perms)
            request = self.context.get("request")
            transaction.on_commit(lambda: AuditService.success(AuditAction.PROFILE_CREATED, category=AuditCategory.ACCESS, request=request, user=getattr(request, "user", None), instance=perfil, after={"nome": perfil.nome, "ativo": perfil.ativo, "padrao": perfil.padrao}))
        return perfil

    def update(self, instance, validated_data):
        perms = validated_data.pop("permissoes_modulos", None)
        with transaction.atomic():
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()
            self._save_perms(instance, perms)
            increment_permissions_version(instance.empresa)
            request = self.context.get("request")
            transaction.on_commit(lambda: AuditService.success(AuditAction.PROFILE_UPDATED, category=AuditCategory.ACCESS, request=request, user=getattr(request, "user", None), instance=instance, metadata={"campos": list(validated_data.keys())}))
        return instance


def CompanyModuleKeys(empresa):
    from accounts.services.effective_access import CompanyModuleService

    return CompanyModuleService(empresa).available_module_keys()
