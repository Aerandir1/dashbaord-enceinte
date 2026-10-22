// Thème (phosphore vert la nuit, ambre le jour), partagé par le tableau de
// bord et la page Réglages. Par défaut il suit le système ; un choix manuel
// est gardé pour la session de l'onglet.

const _systemQuery = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)');
const THEME_KEY = 'dashboard-theme';

function _manualTheme() {
  const v = sessionStorage.getItem(THEME_KEY);
  return v === 'light' || v === 'dark' ? v : null;
}

function _systemTheme() {
  return _systemQuery && _systemQuery.matches ? 'light' : 'dark';
}

// Sélecteur de la page Réglages : boutons [data-theme-choice] = dark | light | auto.
function _syncThemeChoice() {
  const choice = _manualTheme() || 'auto';
  document.querySelectorAll('[data-theme-choice]').forEach((btn) => {
    btn.setAttribute('aria-checked', String(btn.dataset.themeChoice === choice));
  });
}

function applyTheme() {
  document.body.dataset.theme = _manualTheme() || _systemTheme();
  _syncThemeChoice();
}

function setThemeChoice(choice) {
  if (choice === 'auto') sessionStorage.removeItem(THEME_KEY);
  else sessionStorage.setItem(THEME_KEY, choice);
  applyTheme();
}

document.querySelectorAll('[data-theme-choice]').forEach((btn) => {
  btn.addEventListener('click', () => setThemeChoice(btn.dataset.themeChoice));
});

// Suit les changements du thème système en temps réel (hors choix manuel).
if (_systemQuery && _systemQuery.addEventListener) {
  _systemQuery.addEventListener('change', applyTheme);
}

applyTheme();
