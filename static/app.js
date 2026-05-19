/**
 * WC Analytics 前端应用
 * 温暖优雅版 · 竞彩核心体验
 */

// ─── 安全工具 ───
function escapeHtml(str) {
  if (typeof str !== 'string') return String(str ?? '');
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#039;');
}

// ─── 状态 ───
const AppState = {
  user: null,
  teams: [],
  currentMatchId: null,
  filter: 'jingcai',
  jingcaiIssues: [],
  jingcaiAllIssues: [],
  jingcaiDetailCache: {},
  selectedIssueId: null,
  page: 1,
  pageSize: 30,
  strategyCache: {},
};

// ─── 工具 ───
function fmtPct(v) {
  if (v === null || v === undefined) return '-';
  return (v * 100).toFixed(1) + '%';
}

function fmtOdds(v) {
  if (v === null || v === undefined) return '-';
  return Number(v).toFixed(2);
}

function fmtBJ(kickoffAt) {
  if (!kickoffAt) return I18n.t('match.countdown');
  const d = new Date(kickoffAt);
  return d.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit', day: '2-digit', weekday: 'short',
    hour: '2-digit', minute: '2-digit',
  });
}

function fmtBJShort(kickoffAt) {
  if (!kickoffAt) return '';
  const d = new Date(kickoffAt);
  const hh = String(d.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', hour12: false }));
  const mm = String(d.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', minute: '2-digit' }));
  return `${hh}:${mm}`;
}

// Freshness indicator: shows "X分钟前更新" with color coding
function freshnessTag(updatedAt) {
  if (!updatedAt) return '';
  const now = Date.now();
  const then = new Date(updatedAt).getTime();
  const diffMin = Math.round((now - then) / 60000);
  if (diffMin < 0) return '';
  let color, text;
  if (diffMin < 5) { color = 'text-accent-green'; text = I18n.t('data.refresh'); }
  else if (diffMin < 30) { color = 'text-accent-green'; text = diffMin + '分钟前'; }
  else if (diffMin < 120) { color = 'text-accent-yellow'; text = Math.floor(diffMin / 60) + '小时前'; }
  else { color = 'text-accent-red'; text = Math.floor(diffMin / 60) + '小时前'; }
  return `<span class="text-sm ${color} font-light">${text}</span>`;
}

function matchCountdown(kickoffAt) {
  if (!kickoffAt) return null;
  const diff = new Date(kickoffAt) - new Date();
  if (diff <= 0) return null;
  const days = Math.floor(diff / 86400000);
  const hours = Math.floor((diff % 86400000) / 3600000);
  const mins = Math.floor((diff % 3600000) / 60000);
  if (days > 0) return `${days}天${hours}小时`;
  if (hours > 0) return `${hours}小时${mins}分`;
  return `${mins}分钟`;
}

function isLocked(match) {
  // 🔓 测试模式：全部开放，无需付费
  return false;
  if (!AppState.user) return true;
  if (match.status === 'finished') return false;
  if (AppState.user.is_paid && AppState.user.paid_until) {
    return new Date(AppState.user.paid_until) < new Date();
  }
  return !AppState.user.is_paid;
}

function lockOverlay(html) {
  return `
  <div class="relative">
    <div class="lock-blur select-none pointer-events-none">${html}</div>
    <div class="absolute bottom-0 left-0 right-0 flex flex-col items-center gap-1.5 py-4 bg-gradient-to-t from-cream-light via-cream-light/80 to-transparent">
      <div class="text-sm text-warm-gray">${I18n.t('match.lockOverlay')}</div>
      <button onclick="showRedeemModal()" class="text-sm px-3 py-1 border border-beige/30 text-beige rounded-lg hover:bg-charcoal hover:text-cream-light hover:border-charcoal transition-all duration-300">${I18n.t('modal.redeemTitle')}</button>
    </div>
  </div>`;
}

function calcEV(prob, odds) {
  if (prob == null || odds == null || odds <= 0) return null;
  return prob * odds - 1;
}

function evColor(ev) {
  if (ev == null) return 'text-warm-gray';
  if (ev > 0.05) return 'text-accent-green font-medium';
  if (ev > 0) return 'text-accent-cyan';
  if (ev < -0.05) return 'text-accent-red';
  return 'text-warm-gray';
}

function evLabel(ev) {
  if (ev == null) return '';
  return `EV ${ev > 0 ? '+' : ''}${(ev * 100).toFixed(1)}%`;
}

// ─── 初始化 ───
async function initApp() {
  if (WCApi.Auth.isLoggedIn()) {
    try { AppState.user = await WCApi.Auth.me(); } catch { WCApi.Auth.logout(); }
  }
  renderNavUser();
  try { AppState.teams = await WCApi.Data.getTeams(); } catch { /* non-critical */ }
  loadJingcaiView();
  startWcCountdown();
  setupSSE();
}

// ─── SSE 实时推送 ───
function setupSSE() {
  const evtSource = new EventSource('/api/events');
  evtSource.addEventListener('jingcai_update', (e) => {
    try {
      const data = JSON.parse(e.data);
      console.log('[sse] jingcai_update:', data);
      const reportSection = document.getElementById('sectionReport');
      if (reportSection && reportSection.style.display !== 'none') {
        loadReportDashboard();
      }
      const matchesSection = document.getElementById('sectionMatches');
      if (matchesSection && matchesSection.style.display !== 'none') {
        loadJingcaiView();
      }
    } catch (err) {
      console.warn('[sse] parse error:', err);
    }
  });
  evtSource.onerror = () => {
    console.warn('[sse] connection error, will retry...');
  };
}

// ─── 导航栏 ───
function renderNavUser() {
  const c = document.getElementById('navUser');
  if (!c) return;
  if (AppState.user) {
    c.innerHTML = `
    <span class="text-sm font-light text-warm-gray">${escapeHtml(AppState.user.email)}</span>
    ${AppState.user.is_paid
      ? '<span class="text-xs px-2 py-0.5 rounded border border-beige/30 text-beige font-medium tracking-wide">PRO</span>'
      : '<span class="text-xs px-2 py-0.5 rounded border border-charcoal/10 text-warm-gray font-light">Free</span>'}
    <button onclick="showRedeemModal()" class="text-sm font-light text-warm-gray hover:text-beige transition-colors duration-300">${I18n.t('nav.redeem')}</button>
    <button onclick="openSettingsModal()" class="text-sm font-light text-warm-gray hover:text-beige transition-colors duration-300">${I18n.t('nav.settings')}</button>
    <button onclick="handleLogout()" class="text-sm font-light text-warm-gray hover:text-accent-red transition-colors duration-300">${I18n.t('nav.logout')}</button>`;
  } else {
    c.innerHTML = `
    <span class="text-sm font-light text-warm-gray">${I18n.t('nav.proPredict')}</span>
    <button onclick="showLoginModal()" class="text-sm px-4 py-1.5 border border-charcoal/15 text-charcoal hover:bg-charcoal hover:text-cream-light font-light tracking-wide rounded-lg transition-all duration-300">${I18n.t('nav.login')}</button>`;
  }
}

// ─── Section Tabs ───
function switchSection(section) {
  const sections = { matches: 'sectionMatches', report: 'sectionReport', validation: 'sectionValidation', feedback: 'sectionFeedback', ai: 'sectionAI' };
  for (const [key, id] of Object.entries(sections)) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('hidden', key !== section);
  }
  document.querySelectorAll('.section-tab').forEach(btn => {
    const isActive = btn.dataset.section === section;
    btn.classList.toggle('border-charcoal', isActive);
    btn.classList.toggle('text-charcoal', isActive);
    btn.classList.toggle('font-medium', isActive);
    btn.classList.toggle('border-transparent', !isActive);
    btn.classList.toggle('text-warm-gray', !isActive);
    btn.classList.toggle('font-light', !isActive);
  });
  if (section === 'report') loadReportDashboard();
  if (section === 'validation') loadValidationDashboard();
  if (section === 'feedback') loadFeedback();
}

// ─── Filter Tabs ───
function setFilter(filter) {
  AppState.filter = filter;
  AppState.page = 1;
  document.querySelectorAll('.tab-filter').forEach(btn => {
    const isActive = btn.dataset.filter === filter;
    btn.classList.toggle('border-charcoal/15', isActive);
    btn.classList.toggle('bg-charcoal', isActive);
    btn.classList.toggle('text-cream-light', isActive);
    btn.classList.toggle('text-warm-gray', !isActive);
  });
  if (filter === 'jingcai') { loadJingcaiView(); } else { loadMatchView(); }
}

// ─── 竞彩首页 ───
async function loadJingcaiView() {
  const grid = document.getElementById('matchGrid');
  grid.innerHTML = '<div class="col-span-full text-center py-24 text-warm-gray"><div class="w-10 h-10 rounded-full border-2 border-accent/20 border-t-accent animate-spin mx-auto mb-4"></div><div class="text-base font-light">加载中...</div></div>';
  try {
    const [onSale, allIssues] = await Promise.all([WCApi.Jingcai.listIssues('on_sale'), WCApi.Jingcai.listIssues()]);
    AppState.jingcaiIssues = onSale;
    AppState.jingcaiAllIssues = allIssues;
    if (onSale.length && !AppState.selectedIssueId) { AppState.selectedIssueId = onSale[0].issue_id; }
    renderJingcaiHome();
  } catch (e) {
    grid.innerHTML = `<div class="col-span-full text-center py-24 text-warm-gray"><div class="text-base font-light">${I18n.t('loading.failed')}</div><div class="text-sm mt-2 text-warm-gray">${escapeHtml(e.message)}</div></div>`;
  }
}

function renderJingcaiHome() {
  const grid = document.getElementById('matchGrid');
  const onSale = AppState.jingcaiIssues;
  const allIssues = AppState.jingcaiAllIssues;

  // 期号选择
  let issueTabs = '<div class="col-span-full flex items-center gap-2 mb-5 flex-wrap">';
  for (const issue of onSale) {
    const mc = (issue.matches || []).length;
    const isActive = issue.issue_id === AppState.selectedIssueId;
    issueTabs += `<button onclick="selectIssue('${escapeHtml(issue.issue_id)}')" class="px-3 py-1.5 text-sm font-light tracking-wide rounded-lg transition-all duration-300 ${isActive ? 'border border-charcoal/15 bg-charcoal text-cream-light' : 'border border-charcoal/10 text-charcoal hover:bg-charcoal hover:text-cream-light hover:border-charcoal'}">${I18n.t('match.issuePrefix')}${escapeHtml(issue.issue_id)}${I18n.t('match.issueSuffix')} <span class="num text-xs opacity-60">${mc}场</span></button>`;
    issueTabs += `<button onclick="showOptimalCombo(${issue.id})" class="px-3 py-1.5 text-sm font-light tracking-wide rounded-lg transition-all duration-300 border border-accent-yellow/30 text-accent-yellow hover:bg-accent-yellow/10">✨ ${I18n.t('match.optimalCombo')}</button>`;
  }
  const closedIssues = allIssues.filter(i => i.status !== 'on_sale');
  if (closedIssues.length) {
    issueTabs += `<button onclick="toggleClosedIssues()" class="px-2 py-1 text-sm font-light text-warm-gray hover:text-charcoal transition-colors duration-300">${I18n.t('data.locked')} (${closedIssues.length})</button>`;
  }
  issueTabs += '</div>';

  let closedHtml = '<div id="closedIssuesPanel" class="col-span-full hidden mb-5 glass p-5">';
  for (const issue of closedIssues) {
    const mc = (issue.matches || []).length;
    closedHtml += `<div class="flex items-center justify-between py-2.5 border-b border-charcoal/10 last:border-0">
      <span class="text-base font-light text-charcoal">${I18n.t('match.issuePrefix')}${escapeHtml(issue.issue_id)}${I18n.t('match.issueSuffix')}</span>
      <span class="text-sm px-2 py-0.5 rounded border border-beige/30 text-beige font-light">${I18n.t('data.locked')}</span>
      <span class="text-base font-light text-warm-gray num">${mc}场</span>
      <button onclick="selectIssue('${escapeHtml(issue.issue_id)}')" class="text-sm font-light text-warm-gray hover:text-beige transition-colors duration-300">${I18n.t('match.view')}</button>
    </div>`;
  }
  closedHtml += '</div>';

  const currentIssue = allIssues.find(i => i.issue_id === AppState.selectedIssueId) || onSale[0];
  if (!currentIssue) {
    grid.innerHTML = issueTabs + '<div class="col-span-full text-center py-24 text-warm-gray"><div class="text-base font-light">暂无赛事数据</div><div class="text-sm mt-2 text-warm-gray">系统每日 09:00/15:00 自动同步</div></div>';
    return;
  }

  const matchList = currentIssue.matches || [];
  const isOnSale = currentIssue.status === 'on_sale';
  const byCompetition = {};
  for (const im of matchList) {
    const comp = im.match?.competition || '其他';
    if (!byCompetition[comp]) byCompetition[comp] = [];
    byCompetition[comp].push(im);
  }

  let matchHtml = '';
  if (!isOnSale) {
    matchHtml += '<div class="col-span-full mb-4"><div class="glass p-4 border border-accent-yellow/20"><span class="text-sm font-light text-accent-yellow">本期已停售，数据仅供参考</span></div></div>';
  }
  for (const [comp, ims] of Object.entries(byCompetition)) {
    matchHtml += `<div class="col-span-full mt-5 mb-2"><span class="font-serif text-lg font-medium text-charcoal tracking-wide">${escapeHtml(comp)}</span></div>`;
    matchHtml += ims.map(im => renderJingcaiCard(im, isOnSale)).join('');
  }

  grid.innerHTML = issueTabs + closedHtml + matchHtml;
}

function renderJingcaiCard(im, isOnSale) {
  const m = im.match;
  if (!m) return '';

  const homeName = m.home_team?.name || I18n.t('match.defaultHome');
  const awayName = m.away_team?.name || I18n.t('match.defaultAway');
  const homeFlag = m.home_team?.flag || '';
  const awayFlag = m.away_team?.flag || '';
  const kickoff = fmtBJ(m.kickoff_at);
  const kickoffShort = fmtBJShort(m.kickoff_at);
  const countdown = matchCountdown(m.kickoff_at);
  const handicap = im.handicap || 0;
  const handicapLabel = handicap ? `让${handicap > 0 ? '+' : ''}${handicap}` : '';

  const spfOddsHome = m.odds_home;
  const spfOddsDraw = m.odds_draw;
  const spfOddsAway = m.odds_away;

  const rqOdds = im.rq_odds || null;
  const rqHome = rqOdds?.home || null;
  const rqDraw = rqOdds?.draw || null;
  const rqAway = rqOdds?.away || null;

  const preds = m.predictions || [];
  const spf = preds.find(p => p.play_type === 'SPF')?.probabilities || {};
  const rq = preds.find(p => p.play_type === 'RQ')?.probabilities || {};

  const spfEVHome = calcEV(spf.home, spfOddsHome);
  const spfEVDraft = calcEV(spf.draw, spfOddsDraw);
  const spfEVAway = calcEV(spf.away, spfOddsAway);

  const spfMax = Math.max(spf.home || 0, spf.draw || 0, spf.away || 0);
  const spfMaxKey = spf.home === spfMax ? 'home' : spf.away === spfMax ? 'away' : 'draw';
  const spfMaxLabel = { home: I18n.t('match.homeWin'), draw: I18n.t('match.draw'), away: I18n.t('match.awayWin') }[spfMaxKey];

  const rqEVHome = calcEV(rq.home, rqHome);
  const rqEVDraft = calcEV(rq.draw, rqDraw);
  const rqEVAway = calcEV(rq.away, rqAway);

  const scorePred = preds.find(p => p.play_type === 'SCORE')?.probabilities || {};
  const topScores = Object.entries(scorePred).sort((a, b) => b[1] - a[1]).slice(0, 2);

  return `
  <div class="glass p-5 cursor-pointer group" onclick="openJingcaiModal(${m.id}, '${escapeHtml(im.sequence || '')}')">
    <div class="flex items-center justify-between mb-3">
      <div class="flex items-center gap-2 text-base font-light text-warm-gray">
        <span class="num">${kickoffShort || kickoff}</span>
        ${im.sequence ? `<span class="text-sm px-1.5 py-0.5 rounded border border-beige/30 text-beige num">${im.sequence}</span>` : ''}
      </div>
      <div class="flex items-center gap-2">
        ${countdown ? `<span class="text-sm text-accent-yellow num font-light">${countdown}</span>` : ''}
        ${!isOnSale ? '<span class="text-sm px-2 py-0.5 rounded border border-beige/30 text-beige font-light">已停售</span>' : ''}
      </div>
    </div>

    <div class="flex items-center justify-between mb-4">
      <div class="flex items-center gap-2 min-w-0 flex-1">
        ${homeFlag ? `<span class="text-base">${homeFlag}</span>` : ''}
        <span class="text-base font-medium text-charcoal truncate">${escapeHtml(homeName)}</span>
      </div>
      <span class="text-sm text-warm-gray mx-3 num font-light">VS</span>
      <div class="flex items-center gap-2 min-w-0 flex-1 flex-row-reverse">
        ${awayFlag ? `<span class="text-base">${awayFlag}</span>` : ''}
        <span class="text-base font-medium text-charcoal truncate text-right">${escapeHtml(awayName)}</span>
      </div>
    </div>

    <div class="border-t border-accent/10 pt-3 space-y-2">
      ${renderOddsRow('主胜', spfOddsHome, spf.home, spfEVHome, spfMaxKey === 'home')}
      ${renderOddsRow('平', spfOddsDraw, spf.draw, spfEVDraft, spfMaxKey === 'draw')}
      ${renderOddsRow('客胜', spfOddsAway, spf.away, spfEVAway, spfMaxKey === 'away')}
    </div>

    ${rqOdds ? `
    <div class="border-t border-accent/10 pt-2 mt-2 space-y-2">
      <div class="text-xs uppercase tracking-[0.25em] text-warm-gray font-sans font-medium mb-1">让球 ${handicapLabel}</div>
      ${renderOddsRow('让胜', rqHome, rq.home, rqEVHome, false)}
      ${renderOddsRow('让平', rqDraw, rq.draw, rqEVDraft, false)}
      ${renderOddsRow('让负', rqAway, rq.away, rqEVAway, false)}
    </div>` : ''}

    <div class="mt-3 pt-2 border-t border-accent/10 flex items-center justify-between">
      <div class="flex items-center gap-2 text-base">
        <span class="text-xs uppercase tracking-[0.25em] text-warm-gray font-sans font-medium">${I18n.t('match.prediction')}</span>
        <span class="font-medium ${spfMaxKey === 'home' ? 'text-beige' : spfMaxKey === 'away' ? 'text-accent-cyan' : 'text-warm-gray'}">${spfMaxLabel}</span>
        <span class="num text-warm-gray font-light">${fmtPct(spf[spfMaxKey])}</span>
      </div>
      ${topScores.length ? `<div class="text-sm text-warm-gray font-light">比分 <span class="num text-beige font-medium">${topScores[0][0]}</span></div>` : ''}
    </div>

    ${(() => {
      const spfPred = preds.find(p => p.play_type === 'SPF');
      const modelVer = spfPred?.model_version || '';
      const lockedAt = spfPred?.locked_at || '';
      const checksum = spfPred?.input_checksum || '';
      if (!modelVer && !lockedAt) return '';
      const lockedTime = lockedAt ? new Date(lockedAt).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '';
      const shortChecksum = checksum ? checksum.slice(0, 8) : '';
      return `<div class="mt-2 pt-2 border-t border-accent/5 flex items-center gap-3 text-xs text-warm-gray/60 font-light num">
        ${modelVer ? `<span>v${modelVer}</span>` : ''}
        ${lockedTime ? `<span>锁定 ${lockedTime}</span>` : ''}
        ${shortChecksum ? `<span title="${checksum}">#${shortChecksum}</span>` : ''}
      </div>`;
    })()}
  </div>`;
}

function renderOddsRow(label, odds, prob, ev, isMax) {
  const probVal = prob != null ? prob : 0;
  const evVal = ev != null ? ev : null;
  const highlight = isMax ? 'text-beige' : 'text-warm-gray';
  const barWidth = Math.max((probVal || 0) * 100, 2);

  return `
  <div class="flex items-center gap-2 text-base font-light">
    <span class="w-8 ${highlight}">${label}</span>
    <div class="flex-1 h-1 bar-bg rounded-full overflow-hidden">
      <div class="h-full rounded-full ${isMax ? 'bar-fill' : 'bg-charcoal/10'}" style="width:${barWidth}%"></div>
    </div>
    <span class="w-10 text-right num text-warm-gray">${fmtOdds(odds)}</span>
    <span class="w-12 text-right num ${highlight}">${fmtPct(prob)}</span>
    <span class="w-14 text-right num ${evColor(evVal)}">${evLabel(evVal)}</span>
  </div>`;
}

function toggleClosedIssues() {
  const panel = document.getElementById('closedIssuesPanel');
  if (panel) panel.classList.toggle('hidden');
}

async function selectIssue(issueId) {
  AppState.selectedIssueId = issueId;
  if (!AppState.jingcaiDetailCache[issueId]) {
    try {
      const detail = await WCApi.Jingcai.getIssue(issueId);
      AppState.jingcaiDetailCache[issueId] = detail;
      const idx = AppState.jingcaiAllIssues.findIndex(i => i.issue_id === issueId);
      if (idx >= 0) AppState.jingcaiAllIssues[idx] = detail;
    } catch { /* fallback */ }
  }
  renderJingcaiHome();
}

// ─── 普通比赛视图 ───
async function loadMatchView() {
  const grid = document.getElementById('matchGrid');
  grid.innerHTML = '<div class="col-span-full text-center py-24 text-warm-gray"><div class="w-10 h-10 rounded-full border-2 border-accent/20 border-t-accent animate-spin mx-auto mb-4"></div><div class="text-base font-light">加载中...</div></div>';
  try {
    const matches = await WCApi.Data.getMatches();
    AppState.matches = matches;
    renderCards();
  } catch (e) {
    grid.innerHTML = '<div class="col-span-full text-center py-24 text-warm-gray"><div class="text-base font-light">加载失败</div></div>';
  }
}

function renderCards() {
  const grid = document.getElementById('matchGrid');
  if (!grid) return;

  let matches = AppState.matches || [];
  if (AppState.filter === 'today' || AppState.filter === 'tomorrow') {
    const bjNow = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Shanghai' }));
    matches = matches.filter(m => {
      if (!m.kickoff_at) return false;
      const d = new Date(new Date(m.kickoff_at).toLocaleString('en-US', { timeZone: 'Asia/Shanghai' }));
      if (AppState.filter === 'today') { return d.getDate() === bjNow.getDate() && d.getMonth() === bjNow.getMonth(); }
      const tomorrow = new Date(bjNow); tomorrow.setDate(tomorrow.getDate() + 1);
      return d.getDate() === tomorrow.getDate() && d.getMonth() === tomorrow.getMonth();
    });
  } else if (AppState.filter !== 'all' && AppState.filter !== 'jingcai') {
    matches = matches.filter(m => m.match_type === AppState.filter);
    if (AppState.filter === 'world_cup') {
      matches = matches.filter(m => m.status !== 'finished' || (m.predictions && m.predictions.length > 0));
    }
  }

  if (!matches.length) {
    const labelMap = { world_cup: I18n.t('filter.world_cup'), friendly: I18n.t('filter.friendly'), today: I18n.t('filter.today'), tomorrow: I18n.t('filter.tomorrow') };
    grid.innerHTML = `<div class="col-span-full text-center py-24 text-warm-gray"><div class="text-base font-light">暂无${labelMap[AppState.filter] || ''}比赛${I18n.t('feedback.data')}</div></div>`;
    return;
  }

  const start = (AppState.page - 1) * AppState.pageSize;
  const pageMatches = matches.slice(start, start + AppState.pageSize);
  grid.innerHTML = pageMatches.map(m => renderMatchCard(m)).join('') + renderPagination(matches.length);
}

function renderMatchCard(m) {
  const homeTeam = AppState.teams.find(t => t.id === m.home_team_id) || { name: m.home_team?.name || I18n.t('match.defaultHome'), flag: '', fifa_rank: '-' };
  const awayTeam = AppState.teams.find(t => t.id === m.away_team_id) || { name: m.away_team?.name || I18n.t('match.defaultAway'), flag: '', fifa_rank: '-' };

  const spf = m.predictions?.find(p => p.play_type === 'SPF')?.probabilities || {};
  const score = m.predictions?.find(p => p.play_type === 'SCORE')?.probabilities || {};
  const topScores = Object.entries(score).sort((a, b) => b[1] - a[1]).slice(0, 3);
  const scoreTags = topScores.map(([s, p], i) => `<span class="text-sm px-2 py-0.5 rounded ${i === 0 ? 'border border-beige/30 text-beige' : 'border border-accent/15 text-warm-gray'} font-light">${escapeHtml(s)} ${fmtPct(p)}</span>`).join(' ');

  const countdown = matchCountdown(m.kickoff_at);
  const typeBadge = m.match_type === 'friendly'
    ? '<span class="text-sm px-2 py-0.5 rounded border border-accent-yellow/30 text-accent-yellow font-light">热身赛</span>'
    : '<span class="text-sm px-2 py-0.5 rounded border border-beige/30 text-beige font-light">世界杯</span>';
  const statusBadge = m.status === 'finished'
    ? '<span class="text-sm px-2 py-0.5 rounded border border-accent/15 text-warm-gray font-light">已结束</span>'
    : '<span class="text-sm px-2 py-0.5 rounded border border-accent-cyan/30 text-accent-cyan font-light">未开赛</span>';

  return `
  <div class="glass p-6 cursor-pointer group" onclick="openModal(${m.id})">
    <div class="flex items-center justify-between mb-3">
      <div class="flex items-center gap-2 text-base font-light text-warm-gray num">
        <span>${fmtBJ(m.kickoff_at)}</span>
        <span class="text-charcoal/15">·</span>
        <span class="text-warm-gray">${m.group || m.stage || ''}</span>
      </div>
      <div class="flex items-center gap-2">${typeBadge} ${statusBadge} ${freshnessTag(m.updated_at)}</div>
    </div>
    ${countdown && m.status !== 'finished' ? `<div class="text-base text-accent-yellow num font-light mb-2">${countdown}</div>` : ''}
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-3">
        ${homeTeam.flag ? `<span class="text-xl">${homeTeam.flag}</span>` : ''}
        <div><div class="text-base font-medium text-charcoal">${escapeHtml(homeTeam.name)}</div><div class="text-sm font-light text-warm-gray num">#${homeTeam.fifa_rank}</div></div>
      </div>
      <div class="text-sm text-warm-gray num font-light">VS</div>
      <div class="flex items-center gap-3 flex-row-reverse">
        ${awayTeam.flag ? `<span class="text-xl">${awayTeam.flag}</span>` : ''}
        <div class="text-right"><div class="text-base font-medium text-charcoal">${escapeHtml(awayTeam.name)}</div><div class="text-sm font-light text-warm-gray num">#${awayTeam.fifa_rank}</div></div>
      </div>
    </div>
    <div class="mt-3 pt-3 border-t border-accent/10">
      <div class="flex justify-between text-base font-light text-warm-gray mb-3">
        <span class="flex items-center gap-1.5"><span class="text-xs uppercase tracking-[0.25em] text-warm-gray font-sans font-medium">${I18n.t('match.oddsSource')}</span> ${m.odds_source ? `<span class="px-1.5 py-0.5 rounded text-sm ${m.odds_source === 'synthetic' ? 'border border-accent/15 text-warm-gray' : 'border border-accent/30 text-accent'} font-light">${m.odds_source === 'synthetic' ? '合成' : '真实'}</span>` : ''}</span>
        <span class="num">${m.odds_home || '-'} / ${m.odds_draw || '-'} / ${m.odds_away || '-'}${m.odds_degraded ? ' <span class="text-sm px-1.5 py-0.5 rounded border border-accent-yellow/30 text-accent-yellow font-light">赔率缺失</span>' : ''}</span>
      </div>
      ${spf.home ? `
      <div class="space-y-2">
        <div class="flex items-center gap-3 text-base font-light">
          <span class="w-10 text-warm-gray">${escapeHtml(homeTeam.name)}</span>
          <div class="flex-1 h-1.5 bar-bg rounded-full overflow-hidden"><div class="h-full bar-fill rounded-full" style="width:${(spf.home || 0) * 100}%"></div></div>
          <span class="num w-10 text-right text-beige">${fmtPct(spf.home)}</span>
        </div>
        <div class="flex items-center gap-3 text-base font-light">
          <span class="w-10 text-warm-gray">平局</span>
          <div class="flex-1 h-1.5 bar-bg rounded-full overflow-hidden"><div class="h-full bar-fill rounded-full opacity-50" style="width:${(spf.draw || 0) * 100}%"></div></div>
          <span class="num w-10 text-right text-warm-gray">${fmtPct(spf.draw)}</span>
        </div>
        <div class="flex items-center gap-3 text-base font-light">
          <span class="w-10 text-warm-gray">${escapeHtml(awayTeam.name)}</span>
          <div class="flex-1 h-1.5 bar-bg rounded-full overflow-hidden"><div class="h-full bar-fill rounded-full opacity-30" style="width:${(spf.away || 0) * 100}%"></div></div>
          <span class="num w-10 text-right text-warm-gray">${fmtPct(spf.away)}</span>
        </div>
      </div>
      ${topScores.length ? `<div class="mt-3 flex items-center gap-2 flex-wrap"><span class="text-xs uppercase tracking-[0.25em] text-warm-gray font-sans font-medium">比分</span>${scoreTags}</div>` : ''}
    ` : '<div class="mt-4 text-center py-3 text-warm-gray text-sm font-light">预测数据未加载</div>'}
    </div>
    ${m.status === 'finished' ? '<div class="mt-3 pt-3 border-t border-accent/10 text-center"><span class="text-sm text-accent-green font-light">赛后验证 · 预测结果已开放</span></div>' : ''}
  </div>`;
}

// ─── 弹窗 ───
async function openJingcaiModal(matchId, seq) {
  AppState.currentMatchId = matchId;
  await openModal(matchId);
}

async function openModal(matchId) {
  AppState.currentMatchId = matchId;

  let m = null;
  for (const issue of AppState.jingcaiAllIssues) {
    const im = (issue.matches || []).find(x => x.match && x.match.id === matchId);
    if (im) {
      m = im.match;
      m._jingcaiOdds = { handicap: im.handicap || 0, rq_odds: im.rq_odds || null, score_odds: im.score_odds || null, goals_odds: im.goals_odds || null, half_odds: im.half_odds || null };
      break;
    }
  }
  if (!m) m = (AppState.matches || []).find(x => x.id === matchId);
  if (!m) return;

  let strategyData = null;
  let strategyError = null;
  try { strategyData = await WCApi.Strategy.getStrategy(matchId, 'balanced'); AppState.strategyCache[matchId] = strategyData; } catch (e) { strategyError = escapeHtml(e.message); }

  const locked = isLocked(m);
  const homeTeam = m.home_team || AppState.teams.find(t => t.id === m.home_team_id) || { name: I18n.t('match.defaultHome'), flag: '' };
  const awayTeam = m.away_team || AppState.teams.find(t => t.id === m.away_team_id) || { name: I18n.t('match.defaultAway'), flag: '' };
  const outcomeMap = { home: I18n.t('match.homeWin'), draw: I18n.t('match.draw'), away: I18n.t('match.awayWin') };

  const tabs = [I18n.t('match.spf'), I18n.t('match.rq'), I18n.t('match.score'), I18n.t('match.goals'), I18n.t('match.half')];
  const preds = strategyData?.predictions || m.predictions || [];
  const spf = preds.find(p => p.play_type === 'SPF')?.probabilities || {};
  const score = preds.find(p => p.play_type === 'SCORE')?.probabilities || {};
  const spfPred = preds.find(p => p.play_type === 'SPF');
  const modelVer = spfPred?.model_version || '';
  const lockedAt = spfPred?.locked_at || '';
  const checksum = spfPred?.input_checksum || '';
  const lockedTime = lockedAt ? new Date(lockedAt).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '';
  const shortChecksum = checksum ? checksum.slice(0, 8) : '';

  const spfForBanner = strategyData?.predictions?.find(p => p.play_type === 'SPF');
  const predOutcome = spfForBanner ? Object.keys(spfForBanner.probabilities).reduce((a, b) => spfForBanner.probabilities[a] > spfForBanner.probabilities[b] ? a : b) : null;
  const topScore = Object.entries(score).sort((a, b) => b[1] - a[1])[0] || null;

  let resultBanner = '';
  if (m.status === 'finished' && m.actual_outcome) {
    const predCorrect = predOutcome === m.actual_outcome;
    resultBanner = `
    <div class="px-6 py-4 ${predCorrect ? 'bg-accent-green/[0.06] border-b border-accent-green/20' : 'bg-accent-red/[0.06] border-b border-accent-red/20'}">
      <div class="flex items-center justify-between">
        <div>
          <div class="text-base font-medium ${predCorrect ? 'text-accent-green' : 'text-accent-red'}">${predCorrect ? '预测正确' : '预测错误'}</div>
          <div class="text-sm text-warm-gray font-light mt-0.5">预测: ${outcomeMap[predOutcome] || '?'} · 实际: ${outcomeMap[m.actual_outcome]}</div>
        </div>
        <div class="text-2xl font-bold num ${predCorrect ? 'text-accent-green' : 'text-accent-red'}">${m.actual_home_goals ?? '-'} : ${m.actual_away_goals ?? '-'}</div>
      </div>
    </div>`;
  }

  let strategySection = '';
  if (!resultBanner && m.status !== 'finished' && strategyData?.strategies?.length) {
    strategySection = `
    <div class="px-6 py-4 bg-accent/5 border-b border-accent/10">
      <div class="text-sm text-warm-gray font-light mb-3 flex items-center gap-2">
        <span class="font-serif text-lg font-medium text-charcoal">${I18n.t('match.strategy')}</span>
        <span class="text-charcoal/15">·</span>
        <span>${I18n.t('match.kellyDesc')}</span>
      </div>
      ${renderStrategies(strategyData.strategies)}
      <div id="nnBetValue" class="mt-3"></div>
    </div>`;
  }

  const homeElo = homeTeam.elo || m.home_team?.elo || '?';
  const awayElo = awayTeam.elo || m.away_team?.elo || '?';
  const eloDiff = (typeof homeElo === 'number' && typeof awayElo === 'number') ? homeElo - awayElo : null;

  const content = document.getElementById('modalContent');
  content.innerHTML = `
    <div class="sticky top-0 z-10 bg-cream/90 backdrop-blur-md border-b border-accent/10 px-5 py-3 flex items-center justify-between">
      <div>
        <div class="font-serif text-base font-medium text-charcoal tracking-wide">${escapeHtml(homeTeam.name || '主队')} ${homeTeam.flag || ''} vs ${awayTeam.flag || ''} ${escapeHtml(awayTeam.name || '客队')}</div>
        <div class="text-sm text-warm-gray font-light mt-0.5">${m.competition || ''} · ${fmtBJ(m.kickoff_at)}</div>
        ${matchCountdown(m.kickoff_at) ? `<div class="text-sm text-accent-yellow num font-light mt-0.5">${matchCountdown(m.kickoff_at)}</div>` : ''}
      </div>
      <div class="flex items-center gap-2">
        <button onclick="sendAiMessage(${m.id});closeModal()" class="px-3 py-1.5 text-xs font-medium rounded-lg bg-accent/10 text-accent hover:bg-accent/20 transition-colors">${I18n.t('tab.ai')}</button>
        <button onclick="closeModal()" class="text-warm-gray hover:text-charcoal text-xl leading-none transition-colors duration-300">&times;</button>
      </div>
    </div>

    <div class="px-5 py-2.5 bg-accent/5 border-b border-accent/10">
      <div class="flex items-center gap-4 text-base font-light flex-wrap">
        <div>
          <span class="text-xs uppercase tracking-[0.25em] text-warm-gray font-sans font-medium">预测</span>
          <span class="font-medium text-charcoal ml-1">${outcomeMap[predOutcome]}</span>
          <span class="num text-beige ml-1">(${(Math.max(spf.home || 0, spf.draw || 0, spf.away || 0) * 100).toFixed(0)}%)</span>
        </div>
        ${topScore ? `<div><span class="text-xs uppercase tracking-[0.25em] text-warm-gray font-sans font-medium">比分</span> <span class="font-medium text-beige num">${topScore[0]}</span> <span class="text-warm-gray num font-light">${fmtPct(topScore[1])}</span></div>` : ''}
        ${eloDiff != null ? `<div><span class="text-xs uppercase tracking-[0.25em] text-warm-gray font-sans font-medium">Elo</span> <span class="num ${eloDiff > 100 ? 'text-accent-green' : eloDiff > 0 ? 'text-beige' : 'text-warm-gray'} font-light">${eloDiff > 0 ? '+' : ''}${eloDiff}</span></div>` : ''}
        ${modelVer ? `<div><span class="text-xs uppercase tracking-[0.25em] text-warm-gray font-sans font-medium">模型</span> <span class="num text-warm-gray font-light">v${modelVer}</span></div>` : ''}
      </div>
      ${lockedTime || shortChecksum ? `<div class="mt-1.5 flex items-center gap-3 text-xs text-warm-gray/50 font-light num">
        ${lockedTime ? `<span>锁定 ${lockedTime}</span>` : ''}
        ${shortChecksum ? `<span title="${checksum}">#${shortChecksum}</span>` : ''}
      </div>` : ''}
      ${m._jingcaiOdds?.handicap ? `<div class="mt-2"><span class="text-sm px-2 py-0.5 rounded border border-accent-yellow/30 text-accent-yellow font-light">让${m._jingcaiOdds.handicap > 0 ? '+' : ''}${m._jingcaiOdds.handicap}</span></div>` : ''}
    </div>

    ${strategySection}
    ${resultBanner}

    <div class="px-5 py-2 border-b border-charcoal/10">
      <div class="flex gap-1">
        ${tabs.map((t, i) => `<button class="tab-btn flex-1 py-2 text-sm font-medium tracking-wide border-b-2 transition-all duration-300 ${i === 0 ? 'border-charcoal text-charcoal' : 'border-transparent text-warm-gray hover:text-charcoal'}" onclick="switchTab(this,'${t}',${m.id})">${t}</button>`).join('')}
      </div>
    </div>

    <div id="tabBody" class="px-5 py-4 space-y-4 min-h-[200px]">
      ${renderTabContent('胜平负', m, strategyData, locked, strategyError)}
    </div>`;

  document.getElementById('detailModal').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
}

function renderStrategies(strategies) {
  if (!strategies || !strategies.length) return '';
  const riskLabels = { low: I18n.t('strategy.low'), medium: I18n.t('strategy.medium'), high: I18n.t('strategy.high') };
  const riskColors = { low: 'text-accent-green', medium: 'text-accent-yellow', high: 'text-accent-red' };

  return `<div class="grid grid-cols-1 gap-2">
  ${strategies.map(s => {
    const riskClass = riskColors[s.risk_level] || riskColors.medium;
    const evVal = s.ev || 0;
    const evCls = evVal > 0.05 ? 'text-accent-green' : evVal > 0 ? 'text-accent-cyan' : 'text-warm-gray';
    return `
    <div class="p-4 bg-accent/5 rounded-xl border border-accent/15">
      <div class="flex items-center justify-between mb-1.5">
        <div class="flex items-center gap-2">
          <span class="text-base font-medium text-charcoal">${s.play_label || ''} · ${s.selection_label || ''}</span>
          <span class="text-sm ${riskClass} font-light">${riskLabels[s.risk_level] || ''}</span>
        </div>
        <div class="flex items-center gap-3 text-base num font-light">
          <span class="text-warm-gray"><span class="text-xs uppercase tracking-[0.25em] font-sans font-medium">赔率</span> <span class="text-charcoal">${s.odds?.toFixed(2) || '-'}</span></span>
          <span class="text-warm-gray"><span class="text-xs uppercase tracking-[0.25em] font-sans font-medium">概率</span> <span class="text-beige">${(s.probability * 100).toFixed(1)}%</span></span>
          <span class="${evCls}">EV ${evVal > 0 ? '+' : ''}${(evVal * 100).toFixed(1)}%</span>
        </div>
      </div>
      ${s.rationale ? `<div class="text-base text-warm-gray font-light leading-relaxed">${escapeHtml(s.rationale)}</div>` : ''}
      ${s.stake_pct ? `<div class="text-sm text-warm-gray font-light mt-1"><span class="text-xs uppercase tracking-[0.25em] font-sans font-medium">仓位</span> <span class="num text-accent-yellow">${s.stake_pct.toFixed(1)}%</span></div>` : ''}
    </div>`;
  }).join('')}
  </div>`;
}

function closeModal() {
  document.getElementById('detailModal').classList.add('hidden');
  document.body.style.overflow = '';
  AppState.currentMatchId = null;
}

function switchTab(btn, tabName, matchId) {
  document.querySelectorAll('.tab-btn').forEach(b => {
    b.classList.remove('border-charcoal', 'text-charcoal');
    b.classList.add('border-transparent', 'text-warm-gray');
  });
  btn.classList.remove('border-transparent', 'text-warm-gray');
  btn.classList.add('border-charcoal', 'text-charcoal');

  let m = null;
  for (const issue of AppState.jingcaiAllIssues) {
    const im = (issue.matches || []).find(x => x.match && x.match.id === matchId);
    if (im) { m = im.match; m._jingcaiOdds = { handicap: im.handicap, rq_odds: im.rq_odds, score_odds: im.score_odds, goals_odds: im.goals_odds, half_odds: im.half_odds }; break; }
  }
  if (!m) m = (AppState.matches || []).find(x => x.id === matchId);

  const locked = isLocked(m);
  const strategyData = AppState.strategyCache[matchId] || { predictions: m?.predictions || [] };
  document.getElementById('tabBody').innerHTML = renderTabContent(tabName, m, strategyData, locked, null);
}

function renderTabContent(tabName, match, strategyData, locked, error) {
  const preds = strategyData?.predictions || [];
  switch (tabName) {
    case I18n.t('match.spf'): return renderSPFTab(preds, match, locked);
    case I18n.t('match.rq'): return renderRQTab(preds, match, locked);
    case I18n.t('match.score'): return renderScoreTab(preds, match, locked);
    case I18n.t('match.goals'): return renderGoalsTab(preds, match, locked);
    case I18n.t('match.half'): return renderHalfTab(preds, match, locked);
    default: return '';
  }
}

function renderSPFTab(preds, match, locked) {
  const spf = preds.find(p => p.play_type === 'SPF')?.probabilities || {};
  const odds = { home: match.odds_home || null, draw: match.odds_draw || null, away: match.odds_away || null };
  const items = [
    { key: 'home', label: I18n.t('match.homeWin'), prob: spf.home, odds: odds.home },
    { key: 'draw', label: I18n.t('match.draw'), prob: spf.draw, odds: odds.draw },
    { key: 'away', label: I18n.t('match.awayWin'), prob: spf.away, odds: odds.away },
  ].map(x => ({ ...x, ev: calcEV(x.prob, x.odds) }));

  const html = `
  <div class="text-xs uppercase tracking-[0.25em] text-warm-gray font-sans font-medium mb-4">${I18n.t('match.spfTab')}</div>
  <div class="space-y-3">
    ${items.map((x, i) => `
    <div class="flex items-center gap-3">
      <span class="w-10 text-base text-warm-gray font-light">${x.label}</span>
      <div class="flex-1 h-2 bar-bg rounded-full overflow-hidden">
        <div class="h-full rounded-full ${i === 0 ? 'bar-fill' : i === 1 ? 'bg-accent-cyan/50' : 'bg-accent-cyan/25'}" style="width:${(x.prob || 0) * 100}%"></div>
      </div>
      <span class="num w-12 text-right text-base ${x.ev != null && x.ev > 0.05 ? 'text-accent-green' : x.ev != null && x.ev > 0 ? 'text-accent-cyan' : 'text-warm-gray'} font-light">${fmtPct(x.prob)}</span>
      <span class="num w-10 text-right text-base text-warm-gray font-light">${fmtOdds(x.odds)}</span>
      <span class="num w-14 text-right text-sm ${evColor(x.ev)}">${evLabel(x.ev)}</span>
    </div>`).join('')}
  </div>`;
  return locked && match.status !== 'finished' ? lockOverlay(html) : html;
}

function renderRQTab(preds, match, locked) {
  const rq = preds.find(p => p.play_type === 'RQ')?.probabilities || {};
  const rqOdds = match?._jingcaiOdds?.rq_odds || null;
  const handicap = match?._jingcaiOdds?.handicap || 0;
  const handicapLabel = handicap ? `让${handicap > 0 ? '+' : ''}${handicap}` : '';

  const items = [{ key: 'home', label: I18n.t('match.rqWin') }, { key: 'draw', label: I18n.t('match.rqDraw') }, { key: 'away', label: I18n.t('match.rqLose') }].map(x => {
    const prob = rq[x.key] || 0;
    const odds = rqOdds ? rqOdds[x.key] : null;
    return { ...x, prob, odds, ev: calcEV(prob, odds) };
  });

  const html = `
  <div class="text-xs uppercase tracking-[0.25em] text-warm-gray font-sans font-medium mb-4">${I18n.t('match.rq')} · ${I18n.t('match.model')}${I18n.t('strategy.probability')}${rqOdds ? ' + 赛事赔率 EV' : '分布'}</div>
  ${handicapLabel ? `<div class="text-center mb-4"><span class="text-sm px-2 py-0.5 rounded border border-accent-yellow/30 text-accent-yellow font-light">${escapeHtml(handicapLabel)}</span></div>` : ''}
  <div class="space-y-3">
    ${items.map((x, i) => `
    <div class="flex items-center gap-3">
      <span class="w-10 text-base text-warm-gray font-light">${x.label}</span>
      <div class="flex-1 h-2 bar-bg rounded-full overflow-hidden">
        <div class="h-full rounded-full ${i === 0 ? 'bar-fill' : i === 1 ? 'bg-accent-cyan/50' : 'bg-accent-cyan/25'}" style="width:${(x.prob || 0) * 100}%"></div>
      </div>
      <span class="num w-12 text-right text-base text-beige font-light">${fmtPct(x.prob)}</span>
      ${x.odds != null ? `<span class="num w-10 text-right text-base text-warm-gray font-light">${fmtOdds(x.odds)}</span>` : ''}
      <span class="num w-14 text-right text-sm ${evColor(x.ev)}">${evLabel(x.ev)}</span>
    </div>`).join('')}
  </div>`;
  return locked && match.status !== 'finished' ? lockOverlay(html) : html;
}

function renderScoreTab(preds, match, locked) {
  const score = preds.find(p => p.play_type === 'SCORE')?.probabilities || {};
  const scoreOdds = match?._jingcaiOdds?.score_odds || null;
  const entries = Object.entries(score).sort((a, b) => b[1] - a[1]).slice(0, 8);

  const html = `
  <div class="text-xs uppercase tracking-[0.25em] text-warm-gray font-sans font-medium mb-4">${I18n.t('match.score')}${scoreOdds ? ' + 市场EV' : '概率'}</div>
  <div class="grid grid-cols-4 gap-2">
    ${entries.map(([s, p]) => {
      const odds = scoreOdds ? scoreOdds[s] : null;
      const ev = calcEV(p, odds);
      return `
      <div class="p-3 bg-white rounded-xl border ${ev != null && ev > 0.05 ? 'border-accent-green/20' : 'border-charcoal/10'} text-center">
        <div class="num text-base font-medium text-charcoal">${escapeHtml(s)}</div>
        <div class="num text-base text-beige font-light mt-0.5">${fmtPct(p)}</div>
        ${odds != null ? `<div class="num text-sm text-warm-gray font-light mt-0.5">${fmtOdds(odds)}</div>` : ''}
        ${ev != null ? `<div class="num text-sm mt-0.5 ${evColor(ev)}">${evLabel(ev)}</div>` : ''}
      </div>`;
    }).join('')}
  </div>`;
  return locked ? lockOverlay(html) : html;
}

function renderGoalsTab(preds, match, locked) {
  const goals = preds.find(p => p.play_type === 'GOALS')?.probabilities || {};
  const goalsOdds = match?._jingcaiOdds?.goals_odds || null;
  const entries = Object.entries(goals).sort((a, b) => parseInt(a[0].replace('+', '')) - parseInt(b[0].replace('+', '')));

  const html = `
  <div class="text-xs uppercase tracking-[0.25em] text-warm-gray font-sans font-medium mb-4">${I18n.t('match.goals')}${goalsOdds ? ' + 市场EV' : ''}</div>
  <div class="space-y-2">
    ${entries.map(([g, p], i) => {
      const odds = goalsOdds ? goalsOdds[g] : null;
      const ev = calcEV(p, odds);
      return `
      <div class="flex items-center gap-3">
        <span class="w-8 text-base text-warm-gray font-light">${g}球</span>
        <div class="flex-1 h-2 bar-bg rounded-full overflow-hidden">
          <div class="h-full rounded-full ${i < 2 ? 'bar-fill' : i < 4 ? 'bg-accent-cyan/40' : 'bg-accent-cyan/20'}" style="width:${(p || 0) * 100}%"></div>
        </div>
        <span class="num w-12 text-right text-base text-beige font-light">${fmtPct(p)}</span>
        ${odds != null ? `<span class="num w-10 text-right text-base text-warm-gray font-light">${fmtOdds(odds)}</span>` : ''}
        <span class="num w-14 text-right text-sm ${evColor(ev)}">${evLabel(ev)}</span>
      </div>`;
    }).join('')}
  </div>`;
  return locked ? lockOverlay(html) : html;
}

function renderHalfTab(preds, match, locked) {
  const half = preds.find(p => p.play_type === 'HALF')?.probabilities || {};
  const halfOdds = match?._jingcaiOdds?.half_odds || null;
  const entries = Object.entries(half).sort((a, b) => b[1] - a[1]).slice(0, 6);

  const html = `
  <div class="text-xs uppercase tracking-[0.25em] text-warm-gray font-sans font-medium mb-4">${I18n.t('match.half')}${halfOdds ? ' + 市场EV' : ''}</div>
  <div class="space-y-2">
    ${entries.map(([h, p], i) => {
      const odds = halfOdds ? halfOdds[h] : null;
      const ev = calcEV(p, odds);
      return `
      <div class="flex items-center gap-3">
        <span class="w-10 text-base text-warm-gray font-light">${escapeHtml(h)}</span>
        <div class="flex-1 h-2 bar-bg rounded-full overflow-hidden">
          <div class="h-full rounded-full ${i === 0 ? 'bar-fill' : i === 1 ? 'bg-accent-cyan/50' : 'bg-accent-cyan/25'}" style="width:${(p || 0) * 100}%"></div>
        </div>
        <span class="num w-12 text-right text-base text-beige font-light">${fmtPct(p)}</span>
        ${odds != null ? `<span class="num w-10 text-right text-base text-warm-gray font-light">${fmtOdds(odds)}</span>` : ''}
        <span class="num w-14 text-right text-sm ${evColor(ev)}">${evLabel(ev)}</span>
      </div>`;
    }).join('')}
  </div>`;
  return locked ? lockOverlay(html) : html;
}

// ─── 分页 ───
function renderPagination(totalItems) {
  const totalPages = Math.ceil(totalItems / AppState.pageSize);
  if (totalPages <= 1) return '';
  let html = '<div class="col-span-full flex items-center justify-center gap-2 mt-8">';
  for (let p = 1; p <= totalPages; p++) {
    html += `<button onclick="goToPage(${p})" class="w-8 h-8 text-base rounded-lg ${p === AppState.page ? 'bg-charcoal text-cream-light' : 'text-warm-gray hover:text-charcoal border border-charcoal/10'} transition-all duration-300 num font-light">${p}</button>`;
    }
    html += '</div>';
    grid.innerHTML = html;
}

// ─── AI 分析聊天 ───────────────────────────────
const AiState = { history: [], loading: false };

function addAiMessage(text, isUser) {
  const container = document.getElementById('aiChatMessages');
  const div = document.createElement('div');
  div.className = 'flex items-start gap-3 ai-message ' + (isUser ? 'ai-user' : 'ai-bot');
  if (isUser) {
    div.innerHTML = [
      '<div class="bg-charcoal text-cream-light rounded-2xl rounded-tr-sm px-5 py-3 text-sm leading-relaxed max-w-[80%] ml-auto">',
      escapeHtml(text),
      '</div>',
      '<div class="w-8 h-8 rounded-full bg-charcoal/10 flex items-center justify-center flex-shrink-0 mt-1">',
      '<svg class="w-4 h-4 text-charcoal" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>',
      '</div>',
    ].join('');
  } else {
    div.innerHTML = [
      '<div class="w-8 h-8 rounded-full bg-accent/10 flex items-center justify-center flex-shrink-0 mt-1">',
      '<svg class="w-4 h-4 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>',
      '</div>',
      '<div class="bg-warm-gray/5 rounded-2xl rounded-tl-sm px-5 py-4 text-sm leading-relaxed">',
      '<p class="font-medium text-accent mb-1">AI 分析助手</p>',
      '<div>' + markedParse(text) + '</div>',
      '</div>',
    ].join('');
  }
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function markedParse(text) {
  return text.replace(/### (.+)/g, '<h3 class="text-base font-medium mt-3 mb-1">$1</h3>')
    .replace(/\*\*(.+?)\*\*/g, '<strong class="font-medium">$1</strong>')
    .replace(/\n/g, '<br>');
}

async function sendAiMessage(matchId) {
  const input = document.getElementById('aiInput');
  const text = (matchId ? null : input.value.trim());
  if (!text && !matchId) return;
  if (AiState.loading) return;

  const msg = matchId ? I18n.t('ai.requestAnalyze') : text;
  if (!matchId) {
    addAiMessage(msg, true);
    input.value = '';
    AiState.history.push({ role: 'user', content: msg });
  }

  AiState.loading = true;
  const btn = document.querySelector('#sectionAI .btn-primary');
  if (btn) btn.textContent = I18n.t('ai.analyzing');

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg, match_id: matchId || undefined, history: AiState.history.slice(-10) }),
    });
    const data = await resp.json();
    if (resp.ok) {
      addAiMessage(data.reply, false);
      AiState.history.push({ role: 'assistant', content: data.reply });
    } else {
      addAiMessage('抱歉，AI 服务暂时不可用。' + (data.detail ? ' (' + data.detail + ')' : ''), false);
    }
  } catch (e) {
    addAiMessage(I18n.t('ai.networkError'), false);
  } finally {
    AiState.loading = false;
    if (btn) btn.textContent = I18n.t('feedback.submit');
  }
}

