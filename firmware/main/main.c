#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "esp_wifi.h"
#include "esp_timer.h"
#include "mqtt_client.h"

// ===================== Configuração =====================
#define WIFI_SSID       "NOME_DA_SUA_REDE_WIFI"
#define WIFI_PASS       "SENHA_DA_REDE"
#define BROKER_URL      "mqtt://192.168.56.1"

// Identificador da sala onde este nó está instalado.
// Compõe os tópicos MQTT: sala/<SALA_ID>/...
#define SALA_ID         "101"

#define PIR_SENSOR_PIN    2 // Entrada: Sensor de movimento HC-SR501
#define LIGHT_SENSOR_PIN  3 // Entrada: Sensor de luminosidade LDR (1 = claro, 0 = escuro)
#define RELAY_LIGHT_PIN   4 // Saída: Relé de Iluminação (SSR)

// Failsafe local (RF04): tempo, em segundos, que a sala pode permanecer sem
// movimento antes do ESP32 desligar a carga de forma AUTÔNOMA, independente do
// back-end. É o mecanismo de robustez descrito na Seção 4 do artigo.
// Valor de produção sugerido: 600 s (10 min). Reduza para testes em bancada.
#define TIMEOUT_AUSENCIA_S   600

// Período de amostragem da máquina de estados.
#define LOOP_DELAY_MS        500

// ===================== Estado global =====================
static const char *TAG = "SISTEMA_EDGE";

esp_mqtt_client_handle_t client = NULL;
static bool mqtt_conectado = false;

// Máquina de estados da camada de borda (Seção 3.2 / 4.1 do artigo).
typedef enum {
    ESTADO_OCUPADA,            // Movimento detectado: carga mantida.
    ESTADO_AUSENCIA_DETECTADA, // Sem movimento: aguardando timeout local.
    ESTADO_DESLIGAMENTO        // Carga desativada (por timeout ou comando remoto).
} estado_sala_t;

static estado_sala_t estado_atual = ESTADO_DESLIGAMENTO;
static int relay_state = 0;            // 0 = desligado, 1 = ligado
static int64_t ultimo_movimento_us = 0; // marca de tempo do último movimento

// ===================== Atuação no relé =====================
static void set_relay(int ligar) {
    if (relay_state == ligar) {
        return; // sem mudança, evita re-publicar/log redundante
    }
    relay_state = ligar;
    gpio_set_level(RELAY_LIGHT_PIN, ligar);
    ESP_LOGI(TAG, "Relé de iluminação -> %s", ligar ? "LIGADO" : "DESLIGADO");

    // Publica o estado do relé para o back-end (rastreabilidade / confirmação RF03).
    if (mqtt_conectado && client) {
        esp_mqtt_client_publish(client, "sala/" SALA_ID "/rele",
                                ligar ? "ON" : "OFF", 0, 1, 1);
    }
}

// ===================== MQTT =====================
static void mqtt_event_handler(void *handler_args, esp_event_base_t base,
                               int32_t event_id, void *event_data) {
    esp_mqtt_event_handle_t event = event_data;

    switch ((esp_mqtt_event_id_t)event_id) {
    case MQTT_EVENT_CONNECTED:
        mqtt_conectado = true;
        ESP_LOGI(TAG, "✅ Conectado ao Broker MQTT.");
        // Assina o tópico de comando remoto (RF03): ligar/desligar via Telegram.
        esp_mqtt_client_subscribe(client, "sala/" SALA_ID "/comando", 1);
        ESP_LOGI(TAG, "📡 Assinado: sala/" SALA_ID "/comando");
        break;

    case MQTT_EVENT_DISCONNECTED:
        mqtt_conectado = false;
        ESP_LOGW(TAG, "⚠️ Desconectado do Broker MQTT.");
        break;

    case MQTT_EVENT_DATA: {
        char topico[64] = {0};
        char dados[16] = {0};
        int tlen = event->topic_len < (int)sizeof(topico) - 1 ? event->topic_len : (int)sizeof(topico) - 1;
        int dlen = event->data_len  < (int)sizeof(dados)  - 1 ? event->data_len  : (int)sizeof(dados)  - 1;
        memcpy(topico, event->topic, tlen);
        memcpy(dados,  event->data,  dlen);
        ESP_LOGI(TAG, "📥 Comando recebido [%s]: %s", topico, dados);

        // Comando remoto sobrepõe a lógica local (Seção 3.2: "comando remoto").
        if (strcmp(dados, "OFF") == 0) {
            set_relay(0);
            estado_atual = ESTADO_DESLIGAMENTO;
        } else if (strcmp(dados, "ON") == 0) {
            set_relay(1);
            estado_atual = ESTADO_OCUPADA;
            ultimo_movimento_us = esp_timer_get_time(); // reinicia o contador de ausência
        }
        break;
    }

    default:
        break;
    }
}

static void mqtt_app_start(void) {
    esp_mqtt_client_config_t mqtt_cfg = {
        .broker.address.uri = BROKER_URL,
    };
    client = esp_mqtt_client_init(&mqtt_cfg);
    esp_mqtt_client_register_event(client, ESP_EVENT_ANY_ID, mqtt_event_handler, NULL);
    esp_mqtt_client_start(client);
    ESP_LOGI(TAG, "Conectando ao Broker MQTT...");
}

