import { formatDistanceToNowStrict } from "date-fns";

export function formatPhone(phone: string): string {
  const m = phone.match(/^\+1(\d{3})(\d{3})(\d{4})$/);
  return m ? `(${m[1]}) ${m[2]}-${m[3]}` : phone;
}

export function formatMoney(value: number | string | null | undefined): string | null {
  const n = Number(value ?? 0);
  if (!n) return null;
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

export function relativeTime(iso: string | null | undefined): string | null {
  return iso ? `${formatDistanceToNowStrict(new Date(iso))} ago` : null;
}

export function formatDuration(seconds: number | null | undefined): string {
  const s = seconds ?? 0;
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
