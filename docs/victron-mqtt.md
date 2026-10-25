# Victron MQTT und Docker

Der Emulator stellt Messwerte eines vorhandenen Zählers als KSEM über Modbus RTU
bereit. Der PLENTICORE bleibt der Modbus-Master. Die Verbindung zum Wechselrichter
benötigt RS485; seine LAN-/Modbus-TCP-Schnittstelle ist kein Ersatz dafür.

Als Ziel sind die zusätzlichen Verbrauchsanzeigen und Statistiken eines reinen
AC-PV-Wechselrichters vorgesehen. Ein am Victron angeschlossener Speicher wird nicht
als KOSTAL-Batterie emuliert. Victron beziehungsweise Home Assistant bleiben die
maßgeblichen Quellen. Insbesondere können Netz-, Autarkie- und Quellenstatistiken
des PLENTICORE vom tatsächlichen Gesamtsystem abweichen. Historische Statistiken
werden nicht nachgetragen.

## Datenquellen

`SOURCE_TYPE=shelly` ist der Standard. Setze `SHELLY_URL` auf die lokale `/status`-URL,
optional `SHELLY_USERNAME` (Standard `admin`) und `SHELLY_PASSWORD`. Drei vollständige
`emeters` mit `power`, `voltage`, `current` und `pf` sind erforderlich. Ohne eigene
Frequenzangabe bleibt wie im Original die Annahme von 50 Hz bestehen. Der ursprüngliche
Shelly-Poller wird weiterhin verwendet.

Für `SOURCE_TYPE=victron_mqtt` sind folgende Einstellungen vorgesehen:

| Variable | Bedeutung / Standard |
| --- | --- |
| `MQTT_HOST`, `MQTT_PORT` | Broker-Adresse, Port `1883` |
| `MQTT_PREFIX` | Optionaler Bridge-Präfix ohne abschließenden Schrägstrich |
| `MQTT_USERNAME`, `MQTT_PASSWORD` | Optionale Broker-Zugangsdaten |
| `VICTRON_PORTAL_ID` | Portal-ID des GX-Geräts, erforderlich |
| `VICTRON_SERVICE`, `VICTRON_INSTANCE` | Zählermesspunkt, Standard `acload` / `40` |
| `SERIAL_PORT` | Dedizierter RS485-Port, im Container `/dev/ksem` |
| `STALE_SECONDS` | Maximales Alter aller erforderlichen Werte, Standard `5` Sekunden |
| `STATE_FILE` | Persistente Energiezähleroffsets, `/data/energy-offsets.json` |
| `HEALTH_FILE` | Laufzeitstatus, `/tmp/ksem-health.json` |

Erwartete Unterthemen unter `N/<Portal-ID>/<Service>/<Instanz>/`:

- `Connected` muss `1` sein.
- `Ac/L1/Power`, `Voltage`, `Current`, `PowerFactor`, `Energy/Forward`, entsprechend auch L2/L3.
- `Ac/Frequency` und `Ac/Energy/Forward`.

Die Nachrichten enthalten `{"value": <Zahl>}`. Leistung wird in W, Spannung in V,
Strom in A, Frequenz in Hz und Energie in kWh erwartet. Retained-Nachrichten werden
nicht als aktuelle Messungen akzeptiert. Nach einem Neustart oder einer Wiederverbindung
müssen alle erforderlichen Werte erneut eintreffen. Eine vollständige Aktualisierung
wird gemeinsam übernommen; einzelne Phasenpakete ersetzen keinen ganzen Registersatz.

Nach bestätigter MQTT-Anmeldung und Subscription wird ein vollständiger Venus-Snapshot
angefordert. Danach fragt der Emulator sekündlich den `Ac`-Unterbaum und `Connected`
über `R/...` ab. Alle 30 Sekunden hält ein Keepalive mit `suppress-republish` den
Datenstrom aktiv. Sämtliche Veröffentlichungen verwenden QoS 1 und `retain=false`.
Der Emulator veröffentlicht keine `W/...`-Steuerbefehle.

Fehlende, nicht numerische, getrennte oder veraltete Messungen führen zu Modbus-
Fehlercode `04` (Device Failure), nicht zu einem gültigen Nullwert. Nach einer
vollständigen gültigen Aktualisierung werden wieder Messwerte geliefert.

## Register und Energiezähler

Momentanwerte und grundlegende PnP-Register entsprechen dem ursprünglichen Aufbau:
Register `0–147`, Messintervall `8244`, Unixzeit in Millisekunden `8245–8248`,
Modbus-Spezifikationsversion `8249`. Wirkleistung wird phasenweise und saldiert in
Bezug/Einspeisung aufgeteilt. Frequenz wird bei Victron gemessen übernommen.
Scheinleistung und Blindleistungsbetrag werden wie im Original aus U, I und P
abgeleitet; die Richtung der Blindleistung wird aus der Wirkleistung angenähert.
Diese Größen sind kein Ersatz für eine vollständige Vierquadrantenmessung.

