# Protocol Schedule Schema Analysis

This is the durable requirements log used to design the protocol schedule schema.
All 11 supplied protocols have now been reviewed. This evidence base is ready to drive schema and extraction-pipeline design.

## Shared principles

- Preserve the exact protocol wording and page-level evidence alongside normalized values.
- Do not invent timing, windows, visits, procedures, or defaults.
- Keep visit-level timing/windows separate from procedure-level timing/tolerances.
- Represent exact, ranged, minimum, maximum, approximate, conditional, and unresolved timing.
- Keep intervals such as washout periods separate from actual patient encounters.
- Preserve conflicting statements for review instead of silently choosing one.

## Protocol 1 — CRD/09

Source: `1  CRD-09_Protocol_Version 01_12Aug16_Final.pdf`

- 69 pages; Schedule of Events on page 64.
- Design: randomized two-treatment, two-period, two-sequence crossover study.
- Baseline anchor: Period I dosing on Day 1.
- Actual encounters: Screening; Period I Days 0–3; Period II Days 0–3; telephonic follow-up.
- Washout is an interval constraint, not a visit: at least 21 days between dosing days.
- Screening is within the ten days before first dosing; it is not necessarily exactly Day -10.
- Period II timing is minimum/earliest timing because the dosing gap is `at least 21 days`.
- Telephonic follow-up is relative: 30 days after the last study-drug administration.
- End-of-study assessment occurs after the final 48-hour PK sample on Period II Day 3.
- Check-in occurs on Day 0 at least 12 hours before dosing.
- Infusion duration is 30 minutes with a ±1-minute duration tolerance.
- PK schedule includes pre-dose and 5-minute through 48-hour timepoints.
- Pre-dose PK is within 5 minutes before dosing; in-house post-dose PK is within +2 minutes, not ±2 minutes.
- Vitals have a pre-dose one-hour constraint, general post-dose 30-minute tolerance, and a special 10-minute tolerance at 0.5 hours.
- Other rules include approximate injection-site monitoring timepoints and six-month chest X-ray validity.
- There is no general visit-level ±day window in this protocol.
- Annexure I carries an older displayed date than the main protocol and must be retained as a source-version conflict.

Schema requirements introduced:

- crossover periods and treatment sequences
- minimum-gap/washout constraints
- relative follow-up visits
- procedure timepoints, anchors, durations, and asymmetric tolerances
- housing/check-in constraints
- validity/lookback periods
- continuous safety-observation periods
- source-version conflicts

## Protocol 2 — BP11-301 Version 2.0

Source: `2a. Protocol_Amendment 1__V2.0_17Jan2024 (1).pdf`

- 100 pages; Schedule of Assessments on pages 49–50, clarified on pages 51–56.
- Design: Screening, Treatment Period 1, Treatment Period 2, rerandomization, and follow-up.
- Baseline anchor: Week 0 / Study Day 1.

| Visit | Protocol timing | Baseline offset | Visit window |
|---|---|---:|---:|
| 1 Screening | Up to Week -4 / Day -28 to -1 | range | none |
| 2 e-diary eligibility period | Week -1 / Day -7 to Day 0 | range | none |
| 3 Baseline | Week 0 / Study Day 1 | 0 | none |
| 4 | Week 2 / Day 15 | +14 | ±3 days |
| 5 | Week 4 / Day 29 | +28 | ±3 days |
| 6 | Week 8 / Day 57 | +56 | ±3 days |
| 7 | Week 12 / Day 85 | +84 | ±3 days |
| 8 | Week 16 / Day 113 | +112 | ±5 days |
| 9 | Week 20 / Day 141 | +140 | ±5 days |
| 10 | Week 24 / Day 169 | +168 | ±5 days |
| 11 telephone | Week 32 / Day 225 | +224 | ±7 days |
| 12 EOS | Week 40 / Day 281 | +280 | ±7 days |

- Normal EOS is Week 40, 20 weeks after the Week 20 last dose.
- Early-termination EOS uses an alternate rule: four weeks after the patient's last dose, with a ±7-day window.
- Week 12 crosses treatment periods: pre-dose assessments belong to TP1, while rerandomization, dosing, and post-dose monitoring belong to TP2.
- Procedures at dosing visits have an explicit order: patient-reported outcomes, vitals, ECG, blood sampling, rerandomization where applicable, dosing, then post-dose reaction monitoring.
- Patients remain onsite for at least 60 minutes after dosing for reaction monitoring.
- Conditional procedures include pregnancy testing by reproductive status, stool testing by two clinical conditions, locally conditional COVID testing, and visit-specific laboratory/PK/PD/immunogenicity sampling.
- E-diary entries recur twice daily and use seven-day pre-visit collection periods.
- Unscheduled assessments are allowed, especially for adverse-event follow-up.
- Rescreening may create a new patient ID and may extend Screening after approval.
- Treatment assignment branches at Baseline and again at Week 12, while visit timing remains shared.
- `Day -7 to Day 0` conflicts semantically with prose describing the seven days before Baseline; preserve both and flag possible off-by-one interpretation.

