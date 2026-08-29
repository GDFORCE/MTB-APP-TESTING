// ISO-3166 countries with their E.164 calling codes, used by the phone field so
// anyone — not only Indian users — can register. Stored as [name, iso2, dial]
// tuples to keep the table scannable; `COUNTRIES` expands it once at module load.
type Row = readonly [name: string, code: string, dial: string];

const ROWS: readonly Row[] = [
  ["Afghanistan", "AF", "93"], ["Åland Islands", "AX", "358"], ["Albania", "AL", "355"],
  ["Algeria", "DZ", "213"], ["American Samoa", "AS", "1684"], ["Andorra", "AD", "376"],
  ["Angola", "AO", "244"], ["Anguilla", "AI", "1264"], ["Antigua and Barbuda", "AG", "1268"],
  ["Argentina", "AR", "54"], ["Armenia", "AM", "374"], ["Aruba", "AW", "297"],
  ["Australia", "AU", "61"], ["Austria", "AT", "43"], ["Azerbaijan", "AZ", "994"],
  ["Bahamas", "BS", "1242"], ["Bahrain", "BH", "973"], ["Bangladesh", "BD", "880"],
  ["Barbados", "BB", "1246"], ["Belarus", "BY", "375"], ["Belgium", "BE", "32"],
  ["Belize", "BZ", "501"], ["Benin", "BJ", "229"], ["Bermuda", "BM", "1441"],
  ["Bhutan", "BT", "975"], ["Bolivia", "BO", "591"], ["Bosnia and Herzegovina", "BA", "387"],
  ["Botswana", "BW", "267"], ["Brazil", "BR", "55"], ["British Indian Ocean Territory", "IO", "246"],
  ["British Virgin Islands", "VG", "1284"], ["Brunei", "BN", "673"], ["Bulgaria", "BG", "359"],
  ["Burkina Faso", "BF", "226"], ["Burundi", "BI", "257"], ["Cambodia", "KH", "855"],
  ["Cameroon", "CM", "237"], ["Canada", "CA", "1"], ["Cape Verde", "CV", "238"],
  ["Caribbean Netherlands", "BQ", "599"], ["Cayman Islands", "KY", "1345"],
  ["Central African Republic", "CF", "236"], ["Chad", "TD", "235"], ["Chile", "CL", "56"],
  ["China", "CN", "86"], ["Christmas Island", "CX", "61"], ["Cocos (Keeling) Islands", "CC", "61"],
  ["Colombia", "CO", "57"], ["Comoros", "KM", "269"], ["Congo (Brazzaville)", "CG", "242"],
  ["Congo (Kinshasa)", "CD", "243"], ["Cook Islands", "CK", "682"], ["Costa Rica", "CR", "506"],
  ["Côte d'Ivoire", "CI", "225"], ["Croatia", "HR", "385"], ["Cuba", "CU", "53"],
  ["Curaçao", "CW", "599"], ["Cyprus", "CY", "357"], ["Czechia", "CZ", "420"],
  ["Denmark", "DK", "45"], ["Djibouti", "DJ", "253"], ["Dominica", "DM", "1767"],
  ["Dominican Republic", "DO", "1809"], ["Ecuador", "EC", "593"], ["Egypt", "EG", "20"],
  ["El Salvador", "SV", "503"], ["Equatorial Guinea", "GQ", "240"], ["Eritrea", "ER", "291"],
  ["Estonia", "EE", "372"], ["Eswatini", "SZ", "268"], ["Ethiopia", "ET", "251"],
  ["Falkland Islands", "FK", "500"], ["Faroe Islands", "FO", "298"], ["Fiji", "FJ", "679"],
  ["Finland", "FI", "358"], ["France", "FR", "33"], ["French Guiana", "GF", "594"],
  ["French Polynesia", "PF", "689"], ["Gabon", "GA", "241"], ["Gambia", "GM", "220"],
  ["Georgia", "GE", "995"], ["Germany", "DE", "49"], ["Ghana", "GH", "233"],
  ["Gibraltar", "GI", "350"], ["Greece", "GR", "30"], ["Greenland", "GL", "299"],
  ["Grenada", "GD", "1473"], ["Guadeloupe", "GP", "590"], ["Guam", "GU", "1671"],
  ["Guatemala", "GT", "502"], ["Guernsey", "GG", "44"], ["Guinea", "GN", "224"],
  ["Guinea-Bissau", "GW", "245"], ["Guyana", "GY", "592"], ["Haiti", "HT", "509"],
  ["Honduras", "HN", "504"], ["Hong Kong", "HK", "852"], ["Hungary", "HU", "36"],
  ["Iceland", "IS", "354"], ["India", "IN", "91"], ["Indonesia", "ID", "62"],
  ["Iran", "IR", "98"], ["Iraq", "IQ", "964"], ["Ireland", "IE", "353"],
  ["Isle of Man", "IM", "44"], ["Israel", "IL", "972"], ["Italy", "IT", "39"],
  ["Jamaica", "JM", "1876"], ["Japan", "JP", "81"], ["Jersey", "JE", "44"],
  ["Jordan", "JO", "962"], ["Kazakhstan", "KZ", "7"], ["Kenya", "KE", "254"],
  ["Kiribati", "KI", "686"], ["Kosovo", "XK", "383"], ["Kuwait", "KW", "965"],
  ["Kyrgyzstan", "KG", "996"], ["Laos", "LA", "856"], ["Latvia", "LV", "371"],
  ["Lebanon", "LB", "961"], ["Lesotho", "LS", "266"], ["Liberia", "LR", "231"],
  ["Libya", "LY", "218"], ["Liechtenstein", "LI", "423"], ["Lithuania", "LT", "370"],
  ["Luxembourg", "LU", "352"], ["Macao", "MO", "853"], ["Madagascar", "MG", "261"],
  ["Malawi", "MW", "265"], ["Malaysia", "MY", "60"], ["Maldives", "MV", "960"],
  ["Mali", "ML", "223"], ["Malta", "MT", "356"], ["Marshall Islands", "MH", "692"],
  ["Martinique", "MQ", "596"], ["Mauritania", "MR", "222"], ["Mauritius", "MU", "230"],
  ["Mayotte", "YT", "262"], ["Mexico", "MX", "52"], ["Micronesia", "FM", "691"],
  ["Moldova", "MD", "373"], ["Monaco", "MC", "377"], ["Mongolia", "MN", "976"],
  ["Montenegro", "ME", "382"], ["Montserrat", "MS", "1664"], ["Morocco", "MA", "212"],
  ["Mozambique", "MZ", "258"], ["Myanmar", "MM", "95"], ["Namibia", "NA", "264"],
  ["Nauru", "NR", "674"], ["Nepal", "NP", "977"], ["Netherlands", "NL", "31"],
  ["New Caledonia", "NC", "687"], ["New Zealand", "NZ", "64"], ["Nicaragua", "NI", "505"],
  ["Niger", "NE", "227"], ["Nigeria", "NG", "234"], ["Niue", "NU", "683"],
  ["Norfolk Island", "NF", "672"], ["North Korea", "KP", "850"], ["North Macedonia", "MK", "389"],
  ["Northern Mariana Islands", "MP", "1670"], ["Norway", "NO", "47"], ["Oman", "OM", "968"],
  ["Pakistan", "PK", "92"], ["Palau", "PW", "680"], ["Palestine", "PS", "970"],
  ["Panama", "PA", "507"], ["Papua New Guinea", "PG", "675"], ["Paraguay", "PY", "595"],
  ["Peru", "PE", "51"], ["Philippines", "PH", "63"], ["Poland", "PL", "48"],
  ["Portugal", "PT", "351"], ["Puerto Rico", "PR", "1787"], ["Qatar", "QA", "974"],
  ["Réunion", "RE", "262"], ["Romania", "RO", "40"], ["Russia", "RU", "7"],
  ["Rwanda", "RW", "250"], ["Saint Barthélemy", "BL", "590"], ["Saint Helena", "SH", "290"],
  ["Saint Kitts and Nevis", "KN", "1869"], ["Saint Lucia", "LC", "1758"],
  ["Saint Martin", "MF", "590"], ["Saint Pierre and Miquelon", "PM", "508"],
  ["Saint Vincent and the Grenadines", "VC", "1784"], ["Samoa", "WS", "685"],
  ["San Marino", "SM", "378"], ["São Tomé and Príncipe", "ST", "239"],
  ["Saudi Arabia", "SA", "966"], ["Senegal", "SN", "221"], ["Serbia", "RS", "381"],
  ["Seychelles", "SC", "248"], ["Sierra Leone", "SL", "232"], ["Singapore", "SG", "65"],
  ["Sint Maarten", "SX", "1721"], ["Slovakia", "SK", "421"], ["Slovenia", "SI", "386"],
  ["Solomon Islands", "SB", "677"], ["Somalia", "SO", "252"], ["South Africa", "ZA", "27"],
  ["South Korea", "KR", "82"], ["South Sudan", "SS", "211"], ["Spain", "ES", "34"],
  ["Sri Lanka", "LK", "94"], ["Sudan", "SD", "249"], ["Suriname", "SR", "597"],
  ["Svalbard and Jan Mayen", "SJ", "47"], ["Sweden", "SE", "46"], ["Switzerland", "CH", "41"],
  ["Syria", "SY", "963"], ["Taiwan", "TW", "886"], ["Tajikistan", "TJ", "992"],
  ["Tanzania", "TZ", "255"], ["Thailand", "TH", "66"], ["Timor-Leste", "TL", "670"],
  ["Togo", "TG", "228"], ["Tokelau", "TK", "690"], ["Tonga", "TO", "676"],
  ["Trinidad and Tobago", "TT", "1868"], ["Tunisia", "TN", "216"], ["Türkiye", "TR", "90"],
  ["Turkmenistan", "TM", "993"], ["Turks and Caicos Islands", "TC", "1649"],
  ["Tuvalu", "TV", "688"], ["Uganda", "UG", "256"], ["Ukraine", "UA", "380"],
  ["United Arab Emirates", "AE", "971"], ["United Kingdom", "GB", "44"],
  ["United States", "US", "1"], ["Uruguay", "UY", "598"], ["Uzbekistan", "UZ", "998"],
  ["Vanuatu", "VU", "678"], ["Vatican City", "VA", "379"], ["Venezuela", "VE", "58"],
  ["Vietnam", "VN", "84"], ["Wallis and Futuna", "WF", "681"], ["Western Sahara", "EH", "212"],
  ["Yemen", "YE", "967"], ["Zambia", "ZM", "260"], ["Zimbabwe", "ZW", "263"],
];

