import { DEFAULT_COUNTRY_CODE, getCountry } from "@/src/data/countries";

export type RegistrationVariant = "sponsor" | "site" | "smo" | "patient";
export type RegistrationFields = Record<string, string>;
export type RegistrationErrors = Partial<Record<string, string>>;

export type RegistrationValidation = {
  valid: boolean;
  errors: RegistrationErrors;
  normalized: RegistrationFields;
  age: number | null;
};

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

export function normalizeEmail(value = "") {
  return value.trim().toLowerCase();
}

// Length rules we are confident about. Everything else falls back to the generic
// E.164 bound (total number ≤ 15 digits) rather than risking a false rejection of
// a legitimate foreign number.
const INDIA_NSN = /^[6-9]\d{9}$/;

function checkNsn(dial: string, digits: string): boolean {
  if (dial === "91") return INDIA_NSN.test(digits);
  // NANP (+1 and its +1XXX territories) is always 10 digits after the country code.
  if (dial.startsWith("1")) return dial.length - 1 + digits.length === 10;
  // E.164 caps a full number at 15 digits; 4 is the shortest usable subscriber part.
  return digits.length >= 4 && digits.length + dial.length <= 15;
}

/** Strip formatting, an international prefix, and the national trunk "0". */
function nationalDigits(value: string, dial: string) {
  let digits = value.replace(/\D/g, "");
  if (digits.startsWith(`00${dial}`)) digits = digits.slice(2 + dial.length);
  else if (digits.startsWith(dial) && digits.length > dial.length + 4) digits = digits.slice(dial.length);
  if (digits.length > 1 && digits.startsWith("0")) digits = digits.replace(/^0+/, "");
  return digits;
}

/**
 * Combine a country selection with a locally typed number into E.164, or "" when
 * the pair cannot form a valid number.
 */
export function normalizePhone(value = "", countryCode = DEFAULT_COUNTRY_CODE) {
  const { dial } = getCountry(countryCode);
  const digits = nationalDigits(value, dial);
  return checkNsn(dial, digits) ? `+${dial}${digits}` : "";
}

export function phoneHint(countryCode = DEFAULT_COUNTRY_CODE) {
  const { dial, name } = getCountry(countryCode);
  if (dial === "91") return "Enter a valid 10-digit Indian mobile number.";
  if (dial.startsWith("1")) return `Enter a valid ${11 - dial.length}-digit number for ${name}.`;
  return `Enter a valid mobile number for ${name}.`;
}

export function parseDob(value = "", reference = new Date()) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim());
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(year, month - 1, day);
  if (
    date.getFullYear() !== year
    || date.getMonth() !== month - 1
    || date.getDate() !== day
  ) return null;
  const today = new Date(
    reference.getFullYear(),
    reference.getMonth(),
    reference.getDate(),
  );
  if (date > today) return null;
  let age = today.getFullYear() - date.getFullYear();
  const monthDelta = today.getMonth() - date.getMonth();
  if (monthDelta < 0 || (monthDelta === 0 && today.getDate() < date.getDate())) age--;
  if (age < 0 || age > 120) return null;
  return { date, age, canonical: `${match[1]}-${match[2]}-${match[3]}` };
}

function required(
  fields: RegistrationFields,
  errors: RegistrationErrors,
  key: string,
  label: string,
) {
  if (!fields[key]?.trim()) errors[key] = `${label} is required.`;
}

export function validateRegistration(
  variant: RegistrationVariant,
  fields: RegistrationFields,
  referenceDate = new Date(),
): RegistrationValidation {
  const normalized: RegistrationFields = {};
  for (const [key, value] of Object.entries(fields)) normalized[key] = value?.trim() || "";
  const phoneCountry = fields.phoneCountry || DEFAULT_COUNTRY_CODE;
  normalized.email = normalizeEmail(fields.email);
  normalized.phone = normalizePhone(fields.phone, phoneCountry);
  normalized.phoneCountry = getCountry(phoneCountry).code;

  const errors: RegistrationErrors = {};
  required(fields, errors, "fullName", "Full name");

  if (!normalized.phone) {
    errors.phone = fields.phone?.trim() ? phoneHint(phoneCountry) : "Phone number is required.";
  }

  if (!normalized.email) {
    errors.email = "Email ID is required.";
  } else if (!EMAIL_RE.test(normalized.email)) {
    errors.email = "Enter a valid email address.";
  }

  let age: number | null = null;
  if (variant === "patient") {
    required(fields, errors, "dob", "Date of birth");
    const parsed = parseDob(fields.dob, referenceDate);
    if (fields.dob?.trim() && !parsed) {
      errors.dob = "Enter a real date in YYYY-MM-DD format (age 0–120).";
    } else if (parsed) {
      age = parsed.age;
      normalized.dob = parsed.canonical;
    }
    required(fields, errors, "gender", "Gender");
  } else {
    required(fields, errors, "designation", "Designation");
    required(fields, errors, "orgName", variant === "smo" ? "SMO name" : "Organization name");
    required(fields, errors, "orgAddress", variant === "smo" ? "SMO address" : "Organization address");
    if (variant === "smo") {
      required(fields, errors, "role", "Role");
    }
    if (variant === "site") {
      required(fields, errors, "hospitalType", "Hospital type");
      required(fields, errors, "role", "Role");
    }
  }

  return {
    valid: Object.keys(errors).length === 0,
    errors,
    normalized,
    age,
  };
}