Schema requirements introduced:

- numbered visits and explicit study-week/study-day labels
- visit ranges and genuine symmetric visit windows
- encounter modality, including telephone and unscheduled encounters
- alternate timing rules for early termination
- activity-level treatment-period attribution within one visit
- procedure ordering relative to dosing
- conditional and recurring procedures
- diary/observation intervals
- randomization, rerandomization, and treatment-transition branches
- rescreening/repeated screening instances
- amendment and protocol-version precedence

## Protocol 3 — CG03-EOC India Version 1.0

Source: `CG03-EOC (ChemoID)_ Platinum Resistant Ovarian Protocol_India version 1.0_11May2020_Final -Signed.pdf`

- 100 pages; master Schedule of Study Procedures on pages 22–23, entry/treatment timing tables on pages 46–48, and detailed visit descriptions on pages 77–85.
- Design: Screening, event-driven pre-treatment visits T1–T4, regimen-dependent treatment beginning at T5, overlapping clinical/cycle and radiological follow-up, EOS/early termination, and later survival calls.
- T1 schedules the biopsy/fluid collection; T2 depends on pathology and sample viability; T3 randomizes after viable growth is confirmed; T4 reviews assay results for Arm 2; T3 and T4 may be telephone encounters. These are milestone/event-driven rather than fixed-day visits.
- T5 is Cycle 1 Day 1. Further treatment timing depends on 1 of 13 chemotherapy regimens selected after randomization.
- Regimens use 21- or 28-day cycles and may dose on Day 1 only, Days 1 and 8, Days 1 and 15, Days 1/8/15, or Days 1–5.
- Treatment is open-ended: at least 4 cycles are expected, while the actual count continues at investigator discretion until progression, unacceptable toxicity, intercurrent illness, consent withdrawal, or another stopping condition.
- A treatment cycle may be delayed up to 7 days for major life events without a protocol violation.
- An individual Day 1 chemotherapy dose generally has a -1/+1-day window. A Friday due date has a special calendar exception extending through Monday (-1/+3 days).
- Treatment holds for unrelated adverse events may last up to 14 days; longer holds require discussion with the Study Chair.
- Day 1 assessments commonly use lookback windows of 3 or 7 days. Day 8/15 assessments apply only to relevant weekly/multi-dose regimens.
- Imaging is time-based, not cycle-based: every 8 weeks ±7 days for the first year, then every 12 weeks ±7 days after the first year, with additional clinically indicated imaging.
- Imaging stops after confirmed RECIST progression. If treatment stops for a reason other than progression, imaging continues on schedule until progression.
- Follow-up visits are F1 Week 8, F2 Week 16, F3 Week 24, F4 Week 32, F5 Week 40, F6 Week 48, F7 Week 60, F8 Week 72, F9 Week 84, and F10 Week 96/EOS.
- General outpatient follow-up is stated as ±7–14 days, while imaging has a specific ±7-day window; these must remain separate rather than being collapsed into one visit window.
- F10 is followed by survival telephone calls 6 and 12 months later.
- Unscheduled standard-of-care visits are explicitly supported.
- Early termination reuses EOS procedures, but the text refers to “Follow-up Visit 12” while the detailed named sequence ends at F10; this is an internal visit-numbering conflict.
- F10 is labeled both Week 96 and “18 months,” which are not calendar-equivalent; preserve and flag the conflict.
- Regimen 13 is internally inconsistent: the summary table describes a 28-day Day 1/8/15 paclitaxel schedule, while later regimen text describes a 21-day Day 1 combination schedule.
- Conditional regimen procedures include MUGA/echo every 12 weeks for anthracycline therapy, periodic audiometry for cisplatin, and regimen-specific laboratory checks.
- Multi-drug treatment events require ordered procedures, such as gemcitabine before carboplatin, PLD before carboplatin, and pre/post hydration around cisplatin.
- Screening and pre-treatment assessments use reusable-result lookbacks ranging from 3 to 60 days rather than a single screening window.

Schema requirements introduced:

- event/milestone-driven visits without fixed offsets
- regimen-dependent schedule templates selected after randomization
- open-ended repeating cycles with minimum cycles and stopping conditions
- multiple intra-cycle dose-day patterns
- cycle delays, dose windows, treatment holds, and calendar/weekend exceptions
- overlapping cycle-based and independent time-based schedules
- continuation rules after treatment discontinuation
- procedure validity/lookback windows by assessment
- multi-drug ordering, duration, hydration, and premedication steps
- conditional regimen-specific monitoring
- relative post-EOS survival calls
- conflicting source rules requiring unresolved alternatives and adjudication

## Protocol 4 — CORONARY Final Study Protocol, 07 June 2010

Source: `CORONARY_Final Study Protocol_07JUN2010.pdf`

