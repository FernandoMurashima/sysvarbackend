# AGENTS.md — Sysvar Central Backend

Backend Django da retaguarda Central do Sysvar ERP (multiempresa). Autoridade para cadastros e dados corporativos. Integra com o Sysvar Hub (operação local das lojas) pelo app `hub/` e pelos módulos de negócio de cada fluxo.

Repositórios relacionados: `sysvarfrontend` (Central), `sysvarhub-backend` e `sysvarhub-frontend` (Hub). Mudanças que atravessam a fronteira Central ↔ Hub devem considerar o outro lado do contrato.

## Estrutura

- Apps: `accounts/`, `auditoria/`, `cadastros/`, `compras/`, `dashboard/`, `distribuicao/`, `financeiro/`, `fiscal/`, `hub/`, `produto/`.
- Configuração: `Varejo/`. Agente local de XML: `local_agent/`.
- `docs/`: só documentação técnica acoplada a este código.

## Comandos

```text
.\venv\Scripts\python.exe manage.py check
.\venv\Scripts\python.exe manage.py test <app>
.\venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\venv\Scripts\python.exe manage.py runserver
```

Testes ficam em `tests.py` / `tests_*.py` de cada app. Rode os do app afetado; a suíte completa só em mudanças amplas.

## Cuidados deste repositório

- Toda alteração de model exige migration; trate dados existentes (defaults, nulos).
- **Produção:** não executar `migrate`, migrations ou alterações de esquema em banco de produção sem autorização explícita.
- Regras fiscais, estoque, financeiro, permissões e sincronização com o Hub exigem teste.
- Endpoints usados pelo Hub são contrato: não quebre compatibilidade sem alterar o Hub na mesma tarefa.
- O agente local envia apenas metadados de XML; nunca envia nem altera os XMLs originais.

## Documentação oficial do Sysvar

A documentação oficial (regras de negócio, decisões, riscos, homologações) fica no cofre:

- local: `C:\Takeshi\takeshi\10 Projetos\Sysvar`
- repositório: `FernandoMurashima/sysvar-vault` (`main`)

A skill `sysvar-contexto` centraliza o procedimento detalhado de recuperação das fontes do projeto; não duplique seu roteiro neste arquivo.

Antes de alterar código:

1. Use a skill `sysvar-contexto`, se estiver instalada e disponível, para localizar as fontes vigentes do módulo.
2. Se indisponível, consulte no cofre somente os documentos pertinentes e existentes; não crie documentos ausentes para completar um padrão.
3. Leia o código atual relacionado antes de criar qualquer coisa.

Não leia as notas gerais grandes (`Sysvar.md`, `Riscos e Cuidados.md`, `Mapa Técnico.md`) inteiras: busque nelas o trecho do módulo.

## Regras de trabalho

- **Pré-verificação:** antes de alterar, confira branch, HEAD e `git status --short`. Preserve alterações preexistentes; nunca sobrescreva trabalho alheio.
- **Tarefa somente de análise:** auditorias, levantamentos e diagnósticos não autorizam editar arquivos, criar commits ou fazer push.
- **Reaproveitar → refatorar → estender → criar.** Não construa do zero o que já existe. Antes de criar model, endpoint, serializer, service, componente ou helper, procure um equivalente.
- **Não invente regra de negócio.** Se houver dúvida que o prompt, a documentação e o código não resolvem, preserve o comportamento atual e registre a dúvida no relatório final.
- **Hierarquia de fontes:** decisão aprovada no prompt > documentação vigente > código atual > inferência. Seções datadas e checkpoints são histórico.
- **Escopo fechado.** Altere só o que a tarefa pede. Correção pequena não vira refatoração.
- **Backend é a autoridade** de validações, cálculos e permissões. O frontend pode antecipar, nunca substituir.
- **Multiempresa:** todo acesso a dados respeita tenant, empresa e loja no backend.
- **Testes proporcionais ao escopo**, incluindo o caso negativo da regra alterada. Execute testes direcionados por padrão; suíte completa somente se a abrangência da mudança justificar ou a tarefa solicitar. Proibido: `skip` sem necessidade, comentar teste, enfraquecer assert, mudar regra aprovada para o teste passar.
- **Segredos:** não versionar nem copiar `.env`, tokens, XML de cliente, dumps, logs ou filas SQLite.

## Encerramento de toda tarefa

1. Em tarefas com alterações autorizadas, executar somente os testes, verificações e builds pertinentes; não rodar a suíte completa por padrão. Em auditorias e diagnósticos, não executar validações invasivas sem solicitação.
2. Somente quando a tarefa autorizar modificações e versionamento: revisar o diff, executar `git add` apenas dos arquivos intencionais, criar `git commit` com resumo curto, imperativo, em português (ex.: `Corrige validacao multiempresa em vendas`) e executar `git push origin main` se autorizado. Nunca incluir modificações preexistentes não relacionadas.
3. Sem mensagens intermediárias. Entregar um único relatório final com: resumo; documentação consultada; código reaproveitado; arquivos alterados; migrations; testes e builds pertinentes (ou motivo da não execução); SHA e confirmação do push quando houver; `git status` final; pendências reais e dúvidas de negócio. Em tarefas somente de leitura, informar expressamente que não houve alterações nem commit/push.