function goToPage(page) {
  AppState.page = page;
  renderCards();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ─── Toast ───
function showToast(message, type = 'error') {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'fixed top-16 right-4 z-[80] flex flex-col gap-2';
    document.body.appendChild(container);
  }
  const colors = { error: 'bg-accent-red', success: 'bg-accent-green', info: 'bg-charcoal' };
  const el = document.createElement('div');
  el.className = `${colors[type] || colors.info} text-cream text-sm font-light px-4 py-2.5 rounded-xl shadow-lg transition-opacity duration-300`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, 4000);
}

// ─── 登录 ───
function showLoginModal() { document.getElementById('loginModal')?.classList.remove('hidden'); }
function closeLoginModal() { document.getElementById('loginModal')?.classList.add('hidden'); }

function handleLogout() {
  WCApi.Auth.logout();
  AppState.user = null;
  renderNavUser();
  loadJingcaiView();
}

async function handleLogin(e) {
  e.preventDefault();
  const email = document.getElementById('loginEmail').value;
  const password = document.getElementById('loginPassword').value;
  try {
    await WCApi.Auth.login(email, password);
    AppState.user = await WCApi.Auth.me();
    closeLoginModal(); renderNavUser(); loadJingcaiView();
  } catch (err) {
    const errorEl = document.getElementById('loginError');
    errorEl.textContent = err.message || I18n.t('modal.loginFailed');
    errorEl.classList.remove('hidden');
  }
}

