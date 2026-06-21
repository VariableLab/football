/**
 * WC Analytics — Frontend App
 * 简洁量化终端: 列表 + 详情双栏布局
 */

document.addEventListener('alpine:init', () => {
  Alpine.store('app', {
    matches: [],
    filter: 'upcoming',
    loading: false,
    selectedId: null,
    selectedMatch: null,
    preview: null,
    previewLoading: false,
    selectedStrategy: null,
    strategyLoading: false,
    bankroll: 1000,
    riskTier: 'balanced',
    mobileView: 'list',  // 'list' | 'detail'

    // 预测数据
    spfPrediction: null,
    rqPrediction: null,
    scorePrediction: null,
    goalsPrediction: null,
    halfPrediction: null,

    get stakeAmount() {
      if (!this.selectedStrategy || !this.selectedStrategy.is_recommended) return 0;
      return this.bankroll * (this.selectedStrategy.stake_pct || 0);
    },

    get predictions() {
      // 从 selectedMatch 获取预测数据
      if (!this.selectedMatch) return [];
      return this.selectedMatch.predictions || [];
    },

    async init() {
      try {
        await I18n.init();
        const [me] = await Promise.all([
          WCApi.Auth.me().catch(() => null),
        ]);
        await this.loadMatches();
      } catch (e) {
        console.error('App init failed', e);
      }
    },

    async loadMatches() {
      this.loading = true;
      try {
        let resp;
        if (['today', 'tomorrow'].includes(this.filter)) {
          resp = await WCApi.Data.getMatches(undefined, undefined, undefined, this.filter);
        } else if (this.filter === 'upcoming') {
          resp = await WCApi.Data.getMatches('future');
        } else {
          resp = await WCApi.Data.getMatches(this.filter);
        }
        this.matches = resp || [];
      } catch (e) {
        console.error('Load matches failed', e);
        this.matches = [];
      } finally {
        this.loading = false;
      }
    },

    async selectMatch(id) {
      if (this.selectedId === id) return;
      this.selectedId = id;
      this.mobileView = 'detail';
      this.previewLoading = true;
      this.strategyLoading = true;

      // Reset predictions
      this.spfPrediction = null;
      this.rqPrediction = null;
      this.scorePrediction = null;
      this.goalsPrediction = null;
      this.halfPrediction = null;
      this.preview = null;
      this.selectedStrategy = null;

      const m = this.matches.find(x => x.id === id);
      this.selectedMatch = m;

      // Load match details (predictions)
      try {
        const matchDetails = await WCApi.Data.getMatch(id);
        this.selectedMatch = matchDetails;
        if (matchDetails?.predictions) {
          for (const p of matchDetails.predictions) {
            if (p.play_type === 'SPF') this.spfPrediction = p;
            else if (p.play_type === 'RQ') this.rqPrediction = p;
            else if (p.play_type === 'SCORE') this.scorePrediction = p;
            else if (p.play_type === 'GOALS') this.goalsPrediction = p;
            else if (p.play_type === 'HALF') this.halfPrediction = p;
          }
        }
      } catch (err) {
        console.error('Match details load failed', err);
      }

      // Load strategy
      try {
        const strategyData = await WCApi.Strategy.getStrategy(id, this.riskTier);
        if (strategyData?.strategies?.length > 0) {
          this.selectedStrategy = strategyData.strategies[0];
        } else {
          this.selectedStrategy = {
            is_recommended: false,
            edge: 0, ev: 0, kelly_fraction: 0, stake_pct: 0,
            play_label: '胜平负 (SPF)',
            selection_label: '暂无推荐',
            odds: 0,
            rationale: '本场赛事不符合价值门槛。',
            risk_level: 'low',
          };
        }
      } catch (err) {
        console.error('Strategy load failed', err);
        this.selectedStrategy = {
          is_recommended: false, edge: 0, ev: 0, kelly_fraction: 0, stake_pct: 0,
          play_label: '胜平负 (SPF)',
          selection_label: '暂无推荐',
          odds: 0,
          rationale: '策略计算失败，请检查网络。',
          risk_level: 'low',
        };
      } finally {
        this.previewLoading = false;
        this.strategyLoading = false;
      }
    },

    async changeRiskTier() {
      if (!this.selectedId) return;
      this.strategyLoading = true;
      try {
        const strategyData = await WCApi.Strategy.getStrategy(this.selectedId, this.riskTier);
        if (strategyData?.strategies?.length > 0) {
          this.selectedStrategy = strategyData.strategies[0];
        } else {
          this.selectedStrategy = {
            is_recommended: false, edge: 0, ev: 0, kelly_fraction: 0, stake_pct: 0,
            play_label: '胜平负 (SPF)',
            selection_label: '暂无推荐',
            odds: 0,
            rationale: '当前风控档位下无推荐。',
            risk_level: 'low',
          };
        }
      } catch (e) {
        console.error('Change risk tier failed', e);
      } finally {
        this.strategyLoading = false;
      }
    },

    recalcStake() {
      if (typeof this.bankroll !== 'number' || this.bankroll < 0) {
        this.bankroll = 0;
      }
    },

    setFilter(f) {
      if (this.filter === f) return;
      this.filter = f;
      this.loadMatches();
    },

    logout() {
      WCApi.Auth.logout();
      window.location.reload();
    }
  });
});

// ─── Helper Functions ───

function fmtBJ(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  const offset = 8 * 60;
  const bj = new Date(d.getTime() + (offset + d.getTimezoneOffset()) * 60000);
  return (bj.getMonth() + 1) + '/' + bj.getDate() + ' ' +
         String(bj.getHours()).padStart(2, '0') + ':' +
         String(bj.getMinutes()).padStart(2, '0');
}

function fmtPct(v) {
  if (v === undefined || v === null) return '0%';
  return (v * 100).toFixed(1) + '%';
}

function getEdge(match) {
  if (!match?.predictions) return null;
  const spf = match.predictions.find(p => p.play_type === 'SPF');
  if (!spf?.probabilities) return null;
  // Edge = model_prob - market_implied
  // Simple: use the probability difference from fair
  const maxProb = Math.max(...Object.values(spf.probabilities));
  return maxProb - 0.33;
}

function labelSPF(k) {
  const map = { home: '主胜', draw: '平局', away: '客胜' };
  return map[k] || k;
}

function labelRQ(k) {
  const map = { home: '让球主胜', draw: '让球平', away: '让球客胜' };
  return map[k] || k;
}

// Expose helpers globally for Alpine templates
window.fmtBJ = fmtBJ;
window.fmtPct = fmtPct;
window.getEdge = getEdge;
window.labelSPF = labelSPF;
window.labelRQ = labelRQ;
