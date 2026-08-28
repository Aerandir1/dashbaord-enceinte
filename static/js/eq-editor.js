/* Éditeur d'égaliseur paramétrique.
 *
 * La courbe affichée n'est pas une approximation graphique : c'est la réponse
 * en fréquence réellement calculée à partir des coefficients des biquads, avec
 * les mêmes formules (RBJ Audio EQ Cookbook) que celles employées par
 * CamillaDSP. Ce que l'on voit correspond donc à ce que l'on entend.
 */

(function () {
  const canvas = document.getElementById('eqCanvas');
  if (!canvas) return;

  const SAMPLE_RATE = 44100;
  const FREQ_MIN = 20;
  const FREQ_MAX = 20000;
  const DB_RANGE = 18; // échelle verticale : ±18 dB

  const GRID_FREQS = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000];
  const GRID_DBS = [-12, -6, 0, 6, 12];

  // Couleurs des bandes, reprises pour le point et sa courbe individuelle.
  const BAND_COLORS = [
    '#38bdf8', '#a78bfa', '#f472b6', '#fb923c',
    '#4ade80', '#facc15', '#22d3ee', '#f87171'
  ];

  const TYPES_WITH_GAIN = ['Peaking', 'Lowshelf', 'Highshelf'];
  const TYPE_LABELS = {
    Peaking: 'Cloche',
    Lowshelf: 'Plateau grave',
    Highshelf: 'Plateau aigu',
    Highpass: 'Coupe-bas',
    Lowpass: 'Coupe-haut',
    Notch: 'Réjecteur'
  };

  let bands = [];
  let autoPreamp = true;
  let preampApplied = 0;
  let selectedId = null;
  let dragging = null;
  let nextId = 1;

  const ctx = canvas.getContext('2d');

  // ── Mathématiques des filtres ────────────────────────────────────────────
  // Coefficients d'un biquad, RBJ Audio EQ Cookbook.
  function coefficients(band) {
    const A = Math.pow(10, band.gain / 40);
    const w0 = (2 * Math.PI * band.freq) / SAMPLE_RATE;
    const cosw = Math.cos(w0);
    const sinw = Math.sin(w0);
    const alpha = sinw / (2 * band.q);
    const sqrtA = Math.sqrt(A);
    let b0, b1, b2, a0, a1, a2;

    switch (band.type) {
      case 'Lowshelf':
        b0 = A * ((A + 1) - (A - 1) * cosw + 2 * sqrtA * alpha);
        b1 = 2 * A * ((A - 1) - (A + 1) * cosw);
        b2 = A * ((A + 1) - (A - 1) * cosw - 2 * sqrtA * alpha);
        a0 = (A + 1) + (A - 1) * cosw + 2 * sqrtA * alpha;
        a1 = -2 * ((A - 1) + (A + 1) * cosw);
        a2 = (A + 1) + (A - 1) * cosw - 2 * sqrtA * alpha;
        break;
      case 'Highshelf':
        b0 = A * ((A + 1) + (A - 1) * cosw + 2 * sqrtA * alpha);
        b1 = -2 * A * ((A - 1) + (A + 1) * cosw);
        b2 = A * ((A + 1) + (A - 1) * cosw - 2 * sqrtA * alpha);
        a0 = (A + 1) - (A - 1) * cosw + 2 * sqrtA * alpha;
        a1 = 2 * ((A - 1) - (A + 1) * cosw);
        a2 = (A + 1) - (A - 1) * cosw - 2 * sqrtA * alpha;
        break;
      case 'Highpass':
        b0 = (1 + cosw) / 2;
        b1 = -(1 + cosw);
        b2 = (1 + cosw) / 2;
        a0 = 1 + alpha;
        a1 = -2 * cosw;
        a2 = 1 - alpha;
        break;
      case 'Lowpass':
        b0 = (1 - cosw) / 2;
        b1 = 1 - cosw;
        b2 = (1 - cosw) / 2;
        a0 = 1 + alpha;
        a1 = -2 * cosw;
        a2 = 1 - alpha;
        break;
      case 'Notch':
        b0 = 1;
        b1 = -2 * cosw;
        b2 = 1;
        a0 = 1 + alpha;
        a1 = -2 * cosw;
        a2 = 1 - alpha;
        break;
      default: // Peaking
        b0 = 1 + alpha * A;
        b1 = -2 * cosw;
        b2 = 1 - alpha * A;
        a0 = 1 + alpha / A;
        a1 = -2 * cosw;
        a2 = 1 - alpha / A;
    }
    return { b0, b1, b2, a0, a1, a2 };
  }

  // Amplitude en dB à une fréquence donnée, sans arithmétique complexe.
  function bandGainAt(band, freq) {
    const { b0, b1, b2, a0, a1, a2 } = coefficients(band);
    const w = (2 * Math.PI * freq) / SAMPLE_RATE;
    const phi = Math.pow(Math.sin(w / 2), 2);

    const num = Math.pow(b0 + b1 + b2, 2)
      - 4 * (b0 * b1 + 4 * b0 * b2 + b1 * b2) * phi
      + 16 * b0 * b2 * phi * phi;
    const den = Math.pow(a0 + a1 + a2, 2)
      - 4 * (a0 * a1 + 4 * a0 * a2 + a1 * a2) * phi
      + 16 * a0 * a2 * phi * phi;

    if (den <= 0 || num <= 0) return -120;
    return 10 * Math.log10(num / den);
  }

  function totalGainAt(freq) {
    let total = 0;
    for (const band of bands) {
      if (band.enabled) total += bandGainAt(band, freq);
    }
    return total;
  }

  // ── Conversions écran ↔ valeurs ──────────────────────────────────────────
  const logMin = Math.log10(FREQ_MIN);
  const logMax = Math.log10(FREQ_MAX);

  function freqToX(freq, width) {
    return ((Math.log10(freq) - logMin) / (logMax - logMin)) * width;
  }
  function xToFreq(x, width) {
    return Math.pow(10, logMin + (x / width) * (logMax - logMin));
  }
  function dbToY(db, height) {
    return height / 2 - (db / DB_RANGE) * (height / 2);
  }
  function yToDb(y, height) {
    return ((height / 2 - y) / (height / 2)) * DB_RANGE;
  }

  function cssVar(name, fallback) {
    const v = getComputedStyle(document.body).getPropertyValue(name).trim();
    return v || fallback;
  }

  // ── Rendu ────────────────────────────────────────────────────────────────
  function resize() {
    const ratio = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.round(rect.width * ratio);
    canvas.height = Math.round(rect.height * ratio);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    draw();
  }

  function draw() {
    const ratio = window.devicePixelRatio || 1;
    const W = canvas.width / ratio;
    const H = canvas.height / ratio;
    const muted = cssVar('--muted', '#9db0ca');
    const text = cssVar('--text', '#e7edf7');

    ctx.clearRect(0, 0, W, H);

    // Grille
    ctx.lineWidth = 1;
    ctx.font = '10px Inter, system-ui, sans-serif';
    ctx.fillStyle = muted;

    GRID_FREQS.forEach((f) => {
      const x = freqToX(f, W);
      ctx.strokeStyle = 'rgba(148,163,184,0.14)';
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, H - 12);
      ctx.stroke();
      const label = f >= 1000 ? `${f / 1000}k` : `${f}`;
      ctx.textAlign = f === FREQ_MIN ? 'left' : f === FREQ_MAX ? 'right' : 'center';
      ctx.fillText(label, x, H - 2);
    });

    GRID_DBS.forEach((db) => {
      const y = dbToY(db, H);
      ctx.strokeStyle = db === 0 ? 'rgba(148,163,184,0.34)' : 'rgba(148,163,184,0.12)';
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(W, y);
      ctx.stroke();
      if (db !== 0) {
        ctx.textAlign = 'left';
        ctx.fillText(`${db > 0 ? '+' : ''}${db}`, 3, y - 3);
      }
    });

    // Courbe de chaque bande, en retrait
    bands.forEach((band, index) => {
      if (!band.enabled) return;
      const color = BAND_COLORS[index % BAND_COLORS.length];
      ctx.strokeStyle = color;
      ctx.globalAlpha = band.id === selectedId ? 0.55 : 0.22;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      for (let x = 0; x <= W; x += 2) {
        const db = bandGainAt(band, xToFreq(x, W));
        const y = dbToY(db, H);
        x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.globalAlpha = 1;
    });

    // Courbe résultante
    const points = [];
    for (let x = 0; x <= W; x += 1) {
      points.push([x, dbToY(totalGainAt(xToFreq(x, W)), H)]);
    }

    const zeroY = dbToY(0, H);
    const gradient = ctx.createLinearGradient(0, 0, 0, H);
    gradient.addColorStop(0, 'rgba(56,189,248,0.20)');
    gradient.addColorStop(1, 'rgba(56,189,248,0.02)');
    ctx.fillStyle = gradient;
    ctx.beginPath();
    ctx.moveTo(0, zeroY);
    points.forEach(([x, y]) => ctx.lineTo(x, y));
    ctx.lineTo(W, zeroY);
    ctx.closePath();
    ctx.fill();

    ctx.strokeStyle = text;
    ctx.lineWidth = 2;
    ctx.beginPath();
    points.forEach(([x, y], i) => (i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
    ctx.stroke();

    // Points de contrôle
    bands.forEach((band, index) => {
      const color = BAND_COLORS[index % BAND_COLORS.length];
      const x = freqToX(band.freq, W);
      const y = dbToY(TYPES_WITH_GAIN.includes(band.type) ? band.gain : 0, H);
      const selected = band.id === selectedId;

      ctx.globalAlpha = band.enabled ? 1 : 0.35;
      ctx.beginPath();
      ctx.arc(x, y, selected ? 8 : 6, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      if (selected) {
        ctx.strokeStyle = text;
        ctx.lineWidth = 2;
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    });
  }

  // ── Envoi au serveur ─────────────────────────────────────────────────────
  let sendTimer = null;
  let sendPending = false;

  function scheduleSend() {
    // Pendant un glissement, on limite le débit : inutile de solliciter le Pi
    // à chaque pixel parcouru.
    if (sendTimer) {
      sendPending = true;
      return;
    }
    sendNow();
    sendTimer = setTimeout(() => {
      sendTimer = null;
      if (sendPending) {
        sendPending = false;
        scheduleSend();
      }
    }, 120);
  }

  async function sendNow() {
    try {
      const response = await fetch('/api/eq', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bands, auto_preamp: autoPreamp })
      });
      const data = await response.json();
      if (!response.ok) {
        setStatus(data.error || "L'égaliseur n'a pas pu être appliqué.", true);
        return;
      }
      preampApplied = data.preamp_applied ?? 0;
      setStatus(null);
      updateReadout();
    } catch (exc) {
      setStatus('Serveur injoignable.', true);
    }
  }

  function setStatus(message, isError) {
    const el = document.getElementById('eqStatus');
    if (!el) return;
    el.textContent = message || '';
    el.classList.toggle('is-error', Boolean(isError));
  }

  // ── Inspecteur de bande ──────────────────────────────────────────────────
  function selected() {
    return bands.find((b) => b.id === selectedId) || null;
  }

  function updateReadout() {
    const band = selected();
    const panel = document.getElementById('eqInspector');
    if (!panel) return;

    panel.classList.toggle('is-empty', !band);
    const preampEl = document.getElementById('eqPreamp');
    if (preampEl) preampEl.textContent = `${preampApplied > 0 ? '+' : ''}${preampApplied.toFixed(1)} dB`;

    if (!band) return;

    const typeEl = document.getElementById('eqType');
    if (typeEl) typeEl.value = band.type;

    const hasGain = TYPES_WITH_GAIN.includes(band.type);
    const gainRow = document.getElementById('eqGainRow');
    if (gainRow) gainRow.style.display = hasGain ? '' : 'none';

    setField('eqFreq', Math.round(band.freq));
    setField('eqGain', band.gain.toFixed(1));
    setField('eqQ', band.q.toFixed(2));

    const enabledEl = document.getElementById('eqEnabled');
    if (enabledEl) enabledEl.checked = band.enabled;
  }

  function setField(id, value) {
    const el = document.getElementById(id);
    if (el && document.activeElement !== el) el.value = value;
  }

  // ── Interactions ─────────────────────────────────────────────────────────
  function pointerPos(event) {
    const rect = canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top, W: rect.width, H: rect.height };
  }

  function bandAt(x, y, W, H) {
    for (let i = bands.length - 1; i >= 0; i -= 1) {
      const band = bands[i];
      const bx = freqToX(band.freq, W);
      const by = dbToY(TYPES_WITH_GAIN.includes(band.type) ? band.gain : 0, H);
      if (Math.hypot(bx - x, by - y) <= 14) return band;
    }
    return null;
  }

  canvas.addEventListener('pointerdown', (event) => {
    const { x, y, W, H } = pointerPos(event);
    const band = bandAt(x, y, W, H);
    if (!band) {
      selectedId = null;
      updateReadout();
      draw();
      return;
    }
    selectedId = band.id;
    dragging = band;
    canvas.setPointerCapture(event.pointerId);
    updateReadout();
    draw();
  });

  canvas.addEventListener('pointermove', (event) => {
    if (!dragging) return;
    const { x, y, W, H } = pointerPos(event);
    dragging.freq = Math.min(20000, Math.max(20, xToFreq(Math.max(0, Math.min(W, x)), W)));
    if (TYPES_WITH_GAIN.includes(dragging.type)) {
      dragging.gain = Math.min(24, Math.max(-24, yToDb(y, H)));
    }
    updateReadout();
    draw();
    scheduleSend();
  });

  function endDrag(event) {
    if (!dragging) return;
    dragging = null;
    try { canvas.releasePointerCapture(event.pointerId); } catch (_) { /* déjà relâché */ }
    scheduleSend();
  }
  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', endDrag);

  // Molette : largeur de bande (Q) de la bande survolée.
  canvas.addEventListener('wheel', (event) => {
    const { x, y, W, H } = pointerPos(event);
    const band = bandAt(x, y, W, H) || selected();
    if (!band) return;
    event.preventDefault();
    const factor = event.deltaY > 0 ? 1.12 : 1 / 1.12;
    band.q = Math.min(10, Math.max(0.1, band.q * factor));
    selectedId = band.id;
    updateReadout();
    draw();
    scheduleSend();
  }, { passive: false });

  // Double-clic : ajoute une bande là où l'on clique.
  canvas.addEventListener('dblclick', (event) => {
    const { x, y, W, H } = pointerPos(event);
    if (bandAt(x, y, W, H)) return;
    addBand(xToFreq(x, W), yToDb(y, H));
  });

  function addBand(freq, gain) {
    if (bands.length >= 12) {
      setStatus('Douze bandes au maximum.', true);
      return;
    }
    const band = {
      id: `b${Date.now().toString(36)}${nextId++}`,
      type: 'Peaking',
      freq: Math.min(20000, Math.max(20, Math.round(freq))),
      gain: Math.min(24, Math.max(-24, Number(gain.toFixed(1)))),
      q: 1.0,
      enabled: true
    };
    bands.push(band);
    bands.sort((a, b) => a.freq - b.freq);
    selectedId = band.id;
    updateReadout();
    draw();
    scheduleSend();
  }

  function removeSelected() {
    const band = selected();
    if (!band) return;
    bands = bands.filter((b) => b.id !== band.id);
    selectedId = bands.length ? bands[0].id : null;
    updateReadout();
    draw();
    scheduleSend();
  }

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Delete' && event.key !== 'Backspace') return;
    if (document.activeElement && /input|select|textarea/i.test(document.activeElement.tagName)) return;
    if (!selected()) return;
    event.preventDefault();
    removeSelected();
  });

  // ── Champs de l'inspecteur ───────────────────────────────────────────────
  function bindField(id, apply) {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('input', () => {
      const band = selected();
      if (!band) return;
      apply(band, el.value);
      draw();
      scheduleSend();
    });
  }

  bindField('eqFreq', (band, value) => {
    const v = Number(value);
    if (Number.isFinite(v)) band.freq = Math.min(20000, Math.max(20, v));
  });
  bindField('eqGain', (band, value) => {
    const v = Number(value);
    if (Number.isFinite(v)) band.gain = Math.min(24, Math.max(-24, v));
  });
  bindField('eqQ', (band, value) => {
    const v = Number(value);
    if (Number.isFinite(v)) band.q = Math.min(10, Math.max(0.1, v));
  });

  const typeSelect = document.getElementById('eqType');
  if (typeSelect) {
    Object.entries(TYPE_LABELS).forEach(([value, label]) => {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      typeSelect.appendChild(option);
    });
    typeSelect.addEventListener('change', () => {
      const band = selected();
      if (!band) return;
      band.type = typeSelect.value;
      if (!TYPES_WITH_GAIN.includes(band.type)) band.gain = 0;
      updateReadout();
      draw();
      scheduleSend();
    });
  }

  const enabledToggle = document.getElementById('eqEnabled');
  if (enabledToggle) {
    enabledToggle.addEventListener('change', () => {
      const band = selected();
      if (!band) return;
      band.enabled = enabledToggle.checked;
      draw();
      scheduleSend();
    });
  }

  document.getElementById('eqAdd')?.addEventListener('click', () => addBand(1000, 0));
  document.getElementById('eqRemove')?.addEventListener('click', removeSelected);

  document.getElementById('eqFlat')?.addEventListener('click', () => {
    bands.forEach((band) => { band.gain = 0; });
    updateReadout();
    draw();
    scheduleSend();
  });

  const autoToggle = document.getElementById('eqAutoPreamp');
  if (autoToggle) {
    autoToggle.addEventListener('change', () => {
      autoPreamp = autoToggle.checked;
      scheduleSend();
    });
  }

  // ── Démarrage ────────────────────────────────────────────────────────────
  async function init() {
    try {
      const response = await fetch('/api/eq');
      const data = await response.json();
      bands = data.bands || [];
      autoPreamp = data.auto_preamp !== false;
      preampApplied = data.preamp_applied ?? 0;
    } catch (_) {
      setStatus('Égaliseur illisible : le serveur ne répond pas.', true);
    }
    if (autoToggle) autoToggle.checked = autoPreamp;
    selectedId = bands.length ? bands[0].id : null;
    updateReadout();
    resize();
  }

  window.addEventListener('resize', resize);
  // Le thème change les couleurs de la grille et de la courbe.
  new MutationObserver(draw).observe(document.body, { attributes: true, attributeFilter: ['data-theme'] });

  init();
})();