async function handleRegister(e) {
  e.preventDefault();
  const email = document.getElementById('loginEmail').value;
  const password = document.getElementById('loginPassword').value;
  try {
    await WCApi.Auth.register(email, password);
    AppState.user = await WCApi.Auth.me();
    closeLoginModal(); renderNavUser(); loadJingcaiView();
  } catch (err) {
    const errorEl = document.getElementById('loginError');
    errorEl.textContent = err.message || I18n.t('modal.registerFailed');
    errorEl.classList.remove('hidden');
  }
}

// ─── 卡密兑换 ───
function showRedeemModal() {
  if (!WCApi.Auth.isLoggedIn()) { showLoginModal(); return; }
  document.getElementById('redeemModal')?.classList.remove('hidden');
}
function closeRedeemModal() { document.getElementById('redeemModal')?.classList.add('hidden'); }

async function handleRedeem(e) {
  if (e) e.preventDefault();
  const key = document.getElementById('redeemKey').value.trim().toUpperCase();
  const errorEl = document.getElementById('redeemError');
  const successEl = document.getElementById('redeemSuccess');
  try {
    const result = await WCApi.License.redeem(key);
    errorEl.classList.add('hidden');
    successEl.textContent = result.message || I18n.t('modal.redeemSuccess');
    successEl.classList.remove('hidden');
    AppState.user = await WCApi.Auth.me();
    renderNavUser();
    setTimeout(() => { closeRedeemModal(); successEl.classList.add('hidden'); }, 1500);
  } catch (err) {
    successEl.classList.add('hidden');
    errorEl.textContent = err.message || I18n.t('modal.redeemFailed');
    errorEl.classList.remove('hidden');
  }
}

