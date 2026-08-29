import React from "react";
import { useRouter, useLocalSearchParams } from "expo-router";
import { OtpVerifyScreen, maskPhone } from "@/src/features/auth/otp-verify";

export default function VerifyPhone() {
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
  const totalSteps = channels.length + 4; // entity-type, register, [verify screen(s)], security-questions, set-password

  return (
    <OtpVerifyScreen
      channel="phone"
      destination={maskPhone(params.phone || "")}
      registrationId={params.registration_id}
      cooldownSeconds={60}
      step={3}
      totalSteps={totalSteps}
      restartRoute={params.invited === "1" ? "/(auth)/join-invite" : "/(auth)/entity-type"}
      onVerified={(data) => {
        const nextParams = {
          registration_id: params.registration_id,
          channels: params.channels,
          role: params.role,
          invited: params.invited || "",
          email: params.email || "",
          phone: params.phone || "",
        };
        if (data.verified) {
          router.push({ pathname: "/(auth)/security-questions", params: nextParams });
        } else {
          // Phone alone isn't enough for this registration — email is also required.
          router.push({ pathname: "/(auth)/verify-email", params: nextParams });
        }
      }}
    />
  );
}