- 44 pages; study flowchart on page 17, visit rules on page 18, and Schedule of Follow-up on page 19.
- Design: preoperative assessment and randomization, CABG surgery, inpatient postoperative observations, discharge, and long-term follow-up.
- The primary clinical anchor is the actual CABG surgery date, not enrollment or a drug-dose baseline. Preserve the source labels `Pre-op`, `OR Day`, `Day 1`, and `Day 2`; the latter two mean postoperative days.
- Discharge is an event-driven encounter. It cannot be assigned a fixed study-day offset in advance.
- Fixed follow-up labels are Day 30, Month 6, Year 1, and yearly follow-ups after Year 1. The protocol does not state general visit-tolerance windows for these encounters.
- Month 6 and yearly visits after Year 1 are telephone visits. Day 30, Year 1, and the final follow-up are intended as clinic visits, but may be conducted by telephone when a participant cannot attend.
- Follow-up duration is cohort-dependent. Yearly telephone follow-ups continue until a common study end date, when the final follow-up is performed. The document gives examples from about 3 to 7 years per participant, averaging 5 years, so the final visit is not always simply `surgery date + 5 years`.
- The flowchart mentions Years 2, 3, 4, and 5 telephone follow-up “as needed,” while the schedule table groups `Yrs 2–4*` and separately lists `Final Visit`. These are compatible only when the final visit happens at Year 5; preserve the common-end-date rule as authoritative instead of inventing a fixed Year-5 visit.
- Procedures are activity-specific: CK-MB is preoperative and on postoperative Days 1 and 2; creatinine is preoperative, captured as peak postoperative, and measured at Year 1 and the 5-year/final visit; ECG is preoperative and at Day 30, with the table also indicating final follow-up.
- EuroQoL and neurocognitive testing are optional at the centre/participant level, but once selected they must continue at discharge, Day 30, Year 1, and final follow-up. This is a persistent conditional pathway, not an independent checkbox at every visit.
- Outcome-event collection continues through follow-up, and adverse-event collection continues until five years after surgery even where the common final visit timing differs.

Schema requirements introduced:

- surgery/procedure-date anchors and postoperative-day labels
- event-driven inpatient milestones such as discharge
- calendar month/year recurrence without converting months or years to fixed day counts
- cohort-wide common-end-date rules that generate patient-specific final visits
- preferred encounter modality with a permitted fallback modality
- persistent opt-in procedure bundles across later visits
- observation intervals that may end independently of the final encounter
- grouped repeated yearly visits with conditional occurrence

## Protocol 5 — Everolimus CR178-17 Version 1.0

Source: `Everolimus protocol-CR178-17 Version 1.0.pdf`

- 77 pages; Time and Events Schedule on pages 11–12 and detailed timing rules on pages 36–44 and 50–52.
- Design: a 21-day Screening phase, Day 0 randomization/check-in, Period I on Days 1–14, immediate crossover without washout, Period II on Days 15–28, and EOS on Day 29.
- Preserve the protocol's raw day labels separately from normalized offsets. If first dose on Day 1 is the application baseline (`offset 0`), Day 0 normalizes to `-1`, Day 6 to `+5`, Day 15 to `+14`, and Day 29 to `+28`; the displayed source label must still remain `Day 0`, `Day 6`, etc.
- Day 0 check-in must occur at least 12 hours before Day 1 dosing. This is a minimum relative constraint, not a visit window.
- Day 6 and Day 20 have genuine symmetric visit windows of ±1 day. The other scheduled study days have no stated visit-level day window.
- Study drug is administered once daily on Days 1–14 and with the alternate treatment on Days 15–28. Period II begins directly after the last Period I PK sample on Day 15; there is explicitly no washout.
- Day 1 and Day 15 establish a daily dosing clock. Home doses must occur at the same time with an allowed 30-minute time window. This is a recurring procedure-time window, not a ±day visit window.
- Dispensing creates supply intervals: Day 1 supplies home dosing for Days 2–7, Day 6 supplies through Day 11, Day 15 supplies Days 16–21, and Day 20 supplies through Day 25. Diary review/compliance occurs at the resupply and later housing visits.
- Day 11 and Day 25 are check-in/housing encounters for the subsequent in-clinic dosing days. Days 12–14 and Days 26–28 form multi-day residential blocks rather than ordinary isolated visits.
- Pre-dose steady-state samples occur on Days 1, 12, 13, 14, 15, 26, 27, and 28 within five minutes before dosing.
- Complete PK profiles on Days 14 and 28 contain 18 post-dose timepoints from 0.17 through 24 hours. General post-dose collection tolerance is `+3 minutes`, while the final 24-hour sample has a specific 10-minute constraint. Store directionality explicitly; `+3 minutes` must not be silently rewritten as `±3 minutes`.
- The Day 14 24-hour sample is collected on Day 15 before the first Period II dose. The Day 28 24-hour sample is collected on Day 29 before EOS assessments. These are cross-day procedure dependencies and ordering rules.
- When PK sampling coincides with vitals or a meal, the required order is blood sample, then vitals, then meal.
- Day 14 and Day 28 require at least 10 hours of fasting before dosing and 4 hours after dosing; Day 28 additionally restricts water for one hour before and after dosing.
- Vitals on Days 14 and 28 at 1 and 3 hours post-dose have ±30-minute tolerances, separate from the PK tolerances.
- Day 29 EOS occurs after the final sample and includes examination, vitals, ECG, safety laboratories, concomitant medication review, adverse-event review, and checkout.
- Premature withdrawal uses the EOS safety-procedure bundle but is triggered by withdrawal rather than Day 29. Investigator-discretion safety tests may also be unscheduled.

