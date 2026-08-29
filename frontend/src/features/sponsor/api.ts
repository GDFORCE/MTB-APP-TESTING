import { api } from "@/src/api/client";
import type {
  SponsorDashboard,
  SponsorNotification,
  SponsorSite,
  SponsorSiteTrial,
  SponsorTrial,
  SponsorTrialDetail,
} from "./types";

const num = (value: unknown, fallback = 0) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const text = (value: unknown, fallback = "") =>
  typeof value === "string" && value.trim() ? value.trim() : fallback;

export function normalizeTrial(raw: any): SponsorTrial {
  const siteValue = Array.isArray(raw?.sites)
    ? raw.sites.length
    : raw?.sites ?? raw?.site_count ?? raw?.siteCount;
  return {
    id: text(raw?.id || raw?._id || raw?.protocol_id, "trial"),
    protocolId: text(raw?.protocolId || raw?.protocol_id || raw?.id, "Protocol"),
    title: text(raw?.title || raw?.name, "Untitled trial"),
    phase: text(raw?.phase),
    condition: text(raw?.condition || raw?.indication),
    drug: text(raw?.drug || raw?.intervention),
    status: text(raw?.status, "Active"),
    recruitmentStatus: text(raw?.recruitmentStatus || raw?.recruitment_status),
    enrolled: num(
      raw?.enrolled
      ?? raw?.enrolled_count
      ?? raw?.enrolledCount
      ?? raw?.randomized
      ?? raw?.randomized_count
      ?? raw?.subjects,
    ),
    randomized: num(raw?.randomized ?? raw?.randomized_count),
    target: num(raw?.target ?? raw?.target_enrollment ?? raw?.targetEnrollment),
    sites: num(siteValue),
    createdByName: text(raw?.createdByName || raw?.created_by_name),
    createdByRole: text(raw?.createdByRole || raw?.created_by_role),
    createdAt: text(raw?.createdAt || raw?.created_at),
    recruitment: raw?.recruitment && typeof raw.recruitment === "object"
      ? {
          screened: num(raw.recruitment.screened),
          screen_fail: num(raw.recruitment.screen_fail),
          randomized: num(raw.recruitment.randomized),
          active: num(raw.recruitment.active),
          withdrawn: num(raw.recruitment.withdrawn),
          dropout: num(raw.recruitment.dropout),
          follow_up: num(raw.recruitment.follow_up),
          completed: num(raw.recruitment.completed),
        }
      : undefined,
  };
}

function normalizeSiteTrial(raw: any): SponsorSiteTrial {
  const trial = normalizeTrial(raw);
  return {
    id: trial.id,
    protocolId: trial.protocolId,
    title: trial.title,
    phase: trial.phase,
    condition: trial.condition,
    drug: trial.drug,
    status: trial.status,
    recruitmentStatus: text(raw?.recruitmentStatus || raw?.recruitment_status),
    piName: text(raw?.piName || raw?.pi_name),
    department: text(raw?.department),
  };
}

export function normalizeSite(raw: any): SponsorSite {
  const enrolled = num(raw?.enrolled ?? raw?.enrolled_count ?? raw?.subjects);
  const target = num(raw?.target ?? raw?.target_enrollment ?? raw?.targetEnrollment);
  const pct = num(
    raw?.enrollmentPct ?? raw?.enrollment_pct,
    target > 0 ? Math.round((enrolled / target) * 100) : 0,
  );
  const rawTrials = Array.isArray(raw?.trials) ? raw.trials : [];
  const rawPis = Array.isArray(raw?.pis) ? raw.pis : [];
  const rawRecruitment = raw?.recruitment;
  const accessType = text(raw?.accessType || raw?.access_type, "full");
  return {
    id: text(raw?.id || raw?._id || raw?.name, "site"),
    name: text(raw?.name || raw?.site_name, "Unnamed site"),
    hospital: text(raw?.hospital || raw?.facility),
    address: text(raw?.address),
    city: text(raw?.city),
    state: text(raw?.state),
    department: text(raw?.department),
    hospitalType: text(raw?.hospitalType || raw?.hospital_type),
    accessType: accessType === "restricted" || accessType === "view_only"
      ? accessType
      : "full",
    status: text(raw?.status, "Active"),
    pi: text(raw?.pi || raw?.pi_name),
    piId: text(raw?.piId || raw?.pi_id),
    piEmail: text(raw?.piEmail || raw?.pi_email),
    piPhone: text(raw?.piPhone || raw?.pi_phone),
    pis: rawPis
      .filter((pi: any) => pi && typeof pi === "object")
      .map((pi: any) => ({
        id: text(pi?.id),
        name: text(pi?.name || pi?.full_name, "Unnamed PI"),
        email: text(pi?.email),
        phone: text(pi?.phone),
        department: text(pi?.department),
      })),
    crc: text(raw?.crc || raw?.crc_name),
    enrolled,
    target,
    enrollmentPct: Math.max(0, Math.min(100, pct)),
    performanceScore: Math.max(
      0,
      Math.min(100, num(raw?.performanceScore ?? raw?.performance_score, pct)),
    ),
    visitCompliance: num(raw?.visitCompliance ?? raw?.visit_compliance),
    adherencePct: num(raw?.adherencePct ?? raw?.adherence_pct),
    overdueVisits: num(raw?.overdueVisits ?? raw?.overdue_visits),
    recruitment: rawRecruitment && typeof rawRecruitment === "object"
      ? {
          screened: num(rawRecruitment.screened),
          screen_fail: num(rawRecruitment.screen_fail),
          randomized: num(rawRecruitment.randomized),
          active: num(rawRecruitment.active),
          withdrawn: num(rawRecruitment.withdrawn),
          dropout: num(rawRecruitment.dropout),
          follow_up: num(rawRecruitment.follow_up),
          completed: num(rawRecruitment.completed),
        }
      : undefined,
    trials: rawTrials
      .filter((trial: any) => trial && typeof trial === "object")
      .map(normalizeSiteTrial),
  };
}

