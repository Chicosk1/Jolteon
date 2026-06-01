"""
Gerenciador de configuração persistente por sala.

Atende ao RF05 / T07 do artigo: o coordenador pode ajustar o tempo de ausência
de cada sala e a configuração PERSISTE após reinício do back-end.

A configuração é gravada em um arquivo JSON (config.json), permitindo que os
valores sobrevivam a reinícios do contêiner Docker quando o arquivo é mapeado
para um volume.
"""

import os
import json
import threading

# Caminho do arquivo de configuração (mapeável para volume no Docker).
CONFIG_PATH = os.getenv(
    "CONFIG_PATH",
    os.path.join(os.path.dirname(__file__), "config.json"),
)

# Valores padrão (em segundos) aplicados a salas ainda não configuradas.
#   timeout_ausencia -> tempo sem movimento até notificar o coordenador (RF01)
#   timeout_resposta -> tempo aguardando resposta antes do desligamento auto (RF04)
DEFAULTS = {
    "timeout_ausencia": 600,  # 10 min
    "timeout_resposta": 300,  # 5 min
}


class ConfigManager:
    def __init__(self, path: str = CONFIG_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._data = {"default": dict(DEFAULTS), "salas": {}}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    carregado = json.load(f)
                self._data["default"].update(carregado.get("default", {}))
                self._data["salas"].update(carregado.get("salas", {}))
                print(f"⚙️  Configuração carregada de {self.path}")
            except Exception as e:
                print(f"⚠️  Falha ao ler config ({e}). Usando padrões.")

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"❌ Falha ao salvar configuração: {e}")

    def ensure_sala(self, sala_id: str):
        """Registra uma sala recém-descoberta com os valores padrão."""
        with self._lock:
            if sala_id not in self._data["salas"]:
                self._data["salas"][sala_id] = dict(self._data["default"])
                self._save()
                print(f"🆕 Sala '{sala_id}' registrada com configuração padrão.")

    def get_sala(self, sala_id: str) -> dict:
        """Retorna a configuração efetiva da sala (padrão se não configurada)."""
        with self._lock:
            return dict(self._data["salas"].get(sala_id, self._data["default"]))

    def set_timeout(self, sala_id: str, timeout_ausencia: int, timeout_resposta: int):
        """Atualiza e persiste os tempos da sala (chamado pela interface web)."""
        with self._lock:
            self._data["salas"][sala_id] = {
                "timeout_ausencia": int(timeout_ausencia),
                "timeout_resposta": int(timeout_resposta),
            }
            self._save()
        print(f"💾 Config da sala {sala_id} atualizada: "
              f"ausencia={timeout_ausencia}s, resposta={timeout_resposta}s")

    def all_salas(self) -> dict:
        with self._lock:
            return dict(self._data["salas"])
