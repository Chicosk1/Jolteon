# 🏫 Sistema IoT de Automação - Jolteon

Sistema embarcado de monitoramento e controle de salas, desenvolvido para reduzir o desperdício de energia elétrica detectando ocupação em tempo real e notificando gestores via Telegram.

---

## 📐 Arquitetura Geral

```
┌──────────────────────────────────────────────────────────┐
│                        HARDWARE (Edge)                   │
│                                                          │
│   [Sensor PIR] ──┐                                       │
│   [Sensor LDR] ──┤──► ESP32 (Firmware C / ESP-IDF) ──────┼──► Wi-Fi
│   [Relé]    ◄────┘                                       │
└──────────────────────────────────────────────────────────┘
                              │ MQTT (TCP/IP)
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    INFRAESTRUTURA (Docker)                  │
│                                                             │
│   ┌─────────────┐     ┌─────────────┐    ┌──────────────┐   │
│   │  Mosquitto  │     │  InfluxDB   │    │   Grafana    │   │
│   │ Broker MQTT │     │  (TSDB v2)  │    │  (Dashboard) │   │
│   │  :1883      │     │   :8086     │    │    :3000     │   │
│   └──────┬──────┘     └──────▲──────┘    └──────▲───────┘   │
└──────────┼────────────────────┼─────────────────┼───────────┘
           │ subscribe          │ write           │ query
           ▼                    │                 │
┌─────────────────────────────────────────────────────────────┐
│                    BACKEND (Python)                         │
│                                                             │
│              main.py — Orquestrador IoT                     │
│         (MQTT Client + Bot Telegram + Job Queue)            │
└────────────────────────────┬────────────────────────────────┘
                             │ HTTPS (Bot API)
                             ▼
                    ┌─────────────────┐
                    │  Bot Telegram   │
                    │  (Alertas +     │
                    │   Controle)     │
                    └─────────────────┘
```

---

## 🧩 Componentes do Sistema

### 1. 📟 Firmware — ESP32 (`/firmware`)

Desenvolvido em **C com ESP-IDF**, roda diretamente no microcontrolador.

**Responsabilidades:**
- Lê o **sensor PIR (HC-SR501)** para detectar movimento (`GPIO 2`)
- Lê o **sensor LDR** para medir luminosidade (`GPIO 3`) — `1` = claro, `0` = escuro
- Controla o **relé de iluminação / SSR** (`GPIO 4`)
- Conecta à rede Wi-Fi (com **reconexão automática**) e publica dados via MQTT
- Executa uma **máquina de estados de 3 condições** (Ocupada → Ausência Detectada → Desligamento)
- **Regra de decisão:** liga a carga apenas com *presença + ambiente escuro*; sob luz natural, mantém desligada
- **Failsafe local (RF04):** após `TIMEOUT_AUSENCIA_S` sem movimento, desliga a carga de forma **autônoma**, sem depender do back-end

**Tópicos MQTT publicados:**
| Tópico | Payload | Descrição |
|---|---|---|
| `sala/{id}/ocupacao` | `1` ou `0` | Estado do sensor de movimento |
| `sala/{id}/luminosidade` | `1` ou `0` | Estado do sensor de luz |
| `sala/{id}/rele` | `ON` / `OFF` | Confirmação do estado atual do relé |

**Tópicos MQTT assinados:**
| Tópico | Payload | Descrição |
|---|---|---|
| `sala/{id}/comando` | `ON` / `OFF` | Controle remoto do relé (sobrepõe a lógica local) |

---

### 2. 🐳 Infraestrutura — Docker (`/infrastructure`)

Toda a infraestrutura de servidores roda em **contêineres Docker** via `docker-compose.yml`.

#### 🦟 Mosquitto (Broker MQTT)
- **Imagem:** `eclipse-mosquitto:2`
- **Porta:** `1883`
- **Função:** Recebe as mensagens publicadas pelo ESP32 e as distribui para os assinantes (backend Python).
- **Configuração:** Modo anônimo habilitado (`allow_anonymous true`) — recomenda-se autenticação em produção.

#### 📦 InfluxDB v2 (Banco de Dados Time-Series)
- **Imagem:** `influxdb:2`
- **Porta:** `8086`
- **Função:** Armazena o histórico de todas as leituras dos sensores (movimento, luminosidade) com timestamps precisos.
- **Organização:** `unimater` | **Bucket:** `energia_salas`

#### 📊 Grafana (Dashboard)
- **Imagem:** `grafana/grafana:latest`
- **Porta:** `3000`
- **Função:** Visualização gráfica dos dados históricos consultados no InfluxDB. Permite criar dashboards de consumo e ocupação por sala.
- **Acesso padrão:** `admin` / `admin` *(alterar em produção)*

---

### 3. 🐍 Backend — Python (`/backend`)

Coração lógico do sistema. Roda como processo Python e integra todos os serviços.

**Arquivo principal:** `backend/src/main.py`

