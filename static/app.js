/**
 * WC Analytics — 专业量化终端版 (v5.0)
 * 重构版：采用“数据叙事画廊”架构，实现高密度信号与深度研判的无缝切换
 */

document.addEventListener('alpine:init', () => {
  Alpine.store('app', {
    matches: [],
    teams: [],
    filter: 'upcoming', // 默认切到全部
    user: null,
    loading: false,
    
    // V5 新增：当前选中的比赛及前瞻内容
    selectedId: null,
    preview: null,
    previewLoading: false,
    agentLog: "正在初始化 59 维特征扫描引擎...",

    async init() {
      await I18n.init();
      try {
        // 並行加載基礎數據與用戶信息
        // WCApi.Data.getTeams 已在內部解包 items
        const [teamsArray, me] = await Promise.all([
          WCApi.Data.getTeams(),
          WCApi.Auth.me().catch(() => null)
        ]);
        
        this.teams = teamsArray || [];
        this.user = me;
        
        await this.loadMatches();
        
        // 自动选中第一场有 Edge 的比赛
        if (this.matches && this.matches.length > 0) {
          this.selectMatch(this.matches[0].id);
        }
      } catch (e) {
        console.error('App init failed', e);
        this.agentLog = "🔴 系统初始化异常，请检查后端连接。";
      }
    },

    async loadMatches() {
      this.loading = true;
      this.agentLog = `正在拉取 ${this.filter} 市场快照...`;
      try {
        let resp;
        if (['today', 'tomorrow'].includes(this.filter)) {
          resp = await WCApi.Data.getMatches(undefined, undefined, undefined, this.filter);
        } else if (this.filter === 'upcoming') {
          resp = await WCApi.Data.getMatches('upcoming');
        } else {
          resp = await WCApi.Data.getMatches(this.filter);
        }
        // WCApi.Data.getMatches 已在內部使用 unwrapItems
        this.matches = resp || [];
        this.agentLog = `成功获取 ${this.matches.length} 条实时信号。`;
      } catch (e) {
        console.error('Load matches failed', e);
        this.matches = [];
        this.agentLog = "⚠️ 信号流抓取中断，正在重试...";
      } finally {
        this.loading = false;
      }
    },

    async selectMatch(id) {
      if (this.selectedId === id) return;
      this.selectedId = id;
      this.previewLoading = true;
      this.preview = null;
      
      const m = this.matches.find(m => m.id === id);
      this.agentLog = `正在校准 ${m?.home_team?.name || '未知'} 的 59 维残差特征...`;

      try {
        const data = await WCApi.Content.getPreview(id);
        this.preview = data;
        this.agentLog = `✅ ${m?.home_team?.name} 模型推演完成，偏差值已锁定。`;
      } catch (e) {
        console.error('Preview load failed', e);
        this.agentLog = "❌ 深度特征对齐失败，请检查数据完整性。";
      } finally {
        this.previewLoading = false;
      }
    },

    setFilter(newFilter) {
      if (this.filter === newFilter) return;
      this.filter = newFilter;
      this.loadMatches();
    },

    logout() {
      WCApi.Auth.logout();
      this.user = null;
      window.location.reload();
    }
  });
});

/**
 * 格式化辅助函数
 */
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

function openReport(matchId) {
  window.dispatchEvent(new CustomEvent('open-report-modal', { detail: { matchId } }));
}