// ─── 世界杯倒计时 ───
function startWcCountdown() {
  const wcOpen = new Date('2026-06-11T08:00:00+08:00');
  const el = document.getElementById('wcCountdown');
  if (!el) return;
  function tick() {
    const diff = wcOpen - new Date();
    if (diff <= 0) { el.textContent = I18n.t('countdown.opened'); return; }
    const days = Math.floor(diff / 86400000);
    const hours = Math.floor((diff % 86400000) / 3600000);
    el.textContent = `${days}天 ${hours}小时`;
  }
  tick(); setInterval(tick, 60000);
}

// ─── 验证看板 ───
async function loadValidationDashboard() {
  const container = document.getElementById('validationDashboard');
  if (!container) return;
  try {
    const health = await (await fetch('/api/health')).json();
    if (health.status !== 'ok') {
      container.innerHTML = `<div class="glass p-5 border border-accent-yellow/20"><div class="text-base text-accent-yellow font-medium">${I18n.t('validation.systemError')}</div><div class="text-sm text-warm-gray font-light mt-1">${escapeHtml(health.checks?.alerts || '')}</div></div>`;
    }
  } catch { /* best-effort */ }
  try {
    const report = await WCApi.Validation.getReport('friendly');
    container.innerHTML = renderValidationHtml(report);
  } catch { container.innerHTML = '<div class="text-center py-12 text-warm-gray text-base font-light">验证数据加载失败</div>'; }
}

