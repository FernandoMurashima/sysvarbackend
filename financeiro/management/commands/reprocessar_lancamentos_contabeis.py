from django.core.management.base import BaseCommand

from financeiro.models import LancamentoContabil, MovimentacaoFinanceira
from financeiro.services import gerar_lancamento_contabil_movimentacao


class Command(BaseCommand):
    help = 'Gera lançamentos contábeis para movimentações financeiras efetivas sem lançamento.'

    def add_arguments(self, parser):
        parser.add_argument('--empresa', type=int, help='Filtra uma empresa específica.')
        parser.add_argument('--pendentes', action='store_true', help='Mostra também a quantidade de lançamentos pendentes ao final.')

    def handle(self, *args, **options):
        qs = MovimentacaoFinanceira.objects.select_related(
            'empresa', 'idloja', 'Idnatureza', 'Idnatureza__plano_contabil', 'caixa', 'conta_bancaria'
        ).filter(status=MovimentacaoFinanceira.STATUS_EFETIVA, lancamento_contabil__isnull=True)
        if options.get('empresa'):
            qs = qs.filter(empresa_id=options['empresa'])

        criados = 0
        pendentes = 0
        for mov in qs.order_by('data_movimento', 'Idmovimentacao'):
            lancamento = gerar_lancamento_contabil_movimentacao(mov)
            if not lancamento:
                continue
            criados += 1
            if lancamento.status == LancamentoContabil.STATUS_PENDENTE:
                pendentes += 1

        self.stdout.write(self.style.SUCCESS(f'Lançamentos criados: {criados}'))
        if options.get('pendentes'):
            total_pendentes = LancamentoContabil.objects.filter(status=LancamentoContabil.STATUS_PENDENTE)
            if options.get('empresa'):
                total_pendentes = total_pendentes.filter(empresa_id=options['empresa'])
            self.stdout.write(f'Pendentes novos: {pendentes}')
            self.stdout.write(f'Pendentes totais: {total_pendentes.count()}')
