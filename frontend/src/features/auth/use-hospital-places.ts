import { useEffect, useRef, useState } from "react";

import { api } from "@/src/api/client";

export type GooglePlacePrediction = {
  place_id: string;
  name: string;
  description: string;
};
export type GoogleHospitalPrediction = GooglePlacePrediction;
export type GooglePlaceScope = "hospitals" | "organizations";

function newSessionToken() {
  // This URL-safe value groups billing requests; it is not a credential.
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`.slice(0, 36);
}

export function useGooglePlaces(
  query: string,
  enabled = true,
  scope: GooglePlaceScope = "hospitals",
) {
  const sessionToken = useRef(newSessionToken());
  const sessionCache = useRef(new Map<string, GooglePlacePrediction[]>());
  const [predictions, setPredictions] = useState<GooglePlacePrediction[]>([]);
  const [searching, setSearching] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [selectingPlaceId, setSelectingPlaceId] = useState("");

  useEffect(() => {
    const clean = query.trim();
    if (!enabled || clean.length < 2) {
      setPredictions([]);
      setSearching(false);
      setHasSearched(false);
      setSearchError("");
      return;
    }
    const cacheKey = `${scope}:${clean.toLocaleLowerCase()}`;
    const cached = sessionCache.current.get(cacheKey);
    if (cached) {
      setPredictions(cached);
      setSearching(false);
      setHasSearched(true);
      setSearchError("");
      return;
    }
    let ignore = false;
    setSearching(true);
    setHasSearched(false);
    setSearchError("");
    // Preserve useful two-letter brands such as "TX", but wait longer so a
    // user who keeps typing does not spend an extra Autocomplete request.
    const delay = clean.length === 2 ? 800 : 350;
    const timer = setTimeout(async () => {
      try {
        const response = await api.get(`/public/places/${scope}/autocomplete`, {
          params: { input: clean, session_token: sessionToken.current },
        });
        if (!ignore) {
          const next = Array.isArray(response.data?.predictions) ? response.data.predictions : [];
          sessionCache.current.set(cacheKey, next);
          setPredictions(next);
          setHasSearched(true);
        }
      } catch {
        if (!ignore) {
          setPredictions([]);
          setHasSearched(true);
          setSearchError("Address search unavailable—enter manually.");
        }
      } finally {
        if (!ignore) setSearching(false);
      }
    }, delay);
    return () => {
      ignore = true;
      clearTimeout(timer);
    };
  }, [enabled, query, scope]);

  const getAddress = async (prediction: GooglePlacePrediction) => {
    setSelectingPlaceId(prediction.place_id);
    try {
      const response = await api.get(
        `/public/places/${scope}/${encodeURIComponent(prediction.place_id)}`,
        { params: { session_token: sessionToken.current } },
      );
      return {
        placeId: String(response.data?.place_id || prediction.place_id),
        address: String(response.data?.address || ""),
      };
    } finally {
      setSelectingPlaceId("");
      setPredictions([]);
      setHasSearched(false);
      setSearchError("");
      sessionCache.current.clear();
      sessionToken.current = newSessionToken();
    }
  };

  return {
    predictions,
    searching,
    hasSearched,
    searchError,
    selectingPlaceId,
    getAddress,
  };
}

export function useHospitalPlaces(query: string, enabled = true) {
  return useGooglePlaces(query, enabled, "hospitals");
}
