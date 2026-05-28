/**
 * MatchCard 响应式组件 (Alpine.js 版 - 视觉进化版)
 */
function MatchCard(match) {
  return {
    match: match,
    homeTeam: null,
    awayTeam: null,
    spf: {},
    topScores: [],
    countdown: '',
    highestEdge: null,
    aiSnippet: '',

    init() {
      // 初始化数据
      this.homeTeam = AppState.teams.find(t => t.id === this.match.home_team_id) || 
                      { name: this.match.home_team?.name || I18n.t('match.defaultHome'), flag: '', fifa_rank: '-' };
      this.awayTeam = AppState.teams.find(t => t.id === this.match.away_team_id) || 
                      { name: this.match.away_team?.name || I18n.t('match.defaultAway'), flag: '', fifa_rank: '-' };
      
      const spfPred = this.match.predictions?.find(p => p.play_type === 'SPF');
      this.spf = spfPred?.probabilities || {};
      
      const scorePred = this.match.predictions?.find(p => p.play_type === 'SCORE');
      const scoreProbs = scorePred?.probabilities || {};
      this.topScores = Object.entries(scoreProbs)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3)
        .map(([score, prob]) => ({ score, prob }));

      this.calculateEdge();
      this.updateCountdown();
      setInterval(() => this.updateCountdown(), 60000);
    },

    calculateEdge() {
      // 计算最大错价和一句话点评
      if (!this.match.odds_home || !this.spf.home) {
        this.aiSnippet = "等待赔率数据以进行错价分析";
        return;
      }
      
      const impH = 1 / this.match.odds_home;
      const impD = 1 / this.match.odds_draw;
      const impA = 1 / this.match.odds_away;
      const totalImp = impH + impD + impA;
      
      const edgeH = this.spf.home - (impH / totalImp);
      const edgeD = this.spf.draw - (impD / totalImp);
      const edgeA = this.spf.away - (impA / totalImp);

      const edges = [
        { label: '主胜', val: edgeH, prob: this.spf.home, outcome: 'home' },
        { label: '平局', val: edgeD, prob: this.spf.draw, outcome: 'draw' },
        { label: '客胜', val: edgeA, prob: this.spf.away, outcome: 'away' }
      ].sort((a, b) => b.val - a.val);

      this.highestEdge = edges[0];

      if (this.highestEdge.val > 0.10) {
        this.aiSnippet = `🔥 发现极端错价: 市场严重低估了${this.highestEdge.label}可能`;
      } else if (this.highestEdge.val > 0.05) {
        this.aiSnippet = `💡 模型倾向: ${this.highestEdge.label}具有量化投资价值`;
      } else {
        const topProb = [
          { label: '主胜', val: this.spf.home },
          { label: '平局', val: this.spf.draw },
          { label: '客胜', val: this.spf.away }
        ].sort((a,b) => b.val - a.val)[0];
        
        if (topProb.val > 0.6) {
          this.aiSnippet = `⚖️ 实力碾压: 极大概率${topProb.label}`;
        } else {
          this.aiSnippet = `🛡️ 势均力敌: 市场定价与模型预测基本一致`;
        }
      }
    },

    updateCountdown() {
      this.countdown = matchCountdown(this.match.kickoff_at);
    },

    get typeBadgeClass() {
      return this.match.match_type === 'friendly' 
        ? 'border-accent-yellow/30 text-accent-yellow bg-accent-yellow/5' 
        : 'border-accent/30 text-accent bg-accent/5';
    },

    get statusBadgeClass() {
      return this.match.status === 'finished'
        ? 'border-white/10 text-white/40 bg-white/5'
        : 'border-accent-cyan/30 text-accent-cyan bg-accent-cyan/5';
    }
  };
}
