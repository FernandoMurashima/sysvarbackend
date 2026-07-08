from decimal import Decimal

from cadastros.models import PlanoContabil

from .models import LancamentoContabil, MovimentacaoFinanceira


def _conta_por_codigo(empresa, codigo):
    if not empresa or not codigo:
        return None
    return PlanoContabil.objects.filter(empresa=empresa, codigo=codigo, ativa=True).first()


def _conta_analitica_por_classe(empresa, classe, termos=()):
    if not empresa:
        return None
    qs = PlanoContabil.objects.filter(empresa=empresa, classe=classe, ativa=True, analitica=True)
    for termo in termos:
        conta = qs.filter(descricao__icontains=termo).order_by('codigo').first()
        if conta:
            return conta
    return qs.order_by('codigo').first()


def _conta_operacional(movimentacao):
    empresa = movimentacao.empresa
    if movimentacao.origem == MovimentacaoFinanceira.ORIGEM_CMV:
        return _conta_analitica_por_classe(empresa, PlanoContabil.CLASSE_ATIVO, ('Estoque', 'Mercadoria'))

    destino = movimentacao.conta_bancaria or movimentacao.caixa
    codigo = getattr(destino, 'conta_contabil', None)
    conta = _conta_por_codigo(empresa, codigo)
    if conta:
        return conta

    if movimentacao.conta_bancaria_id:
        return _conta_analitica_por_classe(empresa, PlanoContabil.CLASSE_ATIVO, ('Banco', 'Conta', 'Dispon'))
    return _conta_analitica_por_classe(empresa, PlanoContabil.CLASSE_ATIVO, ('Caixa', 'Dispon'))


def _conta_natureza(movimentacao):
    natureza = movimentacao.Idnatureza
    if not natureza:
        return None
    if natureza.plano_contabil_id:
        return natureza.plano_contabil
    conta = _conta_por_codigo(movimentacao.empresa, natureza.conta_contabil)
    if conta:
        return conta

    operacao = (natureza.natureza_operacao or '').upper()
    if operacao == 'RECEITA':
        return _conta_analitica_por_classe(movimentacao.empresa, PlanoContabil.CLASSE_RECEITA, (natureza.categoria_principal, 'Receita'))
    if operacao == 'DESPESA':
        texto = ' '.join([
            natureza.categoria_principal or '',
            natureza.subcategoria or '',
            natureza.descricao or '',
            natureza.categoria_gerencial or '',
        ]).lower()
        if any(palavra in texto for palavra in ('cmv', 'custo', 'mercadoria vendida')):
            conta_custo = _conta_analitica_por_classe(
                movimentacao.empresa,
                PlanoContabil.CLASSE_CUSTO,
                (natureza.categoria_principal, natureza.subcategoria, natureza.descricao, 'CMV', 'Custo')
            )
            if conta_custo:
                return conta_custo
        return _conta_analitica_por_classe(movimentacao.empresa, PlanoContabil.CLASSE_DESPESA, (natureza.categoria_principal, 'Despesa'))
    return None


def gerar_lancamento_contabil_movimentacao(movimentacao):
    if not movimentacao or not movimentacao.pk:
        return None
    if movimentacao.status != MovimentacaoFinanceira.STATUS_EFETIVA:
        return None
    if not movimentacao.empresa_id:
        return None

    try:
        existente = movimentacao.lancamento_contabil
    except Exception:
        existente = None
    if existente:
        return existente

    conta_operacional = _conta_operacional(movimentacao)
    conta_natureza = _conta_natureza(movimentacao)
    observacoes = []

    if not conta_operacional:
        observacoes.append('Conta operacional de caixa/banco não localizada.')
    if not conta_natureza:
        observacoes.append('Conta contábil da natureza não localizada.')

    conta_debito = None
    conta_credito = None
    if movimentacao.tipo == MovimentacaoFinanceira.TIPO_ENTRADA:
        conta_debito = conta_operacional
        conta_credito = conta_natureza
    elif movimentacao.tipo == MovimentacaoFinanceira.TIPO_SAIDA:
        conta_debito = conta_natureza
        conta_credito = conta_operacional
    else:
        observacoes.append('Transferência ainda exige lançamento contábil pareado.')

    status = LancamentoContabil.STATUS_GERADO
    if observacoes or not conta_debito or not conta_credito:
        status = LancamentoContabil.STATUS_PENDENTE

    return LancamentoContabil.objects.create(
        empresa=movimentacao.empresa,
        idloja=movimentacao.idloja,
        movimentacao=movimentacao,
        data_lancamento=movimentacao.data_movimento,
        documento=movimentacao.documento,
        historico=movimentacao.historico[:255],
        origem=movimentacao.origem,
        natureza=movimentacao.Idnatureza,
        conta_debito=conta_debito,
        conta_credito=conta_credito,
        valor=Decimal(movimentacao.valor or 0),
        status=status,
        observacao=' '.join(observacoes)[:255],
    )


def estornar_lancamento_contabil_movimentacao(movimentacao, motivo=''):
    if not movimentacao or not movimentacao.pk:
        return None
    try:
        lancamento = movimentacao.lancamento_contabil
    except Exception:
        lancamento = None
    if not lancamento or lancamento.status == LancamentoContabil.STATUS_ESTORNADO:
        return lancamento
    lancamento.status = LancamentoContabil.STATUS_ESTORNADO
    if motivo:
        lancamento.observacao = (motivo or '')[:255]
    lancamento.save(update_fields=['status', 'observacao'])
    return lancamento