function normalizeNotification(raw: any, index: number): SponsorNotification {
  return {
    id: text(raw?.id || raw?._id, `notification-${index}`),
    title: text(raw?.title, "Update"),
    message: text(raw?.message || raw?.body),
    type: text(raw?.type || raw?.kind || raw?.category, "system"),
    unread: Boolean(raw?.unread ?? !raw?.read),
    time: text(raw?.time || raw?.created_at || raw?.createdAt),
  };
}

export function normalizeDashboard(payload: any): SponsorDashboard {
  const raw = payload?.data ?? payload ?? {};
  const trials = (Array.isArray(raw?.trials) ? raw.trials : []).map(normalizeTrial);
  const sites = (Array.isArray(raw?.sites) ? raw.sites : []).map(normalizeSite);
  const notifications = Array.isArray(raw?.recentNotifications)
    ? raw.recentNotifications
    : Array.isArray(raw?.recent_notifications)
      ? raw.recent_notifications
      : [];
  const portfolio = raw?.portfolio || {};
  const totals = raw?.totals || {};
  const capabilities = raw?.capabilities || {};
  return {
    portfolio: {
      healthScore: num(portfolio.healthScore ?? portfolio.health_score),
      status: text(portfolio.status, "On track"),
      activeTrials: num(portfolio.activeTrials ?? portfolio.active_trials),
      alerts: num(portfolio.alerts),
      enrolled: num(portfolio.enrolled ?? totals.subjects ?? totals.patients),
      target: num(portfolio.target),
      enrollmentPct: num(portfolio.enrollmentPct ?? portfolio.enrollment_pct),
      compliancePct: num(portfolio.compliancePct ?? portfolio.compliance_pct),
      adherencePct: num(portfolio.adherencePct ?? portfolio.adherence_pct),
      recruitment: {
        screened: num(portfolio?.recruitment?.screened),
        screen_fail: num(portfolio?.recruitment?.screen_fail),
        randomized: num(portfolio?.recruitment?.randomized),
        active: num(portfolio?.recruitment?.active),
        withdrawn: num(portfolio?.recruitment?.withdrawn),
        dropout: num(portfolio?.recruitment?.dropout),
        follow_up: num(portfolio?.recruitment?.follow_up),
        completed: num(portfolio?.recruitment?.completed),
      },
    },
    totals: {
      trials: num(totals.trials, trials.length),
      sites: num(totals.sites, sites.length),
      subjects: num(totals.subjects ?? totals.patients),
      pis: num(totals.pis ?? totals.principal_investigators),
    },
    trials,
    sites,
    recentNotifications: notifications.map(normalizeNotification),
    capabilities: {
      canAddTrial: Boolean(capabilities.canAddTrial ?? capabilities.can_add_trial ?? true),
      canAddSite: Boolean(capabilities.canAddSite ?? capabilities.can_add_site),
      canShareSchedule: Boolean(capabilities.canShareSchedule ?? capabilities.can_share_schedule ?? true),
      canManageOrganization: Boolean(
        capabilities.canManageOrganization ?? capabilities.can_manage_organization,
      ),
    },
  };
}

export async function getSponsorDashboard(): Promise<SponsorDashboard> {
  const response = await api.get("/sponsor/dashboard");
  return normalizeDashboard(response.data);
}

export async function getSponsorTrialDetail(id: string): Promise<SponsorTrialDetail> {
  const response = await api.get(`/sponsor/trials/${id}`);
  return response.data as SponsorTrialDetail;
}
