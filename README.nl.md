# WeHeat-Control

*[English](README.md) | Nederlands*

Schakel een **Weheat**-warmtepomp tussen zijn **stooklijn** en **OpenTherm**
vanuit **Homey** (of de opdrachtregel), binnen je eigen netwerk.

## Waarom zou je dit willen?

**Een Weheat-warmtepomp heeft geen echte uitknop.** Op zijn stooklijn blijft
hij warmte maken zodra de buitentemperatuur daarom vraagt, wat de stroom op
dat moment ook kost.

Dat telt bij een **dynamisch energiecontract**, waar de prijs elk uur anders
is. Op een koude winteravond kunnen een paar uur een veelvoud van het
gemiddelde kosten, en dat zijn vaak precies de uren dat de warmtepomp het
hardst draait. Een goed geïsoleerd huis kan makkelijk een paar uur zonder
verwarming, dus je zet de pomp dan liever stil en verwarmt weer als de stroom
goedkoop is.

De truc, als je een **OpenTherm-kamerthermostaat** op de warmtepomp hebt
aangesloten:

1. **Normaal** draait de pomp op zijn **stooklijn**.
2. **Vóór de dure uren** zet je hem op **OpenTherm**: nu beslist de
   thermostaat. Zet de thermostaat op een **heel lage temperatuur**, dan
   meldt die de warmtepomp dat er geen warmte nodig is, en stopt de pomp met
   verwarmen.
3. **Als de prijzen weer normaal zijn**, zet je hem terug op de
   **stooklijn**. WeHeat-Control zet precies de stooklijn terug die je had.

WeHeat-Control doet stap 2 en 3 met één aanroep vanuit Homey, dus een
Homey-flow kan het automatisch doen, bijvoorbeeld op een prijstrigger uit de
app van je energieleverancier. (De thermostaat lager en weer hoger zetten
doe je zelf of je Homey-flow; dit script schakelt alleen de regelwijze van de
warmtepomp.)

Weheat werkt aan een slimmere eigen regeling ("Weheat Intelligence"). Tot die
er is, kun je met deze truc de warmtepomp uitzetten tijdens dure uren.

## De twee standen

| Stand | Wat de pomp doet |
|---|---|
| `curve` | Draait op zijn **stooklijn**: de watertemperatuur volgt de buitentemperatuur (ControlMethod 1). Terug naar `curve` zet precies de stooklijnpunten terug die hij daarvoor had. |
| `opentherm` | De **OpenTherm**-kamerthermostaat beslist (ControlMethod 4). |

Homey stuurt `POST http://<server>:8095/api/mode` met `{"mode": "opentherm"}`
of `{"mode": "curve"}`.

> **Experimenteel.** Dit gebruikt de API die het eigen webportaal van Weheat
> gebruikt (`https://api.weheat.nl/api/v1`). Dat is geen gedocumenteerde
> openbare API en kan zonder aankondiging veranderen. Het verandert
> instellingen op de warmtepomp: gebruik het alleen op je eigen pomp, en kijk
> de eerste keren in de Weheat-app of het resultaat klopt.

## Hoe het werkt

1. **Inloggen.** Het logt in op het Weheat-portaal (`portal.weheat.nl`) met je
   eigen Weheat-gebruikersnaam en -wachtwoord, zoals een browser dat doet: de
   aanmelding van het portaal, de inlogpagina van Weheat (`auth.weheat.nl`),
   terug naar het portaal, en dan het token van de portaalsessie (ongeveer 5
   minuten; vernieuwt zichzelf, zo nodig opnieuw inloggen). De
   instellingen-API van Weheat accepteert alleen het eigen token van het
   portaal: een token van de officiële third-party-API krijgt *"This endpoint
   is not available for third-party API clients"*. (Die officiële API is
   alleen-lezen; `find` gebruikt hem ook.)
2. **Lezen.** `GET /api/v1/installations/<installatie>/settings` geeft de
   instellingen van de pomp, waaronder `ControlMethod` (`1` = stooklijn,
   `4` = OpenTherm) en `HeatingCurveY` (de stooklijnpunten).
