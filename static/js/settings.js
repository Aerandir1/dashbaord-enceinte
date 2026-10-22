// Réglages : renommage de l'enceinte + gestion du Wi-Fi.
// Ce script pilote aussi bien la page /reglages que la page d'accueil /setup
// (mode point d'accès). Chaque bloc est protégé par la
// présence de ses éléments : le même fichier sert les deux pages sans erreur.

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  return { ok: response.ok, data };
}

// Pictos dessinés en SVG (pas d'emoji : rendu identique partout, teinté au
// phosphore via currentColor).
const ICON_CHECK = '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true">'
  + '<path d="M3.5 12.5 6 10l3.5 3.5L18 5l2.5 2.5-11 11z"/></svg>';
const ICON_LOCK = '<svg class="ico ico-lock" viewBox="0 0 24 24" aria-hidden="true">'
  + '<path d="M7 10V7.5a5 5 0 0 1 10 0V10h1.5v11h-13V10zm3 0h4V7.5a2 2 0 0 0-4 0zm.5 4v4h3v-4z" fill-rule="evenodd"/></svg>';

// ── Nom de l'enceinte ──────────────────────────────────────────────────────
(function initRename() {
  const input = document.getElementById('deviceNameInput');
  const button = document.getElementById('deviceNameSave');
  if (!input || !button) return;

  async function save() {
    const name = input.value.trim();
    if (!name) return;
    button.disabled = true;
    const previous = button.textContent;
    button.textContent = 'Renommage…';
    const { ok, data } = await postJson('/api/name', { name });
    if (ok) button.innerHTML = `${ICON_CHECK}<span>Renommé</span>`;
    else button.textContent = previous;
    if (!ok) alert(data.error || 'Renommage impossible');
    setTimeout(() => { button.textContent = previous; button.disabled = false; }, 1500);
  }

  button.addEventListener('click', save);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') save(); });
})();

// ── Wi-Fi ──────────────────────────────────────────────────────────────────
(function initWifi() {
  const list = document.getElementById('wifiList');
  const scanBtn = document.getElementById('wifiScan');
  if (!list || !scanBtn) return;

  const statusLine = document.getElementById('wifiStatusLine');
  const connectRow = document.getElementById('wifiConnectRow');
  const passwordInput = document.getElementById('wifiPassword');
  const connectBtn = document.getElementById('wifiConnect');
  const hint = document.getElementById('wifiHint');

  let selected = null; // { ssid, secured }

  function setHint(text) { if (hint) hint.textContent = text || ''; }

  // Barres de signal construites, comme l'indicateur du bloc État.
  function signalIcon(signal) {
    const bars = signal >= 75 ? 4 : signal >= 50 ? 3 : signal >= 25 ? 2 : 1;
    return [1, 2, 3, 4]
      .map((i) => `<span class="wifi-bar${i <= bars ? ' active' : ''}"></span>`)
      .join('');
  }

  async function refreshStatus() {
    if (!statusLine) return;
    try {
      const r = await fetch('/api/wifi/status');
      const s = await r.json();
      statusLine.textContent = s.ssid
        ? `Connecté à ${s.ssid}${s.connectivity && s.connectivity !== 'full' ? ' (sans Internet)' : ''}`
        : 'Non connecté';
    } catch (_) {
      statusLine.textContent = '—';
    }
  }

  function renderNetworks(networks) {
    list.innerHTML = '';
    if (!networks.length) {
      const li = document.createElement('li');
      li.className = 'wifi-empty';
      li.textContent = 'Aucun réseau trouvé';
      list.appendChild(li);
      return;
    }
    networks.forEach((net) => {
      const li = document.createElement('li');
      li.className = 'wifi-item' + (net.active ? ' active' : '');
      li.setAttribute('role', 'button');
      li.tabIndex = 0;
      li.innerHTML =
        `<span class="wifi-name">${net.ssid.replace(/</g, '&lt;')}</span>` +
        `<span class="wifi-meta">${net.secured ? ICON_LOCK : ''}` +
        `<span class="wifi-indicator wifi-signal" aria-hidden="true">${signalIcon(net.signal)}</span></span>`;
      const choose = () => selectNetwork(net, li);
      li.addEventListener('click', choose);
      li.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(); } });
      list.appendChild(li);
    });
  }

  function selectNetwork(net, li) {
    selected = net;
    list.querySelectorAll('.wifi-item').forEach((el) => el.classList.remove('selected'));
    li.classList.add('selected');
    if (connectRow) {
      connectRow.hidden = false;
      if (passwordInput) {
        passwordInput.value = '';
        passwordInput.style.display = net.secured ? '' : 'none';
        if (net.secured) passwordInput.focus();
      }
    }
    setHint(`Réseau sélectionné : ${net.ssid}`);
  }

  async function scan() {
    scanBtn.disabled = true;
    const previous = scanBtn.textContent;
    scanBtn.textContent = 'Recherche…';
    try {
      const r = await fetch('/api/wifi/scan');
      const data = await r.json();
      if (!r.ok) { setHint(data.error || 'Balayage impossible'); renderNetworks([]); }
      else renderNetworks(data.networks || []);
    } catch (_) {
      setHint('Balayage impossible');
    } finally {
      scanBtn.textContent = previous;
      scanBtn.disabled = false;
    }
  }

  async function connect() {
    if (!selected) return;
    connectBtn.disabled = true;
    setHint(`Connexion à ${selected.ssid}…`);
    const { ok, data } = await postJson('/api/wifi/connect', {
      ssid: selected.ssid,
      password: passwordInput ? passwordInput.value : '',
    });
    if (ok) {
      setHint(
        `Connecté à ${selected.ssid}. Si l'adresse de l'enceinte a changé, ` +
        `rejoins le tableau de bord à sa nouvelle adresse.`
      );
      refreshStatus();
    } else {
      setHint(data.error || 'Connexion impossible');
    }
    connectBtn.disabled = false;
  }

  scanBtn.addEventListener('click', scan);
  if (connectBtn) connectBtn.addEventListener('click', connect);
  if (passwordInput) {
    passwordInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') connect(); });
  }

  refreshStatus();
  // La page /setup balaie d'emblée : l'utilisateur vient justement configurer.
  if (document.body.dataset.autoScan === '1') scan();
})();
