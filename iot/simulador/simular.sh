#!/usr/bin/env bash
# Simula a ESP32 sem hardware: a lata do PEV enche de 10% a 90% e esvazia.
#
# Uso:
#   SENSOR_TOKEN=... ./simular.sh
#   LOGINC_URL=http://127.0.0.1:8770 PEV=v1 ./simular.sh
#
# Sem SENSOR_TOKEN, lê o token de iot/pev_esp32/segredos.h.
# Abra <LOGINC_URL>/pev em outra aba para ver a lata enchendo ao vivo.
set -euo pipefail
cd "$(dirname "$0")"
BASE=${LOGINC_URL:-https://loginc.tailb54547.ts.net}
TOKEN=${SENSOR_TOKEN:-$(sed -n 's/^#define SENSOR_TOKEN "\(.*\)"/\1/p' ../pev_esp32/segredos.h 2>/dev/null || true)}
if [ -z "$TOKEN" ]; then
  echo "Defina SENSOR_TOKEN ou crie iot/pev_esp32/segredos.h a partir do segredos.exemplo.h" >&2
  exit 1
fi
PEV=${PEV:-v2}; ALTURA=30
for cm in 27 24 21 18 15 12 9 6 3 3 3 27; do
  printf '%5s cm -> ' "$cm"
  curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "{\"pev\":\"$PEV\",\"distancia_cm\":$cm,\"altura_cm\":$ALTURA}" "$BASE/api/sensor"; echo
  sleep 1.5
done
