/**
 * WC Analytics — 主应用逻辑 (v3.0 Lightweight Feed 版)
 */

const AppState = {
  matches: [],
  teams: [],
  page: 1,
  pageSize: 20, // Load more matches at once for the feed
  user: null,
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
    
    // 2. 加载赛事 Feed
    loadMatchFeed();
    
  } catch (e) {
    console.error('App init failed', e);
  }
}

// ─── 比赛数据加载 ───
async function loadMatchFeed() {
  try {
    // 获取竞彩在售/即将开始的比赛
    const matches = await WCApi.Data.getMatches('jingcai');
    AppState.matches = matches || [];
    
    // 触发渲染
    renderFeed();
  } catch (e) {
    console.error('Load matches failed', e);
    window.dispatchEvent(new CustomEvent('matches-updated', { detail: { matches: [] } }));
  }
}

function renderFeed() {
  const matches = AppState.matches;
  window.dispatchEvent(new CustomEvent('matches-updated', { 
    detail: { matches: matches } 
  }));
}

// ─── 工具函数 ───
function fmtBJ(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  const offset = 8 * 60;
  const bj = new Date(d.getTime() + (offset + d.getTimezoneOffset()) * 60000);
  return `${bj.getMonth()+1}/${bj.getDate()} ${bj.getHours().toString().padStart(2, '0')}:${bj.getMinutes().toString().padStart(2, '0')}`;
}

function fmtPct(v) { return v ? (v * 100).toFixed(1) + '%' : '-'; }

// 启动
document.addEventListener('DOMContentLoaded', initApp);
