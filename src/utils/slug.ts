export function slugify(input: string): string {
  const s = (input ?? "").trim().toLowerCase();
  const out = s
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-{2,}/g, "-");
  return out || "unknown";
}

export function normalizeCandidateName(input: string): string {
  return (input ?? "").trim().split(/\s+/).join(" ");
}

