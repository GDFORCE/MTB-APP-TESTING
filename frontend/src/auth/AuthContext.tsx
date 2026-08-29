import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { api, tokenStore, setSessionExpiredHandler } from '../api/client';
import { commitAutofillContext } from '../../modules/mtb-autofill';

export type Role = 'sponsor' | 'cro' | 'smo' | 'site' | 'pi' | 'crc' | 'patient';
export type User = { id: string; email: string; full_name: string; role: Role; phone?: string; organization?: string; avatar_initials?: string; avatar_file_id?: string; org_admin?: boolean; site?: string };

type Session = { access_token: string; refresh_token: string; user: User };
interface Ctx {
  user: User | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<User>;
  signUp: (data: any) => Promise<User>;
  applySession: (data: Session) => Promise<User>;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
}
const AuthCtx = createContext<Ctx>(null as any);
export const useAuth = () => useContext(AuthCtx);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const t = await tokenStore.get('access_token');
      if (!t) { setUser(null); return; }
      const r = await api.get('/auth/me');
      setUser(r.data);
    } catch (error: any) {
      setUser(null);
      if ([400, 401, 403].includes(error?.response?.status)) {
        await Promise.all([
          tokenStore.del('access_token'),
          tokenStore.del('refresh_token'),
        ]);
      }
    }
  }, []);

  useEffect(() => { (async () => { await refresh(); setLoading(false); })(); }, [refresh]);

  // Let the API client clear React auth state when a token refresh hard-fails.
  useEffect(() => {
    setSessionExpiredHandler(() => setUser(null));
    return () => setSessionExpiredHandler(null);
  }, []);

  const signIn = async (email: string, password: string) => {
    const r = await api.post('/auth/login', { email, password });
    // Expo Router swaps screens inside one Android Activity, so Android cannot
    // infer that the login form was submitted. Explicitly finish the OS
    // autofill context while the hinted fields are still mounted. No
    // credential value crosses this bridge or enters app storage.
    commitAutofillContext();
    await tokenStore.set('access_token', r.data.access_token);
    await tokenStore.set('refresh_token', r.data.refresh_token);
    setUser(r.data.user);
    return r.data.user;
  };
  const signUp = async (data: any) => {
    const r = await api.post('/auth/register', data);
    await tokenStore.set('access_token', r.data.access_token);
    await tokenStore.set('refresh_token', r.data.refresh_token);
    setUser(r.data.user);
    return r.data.user;
  };
  // Persist tokens + user from a server session payload (used by OTP-verified registration).
  const applySession = async (data: Session) => {
    await tokenStore.set('access_token', data.access_token);
    await tokenStore.set('refresh_token', data.refresh_token);
    setUser(data.user);
    return data.user;
  };
  const signOut = async () => {
    const refreshToken = await tokenStore.get('refresh_token');
    try {
      if (refreshToken) await api.post('/auth/logout', { refresh_token: refreshToken });
    } finally {
      await Promise.all([
        tokenStore.del('access_token'),
        tokenStore.del('refresh_token'),
      ]);
      setUser(null);
    }
  };

  return <AuthCtx.Provider value={{ user, loading, signIn, signUp, applySession, signOut, refresh }}>{children}</AuthCtx.Provider>;
}
