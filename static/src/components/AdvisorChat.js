/**
 * AdvisorChat 响应式组件 (Alpine.js 版)
 */
function AdvisorChat() {
  return {
    messages: [],
    input: '',
    loading: false,
    history: [],

    init() {
      // 初始欢迎语
      this.messages.push({
        role: 'assistant',
        content: '你好，我是 ProQuant 首席量化顾问。我仅被授权基于本项目 48 维模型及 ROI 神经网络数据为您提供博弈分析。请问您需要了解哪场赛事的量化 Edge？'
      });

      // 监听全局事件，用于从比赛详情页发起分析
      window.addEventListener('request-advisor-analysis', async (e) => {
        const matchId = e.detail.matchId;
        await this.sendMessage(matchId);
      });
    },

    async sendMessage(matchId = null, command = null) {
      const text = command ? command : (matchId ? '请深度分析本场比赛的量化价值。' : this.input.trim());
      if (!text && !matchId) return;
      if (this.loading) return;

      // 1. 添加用户消息到界面
      this.messages.push({ role: 'user', content: text });
      if (!matchId && !command) this.input = '';
      
      this.loading = true;

      
      try {
        const resp = await fetch('/api/advisor/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ 
            message: text, 
            match_id: matchId || undefined, 
            history: this.history.slice(-6) // 只带最近 3 轮对话，节省上下文
          }),
        });
        
        const data = await resp.json();
        if (resp.ok) {
          this.messages.push({ role: 'assistant', content: data.reply });
          // 更新历史记录（仅内部逻辑使用）
          this.history.push({ role: 'user', content: text });
          this.history.push({ role: 'assistant', content: data.reply });
        } else {
          this.messages.push({ 
            role: 'assistant', 
            content: '抱歉，顾问服务暂时不可用：' + (data.detail || '网络错误') 
          });
        }
      } catch (e) {
        this.messages.push({ role: 'assistant', content: '连接失败，请检查网络。' });
      } finally {
        this.loading = false;
        // 自动滚动到底部
        this.$nextTick(() => {
          const el = document.getElementById('advisorChatMessages');
          if (el) el.scrollTop = el.scrollHeight;
        });
      }
    }
  };
}
