"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { RealtimeChannel, RealtimePostgresChangesPayload } from "@supabase/supabase-js";
import { supabase } from "@/lib/supabase";
import type { CallLog, Contact, PipelineStage, Sentiment } from "@/lib/types";

type LatestCall = Pick<CallLog, "id" | "contact_id" | "sentiment" | "outcome" | "created_at">;

/** Contacts + latest call per contact, kept live with Postgres Changes on one channel. */
export function useCrm() {
  const [contacts, setContacts] = useState<Map<string, Contact>>(new Map());
  const [latestCalls, setLatestCalls] = useState<Map<string, LatestCall>>(new Map());
  const [bookedCount, setBookedCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [recentlyMoved, setRecentlyMoved] = useState<Set<string>>(new Set());
  const [callLogVersion, setCallLogVersion] = useState(0);
  const moveTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  const flagMoved = useCallback((id: string) => {
    setRecentlyMoved((s) => new Set(s).add(id));
    clearTimeout(moveTimers.current.get(id));
    moveTimers.current.set(
      id,
      setTimeout(() => setRecentlyMoved((s) => {
        const next = new Set(s);
        next.delete(id);
        return next;
      }), 2500),
    );
  }, []);

  const refreshBooked = useCallback(async () => {
    const { count } = await supabase()
      .from("appointments")
      .select("id", { count: "exact", head: true })
      .eq("status", "confirmed");
    setBookedCount(count ?? 0);
  }, []);

  useEffect(() => {
    const db = supabase();
    let cancelled = false;

    (async () => {
      const [{ data: contactRows }, { data: callRows }] = await Promise.all([
        db.from("contacts").select("*").order("updated_at", { ascending: false }),
        db.from("call_logs").select("id, contact_id, sentiment, outcome, created_at").order("created_at", { ascending: false }).limit(1000),
      ]);
      if (cancelled) return;
      setContacts(new Map((contactRows ?? []).map((c: Contact) => [c.id, c])));
      const latest = new Map<string, LatestCall>();
      for (const row of (callRows ?? []) as LatestCall[]) if (!latest.has(row.contact_id)) latest.set(row.contact_id, row);
      setLatestCalls(latest);
      await refreshBooked();
      setLoading(false);
    })();

    // Realtime must join with the user's access token, not the anon key: RLS only shows rows to
    // admins, so an anonymous subscription silently receives nothing. On a page reload the session
    // loads from cookies asynchronously, so authenticate first, then subscribe.
    let channel: RealtimeChannel | null = null;
    const { data: authListener } = db.auth.onAuthStateChange((_event, session) => {
      void db.realtime.setAuth(session?.access_token ?? null);
    });
    (async () => {
      const { data } = await db.auth.getSession();
      if (cancelled) return;
      await db.realtime.setAuth(data.session?.access_token ?? null);
      channel = db
        .channel("crm-pipeline")
        .on("postgres_changes", { event: "*", schema: "public", table: "contacts" }, (p: RealtimePostgresChangesPayload<Contact>) => {
          setContacts((prev) => {
            const next = new Map(prev);
            if (p.eventType === "DELETE") {
              next.delete((p.old as Contact).id);
            } else {
              const row = p.new as Contact;
              if (prev.get(row.id)?.stage !== row.stage) flagMoved(row.id);
              next.set(row.id, row);
            }
            return next;
          });
        })
        .on("postgres_changes", { event: "*", schema: "public", table: "call_logs" }, (p: RealtimePostgresChangesPayload<CallLog>) => {
          if (p.eventType === "DELETE") return;
          const row = p.new as CallLog;
          setLatestCalls((prev) => {
            const current = prev.get(row.contact_id);
            if (current && current.id !== row.id && current.created_at > row.created_at) return prev;
            return new Map(prev).set(row.contact_id, row);
          });
          setCallLogVersion((v) => v + 1);
        })
        .on("postgres_changes", { event: "*", schema: "public", table: "appointments" }, () => {
          void refreshBooked();
        })
        .subscribe();
    })();

    return () => {
      cancelled = true;
      authListener.subscription.unsubscribe();
      if (channel) void db.removeChannel(channel);
    };
  }, [flagMoved, refreshBooked]);

  const moveContact = useCallback(async (id: string, stage: PipelineStage) => {
    let previous: PipelineStage | undefined;
    setContacts((prev) => {
      const c = prev.get(id);
      if (!c) return prev;
      previous = c.stage;
      return new Map(prev).set(id, { ...c, stage });
    });
    const { error } = await supabase().from("contacts").update({ stage }).eq("id", id);
    if (error && previous) {
      const restore = previous;
      setContacts((prev) => {
        const c = prev.get(id);
        return c ? new Map(prev).set(id, { ...c, stage: restore }) : prev;
      });
      throw error;
    }
  }, []);

  const list = useMemo(() => [...contacts.values()], [contacts]);
  const kpis = useMemo(() => {
    const total = list.length;
    const active = list.filter((c) => c.stage === "calling_in_progress").length;
    const booked = list.filter((c) => c.stage === "appointment_booked").length;
    return { total, active, appointments: bookedCount, conversion: total ? Math.round((booked / total) * 1000) / 10 : 0 };
  }, [list, bookedCount]);

  const sentimentFor = useCallback((id: string): Sentiment | null => latestCalls.get(id)?.sentiment ?? null, [latestCalls]);

  return { contacts: list, contactById: contacts, loading, kpis, moveContact, sentimentFor, recentlyMoved, callLogVersion };
}