**Responsabilidades:**
- Conecta ao broker Mosquitto e **assina** `sala/+/ocupacao` e `sala/+/luminosidade`
- A cada mensagem recebida, **salva o dado** no InfluxDB (`sensor_pir` e `sensor_ldr`)
- **Descobre salas dinamicamente** ao receber o primeiro evento de cada uma
- Executa um **job periódico** com duas fases de timeout, **configuráveis por sala**:
  - **Fase 1 (RF01/RF02):** sala vazia além de `timeout_ausencia` → envia alerta no Telegram com botões
  - **Fase 2 (RF04):** sem resposta do coordenador em `timeout_resposta` → **desliga automaticamente**
- Ao receber um clique nos botões, **publica comando MQTT** (`ON`/`OFF`) de volta para o ESP32
- Expõe uma **interface web de configuração** (RF05) para ajustar os tempos por sala, **persistidos** em `config.json`

**Módulos:**
| Arquivo | Responsabilidade |
|---|---|
| `main.py` | Orquestrador (MQTT + Telegram + InfluxDB + jobs de timeout) |
| `config_manager.py` | Configuração persistente por sala (RF05/T07) |
| `web.py` | Interface web Flask de configuração (porta `5000`) |

---

### 4. 🤖 Bot Telegram

O bot é o canal de comunicação entre o sistema e o gestor do prédio.

**Fluxo de um alerta:**

```
Sala vazia por > 30s
       │
       ▼
Backend detecta timeout
       │
       ▼
Bot envia mensagem ao gestor:
  "⚠️ Sala 101 está vazia há X segundos."
  [ 💡 Ligar ]  [ 🛑 Desligar ]
       │
       ▼
Gestor clica em um botão
       │
       ▼
Backend publica MQTT: sala/101/comando → "OFF"
       │
       ▼
ESP32 recebe e aciona o relé
```

**Configuração necessária:**
1. Criar um bot no [@BotFather](https://t.me/BotFather) e obter o `TELEGRAM_TOKEN`
2. Iniciar o bot e usar o comando `/start` para obter o `GESTOR_CHAT_ID`
3. Preencher o arquivo `.env` com essas credenciais

---

## ⚙️ Configuração e Execução

### Pré-requisitos
- Docker e Docker Compose instalados
- Python 3.10+
- ESP-IDF configurado (para compilar o firmware)

### 1. Configurar variáveis de ambiente

Crie o arquivo `backend/.env`:

```env
TELEGRAM_TOKEN=seu_token_aqui
GESTOR_CHAT_ID=seu_chat_id_aqui

INFLUX_URL=http://localhost:8086
INFLUX_TOKEN=seu_token_influx
INFLUX_ORG=unimater
INFLUX_BUCKET=energia_salas
```

> No Docker, `MQTT_BROKER` e `INFLUX_URL` são sobrescritos automaticamente pelo
> `docker-compose.yml` para apontar aos nomes dos contêineres (`mosquitto`/`influxdb`).

### 2. Subir toda a stack (infra + back-end) via Docker

```bash
cd infrastructure
docker compose up -d --build
```

Isso sobe Mosquitto, InfluxDB, Grafana **e o back-end Python**. A **interface web de
configuração** fica disponível em **http://localhost:5000** (ajuste de timeouts por sala).

> **Alternativa (back-end fora do Docker, para desenvolvimento):**
> ```bash
> cd backend
> pip install -r requirements.txt
> python src/main.py
> ```

### 3. Compilar e gravar o firmware

Edite `firmware/main/main.c` com as credenciais da rede Wi-Fi e o IP do broker, depois:

```bash
cd firmware
idf.py build flash monitor
```

---

## 🧪 Testes

O plano de testes completo (executável, com comandos e mapeamento aos requisitos
RF/RNF e aos testes T01–T08 do artigo) está em **[PLANO_DE_TESTES.md](PLANO_DE_TESTES.md)**.

## 📁 Estrutura do Repositório

```
.
├── backend/
│   ├── src/
│   │   ├── main.py           # Orquestrador principal (MQTT + Telegram + InfluxDB)
│   │   ├── mqtt_client.py    # Cliente MQTT standalone (utilitário)
│   │   └── telegram_bot.py   # Bot Telegram standalone (utilitário)
│   └── requirements.txt
├── firmware/
│   └── main/
│       └── main.c            # Firmware ESP32 (ESP-IDF)
├── infrastructure/
│   ├── config/
│   │   └── mosquitto.conf    # Configuração do broker
│   └── docker-compose.yml    # Stack completa de serviços
└── README.md
```

---

## 🔮 Próximos Passos

- [ ] Implementar autenticação no broker Mosquitto (usuário/senha)
- [ ] Provisionar dashboards de consumo no Grafana (RNF02 — uptime)
- [ ] Validação física em sala-modelo e medição de payback real
- [ ] (Futuro) Climatização, integração ERP e predição de ocupação por ML