#!/usr/bin/env bash
# Reverse of install.sh. Never touches the database automatically -- dropping
# `mandi_geo` (role+database, plus its PostGIS tables) is a deliberate,
# manual, data-loss-capable step and is only printed as a reminder, not
# executed.
#
# Usage: ./uninstall.sh <ha-config-dir>
set -euo pipefail

HA_CONFIG_DIR="${1:?Usage: uninstall.sh <ha-config-dir>}"

echo "== 1/3: systemd =="
sudo systemctl disable --now columbia-911-fire-fetch.timer || true
sudo systemctl disable --now columbia-911-police-fetch.timer || true
sudo rm -f /etc/systemd/system/columbia-911-fire-fetch.service /etc/systemd/system/columbia-911-fire-fetch.timer
sudo rm -f /etc/systemd/system/columbia-911-police-fetch.service /etc/systemd/system/columbia-911-police-fetch.timer
sudo systemctl daemon-reload

echo "== 2/3: HA dashboard + package files =="
rm -f "${HA_CONFIG_DIR}/lovelace/columbia_911.yaml" "${HA_CONFIG_DIR}/packages/columbia_911.yaml"

echo "== fire/medical map card =="
rm -rf "${HA_CONFIG_DIR}/www/community/mandi-fire-medical-map"
python3 - "${HA_CONFIG_DIR}/.storage/lovelace_resources" <<'PYEOF'
import json, sys

path = sys.argv[1]
with open(path) as f:
    data = json.load(f)
before = len(data["data"]["items"])
data["data"]["items"] = [
    i for i in data["data"]["items"]
    if i["url"] != "/local/community/mandi-fire-medical-map/mandi-fire-medical-map-card.js"
]
if len(data["data"]["items"]) != before:
    with open(path, "w") as f:
        json.dump(data, f)
    print("removed fire/medical map card lovelace resource")
PYEOF

echo "== 3/3: manual step reminders =="
cat <<'EOF'
Remove the `columbia-911-dashboard` block from homeassistant/config/configuration.yaml,
then:
    docker exec homeassistant python3 -m homeassistant --script check_config --config /config
    docker compose -f ~/homeassistant/docker-compose.yaml restart homeassistant

The `mandi_geo` database and role were left in place -- this script never
drops data automatically. To remove them yourself:
    sudo -u postgres psql -c "DROP DATABASE mandi_geo;"
    sudo -u postgres psql -c "DROP ROLE mandi_geo;"
    sudo rm -f /etc/mandi/como-911.env
EOF