// ===================== Wi-Fi (com reconexão automática - T05) =====================
static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, "Wi-Fi caiu. Reconectando...");
        mqtt_conectado = false;
        esp_wifi_connect(); // reconecta automaticamente, sem perder estado do relé
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ESP_LOGI(TAG, "✅ Wi-Fi conectado. Iniciando MQTT...");
        if (client == NULL) {
            mqtt_app_start();
        }
    }
}

static void wifi_init_sta(void) {
    esp_netif_init();
    esp_event_loop_create_default();
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&cfg);

    esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                        &wifi_event_handler, NULL, NULL);
    esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                        &wifi_event_handler, NULL, NULL);

    wifi_config_t wifi_config = {
        .sta = {
            .ssid = WIFI_SSID,
            .password = WIFI_PASS,
        },
    };
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
    esp_wifi_start();
    ESP_LOGI(TAG, "Conectando ao WiFi...");
}

// ===================== GPIOs =====================
void iniciar_gpios() {
    gpio_reset_pin(RELAY_LIGHT_PIN);
    gpio_set_direction(RELAY_LIGHT_PIN, GPIO_MODE_OUTPUT);
    gpio_set_level(RELAY_LIGHT_PIN, 0);

    gpio_reset_pin(PIR_SENSOR_PIN);
    gpio_set_direction(PIR_SENSOR_PIN, GPIO_MODE_INPUT);
    gpio_set_pull_mode(PIR_SENSOR_PIN, GPIO_PULLDOWN_ONLY);

    gpio_reset_pin(LIGHT_SENSOR_PIN);
    gpio_set_direction(LIGHT_SENSOR_PIN, GPIO_MODE_INPUT);
    gpio_set_pull_mode(LIGHT_SENSOR_PIN, GPIO_PULLDOWN_ONLY);

    ESP_LOGI(TAG, "GPIOs inicializados com sucesso.");
}

// ===================== Publicação de eventos =====================
static void publicar_evento(const char *sufixo, int valor) {
    if (!mqtt_conectado || !client) {
        return;
    }
    char topico[64];
    snprintf(topico, sizeof(topico), "sala/" SALA_ID "/%s", sufixo);
    esp_mqtt_client_publish(client, topico, valor ? "1" : "0", 0, 1, 0);
}

// ===================== Programa principal =====================
void app_main(void) {
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }

    iniciar_gpios();
    wifi_init_sta(); // MQTT é iniciado automaticamente ao obter IP (ver wifi_event_handler)

    int ultimo_movimento_pub = -1;
    int ultima_luminosidade_pub = -1;
    ultimo_movimento_us = esp_timer_get_time();

    while (1) {
        int movimento    = gpio_get_level(PIR_SENSOR_PIN);
        int luminosidade = gpio_get_level(LIGHT_SENSOR_PIN); // 1 = claro, 0 = escuro

        // ---- Publicação por borda (edge): só publica quando o valor muda ----
        if (movimento != ultimo_movimento_pub) {
            ultimo_movimento_pub = movimento;
            publicar_evento("ocupacao", movimento);
            ESP_LOGI(TAG, "%s", movimento ? "Movimento detectado!" : "Sem movimento.");
        }
        if (luminosidade != ultima_luminosidade_pub) {
            ultima_luminosidade_pub = luminosidade;
            publicar_evento("luminosidade", luminosidade);
            ESP_LOGI(TAG, "%s", luminosidade ? "Ambiente claro." : "Ambiente escuro.");
        }

        // ---- Máquina de estados ----
        if (movimento) {
            // Sala ocupada: reinicia o temporizador de ausência.
            estado_atual = ESTADO_OCUPADA;
            ultimo_movimento_us = esp_timer_get_time();

            // Regra de decisão (Seção 7.1): liga a carga apenas em ambiente ESCURO.
            // Sob luz natural adequada (claro), mantém desligada mesmo com presença.
            if (luminosidade == 0) {
                set_relay(1);
            } else {
                set_relay(0);
            }
        } else {
            // Sem movimento: avalia o tempo de ausência para o failsafe local.
            int64_t ausencia_s = (esp_timer_get_time() - ultimo_movimento_us) / 1000000;

            if (ausencia_s >= TIMEOUT_AUSENCIA_S) {
                // Failsafe local (RF04): desliga autonomamente, sem depender do back-end.
                if (estado_atual != ESTADO_DESLIGAMENTO) {
                    ESP_LOGW(TAG, "Failsafe local: %d s sem movimento. Desligando carga.",
                             (int)ausencia_s);
                }
                estado_atual = ESTADO_DESLIGAMENTO;
                set_relay(0);
            } else {
                // Ausência detectada: carga mantida; back-end será notificado pelo
                // evento de ocupação=0 já publicado acima.
                estado_atual = ESTADO_AUSENCIA_DETECTADA;
            }
        }

        vTaskDelay(pdMS_TO_TICKS(LOOP_DELAY_MS));
    }
}
