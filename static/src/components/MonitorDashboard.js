/**
 * MonitorDashboard 响应式组件 (Alpine.js 版)
 */
function MonitorDashboard() {
  return {
    reports: [],
    history: [],
    loading: true,
    latestAccuracy: 0,
    latestBrier: 0,
    modelDim: 0,
    lrEnabled: false,

    async init() {
      await this.fetchStats();
      await this.fetchReports();
      this.loading = false;
    },

    async fetchStats() {
      try {
        const resp = await fetch('/api/monitor/accuracy-stats');
        const data = await resp.json();
        if (data.overall_history) {
          this.history = data.overall_history;
          this.latestAccuracy = data.latest_accuracy;
          this.latestBrier = data.latest_brier;
          this.modelDim = data.model_dimension;
          this.lrEnabled = data.is_lr_enabled;
        }
      } catch (e) {
        console.error('Failed to fetch stats', e);
      }
    },

    async fetchReports() {
      try {
        // 尝试获取报告（可能需要登录）
        const token = localStorage.getItem('token');
        if (!token) return;

        const resp = await fetch('/api/monitor/reports?n=7', {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (resp.ok) {
          this.reports = await resp.json();
        }
      } catch (e) {
        console.error('Failed to fetch reports', e);
      }
    },

    getBrierColor(brier) {
      if (brier < 0.18) return 'text-accent-green';
      if (brier < 0.21) return 'text-accent-yellow';
      return 'text-accent-red';
    },

    getAccuracyColor(acc) {
      if (acc > 0.55) return 'text-accent-green';
      if (acc > 0.48) return 'text-accent-yellow';
      return 'text-accent-red';
    }
  };
}
