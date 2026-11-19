# Dívida técnica

Itens conhecidos que ficaram para depois, com o contexto necessário para retomá-los sem
reler a conversa em que surgiram. Para adicionar um item, copie o modelo no fim do arquivo.

---

## DT-01 — EXEC como sessão de terminal interativa

**Registrado em:** 2026-11-18 · **Prioridade:** média/baixa · **Exigido pela especificação?** Não

### O que se quer

Hoje o `exec` roda **um comando isolado** e devolve o resultado. O desejo é que o
controlador possa **assumir o terminal do agente**: abrir uma sessão de shell e executar
vários comandos em sequência, como num SSH, com o estado preservado entre eles
(`cd`, variáveis de ambiente) e a saída chegando enquanto o comando roda.

### Como é hoje

Em `src/agent/handlers/exec_handler.py` (`ExecHandler`, `run_command`):

| Aspecto | Comportamento atual |
|---|---|
| Processo | Um `subprocess.Popen(shell=True)` **novo por comando**, que morre no fim. |
| Estado | Nenhum entre comandos: `cd /tmp` não vale para o comando seguinte. |
| Entrada | `stdin` fechado (`DEVNULL`). Programas que perguntam algo não funcionam. |
| Saída | Capturada **inteira** e devolvida **só no fim**, com teto de 1 MB por stream. |
| Sessão | O comando **bloqueia** a sessão do agente até terminar (padrão 30 s, teto 300 s): nenhum outro comando é atendido nesse tempo. |
| Encerramento | Ao estourar o prazo mata a árvore de processos (`killpg` / `taskkill /T`). |

A especificação (RF2) pede só "rodar um comando do SO e devolver saída padrão e código de
saída", então **o requisito já está cumprido**. Esta dívida é uma evolução, não uma falha.

### Por que é dívida e não simples melhoria

Fazer direito exige mexer em três pontos que hoje estão acoplados de um jeito que não
comporta isso:

1. **A sessão é síncrona.** `Session._dispatch` executa o tratador na mesma thread que lê
   do socket. Uma sessão de terminal precisa ler a saída do shell **ao mesmo tempo** em que
   recebe a entrada do operador. O `command_executer_thread.py` (hoje vazio) é o lugar
   previsto para isso.
2. **Várias threads escreverão no mesmo socket** (respostas, saída do shell, eventos). O
   `send_msg` já aceita um `lock`, mas nenhum chamador o usa ainda.
3. **O protocolo é pedido e resposta.** Um terminal é um fluxo: precisa de eventos
   assíncronos de saída, e não de uma resposta única por comando.

### Pontos de decisão

- **PTY ou pipes.**
  - *Pipes* (um `sh` persistente com stdin/stdout em pipe): simples e portável, mas não
    roda programas interativos (`vim`, `top`, `sudo` pedindo senha) e exige um marcador para
    saber onde cada comando termina.
  - *PTY* (`pty` / `os.openpty` no POSIX): terminal de verdade, com Ctrl+C e programas
    interativos. No Windows não existe na biblioteca padrão (ConPTY, ou a dependência
    `pywinpty`). Se o grupo optar só por POSIX, isolar e justificar (Seção 6 do enunciado,
    "Portabilidade").
- **Protocolo.** Sugestão: ações `SHELL_OPEN`, `SHELL_INPUT`, `SHELL_RESIZE`,
  `SHELL_CLOSE` do controlador, e **eventos** `SHELL_OUTPUT` / `SHELL_EXIT` do agente (tipo
  `evento`, com o `id` da sessão de shell). O controlador já recebe eventos pela fila
  `incoming`.
- **Uma sessão de shell por vez** (coerente com "um controlador por vez"), com limite de
  inatividade.
- **Contrapressão.** Fila limitada para a saída: um `yes` rodando no shell não pode
  encher a memória do agente nem o socket.

### Restrições que NÃO podem ser violadas (Seção 3 do enunciado)

Este item é o que mais se aproxima do comportamento de uma ferramenta maliciosa, então
as regras de transparência valem com mais força:

- **Indicador visível:** enquanto houver shell aberto, o aviso na tela do agente deve dizer
  isso, além do endereço do controlador.
- **Log:** todo comando enviado ao shell vai para o log do agente, com origem e horário.
  Decidir como tratar entrada que não é comando (senha digitada num `sudo`): registrar que a
  entrada ocorreu sem registrar o conteúdo, para o log não virar um coletor de credenciais
  (item proibido: "coleta sorrateira de credenciais").
- **Kill switch:** deve encerrar o shell **e todos os processos descendentes**, não só o
  processo principal.
- **Sem sobreviver à sessão:** ao fechar a sessão, ao cair a conexão ou ao parar o agente,
  o shell e seus filhos morrem. Nada de processo órfão em segundo plano (seria uma forma
  de persistência).
- **Não é um sandbox:** assim como o `exec`, o terminal **não respeita a `raiz`**. Registrar
  isso no relatório de análise ética.

### Critérios de pronto

- [ ] `cd` e variáveis de ambiente persistem entre comandos da mesma sessão.
- [ ] A saída aparece enquanto o comando roda, sem esperar o fim.
- [ ] Ctrl+C do operador interrompe o comando em andamento sem fechar o shell.
- [ ] Um comando longo não bloqueia outras ações (ex.: `ls`, `info`) enquanto roda.
- [ ] Fechar a sessão, derrubar a conexão ou acionar o kill switch mata o shell e todos os
      processos descendentes (com teste que verifica que nenhum sobrou).
- [ ] Todo comando enviado ao shell aparece no log; senhas não.
- [ ] Saída excessiva não esgota a memória (teste com um `yes`).
- [ ] O indicador visível mostra que há um shell aberto.
- [ ] Testes cobrindo o encerramento por todos os caminhos (fim normal, queda, `stop()`).

### Dependências (fazer antes)

1. **Indicador visível e kill switch** (eliminatórios; o terminal depende de ambos).
2. **Thread de trabalho para o EXEC e `lock` nas escritas do socket.**
3. Idealmente, **TLS**: um terminal remoto em texto claro é pior do que o `exec` pontual.

### Arquivos envolvidos

- `src/agent/handlers/exec_handler.py` (base atual)
- `src/agent/command_executer_thread.py` (vazio, previsto para isto)
- `src/agent/session.py` (`_dispatch` síncrono)
- `src/shared/sender.py` (`send_msg(..., lock=)`)
- `src/shared/protocol.py` (novas ações)
- `src/controller/connection.py` (já entrega eventos em `incoming`)
- `src/controller/controller.py` (CLI: precisaria de um modo interativo)

---

## Modelo para novos itens

```
## DT-NN — Título curto

**Registrado em:** AAAA-MM-DD · **Prioridade:** alta/média/baixa · **Exigido pela especificação?** Sim/Não

### O que se quer
### Como é hoje
### Por que é dívida
### Critérios de pronto
### Arquivos envolvidos
```
