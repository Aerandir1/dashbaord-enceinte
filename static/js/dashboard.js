// Ordre des entrées dans le sélecteur : il pilote la position du témoin.
const SOURCE_ORDER = ['spotify', 'airplay', 'none'];

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

function render(state) {
  if (!state) return;

  const services = state.services || {};

  document.getElementById('deviceLine').textContent = `${state.device_name} · ${state.room}`;
  document.getElementById('trackName').textContent = state.current_track;
  document.getElementById('artistName').textContent = state.current_artist;
  document.getElementById('volumeValue').textContent = state.volume;
  document.getElementById('volumeSlider').value = state.volume;
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
  document.getElementById('audioOutput').textContent = state.audio_output || '—';
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

  const eqPreset = document.getElementById('eqPreset');
  if (state.eq_preset && eqPreset) {
    eqPreset.value = state.eq_preset;
  }

  const eqBands = state.eq_bands || {};
  const eqIds = {
    '60Hz': 'eq60',
    '230Hz': 'eq230',
    '910Hz': 'eq910',
    '3.6kHz': 'eq3600',
    '14kHz': 'eq14000'
  };

  Object.entries(eqIds).forEach(([band, id]) => {
    const slider = document.getElementById(id);
    const value = eqBands[band] ?? 0;
    if (slider) slider.value = value;
    const label = document.getElementById(`${id}Value`);
    if (label) label.textContent = `${value} dB`;
  });
}

document.querySelectorAll('[data-playback]').forEach((button) => {
  button.addEventListener('click', async () => {
    const state = await callApi('/api/playback', { action: button.dataset.playback });
    render(state);
  });
});

document.getElementById('volDown').addEventListener('click', async () => {
  const state = await callApi('/api/volume', { delta: -5 });
  render(state);
});

document.getElementById('volUp').addEventListener('click', async () => {
  const state = await callApi('/api/volume', { delta: 5 });
  render(state);
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

document.getElementById('eqPreset').addEventListener('change', async (event) => {
  if (event.target.value === 'custom') return;
  const state = await callApi('/api/eq', { preset: event.target.value });
  render(state);
});

document.querySelectorAll('.eq-slider').forEach((slider) => {
  slider.addEventListener('input', () => {
    const label = document.getElementById(`${slider.id}Value`);
    if (label) label.textContent = `${slider.value} dB`;
  });

  slider.addEventListener('change', async () => {
    const state = await callApi('/api/eq', {
      band: slider.dataset.band,
      gain: Number(slider.value)
    });
    render(state);
  });
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
    const current = SOURCE_ORDER.indexOf(sourceSelector.dataset.source || 'none');
    const next = (current + step + SOURCE_ORDER.length) % SOURCE_ORDER.length;
    const target = sourceSelector.querySelector(`[data-source="${SOURCE_ORDER[next]}"]`);
    if (target) {
      target.focus();
      selectSource(SOURCE_ORDER[next]);
    }
  });
}

initTheme();

fetchState().then((state) => {
  if (state) render(state);
});

startRealtimeSync();