Zusätzlich werden die verfügbaren Bezugs-Energiezähler bereitgestellt:

| Register | Zähler | Darstellung |
| --- | --- | --- |
| `512–515` | Gesamt | uint64, 0,1 Wh |
| `592–595` | L1 | uint64, 0,1 Wh |
| `672–675` | L2 | uint64, 0,1 Wh |
| `752–755` | L3 | uint64, 0,1 Wh |

Nicht vorhandene Energiezähler, insbesondere Rückspeisezähler, werden nicht erfunden;
ihre Register liefern Fehlercode `02`. Unterstützt sind Lesezugriffe FC03 und FC04.
Modbus-Schreibzugriffe werden abgewiesen.

Beim ersten gültigen MQTT-Paket werden persistente Startoffsets angelegt. Der virtuelle
Zähler zählt ab diesem Zeitpunkt. Die Offsets und zuletzt bestätigten Rohzähler werden
atomar gespeichert und nach Neustarts wiederverwendet. Ein rückläufiger Quellzähler,
ein beschädigter Zustand oder eine andere konfigurierte Quelle wird als Fehler behandelt.
Den Zustand dann sichern und die Zählerkonfiguration prüfen; nicht blind löschen.
Das Volume muss bei Image-Updates erhalten bleiben.

## Docker Compose

1. `compose.example.yml` übernehmen und `.env.example` lokal als `.env` ausfüllen.
2. Einen eigenen USB-RS485-Adapter mit eindeutigem Gerätepfad anschließen.
   `KSEM_SERIAL_DEVICE` auf seinen `/dev/serial/by-id/...`-Pfad setzen. Die GID mit
   `stat -Lc '%g' /dev/serial/by-id/<Adapter>` ermitteln und als `KSEM_SERIAL_GID` setzen.
3. Den Adapter mit dem Zähleranschluss des PLENTICORE verbinden. RS485-Kabel und
   Schnittstellenparameter entsprechend der Geräteanleitung verwenden.
4. Die bisherige Zähler- und Regelungskonfiguration dokumentieren. Für eine Quelle,
   die ausschließlich alle Hausverbraucher erfasst, am PLENTICORE mit Installateur-
   Zugang `KSEM` und `Hausverbrauch / Position 1` einstellen. Leistungsgrenzen und
   vorhandene Regelungsfunktionen nicht verändern.
5. Den einzelnen Dienst bauen und starten:

```bash
docker compose --profile ksem up -d --build ksem-emulator
docker compose logs -f ksem-emulator
```

Der Dienst benutzt Slave-Adresse 1, 38400 Baud, 8N2. Er startet über das Profil
`ksem` gezielt und wird durch `restart: unless-stopped` nach einem Rechnerneustart
wiederhergestellt. Ohne angegebenen Adapter bleibt die Compose-Datei gültig, aber
der Dienst lässt sich nicht starten. Die anderen Dienste benötigen dieses Profil nicht.
Nach einem USB-Wechsel bei Bedarf nur diesen Container neu erstellen.

Das Image läuft als UID/GID 10001. `devices` und die zusätzliche Gerätegruppe geben
Zugriff auf genau den zugewiesenen Port. Das Datenvolume wird beim ersten Anlegen mit
passenden Eigentümern initialisiert. Root-Dateisystem, Berechtigungen und Healthcheck
sind in der Compose-Vorlage vorgegeben; es gibt keine veröffentlichten Netzwerkports.

## Prüfung ohne Wechselrichter

```bash
uv sync --frozen
uv run --frozen pytest -q
uv run --frozen ruff check ksem_emulator tests ksemServer.py shellyPoller.py
docker build -t local/ksem-emulator:0.1.0 .
```

Mit gesetzten MQTT-Umgebungsvariablen prüft `python ksemServer.py --check --duration 8`
den echten Eingang. Dieser Modus öffnet keinen seriellen Port und schreibt keinen
persistenten Energiezustand. Er verlangt acht Sekunden durchgehend vollständige,
frische Daten nach dem ersten gültigen Paket. Im Docker-Image können die gleichen
Argumente hinter dem Image-Namen angegeben werden.

Die automatisierten Tests verwenden einen lokalen HTTP-Server für Shelly und ein
isoliertes Pseudo-Terminal für echte Modbus-RTU-Telegramme. Es wird kein physisches
serielles Gerät geöffnet. Nach der tatsächlichen Inbetriebnahme Momentanwerte unter
verschiedenen regulären Betriebszuständen und einen vollständigen Tagesverbrauch
gegen den maßgeblichen Verbrauchszähler vergleichen; Ziel sind höchstens 5 % Differenz.
