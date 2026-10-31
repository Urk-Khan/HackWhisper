import { fetchRows } from "@/lib/supabase";
import { CallLog } from "@/lib/types";
import CallsTable from "@/components/CallsTable";

export const dynamic = "force-dynamic";

export default async function CallsPage() {
  let calls: CallLog[] = [];
  let loadError: string | null = null;

  try {
    calls = await fetchRows<CallLog>("call_logs", "select=*&order=created_at.desc&limit=300");
  } catch (err) {
    loadError = err instanceof Error ? err.message : "Unknown error";
  }

  return (
    <div>
      <header className="page-header">
        <h1 className="page-title">Calls</h1>
        <p className="page-subtitle">Every call the voice agent has answered, most recent first.</p>
      </header>

      {loadError ? (
        <div className="panel empty-state">
          <strong>Couldn&apos;t load calls</strong>
          {loadError}
        </div>
      ) : (
        <CallsTable calls={calls} />
      )}
    </div>
  );
}