3. **Onthouden.** Elke keer dat hij de stooklijn verlaat, bewaart hij eerst de
   stooklijnpunten in `state.json`. Hij weigert te schakelen als hij ze niet
   kan lezen, zodat hij altijd terug kan.
4. **Schakelen.** `PATCH /api/v1/control-boards/<regelbord>/settings`:
   - OpenTherm: `{"settings":{"ControlMethod":4},"updateImmediately":false}`
   - terug naar de stooklijn: `{"settings":{"ControlMethod":1,"HeatingCurveY":[44.1,28,24,19,18]},"updateImmediately":false}`
     (de onthouden punten)

   Een stand waar de pomp al in staat, stuurt niets.

## Installeren

Op een computer die altijd aan staat, in hetzelfde netwerk als Homey (een
Raspberry Pi of een kleine Linux-server). Vraagt Python 3.10 of nieuwer. De
code staat op https://github.com/Xibel/WeHeat-Control.

```sh
git clone https://github.com/Xibel/WeHeat-Control.git weheat-control
cd weheat-control
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.json config.json
chmod 600 config.json
```

Vul `config.json` in:

| Instelling | Wat |
|---|---|
| `username`, `password` | Je Weheat-login (dezelfde als in de Weheat-app). |
| `installation_id` | Leeg laten: `find` vult hem in. Of kopieer hem uit de adresbalk van het portaal (`portal.weheat.nl/.../installations/<id>/...`). |
| `control_board_id` | Leeg laten: `find` vult hem in als dat lukt. Anders: open het portaal in Chrome, ontwikkelaarstools (F12) → Network, verander een instelling, en kijk naar het verzoek `control-boards/<id>/settings`. |
| `update_immediately` | `false`, wat het portaal stuurt: de pomp neemt de wijziging over bij zijn volgende synchronisatie. |
| `api_host`, `api_port` | Waar de API luistert (standaard alle netwerkkaarten, poort `8095`). |
| `api_token` | Optioneel. Als hij is ingevuld, moet Homey hem meesturen (zie hieronder). Aanbevolen. |
| `state_file` | Tokens, id's en de onthouden stooklijn (standaard `state.json`). |

`config.json` en `state.json` bevatten je wachtwoord en tokens. Ze staan in
`.gitignore`; deel ze nooit.

## Stap 1: uitproberen (alleen lezen)

```sh
.venv/bin/python weheat_control.py login          # kunnen we inloggen?
.venv/bin/python weheat_control.py find           # installatie en regelbord
.venv/bin/python weheat_control.py status         # stand, stooklijn, wat onthouden is
.venv/bin/python weheat_control.py status --raw   # alle instellingen, zoals Weheat ze stuurt
```

## Stap 2: met de hand schakelen

```sh
.venv/bin/python weheat_control.py save                      # de stooklijn nu onthouden
.venv/bin/python weheat_control.py mode opentherm --dry-run  # alleen laten zien wat er gestuurd zou worden
.venv/bin/python weheat_control.py mode opentherm
.venv/bin/python weheat_control.py mode curve
```

Kijk daarna in de Weheat-app of de pomp deed wat je verwachtte. Verander je
later zelf de stooklijn, dan is dat geen probleem: de stooklijn wordt elke
keer opnieuw bewaard als de pomp hem verlaat.

## Stap 3: de API voor Homey

```sh
.venv/bin/python weheat_control.py serve
```

| Verzoek | Wat |
|---|---|
| `POST /api/mode` met `{"mode": "opentherm"}` | Naar OpenTherm |
| `POST /api/mode` met `{"mode": "curve"}` | Terug naar de stooklijn (de onthouden punten) |
| `GET /api/mode` | Nu: regelwijze, stooklijn, laatste stand, de onthouden stooklijn |
| `GET /api/health` | `{"ok": true}`; geen token nodig |

Antwoorden zijn JSON, bijvoorbeeld
`{"changed": true, "mode": "opentherm", "sent": {"ControlMethod": 4}, "ok": true}`.
Een verkeerde body geeft HTTP 400; een probleem bij Weheat geeft HTTP 502 met
`{"error": "..."}`.

