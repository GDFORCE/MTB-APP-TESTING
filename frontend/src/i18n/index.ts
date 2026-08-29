// i18n setup — English + Hindi to start. Add more languages by extending `resources`.
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import AsyncStorage from "@react-native-async-storage/async-storage";

export const APP_LOCALES = [
  { code: "en", label: "English" },
  { code: "hi", label: "Hindi — हिंदी" },
  { code: "ta", label: "Tamil — தமிழ்" },
  { code: "te", label: "Telugu — తెలుగు" },
  { code: "kn", label: "Kannada — ಕನ್ನಡ" },
  { code: "mr", label: "Marathi — मराठी" },
] as const;

export type AppLocale = typeof APP_LOCALES[number]["code"];

export function normalizeLocale(value?: string | null): AppLocale {
  const clean = (value || "").trim().toLowerCase();
  const exact = APP_LOCALES.find((locale) => locale.code === clean);
  if (exact) return exact.code;
  const byLabel = APP_LOCALES.find((locale) =>
    locale.label.toLowerCase() === clean
    || locale.label.toLowerCase().startsWith(`${clean} —`)
    || clean.startsWith(locale.label.split(" —")[0].toLowerCase())
  );
  return byLabel?.code || "en";
}

export function localeLabel(value?: string | null): string {
  const code = normalizeLocale(value);
  return APP_LOCALES.find((locale) => locale.code === code)?.label || "English";
}

const en = {
  welcome: { title: "Your trial, one sunrise at a time.", subtitle: "One warm place for sponsors, sites and patients to follow a clinical trial.", create: "Create an account", signIn: "Sign in", forgot: "Forgot password?" },
  common: { save: "Save", cancel: "Cancel", continue: "Continue", back: "Back", done: "Done", retry: "Retry", language: "Language" },
  dashboard: { hi: "Hi", welcomeBack: "Welcome back", yourProgress: "Your progress", visitsCompleted: "visits completed", nextVisit: "Next visit", visitSchedule: "Visit schedule", updates: "Updates", yourTrials: "Your trials", patients: "Patients", seeAll: "See all" },
  trial: { myTrial: "My Trial", myVisits: "My Visits", aboutTrial: "About this study", visits: "Visits", medications: "Medications", progress: "Progress", roadAhead: "The road ahead", contactPI: "Contact PI" },
  med: { todaysMeds: "Today's medications", taken: "Taken", notTaken: "Not taken", skip: "Skip", pending: "Pending", reminder: "Medication Reminder" },
  profile: { editProfile: "Edit profile", changePassword: "Change password", notifPrefs: "Notification preferences", termsConditions: "Terms & conditions", helpSupport: "Help & support", logout: "Log out" },
};

const hi = {
  welcome: { title: "आपका ट्रायल, हर सुबह एक नई शुरुआत।", subtitle: "स्पॉन्सर, साइट और मरीज़ों के लिए एक गर्मजोश जगह।", create: "अकाउंट बनाएँ", signIn: "साइन इन करें", forgot: "पासवर्ड भूल गए?" },
  common: { save: "सहेजें", cancel: "रद्द करें", continue: "जारी रखें", back: "वापस", done: "हो गया", retry: "पुनः प्रयास", language: "भाषा" },
  dashboard: { hi: "नमस्ते", welcomeBack: "वापस स्वागत है", yourProgress: "आपकी प्रगति", visitsCompleted: "मुलाक़ातें पूरी", nextVisit: "अगली मुलाक़ात", visitSchedule: "मुलाक़ात कार्यक्रम", updates: "अपडेट्स", yourTrials: "आपके ट्रायल्स", patients: "मरीज़", seeAll: "सभी देखें" },
  trial: { myTrial: "मेरा ट्रायल", myVisits: "मेरी मुलाक़ातें", aboutTrial: "इस अध्ययन के बारे में", visits: "मुलाक़ातें", medications: "दवाइयाँ", progress: "प्रगति", roadAhead: "आगे का सफ़र", contactPI: "PI से संपर्क करें" },
  med: { todaysMeds: "आज की दवाइयाँ", taken: "ली गई ✓", notTaken: "नहीं ली", skip: "छोड़ें", pending: "बाक़ी है", reminder: "दवा रिमाइंडर" },
  profile: { editProfile: "प्रोफ़ाइल एडिट करें", changePassword: "पासवर्ड बदलें", notifPrefs: "नोटिफ़िकेशन सेटिंग्स", termsConditions: "नियम और शर्तें", helpSupport: "मदद और सहायता", logout: "लॉग आउट" },
};

// i18next exposes its configured singleton through the default export; these
// instance methods are intentional despite the generic named-export warning.
// eslint-disable-next-line import/no-named-as-default-member
i18n.use(initReactI18next).init({
  // Languages awaiting translated resource packs intentionally inherit English
  // through fallbackLng, while retaining their canonical locale selection.
  resources: {
    en: { translation: en },
    hi: { translation: hi },
    ta: { translation: en },
    te: { translation: en },
    kn: { translation: en },
    mr: { translation: en },
  },
  lng: "en",
  fallbackLng: "en",
  interpolation: { escapeValue: false },
  compatibilityJSON: "v4",
});

// Restore saved language preference
(async () => {
  try {
    const saved = await AsyncStorage.getItem("app_lang");
    // eslint-disable-next-line import/no-named-as-default-member
    if (saved) i18n.changeLanguage(normalizeLocale(saved));
  } catch {}
})();

export async function setLanguage(lng: string): Promise<AppLocale> {
  const canonical = normalizeLocale(lng);
  // eslint-disable-next-line import/no-named-as-default-member
  await i18n.changeLanguage(canonical);
  try { await AsyncStorage.setItem("app_lang", canonical); } catch {}
  return canonical;
}

export default i18n;
