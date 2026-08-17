# Local Worker MCP

MCP local, agnóstico de cliente. **Frontier planeja, delega e revisa. Local executa o trabalho pesado, mecânico e verificável.**

O objetivo é reduzir o consumo de tokens das IAs pagas sem jogar o conteúdo bruto no contexto delas.

```
Codex / Claude / Gemini / Grok
              │
              ▼
       Local Worker MCP
              │
       ┌──────┴───────────────┐
       ▼                      ▼
 Gemma 4 12B QAT          arquivos / PDF
 via Ollama               extração + evidências
       │
       ▼
 resultado compacto e verificável
       │
       ▼
 Frontier revisa
```

Isso **não** substitui a IA principal e **não** é um model router. É delegação real de trabalho.

```
LOCAL DISPONÍVEL?          NÃO
      │                     │
      ▼                     ▼
   DELEGA                NÃO INSISTE
      │                     │
      ▼                     ▼
   COMPRIME            FRONTIER ASSUME
      │
      ▼
 FRONTIER REVISA
```

A Gemma é uma otimização. Ela jamais pode virar ponto único de falha.

## Princípio

Delegar quando a tarefa for mecânica, repetitiva, verificável ou intensiva em contexto: PDF, logs, CSV, código, extração, classificação, resumo.

Manter na frontier: decisões arquiteturais, segurança, mudanças críticas, julgamento subjetivo, requisitos ambíguos.

Se o worker local estiver offline, a frontier continua. O MCP devolve `unavailable` rápido e recomenda fallback. O cliente pode registrar:

```text
Worker local indisponível; executei diretamente.
```

Não exige intervenção do usuário.

## Requisitos

