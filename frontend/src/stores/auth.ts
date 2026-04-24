import { defineStore } from 'pinia';
import { api } from '@/api/client';

export type Role = 'VIEWER' | 'OPERATOR' | 'ADMIN';

export interface AuthUser {
  id: number;
  username: string;
  full_name: string | null;
  email: string | null;
  role: Role;
  last_login_at: string | null;
  must_change_password?: boolean;
  failed_login_count?: number;
  locked_until?: string | null;
  created_at: string;
}

interface State {
  token: string | null;
  user: AuthUser | null;
  expiresAt: string | null;
}

export const useAuthStore = defineStore('auth', {
  state: (): State => ({
    token: localStorage.getItem('noahub_token'),
    user: JSON.parse(localStorage.getItem('noahub_user') || 'null'),
    expiresAt: localStorage.getItem('noahub_expires'),
  }),

  getters: {
    isAuthenticated: (s) => !!s.token && !!s.user,
    role: (s): Role | null => s.user?.role ?? null,
    isAdmin: (s) => s.user?.role === 'ADMIN',
    isOperator: (s) => s.user?.role === 'OPERATOR' || s.user?.role === 'ADMIN',
    canMutate: (s) => s.user?.role === 'OPERATOR' || s.user?.role === 'ADMIN',
    // 강제 비밀번호 변경 필요 여부
    mustChangePassword: (s) => !!s.user?.must_change_password,
  },

  actions: {
    async login(username: string, password: string) {
      const res = await api.post('/auth/login', { username, password });
      this.token = res.data.access_token;
      this.user = res.data.user;
      this.expiresAt = res.data.expires_at;
      localStorage.setItem('noahub_token', this.token!);
      localStorage.setItem('noahub_user', JSON.stringify(this.user));
      localStorage.setItem('noahub_expires', this.expiresAt!);
    },

    async logout() {
      try {
        if (this.token) await api.post('/auth/logout');
      } catch {
        /* server error during logout is non-fatal — we still clear locally */
      }
      this.token = null;
      this.user = null;
      this.expiresAt = null;
      localStorage.removeItem('noahub_token');
      localStorage.removeItem('noahub_user');
      localStorage.removeItem('noahub_expires');
    },

    async refreshMe() {
      try {
        const res = await api.get('/auth/me');
        this.user = res.data;
        localStorage.setItem('noahub_user', JSON.stringify(this.user));
      } catch {
        // 토큰 만료/무효 → 로그아웃 상태로
        await this.logout();
      }
    },

    ///B.4 — 비밀번호 변경 후 must_change_password 등 사용자 상태 동기화
    setUser(u: AuthUser) {
      this.user = u;
      localStorage.setItem('noahub_user', JSON.stringify(u));
    },

    // 비밀번호 변경 응답에 포함된 새 토큰으로 교체. 기존 토큰은
    // 서버에서 tokens_invalid_before 갱신으로 무효화됨 → 새 토큰만 살아있음.
    setToken(token: string, expiresAt: string) {
      this.token = token;
      this.expiresAt = expiresAt;
      localStorage.setItem('noahub_token', token);
      localStorage.setItem('noahub_expires', expiresAt);
    },
  },
});