/**
 * CopilotBubble - Floating AI Assistant
 */
function CopilotBubble() {
  return {
    show: false,
    loading: false,
    content: '',
    matchId: null,
    position: { x: 0, y: 0 },

    init() {
      window.addEventListener('open-copilot', async (e) => {
        const { matchId, event } = e.detail;
        this.matchId = matchId;
        this.show = true;
        this.loading = true;
        this.content = '';
        
        // Calculate position
        this.position = {
          x: Math.min(event.clientX, window.innerWidth - 350),
          y: Math.min(event.clientY, window.innerHeight - 400)
        };

        await this.fetchAnalysis();
      });

      // Close on escape
      window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') this.show = false;
      });
    },

    async fetchAnalysis() {
      try {
        const token = localStorage.getItem('token');
        const response = await fetch('/api/advisor/chat', {
          method: 'POST',
          headers: { 
            'Content-Type': 'application/json',
            ...(token ? { 'Authorization': `Bearer ${token}` } : {})
          },
          body: JSON.stringify({ 
            message: "请对本场比赛进行极简量化研判，重点指出错价机会。",
            match_id: this.matchId 
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
                  this.content += data.content;
                } else if (data.error) {
                  this.content = data.error;
                }
              } catch (e) {
                console.error('SSE Parse Error', e);
              }
            }
          }
        }
      } catch (e) {
        this.content = "暂时无法连接到量化专家。";
        this.loading = false;
      }
    },

    close() {
      this.show = false;
    }
  };
}