export interface Country {
  /** ISO-3166 alpha-2, e.g. "IN". Also the value persisted alongside the number. */
  code: string;
  name: string;
  /** Calling code without the leading "+", e.g. "91". */
  dial: string;
}

export const COUNTRIES: Country[] = ROWS
  .map(([name, code, dial]) => ({ name, code, dial }))
  .sort((a, b) => a.name.localeCompare(b.name));

export const DEFAULT_COUNTRY_CODE = "IN";

const BY_CODE: Record<string, Country> = {};
for (const c of COUNTRIES) BY_CODE[c.code] = c;

// Several territories share a calling code (44 → GB/GG/IM/JE). When we have only
// a number we cannot tell them apart, so each shared code resolves to its
// best-known "owner" and the rest are reachable by explicit selection.
const PRIMARY_BY_DIAL: Record<string, string> = {
  "1": "US", "7": "RU", "39": "IT", "44": "GB", "47": "NO", "61": "AU",
  "212": "MA", "262": "RE", "358": "FI", "590": "GP", "599": "CW",
};
const BY_DIAL: Record<string, Country> = {};
for (const c of COUNTRIES) {
  const owner = PRIMARY_BY_DIAL[c.dial];
  if (owner ? owner === c.code : !BY_DIAL[c.dial]) BY_DIAL[c.dial] = c;
}

