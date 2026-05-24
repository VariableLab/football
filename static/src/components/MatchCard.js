/**
 * MatchCard 响应式组件 (Alpine.js 版)
 */
function MatchCard(match) {
  return {
    match: match,
    homeTeam: null,
    awayTeam: null,
    spf: {},
    topScores: [],
    countdown: '',

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

      this.updateCountdown();
      setInterval(() => this.updateCountdown(), 60000);
    },

    updateCountdown() {
      this.countdown = matchCountdown(this.match.kickoff_at);
    },

    get typeBadgeClass() {
      return this.match.match_type === 'friendly' 
        ? 'border-accent-yellow/30 text-accent-yellow' 
        : 'border-beige/30 text-beige';
    },

    get statusBadgeClass() {
      return this.match.status === 'finished'
        ? 'border-accent/15 text-warm-gray'
        : 'border-accent-cyan/30 text-accent-cyan';
    }
  };
}
