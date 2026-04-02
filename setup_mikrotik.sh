#!/usr/bin/env bash
# setup_mikrotik.sh — Configura el router MikroTik per a wifi_off
#
# Què fa:
#   1. Activa el servei API (port 8728)
#   2. Crea el grup wifi-control amb les polítiques mínimes
#   3. Crea l'usuari wifiapp
#   4. (Opcional) Restringeix l'API a una IP específica
#   5. Verifica que el port 8728 és accessible
#
# Requisits a la màquina local:
#   - ssh (client OpenSSH)
#   - nc (netcat) per verificar el port

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[AVÍS]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; }

# ── Paràmetres ────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════"
echo "  Configuració MikroTik per a wifi_off"
echo "════════════════════════════════════════════════"
echo ""

read -rp "IP del router MikroTik [192.168.88.1]: " ROUTER_IP
ROUTER_IP="${ROUTER_IP:-192.168.88.1}"

read -rp "Usuari administrador del router [admin]: " ADMIN_USER
ADMIN_USER="${ADMIN_USER:-admin}"

read -rsp "Contrasenya de l'administrador (pot estar buida): " ADMIN_PASS
echo ""

read -rp "Nom del nou usuari per a l'app [wifiapp]: " APP_USER
APP_USER="${APP_USER:-wifiapp}"

while true; do
    read -rsp "Contrasenya per a '${APP_USER}': " APP_PASS
    echo ""
    read -rsp "Repeteix la contrasenya: " APP_PASS2
    echo ""
    if [ "$APP_PASS" = "$APP_PASS2" ]; then
        break
    fi
    err "Les contrasenyes no coincideixen. Torna-ho a intentar."
done

read -rp "Restringir l'API a una IP específica? (deixa buit per no restringir): " ALLOWED_IP

echo ""
echo "────────────────────────────────────────────────"
echo "  Resum de la configuració"
echo "────────────────────────────────────────────────"
echo "  Router:       ${ROUTER_IP}"
echo "  Admin:        ${ADMIN_USER}"
echo "  Nou usuari:   ${APP_USER}"
if [ -n "$ALLOWED_IP" ]; then
    echo "  IP permesa:   ${ALLOWED_IP}/32"
fi
echo "────────────────────────────────────────────────"
read -rp "Continuar? [s/N]: " CONFIRM
if [[ ! "$CONFIRM" =~ ^[sS]$ ]]; then
    echo "Cancel·lat."
    exit 0
fi

# ── Funció per executar comandes al router via SSH ────────────────────────────
# MikroTik accepta SSH però no té un shell POSIX; cada línia és una comanda RouterOS.
# Usem BatchMode=yes per evitar prompts interactius i StrictHostKeyChecking=no
# per a la primera connexió (entorn de xarxa local de confiança).
run_router() {
    local cmd="$1"
    ssh \
        -o BatchMode=no \
        -o StrictHostKeyChecking=no \
        -o ConnectTimeout=10 \
        "${ADMIN_USER}@${ROUTER_IP}" \
        "$cmd"
}

# ── 1. Verificar connectivitat SSH ───────────────────────────────────────────
echo ""
echo "▶ Verificant connectivitat SSH al router..."

if ! ssh \
    -o BatchMode=no \
    -o StrictHostKeyChecking=no \
    -o ConnectTimeout=10 \
    "${ADMIN_USER}@${ROUTER_IP}" \
    "/system identity print" > /dev/null 2>&1; then
    err "No s'ha pogut connectar per SSH a ${ROUTER_IP} amb l'usuari '${ADMIN_USER}'."
    err "Comprova la IP, l'usuari i que SSH estigui activat al router (IP → Services → ssh)."
    exit 1
fi
ok "Connexió SSH establerta."

# ── 2. Activar el servei API ──────────────────────────────────────────────────
echo ""
echo "▶ Activant el servei API (port 8728)..."
run_router "/ip service enable api"
ok "Servei API activat."

# ── 3. Crear el grup wifi-control ─────────────────────────────────────────────
echo ""
echo "▶ Creant el grup 'wifi-control'..."

# Si el grup ja existeix, RouterOS retorna un error; l'ignorem amb || true
run_router "/user group add name=wifi-control policy=read,write,api" 2>/dev/null || \
    warn "El grup 'wifi-control' ja existia (s'omete la creació)."
ok "Grup 'wifi-control' llest."

# ── 4. Crear l'usuari de l'app ────────────────────────────────────────────────
echo ""
echo "▶ Creant l'usuari '${APP_USER}'..."

run_router "/user add name=${APP_USER} password=${APP_PASS} group=wifi-control" 2>/dev/null || \
    warn "L'usuari '${APP_USER}' ja existia. Actualitzant la contrasenya..."
    run_router "/user set [find name=${APP_USER}] password=${APP_PASS}" 2>/dev/null || true

ok "Usuari '${APP_USER}' llest."

# ── 5. Restringir l'API per IP (opcional) ────────────────────────────────────
if [ -n "$ALLOWED_IP" ]; then
    echo ""
    echo "▶ Restringint l'API a ${ALLOWED_IP}/32..."
    run_router "/ip service set api address=${ALLOWED_IP}/32"
    ok "Accés a l'API restringit a ${ALLOWED_IP}/32."
fi

# ── 6. Verificar que el port 8728 és accessible ───────────────────────────────
echo ""
echo "▶ Verificant que el port 8728 és accessible..."

if nc -zv -w 5 "${ROUTER_IP}" 8728 2>/dev/null; then
    ok "Port 8728 accessible."
else
    warn "No s'ha pogut verificar el port 8728 amb nc."
    warn "Comprova manualment: nc -zv ${ROUTER_IP} 8728"
fi

# ── 7. Generar el fitxer .env ─────────────────────────────────────────────────
echo ""
echo "▶ Generant fitxer .env..."

ENV_FILE="$(dirname "$0")/.env"

if [ -f "$ENV_FILE" ]; then
    warn ".env ja existeix. Es guarda una còpia a .env.bak"
    cp "$ENV_FILE" "${ENV_FILE}.bak"
fi

SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || \
         od -An -tx1 /dev/urandom | tr -d ' \n' | head -c 64)

cat > "$ENV_FILE" <<EOF
MIKROTIK_HOST=${ROUTER_IP}
MIKROTIK_USER=${APP_USER}
MIKROTIK_PASSWORD=${APP_PASS}
SECRET_KEY=${SECRET}
EOF

ok ".env creat amb SECRET_KEY generat aleatòriament."

# ── Resum final ───────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════"
echo -e "${GREEN}  Configuració completada!${NC}"
echo "════════════════════════════════════════════════"
echo ""
echo "  Pròxims passos:"
echo "    docker compose up -d --build"
echo "    curl http://localhost:5000/status"
echo ""