Schema requirements introduced:

- raw protocol day labels plus independently normalized baseline offsets
- adjacent crossover periods without a washout interval
- recurring daily home and in-clinic dosing instances
- time-of-day inheritance with minute-level tolerance
- dispensing/supply coverage and diary-compliance activities
- multi-day housing/confinement blocks
- dense intra-day PK timepoint series
- asymmetric and timepoint-specific procedure tolerances
- cross-day sample/dose/EOS dependencies and procedure ordering
- fasting, water, meal, and other preparation constraints
- reusable EOS procedure bundles for early withdrawal

## Protocol 6 — TRC160334 Phase II Protocol Version 1.0

Source: `P16 Protocol_Phase II_Ver 1.0.pdf`

- 100 pages; schematic schedule on pages 14–15, Activity Chart on pages 16–17, design rules on pages 34–36, and detailed visit descriptions on pages 53–60.
- Design: Screening, 12 weeks of randomized twice-daily treatment, one-week safety follow-up, and an extension follow-up for recurrence of ulcerative-colitis episodes.
- Visit 1 Screening spans Day -21 through Day 0 and may be split across several calendar dates. The text calls this both a maximum of 22 days and `Day -21 to Day 0`; preserve the raw inclusive range rather than converting it into one encounter.
- Screening contains dependencies: initial eligibility assessments precede endoscopy; endoscopy/biopsy must occur at least 5 and no more than 7 days before Day 1 randomization; a paper diary runs from endoscopy until the next visit for no more than 7 days.
- Day 1 is the randomization, baseline, and first-dose visit. Using Day 1 as the normalized baseline gives this visit offset 0 while retaining the source label `Day 1`.
- Visit 3 is Week 2 ±2 days, Visit 4 Week 4 ±2 days, Visit 5 Week 8 ±2 days, and Visit 6/EOT Week 12 ±2 days, all relative to randomization.
- Visit 7 is written `Week 13 +3 days`. This should be represented as a one-sided late allowance unless later evidence establishes that the plus sign was intended as symmetric. It must not automatically become `±3 days`.
- Visit 8/EOS is Week 37 ±7 days, normally 24 weeks after Visit 7. If the participant cannot attend within the window, the required recurrence information may be collected by telephone.
- Extension follow-up is also cohort-dependent: when the last enrolled participant completes Week 12, participants still in extension follow-up are called back as soon as possible and receive an early EOS visit. The last enrolled participant completes Week 13 follow-up but does not enter the 24-week extension.
- The treatment phase uses twice-daily dosing for 12 weeks. Morning dosing follows an overnight fast, breakfast is delayed for two hours, and evening dosing is preferably 12 hours after the morning dose with no food for two hours before or after it. Doses should remain at approximately the same time each day.
- IP is dispensed through the next scheduled visit with extra units covering the positive side of the ±2-day visit window. Supply quantity is derived from a future window, not a new patient-visit tolerance.
- Electronic diary collection is continuous during treatment and includes dosing, meal time, and disease symptoms; paper and electronic diaries have distinct phases and purposes.
- Stool collection is prepared at the preceding visit and performed from the first bowel movement on the next visit morning. This creates a cross-visit preparation/collection dependency.
- If a participant arrives nonfasting for scheduled laboratory work, the laboratory collection is deferred to a next-day fasting sub-visit rather than moving the entire visit.
- Unscheduled visits are permitted. Their activities depend on whether the visit concerns a UC episode, treatment-period UC symptoms, an adverse event, or another complaint.
- Early withdrawal procedures branch on treatment exposure: after at least four weeks, use the Visit 6/EOT bundle including endoscopy if feasible; before four weeks, capture Partial Mayo/IBDQ and use the Visit 7 safety bundle.
- Rescreening is allowed only for a new/subsequent active-disease episode and creates a new-subject workflow with new consent.

Schema requirements introduced:

- multi-date screening phases with internal ordered milestones
- one-sided visit windows
- cohort-triggered early final visits
- preferred clinic modality with telephone fallback
- recurring twice-daily treatment with relative time-of-day and meal rules
- supply calculations based on future visit windows
- phased paper/electronic diary streams
- cross-visit specimen preparation and collection dependencies
- procedure-only next-day sub-visits
- condition-specific unscheduled-visit templates
- exposure-dependent early-withdrawal procedure bundles
- episode-dependent rescreening

