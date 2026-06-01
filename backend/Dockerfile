FROM python:3.11-slim

WORKDIR /app

# Dependências primeiro (melhor cache de camadas)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código da aplicação
COPY src/ ./src/

ENV PYTHONUNBUFFERED=1
# Persiste a configuração por sala em volume (RF05/T07)
ENV CONFIG_PATH=/app/data/config.json

# Porta da interface web de configuração
EXPOSE 5000

WORKDIR /app/src
CMD ["python", "main.py"]
