/**
 * Formatting helpers shared across views.
 *
 * Backend timestamps are UTC ISO strings; we display them in the user's
 * local time zone (KST for Korean operators).
 */

const pad = (n: number) => String(n).padStart(2, '0');

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** Compact form: MM-DD HH:mm — for tight cells like LIVE feeds. */
export function formatDateTimeShort(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * Convert a cron expression to a human-readable Korean label.
 * Returns the schedule kind + display text + a chip color hint.
 */
export function cronToLabel(
  expr: string | null | undefined,
): { label: string; color: string; icon: string } {
  if (!expr) return { label: '수동만', color: 'grey', icon: 'mdi-hand-back-right-outline' };
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return { label: expr, color: 'default', icon: 'mdi-cog-outline' };
  const [m, h, d, mo, w] = parts;

  if (mo === '*' && d === '*' && w === '*' && h === '*' && m.startsWith('*/')) {
    return { label: `매 ${m.slice(2)}분`, color: 'info', icon: 'mdi-timer-sand' };
  }
  if (mo === '*' && d === '*' && w === '*' && h === '*' && /^\d+$/.test(m)) {
    return { label: `매시 ${pad(parseInt(m))}분`, color: 'info', icon: 'mdi-clock-outline' };
  }
  if (mo === '*' && d === '*' && w === '*' && /^\d+$/.test(m) && /^\d+$/.test(h)) {
    return {
      label: `매일 ${pad(parseInt(h))}:${pad(parseInt(m))}`,
      color: 'primary',
      icon: 'mdi-calendar-today',
    };
  }
  if (mo === '*' && d === '*' && w !== '*' && /^\d+$/.test(m) && /^\d+$/.test(h)) {
    const wmap = ['일', '월', '화', '수', '목', '금', '토'];
    const wds: number[] = [];
    for (const tok of w.split(',')) {
      if (tok.includes('-')) {
        const [a, b] = tok.split('-').map((n) => parseInt(n));
        for (let i = a; i <= b; i++) wds.push(i);
      } else {
        const n = parseInt(tok);
        if (!Number.isNaN(n)) wds.push(n);
      }
    }
    const wdayStr =
      wds.length === 5 && [1, 2, 3, 4, 5].every((x) => wds.includes(x))
        ? '평일'
        : wds.map((x) => wmap[x % 7]).join('·');
    return {
      label: `${wdayStr} ${pad(parseInt(h))}:${pad(parseInt(m))}`,
      color: 'primary',
      icon: 'mdi-calendar-week',
    };
  }
  if (mo === '*' && /^\d+$/.test(d) && w === '*' && /^\d+$/.test(m) && /^\d+$/.test(h)) {
    return {
      label: `매월 ${parseInt(d)}일 ${pad(parseInt(h))}:${pad(parseInt(m))}`,
      color: 'primary',
      icon: 'mdi-calendar-month',
    };
  }
  return { label: expr, color: 'default', icon: 'mdi-cog-outline' };
}