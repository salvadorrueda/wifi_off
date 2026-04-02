import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import routeros_api
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "wifi-off-secret-key")

logger = logging.getLogger(__name__)

# Global state
wifi_state = {
    "disabled": False,
    "re_enable_at": None,
    "timer": None,
    "last_error": None,
}
state_lock = threading.Lock()


def _get_connection_params(form_data):
    """Return router connection parameters preferring env vars over form input."""
    host = os.getenv("MIKROTIK_HOST") or form_data.get("host", "").strip()
    username = os.getenv("MIKROTIK_USER") or form_data.get("username", "").strip()
    password = os.getenv("MIKROTIK_PASSWORD") or form_data.get("password", "")
    return host, username, password


def _set_wireless_disabled(host, username, password, disabled: bool):
    """Connect to the Mikrotik router and enable or disable all wireless interfaces."""
    pool = routeros_api.RouterOsApiPool(
        host,
        username=username,
        password=password,
        plaintext_login=True,
    )
    api = pool.get_api()
    try:
        wireless = api.get_resource("/interface/wireless")
        interfaces = wireless.get()
        for iface in interfaces:
            if disabled:
                wireless.call("disable", {"numbers": iface[".id"]})
            else:
                wireless.call("enable", {"numbers": iface[".id"]})
    finally:
        pool.disconnect()


def _re_enable_wifi(host, username, password):
    """Background thread target: re-enable WiFi and clear state."""
    try:
        _set_wireless_disabled(host, username, password, disabled=False)
    except Exception as exc:  # noqa: BLE001
        with state_lock:
            wifi_state["last_error"] = str(exc)
    finally:
        with state_lock:
            wifi_state["disabled"] = False
            wifi_state["re_enable_at"] = None
            wifi_state["timer"] = None


@app.route("/")
def index():
    env_host = os.getenv("MIKROTIK_HOST", "")
    env_user = os.getenv("MIKROTIK_USER", "")
    credentials_from_env = bool(env_host and env_user)
    return render_template(
        "index.html",
        credentials_from_env=credentials_from_env,
        env_host=env_host,
    )


@app.route("/wifi/off", methods=["POST"])
def wifi_off():
    """Disable WiFi for the specified number of minutes."""
    with state_lock:
        if wifi_state["disabled"]:
            return jsonify({"ok": False, "error": "La WiFi ja està apagada."}), 400

    try:
        minutes = int(request.form.get("minutes", 0))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Durada no vàlida."}), 400

    if minutes <= 0:
        return jsonify({"ok": False, "error": "La durada ha de ser superior a 0 minuts."}), 400

    host, username, password = _get_connection_params(request.form)
    if not host or not username:
        return jsonify({"ok": False, "error": "Falten les dades de connexió al router."}), 400

    try:
        _set_wireless_disabled(host, username, password, disabled=True)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error disabling wireless: %s", exc)
        return jsonify({"ok": False, "error": "Error connectant al router. Comprova les dades de connexió."}), 500

    re_enable_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)

    timer = threading.Timer(
        minutes * 60,
        _re_enable_wifi,
        args=(host, username, password),
    )
    timer.daemon = True
    timer.start()

    with state_lock:
        wifi_state["disabled"] = True
        wifi_state["re_enable_at"] = re_enable_at.isoformat()
        wifi_state["timer"] = timer
        wifi_state["last_error"] = None

    return jsonify({"ok": True, "re_enable_at": re_enable_at.isoformat()})


@app.route("/wifi/on", methods=["POST"])
def wifi_on():
    """Re-enable WiFi immediately."""
    with state_lock:
        if not wifi_state["disabled"]:
            return jsonify({"ok": False, "error": "La WiFi ja està encesa."}), 400
        timer = wifi_state.get("timer")

    host, username, password = _get_connection_params(request.form)
    if not host or not username:
        return jsonify({"ok": False, "error": "Falten les dades de connexió al router."}), 400

    if timer is not None:
        timer.cancel()

    try:
        _set_wireless_disabled(host, username, password, disabled=False)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error enabling wireless: %s", exc)
        return jsonify({"ok": False, "error": "Error connectant al router. Comprova les dades de connexió."}), 500

    with state_lock:
        wifi_state["disabled"] = False
        wifi_state["re_enable_at"] = None
        wifi_state["timer"] = None
        wifi_state["last_error"] = None

    return jsonify({"ok": True})


@app.route("/status")
def status():
    """Return the current WiFi state as JSON."""
    with state_lock:
        re_enable_at = wifi_state["re_enable_at"]
        remaining = None
        if re_enable_at:
            delta = datetime.fromisoformat(re_enable_at) - datetime.now(timezone.utc)
            remaining = max(0, int(delta.total_seconds()))
        return jsonify(
            {
                "disabled": wifi_state["disabled"],
                "re_enable_at": re_enable_at,
                "remaining_seconds": remaining,
                "last_error": wifi_state["last_error"],
            }
        )


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug, host="0.0.0.0", port=5000)
