import os
import time
import threading
import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

from config_manager import ConfigManager
from web import iniciar_web

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("GESTOR_CHAT_ID")

# Em Docker, o broker é o serviço "mosquitto"; localmente, "localhost".
BROKER = os.getenv("MQTT_BROKER", "localhost")
PORT = int(os.getenv("MQTT_PORT", "1883"))
WEB_PORT = int(os.getenv("WEB_PORT", "5000"))

INFLUX_URL    = os.getenv("INFLUX_URL")
INFLUX_TOKEN  = os.getenv("INFLUX_TOKEN")
INFLUX_ORG    = os.getenv("INFLUX_ORG")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET")

# --- InfluxDB ---
write_api = None
try:
    db_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = db_client.write_api(write_options=SYNCHRONOUS)
    print("✅ InfluxDB Configurado!")
except Exception as e:
    print(f"❌ Erro no InfluxDB: {e}")

# --- Configuração persistente por sala (RF05) ---
config = ConfigManager()

# --- Estado em tempo real das salas (compartilhado com a interface web) ---
# Protegido por lock pois é acessado por 3 threads: callback MQTT, job do
# Telegram (asyncio) e servidor web Flask.
salas = {}
salas_lock = threading.Lock()


def _registrar_sala(sala_id: str):
    """Garante que a sala exista no estado em memória e na configuração."""
    config.ensure_sala(sala_id)
    with salas_lock:
        if sala_id not in salas:
            salas[sala_id] = {
                "ultimo_movimento": time.time(),
                "ocupada": False,
                "alerta_enviado": False,
                "alerta_ts": None,
                "auto_desligado": False,
                "ultima_luminosidade": None,
            }


def _salvar_influx(measurement: str, sala_id: str, campo: str, valor: int):
    if write_api is None:
        return
    try:
        ponto = Point(measurement).tag("sala", sala_id).field(campo, valor)
        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=ponto)
        print(f"💾 {measurement}: Sala {sala_id} | {campo}={valor}")
    except Exception as e:
        print(f"❌ Erro ao salvar no banco: {e}")


mqtt_client = mqtt.Client("Backend_Orquestrador")


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("✅ MQTT Conectado com sucesso!")
        client.subscribe("sala/+/ocupacao")
        client.subscribe("sala/+/luminosidade")
        print("📡 Escutando: sala/+/ocupacao e sala/+/luminosidade")
    else:
        print(f"❌ Falha ao conectar no MQTT (rc={rc})")


def on_message(client, userdata, msg):
    topico = msg.topic
    payload = msg.payload.decode("utf-8")
    print(f"📡 MQTT Recebido -> {topico}: {payload}")

    partes = topico.split("/")
    if len(partes) != 3:
        return
    _, sala_id, tipo = partes
    _registrar_sala(sala_id)

    valor = int(payload) if payload.isdigit() else (1 if payload.lower() == "true" else 0)

    if tipo == "ocupacao":
        _salvar_influx("sensor_pir", sala_id, "movimento", valor)
        with salas_lock:
            salas[sala_id]["ocupada"] = bool(valor)
            if valor == 1:
                # Movimento: reinicia o ciclo de ausência e cancela alertas pendentes.
                salas[sala_id]["ultimo_movimento"] = time.time()
                salas[sala_id]["alerta_enviado"] = False
                salas[sala_id]["alerta_ts"] = None
                salas[sala_id]["auto_desligado"] = False

    elif tipo == "luminosidade":
        _salvar_influx("sensor_ldr", sala_id, "luminosidade", valor)
        with salas_lock:
            salas[sala_id]["ultima_luminosidade"] = valor


