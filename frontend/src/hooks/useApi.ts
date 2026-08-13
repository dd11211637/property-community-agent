/* eslint-disable react-hooks/exhaustive-deps, react-hooks/refs, react-hooks/set-state-in-effect */
import { useCallback, useEffect, useRef, useState } from "react";

export function useApi<T>(loader: () => Promise<T>, dependencyKey = "initial") {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const loaderRef = useRef(loader);
  const requestIdRef = useRef(0);
  loaderRef.current = loader;
  const load = useCallback(async () => {
    const requestId = ++requestIdRef.current;
    setLoading(true);
    setError(null);
    try {
      const result = await loaderRef.current();
      if (requestId === requestIdRef.current) setData(result);
    } catch (reason) {
      if (requestId === requestIdRef.current) setError(reason);
    } finally {
      if (requestId === requestIdRef.current) setLoading(false);
    }
  }, [dependencyKey]);
  useEffect(() => {
    void load();
    return () => {
      requestIdRef.current += 1;
    };
  }, [load]);
  return { data, error, loading, reload: load };
}
