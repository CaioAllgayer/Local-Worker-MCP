# Política de delegação (colar no AGENTS.md / CLAUDE.md do cliente)

O Local Worker MCP é opcional. Se indisponível, continue a tarefa diretamente e mencione isso em uma linha.

Delegar ao worker local quando a tarefa for mecânica, repetitiva, verificável ou intensiva em contexto:

- PDF longo → `delegate_pdf`
- TXT / Markdown / CSV / JSON / código / logs → `delegate_file`
- várias tarefas independentes → `delegate_batch`
- extração / resumo / classificação / transformação → `delegate_task`

Antes de uma sessão pesada, `local_status`. Se `reachable=false` ou `circuit_breaker.state=open`, não insista.

Não delegar: decisões arquiteturais, segurança, mudanças críticas, julgamento subjetivo, requisitos ambíguos.

O worker devolve síntese + evidências (página/linha). Revise. Não releia o arquivo inteiro sem necessidade.
