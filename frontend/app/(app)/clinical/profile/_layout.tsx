// Placeholder sub-screens for Site User Profile.
// Each renders a dawn-header + "Coming in next iteration" body so the menu rows don't crash.
// Will be replaced with pixel-perfect implementations of: edit-profile, entity-change,
// change-password, notifications, reports, tnc, help (with help-faq/contact/tickets).
import { Stack } from "expo-router";
export default function ProfileLayout() { return <Stack screenOptions={{ headerShown: false }} />; }
