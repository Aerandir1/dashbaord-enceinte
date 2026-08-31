// Ordre des entrées dans le sélecteur : il pilote la position du témoin.
// "musicassistant" occupe sa place mais n'est pas encore branchée côté
// serveur ; elle est annoncée dans l'interface et reste non sélectionnable.
const SOURCE_ORDER = ['spotify', 'airplay', 'musicassistant', 'none'];
const SOURCE_PLANNED = new Set(['musicassistant']);

async function callApi(url, payload) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    alert(data.error || 'Action impossible');
    return null;
  }

  return response.json();
}

async function fetchState() {
  const response = await fetch('/api/state');
  if (!response.ok) return null;
  return response.json();
}

let _stateEventSource = null;

function startRealtimeSync() {
  if (!window.EventSource) return;
  if (_stateEventSource) _stateEventSource.close();

  _stateEventSource = new EventSource('/api/stream');

  _stateEventSource.addEventListener('state', (event) => {
    try {
      const state = JSON.parse(event.data);
      render(state);
    } catch (_) {
      // ignore payload invalid
    }
  });

  _stateEventSource.onerror = () => {
    // tentative de reconnexion contrôlée
    if (_stateEventSource) {
      _stateEventSource.close();
      _stateEventSource = null;
    }
    setTimeout(startRealtimeSync, 1500);
  };
}

const _systemQuery = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)');

function _isManualOverride() {
  const v = sessionStorage.getItem('dashboard-theme');
  return v === 'light' || v === 'dark';
}

function applyTheme(theme, synced) {
  const body = document.body;
  const toggle = document.getElementById('themeToggle');
  body.dataset.theme = theme;
  if (toggle) {
    const isLight = theme === 'light';
    if (synced) {
      toggle.textContent = isLight ? '☀️ Système' : '🌙 Système';
      toggle.setAttribute('aria-label', 'Thème synchronisé avec le système — cliquez pour choisir manuellement');
      toggle.classList.add('synced');
    } else {
      toggle.textContent = isLight ? '☀️ Jour' : '🌙 Nuit';
      toggle.setAttribute('aria-label', isLight ? 'Activer le thème nuit' : 'Activer le thème jour');
      toggle.classList.remove('synced');
    }
  }
}

function initTheme() {
  if (_isManualOverride()) {
    applyTheme(sessionStorage.getItem('dashboard-theme'), false);
  } else {
    const preferLight = _systemQuery && _systemQuery.matches;
    applyTheme(preferLight ? 'light' : 'dark', true);
  }

  // Écoute les changements du thème système en temps réel
  if (_systemQuery && _systemQuery.addEventListener) {
    _systemQuery.addEventListener('change', (e) => {
      if (!_isManualOverride()) {
        applyTheme(e.matches ? 'light' : 'dark', true);
      }
    });
  }
}

// L'écran de chargement s'efface au tout premier état reçu (que ce soit via
// /api/state ou le premier événement SSE), une seule fois.
let _splashDismissed = false;
function dismissSplash() {
  if (_splashDismissed) return;
  _splashDismissed = true;
  document.body.classList.add('is-ready');
  // Retiré du DOM après le fondu pour ne rien laisser traîner.
  setTimeout(() => document.getElementById('appSplash')?.remove(), 500);
}

// ── Pochette AirPlay ──
// Le serveur ne renvoie pas d'URL : on la construit à partir de la piste et on
// réessaie de la charger, car shairport écrit l'image un court instant APRÈS
// les métadonnées. On précharge (jamais de cadre cassé) et on n'affiche que si
// l'image se charge vraiment ; sinon on garde l'orbe.
let _coverTrack = null;
let _coverTimer = null;

function _hideCover() {
  const img = document.getElementById('npCover');
  const art = document.querySelector('.np-art');
  if (img) { img.hidden = true; img.removeAttribute('src'); }
  if (art) art.classList.remove('has-cover');
}

function updateCover(state) {
  const img = document.getElementById('npCover');
  const art = document.querySelector('.np-art');
  if (!img || !art) return;

  const meta = (state.active_service === 'airplay' && state.airplay_metadata) || {};
  const track = meta.title ? `${meta.title}|${meta.artist || ''}` : null;

  if (track === _coverTrack) return; // même piste : rien à refaire
  _coverTrack = track;
  clearTimeout(_coverTimer);
  _hideCover(); // nouvelle piste (ou plus d'AirPlay) : on repart de l'orbe

  if (!track) return;

  const url = `/api/airplay/cover?t=${encodeURIComponent(track)}`;
  let attempts = 0;
  const tryLoad = () => {
    if (_coverTrack !== track) return; // piste re-changée entre-temps
    const probe = new Image();
    probe.onload = () => {
      if (_coverTrack !== track) return;
      img.src = url;
      img.hidden = false;
      art.classList.add('has-cover');
    };
    probe.onerror = () => {
      if (_coverTrack !== track || attempts++ >= 6) return; // abandon → orbe
      _coverTimer = setTimeout(tryLoad, 700); // pochette pas encore écrite
    };
    probe.src = url;
  };
  tryLoad();
}

