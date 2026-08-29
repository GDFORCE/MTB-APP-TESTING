import React, { useState, useEffect, useMemo } from "react";
import { View, Text, TextInput, StyleSheet, ScrollView, Pressable, Modal, KeyboardAvoidingView, Platform, ActivityIndicator, Linking } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useLocalSearchParams } from "expo-router";
import { Check, ClipboardCheck, ShieldCheck, Building2, Mail, ChevronDown } from "lucide-react-native";
import { api } from "@/src/api/client";
import { colors, spacing, radii, fonts } from "@/src/theme/tokens";
import { Eyebrow, Body, Small } from "@/src/components/ui";
import { AuthHeader } from "@/src/components/AuthHeader";
import { Rise } from "@/src/components/Rise";
import { Springy } from "@/src/components/Springy";
import { PhoneField } from "@/src/components/PhoneField";
import { DEFAULT_COUNTRY_CODE, splitE164 } from "@/src/data/countries";
import {
  RegistrationErrors,
  RegistrationVariant,
  validateRegistration,
} from "@/src/features/auth/registration-validation";
import { GoogleHospitalPrediction, GooglePlacePrediction, useGooglePlaces, useHospitalPlaces } from "@/src/features/auth/use-hospital-places";
import { sanitizeAddress, sanitizeDesignation, sanitizeName, sanitizeOrgName } from "@/src/lib/validators";

// ── Role → header label + which form variant to render ──────────────────────
const labelMap: Record<string, string> = {
  sponsor: "Sponsor", cro: "CRO", smo: "SMO",
  site: "Site / Hospital", pi: "Site / Hospital", crc: "Site / Hospital",
  patient: "Patient",
};
const DEFAULT_DEPARTMENT_OPTIONS = [
  "Emergency Medicine", "Internal Medicine", "General Surgery", "Critical Care / Intensive Care",
  "Cardiology", "Gastroenterology", "Pulmonology", "Nephrology", "Neurology", "Endocrinology",
  "Haematology", "Infectious Diseases", "Allergy & Immunology", "Clinical Pharmacology", "Medical Genetics",
  "Orthopaedic Surgery", "Neurosurgery", "Cardiothoracic Surgery", "Urology", "Otolaryngology (ENT)",
  "Vascular Surgery", "Plastic & Reconstructive Surgery", "Colorectal Surgery", "Transplant Surgery", "Paediatric Surgery",
  "Paediatrics", "Obstetrics & Gynaecology (OB/GYN)", "Geriatrics", "Family Medicine",
  "Oncology", "Dermatology", "Psychiatry", "Rheumatology", "Ophthalmology", "Physical Medicine & Rehabilitation",
  "Radiology", "Nuclear Medicine", "Pathology", "Anaesthesiology", "Pain Medicine", "Palliative Care", "Sleep Medicine",
];
function variantFor(role?: string): RegistrationVariant {
  if (role === "smo") return "smo";
  if (role === "site" || role === "pi" || role === "crc") return "site";
  if (role === "patient") return "patient";
  return "sponsor";
}
// These objects define each form's field shape only. Registration always starts
// blank; real invitation values are applied separately from route parameters.
const FIELD_SHAPES: Record<string, Record<string, string>> = {
  site: { fullName: "Dr. Rajesh Kumar", designation: "Principal Investigator", email: "r.kumar@apollo.com", phone: "98100 12345", orgName: "Apollo Hospitals Mumbai", orgAddress: "", hospitalType: "Private", role: "PI", department: "", departmentOther: "" },
  smo: { fullName: "Dr. Rajesh Kumar", designation: "SMO Manager", email: "r.kumar@smo.com", phone: "98100 12345", orgName: "MedSites SMO Pvt Ltd", orgAddress: "", role: "" },
  patient: { fullName: "Priya Kapoor", phone: "98765 43210", email: "", dob: "1985-06-15", gender: "", language: "English" },
  sponsor: { fullName: "John Doe", designation: "Clinical Research Manager", email: "john.doe@pharmaco.com", phone: "98765 43210", orgName: "PharmaCo Ltd", orgAddress: "21 Business Park, Mumbai 400001" },
};
function initFields(variant: string): Record<string, string> {
  const shape = FIELD_SHAPES[variant] || FIELD_SHAPES.sponsor;
  const empty: Record<string, string> = {};
  for (const key of Object.keys(shape)) empty[key] = "";
  return empty;
}

// Acknowledgement shown before an organization administrator fills the form.
const organizationRegistrationInstructions = [
  "The information provided about the organization must be accurate and complete.",
  "You will become the Organization Admin after successful verification.",
  "You are responsible for inviting only authorized users from your organization.",
  "You must assign appropriate roles and access permissions based on each user's responsibilities.",
  "You are responsible for reviewing, updating, or removing user access when a user's role changes or they leave the organization.",
  "You must protect confidential, clinical trial, and patient-related information available through the platform.",
  "You must not share your login credentials, password, or OTP with others.",
  "Administrative activities performed through your account may be recorded for security and audit purposes.",
];
const OTHER_DEPARTMENT = "Others Specify";

const smoRegistrationInstructions = [
  "Register an organization, hospital, or clinical trial site only if you are authorized or have a legitimate professional relationship to manage its clinical trial activities on this platform.",
  "The information provided during registration must be accurate and complete.",
  "You will become the Organization Admin after successful verification.",
  "An SMO Admin may register and administratively manage hospitals or clinical trial sites where the SMO supports investigators and/or clinical trial activities.",
  "The SMO Admin is responsible for maintaining accurate site details and managing the platform access of users associated with the trials supported by the SMO.",
  "Registering or managing a hospital/site on the platform does not imply ownership of the hospital or authority to act on behalf of the hospital beyond the activities for which the SMO is authorized.",
  "Invite only authorized users and provide access based on their roles, responsibilities, site affiliation, and trial involvement.",
  "Review, update, or remove user access when a user's role, site affiliation, or trial involvement changes.",
  "Protect confidential, clinical trial, site, investigator, and patient-related information available through the platform.",
  "Do not share your login credentials, password, or OTP with anyone.",
  "Administrative activities performed through your account may be recorded for security and audit purposes.",
];

