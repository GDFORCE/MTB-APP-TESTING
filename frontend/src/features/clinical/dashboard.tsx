import React, { useEffect, useRef, useState } from "react";
import {
  AccessibilityInfo,
  Animated,
  Easing,
  StyleProp,
  Text,
  TextStyle,
  ViewStyle,
} from "react-native";

export type ClinicalDashboardTask = {
  id: string;
  type: "admin_task" | "overdue_visit" | "window_closes_today" | "visit_today" | "schedule_review" | "unread_messages";
  title: string;
  subtitle: string;
  due: string | null;
  due_label?: string;
  deadline_state?: "overdue" | "window_closes_today" | "scheduled_today";
  days_overdue?: number;
  patient_id?: string;
  trial_id?: string;
  visit_instance_id?: string;
  visit_name?: string;
  workflow_task_id?: string;
  workflow_task_kind?: "admin_tasks";
  schedule_review_id?: string;
  priority: "high" | "medium" | "low";
  count?: number;
};

export type ClinicalDashboardVisit = {
  id: string;
  patient_id: string;
  trial_id?: string;
  name?: string;
  scheduled_date?: string;
  status?: string;
  patient_initials?: string;
  subject_label?: string;
  protocol_id?: string;
  condition?: string;
  pi_name?: string;
  site?: string;
};

export type ClinicalDashboard = {
  role: "pi" | "crc";
  generated_at: string;
  totals: {
    trials: number;
    patients: number;
    sites: number;
    sponsors: number;
    team: number;
    pis: number;
    crcs: number;
  };
  today: {
    date: string;
    total: number;
    completed: number;
    pending: number;
    overdue: number;
  };
  trials: any[];
  patients: any[];
  tasks: ClinicalDashboardTask[];
  today_visits: ClinicalDashboardVisit[];
  upcoming_visits: ClinicalDashboardVisit[];
  team: any[];
  sites: string[];
  sponsors: string[];
  capabilities: {
    can_add_patient: boolean;
    can_create_trial: boolean;
    can_review_schedules: boolean;
    can_complete_visits: boolean;
    can_invite_patients: boolean;
    can_view_team_calendar: boolean;
    can_manage_organization: boolean;
  };
};

export function useReducedMotion() {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    let alive = true;
    AccessibilityInfo.isReduceMotionEnabled()
      .then((value) => { if (alive) setReduced(value); })
      .catch(() => {});
    const subscription = AccessibilityInfo.addEventListener(
      "reduceMotionChanged",
      setReduced,
    );
    return () => {
      alive = false;
      subscription.remove();
    };
  }, []);

  return reduced;
}

export function DashboardReveal({
  children,
  delay = 0,
  style,
}: {
  children: React.ReactNode;
  delay?: number;
  style?: StyleProp<ViewStyle>;
}) {
  const reduced = useReducedMotion();
  const progress = useRef(new Animated.Value(reduced ? 1 : 0)).current;

  useEffect(() => {
    if (reduced) {
      progress.setValue(1);
      return;
    }
    Animated.timing(progress, {
      toValue: 1,
      delay,
      duration: 360,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: true,
    }).start();
  }, [delay, progress, reduced]);

  return (
    <Animated.View
      style={[
        style,
        {
          opacity: progress,
          transform: [{
            translateY: progress.interpolate({
              inputRange: [0, 1],
              outputRange: [10, 0],
            }),
          }],
        },
      ]}
    >
      {children}
    </Animated.View>
  );
}

export function AnimatedCount({
  value,
  style,
}: {
  value: number;
  style?: StyleProp<TextStyle>;
}) {
  const reduced = useReducedMotion();
  const animated = useRef(new Animated.Value(reduced ? value : 0)).current;
  const [display, setDisplay] = useState(reduced ? value : 0);

  useEffect(() => {
    const id = animated.addListener(({ value: current }) => {
      setDisplay(Math.round(current));
    });
    return () => animated.removeListener(id);
  }, [animated]);

  useEffect(() => {
    if (reduced) {
      animated.setValue(value);
      setDisplay(value);
      return;
    }
    Animated.timing(animated, {
      toValue: value,
      duration: 520,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: false,
    }).start();
  }, [animated, reduced, value]);

  return <Text style={style}>{display}</Text>;
}

export function useAnimatedProgress(value: number) {
  const reduced = useReducedMotion();
  const animated = useRef(new Animated.Value(reduced ? value : 0)).current;
  const [display, setDisplay] = useState(reduced ? value : 0);

  useEffect(() => {
    const id = animated.addListener(({ value: current }) => setDisplay(current));
    return () => animated.removeListener(id);
  }, [animated]);

  useEffect(() => {
    if (reduced) {
      animated.setValue(value);
      setDisplay(value);
      return;
    }
    Animated.timing(animated, {
      toValue: value,
      duration: 650,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: false,
    }).start();
  }, [animated, reduced, value]);

  return display;
}
