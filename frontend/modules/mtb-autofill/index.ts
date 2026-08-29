import { requireOptionalNativeModule } from "expo-modules-core";
import { Platform } from "react-native";

type MtbAutofillNativeModule = {
  commit(): boolean;
};

const nativeModule = Platform.OS === "android"
  ? requireOptionalNativeModule<MtbAutofillNativeModule>("MtbAutofill")
  : null;

/**
 * Finishes Android's current autofill context after a successful login.
 * The OS password manager owns the values; this module never receives them.
 */
export function commitAutofillContext(): boolean {
  return nativeModule?.commit() ?? false;
}