### Het API-token

Het `api_token` is alleen tussen Homey en dit script. Weheat ziet het nooit.

- **Wat het is:** een zelfbedacht gedeeld wachtwoord, `api_token` in
  `config.json`. Het script controleert het bij elk verzoek. Zonder token, of
  met een verkeerd token, antwoordt het 401 en verandert het niets. Alleen
  `GET /api/health` werkt zonder.
- **Waarom:** zonder token kan iedereen in het netwerk die het adres kent de
  warmtepomp schakelen.
- **Waar het komt:** zet dezelfde waarde in de Homey-flow, als header
  `Authorization: Bearer <api_token>`, of als `?token=<api_token>` achter het
  adres (voor kaarten die geen headers kunnen zetten).
- **Los van de Weheat-login:** het script logt zelf in bij Weheat met de
  `username` en `password` in `config.json`, en houdt dat Weheat-token voor
  zichzelf. Homey heeft je Weheat-wachtwoord nooit nodig.
- **Leeg laten** zet de controle uit: het script logt een waarschuwing bij het
  starten, en iedereen in het netwerk kan de pomp schakelen. Niet aanbevolen.

Bedenk je eigen lange willekeurige tekst, bijvoorbeeld:

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```

### In Homey

**HomeyScript** (een kaart "Voer een script uit met argument"; het argument
is `opentherm` of `curve`):

```javascript
const BASE = "http://192.168.1.20:8095";   // de computer waar WeHeat-Control draait
const TOKEN = "jouw api_token";            // of "" zonder token

const mode = args[0];                      // "opentherm" of "curve"
const r = await fetch(`${BASE}/api/mode`, {
  method: "POST",
  headers: { "Content-Type": "application/json", ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}) },
  body: JSON.stringify({ mode }),
});
const answer = await r.json();
if (!r.ok) throw new Error(answer.error);
return answer.mode;
```

**Zonder script**: gebruik een HTTP-verzoekkaart (bijvoorbeeld uit de app
"HTTP request flow cards"): methode `POST`, adres
`http://192.168.1.20:8095/api/mode?token=<jouw api_token>`, body
`{"mode": "opentherm"}` (of `"curve"`), content type `application/json`.

Gewoon HTTP: houd de API binnen je eigen netwerk, zet de poort niet open naar
internet.

### Laten draaien (Linux, systemd)

`/etc/systemd/system/weheat-control.service`:

```ini
[Unit]
Description=WeHeat-Control API
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/opt/weheat-control
ExecStart=/opt/weheat-control/.venv/bin/python weheat_control.py serve
Restart=on-failure
User=weheat

[Install]
WantedBy=multi-user.target
```

```sh
sudo systemctl enable --now weheat-control
journalctl -u weheat-control -f        # elke schakeling wordt gelogd
```

## Disclaimer

WeHeat-Control is een persoonlijk project. Het is niet gemaakt, ondersteund
of goedgekeurd door Weheat. Het gebruikt de portaal-API van Weheat, die geen
openbare API is, dus het kan ophouden met werken zodra Weheat iets verandert,
en het gebruik ervan is misschien niet wat Weheat bedoelt.

Je gebruikt het **op eigen risico**. Het schakelt de warmtepomp alleen tussen
twee standen die Weheat zelf aanbiedt, maar jij bent verantwoordelijk voor hoe
je het gebruikt: controleer dat je huis warm genoeg blijft, dat de pomp terug
gaat naar zijn stooklijn, en dat het resultaat is wat je verwacht. De maker
kan niet aansprakelijk worden gesteld voor schade, kosten of andere gevolgen
van het gebruik van deze software, zoals een koud huis, een gemiste
terugschakeling, hogere energiekosten of problemen met je Weheat-account of
garantie.

## Licentie

MIT, zie [LICENSE](LICENSE): vrij te gebruiken, aan te passen en te delen,
geleverd "zoals het is", zonder garantie of aansprakelijkheid. (De
licentietekst zelf is Engels; die geldt.)
