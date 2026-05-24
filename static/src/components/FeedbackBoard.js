/**
 * FeedbackBoard 响应式组件 (Alpine.js 版)
 */
function FeedbackBoard() {
  return {
    items: [],
    loading: true,
    category: 'all',
    content: '',
    submitting: false,

    async init() {
      // 监听类别切换
      window.addEventListener('feedback-category-changed', (e) => {
        this.category = e.detail.category;
        this.fetchFeedback();
      });
      
      // 监听刷新请求
      window.addEventListener('feedback-refresh', () => this.fetchFeedback());

      await this.fetchFeedback();
    },

    async fetchFeedback() {
      this.loading = true;
      try {
        const params = {};
        if (this.category !== 'all') params.category = this.category;
        const data = await WCApi.Feedback.list(params);
        this.items = data.items || [];
      } catch (e) {
        console.error('Failed to fetch feedback', e);
      } finally {
        this.loading = false;
      }
    },

    async submitFeedback() {
      if (this.content.length < 5) {
        alert(I18n.t('feedback.tooShort') || '内容至少5个字符');
        return;
      }
      this.submitting = true;
      try {
        await WCApi.Feedback.create({
          category: this.category === 'all' ? 'discussion' : this.category,
          content: this.content,
          is_anonymous: false
        });
        this.content = '';
        await this.fetchFeedback();
      } catch (e) {
        alert('提交失败');
      } finally {
        this.submitting = false;
      }
    },

    async toggleLike(id) {
      try {
        const res = await WCApi.Feedback.like(id);
        const item = this.items.find(i => i.id === id);
        if (item) {
          item.likes = res.likes;
        }
      } catch (e) {
        if (e.status === 401) {
          alert('请先登录');
          window.location.hash = '#auth';
        }
      }
    },

    fmtDate(iso) {
      if (!iso) return '';
      const d = new Date(iso);
      return `${d.getMonth()+1}/${d.getDate()} ${d.getHours()}:${d.getMinutes().toString().padStart(2, '0')}`;
    }
  };
}