function renderValidationHtml(report) {
  const s = report.summary;
  const matches = report.matches || [];
  if (s.validated_matches === 0) {
    return '<div class="glass p-8 text-center"><div class="text-base text-warm-gray font-light">暂无已验证比赛</div><div class="text-base text-warm-gray font-light mt-1">比赛结束后自动更新</div></div>';
  }

  const outcomeMap = { home: I18n.t('match.homeWin'), draw: I18n.t('match.draw'), away: I18n.t('match.awayWin') };
  const matchRows = matches.map(m => `
  <div class="flex items-center gap-3 py-3 border-b border-charcoal/10 last:border-0">
    <div class="w-6 text-center">${m.direction_correct ? '<span class="text-accent-green text-base">&#10003;</span>' : '<span class="text-accent-red text-base">&#10007;</span>'}</div>
    <div class="flex-1 min-w-0">
      <div class="text-base text-charcoal font-light truncate">${escapeHtml(m.home_team)} vs ${escapeHtml(m.away_team)}</div>
      <div class="text-sm text-warm-gray font-light mt-0.5">预测: ${outcomeMap[m.predicted_outcome]} (${(m.probabilities[m.predicted_outcome] * 100).toFixed(0)}%) · 实际: ${outcomeMap[m.actual_outcome]}${m.actual_score ? ' · ' + m.actual_score : ''}</div>
    </div>
    <div class="text-sm text-warm-gray num font-light">Brier ${m.brier_score.toFixed(3)}</div>
  </div>`).join('');

  return `
  <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
    <div class="glass p-6 text-center"><div class="text-2xl font-bold num ${s.direction_accuracy >= 0.6 ? 'text-accent-green' : 'text-accent-yellow'}">${(s.direction_accuracy * 100).toFixed(0)}%</div><div class="text-base text-warm-gray font-light mt-1">${I18n.t('validation.directionAccuracy')}</div></div>
    <div class="glass p-6 text-center"><div class="text-2xl font-bold num text-accent-cyan">${s.avg_brier_score.toFixed(3)}</div><div class="text-base text-warm-gray font-light mt-1">Brier Score</div></div>
    <div class="glass p-6 text-center"><div class="text-2xl font-bold num text-charcoal">${s.validated_matches}</div><div class="text-base text-warm-gray font-light mt-1">${I18n.t('validation.validatedCount')}</div></div>
    <div class="glass p-6 text-center"><div class="text-2xl font-bold num text-charcoal">${(s.avg_max_prob * 100).toFixed(0)}%</div><div class="text-base text-warm-gray font-light mt-1">${I18n.t('validation.avgMaxProb')}</div></div>
  </div>
  <div class="glass p-6">
    <div class="text-xs uppercase tracking-[0.25em] text-warm-gray font-sans font-medium mb-3">${I18n.t('validation.detail')}</div>
    <div>${matchRows}</div>
  </div>`;
}