## Protocol 7 — Doxorubicin CR150-16 Version 1.0

Source: `protocol.pdf`

- 77 pages; Table of Events on pages 11–12, design and visit rules on pages 34–39, and PK timing rules on page 44.
- Design: a 20-day Screening phase and two-treatment, two-sequence crossover study. Period I is labeled Days 0–21 and Period II Days 28–49; doses occur on Day 1 and Day 29, exactly 28 days apart.
- Day 0 is randomization and evening check-in. Day 1 is first dosing and is the natural normalized baseline (`offset 0`), while the raw Day 0 label remains distinct (`offset -1`).
- The protocol describes 15 site encounters by grouping Day 0–2 and Day 28–30 as hospitalization blocks, followed by ambulatory PK encounters on Days 3, 5, 8, 11, 15, 21 and Days 31, 33, 36, 39, 43, 49. Hospitalization may be extended at investigator discretion.
- Period II check-in occurs on Day 28, alternate-treatment dosing on Day 29, and EOS on Day 49. The Day 22–27 gap is a non-encounter interval within the required 28-day dose-to-dose spacing; the document does not explicitly name it a washout.
- Each dose is an IV infusion. It starts at 1 mg/min for at least 10 minutes and, absent an infusion reaction, increases to finish over one hour. An infusion lasting beyond one hour ±5 minutes triggers withdrawal.
- Complete PK profiles contain pre-dose and dense intra-day samples through 25 hours. Pre-dose is approximately 30 minutes before dosing, with concurrent-event ordering of blood sample, then vitals, then meal.
- Ambulatory PK samples occur at 49, 97, 169, 241, 337, and 504 hours after each dose and have ±2-hour collection windows. These must remain dose-relative hour offsets even when a printed study-day label is also present.
- The synopsis assigns ±5 minutes to the 0.25-, 0.50-, 0.75-, and 1-hour samples, while the detailed PK section states a general `+3 minutes` post-dose window. Preserve both statements as conflicting evidence for reviewer adjudication; do not average or choose between them automatically.
- The 504-hour sample equals 21 full days after dosing. With Day 1/Day 29 as time zero, that corresponds arithmetically to Day 22/Day 50, but the protocol repeatedly labels it Day 21/Day 49. Preserve both the 504-hour source timing and printed day label and flag the mismatch rather than silently shifting it.
- Fasting is preferred. When a patient's health prevents fasting, a non-high-fat breakfast is permitted and treatment starts two hours later. This is a conditional preparation pathway.
- Antiemetic and dexamethasone premedication may be used at investigator discretion and, if used, must occur at least one hour before dosing.
- Withdrawal reuses the Day 49 EOS safety bundle, including examination, vitals, safety laboratories, ECG, adverse events, and concomitant medications. Unscheduled safety laboratory testing is also allowed.
- The Table of Events pages are dated 30 May 2017 while most of the protocol is dated 1 June 2017. This page-level version discrepancy must be retained with the source evidence.

Schema requirements introduced:

- hospitalization blocks counted as single encounters
- dose-relative ambulatory visits expressed in hours and mapped to raw study-day labels
- multiple simultaneous timing representations with consistency validation
- conflicting procedure tolerances requiring adjudication
- investigator-extendable encounter duration
- conditional fasting/nonfasting preparation branches
- conditional premedication with minimum lead time
- page-level document-version discrepancies

## Protocol 8 — NCS-CT-006-AL-BENZ Version 1.0

Source: `Protocol_Final_NCS-CT-006-AL-Benz_29 Jun 16.pdf`

- 73 pages; synopsis schedule on pages 16–17, study overview on pages 29–30, and detailed visit procedures on pages 45–50.
- Design: randomized, double-blind, parallel topical-acne study with 10 weeks of twice-daily home treatment.
- Visit 1 combines Screening, Baseline, eligibility confirmation, randomization, and dispensing on Day 0. There is no separate pre-baseline screening window.
- Visit 2 is Week 4 / Day 28 ±4 days, Visit 3 is Week 8 / Day 56 ±4 days, and Visit 4/EOT is Week 10 / Day 70 ±4 days. Each is explicitly calculated from the date of Visit 1.
- The protocol separately states a fixed 70-day treatment course and a Day 70 visit with a ±4-day window. Treatment duration and encounter window must be represented separately because the text does not say that treatment automatically shortens or extends when Visit 4 occurs early or late.
- Study product is self-applied at home twice daily, morning and evening, after washing, warm-water rinsing, and drying. The first dose is also taken at home; the container must not be opened at the study centre.
- Diary cards continuously record application date/time, missed applications, and adverse events. Product and diary are returned, reconciled, collected, and replaced at Visits 2 and 3, and finally collected at Visit 4.
- Protocol-compliant exposure is defined as 75%–125% of scheduled applications with no more than three consecutive missed days. These are adherence rules, not visit windows.
- The acne lesion count and IGA are performed at Baseline and every post-baseline visit. Local irritation assessments begin only after Baseline.
- Pregnancy testing is conditional on childbearing potential and is performed at each scheduled visit. Contraception continues for 30 days after the last administration but does not itself create a scheduled follow-up encounter.
- Unscheduled visits are allowed at any time. A continuing-participation unscheduled visit uses the Visit 4 assessment bundle except product/diary collection; if the participant is discontinued during it, it becomes an Early Discontinuation Visit and uses the full Visit 4 bundle.
- An adverse-event unscheduled visit may generate additional investigator-directed follow-up visits. Adverse events causing discontinuation continue to be followed until resolution, clinical insignificance, stabilization, or loss to follow-up.
- The file name contains `29 Jun 16`, while the title page and page headers identify Version 1.0 as dated 24 June 2016. Retain both metadata values and use the internal protocol date as document evidence pending confirmation of what the filename date represents.