// An organization from GET /api/organizations. Continue checks whether the typed
// name matches an onboarded org; existing orgs surface their platform contact,
// while new org registrations proceed directly.
interface PlatformContact { name: string; designation?: string; email?: string; phone?: string }
interface Org { id: string; name: string; type: string; address?: string; contact?: string; email?: string; website?: string; status?: string; platform_contact?: PlatformContact }
type SmoHospital = { name: string; address: string; type: string; googlePlaceId?: string };
// Map the selected role to the org `type` used to narrow the directory search.
function orgTypeFor(role?: string): string | undefined {
  if (role === "sponsor") return "sponsor";
  if (role === "cro") return "cro";
  if (role === "smo") return "smo";
  if (role === "site" || role === "pi" || role === "crc") return "site";
  return undefined;
}
// ── Shared primitives ───────────────────────────────────────────────────────
function Field({ label, required, error, children }: { label: string; required?: boolean; error?: string; children: React.ReactNode }) {
  return (
    <View style={{ marginBottom: spacing.md }}>
      <Text style={f.label}>{label}{required && <Text style={{ color: colors.accent }}> *</Text>}</Text>
      {children}
      {!!error && <Small color={colors.destructive} style={f.fieldError}>{error}</Small>}
    </View>
  );
}
function Input(props: React.ComponentProps<typeof TextInput>) {
  return <TextInput autoComplete="off" importantForAutofill="no" textContentType="none" selectionColor={colors.primary} cursorColor={colors.primary} placeholderTextColor={colors.mutedFg + "99"} {...props} style={[f.input, props.style]} />;
}
function SectionRow({ title }: { title: string }) {
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 12, paddingTop: spacing.sm, marginBottom: spacing.md }}>
      <Eyebrow>{title}</Eyebrow>
      <View style={{ flex: 1, height: 1, backgroundColor: colors.border }} />
    </View>
  );
}
function Select({ value, placeholder, options, onChange }: { value: string; placeholder: string; options: string[]; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Pressable onPress={() => setOpen(true)} style={[f.input, { flexDirection: "row", alignItems: "center", justifyContent: "space-between" }]}>
        <Text style={{ fontSize: 15, color: value ? colors.foreground : colors.mutedFg + "99", fontFamily: fonts.regular }}>{value || placeholder}</Text>
        <ChevronDown size={18} color={colors.mutedFg} />
      </Pressable>
      <Modal visible={open} transparent animationType="fade" onRequestClose={() => setOpen(false)}>
        <Pressable style={f.selectOverlay} onPress={() => setOpen(false)}>
          <View style={f.selectSheet}>
            <ScrollView style={f.selectOptions} showsVerticalScrollIndicator={false} nestedScrollEnabled>
              {options.map((o) => {
              const on = o === value;
              return (
                <Pressable key={o} onPress={() => { onChange(o); setOpen(false); }} style={[f.selectItem, on && { backgroundColor: colors.primary }]}>
                  <Text style={{ fontSize: 15, color: on ? colors.primaryFg : colors.foreground, fontFamily: on ? fonts.semibold : fonts.regular }}>{o}</Text>
                  {on && <Check size={16} color={colors.primaryFg} strokeWidth={3} />}
                </Pressable>
              );
              })}
            </ScrollView>
          </View>
        </Pressable>
      </Modal>
    </>
  );
}

// Centered modal card — the shared shell for responsibilities / declaration / terms / org-check.
function ModalCard({ visible, onClose, dismissible = true, children }: { visible: boolean; onClose: () => void; dismissible?: boolean; children: React.ReactNode }) {
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <View style={f.cardOverlay}>
        {/* Full-screen backdrop catches taps to dismiss; the card sits above it as a
            plain View so the inner ScrollView keeps its scroll gesture (Android). */}
        {dismissible && <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />}
        <View style={f.card}>
          <Rise delay={0} distance={12} duration={320} style={{ maxHeight: "100%" }}>{children}</Rise>
        </View>
      </View>
    </Modal>
  );
}
function ModalHead({ icon, eyebrow, title, subtitle }: { icon: React.ReactNode; eyebrow: string; title: string; subtitle?: string }) {
  return (
    <View style={{ paddingHorizontal: spacing.lg, paddingTop: spacing.lg, paddingBottom: spacing.md, alignItems: "center" }}>
      <View style={f.iconBadge}>{icon}</View>
      <Eyebrow color={colors.accent} style={{ marginBottom: 4 }}>{eyebrow}</Eyebrow>
      <Text style={{ fontFamily: fonts.heading, fontSize: 20, color: colors.foreground, textAlign: "center" }}>{title}</Text>
      {subtitle ? <Small style={{ marginTop: 6, textAlign: "center", lineHeight: 20 }}>{subtitle}</Small> : null}
    </View>
  );
}

function SmoHospitalFields({
  hospital,
  submitted,
  onUpdate,
}: {
  hospital: SmoHospital;
  submitted: boolean;
  onUpdate: (patch: Partial<SmoHospital>) => void;
}) {
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [lookupError, setLookupError] = useState("");
  const {
    predictions,
    searching,
    hasSearched,
    searchError,
    selectingPlaceId,
    getAddress,
  } = useHospitalPlaces(hospital.name, showSuggestions);

  const selectGoogleHospital = async (prediction: GoogleHospitalPrediction) => {
    setLookupError("");
    onUpdate({ name: prediction.name });
    try {
      const details = await getAddress(prediction);
      onUpdate({ name: prediction.name, address: details.address, googlePlaceId: details.placeId });
    } catch {
      setLookupError("Address could not be loaded. Please enter it manually.");
    } finally {
      setShowSuggestions(false);
    }
  };

  return (
    <>
      <Field label="Hospital / Site Name" required error={submitted && !hospital.name.trim() ? "Hospital / site name is required." : undefined}>
        <Input
          value={hospital.name}
          onChangeText={(name) => {
            onUpdate({ name: sanitizeOrgName(name), googlePlaceId: undefined });
            setLookupError("");
            setShowSuggestions(true);
          }}
          onFocus={() => setShowSuggestions(true)}
          placeholder="Search or enter hospital name"
        />
        {showSuggestions && hospital.name.trim().length >= 2 && (searching || predictions.length > 0 || hasSearched || !!searchError) && (
          <View style={f.suggestBox}>
            {!!searchError ? (
              <View style={f.suggestRow}>
                <Small color={colors.destructive}>{searchError}</Small>
              </View>
            ) : !searching && hasSearched && predictions.length === 0 ? (
              <View style={f.suggestRow}>
                <Small>No matching hospitals found. You can enter the hospital manually.</Small>
              </View>
            ) : searching && predictions.length === 0 ? (
              <View style={f.suggestRow}>
                <ActivityIndicator size="small" color={colors.primary} />
                <Small style={{ marginLeft: 8 }}>Searching Google…</Small>
              </View>
            ) : (
              <>
                <View style={f.suggestGroupRow}>
                  <Text style={f.suggestGroupLabel}>Matching hospitals</Text>
                  {searching && <ActivityIndicator size="small" color={colors.primary} />}
                </View>
                {predictions.map((prediction) => (
                  <Pressable
                    key={prediction.place_id}
                    onPress={() => void selectGoogleHospital(prediction)}
                    disabled={!!selectingPlaceId}
                    style={f.suggestRow}
                  >
                    <View style={f.suggestIcon}><Building2 size={15} color={colors.primary} /></View>
                    <View style={{ flex: 1 }}>
                      <Body weight="700" style={{ fontSize: 14 }}>{prediction.name}</Body>
                      <Small numberOfLines={2}>{prediction.description}</Small>
                    </View>
                    {selectingPlaceId === prediction.place_id && <ActivityIndicator size="small" color={colors.primary} />}
                  </Pressable>
                ))}
              </>
            )}
            {!searching && predictions.length > 0 && <Text style={f.googleAttribution}>Google Maps</Text>}
          </View>
        )}
      </Field>
      <Field label="Hospital / Site Location" required error={submitted && !hospital.address.trim() ? "Hospital / site location is required." : undefined}>
        <Input value={hospital.address} onChangeText={(address) => onUpdate({ address: sanitizeAddress(address) })} placeholder="Area, City" />
        {!!lookupError && <Small color={colors.destructive} style={f.fieldError}>{lookupError}</Small>}
      </Field>
    </>
  );
}

