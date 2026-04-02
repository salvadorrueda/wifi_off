# Explicació tècnica pas a pas — wifi_off

> Lectura equivalent a la sortida d'un debugger de sistema: cada instrucció és explicada
> en l'ordre en què el sistema l'executa, des del `docker build` fins a la resposta HTTP.

---

## 1. Construcció del contenidor — `docker build`

Quan executem `docker compose up --build`, Docker llegeix el `Dockerfile` línia a línia i
construeix **capes** imutables apilades una sobre l'altra.

```
# syntax=docker/dockerfile:1
```
**Capa 0 — Directiva de sintaxi.**
No és una instrucció executable. Li diu al motor de build quin parser de Dockerfile usar
(la versió estable actual). Apareix abans de qualsevol instrucció real i no genera capa.

---

```dockerfile
FROM python:3.11-slim
```
**Capa 1 — Imatge base.**
Docker descarrega (o usa la còpia local a caché) la imatge oficial `python:3.11-slim` del
Docker Hub. Aquesta imatge conté:
- Debian 12 (Bookworm) minimalista (~40 MB de sistema de fitxers)
- CPython 3.11 compilat i instal·lat a `/usr/local/bin/python3.11`
- `pip` i `setuptools`

El resultat és un sistema de fitxers de base sobre el qual s'apilen les capes següents.

---

```dockerfile
RUN groupadd --gid 1000 appuser \
 && useradd --uid 1000 --gid appuser --no-create-home appuser
```
**Capa 2 — Usuari no-root.**
Executa dues comandes de shell dins del contenidor temporal:

1. `groupadd --gid 1000 appuser`
   Crea el grup `appuser` amb GID 1000 a `/etc/group`.

2. `useradd --uid 1000 --gid appuser --no-create-home appuser`
   Crea l'usuari `appuser` amb UID 1000, associat al grup anterior.
   `--no-create-home` evita crear `/home/appuser` (no necessari).

Resultat: el procés de l'aplicació s'executarà amb UID 1000, no com a `root` (UID 0).
Si un atacant aconseguís sortir de l'aplicació, no tindria privilegis de root al sistema.

---

```dockerfile
WORKDIR /app
```
**Capa 3 — Directori de treball.**
Crea el directori `/app` dins del sistema de fitxers del contenidor i estableix que totes
les instruccions posteriors (`COPY`, `RUN`, `CMD`) s'executin des d'aquest directori.
Equivalent a `mkdir -p /app && cd /app`.

---

```dockerfile
COPY requirements.txt .
```
**Capa 4 — Còpia del manifest de dependències.**
Copia `requirements.txt` de la màquina host al directori `/app/` del contenidor.
Es copia **abans** del codi font per aprofitar la caché de Docker: si el codi canvia
però `requirements.txt` no, Docker reutilitza les capes 4 i 5 (pip install) sense
tornar a descarregar res.

---

```dockerfile
RUN pip install --no-cache-dir -r requirements.txt
```
**Capa 5 — Instal·lació de dependències Python.**
`pip` llegeix `requirements.txt` i instal·la tres paquets (i les seves dependències
transitives) directament a `/usr/local/lib/python3.11/site-packages/`:

| Paquet | Versió mínima | Propòsit |
|---|---|---|
| `flask` | 2.3.0 | Framework web HTTP |
| `routeros-api` | 0.17.0 | Client de l'API de MikroTik |
| `python-dotenv` | 1.0.0 | Càrrega de fitxers `.env` |

`--no-cache-dir` evita guardar els fitxers `.whl` descarregats, mantenint la imatge més
petita.

---

```dockerfile
COPY app.py .
COPY templates/ templates/
```
**Capes 6 i 7 — Còpia del codi font.**
Copia els dos elements necessaris en runtime:
- `app.py` → `/app/app.py` (lògica del servidor)
- `templates/index.html` → `/app/templates/index.html` (interfície web)

El `.dockerignore` exclou explícitament `.env`, `tests.py`, `__pycache__`, etc., de
manera que aquests fitxers no arriben mai a la imatge.

---

```dockerfile
USER appuser
```
**Canvi d'usuari.**
A partir d'aquí, qualsevol procés que s'iniciï dins del contenidor ho farà com a
`appuser` (UID 1000), no com a `root`. Inclou el procés final de l'aplicació.

---

```dockerfile
EXPOSE 5000
```
**Metadada de port.**
Documenta que el contenidor escoltarà al port 5000. No obre el port per si sol:
l'obertura real es configura al `docker-compose.yml` amb `ports: - "5000:5000"`.

---