- Python 3.10+
- [Ollama](https://ollama.com/) no mesmo PC ou em outro da LAN
- Um modelo local (recomendado: Gemma 4 12B QAT)

## Instalação

```powershell
git clone https://github.com/CaioAllgayer/Local-Worker-MCP.git
cd Local-Worker-MCP
python -m pip install -e ".[dev]"
copy .env.example .env
```

## Ollama + Gemma

1. Instale e inicie o Ollama.
2. Baixe o modelo. O nome **não é hardcoded** — use o nome real no seu `ollama list`:

```powershell
ollama list
ollama pull <nome-real-do-gemma>
```

3. Se `LOCAL_LLM_MODEL` ficar vazio, o worker tenta detectar um modelo cujo nome contém `gemma`. Senão, usa o primeiro modelo listado.

4. Teste o endpoint:

```powershell
curl http://127.0.0.1:11434/api/tags
local-worker status
```

5. Suba o MCP:

```powershell
local-worker-mcp
```

## Mesmo PC

```env
LOCAL_LLM_PROVIDER=ollama
LOCAL_LLM_BASE_URL=http://127.0.0.1:11434
LOCAL_LLM_MODEL=
```

`local` vs `lan` é detectado pelo hostname. `127.0.0.1`, `localhost` e `::1` são locais.

## Notebook usando o desktop

O worker **não assume localhost**. O backend pode estar em outro PC da LAN.

No notebook:

```env
LOCAL_LLM_PROVIDER=ollama
LOCAL_LLM_BASE_URL=http://192.168.x.x:11434
```

Troque `192.168.x.x` pelo IP atual do desktop (`ipconfig` no Windows, `ip a` no Linux). Não há IP fixo no projeto.

O comportamento é o mesmo: fail-fast, circuit breaker, cache, compressão.

No desktop, o Ollama precisa aceitar conexões da LAN (variável `OLLAMA_HOST=0.0.0.0` e firewall liberando a porta 11434).

## OpenAI-compatible

LM Studio, llama.cpp server, vLLM e similares:

```env
LOCAL_LLM_PROVIDER=openai_compatible
LOCAL_LLM_BASE_URL=http://127.0.0.1:1234/v1
LOCAL_LLM_MODEL=...
LOCAL_LLM_API_KEY=
```

## Fail-fast e circuit breaker

Defaults:

```env
LOCAL_LLM_CONNECT_TIMEOUT_SECONDS=2
LOCAL_LLM_REQUEST_TIMEOUT_SECONDS=45
LOCAL_LLM_MAX_RETRIES=0
LOCAL_LLM_CIRCUIT_BREAKER_FAILURES=2
LOCAL_LLM_CIRCUIT_BREAKER_COOLDOWN_SECONDS=60
```

Conexão recusada **não** entra em retry. Depois de N falhas o circuito abre e as próximas chamadas devolvem `unavailable` imediatamente. Após o cooldown, uma tentativa é permitida.

```json
{
  "status": "unavailable",
  "fallback_recommended": true,
  "reason": "Local LLM endpoint unreachable"
}
```

## Ferramentas MCP

| Ferramenta | Função |
|---|---|
| `local_status` | provider, endpoint, local/LAN, latência, modelo, circuit breaker, cache |
| `delegate_task` | tarefa genérica → JSON compacto |
| `delegate_batch` | tarefas independentes em paralelo (`MAX_PARALLEL_WORKERS=4`) |
| `delegate_file` | TXT, Markdown, CSV, JSON, código, logs |
| `delegate_pdf` | extração por página, chunking, síntese hierárquica, evidências |
| `cache_stats` | tamanho, entradas, hits, misses, hit rate, expirados |
| `cache_cleanup` | GC agora (TTL → não reutilizados → LRU) |
| `cache_clear` | apaga entradas descartáveis |

O conteúdo bruto do arquivo **não** precisa entrar no contexto da IA paga. O worker lê, comprime e devolve evidências verificáveis (página, linha, trecho).

## Segurança

Default: `SECURITY_MODE=READ_ONLY`, `ENABLE_SHELL=false`.

```env
SECURITY_MODE=READ_ONLY
ALLOWED_PATHS=C:\Projects,D:\Research
ENABLE_SHELL=false
```

- `READ_ONLY` — só leitura nos paths autorizados; escrita e shell bloqueados
- `WORKSPACE_WRITE` — leitura/escrita nos paths autorizados; shell só se `ENABLE_SHELL=true`
- `FULL_LOCAL` — mais permissivo; ainda bloqueia comandos destrutivos

Path traversal é bloqueado. `rm`, `del`, `format` etc. são recusados.

## Cache e logs

Cache persistente em `~/.local-worker-mcp/cache`, autolimpante:

```env
CACHE_TTL_DAYS=30
CACHE_MAX_SIZE_GB=10
CACHE_CLEANUP_THRESHOLD_PERCENT=90
CACHE_TARGET_USAGE_PERCENT=80
CACHE_CLEANUP_INTERVAL_HOURS=6
```

Entradas são descartáveis por padrão. `persistent=true` preserva artefatos importantes.

Logs rotacionam e expiram:

```env
LOG_RETENTION_DAYS=14
LOG_MAX_SIZE_MB=250
```

O log **não** guarda o conteúdo completo dos arquivos.

## Benchmark

```powershell
local-worker benchmark arquivo.pdf
```

Saída:

```text
Arquivo: arquivo.pdf
Worker: gemma4:12b-qat
Backend: ollama
Endpoint: LAN/local
Tamanho: ...
Tokens originais estimados: ...
Tokens processados localmente: ...
Resultado para frontier: ...
Compressão: ...
Tempo: ...
Cache: HIT/MISS
```

## Codex

`~/.codex/config.toml` ou o JSON do cliente:

```json
{
  "mcpServers": {
    "local-worker": {
      "command": "local-worker-mcp",
      "env": {
        "LOCAL_LLM_PROVIDER": "ollama",
        "LOCAL_LLM_BASE_URL": "http://127.0.0.1:11434",
        "ALLOWED_PATHS": "C:\\Projects"
      }
    }
  }
}
```

Ver `examples/codex.json`.

## Claude Code

```powershell
claude mcp add local-worker --scope user -- local-worker-mcp
```

Ou cole `examples/claude_code.json` em `~/.claude.json`.

No `CLAUDE.md` / `AGENTS.md` do projeto, ensine a política:

> Tarefas mecânicas e leitura de arquivos grandes vão para `delegate_pdf` / `delegate_file` / `delegate_task`. Se `local_status` ou a ferramenta devolver `unavailable`, execute diretamente e siga em frente.

## Outros clientes MCP

Qualquer cliente stdio funciona. Exemplo genérico em `examples/generic.json`:

```json
{
  "mcpServers": {
    "local-worker": {
      "command": "local-worker-mcp",
      "env": {
        "LOCAL_LLM_PROVIDER": "ollama",
        "LOCAL_LLM_BASE_URL": "http://127.0.0.1:11434"
      }
    }
  }
}
```

## Exemplos

- `examples/pdf.md` — paper / PDF longo
- `examples/code.md` — leitura inicial de repositório
- `examples/logs.md` — extração de erros

## Testes

```powershell
python -m pip install -e ".[dev]"
pytest
ruff check src tests
```

A suíte **não** depende de Ollama/Gemma reais. Tudo é mockado.

## O que não entra neste MVP

Automação de GUI Windows, Playwright, multiagente complexo, RAG vetorial, dashboard, Kubernetes, roteador ML.

A arquitetura deixa espaço para `delegate_repo`, `delegate_git`, `delegate_browser` etc. na próxima fase.

## Licença

MIT.