Schema requirements introduced:

- combined screening/baseline/randomization encounters
- visit offsets explicitly anchored to another visit date
- fixed treatment duration separated from a flexible final-visit window
- recurring twice-daily topical application with preparation instructions
- diary/product dispensing, return, reconciliation, and replacement intervals
- quantitative adherence thresholds and consecutive-missed-dose rules
- unscheduled encounters that conditionally convert into early-discontinuation visits
- post-treatment obligations without a required visit
- filename-versus-internal document metadata discrepancies

## Protocol 9 — PICN CLR_10_13 Version 01, Amendment 02

Source: `Ptc_PICN_V1 A2.pdf`

- The PDF has 249 physical pages: the clinical protocol occupies the first 56 pages and is followed by the CTCAE Version 4.02 reference document. Only the protocol segment defines this study's schedule; the appended reference terminology must not be parsed as visit instructions.
- Schedule of Assessments appears on page 4 and is repeated in Appendix I on page 42; detailed sequence rules are on pages 22–25.
- Design: randomized, open-label, parallel oncology study with treatment every three weeks until disease progression or unacceptable toxicity.
- Screening is Day -7 to Day -1. The randomization/Cycle 1 dosing visit must occur no more than seven days after Screening when laboratory reports are available.
- Each cycle contains a treatment-administration visit followed by three intra-cycle visits. The narrative schedules IC-1 seven days after dosing, IC-2 seven days after IC-1, IC-3 seven days after IC-2, and the next-cycle visit within three days after IC-3 to allow central laboratory results to arrive.
- The chained seven-day visits plus a next cycle within three days are not fully equivalent to the separate statement that dosing occurs “every 3-weekly.” Preserve both recurrence descriptions and flag the cycle-start interpretation for review rather than imposing a fixed 21-day cycle.
- Before each later cycle, eligibility to dose depends on the latest IC-3 results. Dosing is held until ANC is at least 1.5 × 10^9/L and platelets at least 100 × 10^9/L, so actual cycle starts may move beyond their planned dates.
- Treatment is open-ended; there is no fixed number of cycles or calendar EOS. Treatment stops for progression, unacceptable toxicity, withdrawal, or another discontinuation condition.
- Imaging occurs at IC-3 in Cycles 2, 4, and 6, with additional event-driven imaging to confirm suspected progression. The imaging method selected at Screening must remain consistent for each tumor throughout follow-up.
- Study medication is administered as a 30-minute IV infusion with a ±5-minute duration tolerance. Start and stop times are recorded, and the injection site must avoid massage or undue movement for at least 30 minutes afterward.
- Premedication for hypersensitivity is explicitly not administered. This negative procedure requirement must be distinguishable from a procedure that was merely not mentioned.
- Dose changes branch by product, toxicity, recurrence, and G-CSF use. PICN permits no more than two reductions; additional reductions require withdrawal. Grade-based toxicity can delay or reduce later doses.
- Laboratory content varies by intra-cycle position: IC-1 and IC-2 contain hematology, while IC-3 adds chemistry, urinalysis, ECG, and chest imaging. Conditional microscopy follows positive urine protein or blood.
- The schedule calls follow-up “weekly,” referring to recurring intra-cycle assessment visits rather than a fixed post-treatment follow-up visit.
- Participants who discontinue should receive the final-visit examination and laboratory bundle where possible; unresolved adverse events continue to be followed to a final outcome or stable condition.

Schema requirements introduced:

- segmentation of an uploaded PDF into protocol and attached reference documents
- open-ended cycle recurrence with chained relative visits
- competing recurrence formulations requiring review
- lab-result-gated cycle starts and dynamically delayed visits
- selected-cycle procedures such as imaging on Cycles 2, 4, and 6
- consistent-method constraints across repeated assessments
- explicit prohibited/omitted procedures
- product- and toxicity-dependent dose-reduction state machines
- conditional reflex procedures based on test results

