/**
 * RedeemModal 响应式组件 (Alpine.js 版)
 */
function RedeemModal() {
  return {
    key: '',
    error: '',
    success: '',
    loading: false,
    show: false,

    init() {
      window.addEventListener('open-redeem-modal', () => {
        this.show = true;
        this.error = '';
        this.success = '';
        this.key = '';
      });
      window.addEventListener('close-redeem-modal', () => {
        this.show = false;
      });
    },

    async handleRedeem() {
      if (!this.key.trim()) return;
      this.loading = true;
      this.error = '';
      this.success = '';
      try {
        const res = await WCApi.Auth.redeem(this.key.trim());
        this.success = res.message || '卡密兑换成功，会员权益已激活';
        // 延迟关闭并刷新
        setTimeout(() => {
          this.show = false;
          location.reload();
        }, 1500);
      } catch (e) {
        this.error = e.detail || '兑换失败，请检查卡密是否正确或已被使用';
      } finally {
        this.loading = false;
      }
    }
  };
}
