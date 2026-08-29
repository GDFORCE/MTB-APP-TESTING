import React from "react";
import { useRouter, useLocalSearchParams } from "expo-router";
import { OtpVerifyScreen, maskEmail } from "@/src/features/auth/otp-verify";

export default function VerifyEmail() {
  const router = useRouter();
  const params = useLocalSearchParams<{
    registration_id: string;
    channels: string;
    email: string;
    phone: string;
    role: string;
    invited?: string;
  }>();
  const channels: string[] = (() => { try { return JSON.parse(params.channels || "[]"); } catch { return []; } })();
  const totalSteps = channels.length + 4;

  return (
    <OtpVerifyScreen
      channel="email"
      destination={maskEmail(params.email || "")}
      registrationId={params.registration_id}
      cooldownSeconds={120}
      step={4}
      totalSteps={totalSteps}
      restartRoute={params.invited === "1" ? "/(auth)/join-invite" : "/(auth)/entity-type"}
      onVerified={() => {
        router.push({
          pathname: "/(auth)/security-questions",
          params: {
            registration_id: params.registration_id,
            channels: params.channels,
            role: params.role,
            invited: params.invited || "",
            email: params.email || "",
            phone: params.phone || "",
          },
        });
      }}
    />
  );
}
