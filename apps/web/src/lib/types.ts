export const STAGES = [
  "new_lead",
  "calling_in_progress",
  "no_answer_retry",
  "follow_up_requested",
  "appointment_booked",
  "transferred_to_human",
  "not_interested",
] as const;

export type PipelineStage = (typeof STAGES)[number];

export const STAGE_LABELS: Record<PipelineStage, string> = {
  new_lead: "New Lead",
  calling_in_progress: "Calling in Progress",
  no_answer_retry: "No Answer / Retry",
  follow_up_requested: "Follow-up Requested",
  appointment_booked: "Appointment Booked",
  transferred_to_human: "Transferred to Human",
  not_interested: "Not Interested",
};

export type Sentiment = "positive" | "neutral" | "negative";
export type CallOutcome = "booked" | "voicemail" | "follow_up_requested" | "transferred" | "not_interested";

export interface Contact {
  id: string;
  phone: string;
  full_name: string | null;
  email: string | null;
  stage: PipelineStage;
  deal_value: number | string | null;
  retry_count: number;
  tags: string[] | null;
  custom_fields: Record<string, unknown> | null;
  last_contacted_at: string | null;
  next_follow_up_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface TranscriptLine {
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  interrupted?: boolean;
}

export interface CallLog {
  id: string;
  contact_id: string;
  provider_call_id: string | null;
  direction: "inbound" | "outbound" | "webrtc";
  duration_seconds: number | null;
  recording_url: string | null;
  transcript: TranscriptLine[] | null;
  summary: string | null;
  outcome: CallOutcome | null;
  sentiment: Sentiment | null;
  extracted_data: Record<string, unknown> | null;
  created_at: string;
}

export interface Appointment {
  id: string;
  contact_id: string;
  call_log_id: string | null;
  cal_booking_uid: string | null;
  scheduled_at: string;
  status: "confirmed" | "cancelled" | "rescheduled" | "completed";
  notes: string | null;
  created_at: string;
}

export interface AgentConfig {
  id: 1;
  agent_name: string;
  company_name: string;
  nova_voice_id: string;
  outbound_greeting: string;
  inbound_greeting: string;
  qualification_questions: string[];
  objection_handlers: Record<string, string>;
  human_transfer_phone: string | null;
  cal_event_type_id: number;
  timezone: string;
  business_hours_start: number;
  business_hours_end: number;
  updated_at: string;
}

/** Nova Sonic voice IDs (see AWS "Nova Sonic language support" docs for the full, current list). */
export const NOVA_VOICES = [
  { id: "tiffany", label: "Tiffany — US English, feminine" },
  { id: "matthew", label: "Matthew — US English, masculine" },
  { id: "amy", label: "Amy — British English, feminine" },
  { id: "ambre", label: "Ambre — French, feminine" },
  { id: "florian", label: "Florian — French, masculine" },
  { id: "beatrice", label: "Beatrice — Italian, feminine" },
  { id: "lorenzo", label: "Lorenzo — Italian, masculine" },
  { id: "greta", label: "Greta — German, feminine" },
  { id: "lennart", label: "Lennart — German, masculine" },
  { id: "lupe", label: "Lupe — US Spanish, feminine" },
  { id: "carlos", label: "Carlos — US Spanish, masculine" },
] as const;
