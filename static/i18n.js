(function() {
  var STORAGE_KEY = 'wc_lang';
  var FALLBACK_LANG = 'zh';
  var SUPPORTED = ['zh','en','fr','es','de','it'];
  var cache = {};
  var currentLang = FALLBACK_LANG;

  // ── 内置中文翻译（XHR 加载前即可用）──
  cache['zh'] = {
    "nav.disclaimer": "学术研究工具 · 非投注建议",
    "nav.login": "登录",
    "nav.logout": "退出",
    "nav.settings": "设置",
    "nav.redeem": "授权",
    "nav.proPredict": "概率估算仅供参考",
    "nav.proLabel": "PRO",
    "nav.freeLabel": "Free",
    "hero.title": "足球概率校准研究框架",
    "hero.subtitle": "市场数据追踪 · 概率校准模型 · 概率校准输出",
    "hero.star": "Star on GitHub",
    "hero.badges.matches": "31K+ matches",
    "hero.badges.teams": "462 teams",
    "hero.badges.predictions": "157K outputs",
    "hero.badges.fusion": "3-layer fusion",
    "hero.badges.opensource": "Open source",
    "about.title": "关于本项目",
    "about.p1": "我们是一群关注体育数据分析的研究者。开发这个系统是因为发现许多“预测模型”缺乏可复现性和严格的概率校准。WC Analytics 是我们在足球概率建模严谨性上的尝试——全开源、赛前快照锁定、完全可复现。",
    "about.stack": "技术栈:",
    "tab.matches": "样本赛事",
    "tab.validation": "验证",
    "tab.report": "报告",
    "tab.ai": "模型解释",
    "tab.feedback": "留言",
    "filter.jingcai": "研究样本",
    "filter.all": "全部",
    "filter.today": "今日",
    "filter.tomorrow": "明日",
    "filter.world_cup": "杯赛样本",
    "filter.friendly": "热身赛",
    "loading": "加载中...",
    "loading.failed": "加载失败",
    "no.data": "暂无样本数据",
    "no.matches": "暂无%s样本数据",
    "no.data.sync": "系统每日 09:00/15:00 自动同步",
    "data.refresh": "刚刚更新",
    "data.minutesAgo": "%d分钟前",
    "data.hoursAgo": "%d小时前",
    "data.locked": "快照已锁定",
    "data.onSale": "样本录入中",
    "data.drawn": "已完赛",
    "data.verified": "已验证",
    "data.closed": "快照锁定",
    "match.spf": "胜平负",
    "match.spfTab": "胜平负 · 模型概率 + 市场偏差",
    "match.homeWin": "主胜",
    "match.draw": "平",
    "match.awayWin": "客胜",
    "match.drawWord": "平局",
    "match.rq": "让球",
    "match.rqWin": "让胜",
    "match.rqDraw": "让平",
    "match.rqLose": "让负",
    "match.rqTab": "让球 · 模型概率%s",
    "match.score": "比分",
    "match.scoreTab": "比分%s",
    "match.goals": "总进球",
    "match.goalsTab": "总进球%s",
    "match.half": "半全场",
    "match.halfTab": "半全场%s",
    "match.handicap": "让%s",
    "match.prediction": "估算结果",
    "match.oddsSource": "市场快照",
    "match.oddsMissing": "快照缺失",
    "match.synthetic": "合成",
    "match.real": "真实",
    "match.vsText": "VS",
    "match.statusFinished": "已结束",
    "match.statusUnstarted": "未开赛",
    "match.typeFriendly": "热身赛",
    "match.typeWorldCup": "杯赛",
    "match.finishedTag": "赛后验证 · 概率输出已开放",
    "match.predCorrect": "校准命中",
    "match.predWrong": "校准偏差",
    "match.predVsActual": "估算: %s · 实际: %s",
    "match.strategy": "模型推演策略",
    "match.kellyDesc": "概率校准 + 统计置信度",
    "match.model": "模型",
    "match.lockedAt": "锁定 %s",
    "match.elapsed": "Elo",
    "match.aiAnalyze": "模型解释",
    "match.lockOverlay": "输入授权码查看研究策略",
    "match.issueLabel": "第%s批次",
    "match.matchCount": "%d场",
    "match.closedIssues": "已锁定批次 (%d)",
    "match.view": "查看",
    "match.predictFail": "概率数据未加载",
    "match.optimalCombo": "校准摘要",
    "match.optimalTitle": "核心样本汇总 · 关键分析 %d 场",
    "match.optimalDesc": "基于 Brier Score 优化，数据驱动校准",
    "match.optimalLoading": "模型推演中...",
    "match.optimalAnalyzing": "正在分析模型指标",
    "match.optimalNoData": "暂无摘要数据",
    "match.optimalNoDataDetail": "该批次可能没有足够的样本数据",
    "match.loadTime": "加载耗时 %dms",
    "match.correctHit": "命中",
    "match.correctMiss": "偏差",
    "match.resultPrefix": "结果: %s",
    "match.resultSPFHits": "SPF: %d/%d",
    "match.resultR9Hits": "R9: %d/9",
    "match.selfAnalysis": "自分析:",
    "match.infoReference": "本批次已锁定，数据仅供模型验证参考",
    "match.countdown": "待定",
    "match.noPrediction": "无输出",
    "report.title": "研究报告",
    "report.empty": "暂无批次数据",
    "report.loading": "加载中...",
    "report.accuracyLabel": "命中率",
    "report.totalMatches": "%d 场",
    "validation.title": "模型验证",
    "validation.empty": "暂无已验证样本",
    "validation.auto": "比赛结束后自动更新验证指标",
    "validation.directionAccuracy": "方向准确率",
    "validation.brierScore": "Brier Score",
    "validation.validatedCount": "已验证样本",
    "validation.avgMaxProb": "平均最高概率",
    "validation.detail": "逐场验证明细",
    "validation.systemError": "系统状态异常",
    "validation.loadFailed": "验证数据加载失败",
    "feedback.title": "研究反馈",
    "feedback.all": "全部",
    "feedback.discussion": "研究讨论",
    "feedback.suggestion": "体验建议",
    "feedback.bug": "数据问题",
    "feedback.data": "模型问题",
    "feedback.placeholder": "留下你的研究反馈...（至少5个字）",
    "feedback.minLength": "至少输入5个字",
    "feedback.anonymous": "匿名",
    "feedback.submit": "发送",
    "feedback.sendFailed": "发送失败: %s",
    "feedback.empty": "暂无反馈",
    "feedback.like": "赞",
    "ai.title": "模型研究助手",
    "ai.greeting": "你好！我可以帮助你理解 WC Analytics 如何基于历史比赛数据生成概率估算、校准指标和模型验证报告。请注意，我仅解释模型逻辑，不提供任何结果建议。",
    "ai.suggestions": "试试问：<br>• \"模型如何生成校准概率？\"<br>• \"Brier Score 是如何计算的？\"<br>• \"主胜概率最高的几场样本及其推演逻辑？\"",
    "ai.placeholder": "询问模型逻辑...",
    "ai.analyzing": "分析中...",
    "ai.send": "发送",
    "ai.unavailable": "抱歉，模型助手暂时不可用。%s",
    "ai.networkError": "网络错误，请稍后重试。",
    "ai.requestAnalyze": "请解释这场比赛的模型推演逻辑。",
    "ai.analysisBtn": "逻辑解释",
    "modal.login": "登录",
    "modal.email": "邮箱",
    "modal.password": "密码",
    "modal.loginBtn": "登录",
    "modal.registerBtn": "注册",
    "modal.loginFailed": "登录失败",
    "modal.registerFailed": "注册失败",
    "modal.redeemTitle": "授权码",
    "modal.redeemBtn": "授权",
    "modal.redeemKey": "WC26-XXXX-XXXX-XXXX-XXXX",
    "modal.redeemSuccess": "授权成功！",
    "modal.redeemFailed": "授权失败",
    "modal.settingsTitle": "设置",
    "modal.loginFirst": "请先登录以保存研究偏好",
    "modal.settingsFailed": "加载设置失败",
    "settings.riskTier": "模型偏好",
    "settings.conservative": "稳健型",
    "settings.conservativeDesc": "侧重高概率样本",
    "settings.balanced": "均衡型",
    "settings.balancedDesc": "标准校准权重",
    "settings.aggressive": "进取型",
    "settings.aggressiveDesc": "侧重高偏差样本",
    "settings.speculative": "探索型",
    "settings.speculativeDesc": "实验性模型参数",
    "settings.showEV": "显示市场偏差",
    "settings.showProbability": "显示校准概率",
    "settings.notifyOdds": "市场变动通知",
    "settings.notifyMatch": "快照同步提醒",
    "settings.saveFailed": "设置失败: %s",
    "nn.title": "残差神经网络",
    "nn.desc": "神经网络修正参考",
    "nn.estimate": "神经网络修正: %s (%d%%)",
    "nn.notTrained": "神经网络模型更新中",
    "strategy.low": "低偏差",
    "strategy.medium": "中偏差",
    "strategy.high": "高偏差",
    "strategy.odds": "市场快照",
    "strategy.probability": "校准概率",
    "strategy.position": "权重",
    "score.prefix": "比分样本",
    "odds.label": "市场快照",
    "probability.label": "校准概率",
    "ev.label": "偏差",
    "countdown.daysHours": "%d天%d小时",
    "countdown.hoursMins": "%d小时%d分",
    "countdown.mins": "%d分钟",
    "countdown.opened": "数据已锁定",
    "countdown.label": "%s",
    "footer.tagline": "市场数据追踪 · 概率校准模型 · 概率校准输出",
    "footer.links": "链接",
    "footer.disclaimer": "免责声明",
    "footer.privacy": "隐私政策",
    "footer.terms": "用户协议",
    "footer.statement": "声明",
    "footer.statementText": "概率估算仅用于模型研究验证，不代表实际结果。历史回测指标不保证未来表现。",
    "footer.disclaimerText": "⚠️ 免责声明：本项目为学术研究工具，输出结果为数学概率校准值，不构成任何投注建议。请遵守所在地法律法规，理性看待体育竞赛。",
    "footer.copyright": "© 2026 WC Analytics",
    "page.title": "WC Analytics — 开源足球概率校准框架",
    "page.description": "开源的 3 层融合足球比赛概率建模系统，覆盖 31K+ 场次和 462 支球队。学术研究，赛前快照，完全可复现。",
    "strategy.playLabel": "%s · %s",
    "match.rqTabOdds": "让球 · 模型概率 + 市场偏差",
    "match.rqTabDist": "让球 · 模型概率分布",
    "match.scoreTabEV": "比分 + 市场偏差",
    "match.scoreTabProb": "比分概率分布",
    "match.goalsTabEV": "总进球 + 市场偏差",
    "match.goalsTabProb": "总进球概率分布",
    "match.halfTabEV": "半全场 + 市场偏差",
    "match.halfTabProb": "半全场概率分布",
    "match.verifyDone": "赛后验证 · 概率输出已开放",
    "match.sequence": "批次 %s",
    "match.defaultHome": "主队",
    "match.defaultAway": "客队",
    "match.rankPrefix": "基准 #%s",
    "match.issuePrefix": "批次 ",
    "match.issueSuffix": "",
    "match.aiRecommend": "校准摘要",
    "match.oddsLabel": "市场快照",
    "match.scoreLabel": "比分样本",
    "validation.validated": "已验证样本",
    "validation.hitRate": "命中率",
    "validation.autoUpdate": "赛后自动更新指标",
    "loading.calculating": "模型推演中",
    "loading.analyzing": "正在分析模型指标",
    "loading.default": "加载中",
    "nav.logic": "逻辑推演",
    "nav.auth": "账户",
    "terminal.title": "概率校准终端",
    "terminal.subtitle": "对标历史样本 · 神经网络多维推演 · 实时概率校准",
    "terminal.status": "系统状态",
    "terminal.engine_active": "48-特征扫描引擎运行中",
    "tab.jingcai": "🏆 研究样本 (Batch)",
    "tab.today": "今日样本",
    "tab.tomorrow": "明日样本",
    "loading.sync": "正在同步市场快照数据...",
    "match.spf_prob": "胜平负校准概率 (SPF)",
    "match.top_scores": "概率比分样本",
    "match.goals_trend": "进球概率分布",
    "match.read_report": "阅读模型解释报告",
    "report.briefing": "Model Briefing",
    "report.elo_diff": "Elo 分差",
    "report.edge": "模型偏差",
    "report.confidence": "置信度",
    "report.generating": "正在生成模型解释...",
    "report.warning": "⚠️ 模型声明：本报告由 StackingNet v4.5 神经网络自动生成，基于 48 个历史特征变量及即时市场偏差。评估结果仅供数学参考，不作为任何决策依据。",
    "report.close": "关闭报告",
    "poster.verdict": "Calibration Report",
    "poster.source": "WC Analytics Intelligence",
    "poster.save": "保存为图片分发",
    "guide.step1_title": "01. 市场快照捕获",
    "guide.step1_desc": "实时同步全球 3.1 万场历史赛事及即时市场快照。",
    "guide.step2_title": "02. 神经网络扫描",
    "guide.step2_desc": "通过 StackingNet v4.5 进行 48 个特征变量的深度交叉推演。",
    "guide.step3_title": "03. 概率校准输出",
    "guide.step3_desc": "寻找市场快照与模型估算概率之间的数学偏差。"
};

  cache['en'] = {
    "nav.disclaimer": "Research Tool · Not Betting Advice",
    "nav.logic": "Logic",
    "nav.auth": "Auth",
    "hero.title": "WC Analytics",
    "hero.subtitle": "Open-source football probability calibration for researchers.",
    "terminal.title": "Calibration Terminal",
    "terminal.subtitle": "Historical Reference · Multi-Dimensional NN Inference · Real-time Calibration",
    "terminal.status": "System Status",
    "terminal.engine_active": "48-Feature Engine Active",
    "tab.jingcai": "🏆 Research Samples",
    "tab.today": "Today's Samples",
    "tab.tomorrow": "Tomorrow",
    "tab.matches": "Samples",
    "tab.validation": "Validation",
    "tab.report": "Report",
    "tab.ai": "Inference",
    "filter.jingcai": "Research Batch",
    "loading.sync": "Syncing market snapshots...",
    "match.spf_prob": "Win/Draw/Loss Prob (SPF)",
    "match.top_scores": "Likely Score Samples",
    "match.goals_trend": "Goals Distribution",
    "match.read_report": "Read Model Inference Report",
    "report.briefing": "Model Briefing",
    "report.elo_diff": "Elo Gap",
    "report.edge": "Model Bias",
    "report.confidence": "Confidence",
    "report.generating": "Generating Report...",
    "report.warning": "⚠️ Disclaimer: This report is generated by StackingNet v4.5 based on 48 historical features and real-time market bias. Results are for mathematical reference only.",
    "report.close": "Close Report",
    "poster.verdict": "Calibration Report",
    "poster.source": "WC Analytics Intelligence",
    "poster.save": "Save as Image",
    "guide.step1_title": "01. Snapshot Capture",
    "guide.step1_desc": "Real-time sync of 31k+ matches and live market prices.",
    "guide.step2_title": "02. Neural Scan",
    "guide.step2_desc": "48-feature cross-inference using StackingNet v4.5.",
    "guide.step3_title": "03. Probability Calibration",
    "guide.step3_desc": "Identifying mathematical bias between market price and model probability."
};

  function detectLang() {
    var saved = localStorage.getItem(STORAGE_KEY);
    if (saved && SUPPORTED.indexOf(saved) >= 0) return saved;
    var nav = (navigator.language || '').split('-')[0];
    if (SUPPORTED.indexOf(nav) >= 0) return nav;
    return FALLBACK_LANG;
  }

  function loadLang(lang, cb) {
    if (cache[lang]) { if (cb) cb(cache[lang]); return; }
    var xhr = new XMLHttpRequest();
    xhr.open('GET', '/static/locales/' + lang + '.json?v=20260520', true);
    xhr.onload = function() {
      if (xhr.status === 200) {
        try {
          cache[lang] = JSON.parse(xhr.responseText);
          if (cb) cb(cache[lang]);
        } catch(e) { fallback(cb); }
      } else { fallback(cb); }
    };
    xhr.onerror = function() { fallback(cb); };
    xhr.send();
  }

  function fallback(cb) {
    if (cb) cb(cache[FALLBACK_LANG]);
  }

  function t(key) {
    var data = cache[currentLang] || cache[FALLBACK_LANG];
    if (!data) return key;
    var val = data[key];
    if (val === undefined) {
      val = cache[FALLBACK_LANG] ? cache[FALLBACK_LANG][key] : undefined;
      if (val === undefined) return key;
    }
    if (arguments.length <= 1) return val;
    var args = Array.prototype.slice.call(arguments, 1);
    return val.replace(/%d/g, function() { var a = args.shift(); return a !== undefined ? a : '%d'; })
              .replace(/%s/g, function() { var a = args.shift(); return a !== undefined ? a : '%s'; });
  }

  function applyDataI18n() {
    var els = document.querySelectorAll('[data-i18n]');
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var key = el.getAttribute('data-i18n');
      var attr = el.getAttribute('data-i18n-attr');
      var text = t(key);
      if (attr) {
        if (attr === 'placeholder') el.placeholder = text;
        else el.setAttribute(attr, text);
      } else {
        el.innerHTML = text;
      }
    }
  }

  function setLang(lang) {
    if (SUPPORTED.indexOf(lang) < 0) return;
    currentLang = lang;
    localStorage.setItem(STORAGE_KEY, lang);
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : lang;
    if (cache[lang]) {
      applyDataI18n();
      var evt = document.createEvent('CustomEvent');
      evt.initCustomEvent('i18n:change', true, true, { lang: lang });
      window.dispatchEvent(evt);
    } else {
      loadLang(lang, function() {
        applyDataI18n();
        var evt = document.createEvent('CustomEvent');
        evt.initCustomEvent('i18n:change', true, true, { lang: lang });
        window.dispatchEvent(evt);
      });
    }
  }

  function getLang() { return currentLang; }

  function init(cb) {
    currentLang = detectLang();
    document.documentElement.lang = currentLang === 'zh' ? 'zh-CN' : currentLang;
    if (currentLang === FALLBACK_LANG) {
      applyDataI18n();
      if (cb) cb();
      return;
    }
    loadLang(currentLang, function() {
      applyDataI18n();
      if (cb) cb();
    });
  }

  window.I18n = { t: t, setLang: setLang, getLang: getLang, init: init, applyDataI18n: applyDataI18n, SUPPORTED: SUPPORTED };
  init();
})();