```dockerfile
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/status')"
```
**Comprovació de salut.**
Docker executarà aquesta comprovació cada 30 segons:
- Importa `urllib.request` de la stdlib de Python (no necessita curl ni wget).
- Fa una petició HTTP GET a `http://localhost:5000/status` dins del contenidor.
- Si la petició respon (codi HTTP 2xx), el contenidor es marca com a `healthy`.
- Si falla 3 vegades seguides, es marca com a `unhealthy`.
- `--start-period=10s`: les primeres fallades durant els 10 primers segons no compten
  per al recompte de reinicis (dóna temps a Flask per arrencar).

---

```dockerfile
CMD ["python", "app.py"]
```
**Comanda d'inici.**
Quan el contenidor s'engega, Docker executa `python /app/app.py` com a procés principal
(PID 1). Si aquest procés acaba, el contenidor s'atura.

---

## 2. Inici del programa — `python app.py`

El sistema operatiu del contenidor carrega l'intèrpret CPython i comença a executar
`app.py` de dalt a baix.

### 2.1 Importacions (línies 1–9)

```python
import logging      # Mòdul estàndard per escriure logs
import os           # Accés a variables d'entorn i sistema de fitxers
import threading    # Fils d'execució i temporitzadors
import time         # (importat però no usat directament)
from datetime import datetime, timedelta, timezone  # Càlculs de temps amb timezone

import routeros_api          # Client de l'API de MikroTik (paquet extern)
from dotenv import load_dotenv  # Càrrega del fitxer .env
from flask import Flask, jsonify, render_template, request  # Framework web
```

Python llegeix i compila cada mòdul a bytecode la primera vegada. Les importacions
de la stdlib (`logging`, `os`, `threading`) són immediates. Les externes (`routeros_api`,
`dotenv`, `flask`) es busquen a `site-packages/` on pip les va instal·lar.

### 2.2 Càrrega de variables d'entorn (línia 11)

```python
load_dotenv()
```

`python-dotenv` busca un fitxer `.env` al directori de treball (`/app/`). Si existeix,
llegeix cada línia `CLAU=VALOR` i la carrega a `os.environ` — el diccionari global de
variables d'entorn del procés. Si `.env` no existeix (habitual en producció Docker, on
es passa via `env_file` del compose), simplement no fa res.

Variables carregades (del `.env.example`):
```
MIKROTIK_HOST=192.168.88.1
MIKROTIK_USER=admin
MIKROTIK_PASSWORD=
SECRET_KEY=canvia-aquesta-clau-secreta
```

### 2.3 Creació de l'aplicació Flask (línies 13–14)

```python
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "wifi-off-secret-key")
```

1. `Flask(__name__)` crea la instància de l'aplicació web. El paràmetre `__name__`
   és el nom del mòdul actual (`"__main__"`), que Flask utilitza per trobar els
   fitxers de plantilles (`templates/`).

2. `app.secret_key` s'estableix llegint `SECRET_KEY` de `os.environ`. S'utilitza
   per signar les cookies de sessió (tot i que aquesta aplicació no en fa servir).

### 2.4 Configuració del logger (línia 16)

```python
logger = logging.getLogger(__name__)
```

Crea un logger anomenat `"__main__"`. Els missatges d'error que l'app escrigui
(`logger.error(...)`) apareixeran a `stdout` del contenidor, llegibles amb
`docker compose logs`.

### 2.5 Estat global compartit (línies 19–25)

```python
wifi_state = {
    "disabled": False,      # Estat actual: WiFi apagada?
    "re_enable_at": None,   # ISO timestamp de quan s'activarà automàticament
    "timer": None,          # Referència al threading.Timer actiu
    "last_error": None,     # Últim error de connexió al router
}
state_lock = threading.Lock()
```

`wifi_state` és un diccionari Python que viu a la memòria del procés. Tots els fils
(el fil principal de Flask i els fils de temporitzador) comparteixen aquest objecte.

`state_lock` és un **mutex**: garanteix que mai dos fils modifiquen `wifi_state`
simultàniament, evitant condicions de carrera (*race conditions*).

### 2.6 Registre de rutes (línies 71–173)

```python
@app.route("/")
@app.route("/wifi/off", methods=["POST"])
@app.route("/wifi/on",  methods=["POST"])
@app.route("/status")
```

Els decoradors `@app.route` registren funcions Python com a gestors d'URL. Flask manté
una taula de rutes interna. En aquest moment (inici del programa) no s'executa cap
funció — es registra el mapatge URL → funció.

### 2.7 Arrencada del servidor (línies 176–178)

```python
if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug, host="0.0.0.0", port=5000)
```

`if __name__ == "__main__"` és cert perquè estem executant el fitxer directament.

`app.run(host="0.0.0.0", port=5000)` arrenca el servidor HTTP de Werkzeug (el servidor
de referència de Flask):

