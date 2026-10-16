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

  function bandColor(band) {
    const index = bands.indexOf(band);
    return BAND_COLORS[(index < 0 ? 0 : index) % BAND_COLORS.length];
  }

  function formatFreq(freq) {
    if (freq >= 10000) return `${(freq / 1000).toFixed(1)}k`;
    if (freq >= 1000) return `${(freq / 1000).toFixed(2)}k`;
    return `${Math.round(freq)}`;
  }

  function updateReadout() {
    const band = selected();
    const panel = document.getElementById('eqInspector');
    if (!panel) return;

    panel.classList.toggle('is-empty', !band);
    const preampEl = document.getElementById('eqPreamp');
    if (preampEl) preampEl.textContent = `${preampApplied > 0 ? '+' : ''}${preampApplied.toFixed(1)} dB`;

    renderBandChips();
    if (!band) return;

    panel.style.setProperty('--band', bandColor(band));
    panel.classList.toggle('is-bypassed', !band.enabled);

    document.querySelectorAll('#eqTypes .eq-shape').forEach((btn) => {
      const on = btn.dataset.type === band.type;
      btn.classList.toggle('is-active', on);
      btn.setAttribute('aria-checked', on ? 'true' : 'false');
    });

    const hasGain = TYPES_WITH_GAIN.includes(band.type);
    const gainRow = document.getElementById('eqGainRow');
    if (gainRow) gainRow.classList.toggle('is-disabled', !hasGain);

    setField('eqFreq', formatFreq(band.freq));
    setField('eqGain', `${band.gain > 0 ? '+' : ''}${band.gain.toFixed(1)}`);
    setField('eqQ', band.q.toFixed(2));

    const power = document.getElementById('eqPower');
    if (power) power.setAttribute('aria-pressed', band.enabled ? 'true' : 'false');

    knobDials.forEach(updateKnob);
  }

  function setField(id, value) {
    const el = document.getElementById(id);
    if (el && document.activeElement !== el) el.value = value;
  }

  // ── Barre des bandes ─────────────────────────────────────────────────────
  function renderBandChips() {
    const wrap = document.getElementById('eqBands');
    if (!wrap) return;
    const addBtn = document.getElementById('eqAdd');
    wrap.querySelectorAll('.eq-band-chip').forEach((n) => n.remove());
    bands.forEach((band, index) => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'eq-band-chip';
      chip.style.setProperty('--band', bandColor(band));
      chip.classList.toggle('is-selected', band.id === selectedId);
      chip.classList.toggle('is-off', !band.enabled);
      chip.setAttribute('aria-pressed', band.id === selectedId ? 'true' : 'false');
      chip.title = `${TYPE_LABELS[band.type]} · ${formatFreq(band.freq)} Hz`;
      chip.innerHTML = `<span class="eq-band-num">${index + 1}</span><span class="eq-band-freq">${formatFreq(band.freq)}</span>`;
      chip.addEventListener('click', () => {
        selectedId = band.id;
        updateReadout();
        draw();
      });
      wrap.insertBefore(chip, addBtn);
    });
  }

  // ── Potentiomètres ───────────────────────────────────────────────────────
  // Course de 270° comme un potard physique ; fréquence et Q suivent une
  // échelle logarithmique, le gain est bipolaire (arc partant du zénith).
  const KNOB_SWEEP = 135;
  const KNOBS = {
    freq: { min: FREQ_MIN, max: FREQ_MAX, log: true, def: 1000, round: (v) => Math.round(v) },
    gain: { min: -24, max: 24, log: false, bipolar: true, def: 0, round: (v) => Math.round(v * 10) / 10 },
    q: { min: 0.1, max: 10, log: true, def: 1, round: (v) => Math.round(v * 100) / 100 }
  };
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const knobDials = Array.from(document.querySelectorAll('.knob-dial[data-param]'));

  function clamp01(t) { return Math.min(1, Math.max(0, t)); }
  function toNorm(spec, v) {
    return clamp01(spec.log
      ? Math.log(v / spec.min) / Math.log(spec.max / spec.min)
      : (v - spec.min) / (spec.max - spec.min));
  }
  function fromNorm(spec, t) {
    t = clamp01(t);
    return spec.log ? spec.min * Math.pow(spec.max / spec.min, t) : spec.min + t * (spec.max - spec.min);
  }
  function normToAngle(t) { return -KNOB_SWEEP + t * 2 * KNOB_SWEEP; }

  function polar(deg, r) {
    const a = ((deg - 90) * Math.PI) / 180;
    return [50 + r * Math.cos(a), 50 + r * Math.sin(a)];
  }
  function arcPath(from, to, r) {
    if (Math.abs(to - from) < 0.5) return '';
    const [x0, y0] = polar(from, r);
    const [x1, y1] = polar(to, r);
    const large = Math.abs(to - from) > 180 ? 1 : 0;
    const sweep = to > from ? 1 : 0;
    return `M${x0.toFixed(2)} ${y0.toFixed(2)} A${r} ${r} 0 ${large} ${sweep} ${x1.toFixed(2)} ${y1.toFixed(2)}`;
  }

  function buildKnob(dial) {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 100 100');
    svg.setAttribute('aria-hidden', 'true');
    svg.classList.add('knob-ring');

    for (let i = 0; i <= 10; i += 1) {
      const angle = normToAngle(i / 10);
      const [x0, y0] = polar(angle, 45.5);
      const [x1, y1] = polar(angle, i % 5 === 0 ? 49.5 : 48);
      const tick = document.createElementNS(SVG_NS, 'line');
      tick.setAttribute('x1', x0); tick.setAttribute('y1', y0);
      tick.setAttribute('x2', x1); tick.setAttribute('y2', y1);
      tick.classList.add('knob-tick');
      svg.appendChild(tick);
    }

    const track = document.createElementNS(SVG_NS, 'path');
    track.setAttribute('d', arcPath(-KNOB_SWEEP, KNOB_SWEEP, 40));
    track.classList.add('knob-track');
    const value = document.createElementNS(SVG_NS, 'path');
    value.classList.add('knob-arc');
    svg.append(track, value);

    const cap = document.createElement('div');
    cap.className = 'knob-cap';
    dial.append(svg, cap);
    dial._arc = value;
    dial._cap = cap;
  }

  function updateKnob(dial) {
    const band = selected();
    if (!band) return;
    const spec = KNOBS[dial.dataset.param];
    const v = band[dial.dataset.param];
    const angle = normToAngle(toNorm(spec, v));
    const origin = spec.bipolar ? 0 : -KNOB_SWEEP;
    dial._arc.setAttribute('d', arcPath(Math.min(origin, angle), Math.max(origin, angle), 40));
    dial._cap.style.setProperty('--angle', `${angle}deg`);
    dial.setAttribute('aria-valuemin', spec.min);
    dial.setAttribute('aria-valuemax', spec.max);
    dial.setAttribute('aria-valuenow', v);
  }

  function knobEnabled(dial) {
    const band = selected();
    return Boolean(band) && (dial.dataset.param !== 'gain' || TYPES_WITH_GAIN.includes(band.type));
  }

  function setKnobNorm(dial, t) {
    const band = selected();
    const spec = KNOBS[dial.dataset.param];
    const next = spec.round(fromNorm(spec, t));
    if (next === band[dial.dataset.param]) return;
    band[dial.dataset.param] = next;
    updateReadout();
    draw();
    scheduleSend();
  }

  function nudgeKnob(dial, delta) {
    const band = selected();
    setKnobNorm(dial, toNorm(KNOBS[dial.dataset.param], band[dial.dataset.param]) + delta);
  }

  knobDials.forEach((dial) => {
    buildKnob(dial);
    let lastY = null;
    let norm = 0;

    dial.addEventListener('pointerdown', (event) => {
      if (!knobEnabled(dial) || event.button > 0) return;
      event.preventDefault();
      dial.focus({ preventScroll: true });
      dial.setPointerCapture(event.pointerId);
      dial.classList.add('is-turning');
      lastY = event.clientY;
      norm = toNorm(KNOBS[dial.dataset.param], selected()[dial.dataset.param]);
    });
    dial.addEventListener('pointermove', (event) => {
      if (lastY === null) return;
      // ~220 px pour toute la course ; Maj : réglage fin, cinq fois plus lent.
      norm = clamp01(norm + (lastY - event.clientY) / (event.shiftKey ? 1100 : 220));
      lastY = event.clientY;
      setKnobNorm(dial, norm);
    });
    const release = (event) => {
      if (lastY === null) return;
      lastY = null;
      dial.classList.remove('is-turning');
      try { dial.releasePointerCapture(event.pointerId); } catch (_) { /* déjà relâché */ }
    };
    dial.addEventListener('pointerup', release);
    dial.addEventListener('pointercancel', release);

    dial.addEventListener('dblclick', () => {
      if (!knobEnabled(dial)) return;
      const spec = KNOBS[dial.dataset.param];
      setKnobNorm(dial, toNorm(spec, spec.def));
    });

    dial.addEventListener('wheel', (event) => {
      if (!knobEnabled(dial)) return;
      event.preventDefault();
      nudgeKnob(dial, (event.deltaY < 0 ? 1 : -1) * (event.shiftKey ? 0.002 : 0.012));
    }, { passive: false });

    dial.addEventListener('keydown', (event) => {
      if (!knobEnabled(dial)) return;
      const fine = event.shiftKey ? 0.2 : 1;
      const steps = {
        ArrowUp: 0.01 * fine, ArrowRight: 0.01 * fine,
        ArrowDown: -0.01 * fine, ArrowLeft: -0.01 * fine,
        PageUp: 0.1, PageDown: -0.1
      };
      if (event.key in steps) nudgeKnob(dial, steps[event.key]);
      else if (event.key === 'Home') setKnobNorm(dial, 0);
      else if (event.key === 'End') setKnobNorm(dial, 1);
      else return;
      event.preventDefault();
    });
  });

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
    const active = document.activeElement;
    if (active && (/input|select|textarea/i.test(active.tagName) || active.classList.contains('knob-dial'))) return;
    if (!selected()) return;
    event.preventDefault();
    removeSelected();
  });

  // ── Champs de l'inspecteur ───────────────────────────────────────────────
  // Valeur appliquée à la validation (Entrée ou perte du focus) : appliquer
  // à chaque frappe ferait sauter le filtre à 20 Hz en tapant « 1500 ».
  function bindField(id, apply) {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') el.blur();
      if (event.key === 'Escape') { el.value = ''; el.blur(); }
    });
    el.addEventListener('change', () => {
      const band = selected();
      if (band) {
        apply(band, el.value.trim().replace(',', '.'));
        draw();
        scheduleSend();
      }
    });
    el.addEventListener('blur', updateReadout);
    el.addEventListener('focus', () => el.select());
  }

  bindField('eqFreq', (band, value) => {
    const match = /^([\d.]+)\s*(k?)/i.exec(value);
    const v = match ? Number(match[1]) * (match[2] ? 1000 : 1) : NaN;
    if (Number.isFinite(v)) band.freq = Math.min(20000, Math.max(20, Math.round(v)));
  });
  bindField('eqGain', (band, value) => {
    const v = parseFloat(value);
    if (Number.isFinite(v)) band.gain = Math.min(24, Math.max(-24, v));
  });
  bindField('eqQ', (band, value) => {
    const v = parseFloat(value);
    if (Number.isFinite(v)) band.q = Math.min(10, Math.max(0.1, v));
  });

  // Sélecteur de forme : une icône par type de filtre.
  const SHAPE_ICONS = {
    Peaking: 'M1 11C7 11 9 3 12 3s5 8 11 8',
    Lowshelf: 'M1 4h5c4 0 6 7 10 7h7',
    Highshelf: 'M1 11h7c4 0 6-7 10-7h5',
    Highpass: 'M1 13c3 0 5-9 9-9h13',
    Lowpass: 'M1 4h13c4 0 6 9 9 9',
    Notch: 'M1 5h8c2 0 2 8 3 8s1-8 3-8h8'
  };
  const typeGroup = document.getElementById('eqTypes');
  if (typeGroup) {
    Object.entries(TYPE_LABELS).forEach(([value, label]) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'eq-shape';
      btn.dataset.type = value;
      btn.title = label;
      btn.setAttribute('role', 'radio');
      btn.setAttribute('aria-label', label);
      btn.innerHTML = `<svg viewBox="0 0 24 16" aria-hidden="true"><path d="${SHAPE_ICONS[value]}"/></svg>`;
      btn.addEventListener('click', () => {
        const band = selected();
        if (!band) return;
        band.type = value;
        if (!TYPES_WITH_GAIN.includes(band.type)) band.gain = 0;
        updateReadout();
        draw();
        scheduleSend();
      });
      typeGroup.appendChild(btn);
    });
  }

  document.getElementById('eqPower')?.addEventListener('click', () => {
    const band = selected();
    if (!band) return;
    band.enabled = !band.enabled;
    updateReadout();
    draw();
    scheduleSend();
  });

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