function render(state) {
  if (!state) return;

  const services = state.services || {};

  document.getElementById('deviceLine').textContent = `${state.device_name} · ${state.room}`;
  document.getElementById('trackName').textContent = state.current_track;
  document.getElementById('artistName').textContent = state.current_artist;
  document.getElementById('volumeValue').textContent = state.volume;
  const volumeSlider = document.getElementById('volumeSlider');
  volumeSlider.value = state.volume;
  // Remplit la piste jusqu'à la valeur (dégradé côté CSS).
  volumeSlider.style.setProperty('--val', `${state.volume}%`);
  // L'aurore et l'orbe "respirent" uniquement quand ça joue réellement.
  document.body.classList.toggle('is-playing', Boolean(state.is_playing));
  // ── Wi-Fi (donnees reelles : /proc/net/wireless + nmcli) ──
  const wifi = state.wifi || {};
  document.getElementById('wifiSsid').textContent = wifi.ssid || 'Non connecté';
  document.getElementById('wifiSignal').textContent =
    wifi.signal_dbm === null || wifi.signal_dbm === undefined ? '—' : `${wifi.signal_dbm} dBm`;

  const bars = Number(wifi.bars) || 0;
  const wifiIndicator = document.getElementById('wifiBars');
  wifiIndicator.setAttribute('aria-label', `Signal ${bars} sur 5`);
  wifiIndicator.querySelectorAll('.wifi-bar').forEach((bar, index) => {
    bar.classList.toggle('active', index < bars);
  });

  // ── Audio (format reellement joue, lu dans /proc/asound) ──
  // La sortie est un choix : on ne la reecrit pas pendant que l'utilisateur
  // deroule la liste, sous peine de refermer son menu.
  const outputSelect = document.getElementById('outputSelect');
  if (outputSelect && document.activeElement !== outputSelect) {
    const wanted = (state.outputs || []).map((o) => o.id).join('|');
    if (outputSelect.dataset.signature !== wanted) {
      outputSelect.dataset.signature = wanted;
      outputSelect.innerHTML = '';
      (state.outputs || []).forEach((o) => {
        const option = document.createElement('option');
        option.value = o.id;
        option.textContent = o.label;
        outputSelect.appendChild(option);
      });
    }
    if (state.active_output) outputSelect.value = state.active_output;
  }
  const stream = state.audio_stream || {};
  document.getElementById('audioFormat').textContent = stream.playing
    ? `${(stream.rate / 1000).toFixed(1)} kHz · ${stream.bits} bits · ` +
      (stream.channels === 2 ? 'stéréo' : `${stream.channels} ch`)
    : 'Au repos';

  // ── Sante systeme ──
  const system = state.system || {};
  document.getElementById('cpuTemp').textContent =
    system.temperature_c === null || system.temperature_c === undefined
      ? '—'
      : `${system.temperature_c} °C`;

  const powerHealth = document.getElementById('powerHealth');
  powerHealth.textContent = system.power_label || '—';
  powerHealth.classList.toggle('health-ok', system.power_ok === true);
  powerHealth.classList.toggle('health-warn', system.power_ok === false);

  document.getElementById('updatedAt').textContent = state.updated_since || state.updated_at;

  // ── Sélecteur de source ──
  // Une seule source peut être active : la sélection est directement l'état
  // réel renvoyé par le serveur, jamais un état d'interface parallèle.
  const activeSource = state.active_service || 'none';
  const selector = document.getElementById('sourceSelector');
  if (selector) {
    selector.dataset.source = activeSource;
    selector.dataset.index = SOURCE_ORDER.indexOf(activeSource);
    selector.querySelectorAll('.source-option').forEach((option) => {
      option.setAttribute('aria-checked', String(option.dataset.source === activeSource));
    });
  }

  const sourceHint = document.getElementById('sourceHint');
  if (sourceHint) {
    sourceHint.textContent = state.active_service
      ? `${state.active_service_name} est la seule source active.`
      : "Aucune source active. Choisis une entrée pour l'allumer.";
  }

  const playbackSource = document.getElementById('playbackSource');
  if (playbackSource) {
    playbackSource.textContent =
      state.active_service_name || services[state.active_service]?.name || 'Aucune';
  }

  const hasPlayableSource = Boolean(state.active_service) && Boolean(services[state.active_service]?.online);
  document.querySelectorAll('[data-playback]').forEach((button) => {
    button.disabled = !hasPlayableSource;
  });

  const playBtn = document.getElementById('playBtn');
  playBtn.textContent = state.is_playing ? '⏸' : '▶';
  playBtn.setAttribute('aria-label', state.is_playing ? 'Pause' : 'Lecture');
  playBtn.setAttribute('title', state.is_playing ? 'Pause' : 'Lecture');
  document.getElementById('muteBtn').textContent = state.muted ? 'Activer le son' : 'Muet';

  updateCover(state);

  // Premier rendu réussi : on peut effacer l'écran de chargement.
  dismissSplash();
}

