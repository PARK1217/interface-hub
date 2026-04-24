import axios from 'axios';

export const api = axios.create({
  baseURL: '/api',
  timeout: 15000,
});

// 모든 요청에 JWT 자동 첨부 (있으면)
api.interceptors.request.use((cfg) => {
  const t = localStorage.getItem('noahub_token');
  if (t) {
    cfg.headers = cfg.headers ?? {};
    cfg.headers.Authorization = `Bearer ${t}`;
  }
  return cfg;
});

// 401 응답 시 자동 로그아웃 + 로그인 페이지 이동.
// (auth/login 자체의 401/423 은 폼 에러로 표시해야 하므로 제외)
api.interceptors.response.use(
  (r) => r,
  (err) => {
    const status = err?.response?.status;
    const url = err?.config?.url ?? '';
    const isLoginCall = url.includes('/auth/login');
    if (status === 401 && !isLoginCall) {
      localStorage.removeItem('noahub_token');
      localStorage.removeItem('noahub_user');
      localStorage.removeItem('noahub_expires');
      if (location.pathname !== '/login') {
        location.href = '/login?reason=expired';
      }
    }
    return Promise.reject(err);
  },
);

export interface InterfaceItem {
  id: number;
  name: string;
  description?: string | null;
  organization?: string | null;
  protocol: 'REST' | 'SOAP' | 'FTP' | 'MQ' | 'BATCH';
  endpoint: string;
  method: string;
  headers?: Record<string, string> | null;
  request_template?: Record<string, unknown> | null;
  auth_type: 'NONE' | 'BASIC' | 'API_KEY' | 'OAUTH2' | 'BEARER';
  schedule_cron?: string | null;
  enabled: boolean;
  has_secret?: boolean;
  response_ms_threshold?: number | null;
  failure_rate_threshold?: number | null;
  created_at: string;
  updated_at: string;
  deleted_at?: string | null;
}

export interface CallLogItem {
  id: number;
  interface_id: number;
  status: string;
  http_status: number | null;
  duration_ms: number;
  error_message: string | null;
  error_type?: string | null;
  error_trace?: string | null;
  triggered_by: string;
  called_at: string;
  request?: Record<string, unknown> | null;
  response?: Record<string, unknown> | null;
  parent_log_id?: number | null;
  retry_count?: number;
  is_reprocessed?: boolean;
}

export interface BulkRetryRequest {
  interface_id?: number | null;
  status?: string | null;
  since?: string | null;
  until?: string | null;
  only_failed?: boolean;
  skip_already_reprocessed?: boolean;
  max_count?: number;
}

export interface BulkRetryResponse {
  submitted: number;
  skipped: number;
  new_log_ids: number[];
}

export interface IncidentItem {
  id: number;
  interface_id: number;
  type: string;
  severity: string;
  summary: string;
  root_cause: string | null;
  resolution: string | null;
  detected_at: string;
  resolved_at: string | null;
  related_log_count?: number;
}

export interface SlaReportRow {
  interface_id: number;
  interface_name: string;
  uptime_pct: number;
  avg_response_ms: number;
  target_uptime: number;
  target_response_ms: number;
  meets_uptime: boolean;
  meets_response: boolean;
  deleted_at?: string | null;
}

export const Interfaces = {
  list: (params: Record<string, unknown> = {}) => api.get<InterfaceItem[]>('/interfaces', { params }),
  create: (payload: Partial<InterfaceItem> & { auth_secret?: string }) =>
    api.post<InterfaceItem>('/interfaces', payload),
  update: (id: number, payload: Partial<InterfaceItem> & { auth_secret?: string; secret_change_reason?: string }) =>
    api.patch<InterfaceItem>(`/interfaces/${id}`, payload),
  remove: (id: number) => api.delete(`/interfaces/${id}`),
  restore: (id: number) => api.post<InterfaceItem>(`/interfaces/${id}/restore`),
  execute: (id: number) => api.post<CallLogItem>(`/interfaces/${id}/execute`),
  cronPreview: (expression: string, count = 3) =>
    api.get<{ valid: boolean; error?: string; next_runs?: string[] }>(
      '/interfaces/cron-preview',
      { params: { expression, count } },
    ),
  revealSecret: (id: number, reason: string) =>
    api.post<{ interface_id: number; interface_name: string; auth_type: string; secret: string }>(
      `/interfaces/${id}/reveal-secret`,
      { reason },
    ),
};

export interface HeatmapCell {
  hour: number;
  count: number;
  failure_rate: number;
}

export interface HeatmapRow {
  interface_id: number;
  interface_name: string;
  protocol: string;
  organization: string | null;
  cells: HeatmapCell[];
  deleted_at?: string | null;
}

export const CallLogs = {
  search: (params: Record<string, unknown> = {}) => api.get<CallLogItem[]>('/call-logs', { params }),
  stats: (params: Record<string, unknown> = {}) => api.get('/call-logs/stats', { params }),
  timeseries: (params: Record<string, unknown> = {}) => api.get('/call-logs/timeseries', { params }),
  heatmap: (params: Record<string, unknown> = {}) =>
    api.get<HeatmapRow[]>('/call-logs/heatmap', { params }),
  retry: (id: number) => api.post<CallLogItem>(`/call-logs/${id}/retry`),
  bulkRetry: (payload: BulkRetryRequest) =>
    api.post<BulkRetryResponse>('/call-logs/bulk-retry', payload),
  chain: (id: number) => api.get<CallLogItem[]>(`/call-logs/${id}/chain`),
};

