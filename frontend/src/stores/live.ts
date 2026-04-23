import { defineStore } from 'pinia';
import { subscribe } from '@/api/socket';
import type { CallLogItem, IncidentItem } from '@/api/client';

interface State {
  recentCalls: CallLogItem[];
  recentIncidents: IncidentItem[];
  bound: boolean;
}

export const useLiveStore = defineStore('live', {
  state: (): State => ({ recentCalls: [], recentIncidents: [], bound: false }),
  actions: {
    bind() {
      if (this.bound) return;
      this.bound = true;
      subscribe((channel, data) => {
        if (channel === 'call_log') {
          this.recentCalls = [data, ...this.recentCalls].slice(0, 50);
        } else if (channel === 'incident') {
          this.recentIncidents = [data, ...this.recentIncidents].slice(0, 30);
        }
      });
    },
  },
});