1. Crea un **socket TCP** al port 5000.
2. `host="0.0.0.0"` significa que accepta connexions de qualsevol interfície de xarxa
   (no només `localhost`), necessari per ser accessible des de fora del contenidor.
3. El procés queda **bloquejat** en un bucle `accept()` esperant connexions entrants.
   PID 1 del contenidor ara és aquest bucle.

---

## 3. Connexió al port — com arriba una petició

```
Navegador/curl  →  Docker  →  Contenidor  →  Flask
   :PORT_HOST        NAT         :5000        Werkzeug
```

El `docker-compose.yml` té:
```yaml
ports:
  - "5000:5000"
```

Això fa que Docker creï una regla **iptables** a la màquina host:
- Totes les connexions TCP que arribin al port 5000 de la màquina host es redirigeixen
  (NAT) al port 5000 de la interfície interna del contenidor.

Quan un navegador obre `http://192.168.1.10:5000/`:
1. El kernel de Linux de la host rep el paquet TCP SYN al port 5000.
2. `iptables` el redirigeix a la IP interna del contenidor (ex. `172.17.0.2:5000`).
3. El socket de Werkzeug al contenidor rep la connexió.
4. Werkzeug llegeix la petició HTTP del socket.

---

## 4. Petició GET `/` — carregar la pàgina web

### 4.1 Werkzeug rep i parseja la petició

```
GET / HTTP/1.1
Host: localhost:5000
```

Werkzeug llegeix bytes del socket, parseja la línia de petició (`GET /`), les capçaleres
HTTP, i construeix un objecte `Request` de Flask.

### 4.2 Flask busca la ruta

Flask consulta la seva taula de rutes. Troba que `GET /` correspon a la funció `index`.

### 4.3 Execució de `index()` (línia 72)

```python
@app.route("/")
def index():
    env_host = os.getenv("MIKROTIK_HOST", "")          # llegeix variable d'entorn
    env_user = os.getenv("MIKROTIK_USER", "")          # llegeix variable d'entorn
    credentials_from_env = bool(env_host and env_user) # True si ambdues estan definides
    return render_template(
        "index.html",
        credentials_from_env=credentials_from_env,
        env_host=env_host,
    )
```

- `os.getenv("MIKROTIK_HOST", "")` consulta `os.environ`. Retorna el valor si existeix,
  cadena buida si no.
- `bool(env_host and env_user)` → `True` si ambdues variables estan definides i no
  buides; `False` si falta alguna.
- `render_template("index.html", ...)` obre `/app/templates/index.html`, el processa amb
  el motor de plantilles **Jinja2**, substitueix `{{ credentials_from_env }}` i
  `{{ env_host }}` pels valors Python, i retorna l'HTML resultant com a string.

### 4.4 Resposta HTTP

Flask construeix:
```
HTTP/1.1 200 OK
Content-Type: text/html; charset=utf-8
Content-Length: <bytes>

<!DOCTYPE html>...
```

Werkzeug escriu la resposta al socket. El navegador rep l'HTML i el renderitza.

---

## 5. Petició POST `/wifi/off` — apagar el WiFi 30 minuts

Escenari: l'usuari clica "Apaga WiFi 30 minuts" al navegador.

El JavaScript de `index.html` fa:
```javascript
fetch("/wifi/off", {
    method: "POST",
    body: new FormData(form)  // conté: minutes=30
})
```

### 5.1 Werkzeug rep la petició

```
POST /wifi/off HTTP/1.1
Content-Type: application/x-www-form-urlencoded

minutes=30
```

### 5.2 Flask encamina a `wifi_off()` (línia 84)

```python
@app.route("/wifi/off", methods=["POST"])
def wifi_off():
```

### 5.3 Comprovació d'estat — adquisició del lock (línia 86)

```python
with state_lock:
    if wifi_state["disabled"]:
        return jsonify({"ok": False, "error": "La WiFi ja està apagada."}), 400
```

`with state_lock:` → el fil de Werkzeug **adquireix el mutex**. Si un altre fil el té,
espera. Un cop adquirit:
- Llegeix `wifi_state["disabled"]`.
- Si ja és `True`, retorna error 400 immediatament.
- En el nostre escenari és `False`, continua.
- `with` allibera automàticament el lock en sortir del bloc.

### 5.4 Validació dels paràmetres (línies 91–100)

```python
try:
    minutes = int(request.form.get("minutes", 0))
except (ValueError, TypeError):
    return jsonify({"ok": False, "error": "Durada no vàlida."}), 400

if minutes <= 0:
    return jsonify({"ok": False, "error": "La durada ha de ser superior a 0 minuts."}), 400
```

