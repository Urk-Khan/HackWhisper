import styles from "./StatusBadge.module.css";

type Tone = "amber" | "teal" | "red" | "blue" | "neutral";

const CALL_OUTCOME_TONE: Record<string, Tone> = {
  meeting_booked: "teal",
  warm_lead: "amber",
  info_inquiry: "blue",
  transferred: "blue",
  dropped_call: "blue",
};

const LEAD_STATUS_TONE: Record<string, Tone> = {
  warm: "amber",
  contacted: "blue",
  converted: "teal",
  cold: "neutral",
};

function label(value: string): string {
  if (value === "dropped_call") return "customer support";
  return value.replace(/_/g, " ");
}

export function CallOutcomeBadge({ outcome }: { outcome: string | null }) {
  if (!outcome) return <span className={`${styles.badge} ${styles.neutral}`}>unknown</span>;
  const tone = CALL_OUTCOME_TONE[outcome] || "neutral";
  return <span className={`${styles.badge} ${styles[tone]}`}>{label(outcome)}</span>;
}

export function LeadStatusBadge({ status }: { status: string | null }) {
  if (!status) return <span className={`${styles.badge} ${styles.neutral}`}>unset</span>;
  const tone = LEAD_STATUS_TONE[status] || "neutral";
  return <span className={`${styles.badge} ${styles[tone]}`}>{label(status)}</span>;
}

export function MeetingTypeBadge({ type }: { type: string | null }) {
  const tone: Tone = type === "support" ? "blue" : "amber";
  return <span className={`${styles.badge} ${styles[tone]}`}>{type || "sales"}</span>;
}
