"use client"

import * as React from "react"
import { cn } from "cn"
import { Label } from "@/components/ui/label"

/**
 * A labelled form field. `children` receives the id to put on the control, so the label is
 * programmatically tied to it (inputs, textareas and Select triggers alike).
 */
function Field({
  label,
  hint,
  error,
  invalid,
  className,
  children,
}: {
  label: string
  hint?: React.ReactNode
  error?: string | null
  /** Marks the control invalid when a neighbouring field shows the shared error. */
  invalid?: boolean
  className?: string
  children: (props: { id: string; "aria-describedby"?: string; "aria-invalid"?: boolean }) => React.ReactNode
}) {
  const id = React.useId()
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined
  return (
    <div className={cn("space-y-2", className)}>
      <Label htmlFor={id}>{label}</Label>
      {children({ id, "aria-describedby": describedBy, "aria-invalid": error || invalid ? true : undefined })}
      {error ? (
        <p id={`${id}-error`} className="text-xs text-danger-ink">{error}</p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  )
}

export { Field }