## Protocol 10 — Etrasimod APD334-202 Amendment 2.0

Source: `S-100 ARENA.pdf`

- 191 physical PDF pages; five Schedule of Assessment tables are on protocol pages 156–173. The document is a seamless Phase 2/3 study containing five linked substudies rather than one linear schedule.
- The maximum overall duration is 282 weeks: up to 28 days of Screening, up to 274 weeks across treatment periods, and four weeks of safety follow-up.
- SSA-P2 contains Day -28 to -1 Screening, Day 1 Baseline, a 14-week induction period, and a 52-week extension. Its scheduled labels include W1 ±1 day; W2, W4, W6, W10, W14, W15, End W15, and W20 ±3 days; and W28, W36, W44, W52, and W66 ±7 days.
- `Week 15` and `End of Week 15` are distinct visits associated with transitioning participants enrolled under Amendment 1.0 into Amendment 2.0. A participant's governing amendment and completed-visit history therefore affect which schedule applies; these labels must not be deduplicated merely because they share a week number.
- SS1-P2b and SS2-I use Day -28 to -1 Screening and a 14-week induction schedule: Day 1, W1 ±1 day, W2/W6/W10/W14 ±3 days. Week 14 response status chooses the next path.
- Week 14 responders may enter SS3-M. Nonresponders enter a six-week Extended Induction pathway with EIFD, EI-W1, and EI-W6 visits. EIFD occurs within one week ±3 days of W14 and may occur on the same day; same-day transitions do not repeat assessments.
- SS3-M begins with a Maintenance First Dose visit that may share the prior substudy's final date but must occur within seven days of it. Its 38-week schedule is MFD and M-6 ±3 days, followed by M-14, M-22, M-30, and M-38/Week 52 ±7 days.
- SS3-M contains responder and nonresponder cohorts with different assignments and stopping logic. Response, clinical improvement, prior treatment, selected dose, corticosteroid use, and loss of response all affect the participant's path.
- Loss-of-response evaluation is event-driven and cannot start before M-6. It requires worsening documented over the seven most recent diary days and exclusion of alternative causes. LOR visits may repeat but must be at least one week apart; confirmed LOR triggers a LOR First Dose visit after required ileocolonoscopy and may conditionally trigger dose escalation approximately one week later.
- SS4-E begins with an Extension First Dose visit that may be on the parent substudy's last visit date but no later than 14 days afterward. E-1 is conditional on source cohort and selected dose.
- The LTE combines two interleaved recurrences: nominal 12-week visits at Weeks 12, 24, 36, 64, 76, 88, 116, 128, 140, 168, 180, and 192, and annual visits at Weeks 52, 104, 156, and 208. The explicitly enumerated occurrences are authoritative; blindly expanding `every 12 weeks` would incorrectly add visits at annual boundaries.
- Early Termination occurs within seven days after the last dose and before new treatment. FU1 and FU2 occur 14 and 28 days after the last dose, each ±3 days. In SS4-E, FU visits are unnecessary if ET is already at least 28 days after the last dose.
- Baseline eligibility ileocolonoscopy must occur at least five days before Day 1 and after most other Screening assessments. Repeat endoscopy may be waived when a recent qualifying endoscopy is within a specified six-week or two-week reuse period, depending on context.
- First-dose cardiac monitoring creates hourly procedures at Hours 1, 2, 3, and 4 ±15 minutes, with ECG at the Hour 4 discharge assessment. PK samples use predose windows within 60 minutes and ordered dosing after the blood draw.
- Monthly pregnancy tests continue during non-visit months at home or through unscheduled site visits. Positive urine tests trigger serum confirmation. Symptom-triggered stool testing, ophthalmology, and pulmonary testing can create other unscheduled assessments.
- Daily eDiary collection begins on the first Screening day and supplies both scheduled efficacy calculations and the seven-day evidence interval used to trigger LOR evaluation.
- The 3-mg treatment path may include a first-week 2-mg dose before escalation, except when prior exposure permits immediate 3-mg dosing. Dose history across substudies therefore affects administration rules.

Schema requirements introduced:

- linked substudies with distinct local clocks and bounded same-day transitions
- participant-level amendment/version state and nonduplicate same-week visits
- outcome-driven branching between response, extended induction, maintenance, and LTE paths
- event-triggered loss-of-response workflows with prerequisites and minimum repeat intervals
- interleaved explicitly enumerated recurrence series
- conditional omission of follow-up based on elapsed time since last dose
- same-day transition deduplication of activities
- history-dependent dose escalation across treatment periods
- reusable recent-assessment rules with context-specific validity periods
- home assessments scheduled during non-visit months

## Protocol 11 — BP11-301 Version 2.1, India Amendment 1.1

Source: `S-103 Syneos Health.pdf`

