import { fetchRows } from "@/lib/supabase";
import { Meeting } from "@/lib/types";
import { MeetingTypeBadge } from "@/components/StatusBadge";

export const dynamic = "force-dynamic";

export default async function BookingsPage() {
  let meetings: Meeting[] = [];
  let loadError: string | null = null;

  try {
    meetings = await fetchRows<Meeting>("meetings", "select=*&order=created_at.desc&limit=300");
  } catch (err) {
    loadError = err instanceof Error ? err.message : "Unknown error";
  }

  return (
    <div>
      <header className="page-header">
        <h1 className="page-title">Bookings</h1>
        <p className="page-subtitle">Meetings booked through the voice agent.</p>
      </header>

      {loadError ? (
        <div className="panel empty-state">
          <strong>Couldn&apos;t load bookings</strong>
          {loadError}
        </div>
      ) : meetings.length === 0 ? (
        <div className="panel empty-state">
          <strong>No bookings yet</strong>
          Confirmed meetings will show up here as callers book them.
        </div>
      ) : (
        <div className="panel">
          <table className="table">
            <thead>
              <tr>
                <th>Customer</th>
                <th>Type</th>
                <th>Topic</th>
                <th>Scheduled for</th>
                <th>Contact</th>
                <th>Link</th>
              </tr>
            </thead>
            <tbody>
              {meetings.map((m) => (
                <tr key={m.id}>
                  <td>{m.customer_name || "—"}</td>
                  <td>
                    <MeetingTypeBadge type={m.meeting_type} />
                  </td>
                  <td className="muted">{m.topic || "—"}</td>
                  <td className="mono">
                    {m.time && m.date ? `${m.time} ${m.date}` : m.time || m.date || "—"}
                  </td>
                  <td className="muted">
                    <div>{m.email || "—"}</div>
                    <div className="mono">{m.phone || ""}</div>
                  </td>
                  <td>
                    {m.meet_link ? (
                      <a href={m.meet_link} target="_blank" rel="noreferrer">
                        Join link
                      </a>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
