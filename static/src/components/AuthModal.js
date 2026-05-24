/**
 * AuthModal 响应式组件 (Alpine.js 版)
 */
function AuthModal() {
  return {
    email: '',
    password: '',
    error: '',
    loading: false,
    show: false,

    init() {
      window.addEventListener('open-login-modal', () => {
        this.show = true;
        this.error = '';
      });
      window.addEventListener('close-login-modal', () => {
        this.show = false;
      });
    },

    async handleLogin() {
      this.loading = true;
      this.error = '';
      try {
        const res = await WCApi.Auth.login(this.email, this.password);
        localStorage.setItem('token', res.access_token);
        this.show = false;
        // 触发全局登录成功事件
        window.dispatchEvent(new CustomEvent('auth-success'));
        // 刷新页面或重新加载数据
        location.reload();
      } catch (e) {
        this.error = e.detail || '登录失败，请检查账号密码';
      } finally {
        this.loading = false;
      }
    },

    async handleRegister() {
      this.loading = true;
      this.error = '';
      try {
        const res = await WCApi.Auth.register(this.email, this.password);
        localStorage.setItem('token', res.access_token);
        this.show = false;
        window.dispatchEvent(new CustomEvent('auth-success'));
        location.reload();
      } catch (e) {
        this.error = e.detail || '注册失败';
      } finally {
        this.loading = false;
      }
    }
  };
}
