# 🧪 Plano de Testes — Sistema IoT de Automação de Iluminação (Jolteon)

Validação funcional e não funcional do sistema descrito no artigo *"Sistema IoT de
Automação e Eficiência Energética para Ambientes Institucionais"*. Cada caso de teste
é rastreável a um requisito (RF/RNF) e aos testes T01–T08 do artigo.

> Preencha a coluna **Resultado** (✅ Passou / ❌ Falhou) e as **Evidências** (print, log,
> medição) à medida que executar.

---

## 1. Ambiente de teste

| Camada | Item | Endereço / Detalhe |
|---|---|---|
| Borda | ESP32-C6 com firmware gravado (ou simulação via `mosquitto_pub`) | porta `COM3` |
| Broker | Mosquitto | `localhost:1883` (container `iot_mosquitto`) |
| Banco | InfluxDB | http://localhost:8086 (`gestor` / `senha_segura_123`) |
| Dashboard | Grafana | http://localhost:3000 (`admin` / `admin`) |
| Backend | Orquestrador Python | container `iot_backend` |
| Config | Interface web | http://localhost:5000 |
| Notificação | Bot do Telegram | conversa com o `GESTOR_CHAT_ID` |

**Subir tudo:**
```powershell
cd C:\Repositorios\Jolteon\infrastructure
docker compose up -d --build
docker compose logs -f backend   # acompanhar os logs do orquestrador
```

### Convenções
| Tópico MQTT | Payload | Significado |
|---|---|---|
| `sala/101/ocupacao` | `1` / `0` | movimento detectado / ausência |
| `sala/101/luminosidade` | `1` / `0` | ambiente claro / escuro |
| `sala/101/comando` | `ON` / `OFF` | comando remoto para o relé |
| `sala/101/rele` | `ON` / `OFF` | confirmação do estado do relé (publicado pelo ESP32) |

**Modo acelerado:** para não esperar os 10 min reais, em http://localhost:5000 ajuste a
Sala 101 para `timeout_ausencia = 15` e `timeout_resposta = 15` (segundos). Para o
failsafe local do firmware, recompile com `#define TIMEOUT_AUSENCIA_S 20`.

**Terminais auxiliares (deixe abertos durante os testes):**
```powershell
# Terminal 1 — espião de TODO o tráfego MQTT
docker exec -it iot_mosquitto mosquitto_sub -t "sala/#" -v

# Terminal 2 — para injetar eventos manualmente (simula o ESP32)
# (usado nos passos dos casos abaixo)
```

---

## 2. Matriz de rastreabilidade

| Requisito (artigo) | Casos de teste | Teste do artigo |
|---|---|---|
| RF01 — Detectar ausência pelo período configurado | C1, A4 | T02 |
| RF02 — Notificar via Telegram com opções de ação | C1, C2 | T02 |
| RF03 — Desligar remotamente via botão inline | C3, C4, A5 | T03 |
| RF04 — Timeout automático sem resposta | C5 | T04 |
| RF05 — Ajuste do tempo de ausência via interface web | D1, D2, D3 | T07 |
| RNF01 — Latência detecção→notificação < 60s | E1 | T02 |
| RNF02 — Disponibilidade / uptime | E3 | — |
| RNF03 — Conformidade LGPD (só estado binário) | E2 | — |
| Failsafe local / autonomia da borda | A4, E3 | T04 |
| Reconexão Wi-Fi | A6 | T05 |
| Regra escuro + presença | A2 | — |
| Persistência de dados | B1, B2 | T01 |

---

## 3. Casos de teste

### Grupo A — Firmware (camada de borda) · monitor serial

| ID | Objetivo | Passos | Resultado esperado | Resultado |
|---|---|---|---|---|
| **A1** (T01) | Detecção de presença | Acionar o PIR (movimento na frente do sensor) | Log `Movimento detectado!`; publica `sala/101/ocupacao 1` | [ ] |
| **A2** | Regra **escuro + presença** | (a) Presença **+ LDR escuro** (cobrir o sensor); (b) presença **+ LDR claro** | (a) `Relé -> LIGADO`; (b) relé **permanece DESLIGADO** mesmo com presença | [ ] |
| **A4** (T04) | **Failsafe local** (independe do backend) | **Desligar o Wi-Fi/broker**; gerar presença; depois deixar a sala sem movimento por `TIMEOUT_AUSENCIA_S` | Log `Failsafe local: ... Desligando carga`; relé desliga **sem** o servidor | [ ] |
| **A5** (RF03) | Comando remoto chega ao relé | Com MQTT conectado: `mosquitto_pub -t sala/101/comando -m OFF` e depois `-m ON` | Log `Comando recebido ... OFF/ON`; relé acompanha; publica `sala/101/rele` | [ ] |
| **A6** (T05) | Reconexão Wi-Fi | Derrubar o roteador/broker por ~2 min e religar | Log `Wi-Fi caiu. Reconectando...` e depois reconexão; **relé mantém o estado** | [ ] |

> Comandos do Grupo A (Terminal 2), quando o teste pede publicar:
> ```powershell
> docker exec -it iot_mosquitto mosquitto_pub -t "sala/101/comando" -m "OFF"
> docker exec -it iot_mosquitto mosquitto_pub -t "sala/101/comando" -m "ON"
> ```

### Grupo B — Integração MQTT + persistência

