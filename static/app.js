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
    selectedMatch: null, // 存储完整 match 对象
    preview: null,
    previewLoading: false,
    selectedStrategy: null, // 存储匹配的策略信息
    strategyLoading: false,
    bankroll: 1000,          // 默认模拟账户本金
    riskTier: 'balanced',    // 默认风险偏好档位
    agentLog: "正在初始化 59 维特征扫描引擎...",
    
    // 移动端视图控制: 'list' | 'detail'
    mobileView: 'list',

    get stakeAmount() {
      if (!this.selectedStrategy || !this.selectedStrategy.is_recommended) return 0.0;
      return this.bankroll * (this.selectedStrategy.stake_pct || 0.0);
    },

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
          resp = await WCApi.Data.getMatches('future');
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
      if (this.selectedId === id) {
        this.mobileView = 'detail';
        return;
      }
      this.selectedId = id;
      this.selectedMatch = this.matches.find(m => m.id === id);
      this.mobileView = 'detail';
      
      this.previewLoading = true;
      this.preview = null;
      this.selectedStrategy = null;
      this.strategyLoading = true;
      
      const m = this.selectedMatch;
      this.agentLog = `正在校准 ${m?.home_team?.name || '未知'} 的 59 维残差特征与对冲对策...`;

      try {
        const previewData = await WCApi.Content.getPreview(id);
        this.preview = previewData;
      } catch (err) {
        console.error('Preview load failed', err);
      }

      try {
        const strategyData = await WCApi.Strategy.getStrategy(id, this.riskTier);
        if (strategyData && strategyData.strategies && strategyData.strategies.length > 0) {
          this.selectedStrategy = strategyData.strategies[0];
        } else {
          this.selectedStrategy = {
            is_recommended: false,
            edge: 0.0,
            ev: 0.0,
            kelly_fraction: 0.0,
            stake_pct: 0.0,
            play_label: '胜平负 (SPF)',
            selection_label: '暂无推荐',
            odds: 0.0,
            rationale: '本场赛事不符合价值洼地过滤门槛 (EV <= 0 或 Edge <= 0)。已自动拦截规避风险。',
            risk_level: 'low'
          };
        }
        this.agentLog = `✅ ${m?.home_team?.name || '模型'} 推演完成，量化仓位已就绪。`;
      } catch (err) {
        console.error('Strategy load failed', err);
        // 如果是 403 Forbidden，显示卡密解锁提示而非全局报错崩溃
        if (err.status === 403 || (err.message && err.message.includes('403')) || String(err).includes('403')) {
          this.selectedStrategy = {
            is_recommended: false,
            edge: 0.0,
            ev: 0.0,
            kelly_fraction: 0.0,
            stake_pct: 0.0,
            play_label: '胜平负 (SPF)',
            selection_label: '待解锁',
            odds: 0.0,
            rationale: '🔒 本场赛事处于赛前分析锁定期，量化对策仅对付费订阅会员公开。请点击下方“绑定/激活”按钮解锁。',
            risk_level: 'low'
          };
          this.agentLog = "🔒 深度量化仓位已被锁定，需要绑定/激活卡密。";
        } else {
          this.agentLog = "❌ 深度特征或量化对策对齐失败，请检查网络。";
        }
      } finally {
        this.previewLoading = false;
        this.strategyLoading = false;
      }
    },

    async changeRiskTier() {
      if (!this.selectedId) return;
      this.strategyLoading = true;
      this.agentLog = `正在重算 ${this.selectedMatch?.home_team?.name} 在 [${this.riskTier}] 风控偏好下的凯利比例...`;
      try {
        const strategyData = await WCApi.Strategy.getStrategy(this.selectedId, this.riskTier);
        if (strategyData && strategyData.strategies && strategyData.strategies.length > 0) {
          this.selectedStrategy = strategyData.strategies[0];
        } else {
          this.selectedStrategy = {
            is_recommended: false,
            edge: 0.0,
            ev: 0.0,
            kelly_fraction: 0.0,
            stake_pct: 0.0,
            play_label: '胜平负 (SPF)',
            selection_label: '暂无推荐',
            odds: 0.0,
            rationale: '本场赛事在当前风控档位下未满足期望价值推荐门槛。',
            risk_level: 'low'
          };
        }
        this.agentLog = `✅ 风控档位已切换至 [${this.riskTier}]，最新仓位已锁定。`;
      } catch (e) {
        console.error('Change risk tier failed', e);
        this.agentLog = "❌ 凯利对策重算失败，请检查网络。";
      } finally {
        this.strategyLoading = false;
      }
    },

    recalcStake() {
      if (typeof this.bankroll !== 'number' || this.bankroll < 0) {
        this.bankroll = 0;
      }
    },

    backToList() {
      this.mobileView = 'list';
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
