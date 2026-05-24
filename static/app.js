/**
 * WC Analytics — 主应用逻辑 (v3.0 Alpine.js 驱动版)
 */

const AppState = {
  matches: [],
  teams: [],
  page: 1,
  pageSize: 12,
  filter: 'jingcai',
  user: null,
  jingcaiAllIssues: [],
  strategyCache: {},
  currentMatchId: null,
};

// ─── 核心启动 ───
async function initApp() {
  await I18n.init();
  try {
    // 1. 获取基础数据
    const [teams, me] = await Promise.all([
      WCApi.Data.getTeams(),
      WCApi.Auth.me().catch(() => null)
    ]);
    AppState.teams = teams.items || [];
    AppState.user = me;
    
    // 2. 更新导航栏
    renderNavUser();
    
    // 3. 加载初始视图
    loadMatchView();
    
  } catch (e) {
    console.error('App init failed', e);
  }
}

// ─── 用户导航 ───
function renderNavUser() {
  const el = document.getElementById('navUser');
  if (!AppState.user) {
    el.innerHTML = `<button onclick="openLoginModal()" class="text-sm font-light text-warm-gray hover:text-charcoal transition-colors">${I18n.t('nav.login')}</button>`;
  } else {
    const name = AppState.user.email.split('@')[0];
    const isPaid = AppState.user.is_paid;
    el.innerHTML = `
      <div class="flex items-center gap-4">
        <span class="text-sm font-medium text-charcoal flex items-center gap-2">
          ${name}
          ${isPaid ? '<span class="px-1.5 py-0.5 rounded bg-accent/10 text-accent text-[10px] uppercase font-bold tracking-widest">PRO</span>' : ''}
        </span>
        <button onclick="openSettingsModal()" class="w-8 h-8 rounded-full bg-charcoal/5 flex items-center justify-center hover:bg-charcoal/10 transition-colors">
          <svg class="w-4 h-4 text-warm-gray" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
        </button>
        <button onclick="handleLogout()" class="text-sm font-light text-warm-gray hover:text-accent-red transition-colors">${I18n.t('nav.logout') || '登出'}</button>
      </div>
    `;
  }
}

function handleLogout() {
  localStorage.removeItem('token');
  location.reload();
}

// ─── 视图控制 ───
function switchSection(section) {
  const sections = { matches: 'sectionMatches', report: 'sectionReport', validation: 'sectionValidation', feedback: 'sectionFeedback', ai: 'sectionAI' };
  for (const [key, id] of Object.entries(sections)) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('hidden', key !== section);
  }
  document.querySelectorAll('.section-tab').forEach(btn => {
    const isActive = btn.dataset.section === section;
    btn.classList.toggle('border-accent', isActive);
    btn.classList.toggle('text-charcoal', isActive);
    btn.classList.toggle('font-medium', isActive);
    btn.classList.toggle('border-transparent', !isActive);
    btn.classList.toggle('text-warm-gray', !isActive);
  });
  
  if (section === 'validation') loadValidationDashboard();
  if (section === 'feedback') loadFeedback();
}

function openModal(matchId) {
  document.getElementById('detailModal').classList.remove('hidden');
  window.dispatchEvent(new CustomEvent('open-match-modal', { detail: { matchId } }));
}

function closeModal() {
  document.getElementById('detailModal').classList.add('hidden');
}

function openLoginModal() { window.dispatchEvent(new CustomEvent('open-login-modal')); }
function openRedeemModal() { window.dispatchEvent(new CustomEvent('open-redeem-modal')); }
function openSettingsModal() { window.dispatchEvent(new CustomEvent('open-settings-modal')); }

// ─── 比赛数据加载 ───
async function loadMatchView() {
  try {
    const status = AppState.filter === 'all' ? undefined : AppState.filter;
    // 注意：api_client.js 中的 getMatches 是 positional arguments: (status, group, matchType)
    const matches = await WCApi.Data.getMatches(status);
    AppState.matches = matches || []; // api_client 已经 unwrapItems 了
    renderCards();
  } catch (e) {
    console.error('Load matches failed', e);
    // 即使失败也停止加载动画，并清空列表
    window.dispatchEvent(new CustomEvent('matches-updated', { detail: { matches: [] } }));
  }
}



function renderCards() {
  const matches = AppState.matches;
  const start = (AppState.page - 1) * AppState.pageSize;
  const pageMatches = matches.slice(start, start + AppState.pageSize);
  
  window.dispatchEvent(new CustomEvent('matches-updated', { 
    detail: { matches: pageMatches } 
  }));
}

function setFilter(filter) {
  AppState.filter = filter;
  AppState.page = 1;
  document.querySelectorAll('.tab-filter').forEach(btn => {
    const isActive = btn.dataset.filter === filter;
    btn.classList.toggle('border-charcoal', isActive);
    btn.classList.toggle('text-charcoal', isActive);
    btn.classList.toggle('font-medium', isActive);
    btn.classList.toggle('text-warm-gray', !isActive);
    btn.classList.toggle('border-transparent', !isActive);
  });
  loadMatchView();
}

// ─── 模块加载 ───
async function loadValidationDashboard() {
  try {
    const report = await WCApi.Validation.getReport('world_cup');
    window.dispatchEvent(new CustomEvent('validation-updated', { detail: { report } }));
  } catch (e) { console.error(e); }
}

async function loadFeedback() {
  window.dispatchEvent(new CustomEvent('feedback-refresh'));
}

// ─── 工具函数 ───
function fmtBJ(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  const offset = 8 * 60;
  const bj = new Date(d.getTime() + (offset + d.getTimezoneOffset()) * 60000);
  return `${bj.getMonth()+1}/${bj.getDate()} ${bj.getHours().toString().padStart(2, '0')}:${bj.getMinutes().toString().padStart(2, '0')}`;
}

function freshnessTag(iso) {
  if (!iso) return '';
  const diff = (new Date() - new Date(iso)) / 1000 / 60;
  if (diff < 30) return '<span class="text-accent-green">● Live</span>';
  if (diff < 120) return '<span class="text-warm-gray">● Recent</span>';
  return '';
}

function matchCountdown(kickoff) {
  const diff = new Date(kickoff) - new Date();
  if (diff < 0) return '';
  const hours = Math.floor(diff / 3600000);
  const mins = Math.floor((diff % 3600000) / 60000);
  return `Starts in ${hours}h ${mins}m`;
}

function fmtPct(v) { return v ? (v * 100).toFixed(1) + '%' : '-'; }
function escapeHtml(s) { return s ? String(s).replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":"&#039;"}[m])) : ''; }

// 启动
document.addEventListener('DOMContentLoaded', initApp);
