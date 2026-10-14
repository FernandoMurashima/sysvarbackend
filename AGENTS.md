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
- Regras fiscais, estoque, financeiro, permissões e sincronização com o Hub exigem teste.
- Endpoints usados pelo Hub são contrato: não quebre compatibilidade sem alterar o Hub na mesma tarefa.
- O agente local envia apenas metadados de XML; nunca envia nem altera os XMLs originais.

## Documentação oficial do Sysvar

A documentação oficial (regras de negócio, decisões, riscos, homologações) fica no cofre:

- local: `C:\Takeshi\takeshi\10 Projetos\Sysvar`
- repositório: `FernandoMurashima/sysvar-vault` (`main`)

Antes de alterar código:

1. Se a skill `sysvar-contexto` estiver disponível, use-a para montar o contexto do módulo.
2. Se não estiver, leia em `Contexto do Projeto/`, para o módulo da tarefa: `Estado Atual - <Área>`, `Workflows - <Área> - <Submódulo>` e `Riscos e Cuidados - <Área> - <Submódulo>`. Em `Homologações/`, leia a homologação do módulo quando existir.
3. Leia o código atual relacionado antes de criar qualquer coisa.

Não leia as notas gerais grandes (`Sysvar.md`, `Riscos e Cuidados.md`, `Mapa Técnico.md`) inteiras: busque nelas o trecho do módulo.

## Regras de trabalho

- **Reaproveitar → refatorar → estender → criar.** Não construa do zero o que já existe. Antes de criar model, endpoint, serializer, service, componente ou helper, procure um equivalente.
- **Não invente regra de negócio.** Se houver dúvida que o prompt, a documentação e o código não resolvem, preserve o comportamento atual e registre a dúvida no relatório final.
- **Hierarquia de fontes:** decisão aprovada no prompt > documentação vigente > código atual > inferência. Seções datadas e checkpoints são histórico.
- **Escopo fechado.** Altere só o que a tarefa pede. Correção pequena não vira refatoração.
- **Backend é a autoridade** de validações, cálculos e permissões. O frontend pode antecipar, nunca substituir.
- **Multiempresa:** todo acesso a dados respeita tenant, empresa e loja no backend.
- **Testes proporcionais ao escopo**, incluindo o caso negativo da regra alterada. Proibido: `skip` sem necessidade, comentar teste, enfraquecer assert, mudar regra aprovada para o teste passar.
- **Segredos:** não versionar nem copiar `.env`, tokens, XML de cliente, dumps, logs ou filas SQLite.

## Encerramento de toda tarefa

1. Rodar os testes e o build relevantes (seção Comandos acima).
2. `git add` só dos arquivos alterados intencionalmente; `git commit` com resumo curto, imperativo, em português (ex.: `Corrige escopo multiempresa da devolucao Hub`); `git push origin main`.
3. Sem mensagens intermediárias. Entregar um único relatório final com: resumo; documentação consultada; código reaproveitado; arquivos alterados; migrations; testes e builds com resultado; commit SHA; confirmação do push; pendências reais e dúvidas de negócio.