export default function Register() {
  const router = useRouter();
  // `org`/`email` may arrive prefilled when the user came from an accepted invite.
  const {
    role,
    org: orgParam,
    email: emailParam,
    fullName: fullNameParam,
    designation: designationParam,
    phone: phoneParam,
    dob: dobParam,
    gender: genderParam,
    language: languageParam,
    inviteToken,
  } = useLocalSearchParams<{
    role: string;
    org?: string;
    email?: string;
    fullName?: string;
    designation?: string;
    phone?: string;
    dob?: string;
    gender?: string;
    language?: string;
    inviteToken?: string;
  }>();
  const variant = variantFor(role);
  const isPatient = variant === "patient";
  const isInvite = !!inviteToken;
  // Matches backend required_channels(): patients and invited users verify
  // phone only (one verify screen); everyone else verifies email AND phone
  // (two verify screens), which adds one extra step to the whole flow.
  const registerTotalSteps = (isPatient || isInvite) ? 5 : 6;
  const isSmoAdminRegistration = !isInvite && role === "smo";
  const isOrganizationAdminRegistration = !isInvite && ["sponsor", "cro", "smo", "site"].includes(String(role));
  const activeRegistrationInstructions = isSmoAdminRegistration
    ? smoRegistrationInstructions
    : organizationRegistrationInstructions;
  const inviteEmailLocked = isInvite && !!emailParam;
  const orgNoun = variant === "site" ? "site" : variant === "smo" ? "SMO" : "organization";
  const entityLabel = labelMap[role as string] || "Sponsor";
  const needsSmoHospitals = variant === "smo" && !isInvite;

  const [fld, setFld] = useState<Record<string, string>>(() => {
    const base = initFields(variant);
    // Invite prefills are real user data (not demo defaults) — always apply them.
    if (orgParam) base.orgName = String(orgParam);
    if (emailParam) base.email = String(emailParam);
    if (fullNameParam) base.fullName = String(fullNameParam);
    if (designationParam) base.designation = String(designationParam);
    // An invited phone arrives as stored E.164 — show it under its own flag.
    if (phoneParam) base.phone = splitE164(String(phoneParam)).national;
    if (dobParam) base.dob = String(dobParam);
    if (genderParam) base.gender = String(genderParam);
    if (languageParam) base.language = String(languageParam);
    return base;
  });
  // Dialling country for the phone field, kept beside `fld` because `initFields`
  // blanks every shape key and this one needs a real default.
  const [phoneCountry, setPhoneCountry] = useState(
    () => (phoneParam ? splitE164(String(phoneParam)).country.code : DEFAULT_COUNTRY_CODE),
  );
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [submitted, setSubmitted] = useState(false);
  const [availabilityErrors, setAvailabilityErrors] = useState<RegistrationErrors>({});
  const [checkingAvailability, setCheckingAvailability] = useState(false);
  const [smoHospitals, setSmoHospitals] = useState<SmoHospital[]>([
    { name: "", address: "", type: "" },
  ]);
  const [departmentOptions, setDepartmentOptions] = useState(DEFAULT_DEPARTMENT_OPTIONS);
  const up = (k: string) => (v: string) => {
    setFld((s) => ({ ...s, [k]: v }));
    setTouched((current) => ({ ...current, [k]: true }));
  };
  // Switching country revalidates the typed number, so treat it as a phone edit.
  const onPhoneCountryChange = (code: string) => {
    setPhoneCountry(code);
    setTouched((current) => ({ ...current, phone: true }));
  };

  useEffect(() => {
    let ignore = false;
    api.get("/master-data/options", { params: { fieldType: "department" } })
      .then(({ data }) => {
        if (ignore) return;
        const published = Array.isArray(data?.values) ? data.values : [];
        const privateValues = Array.isArray(data?.private_values)
          ? data.private_values.map((item: any) => item?.value)
          : [];
        const combined = [...DEFAULT_DEPARTMENT_OPTIONS, ...published, ...privateValues]
          .filter((value): value is string => typeof value === "string" && !!value.trim());
        setDepartmentOptions(Array.from(new Set(combined)));
      })
      .catch(() => {
        // Keep the built-in catalogue when the optional master-data lookup fails.
      });
    return () => { ignore = true; };
  }, []);

  const [declarationAccepted, setDeclarationAccepted] = useState(() => isInvite);
  const [showInstructions, setShowInstructions] = useState(() => isOrganizationAdminRegistration);
  const [showDeclaration, setShowDeclaration] = useState(() => !isInvite && !isOrganizationAdminRegistration);
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [showTerms, setShowTerms] = useState(false);
  const [orgCheck, setOrgCheck] = useState<"exists" | null>(null);
  const [orgContactError, setOrgContactError] = useState("");
  // True while Continue performs the authoritative org/contact lookup.
  const [checkingOrg, setCheckingOrg] = useState(false);
  const [startingVerification, setStartingVerification] = useState(false);
  const [err, setErr] = useState("");

  // ── Live org directory lookup (debounced) ─────────────────────────────────
  const [orgMatches, setOrgMatches] = useState<Org[]>([]);
  const [existingOrgMatch, setExistingOrgMatch] = useState<Org | null>(null);
  const [orgSearching, setOrgSearching] = useState(false);
  const [showOrgSuggestions, setShowOrgSuggestions] = useState(false);
  const [googleOrgLookupError, setGoogleOrgLookupError] = useState("");
  const googleOrgEnabled = !isInvite && showOrgSuggestions && (variant === "site" || variant === "sponsor");
  const googleOrgScope = variant === "site" ? "hospitals" : "organizations";
  const {
    predictions: googleOrgMatches,
    searching: googleOrgSearching,
    hasSearched: googleOrgHasSearched,
    searchError: googleOrgSearchError,
    selectingPlaceId: selectingGoogleOrg,
    getAddress: getGoogleOrgAddress,
  } = useGooglePlaces(fld.orgName || "", googleOrgEnabled, googleOrgScope);
  useEffect(() => {
    // Sites use Google as their only visible suggestion source. Sponsor/CRO
    // registration combines Google company results with existing MTB matches.
    // Duplicate detection remains authoritative on Continue.
    if (isPatient || isInvite || variant === "site") {
      setOrgMatches([]);
      setOrgSearching(false);
      return;
    }
    const q = (fld.orgName || "").trim();
    if (q.length < 2) { setOrgMatches([]); setOrgSearching(false); return; }
    setOrgSearching(true);
    // Stale-response guard: a slow in-flight response for an older query must not
    // overwrite newer results. Cleanup flips `ignore` so late responses are dropped.
    let ignore = false;
    const t = setTimeout(async () => {
      try {
        const params: Record<string, string> = { search: q };
        const type = orgTypeFor(role);
        if (type) params.type = type;
        const res = await api.get("/organizations", { params });
        if (ignore) return;
        setOrgMatches(Array.isArray(res.data) ? res.data : []);
      } catch {
        if (!ignore) setOrgMatches([]);
      } finally {
        if (!ignore) setOrgSearching(false);
      }
    }, 300);
    return () => { ignore = true; clearTimeout(t); };
  }, [fld.orgName, role, isInvite, isPatient, variant]);

  // ── Terms & Conditions content (static — no network call, so this popup can
  // never be affected by backend load, slow endpoints like protocol
  // extraction, or connectivity issues) ─────────────────────────────────────
  const TERMS_BLOCKS: { heading: string; body: string }[] = [
    { heading: "1. Use of Application", body: "This platform helps research teams manage clinical-trial visit schedules, patient records, and communication." },
    { heading: "2. Data Privacy & Compliance", body: "All personal and clinical data is handled per applicable data-protection laws and used solely for clinical-trial management." },
    { heading: "3. Data Security", body: "We use encryption at rest and in transit. You are responsible for keeping your credentials confidential." },
    { heading: "4. Audit & Compliance", body: "All actions are logged for audit and may be shared with authorized regulators upon request." },
  ];
  const TERMS_VERSION = "2.1";
  const TERMS_EFFECTIVE = "01 Jan 2025";

  const validation = useMemo(() => {
    const result = validateRegistration(variant, { ...fld, phoneCountry });
    if (!isInvite) return result;
    const errors = { ...result.errors };
    delete errors.orgName;
    delete errors.orgAddress;
    delete errors.hospitalType;
    delete errors.role;
    if (isPatient && !fld.email.trim()) delete errors.email;
    return { ...result, errors, valid: Object.keys(errors).length === 0 };
  }, [fld, isInvite, isPatient, phoneCountry, variant]);

  // Check valid contact values while the user is still on this screen. The
  // backend repeats the check at submission to protect against race conditions.
  useEffect(() => {
    const email = validation.errors.email ? "" : validation.normalized.email;
    const phone = validation.errors.phone ? "" : validation.normalized.phone;
    if (!email && !phone) {
      setAvailabilityErrors({});
      setCheckingAvailability(false);
      return;
    }
    let ignore = false;
    setCheckingAvailability(true);
    setAvailabilityErrors({});
    const timer = setTimeout(async () => {
      try {
        const response = await api.post("/auth/register/check-availability", {
          email: email || undefined,
          phone: phone || undefined,
        });
        if (ignore) return;
        const next: RegistrationErrors = {};
        if (email && response.data?.email?.available === false) next.email = "Email ID is already registered.";
        if (phone && response.data?.phone?.available === false) next.phone = "Phone number is already registered.";
        setAvailabilityErrors(next);
      } catch {
        // The authoritative submission check remains the fallback if this
        // convenience request is interrupted by a temporary network error.
      } finally {
        if (!ignore) setCheckingAvailability(false);
      }
    }, 450);
    return () => { ignore = true; clearTimeout(timer); };
  }, [validation.errors.email, validation.errors.phone, validation.normalized.email, validation.normalized.phone]);

  const smoHospitalsValid = !needsSmoHospitals || (
    smoHospitals.length > 0
    && smoHospitals.every((hospital) => hospital.name.trim() && hospital.address.trim() && hospital.type)
  );
  const customDepartmentMissing = variant === "site"
    && fld.role === "PI"
    && fld.department === OTHER_DEPARTMENT
    && !fld.departmentOther?.trim();
  const canContinue = declarationAccepted && termsAccepted && validation.valid && smoHospitalsValid && !customDepartmentMissing && !checkingAvailability && !availabilityErrors.email && !availabilityErrors.phone;
  const fieldError = (key: keyof RegistrationErrors) =>
    availabilityErrors[key] || (submitted || touched[key] ? validation.errors[key] : undefined);
  const normalizeOrgName = (name: string) => name.trim().replace(/\s+/g, " ").toLowerCase();
  const matchedOrg = existingOrgMatch
    || orgMatches.find((o) => normalizeOrgName(o.name) === normalizeOrgName(fld.orgName || ""))
    || null;
  const declarationText = isPatient
    ? "I confirm that the information I have provided is true, accurate and complete, and I agree to comply with the platform's Terms of Use and Privacy Policy."
    : "I confirm that I am authorized to register and represent this organization, that the information provided is accurate, and I agree to comply with the platform's Terms of Use and Privacy Policy.";

  const proceed = async () => {
    const effectiveRole = role === "site"
      ? fld.role === "PI" ? "pi" : fld.role === "Administrative" ? "site" : "crc"
      : role || "patient";
    const registrationPayload: Record<string, any> = { ...validation.normalized };
    if (variant === "site") {
      if (fld.role === "PI" && fld.department === OTHER_DEPARTMENT) {
        registrationPayload.department = fld.departmentOther.trim();
        registrationPayload.department_is_custom = true;
      }
      delete registrationPayload.departmentOther;
    }
    if (needsSmoHospitals) {
      registrationPayload.hospitals = smoHospitals.map((hospital) => ({
        name: hospital.name.trim(),
        address: hospital.address.trim(),
        type: hospital.type,
        role: validation.normalized.role,
        google_place_id: hospital.googlePlaceId || undefined,
        address_source: hospital.googlePlaceId ? "google_places" : "manual",
      }));
    }
    if (isInvite) {
      delete registrationPayload.orgAddress;
      delete registrationPayload.hospitalType;
      delete registrationPayload.role;
      delete registrationPayload.department;
    }
    const payload: Record<string, any> = {
      ...registrationPayload,
      inviteToken: inviteToken || "",
    };
    // Every path — invited or not — verifies phone/email first, then answers
    // security questions, then sets a password. Start the pending
    // registration and send the OTP now; security questions are collected
    // afterward once verification succeeds (see verify-phone.tsx / verify-email.tsx).
    setStartingVerification(true);
    setErr("");
    try {
      const profile: Record<string, string> = {};
      const coreFields = new Set(["fullName", "email", "phone", "phoneCountry", "orgName", "inviteToken"]);
      Object.keys(payload).forEach((key) => {
        if (!coreFields.has(key) && payload[key]) profile[key] = payload[key];
      });
      const { data } = await api.post("/auth/register/start", {
        full_name: payload.fullName,
        role: effectiveRole,
        email: payload.email || undefined,
        phone: payload.phone,
        organization: payload.orgName || undefined,
        profile,
        invite_token: payload.inviteToken,
        security_questions: [],
      });
      router.push({
        pathname: "/(auth)/verify-phone",
        params: {
          registration_id: data.registration_id,
          channels: JSON.stringify(data.channels),
          email: data.email || "",
          phone: data.phone || "",
          role: effectiveRole,
          invited: isInvite ? "1" : "",
        },
      });
    } catch (error: any) {
      setErr(error?.response?.data?.detail || "Could not start verification. Please try again.");
    } finally {
      setStartingVerification(false);
    }
  };

  const existingOrganizationFromCheck = async (data: any): Promise<Org | null> => {
    if (!data?.exists || !data?.organization) return null;
    let platformContact = data.platform_contact || undefined;
    // Older organizations may not have a designated public contact. Use the
    // platform support contract without exposing organization members.
    if (!platformContact?.email) {
      try {
        const supportResponse = await api.get("/support/contact");
        if (supportResponse.data?.email) {
          platformContact = {
            name: supportResponse.data.name || "MTB Platform Support",
            designation: "Platform Administrator",
            email: supportResponse.data.email,
            phone: supportResponse.data.phone || "",
          };
        }
      } catch {
        // The duplicate warning remains useful even without an email action.
      }
    }
    return { ...data.organization, platform_contact: platformContact };
  };

  const handleContinue = async () => {
    setSubmitted(true);
    setErr("");
    setOrgContactError("");
    if (!canContinue || checkingOrg) return;
    if (isPatient || isInvite) { proceed(); return; }
    setShowOrgSuggestions(false);
    const q = (fld.orgName || "").trim();

    // Continue performs an authoritative, cross-category duplicate check. The
    // backend normalizes case and whitespace and returns only a designated
    // organization's public administrator contact.
    setCheckingOrg(true);
    try {
      const res = await api.get("/organizations/registration-check", {
        params: {
          name: q,
          google_place_id: fld.googlePlaceId || undefined,
        },
      });
      setOrgSearching(false);
      const fresh = await existingOrganizationFromCheck(res.data);
      if (fresh) {
        setExistingOrgMatch(fresh);
        if (variant !== "site") {
          setOrgMatches(current => [fresh, ...current.filter(org => org.id !== fresh.id)]);
        }
        setOrgCheck("exists");
      } else {
        // No match — this is a genuinely new organization. Confirming that
        // intent with a popup added a step to every first-time registration
        // for no benefit; only an existing-organization match needs a stop.
        proceed();
      }
    } catch {
      setErr("We couldn't check this organization right now. Please try again.");
    } finally {
      setCheckingOrg(false);
    }
  };

  const onOrgNameChange = (raw: string) => {
    const v = sanitizeOrgName(raw);
    setFld((s) => ({ ...s, orgName: v, googlePlaceId: "", addressSource: "manual" }));
    setTouched((current) => ({ ...current, orgName: true }));
    setExistingOrgMatch(null);
    setGoogleOrgLookupError("");
    setShowOrgSuggestions(true);
  };
  const pickOrg = (o: Org) => {
    setFld((s) => ({ ...s, orgName: o.name, orgAddress: o.address || s.orgAddress }));
    setTouched((current) => ({ ...current, orgName: true, orgAddress: true }));
    setExistingOrgMatch(o);
    setShowOrgSuggestions(false);
  };
  const pickGoogleOrg = async (prediction: GooglePlacePrediction) => {
    setGoogleOrgLookupError("");
    setExistingOrgMatch(null);
    setFld((current) => ({ ...current, orgName: prediction.name }));
    setTouched((current) => ({ ...current, orgName: true }));
    try {
      const details = await getGoogleOrgAddress(prediction);
      setFld((current) => ({
        ...current,
        orgName: prediction.name,
        orgAddress: details.address,
        googlePlaceId: details.placeId,
        addressSource: "google_places",
      }));
      setTouched((current) => ({ ...current, orgAddress: true }));
      // Keep the MTB directory private: check the stable Place ID immediately
      // and surface only an exact duplicate warning, never a directory list.
      setCheckingOrg(true);
      try {
        const response = await api.get("/organizations/registration-check", {
          params: {
            name: prediction.name,
            google_place_id: details.placeId,
          },
        });
        const existing = await existingOrganizationFromCheck(response.data);
        if (existing) {
          setExistingOrgMatch(existing);
          setOrgCheck("exists");
        }
      } catch {
        setGoogleOrgLookupError(
          "Address loaded, but MTB could not verify this organization yet. It will be checked again when you continue.",
        );
      } finally {
        setCheckingOrg(false);
      }
    } catch {
      setGoogleOrgLookupError("Address could not be loaded. Please enter it manually.");
    } finally {
      setShowOrgSuggestions(false);
    }
  };
  const updateSmoHospital = (index: number, patch: Partial<SmoHospital>) => {
    setSmoHospitals((current) => current.map((hospital, i) => i === index ? { ...hospital, ...patch } : hospital));
  };
  const addSmoHospital = () => {
    setSmoHospitals((current) => [...current, { name: "", address: "", type: "" }]);
  };
  const removeSmoHospital = (index: number) => {
    setSmoHospitals((current) => current.filter((_, i) => i !== index));
  };

  const contactPlatformAdmin = async () => {
    const email = matchedOrg?.platform_contact?.email?.trim();
    if (!email) return;
    const subject = encodeURIComponent(`Access request for ${matchedOrg?.name || "registered organization"}`);
    const body = encodeURIComponent(
      `Hello,\n\nI need assistance or access to the existing ${matchedOrg?.name || "organization"} account on MTB.\n\nThank you.`,
    );
    try {
      await Linking.openURL(`mailto:${email}?subject=${subject}&body=${body}`);
    } catch {
      setOrgContactError(`Unable to open your email application. Please email ${email} directly.`);
    }
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <AuthHeader eyebrow={`Step 2 of ${registerTotalSteps}`} title="Tell us about you" onBack={() => router.back()} step={2} totalSteps={registerTotalSteps} />

        <ScrollView contentContainerStyle={{ paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.lg }} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          <Rise delay={150}>
            <View style={f.entityBadge}>
              <View style={f.entityIcon}><Building2 size={18} color={colors.accent} /></View>
              <View>
                <Text style={f.entityKicker}>Registering as</Text>
                <Text style={f.entityLabel}>{entityLabel}</Text>
              </View>
            </View>
          </Rise>

          <Rise delay={200}>
            {/* Common identity fields */}
            <Field label="Full Name" required error={fieldError("fullName")}><Input value={fld.fullName} onChangeText={(v) => up("fullName")(sanitizeName(v))} style={fieldError("fullName") && f.inputError} /></Field>
            {!isPatient && <Field label="Designation" required error={fieldError("designation")}><Input value={fld.designation} onChangeText={(v) => up("designation")(sanitizeDesignation(v))} style={fieldError("designation") && f.inputError} /></Field>}

            {isPatient ? (
              <>
                <Field label="Phone Number" required error={fieldError("phone")}>
                  <PhoneField
                    value={fld.phone}
                    onChangeText={up("phone")}
                    countryCode={phoneCountry}
                    onChangeCountry={onPhoneCountryChange}
                    error={!!fieldError("phone")}
                  />
                </Field>
                <Field
                  label={isInvite ? "Email ID (Optional)" : "Email ID"}
                  required={!isInvite}
                  error={fieldError("email")}
                >
                  <Input
                    value={fld.email}
                    onChangeText={inviteEmailLocked ? undefined : up("email")}
                    editable={!inviteEmailLocked}
                    keyboardType="email-address"
                    autoCapitalize="none"
                    textContentType="username"
                    autoComplete="email"
                    importantForAutofill="yes"
                    placeholder="patient@example.com"
                    style={[inviteEmailLocked && f.readOnlyInput, fieldError("email") && f.inputError]}
                  />
                  {inviteEmailLocked && <Small color={colors.mutedFg} style={f.lockedHint}>Fixed by your invitation.</Small>}
                </Field>
                <View style={{ flexDirection: "row", gap: 12 }}>
                  <View style={{ flex: 1 }}>
                    <Field label="Date of Birth" required error={fieldError("dob")}><Input value={fld.dob} onChangeText={up("dob")} keyboardType="numbers-and-punctuation" placeholder="YYYY-MM-DD" style={fieldError("dob") && f.inputError} /></Field>
                  </View>
                  <View style={{ flex: 1 }}>
                    <Field label="Age (auto-calculated)">
                      <View style={[f.input, { justifyContent: "center", backgroundColor: colors.surface }]}>
                        <Text style={{ color: colors.mutedFg, fontFamily: fonts.medium, fontSize: 15 }}>{validation.age === null ? "—" : `${validation.age} yrs`}</Text>
                      </View>
                    </Field>
                  </View>
                </View>
                <Field label="Gender" required error={fieldError("gender")}>
                  <Select value={fld.gender} placeholder="Select gender" options={["Female", "Male", "Other", "Prefer not to say"]} onChange={up("gender")} />
                </Field>
                <Field label="Preferred Language">
                  <Select value={fld.language} placeholder="Select language" options={["English", "Hindi", "Tamil", "Telugu", "Kannada", "Marathi"]} onChange={up("language")} />
                </Field>
              </>
            ) : (
              <>
                <Field label="Email ID" required error={fieldError("email")}>
                  <Input
                    value={fld.email}
                    onChangeText={inviteEmailLocked ? undefined : up("email")}
                    editable={!inviteEmailLocked}
                    keyboardType="email-address"
                    autoCapitalize="none"
                    textContentType="username"
                    autoComplete="email"
                    importantForAutofill="yes"
                    style={[inviteEmailLocked && f.readOnlyInput, fieldError("email") && f.inputError]}
                  />
                  {inviteEmailLocked && <Small color={colors.mutedFg} style={f.lockedHint}>Fixed by your invitation.</Small>}
                </Field>
                <Field label="Phone Number" required error={fieldError("phone")}>
                  <PhoneField
                    value={fld.phone}
                    onChangeText={up("phone")}
                    countryCode={phoneCountry}
                    onChangeCountry={onPhoneCountryChange}
                    error={!!fieldError("phone")}
                  />
                </Field>
                {!isInvite && (variant === "site" || variant === "smo") && (
                  <Field label="Role" required error={fieldError("role")}>
                    <Select
                      value={fld.role}
                      placeholder="Select role"
                      options={["PI", "Research Team", "Administrative"]}
                      onChange={up("role")}
                    />
                  </Field>
                )}

                <SectionRow title="Organization" />
                <Field label={variant === "smo" ? "SMO Name" : "Organization Name"} required error={fieldError("orgName")}>
                  <Input
                    value={fld.orgName}
                    onChangeText={isInvite ? undefined : onOrgNameChange}
                    onFocus={isInvite ? undefined : () => setShowOrgSuggestions(true)}
                    editable={!isInvite}
                    placeholder={`Search or type your ${orgNoun} name`}
                    style={[isInvite && f.readOnlyInput, fieldError("orgName") && f.inputError]}
                  />
                  {isInvite && <Small color={colors.mutedFg} style={f.lockedHint}>Assigned by your invitation.</Small>}
                  {!isInvite && showOrgSuggestions && (fld.orgName || "").trim().length >= 2 && !matchedOrg && (orgSearching || googleOrgSearching || googleOrgHasSearched || !!googleOrgSearchError || orgMatches.length > 0 || googleOrgMatches.length > 0) && (
                    <View style={f.suggestBox}>
                      {(orgSearching || googleOrgSearching) && orgMatches.length === 0 && googleOrgMatches.length === 0 ? (
                        <View style={f.suggestRow}>
                          <ActivityIndicator size="small" color={colors.primary} />
                          <Small style={{ marginLeft: 8 }}>Searching…</Small>
                        </View>
                      ) : (
                        <>
                          {googleOrgEnabled && !!googleOrgSearchError && (
                            <View style={f.suggestRow}>
                              <Small color={colors.destructive}>{googleOrgSearchError}</Small>
                            </View>
                          )}
                          {googleOrgEnabled && !googleOrgSearchError && !googleOrgSearching && googleOrgHasSearched && googleOrgMatches.length === 0 && orgMatches.length === 0 && (
                            <View style={f.suggestRow}>
                              <Small>No matching {variant === "site" ? "hospitals" : "organizations"} found. You can enter the name manually.</Small>
                            </View>
                          )}
                          {variant !== "site" && orgMatches.length > 0 && <Text style={f.suggestGroupLabel}>Already on MTB</Text>}
                          {variant !== "site" && orgMatches.map((o) => (
                            <Pressable key={`mtb-${o.id}`} onPress={() => pickOrg(o)} style={f.suggestRow}>
                              <View style={f.suggestIcon}><Building2 size={15} color={colors.primary} /></View>
                              <View style={{ flex: 1 }}>
                                <Body weight="700" style={{ fontSize: 14 }}>{o.name}</Body>
                                {o.address ? <Small numberOfLines={1}>{o.address}</Small> : null}
                              </View>
                            </Pressable>
                          ))}
                          {googleOrgEnabled && googleOrgMatches.length > 0 && (
                            <View style={f.suggestGroupRow}>
                              <Text style={f.suggestGroupLabel}>Matching {variant === "site" ? "hospitals" : "organizations"}</Text>
                              {googleOrgSearching && <ActivityIndicator size="small" color={colors.primary} />}
                            </View>
                          )}
                          {googleOrgEnabled && googleOrgMatches.map((prediction) => (
                            <Pressable
                              key={`google-${prediction.place_id}`}
                              onPress={() => void pickGoogleOrg(prediction)}
                              disabled={!!selectingGoogleOrg}
                              style={f.suggestRow}
                            >
                              <View style={f.suggestIcon}><Building2 size={15} color={colors.primary} /></View>
                              <View style={{ flex: 1 }}>
                                <Body weight="700" style={{ fontSize: 14 }}>{prediction.name}</Body>
                                <Small numberOfLines={2}>{prediction.description}</Small>
                              </View>
                              {selectingGoogleOrg === prediction.place_id && <ActivityIndicator size="small" color={colors.primary} />}
                            </Pressable>
                          ))}
                          {googleOrgEnabled && googleOrgMatches.length > 0 && <Text style={f.googleAttribution}>Google Maps</Text>}
                        </>
                      )}
                    </View>
                  )}
                </Field>
                {!isInvite && (
                  <>
                    <Field label={variant === "smo" ? "SMO Address" : "Organization Address"} required error={fieldError("orgAddress")}>
                      <Input value={fld.orgAddress} onChangeText={(v) => up("orgAddress")(sanitizeAddress(v))} multiline placeholder="Building / Street, City, State, PIN" style={[{ height: 64, textAlignVertical: "top" }, fieldError("orgAddress") && f.inputError]} />
                      {!!googleOrgLookupError && <Small color={colors.destructive} style={f.fieldError}>{googleOrgLookupError}</Small>}
                    </Field>

                    {variant === "smo" && (
                      <View style={f.hospitalsSection}>
                        <SectionRow title="Hospitals" />
                        <Small style={f.hospitalsHelp}>Add the hospital / site locations managed by this SMO.</Small>
                        {smoHospitals.map((hospital, index) => (
                          <View key={index} style={f.hospitalCard}>
                            <View style={f.hospitalCardHead}>
                              <Eyebrow color={colors.mutedFg}>Hospital {String(index + 1).padStart(2, "0")}</Eyebrow>
                              {smoHospitals.length > 1 && (
                                <Pressable onPress={() => removeSmoHospital(index)} hitSlop={8}>
                                  <Small color={colors.destructive} style={{ fontFamily: fonts.semibold }}>Remove</Small>
                                </Pressable>
                              )}
                            </View>
                            <SmoHospitalFields
                              hospital={hospital}
                              submitted={submitted}
                              onUpdate={(patch) => updateSmoHospital(index, patch)}
                            />
                            <Field label="Type of Hospital" required error={submitted && !hospital.type ? "Hospital type is required." : undefined}>
                              <Select value={hospital.type} placeholder="Type of Hospital" options={["Private", "Government"]} onChange={(type) => updateSmoHospital(index, { type })} />
                            </Field>
                          </View>
                        ))}
                        <Pressable onPress={addSmoHospital} style={f.addHospitalButton}>
                          <Text style={f.addHospitalText}>+ Add another hospital</Text>
                        </Pressable>
                      </View>
                    )}

                    {variant === "site" && (
                      <>
                        <Field label="Hospital Type" required error={fieldError("hospitalType")}><Select value={fld.hospitalType} placeholder="Select hospital type" options={["Private", "Government"]} onChange={up("hospitalType")} /></Field>
                        {fld.role === "PI" && (
                          <>
                            <Field label="Department">
                              <Select
                                value={fld.department}
                                placeholder="Select department"
                                options={[...departmentOptions, OTHER_DEPARTMENT]}
                                onChange={(department) => {
                                  up("department")(department);
                                  if (department !== OTHER_DEPARTMENT) up("departmentOther")("");
                                }}
                              />
                            </Field>
                            {fld.department === OTHER_DEPARTMENT && (
                              <Field
                                label="Specify Department"
                                required
                                error={(submitted || touched.departmentOther) && customDepartmentMissing ? "Department is required." : undefined}
                              >
                                <Input
                                  value={fld.departmentOther}
                                  onChangeText={up("departmentOther")}
                                  placeholder="Enter department name"
                                  maxLength={120}
                                  style={customDepartmentMissing && submitted ? f.inputError : undefined}
                                />
                              </Field>
                            )}
                          </>
                        )}
                      </>
                    )}

                  </>
                )}
              </>
            )}

            <Pressable
              testID="terms-checkbox-row"
              onPress={() => (termsAccepted ? setTermsAccepted(false) : setShowTerms(true))}
              style={f.checkRow}
            >
              <View style={[f.checkbox, termsAccepted && { backgroundColor: colors.primary, borderColor: colors.primary }]}>
                {termsAccepted ? <Check size={13} color={colors.primaryFg} /> : null}
              </View>
              <Text style={{ flex: 1, fontFamily: fonts.regular, fontSize: 13, lineHeight: 19, color: colors.mutedFg }}>
                I have read and agree to the{" "}
                <Text style={{ color: colors.primary, fontFamily: fonts.semibold }} onPress={() => setShowTerms(true)}>
                  Terms &amp; Conditions
                </Text>
              </Text>
            </Pressable>
            {submitted && !termsAccepted ? (
              <Small color={colors.destructive} style={f.fieldError}>Please accept the Terms &amp; Conditions to continue.</Small>
            ) : null}

            {err ? <Small color={colors.destructive} style={{ marginTop: 8 }}>{err}</Small> : null}
          </Rise>
        </ScrollView>

        {/* Footer */}
        <View style={f.footer}>
          <Springy testID="register-submit-button" onPress={handleContinue} disabled={!canContinue || checkingOrg || startingVerification} style={[f.cta, canContinue ? { backgroundColor: colors.primary } : { backgroundColor: colors.surface }]}>
            {checkingOrg || startingVerification ? (
              <ActivityIndicator color={colors.primaryFg} />
            ) : (
              <Text style={{ fontFamily: fonts.bold, fontSize: 15, color: canContinue ? colors.primaryFg : colors.mutedFg }}>Continue</Text>
            )}
          </Springy>
        </View>
      </KeyboardAvoidingView>

      {/* Responsibilities — shown on entry for organizations */}
      <ModalCard visible={showInstructions} dismissible={false} onClose={() => router.back()}>
        <ModalHead
          icon={<ClipboardCheck size={24} color={colors.primary} />}
          eyebrow="Before you register"
          title="Before You Register an Organization"
          subtitle={isSmoAdminRegistration
            ? "Please read the following before proceeding:"
            : "Please proceed only if you are authorized or permitted to register this organization."}
        />
        <ScrollView
          style={f.organizationNoticeScroll}
          contentContainerStyle={f.organizationNoticeContent}
          showsVerticalScrollIndicator
          persistentScrollbar
          nestedScrollEnabled
        >
          {!isSmoAdminRegistration && (
            <Small color={colors.foreground} style={{ fontFamily: fonts.semibold, lineHeight: 20, marginBottom: spacing.md }}>
              By continuing, you understand that:
            </Small>
          )}
          {activeRegistrationInstructions.map((text, i) => (
            <View key={i} style={{ flexDirection: "row", gap: 12, marginBottom: 12 }}>
              <View style={f.numBadge}><Text style={{ fontFamily: fonts.heading, fontSize: 13, color: colors.primary }}>{i + 1}</Text></View>
              <Small style={{ flex: 1, lineHeight: 20 }}>{text}</Small>
            </View>
          ))}
          <View style={f.quote}>
            <Small color={colors.foreground} style={{ lineHeight: 21 }}>{declarationText}</Small>
          </View>
        </ScrollView>
        <View style={f.organizationNoticeFooter}>
          <Pressable onPress={() => { setDeclarationAccepted(true); setShowInstructions(false); }} style={[f.cta, { backgroundColor: colors.primary }]}>
            <Text style={{ fontFamily: fonts.bold, fontSize: 14, color: colors.primaryFg }}>I agree & continue</Text>
          </Pressable>
        </View>
      </ModalCard>

      {/* Declaration */}
      <ModalCard visible={showDeclaration} dismissible={false} onClose={() => router.back()}>
        <ModalHead icon={<ShieldCheck size={24} color={colors.primary} />} eyebrow="Please confirm" title="Declaration" subtitle="Read and confirm the statement below to continue." />
        <View style={{ paddingHorizontal: spacing.lg }}>
          <View style={f.quote}><Small color={colors.foreground} style={{ lineHeight: 21 }}>{declarationText}</Small></View>
        </View>
        <View style={{ padding: spacing.lg }}>
          <Pressable onPress={() => { setDeclarationAccepted(true); setShowDeclaration(false); }} style={[f.cta, { backgroundColor: colors.primary }]}>
            <Text style={{ fontFamily: fonts.bold, fontSize: 14, color: colors.primaryFg }}>I Agree & Confirm</Text>
          </Pressable>
        </View>
      </ModalCard>

      {/* Terms & Conditions */}
      <ModalCard visible={showTerms} onClose={() => setShowTerms(false)}>
        <ModalHead icon={<ShieldCheck size={24} color={colors.primary} />} eyebrow="Legal" title="Terms & Conditions" subtitle="Please read before accepting." />
        <ScrollView
          style={f.organizationNoticeScroll}
          contentContainerStyle={f.organizationNoticeContent}
          showsVerticalScrollIndicator
          persistentScrollbar
          nestedScrollEnabled
        >
          <View style={{ flexDirection: "row", justifyContent: "space-between", marginBottom: spacing.md }}>
            <Eyebrow color={colors.mutedFg}>Version {TERMS_VERSION}</Eyebrow>
            <Small style={{ fontFamily: fonts.mono }}>Effective {TERMS_EFFECTIVE}</Small>
          </View>
          {TERMS_BLOCKS.map((block, i) => (
            <View key={block.heading || i} style={{ marginBottom: 12 }}>
              <Text style={{ fontFamily: fonts.heading, fontSize: 14, color: colors.foreground, marginBottom: 4 }}>{block.heading}</Text>
              <Small style={{ lineHeight: 20 }}>{block.body}</Small>
            </View>
          ))}
        </ScrollView>
        <View style={f.organizationNoticeFooter}>
          <Pressable
            testID="terms-accept-button"
            onPress={() => { setTermsAccepted(true); setShowTerms(false); }}
            style={[f.cta, { backgroundColor: colors.primary }]}
          >
            <Text style={{ fontFamily: fonts.bold, fontSize: 14, color: colors.primaryFg }}>I Accept</Text>
          </Pressable>
        </View>
      </ModalCard>

      {/* Org-existence prompt — only shown when the org already exists; a
          brand-new organization proceeds straight through with no popup. */}
      <ModalCard visible={orgCheck !== null} onClose={() => setOrgCheck(null)}>
        {orgCheck === "exists" && matchedOrg ? (
          <>
            <ModalHead
              icon={<Building2 size={24} color={colors.primary} />}
              eyebrow="Organization found"
              title="Organization Already Registered"
              subtitle={`${matchedOrg.name} is already registered on the platform.`}
            />
            <View style={{ paddingHorizontal: spacing.lg }}>
              <View style={f.adminCard}>
                <Small color={colors.foreground} style={{ lineHeight: 20 }}>
                  Please contact the Platform Administrator for assistance or to request access to the existing organization account.
                </Small>
                <View style={f.platformAdminEmailRow}>
                  <Mail size={16} color={colors.primary} />
                  <View style={{ flex: 1 }}>
                    <Eyebrow color={colors.mutedFg}>Platform Admin Email</Eyebrow>
                    <Body weight="700" style={{ fontSize: 14, marginTop: 2 }}>
                      {matchedOrg.platform_contact?.email || "Not available"}
                    </Body>
                  </View>
                </View>
              </View>
              {!!orgContactError && <Small color={colors.destructive} style={{ lineHeight: 18, marginTop: spacing.sm }}>{orgContactError}</Small>}
            </View>
            <View style={f.orgExistsActions}>
              <Pressable onPress={() => { setOrgCheck(null); setOrgContactError(""); }} style={[f.cta, f.orgExistsClose]}>
                <Text style={{ fontFamily: fonts.bold, fontSize: 14, color: colors.foreground }}>Close</Text>
              </Pressable>
              <Pressable
                disabled={!matchedOrg.platform_contact?.email}
                onPress={() => void contactPlatformAdmin()}
                style={[f.cta, f.orgExistsContact, !matchedOrg.platform_contact?.email && { opacity: 0.45 }]}
              >
                <Text style={{ fontFamily: fonts.bold, fontSize: 14, color: colors.primaryFg, textAlign: "center" }}>Contact Platform Admin</Text>
              </Pressable>
            </View>
          </>
        ) : null}
      </ModalCard>
    </SafeAreaView>
  );
}

