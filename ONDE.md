# Onde — protocole des enceintes connectées maison

> Copie de la spécification tenue dans le dépôt Domus (`docs/onde.md`). Implémentation : `app/onde.py`.

**Version 1** · novembre 2026

Onde est le protocole qui permet à une enceinte faite maison de se présenter sur le réseau
local et d'être pilotée par Domus (ou par n'importe quel autre logiciel) : volume, sourdine,
lecture, pause, piste suivante ou précédente, choix de la source, et ce qui est en train de
jouer. L'enceinte reste maîtresse de son matériel : Onde décrit seulement comment on lui
parle.

Le protocole a été pensé pour durer :

- **une découverte standard** (mDNS / DNS-SD, comme AirPlay ou Chromecast) : l'enceinte
  s'annonce, Domus la trouve sans qu'on tape d'adresse ;
- **un identifiant stable** : l'enceinte reste la même pour Domus si elle change d'adresse IP ;
- **un état complet et normalisé** plutôt qu'une collection de champs propres à une
  implémentation ;
- **des capacités annoncées** : l'enceinte dit ce qu'elle sait faire *maintenant* (par
  exemple, la pause n'est pas possible quand Spotify Connect pilote la lecture), et le client
  n'a pas à le deviner ;
- **le temps réel** par Server-Sent Events, sans interrogation en boucle ;
- **des erreurs typées**, pour que le client explique le problème au lieu d'afficher « échec ».

Tout est en HTTP/1.1 et JSON (UTF-8), sur le réseau local.

---

## 1. Découverte

L'enceinte publie un service DNS-SD de type **`_onde._tcp`** :

| Champ          | Valeur                                                        |
|----------------|---------------------------------------------------------------|
| Nom d'instance | le nom de l'enceinte, ex. `Enceinte Chambre`                  |
| Type           | `_onde._tcp.local.`                                           |
| Port           | le port HTTP qui sert Onde (5001 pour le tableau de bord Pi)  |
| TXT `v`        | version majeure du protocole : `1`                            |
| TXT `path`     | préfixe des routes, `/onde/v1`                                |
| TXT `id`       | facultatif : identifiant de l'enceinte (voir `hello`)          |

Le client construit l'**URL de base** : `http://<adresse>:<port><path>`, par exemple
`http://192.168.1.56:5001/onde/v1`. Toutes les routes ci-dessous sont relatives à cette base.

Quand le multicast ne passe pas (Docker Desktop, VLAN séparés…), le client peut aussi
interroger une adresse donnée à la main : il essaie `GET http://<ip>:<port>/onde/v1/hello`,
avec le port **5001** par défaut.

## 2. Identité : `GET /hello`

Toujours accessible, même si l'enceinte est protégée par une clé (§ 7). C'est la route que le
client appelle pour reconnaître une enceinte Onde.

```json
{
  "onde": 1,
  "id": "5f0c2a91d4e8b7c3",
  "name": "Enceinte Chambre",
  "room": "Chambre",
  "model": "Raspberry Pi · HiFiBerry DAC+",
  "software": "dashboard-enceinte",
  "auth": "none",
  "features": ["volume", "mute", "transport", "source", "metadata", "cover", "events"]
}
```

| Champ      | Sens                                                                                 |
|------------|--------------------------------------------------------------------------------------|
| `onde`     | version majeure du protocole parlée par l'enceinte (entier)                          |
| `id`       | identifiant **stable** (lettres minuscules, chiffres, `-`), tiré au premier démarrage et conservé : il ne change ni avec l'IP ni avec le nom |
| `name`     | nom affiché                                                                          |
| `room`     | pièce suggérée par l'enceinte (le client peut l'ignorer)                             |
| `model`    | matériel, libre                                                                      |
| `software` | logiciel qui implémente Onde, libre                                                  |
| `auth`     | `none` ou `key` (§ 7)                                                                |
| `features` | ce que l'implémentation sait faire en général (liste ci-dessous)                     |

Fonctions connues :

| Fonction    | Signification                                                     |
|-------------|-------------------------------------------------------------------|
| `volume`    | volume réglable de 0 à 100                                        |
| `mute`      | sourdine                                                          |
| `transport` | lecture, pause, suivante, précédente (selon la source, voir `controls`) |
| `source`    | choix de la source                                                |
| `metadata`  | titre, artiste, album                                             |
| `cover`     | pochette                                                          |
| `events`    | flux temps réel `GET /events`                                     |

