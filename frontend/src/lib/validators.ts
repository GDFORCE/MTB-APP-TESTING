// Shared "sanitize as you type" helpers for free-text form fields.
// Each function strips characters that don't belong in that field type,
// so invalid input (e.g. digits in a person's name) can never be typed
// in the first place rather than being caught only at submit time.

// Latin-1 Supplement + Latin Extended-A covers accented Western names
// (é, ñ, ü, etc.) without pulling in full Unicode property escapes,
// which older Hermes builds may not support.
const LATIN_EXTENDED = "À-ſ";

const NAME_DISALLOWED = new RegExp(`[^a-zA-Z${LATIN_EXTENDED}\\s'.-]`, "g");
const DESIGNATION_DISALLOWED = new RegExp(`[^a-zA-Z${LATIN_EXTENDED}\\s'.,/&()-]`, "g");
const ORG_NAME_DISALLOWED = new RegExp(`[^a-zA-Z0-9${LATIN_EXTENDED}\\s'.,&()-]`, "g");
const ADDRESS_DISALLOWED = new RegExp(`[^a-zA-Z0-9${LATIN_EXTENDED}\\s'.,/#&-]`, "g");

/** Person names: letters, spaces, hyphens, apostrophes, periods (initials). No digits. */
export function sanitizeName(value: string): string {
  return value.replace(NAME_DISALLOWED, "");
}

/** Job titles / roles: letters plus separators used in real designations (MD/PhD, Sr. Nurse). No digits. */
export function sanitizeDesignation(value: string): string {
  return value.replace(DESIGNATION_DISALLOWED, "");
}

/** Organization / site / hospital names: letters, digits, and common punctuation (e.g. "3M", "St. Mary's"). */
export function sanitizeOrgName(value: string): string {
  return value.replace(ORG_NAME_DISALLOWED, "");
}

/** Street addresses / city / state: letters, digits, and address punctuation (#, /, ,). */
export function sanitizeAddress(value: string): string {
  return value.replace(ADDRESS_DISALLOWED, "");
}

/** Digits only, optionally capped to a max length (phone numbers, OTP codes, numeric counts). */
export function sanitizeDigits(value: string, maxLength?: number): string {
  const digits = value.replace(/\D/g, "");
  return typeof maxLength === "number" ? digits.slice(0, maxLength) : digits;
}