// ─── 启动 ───
document.addEventListener('DOMContentLoaded', initApp);

// ─── Feedback Board ───
let feedbackCategory = 'all';

async function loadFeedback() {
  const el = document.getElementById('feedbackBoard');
  if (!el) return;
  try {
    const params = {};
    if (feedbackCategory !== 'all') params.category = feedbackCategory;
    const data = await WCApi.Feedback.list(params);
    renderFeedbackBoard(data || []);
  } catch { el.innerHTML = '<div class="text-center py-12 text-warm-gray text-base font-light">加载留言失败</div>'; }
}

function renderFeedbackBoard(items) {
  const el = document.getElementById('feedbackBoard');
  if (!el) return;

  const categories = [
    { key: 'all', label: I18n.t('filter.all') },
    { key: 'discussion', label: I18n.t('feedback.discussion') },
    { key: 'suggestion', label: I18n.t('feedback.suggestion') },
    { key: 'bug', label: I18n.t('feedback.bug') },
    { key: 'data_issue', label: I18n.t('feedback.data') },
  ];

  const categoryTabs = categories.map(c =>
    `<button onclick="setFeedbackCategory('${c.key}')" class="px-4 py-2 text-sm font-light tracking-wide border-b-2 transition-all duration-300 ${feedbackCategory === c.key ? 'border-charcoal text-charcoal font-medium' : 'border-transparent text-warm-gray hover:text-charcoal'}">${c.label}</button>`
  ).join('');

  const catLabel = { suggestion: I18n.t('feedback.suggestion'), bug: I18n.t('feedback.bug'), data_issue: I18n.t('feedback.data'), discussion: I18n.t('feedback.discussion') };

  const list = items.length > 0 ? items.map(fb => `
  <div class="glass p-5">
    <div class="flex items-center gap-2 mb-2">
      <span class="text-sm px-2 py-0.5 rounded border border-beige/30 text-beige font-light">${catLabel[fb.category] || fb.category}</span>
      <span class="text-base text-charcoal font-light">${fb.author}</span>
      <span class="text-sm text-warm-gray font-light ml-auto">${fb.created_at ? new Date(fb.created_at).toLocaleDateString('zh-CN') : ''}</span>
    </div>
    <p class="text-base text-charcoal font-light leading-relaxed">${escapeHtml(fb.content)}</p>
    <div class="flex items-center gap-3 mt-2">
      <button onclick="likeFeedback(${fb.id})" class="text-base text-warm-gray hover:text-beige font-light transition-colors duration-300">${fb.likes || 0}</button>
    </div>
  </div>`).join('') : '<div class="text-center py-12 text-warm-gray text-base font-light">暂无留言</div>';

  el.innerHTML = `
  <div class="flex items-center gap-1.5 mb-5 flex-wrap">${categoryTabs}</div>
  <div class="glass p-5 mb-5">
    <textarea id="feedbackInput" rows="2" placeholder="${I18n.t('feedback.placeholder')}" class="w-full bg-transparent text-base text-charcoal font-light placeholder-warm-gray-light focus:outline-none resize-none"></textarea>
    <div id="feedbackStatus" class="text-sm text-accent-red font-light mb-2 hidden"></div>
    <div class="flex items-center justify-between mt-2">
      <select id="feedbackCat" class="bg-cream border border-charcoal/10 rounded-lg text-sm text-charcoal font-light px-2 py-1 focus:outline-none focus:border-beige transition-colors">
        <option value="discussion">${I18n.t('feedback.discussion')}</option>
        <option value="suggestion">${I18n.t('feedback.suggestion')}</option>
        <option value="bug">${I18n.t('feedback.bug')}</option>
        <option value="data_issue">${I18n.t('feedback.data')}</option>
      </select>
      <div class="flex items-center gap-3">
        <label class="flex items-center gap-1 text-sm text-warm-gray font-light"><input type="checkbox" id="feedbackAnon" class="rounded border-charcoal/10"> ${I18n.t('feedback.anonymous')}</label>
        <button onclick="submitFeedback()" class="px-4 py-1.5 border border-charcoal/15 text-charcoal hover:bg-charcoal hover:text-cream-light text-sm font-medium tracking-wide rounded-lg transition-all duration-300">${I18n.t('feedback.submit')}</button>
      </div>
    </div>
  </div>
  <div class="space-y-3">${list}</div>`;
}

