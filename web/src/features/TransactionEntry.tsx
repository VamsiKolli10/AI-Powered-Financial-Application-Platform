import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, createTransaction, getAccounts, importTransactions } from "../api";
import { Banner, Button, Field, Panel, PanelHeader } from "../components";
import type { Account } from "../types";

// getRandomValues is available on HTTP origins too; randomUUID requires HTTPS.
function newEntryId(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function TransactionEntry({ token, onChanged, onUnauthorized }: {
  token: string; onChanged: () => void; onUnauthorized: () => void;
}) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [accountId, setAccountId] = useState("");
  const [description, setDescription] = useState("");
  const [amount, setAmount] = useState("");
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [direction, setDirection] = useState("expense");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  // Keep the id when a failed request is retried; a lost response must not duplicate money.
  const entryId = useRef<{ fingerprint: string; id: string } | null>(null);
  const account = accounts.find((item) => item.id === accountId);
  useEffect(() => {
    let cancelled = false;
    getAccounts(token).then((items) => {
      if (cancelled) return;
      setAccounts(items);
      setAccountId(items[0]?.id ?? "");
    }).catch((reason) => {
      if (cancelled) return;
      if (reason instanceof ApiError && reason.status === 401) onUnauthorized();
      else setError("Accounts could not be loaded. Refresh the page to try again.");
    });
    return () => { cancelled = true; };
  }, [token, onUnauthorized]);

  async function run(action: () => Promise<string>) {
    setBusy(true); setError(""); setMessage("");
    try { setMessage(await action()); onChanged(); }
    catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) onUnauthorized();
      else setError(reason instanceof Error ? reason.message : "Could not save transactions.");
    } finally { setBusy(false); }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!account || !Number.isFinite(Number(amount)) || Number(amount) <= 0) return;
    const fingerprint = JSON.stringify([account.id, direction, amount, description.trim(), date]);
    if (entryId.current?.fingerprint !== fingerprint) entryId.current = { fingerprint, id: newEntryId() };
    const id = entryId.current.id;
    void run(async () => {
      await createTransaction(token, {
        account_id: account.id, external_tx_id: `manual:${id}`,
        amount: `${direction === "expense" ? "-" : ""}${amount}`,
        currency: account.currency, description: description.trim(), occurred_at: new Date(`${date}T00:00:00Z`).toISOString(),
      });
      entryId.current = null; setDescription(""); setAmount("");
      return "Transaction saved. Categorization runs automatically.";
    });
  }

  return <Panel labelledBy="entry-title">
    <PanelHeader eyebrow="Your data" title="Add transactions" titleId="entry-title" />
    {error && <Banner>{error}</Banner>}
    {message && <p role="status">{message}</p>}
    <label className="field"><span className="field__label">Account</span>
      <select className="field__control" value={accountId} onChange={(e) => setAccountId(e.target.value)} disabled={busy || !accounts.length}>
        {!accounts.length && <option value="">No accounts available</option>}
        {accounts.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.currency}</option>)}
      </select>
    </label>
    <form className="entry-form" onSubmit={submit}>
      <Field label="Description" required maxLength={500} value={description} onChange={(e) => setDescription(e.target.value)} disabled={busy} />
      <label className="field"><span className="field__label">Direction</span><select className="field__control" value={direction} onChange={(e) => setDirection(e.target.value)} disabled={busy}>
        <option value="expense">Expense</option><option value="income">Income</option>
      </select></label>
      <Field label={`Amount (${account?.currency ?? "currency"})`} type="number" required min="0.01" max="9999999999999999.99" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} disabled={busy} />
      <Field label="Date (UTC)" type="date" required value={date} onChange={(e) => setDate(e.target.value)} disabled={busy} />
      <Button type="submit" loading={busy} disabled={!account || !description.trim()}>Save transaction</Button>
    </form>
    <details className="csv-import"><summary>Import a CSV</summary>
      <p>Use headers <code>occurred_at,description,amount</code>. Negative amounts are expenses; positive amounts are income. Optional columns: <code>currency,external_tx_id</code>. Dates use YYYY-MM-DD (UTC) or a timestamp with timezone. Maximum 500 rows and 256 KiB. Reimporting the same file skips existing rows.</p>
      <Field label="CSV file" type="file" accept=".csv,text/csv" disabled={busy} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      <Button disabled={!account || !file} loading={busy} onClick={() => void run(async () => {
        if (!file || !account) return "Select a CSV and an account.";
        if (file.size > 262144) throw new Error("CSV must be at most 256 KiB.");
        const result = await importTransactions(token, account.id, await file.text());
        return `Imported ${result.created} transactions; skipped ${result.skipped} existing rows.`;
      })}>Import transactions</Button>
    </details>
  </Panel>;
}
