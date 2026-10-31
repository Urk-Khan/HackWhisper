import { CallLog, CallOutcome } from "@/lib/types";
import styles from "./OutcomePieChart.module.css";

interface OutcomeConfig {
  key: CallOutcome;
  label: string;
  color: string;
  dotClass: string;
}

const OUTCOMES: OutcomeConfig[] = [
  { key: "meeting_booked", label: "Meetings booked", color: "#3fb68a", dotClass: styles.dotTeal },
  { key: "warm_lead", label: "Warm leads", color: "#e8a33d", dotClass: styles.dotAmber },
  { key: "dropped_call", label: "Customer support", color: "#3b82f6", dotClass: styles.dotBlue },
  { key: "info_inquiry", label: "Info inquiry", color: "#0ea5e9", dotClass: styles.dotCyan },
  { key: "transferred", label: "Transferred", color: "#8b5cf6", dotClass: styles.dotPurple },
];

export default function OutcomePieChart({ calls }: { calls: CallLog[] }) {
  const counts: Record<string, number> = {};
  for (const o of OUTCOMES) counts[o.key] = 0;

  for (const call of calls) {
    const outcome = call.outcome || "unknown";
    counts[outcome] = (counts[outcome] || 0) + 1;
  }

  const total = calls.length;
  const radius = 54;
  const circumference = 2 * Math.PI * radius; // ~339.292

  // Build slices for outcomes that have count > 0
  let cumulativePercent = 0;
  const slices = OUTCOMES.map((o) => {
    const count = counts[o.key] || 0;
    const percent = total > 0 ? count / total : 0;
    const strokeDasharray = `${Math.max(0, percent * circumference - (total > 1 ? 2 : 0))} ${circumference}`;
    const strokeDashoffset = -cumulativePercent * circumference;
    cumulativePercent += percent;

    return {
      ...o,
      count,
      percent: Math.round(percent * 100),
      strokeDasharray,
      strokeDashoffset,
    };
  });

  return (
    <div className={styles.wrap}>
      <div className={styles.donutContainer}>
        <svg viewBox="0 0 160 160" className={styles.donutSvg}>
          {/* Background circle track */}
          <circle
            cx="80"
            cy="80"
            r={radius}
            fill="none"
            stroke="var(--border)"
            strokeWidth="16"
            opacity="0.4"
          />

          {/* Slices */}
          {total > 0 &&
            slices
              .filter((s) => s.count > 0)
              .map((s) => (
                <circle
                  key={s.key}
                  cx="80"
                  cy="80"
                  r={radius}
                  fill="none"
                  stroke={s.color}
                  strokeWidth="16"
                  strokeDasharray={s.strokeDasharray}
                  strokeDashoffset={s.strokeDashoffset}
                  transform="rotate(-90 80 80)"
                  className={styles.slice}
                />
              ))}

          {/* Center text */}
          <text x="80" y="76" textAnchor="middle" className={styles.centerNumber}>
            {total}
          </text>
          <text x="80" y="93" textAnchor="middle" className={styles.centerLabel}>
            Total calls
          </text>
        </svg>
      </div>

      <div className={styles.legend}>
        {slices.map((s) => (
          <div key={s.key} className={s.count > 0 ? styles.legendItem : styles.legendItemMuted}>
            <div className={styles.legendLeft}>
              <span className={`${styles.dot} ${s.dotClass}`} style={{ backgroundColor: s.color }} />
              <span className={styles.legendName}>{s.label}</span>
            </div>
            <div className={styles.legendRight}>
              <span className={styles.legendCount}>{s.count}</span>
              <span className={styles.legendPct}>({s.percent}%)</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
