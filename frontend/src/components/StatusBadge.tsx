import { CircleAlert, CircleCheck, Clock3 } from "lucide-react";
import { displayLabel, displayTone } from "../ui/display";

export function StatusBadge({ value }: { value: unknown }) {
  const tone = displayTone(value);
  const Icon = tone === "success" ? CircleCheck : tone === "danger" ? CircleAlert : Clock3;
  return <span className={`status-badge ${tone}`}><Icon aria-hidden="true" />{displayLabel(value)}</span>;
}

