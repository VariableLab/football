/**
 * WC Analytics — 专业量化终端版 (v4.0)
 */

const AppState = {
  matches: [],
  teams: [],
  filter: 'jingcai',
  user: null,
};

async function initApp() {
  await I18n.init();
  try {
    const [teams, me] = await Promise.all([
      WCApi.Data.getTeams(),
      WCApi.Auth.me().catch(() => null)
    ]);
    AppState.teams = teams.items || [];
    AppState.user = me;
    loadMatchView();
  } catch (e) {
    console.error('App init failed', e);
  }
}

async function loadMatchView() {
  // Trigger loading state via event
  window.dispatchEvent(new CustomEvent('matches-loading'));
  try {
    let matches;
    if (['today', 'tomorrow'].includes(AppState.filter)) {
      // 传递为 date 参数 (status=undefined, group=undefined, matchType=undefined, date=filter)
      matches = await WCApi.Data.getMatches(undefined, undefined, undefined, AppState.filter);
    } else {
      // 传递为 status 参数 (如 'jingcai')
      matches = await WCApi.Data.getMatches(AppState.filter);
    }
    AppState.matches = matches || [];
    window.dispatchEvent(new CustomEvent('matches-updated', { detail: { matches: AppState.matches } }));
  } catch (e) {
    console.error('Load matches failed', e);
    window.dispatchEvent(new CustomEvent('matches-updated', { detail: { matches: [] } }));
  }
}

function setFilter(filter) {
  AppState.filter = filter;
  document.querySelectorAll('[data-filter]').forEach(btn => {
    const isActive = btn.dataset.filter === filter;
    btn.classList.toggle('border-b-2', isActive);
    btn.classList.toggle('border-accent', isActive);
    btn.classList.toggle('text-ink', isActive);
    btn.classList.toggle('font-bold', isActive);
    btn.classList.toggle('text-ink-faded', !isActive);
  });
  loadMatchView();
}

function fmtBJ(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  const offset = 8 * 60;
  const bj = new Date(d.getTime() + (offset + d.getTimezoneOffset()) * 60000);
  return `${bj.getMonth()+1}/${bj.getDate()} ${bj.getHours().toString().padStart(2, '0')}:${bj.getMinutes().toString().padStart(2, '0')}`;
}

function fmtPct(v) { 
  if (v === undefined || v === null) return '0%';
  return (v * 100).toFixed(1) + '%'; 
}

function openLoginModal() { window.dispatchEvent(new CustomEvent('open-login-modal')); }

document.addEventListener('DOMContentLoaded', initApp);