export function getCountry(code?: string): Country {
  return BY_CODE[String(code || "").toUpperCase()] || BY_CODE[DEFAULT_COUNTRY_CODE];
}

/** Regional-indicator flag emoji for an alpha-2 code: "IN" → 🇮🇳. */
export function flagEmoji(code: string): string {
  return String(code || "")
    .toUpperCase()
    .replace(/[^A-Z]/g, "")
    .replace(/./g, (ch) => String.fromCodePoint(0x1f1e6 + ch.charCodeAt(0) - 65));
}

/**
 * Split a stored E.164 number back into its country and national part so an
 * existing value (invite prefill, saved profile) reopens with the right flag.
 * Calling codes are 1–4 digits, so we match longest-first.
 */
export function splitE164(value = ""): { country: Country; national: string } {
  const digits = String(value).replace(/\D/g, "");
  if (String(value).trim().startsWith("+") || digits.length > 10) {
    for (let len = 4; len >= 1; len--) {
      const hit = BY_DIAL[digits.slice(0, len)];
      if (hit && digits.length > len) return { country: hit, national: digits.slice(len) };
    }
  }
  return { country: getCountry(DEFAULT_COUNTRY_CODE), national: digits };
}

export function searchCountries(query: string): Country[] {
  const q = query.trim().toLowerCase().replace(/^\+/, "");
  if (!q) return COUNTRIES;
  return COUNTRIES.filter(
    (c) =>
      c.name.toLowerCase().includes(q)
      || c.code.toLowerCase() === q
      || c.dial.startsWith(q),
  );
}
