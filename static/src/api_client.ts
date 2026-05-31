import type { components, paths } from '../api/schema';

type MatchListResponse = components['schemas']['MatchListResponse'];
type StrategyResponse = components['schemas']['StrategyResponse'];
type FeedbackOut = components['schemas']['FeedbackOut'];
type FeedbackListResponse = components['schemas']['FeedbackListResponse'];
type ValidationReport = components['schemas']['ValidationReport'];
type HealthResponse = components['schemas']['HealthResponse'];
type SettingsResponse = components['schemas']['SettingsResponse'];
type NNResponse = components['schemas']['NNResponse'];
type TeamOut = components['schemas']['TeamOut'];
type LicenseRedeemOut = components['schemas']['LicenseRedeemOut'];

interface MatchItem {
  id: number; home_team: TeamOut; away_team: TeamOut;
  kickoff_at: string | null; match_type: string | null;
  status: string; odds_home: number | null; odds_draw: number | null;
  odds_away: number | null; odds_source: string | null;
  odds_degraded: boolean | null; updated_at: string | null;
  predictions: components['schemas']['PredictionOut'][];
  actual_home_goals: number | null; actual_away_goals: number | null;
  actual_outcome: string | null; competition: string | null;
  group: string | null; stage: string | null;
}

// 智能环境感知：确保本地开发、主服务器和 Cloudflare Pages 无缝切换
const getApiBase = () => {
  const host = window.location.hostname;
  // 如果是本地开发或主服务器访问，使用相对路径
  if (host === 'localhost' || host === '127.0.0.1' || host === 'football.nett.to') {
    return '';
  }
  // 否则（如在 Cloudflare Pages 访问），强行指向主服务器 API
  return 'https://football.nett.to';
};

const API_BASE = getApiBase();

function getToken(): string | null {
  return localStorage.getItem('wc_token');
}
function setToken(token: string): void {
  localStorage.setItem('wc_token', token);
}
function removeToken(): void {
  localStorage.removeItem('wc_token');
}

async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const url = `${API_BASE}${path}`;
  const opts: RequestInit & { headers: Record<string, string> } = {
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers as Record<string, string> || {}),
    },
    ...options,
  };

  const token = getToken();
  if (token) {
    opts.headers['Authorization'] = `Bearer ${token}`;
  }

  if (opts.body && typeof opts.body === 'object' && !(opts.body instanceof FormData)) {
    opts.body = JSON.stringify(opts.body);
  }

  const res = await fetch(url, opts);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error((data as { detail?: string }).detail || `HTTP ${res.status}`);
  }
  return res.json();
}

function unwrapItems<T extends { items?: unknown } | unknown[]>(res: T): T extends { items: infer I } ? I : T {
  if (res && typeof res === 'object' && 'items' in res) {
    return (res as { items: I }).items as any;
  }
  return res as any;
}

