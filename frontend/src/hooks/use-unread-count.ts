import { useCallback, useState } from 'react';
import { useFocusEffect } from 'expo-router';
import { api } from '../api/client';

/**
 * Live unread-notification count for bell badges.
 * Contract: GET /api/notifications/unread-count → { count }.
 * Re-fetches whenever the screen regains focus. Returns null while unknown or
 * on any error (endpoint may not be deployed yet) — callers hide the badge then.
 */
export function useUnreadCount(): number | null {
  const [count, setCount] = useState<number | null>(null);
  useFocusEffect(
    useCallback(() => {
      let active = true;
      api.get('/notifications/unread-count')
        .then(r => { if (active) setCount(typeof r.data?.count === 'number' ? r.data.count : null); })
        .catch(() => { if (active) setCount(null); });
      return () => { active = false; };
    }, []),
  );
  return count;
}
