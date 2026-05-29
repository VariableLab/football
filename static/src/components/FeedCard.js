function FeedCard(match, index) {
  return {
    match: match,
    index: index,
    homeTeam: null,
    awayTeam: null,
    isPaywalled: false,
    expanded: false,
    predictionDir: '分析中...',
    rationale: '正在从量化引擎提取数据...',
    
    // 核心博弈数据
    quant: {
      eloDiff: 0,
      edge: 0,
      confidence: 'Normal',
      aiProb: { home: 33, draw: 33, away: 33 },
      bookieProb: { home: 33, draw: 33, away: 33 },
      anomalyIndex: 0
    },

    async init() {
      this.homeTeam = AppState.teams.find(t => t.id === this.match.home_team_id) || { name: this.match.home_team?.name || '主队', elo: 1500 };
      this.awayTeam = AppState.teams.find(t => t.id === this.match.away_team_id) || { name: this.match.away_team?.name || '客队', elo: 1500 };
      
      this.quant.eloDiff = (this.homeTeam.elo || 1500) - (this.awayTeam.elo || 1500);

      // Testing Phase: Disable Paywall to view all truth
      this.isPaywalled = false;

      // 计算庄家概率 (Implied Probabilities)
      if (this.match.odds_home && this.match.odds_draw && this.match.odds_away) {
        const sum = (1/this.match.odds_home) + (1/this.match.odds_draw) + (1/this.match.odds_away);
        this.quant.bookieProb = {
          home: ((1/this.match.odds_home) / sum * 100).toFixed(1),
          draw: ((1/this.match.odds_draw) / sum * 100).toFixed(1),
          away: ((1/this.match.odds_away) / sum * 100).toFixed(1)
        };
      }

      if (!this.isPaywalled) {
        await this.loadStrategy();
      }
    },

    async loadStrategy() {
      try {
        const resp = await fetch(`/api/matches/${this.match.id}/strategy?risk_tier=balanced`);
        if (resp.ok) {
          const data = await resp.json();
          if (data.strategies && data.strategies.length > 0) {
            const top = data.strategies[0];
            this.predictionDir = top.selection_label;
            this.rationale = top.rationale || "根据48维特征扫描，本场存在显著量化优势。";
            this.quant.edge = top.edge;
            this.quant.confidence = top.confidence === 'high' ? 'High' : 'Normal';
            
            // 获取 AI 的原始概率用于对比
            const spf = data.predictions?.find(p => p.play_type === 'SPF');
            if (spf) {
              this.quant.aiProb = {
                home: (spf.probabilities.home * 100).toFixed(1),
                draw: (spf.probabilities.draw * 100).toFixed(1),
                away: (spf.probabilities.away * 100).toFixed(1)
              };
            }
          }
        }
      } catch (e) {
        console.error('Quant load error', e);
      }
    },

    toggle() {
      if (this.isPaywalled) return;
      this.expanded = !this.expanded;
    }
  };
}
