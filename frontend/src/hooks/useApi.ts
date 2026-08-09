/* eslint-disable react-hooks/exhaustive-deps, react-hooks/refs, react-hooks/set-state-in-effect */
import { useCallback, useEffect, useRef, useState } from "react";

export function useApi<T>(loader: () => Promise<T>, dependencyKey = "initial") {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try { setData(await loaderRef.current()); } catch (reason) { setError(reason); } finally { setLoading(false); }
  }, [dependencyKey]);
  useEffect(() => { void load(); }, [load]);
  return { data, error, loading, reload: load };
}
