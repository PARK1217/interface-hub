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
  },
});