Un client ignore les fonctions qu'il ne connaît pas.

## 3. État : `GET /state`

```json
{
  "rev": 128,
  "name": "Enceinte Chambre",
  "room": "Chambre",
  "playback": "playing",
  "volume": 42,
  "muted": false,
  "source": "airplay",
  "sources": [
    { "id": "spotify", "name": "Spotify", "active": false },
    { "id": "airplay", "name": "AirPlay", "active": true }
  ],
  "controls": { "play": true, "pause": true, "next": true, "previous": true },
  "track": {
    "title": "Clair de lune",
    "artist": "Claude Debussy",
    "album": "Suite bergamasque",
    "duration_ms": 302000,
    "cover": "cover?k=9c1e07"
  },
  "output": "HiFiBerry DAC+",
  "signal": { "wifi_dbm": -61, "temperature_c": 41.2 }
}
```

| Champ      | Type                     | Sens |
|------------|--------------------------|------|
| `rev`      | entier                   | augmente à chaque changement de l'état (hors `signal`) ; repart de 1 au redémarrage de l'enceinte |
| `playback` | `playing` · `paused` · `idle` | `idle` : aucune source active ou rien à jouer |
| `volume`   | 0 à 100                  | volume de l'enceinte (pas celui du téléphone) |
| `muted`    | booléen                  | sourdine |
| `source`   | chaîne ou `null`         | identifiant de la source active |
| `sources`  | liste                    | sources que l'enceinte sait activer |
| `controls` | objet de booléens        | commandes de lecture **possibles maintenant**. Un client grise celles qui sont à `false` |
| `track`    | objet ou `null`          | ce qui joue ; chaque champ peut être `null` |
| `track.cover` | URL ou `null`         | pochette : relative à l'URL de base, ou absolue (`https://…`). Elle **change quand l'image change**, le client peut donc la mettre en cache sous cette URL |
| `output`   | chaîne                   | sortie audio utilisée, libre |
| `signal`   | objet                    | santé, indicatif : `wifi_dbm`, `temperature_c` (absents si inconnus) |

Un client **ignore les champs inconnus** : une enceinte peut en ajouter sans changer de version.

## 4. Commandes : `POST /command`

Une commande par requête, en JSON :

```json
{ "cmd": "volume", "value": 35 }
```

| `cmd`         | `value`                  | Effet |
|---------------|--------------------------|-------|
| `play`        | —                        | reprend la lecture |
| `pause`       | —                        | met en pause |
| `toggle`      | —                        | lecture ↔ pause |
| `next`        | —                        | piste suivante |
| `previous`    | —                        | piste précédente |
| `volume`      | entier 0 à 100           | règle le volume ; un volume > 0 retire la sourdine |
| `volume_step` | entier −100 à 100        | monte ou baisse le volume d'autant |
| `mute`        | booléen                  | sourdine ou non |
| `source`      | identifiant ou `null`    | active une source (et arrête les autres) ; `null` arrête tout |

Réponse en cas de succès, **200** avec le nouvel état :

```json
{ "ok": true, "state": { "rev": 129, "volume": 35, "…": "…" } }
```

En cas d'échec, un code HTTP 4xx ou 5xx et une erreur typée :

```json
{ "ok": false, "error": { "code": "unsupported", "message": "La lecture Spotify se pilote depuis l'application Spotify." } }
```

| `code`            | HTTP | Sens |
|-------------------|------|------|
| `bad_request`     | 400  | JSON illisible, `value` manquante ou hors bornes |
| `unknown_command` | 400  | `cmd` inconnue de l'enceinte |
| `unsupported`     | 409  | commande connue mais impossible dans l'état actuel (voir `controls`) |
| `unavailable`     | 409  | aucune source active |
| `unauthorized`    | 401  | clé absente ou fausse (§ 7) |
| `failed`          | 502  | le matériel ou un service de l'enceinte a refusé |

`message` est une phrase en français, destinée à être montrée telle quelle.

## 5. Temps réel : `GET /events`