const f = StyleSheet.create({
  entityBadge: { flexDirection: "row", alignItems: "center", gap: 12, borderWidth: 1, borderColor: colors.accent + "40", backgroundColor: colors.secondary + "88", borderRadius: radii.md, paddingHorizontal: spacing.md, paddingVertical: 12, marginBottom: spacing.md },
  entityIcon: { width: 36, height: 36, borderRadius: 999, backgroundColor: colors.accent + "1F", alignItems: "center", justifyContent: "center" },
  entityKicker: { fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 1.2, textTransform: "uppercase", color: colors.mutedFg },
  entityLabel: { marginTop: 1, fontFamily: fonts.heading, fontSize: 17, color: colors.foreground },
  label: { fontFamily: fonts.semibold, fontSize: 13, color: colors.foreground, marginBottom: 6 },
  input: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15, color: colors.foreground, fontFamily: fonts.regular },
  readOnlyInput: { color: colors.mutedFg, backgroundColor: colors.surface },
  lockedHint: { marginTop: 5, fontSize: 12 },
  inputError: { borderColor: colors.destructive, backgroundColor: colors.destructive + "08" },
  fieldError: { marginTop: 5, lineHeight: 17 },
  suggestBox: { marginTop: 6, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.card, overflow: "hidden" },
  suggestRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 12, paddingVertical: 10, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  suggestIcon: { width: 30, height: 30, borderRadius: 8, backgroundColor: colors.primary + "1A", alignItems: "center", justifyContent: "center" },
  suggestGroupLabel: { paddingHorizontal: 12, paddingTop: 9, paddingBottom: 5, fontFamily: fonts.semibold, fontSize: 10, letterSpacing: 1, textTransform: "uppercase", color: colors.mutedFg, backgroundColor: colors.surface },
  suggestGroupRow: { minHeight: 30, flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingRight: 12, backgroundColor: colors.surface },
  googleAttribution: { paddingHorizontal: 12, paddingVertical: 7, textAlign: "right", fontFamily: fonts.medium, fontSize: 10, color: colors.mutedFg, backgroundColor: colors.surface },
  hospitalsSection: { marginTop: spacing.sm, marginBottom: spacing.md },
  hospitalsHelp: { marginTop: -8, marginBottom: spacing.md, lineHeight: 18 },
  hospitalCard: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.lg, backgroundColor: colors.card, padding: spacing.md, marginBottom: 12 },
  hospitalCardHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.md },
  addHospitalButton: { minHeight: 46, borderWidth: 1, borderStyle: "dashed", borderColor: colors.primary + "70", borderRadius: radii.pill, alignItems: "center", justifyContent: "center", backgroundColor: colors.primary + "08" },
  addHospitalText: { color: colors.primary, fontFamily: fonts.semibold, fontSize: 13 },
  checkRow: { flexDirection: "row", alignItems: "flex-start", gap: 12, marginTop: spacing.sm },
  checkbox: { width: 20, height: 20, borderRadius: 6, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, alignItems: "center", justifyContent: "center", marginTop: 2 },
  footer: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.background },
  cta: { paddingVertical: 15, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
  ghostBtn: { paddingVertical: 12, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
  // Select dropdown
  selectOverlay: { flex: 1, backgroundColor: colors.primaryDeep + "55", justifyContent: "center", paddingHorizontal: spacing.xl },
  selectSheet: { backgroundColor: colors.card, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, padding: 6 },
  selectOptions: { maxHeight: 420 },
  selectItem: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: spacing.md, paddingVertical: 14, borderRadius: radii.md },
  // Centered modal card
  cardOverlay: { flex: 1, backgroundColor: colors.primaryDeep + "80", alignItems: "center", justifyContent: "center", paddingHorizontal: spacing.md, paddingVertical: spacing.md },
  card: { backgroundColor: colors.background, width: "100%", maxWidth: 440, maxHeight: "96%", borderRadius: 28, overflow: "hidden", ...({ shadowColor: "#2E1B33", shadowOpacity: 0.2, shadowRadius: 24, shadowOffset: { width: 0, height: 12 }, elevation: 12 }) },
  iconBadge: { width: 48, height: 48, borderRadius: 16, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center", marginBottom: 12 },
  numBadge: { width: 24, height: 24, borderRadius: 999, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  quote: { borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, padding: spacing.md },
  organizationNoticeScroll: { flexShrink: 1 },
  organizationNoticeContent: { paddingHorizontal: spacing.lg, paddingBottom: spacing.sm },
  organizationNoticeFooter: { paddingHorizontal: spacing.lg, paddingTop: spacing.md, paddingBottom: spacing.lg, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.background },
  banner: { paddingHorizontal: spacing.md, paddingVertical: 8, borderRadius: radii.md },
  adminCard: { borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, padding: 14 },
  platformAdminEmailRow: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: spacing.md, paddingTop: spacing.md, borderTopWidth: 1, borderTopColor: colors.border },
  orgExistsActions: { flexDirection: "row", gap: 10, padding: spacing.lg },
  orgExistsClose: { flex: 0.75, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  orgExistsContact: { flex: 1.5, paddingHorizontal: spacing.sm, backgroundColor: colors.primary },
});