| ID | Objetivo | Passos | Resultado esperado | Resultado |
|---|---|---|---|---|
| **B1** | ESP32 publica eventos | Gerar movimento e variar luminosidade; observar o Terminal 1 (`mosquitto_sub`) | Aparecem `sala/101/ocupacao` e `sala/101/luminosidade` com `0/1` (só na mudança) | [ ] |
| **B2** (T01) | Backend grava no InfluxDB | Após B1, abrir InfluxDB → Data Explorer → bucket `energia_salas` | Existem séries `sensor_pir` (campo `movimento`) e `sensor_ldr` (campo `luminosidade`) | [ ] |

### Grupo C — Backend / Telegram

> Pré-condição: backend rodando, Sala 101 em **modo acelerado** (15/15s).
> Sem hardware, simule o ESP32 publicando os eventos.

| ID | Objetivo | Passos | Resultado esperado | Resultado |
|---|---|---|---|---|
| **C1** (T02/RF01-02) | Alerta por ausência | Publicar `sala/101/ocupacao 1` (sala ocupada) e depois `sala/101/ocupacao 0`; aguardar ~15s | Chega mensagem no Telegram com **nome da sala**, **tempo** e botões **💡 Ligar / 🛑 Desligar** | [ ] |
| **C2** (RNF01) | Conteúdo/latência do alerta | Cronometrar do fim do timeout até a mensagem | Mensagem em **< 60s**; texto cita a sala e o tempo de ausência | [ ] |
| **C3** (T03/RF03) | Desligamento remoto | No alerta do C1, clicar **🛑 Desligar** | Terminal 1 mostra `sala/101/comando OFF`; mensagem editada para "DESLIGADA remotamente" | [ ] |
| **C4** (RF03) | Religamento remoto | Repetir C1 e clicar **💡 Ligar** | Terminal 1 mostra `sala/101/comando ON`; mensagem editada para "LIGADA remotamente" | [ ] |
| **C5** (T04/RF04) | **Auto-desligamento sem resposta** | Repetir C1 e **não clicar em nada**; aguardar +15s | Backend publica `sala/101/comando OFF` sozinho; chega aviso "DESLIGADA automaticamente" | [ ] |
| **C6** | Movimento cancela o ciclo | Provocar timeout (alerta enviado) e então publicar `sala/101/ocupacao 1` | Ciclo reinicia: **não** ocorre auto-desligamento; novo alerta só após novo timeout | [ ] |

> Simulação de ocupação/ausência (Terminal 2):
> ```powershell
> docker exec -it iot_mosquitto mosquitto_pub -t "sala/101/ocupacao" -m "1"   # ocupada
> docker exec -it iot_mosquitto mosquitto_pub -t "sala/101/ocupacao" -m "0"   # vazia
> ```

### Grupo D — Interface web (RF05 / T07)

| ID | Objetivo | Passos | Resultado esperado | Resultado |
|---|---|---|---|---|
| **D1** | Salvar configuração | Em http://localhost:5000, mudar `timeout_ausencia` da Sala 101 e clicar **Salvar** | Página recarrega com o novo valor; log `💾 Config da sala 101 atualizada` | [ ] |
| **D2** (T07) | **Persistência após reinício** | `docker compose restart backend`; reabrir a interface | O valor configurado em D1 **continua**; log `⚙️ Configuração carregada` | [ ] |
| **D3** (RF05) | Nova config entra em vigor | Após D1, rodar o ciclo do C1 | O alerta passa a respeitar o **novo** `timeout_ausencia` | [ ] |
| **D4** | Descoberta dinâmica de sala | Publicar `sala/202/ocupacao 0` (sala inexistente) | Sala `202` aparece na interface com config padrão; log `🆕 Sala '202' registrada` | [ ] |

### Grupo E — Não funcionais

| ID | Objetivo | Passos | Resultado esperado | Resultado |
|---|---|---|---|---|
| **E1** (RNF01/T02) | Latência em 10 ciclos | Repetir C1 dez vezes, cronometrando | Em **10/10**, alerta em < 60s após o fim do timeout | [ ] |
| **E2** (RNF03/LGPD) | Auditoria de privacidade | Observar o Terminal 1 durante toda a operação | **Somente** payloads binários (`0/1`, `ON/OFF`); nenhum dado pessoal trafega | [ ] |
| **E3** (RNF02) | Autonomia sem backend | `docker compose stop backend` e repetir o failsafe A4 | ESP32 **continua desligando** por timeout local; ao voltar o backend, retoma a orquestração | [ ] |
| **E4** (T08) | Consumo do nó (opcional) | Medir o ESP32 com medidor de potência USB | Consumo < 200 mW em operação | [ ] |

---

## 4. Cenário de aceite ponta a ponta (happy path)

Executar na ordem, com a Sala 101 em modo acelerado (15/15s):

1. `ocupacao 1` → relé liga (se escuro) e a sala fica "Ocupada" na web. ✔
2. `ocupacao 0` → após ~15s, **alerta no Telegram**. ✔ (C1)
3. Clicar **🛑 Desligar** → `comando OFF` no MQTT + confirmação. ✔ (C3)
4. Novo `ocupacao 0` sem clicar → após ~15s + 15s, **auto-desligamento**. ✔ (C5)
5. Conferir no InfluxDB/Grafana o histórico de eventos. ✔ (B2)
6. `restart backend` → configuração preservada. ✔ (D2)

**Critério de saída:** todos os casos de prioridade **Alta** do artigo (RF01–RF04, RNF03)
aprovados, mais D1/D2 (RF05). Os itens de hardware (A1–A6, E4) exigem a placa física.

---

## 5. Registro de execução

| Data | Versão (commit) | Executor | Casos aprovados | Casos reprovados | Observações |
|---|---|---|---|---|---|
|  |  |  |  |  |  |
