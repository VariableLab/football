/**
 * SettingsModal 响应式组件 (Alpine.js 版)
 */
function SettingsModal() {
  return {
    show: false,
    loading: false,
    settings: {
      default_risk_tier: 'balanced',
      notifications_enabled: true,
      display_currency: 'CNY'
    },

    init() {
      window.addEventListener('open-settings-modal', async () => {
        this.show = true;
        await this.loadSettings();
      });
      window.addEventListener('close-settings-modal', () => {
        this.show = false;
      });
    },

    async loadSettings() {
      const token = localStorage.getItem('token');
      if (!token) return;
      this.loading = true;
      try {
        const res = await fetch('/api/settings', {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (res.ok) {
          this.settings = await res.json();
        }
      } catch (e) {
        console.error('Failed to load settings', e);
      } finally {
        this.loading = false;
      }
    },

    async updateSettings() {
      const token = localStorage.getItem('token');
      if (!token) return;
      try {
        await fetch('/api/settings', {
          method: 'POST',
          headers: { 
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json'
          },
          body: JSON.stringify(this.settings)
        });
        this.show = false;
      } catch (e) {
        alert('保存设置失败');
      }
    }
  };
}
