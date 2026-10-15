from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import UserModulePermission
from cadastros.models import Empresa, EmpresaContrato, EmpresaModulo, Loja, ModuloSistema
from produto.models import Colecao, Grupo, Produto, Promocao, Subgrupo, Unidade


@override_settings(ALLOWED_HOSTS=['testserver'])
class PromocaoEmpresaApiTests(TestCase):
    campos = ('lojas', 'produtos', 'colecoes', 'grupos', 'subgrupos')
    url = '/api/produto/promocao/'

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nome='Empresa Promo A', documento='33222333000181', plano_completo=True)
        cls.empresa_b = Empresa.objects.create(nome='Empresa Promo B', documento='33222333000182', plano_completo=True)
        cls.user = get_user_model().objects.create_user('promo-admin', 'promo@example.com', '123', empresa=cls.empresa, type='Admin')
        cls.superuser = get_user_model().objects.create_superuser('promo-super', 'promo-super@example.com', '123', empresa=cls.empresa)
        modulo, _ = ModuloSistema.objects.get_or_create(chave='produtos', defaults={
            'nome': 'Produtos', 'categoria': ModuloSistema.CATEGORIA_BASICO, 'basico': True,
        })
        EmpresaContrato.objects.update_or_create(empresa=cls.empresa, defaults={
            'status': EmpresaContrato.STATUS_ATIVO, 'plano_completo': True, 'usuario_master': cls.user,
        })
        EmpresaModulo.objects.update_or_create(empresa=cls.empresa, modulo=modulo, defaults={'contratado': True})
        UserModulePermission.objects.create(user=cls.user, modulo=UserModulePermission.Module.PRODUTOS, acesso=UserModulePermission.Access.EDIT)
        cls.proprios = cls.criar_vinculos(cls.empresa, 'A', '26', '33222333000191')
        cls.outros = cls.criar_vinculos(cls.empresa_b, 'B', '27', '33222333000272')

    @staticmethod
    def criar_vinculos(empresa, sufixo, codigo, cnpj):
        unidade = Unidade.objects.create(empresa=empresa, Descricao='Unidade ' + sufixo, Codigo='UN')
        grupo = Grupo.objects.create(empresa=empresa, Codigo='01', Descricao='Grupo ' + sufixo, Margem=0)
        return {
            'lojas': Loja.objects.create(empresa=empresa, nome_loja='Loja ' + sufixo, apelido_loja=sufixo, cnpj=cnpj),
            'produtos': Produto.objects.create(empresa=empresa, tipo_produto='1', referencia=codigo + '0101001', descricao='Produto ' + sufixo, unidade=unidade),
            'colecoes': Colecao.objects.create(empresa=empresa, Descricao='Coleção ' + sufixo, Codigo=codigo, Estacao='01'),
            'grupos': grupo,
            'subgrupos': Subgrupo.objects.create(empresa=empresa, Idgrupo=grupo, Descricao='Subgrupo ' + sufixo, Margem=0),
        }

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def payload(self, vinculos=None, **overrides):
        dados = {
            'nome': 'Promoção válida', 'ativo': True, 'data_inicio': str(timezone.localdate()),
            'tipo': Promocao.TIPO_DESCONTO_PERCENTUAL, 'valor': '10.0000',
            'escopo': Promocao.ESCOPO_PRODUTO, 'prioridade': 7, 'acumula_cashback': False,
            **{campo: [registro.pk] for campo, registro in (vinculos or self.proprios).items()},
        }
        dados.update(overrides)
        return dados

    def criar_promocao(self):
        resposta = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(resposta.status_code, 201, resposta.data)
        return Promocao.objects.get(pk=resposta.data['Idpromocao'])

    def assert_vinculos(self, promocao, vinculos):
        for campo, registro in vinculos.items():
            self.assertEqual(list(getattr(promocao, campo).values_list('pk', flat=True)), [registro.pk])

    def test_criacao_usa_empresa_autenticada_e_preserva_regras(self):
        promocao = self.criar_promocao()
        self.assertEqual(promocao.empresa_id, self.empresa.pk)
        self.assertEqual(promocao.valor, Decimal('10.0000'))
        self.assertEqual(promocao.prioridade, 7)
        self.assertFalse(promocao.acumula_cashback)
        self.assert_vinculos(promocao, self.proprios)

    def test_criacao_rejeita_cada_vinculo_de_outra_empresa(self):
        for campo in self.campos:
            with self.subTest(campo=campo):
                dados = self.payload(**{campo: [self.proprios[campo].pk, self.outros[campo].pk]})
                resposta = self.client.post(self.url, dados, format='json')
                self.assertEqual(resposta.status_code, 400, resposta.data)
                self.assertIn(campo, resposta.data)
        self.assertFalse(Promocao.objects.exists())

    def test_patch_valido_preserva_vinculos_existentes(self):
        promocao = self.criar_promocao()
        resposta = self.client.patch(f'{self.url}{promocao.pk}/', {'nome': 'Atualizada', 'ativo': False}, format='json')
        self.assertEqual(resposta.status_code, 200, resposta.data)
        promocao.refresh_from_db()
        self.assertEqual(promocao.nome, 'Atualizada')
        self.assertFalse(promocao.ativo)
        self.assert_vinculos(promocao, self.proprios)

    def test_put_valido_preserva_promocao_existente(self):
        promocao = self.criar_promocao()
        resposta = self.client.put(f'{self.url}{promocao.pk}/', self.payload(nome='Edição completa'), format='json')
        self.assertEqual(resposta.status_code, 200, resposta.data)
        self.assert_vinculos(promocao, self.proprios)

    def test_patch_rejeita_cada_vinculo_estrangeiro_sem_alteracao_parcial(self):
        promocao = self.criar_promocao()
        for campo in self.campos:
            with self.subTest(campo=campo):
                resposta = self.client.patch(f'{self.url}{promocao.pk}/', {
                    'nome': 'Não salvar', campo: [self.outros[campo].pk],
                }, format='json')
                self.assertEqual(resposta.status_code, 400, resposta.data)
                self.assertIn(campo, resposta.data)
                promocao.refresh_from_db()
                self.assertEqual(promocao.nome, 'Promoção válida')
                self.assert_vinculos(promocao, self.proprios)

    def test_patch_considera_cada_vinculo_legado_omitido_e_permite_correcao_explicita(self):
        promocao = self.criar_promocao()
        for campo in self.campos:
            with self.subTest(campo=campo):
                # Fixture inconsistente apenas no banco isolado de testes.
                getattr(promocao, campo).set([self.outros[campo]])
                resposta = self.client.patch(f'{self.url}{promocao.pk}/', {'nome': 'Não salvar'}, format='json')
                self.assertEqual(resposta.status_code, 400, resposta.data)
                self.assertIn(campo, resposta.data)
                resposta = self.client.patch(f'{self.url}{promocao.pk}/', {campo: [self.proprios[campo].pk]}, format='json')
                self.assertEqual(resposta.status_code, 200, resposta.data)

    def test_admin_da_empresa_nao_contorna_escopo_com_payload_ou_query_string(self):
        resposta = self.client.post(self.url + f'?empresa={self.empresa_b.pk}', self.payload(self.outros, empresa=self.empresa_b.pk), format='json')
        self.assertEqual(resposta.status_code, 400, resposta.data)
        self.assertIn('empresa', resposta.data)
        promocao = self.criar_promocao()
        resposta = self.client.patch(f'{self.url}{promocao.pk}/', {'empresa': self.empresa_b.pk}, format='json')
        self.assertEqual(resposta.status_code, 400, resposta.data)
        promocao.refresh_from_db()
        self.assertEqual(promocao.empresa_id, self.empresa.pk)

    def test_superusuario_respeita_empresa_escolhida_e_rejeita_mistura(self):
        self.client.force_authenticate(self.superuser)
        resposta = self.client.post(self.url, self.payload(self.outros, empresa=self.empresa_b.pk), format='json')
        self.assertEqual(resposta.status_code, 201, resposta.data)
        promocao = Promocao.objects.get(pk=resposta.data['Idpromocao'])
        self.assertEqual(promocao.empresa_id, self.empresa_b.pk)
        self.assert_vinculos(promocao, self.outros)
        for campo in self.campos:
            with self.subTest(campo=campo):
                dados = self.payload(self.outros, empresa=self.empresa_b.pk, **{campo: [self.proprios[campo].pk]})
                resposta = self.client.post(self.url, dados, format='json')
                self.assertEqual(resposta.status_code, 400, resposta.data)
                self.assertIn(campo, resposta.data)

    def test_superusuario_nao_troca_empresa_com_vinculos_omitidos(self):
        promocao = self.criar_promocao()
        self.client.force_authenticate(self.superuser)
        resposta = self.client.patch(f'{self.url}{promocao.pk}/', {'empresa': self.empresa_b.pk}, format='json')
        self.assertEqual(resposta.status_code, 400, resposta.data)
        for campo in self.campos:
            self.assertIn(campo, resposta.data)
        promocao.refresh_from_db()
        self.assertEqual(promocao.empresa_id, self.empresa.pk)
        self.assert_vinculos(promocao, self.proprios)

    def test_superusuario_troca_empresa_apenas_com_todos_vinculos_coerentes(self):
        promocao = self.criar_promocao()
        self.client.force_authenticate(self.superuser)
        dados = {campo: [registro.pk] for campo, registro in self.outros.items()}
        resposta = self.client.patch(f'{self.url}{promocao.pk}/', {'empresa': self.empresa_b.pk, **dados}, format='json')
        self.assertEqual(resposta.status_code, 200, resposta.data)
        promocao.refresh_from_db()
        self.assertEqual(promocao.empresa_id, self.empresa_b.pk)
        self.assert_vinculos(promocao, self.outros)

    def test_superusuario_sem_empresa_na_criacao_recebe_erro_claro(self):
        self.client.force_authenticate(self.superuser)
        resposta = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(resposta.status_code, 400, resposta.data)
        self.assertIn('empresa', resposta.data)

    def test_limpar_vinculos_e_preservar_validacoes_comerciais(self):
        promocao = self.criar_promocao()
        resposta = self.client.patch(f'{self.url}{promocao.pk}/', {campo: [] for campo in self.campos}, format='json')
        self.assertEqual(resposta.status_code, 200, resposta.data)
        for campo in self.campos:
            self.assertFalse(getattr(promocao, campo).exists())
        for dados, campo in (({'valor': '-1'}, 'valor'), ({'valor': '101'}, 'valor'), ({'data_fim': '2000-01-01'}, 'data_fim')):
            with self.subTest(dados=dados):
                resposta = self.client.patch(f'{self.url}{promocao.pk}/', dados, format='json')
                self.assertEqual(resposta.status_code, 400, resposta.data)
                self.assertIn(campo, resposta.data)
