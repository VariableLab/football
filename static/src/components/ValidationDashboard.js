/**
 * ValidationDashboard 响应式组件 (Alpine.js 版)
 */
function ValidationDashboard() {
  return {
    report: null,
    loading: true,
    error: false,

    async init() {
      // 监听全局事件来更新数据
      window.addEventListener('validation-updated', (e) => {
        this.report = e.detail.report;
        this.loading = false;
      });
    },

    get summary() {
      return this.report?.summary || {};
    },

    get matches() {
      return this.report?.matches || [];
    },

    get outcomeMap() {
      return { 
        home: I18n.t('match.homeWin'), 
        draw: I18n.t('match.draw'), 
        away: I18n.t('match.awayWin') 
      };
    },

    getAccuracyClass(acc) {
      if (acc >= 0.6) return 'text-accent-green';
      if (acc >= 0.5) return 'text-accent-yellow';
      return 'text-accent-red';
    }
  };
}
