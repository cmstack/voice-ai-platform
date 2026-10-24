"use client";

import { CloudOff, RotateCw } from "lucide-react";
import { Button } from "@/components/ui/button";

/** Full-area error for a failed initial load, with the raw cause kept as a secondary detail. */
export function LoadError({ title, detail, onRetry }: { title: string; detail: string; onRetry: () => void }) {
  return (
    <div role="alert" className="mx-auto mt-16 flex max-w-sm flex-col items-center gap-2 px-4 text-center">
      <CloudOff className="size-5 text-danger-ink" strokeWidth={1.75} />
      <p className="text-sm font-semibold">{title}</p>
      <p className="text-xs text-muted-foreground">The database didn&apos;t respond. Check that Supabase is running, then try again.</p>
      <p className="font-mono text-[11px] text-muted-foreground">{detail}</p>
      <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
        <RotateCw strokeWidth={1.75} /> Try again
      </Button>
    </div>
  );
}
