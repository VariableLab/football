function ProMatchCard(match, index) {
  return {
    match: match,
    homeTeam: null,
    awayTeam: null,
    topScores: [],
    goalsProb: { mid: 0, over: 0 },
    quant: {
      aiProb: { home: 33, draw: 33, away: 33 },
      edge: 0
    },

    async init() {
      this.homeTeam = AppState.teams.find(t => t.id === this.match.home_team_id) || { name: this.match.home_team?.name || '主队', elo: 1500 };
      this.awayTeam = AppState.teams.find(t => t.id === this.match.away_team_id) || { name: this.match.away_team?.name || '客队', elo: 1500 };
      
      const spf = this.match.predictions?.find(p => p.play_type === 'SPF');
      if (spf) {
        this.quant.aiProb = {
          home: (spf.probabilities.home * 100).toFixed(0),
          draw: (spf.probabilities.draw * 100).toFixed(0),
          away: (spf.probabilities.away * 100).toFixed(0)
        };
      }

      const score = this.match.predictions?.find(p => p.play_type === 'SCORE');
      if (score) {
        this.topScores = Object.entries(score.probabilities)
          .sort((a,b) => b[1]-a[1])
          .slice(0, 3)
          .map(([score, prob]) => ({ score, prob }));
      }

      const goals = this.match.predictions?.find(p => p.play_type === 'GOALS');
      if (goals) {
        const p2 = goals.probabilities['2'] || 0;
        const p3 = goals.probabilities['3'] || 0;
        this.goalsProb.mid = p2 + p3;
        
        let over25 = 0;
        for (let key in goals.probabilities) {
          if (parseInt(key) >= 3 || key === '7+') over25 += goals.probabilities[key];
        }
        this.goalsProb.over = over25;
      }

      // Calculate Edge if odds exist
      if (spf && this.match.odds_home) {
        const impH = 1 / this.match.odds_home;
        this.quant.edge = spf.probabilities.home - impH;
      }
    }
  };
}

function ReportModal() {
  return {
    show: false,
    loading: true,
    matchId: null,
    matchName: '',
    reportContent: '',
    verdict: '',
    league: '',
    eloDiff: '-',
    edge: '-',
    confidence: '-',

    init() {
      window.addEventListener('open-report-modal', async (e) => {
        const { matchId } = e.detail;
        this.matchId = matchId;
        this.show = true;
        this.loading = true;
        document.getElementById('reportModal').classList.remove('hidden');

        try {
          const resp = await fetch(`/api/advisor/report/${matchId}`, { method: 'POST' });
          const data = await resp.json();
          this.reportContent = data.content;

          // 解析结论印章（从报告中提取胜/平/负）
          const verdictMatch = data.content.match(/预测方向[：:]\s*(胜|平|负)/);
          this.verdict = verdictMatch ? verdictMatch[1] : '待定';

          // Fetch additional quant data
          const stratResp = await fetch(`/api/matches/${matchId}/strategy`);
          const stratData = await stratResp.json();
          this.matchName = stratData.strategies[0]?.strategy_name || '比赛研判';
          this.edge = (stratData.strategies[0]?.edge * 100).toFixed(1) + '%';
          this.confidence = stratData.confidence || 'Normal';
          this.eloDiff = e.detail.eloDiff || '-';

          const matchData = AppState.matches.find(m => m.id === matchId);
          this.league = matchData?.competition || 'QUANT LAB';

        } catch (e) {
          this.reportContent = "报告生成失败，请检查系统状态。";
        } finally {
          this.loading = false;
        }
      });
    },

    openPoster() {
      // 深度解析 AI 报告内容，拆分出进攻、防守、战意等维度
      let cleanContent = this.reportContent.replace(/\*/g, '');
      const segments = [];
      
      // 提取“进攻”维度
      const offenseMatch = cleanContent.match(/(?:进攻|攻击)[维度端]*[：: ]*([\s\S]*?)(?=\n\s*\n|【|\[|\n[^\n]*(?:防守|战意|结论))/i);
      if (offenseMatch) segments.push({ title: "进攻推演", content: offenseMatch[1].trim().substring(0, 120) + "..." });

      // 提取“防守”维度
      const defenseMatch = cleanContent.match(/防守[维度端]*[：: ]*([\s\S]*?)(?=\n\s*\n|【|\[|\n[^\n]*(?:战意|结论))/i);
      if (defenseMatch) segments.push({ title: "防守评估", content: defenseMatch[1].trim().substring(0, 120) + "..." });

      // 提取“战意”维度
      const motivationMatch = cleanContent.match(/战意[维度端]*[：: ]*([\s\S]*?)(?=\n\s*\n|【|\[|\n[^\n]*(?:结论|预测))/i);
      if (motivationMatch) segments.push({ title: "战意画像", content: motivationMatch[1].trim().substring(0, 120) + "..." });

      // 兜底摘要：如果没拆出来，则取前 200 字
      if (segments.length === 0) {
        segments.push({ title: "精算师核心逻辑", content: cleanContent.substring(0, 200).trim() + "..." });
      }

      const matchData = AppState.matches.find(m => m.id === this.matchId);

      window.dispatchEvent(new CustomEvent('open-poster-modal', { 
        detail: { 
          matchId: this.matchId,
          matchName: this.matchName,
          verdict: this.verdict,
          rationaleItems: segments,
          league: this.league,
          kickoffAt: matchData?.kickoff_at,
          aiProb: this.quant.aiProb,
          edge: this.edge
        } 
      }));
    },

    close() {
      this.show = false;
      document.getElementById('reportModal').classList.add('hidden');
    }
  };
}

function PosterModal() {
  return {
    show: false,
    matchId: '',
    matchName: '',
    verdict: '',
    rationaleItems: [],
    league: '',
    kickoffAt: null,
    aiProb: { home: 0, draw: 0, away: 0 },
    edge: '0%',

    init() {
      window.addEventListener('open-poster-modal', (e) => {
        const d = e.detail;
        this.matchId = d.matchId;
        this.matchName = d.matchName;
        this.verdict = d.verdict;
        this.rationaleItems = d.rationaleItems;
        this.league = d.league;
        this.kickoffAt = d.kickoffAt;
        this.aiProb = d.aiProb;
        this.edge = d.edge;
        this.show = true;
        document.getElementById('posterModal').classList.remove('hidden');
      });
    },

    async downloadPoster() {
      const canvas = await html2canvas(document.getElementById('posterCanvas'), {
        backgroundColor: '#FFFFFF',
        scale: 3, 
        useCORS: true
      });
      const link = document.createElement('a');
      link.download = `ProQuant-Dossier-${this.matchId}.png`;
      link.href = canvas.toDataURL('image/png', 1.0);
      link.click();
    },

    close() {
      this.show = false;
      document.getElementById('posterModal').classList.add('hidden');
    }
  };
}

// 全局触发函数
function openReport(matchId) {
  window.dispatchEvent(new CustomEvent('open-report-modal', { detail: { matchId } }));
}
