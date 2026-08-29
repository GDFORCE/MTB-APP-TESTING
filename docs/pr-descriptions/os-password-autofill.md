# OS password autofill

## Summary

- Replaces the app-defined "Remember me" session toggle with native password-manager hints.
- Configures the sign-in identifier and password as one autofill form for iOS Keychain and Android Autofill / Google Password Manager.
- Marks account-creation credentials as a username plus new password, including the final multi-step registration screen.
- Removes the `remember_me` request field, refresh-token metadata, and frontend session-persistence preference. The app never stores a raw password; Secure Store / AsyncStorage continue to contain JWT session tokens only.
- Requires no Expo config plugin or native permission for basic autofill.

## Automated verification

- [x] Frontend TypeScript check (`tsc --noEmit`)
- [x] Targeted frontend lint for every changed TypeScript file
- [x] Backend Python compilation
- [ ] Backend authentication and refresh-token tests (blocked locally: no MongoDB service; Atlas DNS is unavailable in the sandbox)
- [x] Static scan confirms no password persistence or password logging

## Device verification (required before merge)

These checks require a physical device and a custom development or release build; Expo Go is not sufficient for reliable native autofill verification.

- [ ] Build a dev client with `eas build --profile development` (not Expo Go)
- [ ] iOS: after the first successful login, confirm the system Save Password prompt appears
- [ ] iOS: log out and confirm the Keychain autofill suggestion appears on the login fields
- [ ] Android: after the first successful login, confirm the Google Password Manager Save Password prompt appears
- [ ] Android: log out and confirm the autofill suggestion chip appears on the login fields
- [ ] Confirm signup triggers the Save New Password flow, not Use Saved Password
- [ ] Confirm no password value appears in any AsyncStorage or SecureStore key when inspected with a debugger

## Optional cross-platform credential sharing

- Android app-to-website sharing can be added later by hosting `/.well-known/assetlinks.json` for the production app signing certificate.
- iOS app-to-website sharing can be added later with `ios.associatedDomains` (`webcredentials:<domain>`) and an Apple App Site Association file.
