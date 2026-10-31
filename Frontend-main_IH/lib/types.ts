// Mirrors innoventix_schema.sql from the voice agent project.

export type CallOutcome =
  | "meeting_booked"
  | "warm_lead"
  | "info_inquiry"
  | "transferred"
  | "dropped_call";

export interface CallLog {
  id: number;
  call_id: string | null;
  caller_phone: string | null;
  duration_seconds: number | null;
  transcript: string | null;
  outcome: CallOutcome | null;
  created_at: string;
}

export interface Meeting {
  id: number;
  meeting_type: "sales" | "support" | null;
  customer_name: string | null;
  phone: string | null;
  email: string | null;
  topic: string | null;
  date: string | null;
  time: string | null;
  meet_link: string | null;
  call_id: string | null;
  created_at: string;
}

export type LeadStatus = "warm" | "contacted" | "converted" | "cold";

export interface Lead {
  id: number;
  name: string | null;
  phone: string | null;
  interested_service: string | null;
  reason: string | null;
  status: LeadStatus | null;
  call_id: string | null;
  created_at: string;
}
