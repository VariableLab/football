/**
 * AdvisorChat 响应式组件 (Alpine.js 版)
 */
function AdvisorChat() {
  return {
    messages: [],
    input: '',
    loading: false,
    history: [],

    async init() {
      // 1. 初始欢迎
      this.messages.push({
        role: 'assistant',
        content: '我是 ProQuant 首席量化代理。正在为你扫描今日全站数据资产...'
      });

      // 2. 主动获取 VidIQ 风格的早报
      try {
        const resp = await fetch('/api/advisor/briefing');
        const data = await resp.json();
        // 自动将早报作为 AI 的第一条深度回复
        this.messages.push({
          role: 'assistant',
          content: data.briefing
        });
      } catch (e) {
        console.error('Briefing failed:', e);
      }

      // 监听全局事件，用于从比赛详情页发起分析
      window.addEventListener('request-advisor-analysis', async (e) => {
        const matchId = e.detail.matchId;
        await this.sendMessage(matchId);
      });
    },

    async sendMessage(matchId = null, command = null) {
      let text = command ? command : (matchId ? '请深度分析本场比赛的量化价值。' : this.input.trim());
      if (!text && !matchId) return;
      if (this.loading) return;

      // Command Palette Logic
      if (text.startsWith('/trace')) {
        text = "请针对该场比赛展示完整的逻辑链条 (Logic Trace)，解释概率生成的每一个步骤。";
      } else if (text.startsWith('/kelly')) {
        text = "请基于凯利公式 (Kelly Criterion) 计算本场比赛的最优仓位建议，并说明理由。";
      }

      // 1. 添加用户消息到界面
      this.messages.push({ role: 'user', content: text });
      if (!matchId && !command) this.input = '';
      
      this.loading = true;
      const aiMsgIndex = this.messages.push({ role: 'assistant', content: '' }) - 1;

      try {
        const token = localStorage.getItem('token');
        const response = await fetch('/api/advisor/chat', {
          method: 'POST',
          headers: { 
            'Content-Type': 'application/json',
            ...(token ? { 'Authorization': `Bearer ${token}` } : {})
          },
          body: JSON.stringify({ 
            message: text, 
            match_id: matchId || undefined, 
            history: this.history.slice(-6) 
          }),
        });
        
        if (!response.ok) throw new Error('Network error');

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        this.loading = false;

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;

          const chunk = decoder.decode(value);
          const lines = chunk.split('\n');
          
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.substring(6));
                if (data.content) {
                  this.messages[aiMsgIndex].content += data.content;
                } else if (data.error) {
                  this.messages[aiMsgIndex].content = data.error;
                }
                
                // 自动滚动到底部
                this.$nextTick(() => {
                  const el = document.getElementById('advisorChatMessages');
                  if (el) el.scrollTop = el.scrollHeight;
                });
              } catch (e) {
                console.error('SSE Parse Error', e);
              }
            }
          }
        }
        
        // 更新历史记录
        this.history.push({ role: 'user', content: text });
        this.history.push({ role: 'assistant', content: this.messages[aiMsgIndex].content });

      } catch (e) {
        this.messages[aiMsgIndex].content = '连接失败，请检查网络。';
      } finally {
        this.loading = false;
        this.$nextTick(() => {
          const el = document.getElementById('advisorChatMessages');
          if (el) el.scrollTop = el.scrollHeight;
        });
      }
    }
  };
}