function setFeedbackCategory(cat) { feedbackCategory = cat; loadFeedback(); }

async function submitFeedback() {
  const status = document.getElementById('feedbackStatus');
  const input = document.getElementById('feedbackInput');
  const cat = document.getElementById('feedbackCat');
  const anon = document.getElementById('feedbackAnon');
  if (!input || !cat || !status) return;
  status.classList.add('hidden');
  const content = input.value.trim();
  if (!content || content.length < 5) {
    status.textContent = I18n.t('feedback.minLength');
    status.classList.remove('hidden');
    input.focus();
    return;
  }
  try {
    await WCApi.Feedback.create(cat.value, content, null, anon?.checked);
    input.value = '';
    loadFeedback();
  } catch (e) {
    status.textContent = '发送失败: ' + (e.message || '未知错误');
    status.classList.remove('hidden');
  }
}

async function likeFeedback(id) {
  try { await WCApi.Feedback.like(id); loadFeedback(); } catch { /* */ }
}

// ─── Settings Modal ───
async function openSettingsModal() {
  const modal = document.getElementById('settingsModal');
  modal.classList.remove('hidden');
  const content = document.getElementById('settingsContent');
  if (!WCApi.Auth.isLoggedIn()) { content.innerHTML = '<div class="text-center py-4 text-warm-gray text-sm font-light">请先登录</div>'; return; }
  try {
    const s = await WCApi.Settings.get();
    const riskTiers = [
      { value: 'conservative', label: I18n.t('settings.conservative'), desc: I18n.t('settings.conservativeDesc') },
      { value: 'balanced', label: I18n.t('settings.balanced'), desc: I18n.t('settings.balancedDesc') },
      { value: 'aggressive', label: I18n.t('settings.aggressive'), desc: I18n.t('settings.aggressiveDesc') },
      { value: 'speculative', label: I18n.t('settings.speculative'), desc: I18n.t('settings.speculativeDesc') },
    ];
    content.innerHTML = `
    <div>
      <label class="block text-xs uppercase tracking-[0.25em] text-warm-gray font-sans font-medium mb-2">${I18n.t('settings.riskTier')}</label>
      <div class="grid grid-cols-2 gap-2">
        ${riskTiers.map(t => `
        <button onclick="setRiskTier('${t.value}')" class="p-3 rounded-xl text-left transition-all duration-300 ${s.risk_tier === t.value ? 'border border-beige/30 bg-beige/5' : 'bg-white border border-charcoal/10 hover:border-beige/30'}">
          <div class="text-base font-medium ${s.risk_tier === t.value ? 'text-beige' : 'text-charcoal'}">${t.label}</div>
          <div class="text-sm text-warm-gray font-light mt-0.5">${t.desc}</div>
        </button>`).join('')}
      </div>
    </div>
    <div class="flex items-center justify-between py-2.5">
      <span class="text-base text-charcoal font-light">${I18n.t('settings.showEV')}</span>
      <label class="relative inline-flex cursor-pointer"><input type="checkbox" ${s.show_ev ? 'checked' : ''} onchange="toggleSetting('show_ev', this.checked)" class="sr-only peer"><div class="w-8 h-4 bg-cream-dark peer-checked:bg-beige rounded-full after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:bg-white after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:after:translate-x-4"></div></label>
    </div>
    <div class="flex items-center justify-between py-2.5">
      <span class="text-base text-charcoal font-light">${I18n.t('settings.showProbability')}</span>
      <label class="relative inline-flex cursor-pointer"><input type="checkbox" ${s.show_probability ? 'checked' : ''} onchange="toggleSetting('show_probability', this.checked)" class="sr-only peer"><div class="w-8 h-4 bg-cream-dark peer-checked:bg-beige rounded-full after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:bg-white after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:after:translate-x-4"></div></label>
    </div>
    <div class="flex items-center justify-between py-2.5">
      <span class="text-base text-charcoal font-light">${I18n.t('settings.notifyOdds')}</span>
      <label class="relative inline-flex cursor-pointer"><input type="checkbox" ${s.notify_odds_change ? 'checked' : ''} onchange="toggleSetting('notify_odds_change', this.checked)" class="sr-only peer"><div class="w-8 h-4 bg-cream-dark peer-checked:bg-beige rounded-full after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:bg-white after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:after:translate-x-4"></div></label>
    </div>
    <div class="flex items-center justify-between py-2.5">
      <span class="text-base text-charcoal font-light">${I18n.t('settings.notifyMatch')}</span>
      <label class="relative inline-flex cursor-pointer"><input type="checkbox" ${s.notify_match_start ? 'checked' : ''} onchange="toggleSetting('notify_match_start', this.checked)" class="sr-only peer"><div class="w-8 h-4 bg-cream-dark peer-checked:bg-beige rounded-full after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:bg-white after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:after:translate-x-4"></div></label>
    </div>`;
  } catch { content.innerHTML = '<div class="text-center py-4 text-warm-gray text-sm font-light">加载设置失败</div>'; }
}

function closeSettingsModal() { document.getElementById('settingsModal').classList.add('hidden'); }

async function setRiskTier(tier) {
  try { await WCApi.Settings.update({ risk_tier: tier }); openSettingsModal(); } catch (e) { alert('设置失败: ' + e.message); }
}

async function toggleSetting(key, value) {
  try { await WCApi.Settings.update({ [key]: value }); } catch { /* */ }
}

// ─── Neural Network Bet Value ───
async function loadNNBetValue(matchId) {
  const el = document.getElementById('nnBetValue');
  if (!el) return;
  try {
    const data = await WCApi.BetNN.predict(matchId);
    if (!data.ready) { el.innerHTML = '<div class="text-sm text-warm-gray font-light">NN模型尚未训练</div>'; return; }
    const vals = data.bet_values;
    const selMap = { home: I18n.t('match.homeWin'), draw: I18n.t('match.draw'), away: I18n.t('match.awayWin') };
    const maxVal = Math.max(vals.home, vals.draw, vals.away);
    const bars = ['home', 'draw', 'away'].map(sel => {
      const v = vals[sel]; const isBest = v === maxVal; const pct = (v * 100).toFixed(0);
      const color = isBest ? 'bg-beige' : 'bg-charcoal/10';
      return `<div class="flex items-center gap-2 text-base font-light">
        <span class="w-6 text-warm-gray">${selMap[sel]}</span>
        <div class="flex-1 h-2 bg-accent/10 rounded-full overflow-hidden"><div class="${color} h-full rounded-full" style="width:${pct}%"></div></div>
        <span class="w-10 text-right num ${isBest ? 'text-beige' : 'text-warm-gray'} font-light">${pct}%</span>
      </div>`;
    }).join('');
    el.innerHTML = `
    <div class="text-sm text-warm-gray font-light mb-1.5 flex items-center gap-1">
      <span class="text-beige font-medium">${I18n.t('nn.title')}</span>
      <span class="text-charcoal/15">·</span>
      <span>${I18n.t('nn.desc')}</span>
    </div>
    ${bars}
    <div class="text-sm text-beige font-light mt-1">${I18n.t('match.model')}估算: ${selMap[data.recommended]} (${(data.confidence * 100).toFixed(0)}%)</div>`;
  } catch { el.innerHTML = ''; }
}

// ─── Prediction Report ───
async function loadReportDashboard() {
  const el = document.getElementById('reportDashboard');
  if (!el) return;
  el.innerHTML = '<div class="text-center py-12 text-warm-gray text-base font-light"><div class="w-10 h-10 rounded-full border-2 border-accent/20 border-t-accent animate-spin mx-auto mb-4"></div>加载中...</div>';
  try {
    const data = await WCApi.Jingcai.getReport();
    const reports = data.reports || [];
    if (!reports.length) { el.innerHTML = '<div class="text-center py-12 text-warm-gray text-base font-light">暂无期号数据</div>'; return; }
    el.innerHTML = reports.map(r => renderIssueReport(r)).join('');
  } catch (err) { el.innerHTML = `<div class="text-center py-12 text-accent-red text-base font-light">${I18n.t('loading.failed')}: ${err.message}  </div>`;
}
}

function renderIssueReport(r) {
  const hasResult = ['drawn', 'verified', 'closed'].includes(r.status);
  const finishedMatches = (r.matches || []).filter(m => m.actual_outcome != null);
  const showAccuracy = finishedMatches.length > 0;
  const statusColors = { on_sale: 'text-accent-green', locked: 'text-accent-yellow', drawn: 'text-accent-cyan', verified: 'text-accent-cyan', closed: 'text-warm-gray' };
  const statusLabels = { on_sale: I18n.t('data.onSale'), locked: '已锁定', drawn: I18n.t('data.drawn'), verified: I18n.t('data.verified'), closed: '已关闭' };
  const statusColor = statusColors[r.status] || 'text-warm-gray';
  const statusLabel = statusLabels[r.status] || r.status;

  const accuracyBar = showAccuracy
    ? `<div class="flex items-center gap-2 text-sm"><span class="text-xs uppercase tracking-[0.25em] text-warm-gray font-sans font-medium">${I18n.t('report.accuracyLabel')}</span><div class="flex-1 h-1.5 bg-accent/10 rounded-full overflow-hidden"><div class="h-full rounded-full ${r.accuracy >= 0.5 ? 'bg-accent-green' : 'bg-accent-red'}" style="width:${(r.accuracy * 100).toFixed(0)}%"></div></div><span class="num ${r.accuracy >= 0.5 ? 'text-accent-green' : 'text-accent-red'}">${(r.accuracy * 100).toFixed(1)}%</span></div>`
    : '';

  const matchRows = (r.matches || []).map(m => renderReportMatch(m, m.actual_outcome != null)).join('');

  return `<div class="glass-card p-6 space-y-3">
  <div class="flex items-center justify-between">
    <div class="flex items-center gap-2">
      <span class="font-serif text-lg font-medium text-charcoal num">${r.issue_id}</span>
      <span class="text-sm px-2 py-0.5 rounded border border-beige/30 ${statusColor}">${statusLabel}</span>
      <span class="text-sm text-warm-gray font-light">${r.issue_type || ''}</span>
    </div>
    <div class="flex items-center gap-3 text-sm num font-light">
      ${showAccuracy ? `<span class="text-accent-green">${r.spf_hits}/${finishedMatches.length}</span><span class="text-warm-gray">R9: ${r.r9_hits}/9</span>` : hasResult ? `<span class="text-accent-green">${r.spf_hits}/${r.total_matches}</span><span class="text-warm-gray">R9: ${r.r9_hits}/9</span>` : `<span class="text-warm-gray">${r.total_matches} 场</span>`}
    </div>
  </div>
  ${accuracyBar}
  ${hasResult && r.analysis ? `<div class="text-sm text-warm-gray font-light bg-accent/5 rounded-lg px-4 py-2 border border-accent/15"><span class="text-accent-yellow">自分析:</span> ${r.analysis}</div>` : ''}
  <div class="space-y-1">${matchRows}</div>
  </div>`;
}

