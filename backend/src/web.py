"""
Interface web de configuração (RF05).

Servidor Flask leve que permite ao coordenador ajustar, por sala, o tempo de
ausência (até notificar) e o tempo de resposta (até desligar automaticamente).
Roda em uma thread separada do orquestrador, compartilhando o ConfigManager e o
dicionário de estado em tempo real das salas.

Critério de aceite (T07): a nova configuração persiste após reinício e entra em
vigor na próxima verificação de timeout.
"""

import threading
from flask import Flask, request, redirect, url_for, render_template_string

PAGINA = """
<!doctype html>
<html lang="pt-br">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Configuração — Automação de Iluminação</title>
  <style>
    body { font-family: system-ui, Arial, sans-serif; background:#0f172a; color:#e2e8f0; margin:0; padding:2rem; }
    h1 { font-size:1.4rem; }
    .card { background:#1e293b; border-radius:12px; padding:1.2rem 1.4rem; margin-bottom:1rem; box-shadow:0 2px 8px rgba(0,0,0,.3); }
    .titulo-sala { font-size:1.1rem; font-weight:600; margin-bottom:.6rem; }
    .status { display:inline-block; padding:.15rem .6rem; border-radius:999px; font-size:.8rem; }
    .ocupada { background:#166534; color:#dcfce7; }
    .vazia { background:#7f1d1d; color:#fee2e2; }
    label { display:block; font-size:.85rem; margin:.5rem 0 .2rem; color:#94a3b8; }
    input[type=number] { width:120px; padding:.4rem; border-radius:6px; border:1px solid #334155; background:#0f172a; color:#e2e8f0; }
    button { margin-top:.8rem; background:#2563eb; color:#fff; border:none; padding:.5rem 1rem; border-radius:6px; cursor:pointer; }
    button:hover { background:#1d4ed8; }
    .row { display:flex; gap:1.5rem; flex-wrap:wrap; }
    small { color:#64748b; }
  </style>
</head>
<body>
  <h1>⚙️ Configuração de Salas — Eficiência Energética</h1>
  <p><small>Ajuste os tempos por sala. As alterações são salvas e persistem após reinício (RF05).</small></p>

  {% for sala_id, info in salas.items() %}
  <div class="card">
    <div class="titulo-sala">
      Sala {{ sala_id }}
      {% if info.ocupada %}<span class="status ocupada">● Ocupada</span>
      {% else %}<span class="status vazia">○ Vazia</span>{% endif %}
    </div>
    <form method="post" action="{{ url_for('atualizar') }}">
      <input type="hidden" name="sala_id" value="{{ sala_id }}">
      <div class="row">
        <div>
          <label>Tempo de ausência até notificar (segundos)</label>
          <input type="number" name="timeout_ausencia" min="5" value="{{ info.timeout_ausencia }}">
        </div>
        <div>
          <label>Tempo de resposta até desligar auto (segundos)</label>
          <input type="number" name="timeout_resposta" min="5" value="{{ info.timeout_resposta }}">
        </div>
      </div>
      <button type="submit">Salvar</button>
    </form>
  </div>
  {% else %}
  <div class="card">Nenhuma sala detectada ainda. Aguardando eventos MQTT do ESP32...</div>
  {% endfor %}
</body>
</html>
"""


def criar_app(config_manager, salas, salas_lock):
    app = Flask(__name__)

    def _montar_contexto():
        contexto = {}
        with salas_lock:
            ids = set(salas.keys()) | set(config_manager.all_salas().keys())
            for sala_id in sorted(ids):
                cfg = config_manager.get_sala(sala_id)
                runtime = salas.get(sala_id, {})
                contexto[sala_id] = {
                    "timeout_ausencia": cfg["timeout_ausencia"],
                    "timeout_resposta": cfg["timeout_resposta"],
                    "ocupada": runtime.get("ocupada", False),
                }
        return contexto

    @app.route("/")
    def index():
        return render_template_string(PAGINA, salas=_montar_contexto())

    @app.route("/atualizar", methods=["POST"])
    def atualizar():
        sala_id = request.form["sala_id"]
        config_manager.set_timeout(
            sala_id,
            request.form.get("timeout_ausencia", 600),
            request.form.get("timeout_resposta", 300),
        )
        return redirect(url_for("index"))

    @app.route("/api/status")
    def api_status():
        return _montar_contexto()

    return app


def iniciar_web(config_manager, salas, salas_lock, host="0.0.0.0", port=5000):
    """Inicia o servidor web em uma thread daemon."""
    app = criar_app(config_manager, salas, salas_lock)

    def _run():
        app.run(host=host, port=port, debug=False, use_reloader=False)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    print(f"🌐 Interface web de configuração em http://localhost:{port}")
