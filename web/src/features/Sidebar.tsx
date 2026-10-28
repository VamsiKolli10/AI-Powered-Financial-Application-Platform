import { BrandMark, Button, StatusDot } from "../components";
import { useActiveSection } from "../hooks/useActiveSection";
import { SERVICE_LABEL, SERVICE_NAMES, type StatusReport } from "./health";

const SECTIONS = [
  { id: "overview", label: "Overview" },
  { id: "assistant", label: "Assistant" },
  { id: "transactions", label: "Transactions" },
  { id: "alerts", label: "Alerts" },
];

function StatusCard({ report }: { report: StatusReport | null }) {
  if (!report) {
    return <><StatusDot tone="muted" />Checking services…</>;
  }
  const known = SERVICE_NAMES.filter((name) => report.services[name] !== undefined);
  const failing = known.filter((name) => report.services[name] === "error");
  const verb = report.source === "probe" ? "ready" : "responding";
  if (failing.length === 0) {
    return (
      <>
        <StatusDot tone="ok" />All services {verb}
        <small>{report.source === "probe" ? "Readiness checked by the gateway" : "Based on this page's requests"}</small>
      </>
    );
  }
  return (
    <>
      <StatusDot tone="alert" />{failing.length} of {known.length} services unavailable
      <small>{failing.map((name) => SERVICE_LABEL[name]).join(" · ")}</small>
    </>
  );
}

export function Sidebar({ status, onLogout }: { status: StatusReport | null; onLogout: () => void }) {
  const active = useActiveSection(SECTIONS.map((section) => section.id));

  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <BrandMark />
        <strong>Ledger AI</strong>
      </div>
      <nav className="sidebar__nav" aria-label="Dashboard sections">
        {SECTIONS.map((section) => (
          <a
            key={section.id}
            href={`#${section.id}`}
            className="sidebar__link"
            aria-current={active === section.id ? "location" : undefined}
          >
            {section.label}
          </a>
        ))}
      </nav>
      <div className="sidebar__status" role="status">
        <StatusCard report={status} />
      </div>
      <Button variant="ghost" className="sidebar__signout" onClick={onLogout}>Sign out</Button>
    </aside>
  );
}
