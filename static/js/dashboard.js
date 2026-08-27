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
  const librespotifyOnline = Boolean(services.spotify?.online);
  const airplayOnline = Boolean(services.airplay?.online);

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

  const librespotifyStatus = document.getElementById('status-spotify');
  const airplayStatus = document.getElementById('status-airplay');
  const librespotifyChip = document.getElementById('chip-spotify');
  const airplayChip = document.getElementById('chip-airplay');
  const activeService = document.getElementById('activeService');
  const playbackSource = document.getElementById('playbackSource');

  if (librespotifyStatus) librespotifyStatus.textContent = librespotifyOnline ? 'En ligne' : 'Hors ligne';
  if (airplayStatus) airplayStatus.textContent = airplayOnline ? 'En ligne' : 'Hors ligne';

  if (librespotifyChip) {
    librespotifyChip.classList.toggle('online', librespotifyOnline);
    librespotifyChip.classList.toggle('offline', !librespotifyOnline);
    librespotifyChip.classList.toggle('active', state.active_service === 'spotify');
  }

  if (airplayChip) {
    airplayChip.classList.toggle('online', airplayOnline);
    airplayChip.classList.toggle('offline', !airplayOnline);
    airplayChip.classList.toggle('active', state.active_service === 'airplay');
  }

  if (activeService) {
    activeService.textContent =
      state.active_service_name || services[state.active_service]?.name || 'Aucune';
  }

  if (playbackSource) {
    playbackSource.textContent =
      state.active_service_name || services[state.active_service]?.name || 'Aucune';
  }

  const toggleLibrespotify = document.getElementById('toggleSpotify');
  const toggleAirplay = document.getElementById('toggleAirplay');
  if (toggleLibrespotify) {
    toggleLibrespotify.textContent = librespotifyOnline
      ? 'Couper Spotify'
      : 'Activer Spotify';
  }
  if (toggleAirplay) {
    toggleAirplay.textContent = airplayOnline
      ? 'Couper AirPlay'
      : 'Activer AirPlay';
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

document.getElementById('chip-spotify').addEventListener('click', async () => {
  const state = await callApi('/api/services', { service: 'spotify', action: 'select' });
  render(state);
});

document.getElementById('chip-airplay').addEventListener('click', async () => {
  const state = await callApi('/api/services', { service: 'airplay', action: 'select' });
  render(state);
});

document.getElementById('toggleSpotify').addEventListener('click', async () => {
  const state = await callApi('/api/services', { service: 'spotify', action: 'toggle' });
  render(state);
});

document.getElementById('toggleAirplay').addEventListener('click', async () => {
  const state = await callApi('/api/services', { service: 'airplay', action: 'toggle' });
  render(state);
});

initTheme();

fetchState().then((state) => {
  if (state) render(state);
});

startRealtimeSync();