- `request.form.get("minutes", 0)` → llegeix el camp `minutes` del cos de la petició.
  Retorna la string `"30"`.
- `int("30")` → converteix a enter `30`. Si no fos un número, `ValueError` → error 400.
- `30 <= 0` → `False`, continua.

```python
host, username, password = _get_connection_params(request.form)
if not host or not username:
    return jsonify({"ok": False, "error": "Falten les dades de connexió al router."}), 400
```

`_get_connection_params` (línia 28):
```python
def _get_connection_params(form_data):
    host     = os.getenv("MIKROTIK_HOST") or form_data.get("host", "").strip()
    username = os.getenv("MIKROTIK_USER") or form_data.get("username", "").strip()
    password = os.getenv("MIKROTIK_PASSWORD") or form_data.get("password", "")
    return host, username, password
```

- Primer mira `os.environ` (variables del `.env`).
- Si no existeix, mira el formulari HTML (camps `host`, `username`, `password`).
- Retorna `("192.168.88.1", "admin", "")` si el `.env` té les variables.

### 5.5 Connexió al router — `_set_wireless_disabled` (línia 38)

```python
_set_wireless_disabled(host, username, password, disabled=True)
```

Dins de la funció:

```python
pool = routeros_api.RouterOsApiPool(
    host,                  # "192.168.88.1"
    username=username,     # "admin"
    password=password,     # ""
    plaintext_login=True,  # envia la contrasenya en text pla (protocol MikroTik API v1)
)
```

**Creació del pool:** `RouterOsApiPool` és un objecte que gestiona la connexió TCP.
En aquest punt no s'ha connectat encara — és configuració.

```python
api = pool.get_api()
```