function renderReportMatch(m, hasResult) {
  const pick = m.best_pick;
  const playLabels = { SPF: I18n.t('match.spf'), RQ: I18n.t('match.rq'), SCORE: I18n.t('match.score'), GOALS: I18n.t('match.goals'), HALF: I18n.t('match.half') };
  const selLabels = { home: I18n.t('match.homeWin'), draw: I18n.t('match.drawWord'), away: I18n.t('match.awayWin') };
  const outcomeMap = { '3': I18n.t('match.homeWin'), '1': I18n.t('match.drawWord'), '0': I18n.t('match.awayWin') };

  let pickHtml = '';
  if (pick) {
    const plabel = playLabels[pick.play_type] || pick.play_type;
    const slabel = selLabels[pick.selection] || pick.selection;
    const pct = (pick.probability * 100).toFixed(1);
    const barColor = pick.probability >= 0.55 ? 'bg-beige' : pick.probability >= 0.40 ? 'bg-accent-yellow' : 'bg-warm-gray-light';
    pickHtml = `<div class="flex items-center gap-1.5"><span class="text-sm px-1.5 py-0.5 rounded border border-beige/30 text-beige font-light">${plabel}</span><span class="text-base font-medium text-charcoal">${slabel}</span><div class="w-12 h-1 bg-accent/10 rounded-full overflow-hidden"><div class="${barColor} h-full rounded-full" style="width:${pct}%"></div></div><span class="num text-sm ${pick.probability >= 0.55 ? 'text-beige' : 'text-warm-gray'} font-light">${pct}%</span></div>`;
  } else {
    pickHtml = '<span class="text-sm text-warm-gray font-light">无预测</span>';
  }

  let resultHtml = '';
  if (m.actual_outcome) {
    const outcome = outcomeMap[m.actual_outcome] || m.actual_outcome;
    if (m.correct !== null && m.correct !== undefined) {
      resultHtml = m.correct ? '<span class="text-accent-green text-sm">&#10003; 命中</span>' : '<span class="text-accent-red text-sm">&#10007; 未中</span>';
    } else { resultHtml = `<span class="text-warm-gray text-sm font-light">结果: ${outcome}</span>`; }
  }

  const handicapStr = m.handicap ? `(${m.handicap > 0 ? '+' : ''}${m.handicap})` : '';
  return `<div class="flex items-center justify-between py-2 px-3 rounded-lg bg-accent/5 text-base font-light">
    <div class="flex items-center gap-2 min-w-0">
      <span class="text-warm-gray num w-5 text-right">#${m.sequence}</span>
      <span class="text-charcoal truncate">${m.home}${handicapStr} vs ${m.away}</span>
    </div>
    <div class="flex items-center gap-3 flex-shrink-0">
      ${pickHtml}
      ${resultHtml}
    </div>
  </div>`;
}

// ─── 智能串关推荐 ───────────────────────────────
async function showOptimalCombo(issueId) {
    const grid = document.getElementById('matchGrid');
    grid.innerHTML = '<div class="col-span-full text-center py-24 text-warm-gray"><div class="w-10 h-10 rounded-full border-2 border-accent/20 border-t-accent animate-spin mx-auto mb-4"></div><div class="text-base font-light">智能计算中...</div></div>';
    try {
        const res = await fetch('/api/jingcai/issues/' + issueId + '/optimal-combo?top_n=8');
        const data = await res.json();
        renderOptimalCombo(data);
    } catch (e) {
        grid.innerHTML = '<div class="col-span-full text-center py-24 text-warm-gray"><div class="text-base font-light">加载失败</div><div class="text-sm mt-2 text-warm-gray">' + escapeHtml(e.message) + '</div></div>';
    }
}

function renderOptimalCombo(data) {
    const grid = document.getElementById('matchGrid');
    const picks = data.picks || [];
    if (!picks.length) {
        grid.innerHTML = '<div class="col-span-full text-center py-24 text-warm-gray"><div class="text-base font-light">暂无推荐数据</div></div>';
        return;
    }
    let html = '<div class="col-span-full mb-6"><h2 class="font-serif text-2xl font-medium text-charcoal">智能串关推荐 · 最优 8 场</h2><p class="text-sm text-warm-gray mt-1">基于 EV 值排序，数据驱动优选</p></div><div class="col-span-full space-y-3">';
    for (var i = 0; i < picks.length; i++) {
        var p = picks[i];
        html += '<div class="glass p-5 border-l-4 ' + (i < 3 ? 'border-accent-green' : 'border-accent/30') + ' transition-all duration-300">';
        html += '<div class="flex items-center justify-between mb-3">';
        html += '<div class="flex items-center gap-2 text-base font-light text-warm-gray">';
        html += '<span class="num font-medium text-charcoal">#' + (i+1) + '</span>';
        html += '<span>' + escapeHtml(p.home) + ' vs ' + escapeHtml(p.away) + '</span>';
        html += '</div>';
        html += '<div class="text-sm text-warm-gray num">' + fmtBJ(p.kickoff_at) + '</div>';
        html += '</div>';
        html += '<div class="flex items-center justify-between mb-2">';
        html += '<div class="flex items-center gap-3">';
        html += '<span class="px-2 py-1 text-sm rounded bg-accent/10 text-charcoal font-medium">' + p.play_type + '</span>';
        html += '<span class="text-lg font-medium text-beige">' + p.selection_label + '</span>';
        html += '</div>';
        html += '<div class="flex items-center gap-4 text-base num">';
        html += '<span class="text-warm-gray">概率 ' + fmtPct(p.probability) + '</span>';
        html += '<span class="text-warm-gray">赔率 ' + p.odds.toFixed(2) + '</span>';
        html += '<span class="' + (p.ev > 0.1 ? 'text-accent-green' : 'text-warm-gray') + ' font-medium">EV ' + (p.ev > 0 ? '+' : '') + Math.round(p.ev * 100) + '%</span>';
        html += '</div>';
        html += '</div>';
        html += '<div class="text-sm text-warm-gray font-light">' + escapeHtml(p.rationale) + '</div>';
        html += '</div>';
    }
    html += '</div>';
    grid.innerHTML = html;
}

// 优化版：带缓存和加载提示
// 智能推荐缓存
var optimalComboCache = {};

async function showOptimalCombo(issueId) {
    const grid = document.getElementById('matchGrid');
    grid.innerHTML = '<div class="col-span-full text-center py-24 text-warm-gray"><div class="w-10 h-10 rounded-full border-2 border-accent/20 border-t-accent animate-spin mx-auto mb-4"></div><div class="text-base font-light">智能计算中...</div><div class="text-sm mt-2 text-warm-gray">正在分析赛事数据</div></div>';
    
    try {
        // 检查缓存
        if (optimalComboCache[issueId] && optimalComboCache[issueId].expires > Date.now()) {
            renderOptimalCombo(optimalComboCache[issueId].data);
            return;
        }
        
        const startTime = Date.now();
        const res = await fetch('/api/jingcai/issues/' + issueId + '/optimal-combo?top_n=8');
        const data = await res.json();
        const loadTime = Date.now() - startTime;
        
        // 缓存结果（5 分钟）
        optimalComboCache[issueId] = {
            data: data,
            expires: Date.now() + 300000
        };
        
        // 添加加载时间提示
        data.loadTime = loadTime;
        renderOptimalCombo(data);
    } catch (e) {
        grid.innerHTML = '<div class="col-span-full text-center py-24 text-warm-gray"><div class="text-base font-light">加载失败</div><div class="text-sm mt-2 text-warm-gray">' + escapeHtml(e.message) + '</div></div>';
    }
}

function renderOptimalCombo(data) {
    const grid = document.getElementById('matchGrid');
    const picks = data.picks || [];
    
    if (!picks.length) {
        grid.innerHTML = '<div class="col-span-full text-center py-24 text-warm-gray"><div class="text-base font-light">暂无推荐数据</div><div class="text-sm mt-2 text-warm-gray">该期号可能没有足够的比赛数据</div></div>';
        return;
    }
    
    let html = '<div class="col-span-full mb-6">';
    html += '<h2 class="font-serif text-2xl font-medium text-charcoal">智能串关推荐 · 最优 8 场</h2>';
    html += '<p class="text-sm text-warm-gray mt-1">基于 EV 值排序，数据驱动优选';
    if (data.loadTime) {
        html += ' · <span class="num">加载耗时 ' + data.loadTime + 'ms</span>';
    }
    html += '</p></div><div class="col-span-full space-y-3">';
    
    for (var i = 0; i < picks.length; i++) {
        var p = picks[i];
        html += '<div class="glass p-5 border-l-4 ' + (i < 3 ? 'border-accent-green' : 'border-accent/30') + ' transition-all duration-300">';
        html += '<div class="flex items-center justify-between mb-3">';
        html += '<div class="flex items-center gap-2 text-base font-light text-warm-gray">';
        html += '<span class="num font-medium text-charcoal">#' + (i+1) + '</span>';
        html += '<span>' + escapeHtml(p.home) + ' vs ' + escapeHtml(p.away) + '</span>';
        html += '</div>';
        html += '<div class="text-sm text-warm-gray num">' + fmtBJ(p.kickoff_at) + '</div>';
        html += '</div>';
        html += '<div class="flex items-center justify-between mb-2">';
        html += '<div class="flex items-center gap-3">';
        html += '<span class="px-2 py-1 text-sm rounded bg-accent/10 text-charcoal font-medium">' + p.play_type + '</span>';
        html += '<span class="text-lg font-medium text-beige">' + p.selection_label + '</span>';
        html += '</div>';
        html += '<div class="flex items-center gap-4 text-base num">';
        html += '<span class="text-warm-gray">概率 ' + fmtPct(p.probability) + '</span>';
        html += '<span class="text-warm-gray">赔率 ' + p.odds.toFixed(2) + '</span>';
        html += '<span class="' + (p.ev > 0.1 ? 'text-accent-green' : 'text-warm-gray') + ' font-medium">EV ' + (p.ev > 0 ? '+' : '') + Math.round(p.ev * 100) + '%</span>';
        html += '</div>';
        html += '</div>';
        html += '<div class="text-sm text-warm-gray font-light">' + escapeHtml(p.rationale) + '</div>';
        html += '</div>';
    }
    html += '</div>';
    grid.innerHTML = html;
}
