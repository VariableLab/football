(() => {
  // static/src/api_client.ts
  var API_BASE = "";
  function getToken() {
    return localStorage.getItem("wc_token");
  }
  function setToken(token) {
    localStorage.setItem("wc_token", token);
  }
  function removeToken() {
    localStorage.removeItem("wc_token");
  }
  async function apiFetch(path, options = {}) {
    const url = `${API_BASE}${path}`;
    const opts = {
      headers: {
        "Content-Type": "application/json",
        ...options.headers || {}
      },
      ...options
    };
    const token = getToken();
    if (token) {
      opts.headers["Authorization"] = `Bearer ${token}`;
    }
    if (opts.body && typeof opts.body === "object" && !(opts.body instanceof FormData)) {
      opts.body = JSON.stringify(opts.body);
    }
    const res = await fetch(url, opts);
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${res.status}`);
    }
    return res.json();
  }
  function unwrapItems(res) {
    if (res && typeof res === "object" && "items" in res) {
      return res.items;
    }
    return res;
  }
  var WCApi = {
    Auth: {
      isLoggedIn() {
        return !!getToken();
      },
      async register(email, password) {
        const data = await apiFetch("/api/auth/register", {
          method: "POST",
          body: JSON.stringify({ email, password })
        });
        if (data.access_token) setToken(data.access_token);
        return data;
      },
      async login(email, password) {
        const data = await apiFetch("/api/auth/login", {
          method: "POST",
          body: JSON.stringify({ email, password })
        });
        if (data.access_token) setToken(data.access_token);
        return data;
      },
      async me() {
        return apiFetch("/api/auth/me");
      },
      logout() {
        removeToken();
      }
    },
    Data: {
      async getTeams() {
        const res = await apiFetch("/api/teams?limit=500");
        return res.items || res;
      },
      async getMatches(status, group, matchType) {
        const params = new URLSearchParams();
        if (status) params.append("status", status);
        if (group) params.append("group", group);
        if (matchType) params.append("match_type", matchType);
        params.append("limit", "200");
        const res = await apiFetch(`/api/matches?${params.toString()}`);
        return unwrapItems(res);
      },
      async getMatch(matchId) {
        return apiFetch(`/api/matches/${matchId}`);
      }
    },
    Strategy: {
      async getStrategy(matchId, riskTier) {
        const params = new URLSearchParams();
        if (riskTier) params.append("risk_tier", riskTier);
        const qs = params.toString();
        return apiFetch(`/api/matches/${matchId}/strategy${qs ? "?" + qs : ""}`);
      }
    },
    Odds: {
      async getMovement(matchId) {
        return apiFetch(`/api/matches/${matchId}/odds-movement`);
      },
      async getArbitrage() {
        return apiFetch("/api/arbitrage");
      }
    },
    LiveOdds: {
      async getAll() {
        return apiFetch("/api/live-odds");
      },
      async get(matchId) {
        return apiFetch(`/api/live-odds/${matchId}`);
      },
      async start() {
        return apiFetch("/api/live-odds/start", { method: "POST" });
      },
      async stop() {
        return apiFetch("/api/live-odds/stop", { method: "POST" });
      },
      stream(onOddsUpdate, onError) {
        const es = new EventSource(`${API_BASE}/api/live-odds/stream`);
        es.addEventListener("odds_update", (event) => {
          try {
            onOddsUpdate(JSON.parse(event.data));
          } catch {
          }
        });
        if (onError) {
          es.onerror = () => {
            onError(new Error("SSE lost"));
            es.close();
          };
        }
        return es;
      }
    },
    Hedge: {
      async getAlerts() {
        return apiFetch("/api/live-hedge/alerts");
      },
      async addPosition(matchId, selection, odds, stake) {
        const qs = new URLSearchParams({ match_id: String(matchId), selection, odds: String(odds), stake: String(stake) }).toString();
        return apiFetch(`/api/live-hedge/position?${qs}`, { method: "POST" });
      },
      async compute(matchId, selection, odds, stake, fraction) {
        const qs = new URLSearchParams({ selection, odds: String(odds), stake: String(stake), fraction: String(fraction || 1) }).toString();
        return apiFetch(`/api/live-hedge/compute/${matchId}?${qs}`);
      }
    },
    License: {
      async redeem(key) {
        return apiFetch("/api/license/redeem", {
          method: "POST",
          body: JSON.stringify({ key })
        });
      }
    },
    Validation: {
      async getReport(matchType) {
        const params = new URLSearchParams();
        if (matchType) params.append("match_type", matchType);
        const qs = params.toString();
        return apiFetch(`/api/validation${qs ? "?" + qs : ""}`);
      },
      async getCalibration() {
        return apiFetch("/api/validation/calibration");
      },
      async getByPlayType() {
        return apiFetch("/api/validation/by-play-type");
      }
    },
    Jingcai: {
      async listIssues(status) {
        const params = new URLSearchParams();
        if (status) params.append("status", status);
        params.append("limit", "100");
        const res = await apiFetch(`/api/jingcai/issues?${params.toString()}`);
        return res.items || res;
      },
      async getIssue(issueId) {
        return apiFetch(`/api/jingcai/issues/${encodeURIComponent(issueId)}`);
      },
      async getReport() {
        return apiFetch("/api/jingcai/report");
      }
    },
    Feedback: {
      async list(params) {
        const qs = new URLSearchParams(params || {}).toString();
        const res = await apiFetch(`/api/feedback${qs ? "?" + qs : ""}`);
        return res.items || res;
      },
      async create(category, content, matchId, isAnonymous) {
        const token = getToken();
        const headers = { "Content-Type": "application/json" };
        if (token) headers["Authorization"] = `Bearer ${token}`;
        const resp = await fetch(`${API_BASE}/api/feedback`, {
          method: "POST",
          headers,
          body: JSON.stringify({ category, content, match_id: matchId || null, is_anonymous: !!isAnonymous })
        });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error(err.detail || `HTTP ${resp.status}`);
        }
        return resp.json();
      },
      async like(feedbackId) {
        return apiFetch(`/api/feedback/${feedbackId}/like`, { method: "POST" });
      }
    },
    Settings: {
      async get() {
        return apiFetch("/api/settings");
      },
      async update(data) {
        const qs = new URLSearchParams(
          Object.entries(data).filter(([_, v]) => v !== void 0 && v !== null).map(([k, v]) => [k, String(v)])
        ).toString();
        return apiFetch(`/api/settings?${qs}`, { method: "PUT" });
      }
    },
    BetNN: {
      async status() {
        return apiFetch("/api/bet-nn/status");
      },
      async predict(matchId) {
        return apiFetch(`/api/bet-nn/predict/${matchId}`);
      },
      async train() {
        return apiFetch("/api/bet-nn/train", { method: "POST" });
      }
    }
  };
  window.WCApi = WCApi;
})();
//# sourceMappingURL=api_client.js.map
