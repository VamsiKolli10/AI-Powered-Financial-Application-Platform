import { useState } from "react";
import { ApiError, correctCategory } from "../api";
import { Button } from "../components";

export function CategoryEditor({ token, id, category, categories, onChanged, onUnauthorized }: {
  token: string; id: string; category: string | null; categories: Record<string, string>;
  onChanged: () => void; onUnauthorized: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [slug, setSlug] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    setBusy(true); setError("");
    try { await correctCategory(token, id, slug); setEditing(false); onChanged(); }
    catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) onUnauthorized();
      else setError(reason instanceof Error ? reason.message : "Category could not be saved.");
    } finally { setBusy(false); }
  }
  if (!editing) return <>{category ?? "Categorizing…"} <Button variant="ghost" disabled={!Object.keys(categories).length} onClick={() => {
    setSlug(Object.entries(categories).find(([, name]) => name === category)?.[0] ?? "other"); setEditing(true);
  }}>Edit category</Button></>;
  return <div><select aria-label="New category" className="field__control" value={slug} onChange={(e) => setSlug(e.target.value)} disabled={busy}>
    {Object.entries(categories).map(([value, name]) => <option value={value} key={value}>{name}</option>)}
  </select><Button loading={busy} onClick={() => void save()}>Save category</Button>
    <Button variant="ghost" disabled={busy} onClick={() => setEditing(false)}>Cancel</Button>
    {error && <p role="alert">{error}</p>}
  </div>;
}
