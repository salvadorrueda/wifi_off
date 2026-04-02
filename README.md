# wifi_off

Apaga la WiFi d'un router Mikrotik durant un temps determinat mitjançant una interfície web.

## Requisits

- Python 3.8 o superior
- Accés a la API del router Mikrotik (port 8728 actiu)

## Instal·lació

```bash
pip install -r requirements.txt
```

## Configuració

Copia `.env.example` a `.env` i edita els valors:

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
├── requirements.txt
├── .env.example
└── README.md
```