- 100 pages; Schedule of Assessments on pages 49–50. This is a newer India-specific amendment of Protocol 2 (`BP11-301 Version 2.0`, dated 17 January 2024), not a separate study design.
- The Version 2.1 schedule table is textually identical to Version 2.0: Screening through Week 40, the same study-day labels, and the same ±3/±5/±7-day windows. The schedule must be version-linked rather than duplicated into unrelated templates.
- The unchanged normalized sequence is: Screening Day -28 to -1; Baseline Day 1; Weeks 2, 4, 8, 12, 16, 20, 24, 32, and 40; with Week 32 by telephone and Week 40 as EOS.
- All previously documented Protocol 2 rules remain applicable, including Week 12 activity-level treatment-period attribution, rerandomization before Week 12 dosing, procedure ordering, 60-minute post-dose monitoring, early-termination EOS four weeks after the last dose ±7 days, twice-daily diary intervals, and rescreening with a linked new patient ID.
- Version 2.1 identifies itself as `Protocol Amendment 1.1, Version 2.1 (India)` dated 14 November 2024 while retaining Version 2.0 in its lineage. Protocol selection therefore requires jurisdiction and effective-version metadata, not only protocol number.
- A verified amendment-level metadata change is the India enrollment cap: Version 2.0 limits India to 200 of 600 participants (33%), while Version 2.1 raises the cap to 300 of 600 (50%). This does not alter visit generation but proves that nonschedule constraints also require versioned evidence.
- When extraction encounters multiple versions of the same protocol, the reviewer should compare schedule structures and rule text explicitly, inherit only verified unchanged rules, and surface differences. It must not merge versions field-by-field without provenance.

Schema requirements introduced:

- protocol-family and amendment-lineage identifiers
- jurisdiction-specific active versions
- structural schedule comparison across amendments
- verified inheritance of unchanged schedule rules
- versioned nonschedule constraints stored separately from visit generation
- provenance for every inherited, changed, added, or removed rule

## Cumulative schema capability matrix

| Capability | P1 | P2 | P3 | P4 | P5 | P6 | P7 | P8 | P9 | P10 | P11 |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Exact visit offset | Yes | Yes | Some | Some | Yes | Yes | Yes | Yes | Some | Some | Yes |
| Visit timing range | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Minimum/maximum constraint | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Symmetric visit window | No | Yes | Yes | No | Yes | Yes | No | Yes | No | Yes | Yes |
| Asymmetric/special calendar window | No | No | Yes | No | No | Yes | No | No | Yes | Yes | No |
| Procedure-level tolerance | Yes | Yes | Yes | No | Yes | No | Conflict | No | Yes | Yes | Yes |
| Relative visit | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Event/milestone visit | No | No | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Washout/non-visit interval | Yes | No | No | No | None | No | Gap | No | No | No | No |
| Repeating periods/cycles | Yes | Yes | Open | Yearly | Yes | Daily | Yes | Daily | Open | Linked | Yes |
| Regimen/template selection | No | No | Yes | No | No | No | No | No | No | Yes | No |
| Treatment branch/transition | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Extensive | Yes |
| Activity-level period attribution | No | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Independent overlapping schedules | No | No | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | No |
| Telephonic/fallback modality | Yes | Yes | Yes | Yes | No | Yes | No | No | No | No | Yes |
| Unscheduled/event-triggered work | No | Yes | Yes | No | Yes | Yes | Yes | Yes | Yes | Extensive | Yes |
| Early-termination schedule | No | Yes | Yes | No | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Conditional procedures | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Extensive | Yes |
| Recurring diary/monitoring | No | Yes | Yes | AE | Yes | Yes | No | Yes | Labs | Yes | Yes |
| Treatment delay/hold rules | No | No | Yes | No | No | No | No | No | Yes | Yes | No |
| Stopping/continuation rules | No | Yes | Yes | Cohort | Yes | Cohort | Yes | Yes | Yes | Yes | Yes |
| Source/version conflict | Yes | Lineage | Yes | Ambiguous | No | Possible | Yes | Date | Yes | Amendment transition | Version lineage |
| Multi-day residential block | Yes | No | No | Yes | Yes | No | Yes | No | No | No | No |
| Time-of-day inheritance | No | No | No | No | Yes | Yes | No | Yes | No | Yes | No |
| Cross-visit/procedure dependency | Yes | Yes | Yes | No | Yes | Yes | Yes | Yes | Yes | Extensive | Yes |
| Cohort-triggered timing | No | No | No | Yes | No | Yes | No | No | No | Yes | No |
| PDF subdocument segmentation | No | No | No | No | No | No | No | No | Yes | No | No |
| Lab/result-gated scheduling | No | No | Yes | No | No | No | No | No | Yes | Yes | No |
| Amendment/jurisdiction selection | No | Yes | No | No | No | No | No | No | No | Yes | Yes |
| Same-day visit merging rules | No | No | No | No | No | No | No | No | No | Yes | No |
| Interleaved recurrence series | No | No | Yes | No | No | No | No | No | No | Yes | No |