async def checar_timeouts(context):
    """Job periódico: implementa as duas fases de timeout (RF01 e RF04)."""
    agora = time.time()

    with salas_lock:
        itens = list(salas.items())

    for sala_id, dados in itens:
        cfg = config.get_sala(sala_id)
        timeout_ausencia = cfg["timeout_ausencia"]
        timeout_resposta = cfg["timeout_resposta"]
        tempo_vazia = agora - dados["ultimo_movimento"]

        # Fase 1 (RF01/RF02): sala vazia além do limite -> notifica o coordenador.
        if tempo_vazia > timeout_ausencia and not dados["alerta_enviado"]:
            print(f"⚠️ Sala {sala_id} vazia há {int(tempo_vazia)}s. Notificando coordenador...")
            keyboard = [[
                InlineKeyboardButton("💡 Ligar", callback_data=f"ligar_{sala_id}"),
                InlineKeyboardButton("🛑 Desligar", callback_data=f"desligar_{sala_id}"),
            ]]
            mensagem = (
                f"⚠️ *Alerta:* A Sala {sala_id} está vazia há "
                f"{int(tempo_vazia // 60)} min. Deseja desligar a iluminação?\n\n"
                f"_Sem resposta em {timeout_resposta // 60} min, o sistema desliga automaticamente._"
            )
            try:
                await context.bot.send_message(
                    chat_id=CHAT_ID,
                    text=mensagem,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown",
                )
                with salas_lock:
                    salas[sala_id]["alerta_enviado"] = True
                    salas[sala_id]["alerta_ts"] = agora
            except Exception as e:
                print(f"❌ Erro ao enviar alerta no Telegram: {e}")

        # Fase 2 (RF04): alerta enviado e sem resposta -> desligamento automático.
        elif dados["alerta_enviado"] and not dados["auto_desligado"] and dados["alerta_ts"]:
            if (agora - dados["alerta_ts"]) > timeout_resposta:
                print(f"⏱️ Sala {sala_id} sem resposta. Desligamento automático (RF04).")
                mqtt_client.publish(f"sala/{sala_id}/comando", "OFF")
                with salas_lock:
                    salas[sala_id]["auto_desligado"] = True
                try:
                    await context.bot.send_message(
                        chat_id=CHAT_ID,
                        text=(f"🛑 *Sala {sala_id}:* sem resposta do coordenador. "
                              f"Iluminação DESLIGADA automaticamente pelo sistema."),
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    print(f"❌ Erro ao notificar desligamento automático: {e}")


async def button_callback(update, context):
    query = update.callback_query
    await query.answer()

    acao, sala_id = query.data.split("_")

    if acao == "desligar":
        mqtt_client.publish(f"sala/{sala_id}/comando", "OFF")
        novo_texto = f"🛑 *Ação executada:* Iluminação da Sala {sala_id} DESLIGADA remotamente."
        with salas_lock:
            if sala_id in salas:
                salas[sala_id]["auto_desligado"] = True
    else:  # ligar
        mqtt_client.publish(f"sala/{sala_id}/comando", "ON")
        novo_texto = f"💡 *Ação executada:* Iluminação da Sala {sala_id} LIGADA remotamente."
        with salas_lock:
            if sala_id in salas:
                salas[sala_id]["ultimo_movimento"] = time.time()
                salas[sala_id]["alerta_enviado"] = False
                salas[sala_id]["alerta_ts"] = None
                salas[sala_id]["auto_desligado"] = False

    await query.edit_message_text(text=novo_texto, parse_mode="Markdown")


def iniciar_orquestrador():
    if not TOKEN or not CHAT_ID:
        print("❌ Erro fatal: Verifique seu arquivo .env! Estão faltando credenciais.")
        return

    # Interface web de configuração (RF05), em thread separada.
    iniciar_web(config, salas, salas_lock, port=WEB_PORT)

    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.connect(BROKER, PORT, 60)
    mqtt_client.loop_start()

    app = (Application.builder()
                      .token(TOKEN)
                      .read_timeout(30).write_timeout(30).connect_timeout(30)
                      .build())

    app.job_queue.run_repeating(checar_timeouts, interval=10, first=5)
    app.add_handler(CallbackQueryHandler(button_callback))

    print("🚀 Orquestrador IoT Iniciado! Monitorando as salas...")
    app.run_polling()


if __name__ == "__main__":
    iniciar_orquestrador()