Flux [Server-Sent Events](https://html.spec.whatwg.org/multipage/server-sent-events.html)
(`Content-Type: text/event-stream`).

- À la connexion, l'enceinte envoie immédiatement l'état complet.
- Ensuite, elle envoie l'état complet **à chaque changement** (même forme que `GET /state`).
- Sans changement, elle envoie un commentaire `: ping` au moins toutes les **15 secondes**.
  Un client qui ne reçoit rien pendant 45 secondes considère la connexion morte et se reconnecte.

```
event: state
data: {"rev":130,"playback":"paused","volume":35,…}

: ping
```

Envoyer l'état complet plutôt qu'un différentiel rend le client trivial et sans dérive : le
dernier message reçu est toujours la vérité. Un état fait environ 600 octets.

Après une coupure, le client se reconnecte avec un délai croissant (1 s, 2 s, 5 s, puis
toutes les 10 s au plus).

## 6. Pochette : `GET /cover`

Renvoie l'image de la piste en cours (`image/jpeg`, `image/png`…), ou **404** s'il n'y en a pas.
Les paramètres de requête (`?k=…`) ne servent qu'à distinguer les versions dans les caches.

## 7. Clé d'accès (facultative)

Par défaut (`"auth": "none"`), l'enceinte accepte toute requête du réseau local, comme son
tableau de bord.

Si elle est configurée avec une clé (`"auth": "key"`), toutes les routes **sauf `hello`**
exigent l'en-tête :

```
Authorization: Bearer <clé>
```

Sans clé valide : **401** et `{"ok": false, "error": {"code": "unauthorized", …}}`.

## 8. Versions et compatibilité

- `onde` (dans `hello`) et `v` (dans le TXT mDNS) portent la **version majeure**. Elle ne
  change que si un client de la version précédente ne peut plus fonctionner ; les routes
  changent alors de préfixe (`/onde/v2`).
- Ajouter un champ, une fonction (`features`), une commande ou une source ne change pas la
  version : les clients ignorent ce qu'ils ne connaissent pas, et une commande inconnue
  reçoit `unknown_command`.

## 9. Multiroom

Onde v1 pilote **chaque enceinte séparément**. Le regroupement en zones (volume commun,
lecture et pause ensemble) est fait par le client : c'est ce que fait le gestionnaire
multiroom de Domus. La **lecture synchronisée** du même son sur plusieurs enceintes demande
un flux audio commun (par exemple Snapcast) ; elle est prévue sous la fonction `sync`,
non définie dans cette version.

## 10. Exemples avec curl

```sh
curl http://192.168.1.56:5001/onde/v1/hello
curl http://192.168.1.56:5001/onde/v1/state
curl -X POST -H 'Content-Type: application/json' -d '{"cmd":"volume","value":30}' http://192.168.1.56:5001/onde/v1/command
curl -X POST -H 'Content-Type: application/json' -d '{"cmd":"toggle"}' http://192.168.1.56:5001/onde/v1/command
curl -N http://192.168.1.56:5001/onde/v1/events
avahi-browse -rt _onde._tcp
```

---

## Implémentation dans ce tableau de bord

| Élément | Fichier |
|---|---|
| Routes `/onde/v1/*`, veilleur d'état, clé | `app/onde.py` (enregistré dans `app/__init__.py`) |
| Identifiant stable | `/etc/camilladsp/onde.json` (`ONDE_ID_FILE`), dérivé de `/etc/machine-id` |
| Clé facultative | `ONDE_KEY` dans `/etc/default/enceinte` |
| Annonce mDNS | `deploy/onde.service` → `/etc/avahi/services/onde.service` (`install.sh`, ou `docker/entrypoint.sh`) |
| Lecture / pause et pochette Spotify | `deploy/librespot-event.sh` (événements `playing` / `paused`, variable `COVERS`) |

Choix faits ici :

- **`playback`** ne vient pas de la carte son : CamillaDSP la garde ouverte en permanence,
  elle semblerait toujours « en lecture ». AirPlay donne l'état par MPRIS (`PlaybackStatus`),
  Spotify par les événements de librespot.
- **`controls`** : tout est vrai pendant une session AirPlay (MPRIS) ; avec Spotify tout est
  faux, librespot ne se pilotant que depuis l'application Spotify. Volume, sourdine et source
  restent possibles dans les deux cas (ALSA et systemd).
- **`/events`** : un seul fil relit l'état toutes les 2 s tant qu'un client écoute (les
  changements faits depuis le téléphone remontent ainsi), et se réveille aussitôt après une
  commande Onde ou une action du tableau de bord. Wi-Fi et température ne comptent pas comme
  un changement.

Mise à jour d'une enceinte déjà installée :

```sh
cd ~/dashbaord-enceinte && git pull
sudo deploy/install.sh        # annonce mDNS + nouveau script d'événements librespot
curl http://localhost:5001/onde/v1/hello
```