export const Incidents = {
  list: (params: Record<string, unknown> = {}) => api.get<IncidentItem[]>('/incidents', { params }),
  resolve: (id: number, resolution?: string) =>
    api.post<IncidentItem>(`/incidents/${id}/resolve`, null, { params: { resolution } }),
  relatedLogs: (id: number, limit = 200) =>
    api.get<CallLogItem[]>(`/incidents/${id}/related-logs`, { params: { limit } }),
  retryRelated: (id: number, mode: 'latest' | 'all' = 'latest') =>
    api.post<BulkRetryResponse>(`/incidents/${id}/retry-related`, { mode }),
};

export interface SlaTrendPoint {
  period: string;
  interface_id: number;
  interface_name: string;
  uptime_pct: number;
  avg_response_ms: number;
  target_uptime: number;
  target_response_ms: number;
  meets_uptime: boolean;
  meets_response: boolean;
  total_calls: number;
  deleted_at?: string | null;
}

export interface SlaCalendarCell {
  date: string;
  interface_id: number;
  interface_name: string;
  uptime_pct: number;
  avg_response_ms: number;
  target_uptime: number;
  target_response_ms: number;
  meets: boolean;
  total_calls: number;
  deleted_at?: string | null;
}

export const Sla = {
  report: (days = 30) => api.get<SlaReportRow[]>('/sla/report', { params: { days } }),
  upsertTarget: (payload: { interface_id: number; uptime_target: number; response_ms_target: number }) =>
    api.post('/sla/targets', payload),
  trend: (bucket: 'month' | 'quarter' = 'month', months = 6) =>
    api.get<SlaTrendPoint[]>('/sla/trend', { params: { bucket, months } }),
  calendar: (days = 30) =>
    api.get<SlaCalendarCell[]>('/sla/calendar', { params: { days } }),
  exportXlsxUrl: (days = 30) => `/api/sla/export.xlsx?days=${days}`,
};

export const AI = {
  ask: (question: string, top_k = 3) => api.post('/ai/ask', { question, top_k }),
  anomaly: (interfaceId: number) => api.get(`/ai/anomaly/${interfaceId}`),
  status: () => api.get<{ configured: boolean; provider: string; model: string | null }>('/ai/status'),
};

export interface PercentileRow {
  interface_id: number;
  interface_name: string;
  protocol: string;
  organization: string | null;
  total_calls: number;
  failure_count: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  max_ms: number;
  avg_ms: number;
  throughput_per_min: number;
  deleted_at?: string | null;
}
export interface SlowCallRow {
  id: number;
  interface_id: number;
  interface_name: string;
  protocol: string;
  duration_ms: number;
  status: string;
  http_status: number | null;
  called_at: string;
}
export interface ThroughputPoint {
  bucket: string;
  tps: number;
  p95_ms: number;
}

export const Performance = {
  percentiles: (days = 7) => api.get<PercentileRow[]>('/performance/percentiles', { params: { days } }),
  slowTop: (limit = 10, days = 7) =>
    api.get<SlowCallRow[]>('/performance/slow-top', { params: { limit, days } }),
  throughput: (bucket_minutes = 15, hours = 24) =>
    api.get<ThroughputPoint[]>('/performance/throughput', { params: { bucket_minutes, hours } }),
};

export interface UserItem {
  id: number;
  username: string;
  full_name: string | null;
  email: string | null;
  role: 'ADMIN' | 'OPERATOR' | 'VIEWER';
  last_login_at: string | null;
  disabled_at: string | null;
  failed_login_count: number;
  locked_until: string | null;
  must_change_password: boolean;
  created_at: string;
}

export const Users = {
  list: () => api.get<UserItem[]>('/users'),
  create: (payload: { username: string; password: string; full_name?: string; email?: string; role: string }) =>
    api.post<UserItem>('/users', payload),
  update: (id: number, payload: { full_name?: string; email?: string; role?: string }) =>
    api.patch<UserItem>(`/users/${id}`, payload),
  disable: (id: number) => api.post<UserItem>(`/users/${id}/disable`),
  enable: (id: number) => api.post<UserItem>(`/users/${id}/enable`),
  unlock: (id: number) => api.post<UserItem>(`/users/${id}/unlock`),
  resetPassword: (id: number) =>
    api.post<{ user_id: number; username: string; temp_password: string }>(`/users/${id}/reset-password`),
};

export const Auth = {
  changePassword: (current_password: string, new_password: string) =>
    api.post('/auth/change-password', { current_password, new_password }),
};

export interface AuditLogItem {
  id: number;
  actor_user_id: number | null;
  actor_username: string;
  actor_role: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  before_value: Record<string, unknown> | null;
  after_value: Record<string, unknown> | null;
  ip: string | null;
  user_agent: string | null;
  occurred_at: string;
}

export const AuditLogs = {
  list: (params: Record<string, unknown> = {}) =>
    api.get<AuditLogItem[]>('/audit-logs', { params }),
  actions: () => api.get<string[]>('/audit-logs/actions'),
  resourceTypes: () => api.get<string[]>('/audit-logs/resource-types'),
  exportXlsxUrl: (params: Record<string, unknown> = {}) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') q.set(k, String(v));
    }
    const qs = q.toString();
    return `/api/audit-logs/export.xlsx${qs ? '?' + qs : ''}`;
  },
};