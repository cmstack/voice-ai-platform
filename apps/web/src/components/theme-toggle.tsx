"use client";

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { Monitor, Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";

const ORDER = ["system", "light", "dark"] as const;
const ICONS = { system: Monitor, light: Sun, dark: Moon };
const LABELS = { system: "Theme: system", light: "Theme: light", dark: "Theme: dark" };

/** Cycles system → light → dark. Renders a stable placeholder until mounted to avoid hydration mismatch. */
export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const current = (mounted && (theme as (typeof ORDER)[number])) || "system";
  const Icon = ICONS[current];
  const next = ORDER[(ORDER.indexOf(current) + 1) % ORDER.length];

  return (
    <Button variant="ghost" size="icon" aria-label={`${LABELS[current]}. Switch to ${next}.`} title={LABELS[current]} onClick={() => setTheme(next)}>
      <Icon className="size-4" strokeWidth={1.75} />
    </Button>
  );
}