document.querySelectorAll('[data-playback]').forEach((button) => {
  button.addEventListener('click', async () => {
    const state = await callApi('/api/playback', { action: button.dataset.playback });
    render(state);
  });
});

// Changer de sortie fait rouvrir un autre périphérique par CamillaDSP :
// une brève coupure du son est normale.
document.getElementById('outputSelect')?.addEventListener('change', async (event) => {
  const select = event.target;
  select.disabled = true;
  try {
    const state = await callApi('/api/output', { output: select.value });
    if (state) render(state);
  } finally {
    select.disabled = false;
  }
});

document.getElementById('volDown').addEventListener('click', async () => {
  const state = await callApi('/api/volume', { delta: -5 });
  render(state);
});

document.getElementById('volUp').addEventListener('click', async () => {
  const state = await callApi('/api/volume', { delta: 5 });
  render(state);
});

// Remplissage live pendant le glissement (avant l'envoi, qui se fait au relâché).
document.getElementById('volumeSlider').addEventListener('input', (event) => {
  event.target.style.setProperty('--val', `${event.target.value}%`);
});

document.getElementById('volumeSlider').addEventListener('change', async (event) => {
  const state = await callApi('/api/volume', { volume: Number(event.target.value) });
  render(state);
});

document.getElementById('muteBtn').addEventListener('click', async () => {
  const currentText = document.getElementById('muteBtn').textContent;
  const mute = currentText === 'Muet';
  const state = await callApi('/api/volume', { mute });
  render(state);
});

document.getElementById('themeToggle').addEventListener('click', () => {
  const nextTheme = document.body.dataset.theme === 'light' ? 'dark' : 'light';
  sessionStorage.setItem('dashboard-theme', nextTheme);
  applyTheme(nextTheme, false);
});

// Double-clic : retour à la synchronisation avec le système
document.getElementById('themeToggle').addEventListener('dblclick', (e) => {
  e.preventDefault();
  sessionStorage.removeItem('dashboard-theme');
  const preferLight = _systemQuery && _systemQuery.matches;
  applyTheme(preferLight ? 'light' : 'dark', true);
});

// ── Sélection de la source ──
// Choisir une source démarre son service et arrête l'autre côté serveur.
// L'opération prend une seconde ou deux : on l'indique au lieu de laisser
// croire que le clic n'a rien fait.
const sourceSelector = document.getElementById('sourceSelector');

async function selectSource(source) {
  if (!sourceSelector || sourceSelector.classList.contains('is-busy')) return;
  // Le serveur ne connaît pas encore cette source : ne rien tenter.
  if (SOURCE_PLANNED.has(source)) return;
  if (source === (sourceSelector.dataset.source || 'none')) return;

  // Le témoin part immédiatement vers la cible : le retour est instantané,
  // même si le service met un moment à démarrer.
  sourceSelector.classList.add('is-busy');
  sourceSelector.dataset.source = source;
  sourceSelector.dataset.index = SOURCE_ORDER.indexOf(source);

  try {
    const state = await callApi('/api/source', { source });
    if (state) render(state);
    else await fetchState().then((s) => s && render(s));
  } finally {
    sourceSelector.classList.remove('is-busy');
  }
}

if (sourceSelector) {
  sourceSelector.querySelectorAll('.source-option').forEach((option) => {
    option.addEventListener('click', () => selectSource(option.dataset.source));
  });

  // Flèches gauche/droite entre les entrées, comme un groupe de boutons radio.
  sourceSelector.addEventListener('keydown', (event) => {
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
    if (!step) return;
    event.preventDefault();

    // Saute les entrées annoncées mais pas encore disponibles, plutôt que d'y
    // laisser le focus bloqué sur un choix impossible.
    let index = SOURCE_ORDER.indexOf(sourceSelector.dataset.source || 'none');
    for (let hops = 0; hops < SOURCE_ORDER.length; hops += 1) {
      index = (index + step + SOURCE_ORDER.length) % SOURCE_ORDER.length;
      if (!SOURCE_PLANNED.has(SOURCE_ORDER[index])) break;
    }

    const target = sourceSelector.querySelector(`[data-source="${SOURCE_ORDER[index]}"]`);
    if (target) {
      target.focus();
      selectSource(SOURCE_ORDER[index]);
    }
  });
}

initTheme();

fetchState().then((state) => {
  if (state) render(state);
});

startRealtimeSync();

// Filet de sécurité : si aucun état n'arrive (serveur en erreur), on n'enferme
// pas l'utilisateur sur l'écran de chargement — la coquille reste utilisable.
setTimeout(dismissSplash, 8000);

// Service Worker : au prochain lancement, la coquille se peint instantanément
// depuis le cache, avant même le premier aller-retour réseau. Il n'est
// enregistré qu'en contexte sécurisé (HTTPS ou localhost) ; en HTTP sur le LAN
// le navigateur le refuse, ce bloc reste donc sans effet jusqu'à un passage en
// HTTPS (voir le reverse proxy nginx dans deploy/).
if ('serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}

