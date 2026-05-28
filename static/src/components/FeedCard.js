function FeedCard(match, index) {
  return {
    match: match,
    index: index,
    homeTeam: null,
    awayTeam: null,
    isPaywalled: false,
    predictionDir: '分析中...',
    rationale: '正在从量化引擎提取数据...',

    async init() {
      this.homeTeam = AppState.teams.find(t => t.id === this.match.home_team_id) || { name: this.match.home_team?.name || '主队' };
      this.awayTeam = AppState.teams.find(t => t.id === this.match.away_team_id) || { name: this.match.away_team?.name || '客队' };
      
      // Paywall logic: 只有前两场比赛是免费的，后面的全部加上遮罩（引流私域）
      this.isPaywalled = this.index >= 2;

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
            const topStrategy = data.strategies[0];
            this.predictionDir = topStrategy.selection_label;
            this.rationale = topStrategy.rationale || "根据多维数据分析，本场存在显著量化优势。";
          } else {
            this.predictionDir = "平局 / 观望";
            this.rationale = "当前数据模型显示双方势均力敌，盘口未出现明显的错价漏洞，建议谨慎投资。";
          }
        } else {
            this.predictionDir = "暂无数据";
            this.rationale = "正在等待比赛前瞻数据的进一步录入。";
        }
      } catch (e) {
        this.predictionDir = "获取失败";
        this.rationale = "无法连接到服务器引擎。";
      }
    }
  };
}