const WCApi = {
  Auth: {
    isLoggedIn(): boolean {
      return !!getToken();
    },
    async register(email: string, password: string): Promise<{ access_token?: string }> {
      const data = await apiFetch<{ access_token?: string }>('/api/auth/register', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      });
      if (data.access_token) setToken(data.access_token);
      return data;
    },
    async login(email: string, password: string): Promise<{ access_token?: string }> {
      const data = await apiFetch<{ access_token?: string }>('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      });
      if (data.access_token) setToken(data.access_token);
      return data;
    },
    async me(): Promise<{ email: string; is_paid?: boolean; paid_until?: string }> {
      return apiFetch('/api/auth/me');
    },
    logout(): void {
      removeToken();
    },
  },

  Data: {
    async getTeams(): Promise<TeamOut[]> {
      const res = await apiFetch<{ items: TeamOut[] }>('/api/teams?limit=500');
      return res.items || (res as unknown as TeamOut[]);
    },
    async getMatches(status?: string, group?: string, matchType?: string, date?: string): Promise<MatchItem[]> {
      const params = new URLSearchParams();
      if (status) params.append('status', status);
      if (group) params.append('group', group);
      if (matchType) params.append('match_type', matchType);
      if (date) params.append('date', date);
      params.append('limit', '200');
      const res = await apiFetch<MatchListResponse>(`/api/matches?${params.toString()}`);
      return unwrapItems(res) as unknown as MatchItem[];
    },
    async getMatch(matchId: number): Promise<MatchOut> {
      return apiFetch<MatchOut>(`/api/matches/${matchId}`);
    },
  },

  Strategy: {
    async getStrategy(matchId: number, riskTier?: string): Promise<StrategyResponse> {
      const params = new URLSearchParams();
      if (riskTier) params.append('risk_tier', riskTier);
      const qs = params.toString();
      return apiFetch<StrategyResponse>(`/api/matches/${matchId}/strategy${qs ? '?' + qs : ''}`);
    },
  },

  Odds: {
    async getMovement(matchId: number): Promise<unknown> {
      return apiFetch(`/api/matches/${matchId}/odds-movement`);
    },
    async getArbitrage(): Promise<unknown> {
      return apiFetch('/api/arbitrage');
    },
  },

  LiveOdds: {
    async getAll(): Promise<unknown> { return apiFetch('/api/live-odds'); },
    async get(matchId: number): Promise<unknown> { return apiFetch(`/api/live-odds/${matchId}`); },
    async start(): Promise<unknown> { return apiFetch('/api/live-odds/start', { method: 'POST' }); },
    async stop(): Promise<unknown> { return apiFetch('/api/live-odds/stop', { method: 'POST' }); },
    stream(onOddsUpdate: (data: unknown) => void, onError?: (err: Error) => void): EventSource {
      const es = new EventSource(`${API_BASE}/api/live-odds/stream`);
      es.addEventListener('odds_update', (event: Event) => {
        try {
          onOddsUpdate(JSON.parse((event as MessageEvent).data));
        } catch { /* ignore */ }
      });
      if (onError) {
        es.onerror = () => { onError(new Error('SSE lost')); es.close(); };
      }
      return es;
    },
  },

  Hedge: {
    async getAlerts(): Promise<unknown> { return apiFetch('/api/live-hedge/alerts'); },
    async addPosition(matchId: number, selection: string, odds: number, stake: number): Promise<unknown> {
      const qs = new URLSearchParams({ match_id: String(matchId), selection, odds: String(odds), stake: String(stake) }).toString();
      return apiFetch(`/api/live-hedge/position?${qs}`, { method: 'POST' });
    },
    async compute(matchId: number, selection: string, odds: number, stake: number, fraction?: number): Promise<unknown> {
      const qs = new URLSearchParams({ selection, odds: String(odds), stake: String(stake), fraction: String(fraction || 1.0) }).toString();
      return apiFetch(`/api/live-hedge/compute/${matchId}?${qs}`);
    },
  },

  License: {
    async redeem(key: string): Promise<LicenseRedeemOut> {
      return apiFetch<LicenseRedeemOut>('/api/license/redeem', {
        method: 'POST',
        body: JSON.stringify({ key }),
      });
    },
  },

  Validation: {
    async getReport(matchType?: string): Promise<ValidationReport> {
      const params = new URLSearchParams();
      if (matchType) params.append('match_type', matchType);
      const qs = params.toString();
      return apiFetch<ValidationReport>(`/api/validation${qs ? '?' + qs : ''}`);
    },
    async getCalibration(): Promise<unknown> { return apiFetch('/api/validation/calibration'); },
    async getByPlayType(): Promise<unknown> { return apiFetch('/api/validation/by-play-type'); },
  },

  Jingcai: {
    async listIssues(status?: string): Promise<unknown[]> {
      const params = new URLSearchParams();
      if (status) params.append('status', status);
      params.append('limit', '100');
      const res = await apiFetch<{ items: unknown[] }>(`/api/jingcai/issues?${params.toString()}`);
      return res.items || (res as unknown as unknown[]);
    },
    async getIssue(issueId: string): Promise<unknown> {
      return apiFetch(`/api/jingcai/issues/${encodeURIComponent(issueId)}`);
    },
    async getReport(): Promise<{ reports: unknown[] }> {
      return apiFetch('/api/jingcai/report');
    },
  },

  Feedback: {
    async list(params?: Record<string, string>): Promise<FeedbackOut[]> {
      const qs = new URLSearchParams(params || {}).toString();
      const res = await apiFetch<FeedbackListResponse>(`/api/feedback${qs ? '?' + qs : ''}`);
      return res.items || (res as unknown as FeedbackOut[]);
    },
    async create(category: string, content: string, matchId?: number, isAnonymous?: boolean): Promise<unknown> {
      const token = getToken();
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (token) headers['Authorization'] = `Bearer ${token}`;
      const resp = await fetch(`${API_BASE}/api/feedback`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ category, content, match_id: matchId || null, is_anonymous: !!isAnonymous }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error((err as { detail?: string }).detail || `HTTP ${resp.status}`);
      }
      return resp.json();
    },
    async like(feedbackId: number): Promise<unknown> {
      return apiFetch(`/api/feedback/${feedbackId}/like`, { method: 'POST' });
    },
  },

  Settings: {
    async get(): Promise<SettingsResponse> { return apiFetch<SettingsResponse>('/api/settings'); },
    async update(data: Record<string, unknown>): Promise<unknown> {
      const qs = new URLSearchParams(
        Object.entries(data).filter(([_, v]) => v !== undefined && v !== null).map(([k, v]) => [k, String(v)])
      ).toString();
      return apiFetch(`/api/settings?${qs}`, { method: 'PUT' });
    },
  },

  BetNN: {
    async status(): Promise<unknown> { return apiFetch('/api/bet-nn/status'); },
    async predict(matchId: number): Promise<NNResponse> { return apiFetch<NNResponse>(`/api/bet-nn/predict/${matchId}`); },
    async train(): Promise<unknown> { return apiFetch('/api/bet-nn/train', { method: 'POST' }); },
  },
};

declare global {
  interface Window { WCApi: typeof WCApi; }
}
(window as any).WCApi = WCApi;
