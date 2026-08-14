#!/usr/bin/env bash
# Install the CoMo 911 panel: provisions its PostgreSQL role/database/schema
# (only if they don't already exist -- never destructive), installs both
# systemd fetch timers (fire/medical every 5min, police every 30min -- that
# asymmetry is deliberate, police's source feed is ~6h delayed so faster
# polling has no benefit), and copies its Lovelace dashboard + HA package
# YAML into place.
#
# Usage: ./install.sh <install-user> <repo-dir> <ha-config-dir>
# Example: ./install.sh scottgs /home/scottgs/repos/mandi-como-911 /home/scottgs/homeassistant/config
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

INSTALL_USER="${1:?Usage: install.sh <install-user> <repo-dir> <ha-config-dir>}"
REPO_DIR="${2:?Usage: install.sh <install-user> <repo-dir> <ha-config-dir>}"
HA_CONFIG_DIR="${3:?Usage: install.sh <install-user> <repo-dir> <ha-config-dir>}"

provision_db() {
  local db_password="$1"
  # Role/database existence are checked in bash, not SQL -- Postgres has no
  # CREATE ROLE/DATABASE IF NOT EXISTS, and psql's -v substitution can't
  # reach inside a DO $$ ... $$ block (the whole block lexes as one opaque
  # string), so a DO-block existence check silently can't see :variables.
  # Note: psql only interpolates -v variables when SQL arrives over stdin,
  # not via -c "...", hence the heredocs below instead of -c.
  if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='mandi_geo'" | grep -q 1; then
    sudo -u postgres psql -v mandi_geo_password="'${db_password}'" <<'SQL'
CREATE ROLE mandi_geo WITH LOGIN PASSWORD :mandi_geo_password;
SQL
  fi
  if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='mandi_geo'" | grep -q 1; then
    sudo -u postgres psql -c "CREATE DATABASE mandi_geo OWNER mandi_geo;"
  fi
  cat "$SCRIPT_DIR/db/schema.sql" | sudo -u postgres psql -d mandi_geo
  echo "mandi_geo database provisioned (or already present -- schema.sql is idempotent)"
}

echo "== 1/5: env file =="
sudo install -d -m 0755 /etc/mandi
if [ ! -f /etc/mandi/como-911.env ]; then
  read -rsp "MANDI_GEO_DB_PASSWORD for new role 'mandi_geo': " DB_PASSWORD; echo
  echo "MANDI_GEO_DB_PASSWORD=${DB_PASSWORD}" | sudo tee /etc/mandi/como-911.env >/dev/null
  sudo chmod 0600 /etc/mandi/como-911.env
else
  DB_PASSWORD="$(sudo grep -oP '(?<=MANDI_GEO_DB_PASSWORD=).*' /etc/mandi/como-911.env)"
  echo "/etc/mandi/como-911.env already exists, reusing its password"
fi

echo "== 2/5: database (requires postgis extension available on the target) =="
provision_db "$DB_PASSWORD"

echo "== 3/5: systemd units (fire/medical + police) =="
sed -e "s|__INSTALL_USER__|${INSTALL_USER}|g" \
    -e "s|__HA_WWW_DIR__|${HA_CONFIG_DIR}/www|g" \
    -e "s|__REPO_DIR__|${REPO_DIR}|g" \
    "$SCRIPT_DIR/systemd/columbia-911-fire-fetch.service" | sudo tee /etc/systemd/system/columbia-911-fire-fetch.service >/dev/null
sed -e "s|__INSTALL_USER__|${INSTALL_USER}|g" \
    -e "s|__HA_WWW_DIR__|${HA_CONFIG_DIR}/www|g" \
    -e "s|__REPO_DIR__|${REPO_DIR}|g" \
    "$SCRIPT_DIR/systemd/columbia-911-police-fetch.service" | sudo tee /etc/systemd/system/columbia-911-police-fetch.service >/dev/null
sudo cp "$SCRIPT_DIR/systemd/columbia-911-fire-fetch.timer" /etc/systemd/system/columbia-911-fire-fetch.timer
sudo cp "$SCRIPT_DIR/systemd/columbia-911-police-fetch.timer" /etc/systemd/system/columbia-911-police-fetch.timer
sudo systemctl daemon-reload
sudo systemctl enable --now columbia-911-fire-fetch.timer
sudo systemctl enable --now columbia-911-police-fetch.timer

echo "== 4/5: HA dashboard + package files =="
mkdir -p "${HA_CONFIG_DIR}/www/columbia_911"
cp "$SCRIPT_DIR/ha/lovelace/columbia_911.yaml" "${HA_CONFIG_DIR}/lovelace/columbia_911.yaml"
cp "$SCRIPT_DIR/ha/packages/columbia_911.yaml" "${HA_CONFIG_DIR}/packages/columbia_911.yaml"

echo "== 5/5: manual step reminder =="
cat <<'EOF'
Add this dashboard entry to homeassistant/config/configuration.yaml under
`lovelace: dashboards:` if not already present:

    columbia-911-dashboard:
      mode: yaml
      title: CoMo 911
      icon: mdi:phone
      show_in_sidebar: true
      filename: lovelace/columbia_911.yaml

Then:
    docker exec homeassistant python3 -m homeassistant --script check_config --config /config
    docker compose -f ~/homeassistant/docker-compose.yaml restart homeassistant

Note: the fire/medical fetch script requires the `pyproj` Python package
(coordinate transform from Missouri State Plane to WGS84) -- confirm it's
importable by the install user before relying on the timer.
EOF
