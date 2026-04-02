# wifi_off

Apaga la WiFi d'un router Mikrotik durant un temps determinat mitjançant una interfície web.

## Requisits

- Docker i Docker Compose (opció recomanada), **o** Python 3.8 o superior
- Router MikroTik amb l'API activada (port 8728) — vegeu la secció següent

## Configuració prèvia del router MikroTik

Abans d'usar l'aplicació cal activar el servei API al router i crear un usuari específic.
El script `setup_mikrotik.sh` automatitza tots els passos:

```bash
bash setup_mikrotik.sh
```

El script demana la IP del router i les credencials d'administrador i:
1. Activa el servei API al port 8728
2. Crea el grup `wifi-control` amb les polítiques mínimes (`read`, `write`, `api`)
3. Crea l'usuari `wifiapp` amb contrasenya personalitzada
4. Verifica que el port 8728 és accessible

### Passos manuals (alternativa)

Si prefereixes configurar-ho a mà via terminal RouterOS:

```
/ip service enable api
/user group add name=wifi-control policy=read,write,api
/user add name=wifiapp password=CONTRASENYA group=wifi-control
```

Comprova que l'API és activa:
```
/ip service print
```
Ha de mostrar `api` amb `disabled=no` i `port=8728`.

#### Polítiques necessàries per a l'usuari

| Política | Per què |
|---|---|
| `read` | Llegir la llista d'interfícies WiFi |
| `write` | Activar/desactivar les interfícies |
| `api` | Permís d'accés per l'API |

#### Restringir l'accés a l'API per IP (opcional però recomanat)

```
/ip service set api address=192.168.88.X/32
```

Substitueix `192.168.88.X` per la IP del servidor on corre el contenidor.

## Instal·lació

### Opció A — Docker (recomanat)

Requisits: Docker i Docker Compose.

```bash
bash setup_mikrotik.sh   # configura el router i genera el .env
docker compose up -d --build
```

L'aplicació queda disponible a [http://localhost:5000](http://localhost:5000).

### Opció B — Python local

Requisits: Python 3.8 o superior.

```bash
pip install -r requirements.txt
```

## Configuració

Copia `.env.example` a `.env` i edita els valors (el `setup_mikrotik.sh` ho fa automàticament):

```bash
cp .env.example .env
```

| Variable           | Descripció                              | Exemple         |
|--------------------|-----------------------------------------|-----------------|
| `MIKROTIK_HOST`    | IP del router Mikrotik                  | `192.168.88.1`  |
| `MIKROTIK_USER`    | Usuari de la API del router             | `admin`         |
| `MIKROTIK_PASSWORD`| Contrasenya (pot estar buit)            |                 |
| `SECRET_KEY`       | Clau secreta de Flask (canvia-la!)      |                 |

Si no es defineix `.env`, les credencials es poden introduir directament a la interfície web.

## Ús

**Amb Docker:**
```bash
docker compose up -d
```

**Amb Python local:**
```bash
python app.py
```

Obre el navegador a [http://localhost:5000](http://localhost:5000).

1. Introdueix el temps d'apagada en minuts.
2. Prem **Apaga la WiFi** – totes les interfícies WiFi del router s'apagaran.
3. Quan vulguis tornar-la a encendre abans d'hora, prem **Encén la WiFi ara**.

## Estructura

```
wifi_off/
├── app.py              # Servidor Flask + lògica Mikrotik
├── templates/
│   └── index.html      # Interfície web
├── setup_mikrotik.sh   # Script de configuració del router
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```
