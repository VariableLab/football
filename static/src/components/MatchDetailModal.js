/**
 * MatchDetailModal 响应式组件 (Alpine.js 版)
 */
function MatchDetailModal() {
  return {
    match: null,
    strategyData: null,
    loading: true,
    tab: 'spf',
    locked: true,
    error: null,
    riskTier: 'balanced',
    oddsMovement: null,

    async init() {
      window.addEventListener('open-match-modal', async (e) => {
        this.reset();
        await this.loadData(e.detail.matchId);
      });
    },

    reset() {
      this.match = null;
      this.strategyData = null;
      this.loading = true;
      this.tab = 'spf';
      this.locked = true;
      this.error = null;
      this.oddsMovement = null;
    },

    async loadData(matchId) {
      try {
        // 1. 基本信息
        this.match = await WCApi.Data.getMatch(matchId);
        
        // 2. 预测策略 (需要鉴权/付费逻辑)
        try {
          this.strategyData = await WCApi.Strategy.getStrategy(matchId, this.riskTier);
          this.locked = false;
        } catch (e) {
          console.warn('Strategy load failed:', e);
          if (e.message.includes('403') || e.message.includes('paid')) {
            this.locked = true;
          } else {
            this.error = '无法加载预测策略';
          }
        }

        // 3. 赔率走势
        try {
          this.oddsMovement = await WCApi.Odds.getMovement(matchId);
        } catch (e) { /* ignore */ }

      } catch (e) {
        console.error('Match detail load error:', e);
        this.error = '比赛详情加载失败';
      } finally {
        this.loading = false;
      }
    },


    async changeRiskTier(tier) {
      this.riskTier = tier;
      if (this.match) {
        this.loading = true;
        try {
          this.strategyData = await WCApi.Data.getStrategy(this.match.id, tier);
        } catch (e) { /* handle */ }
        this.loading = false;
      }
    },

    get homeTeam() {
      return this.match?.home_team || {};
    },

    get awayTeam() {
      return this.match?.away_team || {};
    },

    get spfPred() {
      return this.match?.predictions?.find(p => p.play_type === 'SPF');
    },

    get rqPred() {
      return this.match?.predictions?.find(p => p.play_type === 'RQ');
    },

    get scorePred() {
      return this.match?.predictions?.find(p => p.play_type === 'SCORE');
    },

    get goalsPred() {
      return this.match?.predictions?.find(p => p.play_type === 'GOALS');
    },

    get halfPred() {
      return this.match?.predictions?.find(p => p.play_type === 'HALF');
    },

    fmtPct(v) { return v ? (v * 100).toFixed(1) + '%' : '-'; },
    
    getOutcomeLabel(outcome) {
      const map = { home: '主胜', draw: '平局', away: '客胜' };
      return map[outcome] || outcome;
    }
  };
}