**Connexió TCP real:** obre un socket TCP al port **8728** de `192.168.88.1` (port de
l'API de MikroTik). Fa el **handshake d'autenticació** del protocol RouterOS API:
1. Envia el nom d'usuari i contrasenya en codificació de longitud de paraula (*word length*).
2. El router valida i respon `/login/ret` amb l'èxit.

```python
wireless = api.get_resource("/interface/wireless")
```

Crea un objecte `resource` que representa el endpoint `/interface/wireless` de l'API.
Encara no envia res al router.

```python
interfaces = wireless.get()
```

**Primera crida API:** envia el missatge:
```
/interface/wireless/print
```
El router respon amb una llista d'objectes, un per cada interfície WiFi. Per exemple:
```python
[
    {".id": "*1", "name": "wlan1", "disabled": "false", ...},
    {".id": "*2", "name": "wlan2", "disabled": "false", ...},
]
```

```python
for iface in interfaces:
    if disabled:
        wireless.call("disable", {"numbers": iface[".id"]})
```

**Per cada interfície WiFi**, envia:
```
/interface/wireless/disable
=numbers=*1
```
El router desactiva la interfície i respon `/command-ret`. Cada crida és síncrona
(espera resposta abans de continuar).

```python
finally:
    pool.disconnect()
```

Tanca el socket TCP. Sempre s'executa, fins i tot si hi ha error.

### 5.6 Programació del temporitzador (línies 108–116)

```python
re_enable_at = datetime.now(timezone.utc) + timedelta(minutes=30)
# → datetime(2024-01-15 10:30:00+00:00)  (exemple)

timer = threading.Timer(
    30 * 60,            # 1800 segons
    _re_enable_wifi,    # funció a executar quan expiri
    args=(host, username, password),
)
timer.daemon = True  # el fil s'atura si el procés principal s'atura
timer.start()
```

`threading.Timer` crea un **nou fil d'execució** que:
1. Espera 1800 segons en `time.sleep(1800)`.
2. Passats els 1800 segons, executa `_re_enable_wifi(host, username, password)`.

En aquest moment tenim **dos fils actius**:
- **Fil principal** (Werkzeug): gestiona peticions HTTP.
- **Fil temporitzador**: dorm 1800 segons.

### 5.7 Actualització de l'estat (línies 118–122)

```python
with state_lock:
    wifi_state["disabled"] = True
    wifi_state["re_enable_at"] = re_enable_at.isoformat()
    # → "2024-01-15T10:30:00+00:00"
    wifi_state["timer"] = timer
    wifi_state["last_error"] = None
```

Adquireix el lock, actualitza els quatre camps del diccionari, allibera el lock.
Altres fils que intentin llegir `wifi_state` esperaran fins que el lock s'alliberi.

### 5.8 Resposta JSON (línia 124)

```python
return jsonify({"ok": True, "re_enable_at": re_enable_at.isoformat()})
```

Flask construeix:
```
HTTP/1.1 200 OK
Content-Type: application/json

{"ok": true, "re_enable_at": "2024-01-15T10:30:00+00:00"}
```

El JavaScript del navegador rep el JSON, mostra el compte enrere.

---

## 6. Petició GET `/status` — consulta de l'estat (polling)

El JavaScript fa una petició cada 10 segons:
```javascript
setInterval(() => fetch("/status").then(r => r.json()).then(updateUI), 10000)
```

### Execució de `status()` (línia 158)

```python
@app.route("/status")
def status():
    with state_lock:
        re_enable_at = wifi_state["re_enable_at"]  # "2024-01-15T10:30:00+00:00"
        remaining = None
        if re_enable_at:
            delta = datetime.fromisoformat(re_enable_at) - datetime.now(timezone.utc)
            # delta = timedelta(seconds=1650)  (exemple: 27.5 minuts)
            remaining = max(0, int(delta.total_seconds()))  # → 1650
        return jsonify({
            "disabled": wifi_state["disabled"],       # True
            "re_enable_at": re_enable_at,             # "2024-01-15T10:30:00+00:00"
            "remaining_seconds": remaining,           # 1650
            "last_error": wifi_state["last_error"],   # None
        })
```

Resposta:
```json
{
  "disabled": true,
  "re_enable_at": "2024-01-15T10:30:00+00:00",
  "remaining_seconds": 1650,
  "last_error": null
}
```

---

## 7. Expiració del temporitzador — reactivació automàtica

Passats els 1800 segons, el fil temporitzador desperta i executa `_re_enable_wifi`.

### Execució de `_re_enable_wifi` (línia 57)

```python
def _re_enable_wifi(host, username, password):
    try:
        _set_wireless_disabled(host, username, password, disabled=False)
        # → mateixa seqüència que l'apagada però amb wireless.call("enable", ...)
    except Exception as exc:
        with state_lock:
            wifi_state["last_error"] = str(exc)
    finally:
        with state_lock:
            wifi_state["disabled"] = False
            wifi_state["re_enable_at"] = None
            wifi_state["timer"] = None
```

- `_set_wireless_disabled(..., disabled=False)` → obre connexió TCP al router,
  obté les interfícies, i per cadascuna envia `/interface/wireless/enable`.
- `finally` s'executa sempre: restableix l'estat a "WiFi encesa" independentment
  de si la connexió va bé o malament.
- Si hi ha error de connexió, `last_error` queda enregistrat i el `/status` el
  retornarà al navegador.

---

## 8. Petició POST `/wifi/on` — reactivació manual

L'usuari clica "Torna a encendre ara".

### Execució de `wifi_on()` (línia 128)

```python
@app.route("/wifi/on", methods=["POST"])
def wifi_on():
    with state_lock:
        if not wifi_state["disabled"]:
            return jsonify({"ok": False, "error": "La WiFi ja està encesa."}), 400
        timer = wifi_state.get("timer")   # guarda referència al temporitzador actiu
```

```python
    if timer is not None:
        timer.cancel()
```

`timer.cancel()` envia un senyal al fil temporitzador per aturar-lo. Si el fil
estava dormint (no havia expirat), s'atura. Si ja havia expirat i estava executant
`_re_enable_wifi`, `cancel()` no té efecte (massa tard), però la funció `wifi_on`
continua igualment.

```python
    try:
        _set_wireless_disabled(host, username, password, disabled=False)
    except Exception as exc:
        ...
        return jsonify({"ok": False, "error": "Error connectant al router..."}), 500

    with state_lock:
        wifi_state["disabled"] = False
        wifi_state["re_enable_at"] = None
        wifi_state["timer"] = None
        wifi_state["last_error"] = None

    return jsonify({"ok": True})
```

Idèntic al temporitzador però iniciat per l'usuari i amb cancel·lació del timer.

---

## 9. Diagrama de fils d'execució

```
Procés Python (PID 1)
│
├─ Fil principal — Werkzeug HTTP server
│   ├─ accept() → nova connexió TCP
│   ├─ GET  /           → index()
│   ├─ GET  /status     → status()
│   ├─ POST /wifi/off   → wifi_off()
│   │                       ├─ adquireix state_lock
│   │                       ├─ valida paràmetres
│   │                       ├─ _set_wireless_disabled() ← bloqueja fins resposta router
│   │                       ├─ threading.Timer(1800, ...).start()
│   │                       ├─ actualitza wifi_state (amb lock)
│   │                       └─ retorna JSON 200
│   └─ POST /wifi/on    → wifi_on()
│
└─ Fil temporitzador (creat per wifi_off)
    ├─ time.sleep(1800)   ← dorm 30 minuts
    └─ _re_enable_wifi()  ← s'executa quan expira
        ├─ _set_wireless_disabled(disabled=False) ← bloqueja fins resposta router
        └─ actualitza wifi_state (amb lock)
```

---

## 10. Resum de ports i protocols

| Connexió | Protocol | Port | Origen | Destí |
|---|---|---|---|---|
| Navegador → Host | TCP/HTTP | 5000 | Navegador | Màquina host |
| Host → Contenidor | TCP/NAT | 5000 | Docker iptables | Contenidor |
| Contenidor → Router | TCP | 8728 | Contenidor | 192.168.88.1 |

El port **8728** és el port estàndard de l'API de MikroTik RouterOS. S'ha d'activar
al router amb: `IP → Services → api → enabled`.

---

## 11. Script de configuració — `setup_mikrotik.sh`

El script prepara el router MikroTik perquè l'aplicació s'hi pugui connectar.
S'executa **una sola vegada**, abans del primer `docker compose up`.

```bash
bash setup_mikrotik.sh
```

### 11.1 Capçalera i opcions de shell (línia 1–15)

```bash
#!/usr/bin/env bash
```
L'**shebang** indica al kernel quin intèrpret ha d'usar per executar el fitxer.
`/usr/bin/env bash` busca `bash` al `PATH` en lloc d'usar una ruta fixa, cosa que
el fa portable entre distribucions Linux i macOS.

```bash
set -euo pipefail
```
Tres opcions de seguretat que s'activen juntes:

| Opció | Efecte |
|---|---|
| `-e` | El script s'atura immediatament si qualsevol comanda retorna codi d'error ≠ 0 |
| `-u` | Error si es referencia una variable no definida (evita errors silenciosos per *typos*) |
| `-o pipefail` | Una canonada (`cmd1 \| cmd2`) falla si **qualsevol** dels membres falla, no només l'últim |

### 11.2 Funcions de color (línies 17–25)

```bash
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[AVÍS]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; }
```

Les variables `RED`, `GREEN`, `YELLOW` i `NC` (*No Color*) contenen **codis d'escapada
ANSI**. Quan el terminal els interpreta, canvien el color del text.

`\033[` és el caràcter d'escapada ESC (codi ASCII 27 en octal). `0;31m` significa
"atribut normal, color vermell". `\033[0m` (*NC*) reseteja tots els atributs.

Les tres funcions (`ok`, `warn`, `err`) són dreceres per imprimir missatges amb el
color corresponent. `$*` expandeix tots els arguments passats a la funció.

### 11.3 Recollida interactiva de paràmetres (línies 34–57)

```bash
read -rp "IP del router MikroTik [192.168.88.1]: " ROUTER_IP
ROUTER_IP="${ROUTER_IP:-192.168.88.1}"
```

`read -rp` mostra el text del prompt i espera que l'usuari escrigui una línia.
- `-r`: no interpreta les barres invertides com a caràcters d'escapada.
- `-p "text"`: mostra el text com a prompt sense salt de línia.

`${ROUTER_IP:-192.168.88.1}`: **expansió amb valor per defecte** de bash. Si
`ROUTER_IP` és buit (l'usuari ha premut Enter sense escriure res), s'assigna
`192.168.88.1`. Si l'usuari ha escrit alguna cosa, es manté el que ha escrit.

```bash
read -rsp "Contrasenya de l'administrador: " ADMIN_PASS
```

`-s`: **mode silenciós** (*silent*). Els caràcters que escriu l'usuari no es mostren
al terminal, necessari per a contrasenyes. El salt de línia posterior `echo ""` és
manual perquè `-s` tampoc imprimeix el salt en prémer Enter.

```bash
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
```

Bucle de **verificació de contrasenya**. Demana la contrasenya dues vegades i compara
les dues strings amb `[ "$APP_PASS" = "$APP_PASS2" ]`. Si no coincideixen, torna a
demanar-les. Si coincideixen, `break` surt del bucle `while true`.

### 11.4 Resum i confirmació (línies 59–74)

```bash
read -rp "Continuar? [s/N]: " CONFIRM
if [[ ! "$CONFIRM" =~ ^[sS]$ ]]; then
    echo "Cancel·lat."
    exit 0
fi
```

`[[ "$CONFIRM" =~ ^[sS]$ ]]` és una **expressió regular** dins del condicional
extès de bash (`[[ ]]`):
- `^` → inici de la string
- `[sS]` → el caràcter 's' o 'S'
- `$` → fi de la string

Si l'usuari escriu qualsevol cosa que no sigui exactament `s` o `S`, el script surt
amb codi 0 (sortida neta, sense error).

### 11.5 Funció `run_router` — execució de comandes RouterOS via SSH (línies 80–88)

```bash
run_router() {
    local cmd="$1"
    ssh \
        -o BatchMode=no \
        -o StrictHostKeyChecking=no \
        -o ConnectTimeout=10 \
        "${ADMIN_USER}@${ROUTER_IP}" \
        "$cmd"
}
```

Aquesta funció encapsula totes les crides SSH al router. Quan s'invoca amb
`run_router "/ip service enable api"`, bash executa:

```
ssh -o BatchMode=no -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
    admin@192.168.88.1 "/ip service enable api"
```

MikroTik implementa un **servidor SSH propi** que, en lloc d'obrir un shell POSIX,
interpreta directament comandes RouterOS. Cada comanda passada com a argument
s'executa i el procés SSH acaba.

Opcions SSH rellevants:

| Opció | Efecte |
|---|---|
| `BatchMode=no` | Permet que SSH demani la contrasenya interactivament si cal |
| `StrictHostKeyChecking=no` | No verifica la clau del host (accepta la primera connexió sense preguntar) |
| `ConnectTimeout=10` | Si no hi ha resposta en 10 segons, falla amb error |

> `StrictHostKeyChecking=no` és acceptable en una xarxa local de confiança.
> En entorns de producció caldria afegir la clau del router a `~/.ssh/known_hosts`.

### 11.6 Pas 1 — Verificació de la connectivitat SSH (línies 94–104)

```bash
if ! ssh \
    -o BatchMode=no \
    -o StrictHostKeyChecking=no \
    -o ConnectTimeout=10 \
    "${ADMIN_USER}@${ROUTER_IP}" \
    "/system identity print" > /dev/null 2>&1; then
    err "No s'ha pogut connectar..."
    exit 1
fi
```

Executa `/system identity print` (comanda RouterOS que retorna el nom del router).
Es fa servir com a **ping de comprovació**: si SSH connecta i el router respon, tot
va bé. La sortida es descarta (`> /dev/null 2>&1`):
- `> /dev/null` → redirigeix stdout al dispositiu nul (descarta la sortida estàndard)
- `2>&1` → redirigeix stderr al mateix lloc que stdout (també descartat)

Si el codi de retorn de SSH és diferent de 0 (error), `! ssh ...` és `true` i
s'entra al bloc `if`, es mostra l'error i `exit 1` atura el script amb codi d'error.

### 11.7 Pas 2 — Activació del servei API (línia 109)

```bash
run_router "/ip service enable api"
```

Envia via SSH la comanda RouterOS:
```
/ip service enable api
```

El router busca el servei `api` a la seva taula de serveis i posa `disabled=no`.
Internament RouterOS obre el port TCP 8728 i comença a escoltar connexions entrants.

### 11.8 Pas 3 — Creació del grup `wifi-control` (línies 117–119)

```bash
run_router "/user group add name=wifi-control policy=read,write,api" 2>/dev/null || \
    warn "El grup 'wifi-control' ja existia (s'omete la creació)."
```

Comanda RouterOS enviada:
```
/user group add name=wifi-control policy=read,write,api
```

RouterOS crea el grup amb les tres polítiques. Si el grup ja existia, RouterOS
retorna un error. El `|| warn ...` captura aquest error (gràcies a que `2>/dev/null`
descarta el missatge d'error de SSH) i mostra l'avís sense aturar el script.

Sense `|| ...`, el `set -e` del principi aturaria el script en trobar un error.

### 11.9 Pas 4 — Creació de l'usuari de l'app (línies 125–129)

```bash
run_router "/user add name=${APP_USER} password=${APP_PASS} group=wifi-control" 2>/dev/null || \
    warn "L'usuari '${APP_USER}' ja existia. Actualitzant la contrasenya..."
    run_router "/user set [find name=${APP_USER}] password=${APP_PASS}" 2>/dev/null || true
```

Primera crida: crea l'usuari nou.
```
/user add name=wifiapp password=XXXX group=wifi-control
```

Si l'usuari ja existia (error), la segona crida actualitza la contrasenya:
```
/user set [find name=wifiapp] password=XXXX
```

`[find name=wifiapp]` és una **subexpressió RouterOS**: busca l'identificador intern
(`.id`) de l'usuari amb `name=wifiapp` i el passa com a argument a `set`.

### 11.10 Pas 5 — Restricció de l'API per IP (línies 132–137, opcional)

```bash
if [ -n "$ALLOWED_IP" ]; then
    run_router "/ip service set api address=${ALLOWED_IP}/32"
fi
```

`[ -n "$ALLOWED_IP" ]` és cert si la variable no és buida (*non-zero length*).
Si l'usuari ha introduït una IP, s'envia:
```
/ip service set api address=192.168.88.X/32
```

RouterOS afegeix una **ACL** al servei API: només acceptarà connexions TCP al port
8728 provinents de la IP indicada. `/32` és la màscara CIDR per a una IP exacta.

### 11.11 Pas 6 — Verificació del port 8728 (línies 143–148)

```bash
if nc -zv -w 5 "${ROUTER_IP}" 8728 2>/dev/null; then
    ok "Port 8728 accessible."
else
    warn "No s'ha pogut verificar el port 8728 amb nc."
fi
```

`nc` (netcat) intenta obrir una connexió TCP al port 8728 del router:
- `-z`: mode *zero I/O* — només comprova si el port és obert, no envia dades.
- `-v`: verbós — mostra el resultat de la connexió.
- `-w 5`: timeout de 5 segons.

Si la connexió TCP s'estableix i es tanca correctament (codi 0), el port és accessible.
Si `nc` no és disponible al sistema o el port no respon, mostra un avís però **no atura
el script** (és una verificació opcional, no un requisit).

### 11.12 Pas 7 — Generació del fitxer `.env` (línies 154–171)

```bash
ENV_FILE="$(dirname "$0")/.env"
```

`dirname "$0"` retorna el directori on es troba el script. Si s'executa des de
`/home/user/wifi_off/setup_mikrotik.sh`, `dirname` retorna `/home/user/wifi_off`.
El `.env` es crea al costat del script, que és el directori arrel del projecte.

```bash
if [ -f "$ENV_FILE" ]; then
    warn ".env ja existeix. Es guarda una còpia a .env.bak"
    cp "$ENV_FILE" "${ENV_FILE}.bak"
fi
```

`[ -f "$ENV_FILE" ]` comprova si el fitxer existeix i és un fitxer regular (no un
directori ni un enllaç). Si existeix, fa una còpia de seguretat abans de sobreescriure.

```bash
SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || \
         od -An -tx1 /dev/urandom | tr -d ' \n' | head -c 64)
```

Genera una **clau secreta aleatòria** de 64 caràcters hexadecimals (32 bytes → 256 bits):

- **Opció 1** (preferida): `python3 -c "import secrets; print(secrets.token_hex(32))"`
  Usa el mòdul `secrets` de Python, dissenyat específicament per a valors
  criptogràficament segurs. Llegeix de `/dev/urandom` internament.

- **Opció 2** (fallback si Python no disponible):
  `od -An -tx1 /dev/urandom | tr -d ' \n' | head -c 64`
  - `od -An -tx1 /dev/urandom`: llegeix bytes aleatoris de `/dev/urandom` (font del
    kernel d'entropia real) i els formata com a hexadecimal (`-tx1`), sense offset (`-An`).
  - `tr -d ' \n'`: elimina espais i salts de línia del format d'`od`.
  - `head -c 64`: agafa els primers 64 caràcters.

```bash
cat > "$ENV_FILE" <<EOF
MIKROTIK_HOST=${ROUTER_IP}
MIKROTIK_USER=${APP_USER}
MIKROTIK_PASSWORD=${APP_PASS}
SECRET_KEY=${SECRET}
EOF
```

`cat > fitxer <<EOF ... EOF` és un **here-document**: escriu el bloc de text
directament al fitxer, substituint les variables bash pels seus valors.
El resultat és el fitxer `.env` llest per usar amb `docker compose`.

### 11.13 Sortida del procés i resum

Un cop tots els passos s'han completat sense errors (gràcies al `set -e`), el script
imprimeix les instruccions de pròxims passos i acaba amb codi de retorn 0 (èxit).

```
════════════════════════════════════════════════
  Configuració completada!
════════════════════════════════════════════════

  Pròxims passos:
    docker compose up -d --build
    curl http://localhost:5000/status
```

### 11.14 Diagrama de flux del script

```
bash setup_mikrotik.sh
│
├─ Recull paràmetres interactivament
│   ├─ ROUTER_IP, ADMIN_USER, ADMIN_PASS
│   ├─ APP_USER, APP_PASS (verificació doble)
│   └─ ALLOWED_IP (opcional)
│
├─ Mostra resum → demana confirmació
│   └─ Si no és 's'/'S' → exit 0
│
├─ SSH → /system identity print          (verificació de connectivitat)
│   └─ Si falla → exit 1
│
├─ SSH → /ip service enable api          (obre port 8728)
├─ SSH → /user group add wifi-control    (grup amb polítiques mínimes)
├─ SSH → /user add wifiapp               (o actualitza contrasenya si existeix)
│
├─ [Opcional] SSH → /ip service set api address=X.X.X.X/32
│
├─ nc -zv router:8728                    (verificació del port)
│
└─ Escriu .env                           (amb SECRET_KEY de 256 bits)
    └─ exit 0
```
